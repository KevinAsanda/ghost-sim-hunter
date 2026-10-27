"""Transactional, persistent analyst cases. No ticket dispatch or network actions."""

import json
import os
import sqlite3
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


TRANSITIONS = {
    "NEW": ("INVESTIGATING", "DISMISSED"),
    "INVESTIGATING": ("CONFIRMED", "DISMISSED"),
    "CONFIRMED": ("RESOLVED", "INVESTIGATING"),
    "DISMISSED": ("INVESTIGATING",),
    "RESOLVED": ("INVESTIGATING",),
}
OUTCOME_FIELDS = ("confirmed_leakage_zar", "cash_recovered_zar", "cost_avoided_zar")


class CaseValidationError(ValueError):
    pass


class CaseConflictError(CaseValidationError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def text_field(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise CaseValidationError(f"{name} must be text of at most {limit} characters.")
    value = value.strip()
    if required and not value:
        raise CaseValidationError(f"{name} is required.")
    return value


class CaseStore:
    def __init__(self, path=None, now=utc_now):
        self.path = Path(path or os.environ.get("GHOST_SIM_CASE_DB") or
                         Path(__file__).resolve().parents[1] / "data" / "cases.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f"Unsupported case database version: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    scope TEXT NOT NULL, sim_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('NEW','INVESTIGATING','CONFIRMED','DISMISSED','RESOLVED')),
                    assigned_to TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, dismissal_reason TEXT NOT NULL,
                    whitelist_expires_at TEXT, version INTEGER NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY(scope, sim_id)
                );
                CREATE TABLE IF NOT EXISTS case_events (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, sim_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, actor TEXT NOT NULL, event_type TEXT NOT NULL,
                    note TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                    FOREIGN KEY(scope, sim_id) REFERENCES cases(scope, sim_id)
                );
                CREATE INDEX IF NOT EXISTS case_events_lookup
                    ON case_events(scope, sim_id, id);
                CREATE TRIGGER IF NOT EXISTS case_events_no_update
                    BEFORE UPDATE ON case_events BEGIN
                    SELECT RAISE(ABORT, 'Case history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS case_events_no_delete
                    BEFORE DELETE ON case_events BEGIN
                    SELECT RAISE(ABORT, 'Case history is append-only'); END;
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(cases)")}
            for field in OUTCOME_FIELDS:
                if field not in columns:
                    db.execute(f"ALTER TABLE cases ADD COLUMN {field} REAL")
            db.execute("PRAGMA user_version = 2")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def empty_case(sim_id):
        return dict(sim_id=sim_id, status="NEW", assigned_to="", created_at=None,
                    updated_at=None, dismissal_reason="", whitelist_expires_at=None,
                    version=0, **{field: None for field in OUTCOME_FIELDS})

    @staticmethod
    def record(row):
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    @staticmethod
    def event(db, scope, sim_id, timestamp, actor, kind, note, before, after):
        db.execute("""INSERT INTO case_events
            (scope,sim_id,occurred_at,actor,event_type,note,before_json,after_json)
            VALUES (?,?,?,?,?,?,?,?)""",
            (scope, sim_id, timestamp, actor, kind, note,
             json.dumps(before), json.dumps(after)))

    def expire(self, db, scope, timestamp):
        expired = db.execute("""SELECT * FROM cases WHERE scope=? AND status='DISMISSED'
            AND whitelist_expires_at <= ?""", (scope, timestamp)).fetchall()
        for row in expired:
            before = self.record(row)
            db.execute("""UPDATE cases SET status='INVESTIGATING', updated_at=?,
                whitelist_expires_at=NULL, dismissal_reason='', version=version+1
                WHERE scope=? AND sim_id=?""", (timestamp, scope, row["sim_id"]))
            after = self.record(db.execute("SELECT * FROM cases WHERE scope=? AND sim_id=?",
                                          (scope, row["sim_id"])).fetchone())
            self.event(db, scope, row["sim_id"], timestamp, "system", "WHITELIST_EXPIRED",
                       "Benign dismissal expired; case reopened for review.", before, after)

    def list_cases(self, scope):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.expire(db, scope, self.now().isoformat())
            return {row["sim_id"]: self.record(row) for row in
                    db.execute("SELECT * FROM cases WHERE scope=?", (scope,))}

    def get_case(self, scope, sim_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.expire(db, scope, self.now().isoformat())
            row = db.execute("SELECT * FROM cases WHERE scope=? AND sim_id=?",
                             (scope, sim_id)).fetchone()
            result = self.record(row) if row else self.empty_case(sim_id)
            result["allowed_transitions"] = list(TRANSITIONS[result["status"]])
            result["history"] = []
            for event in db.execute("SELECT * FROM case_events WHERE scope=? AND sim_id=? ORDER BY id",
                                    (scope, sim_id)):
                entry = dict(event)
                entry["before"] = json.loads(entry.pop("before_json"))
                entry["after"] = json.loads(entry.pop("after_json"))
                result["history"].append(entry)
            return result

    def update_cases(self, scope, rows_by_id, payload):
        """Apply a version-checked batch atomically, including its audit events."""
        if not isinstance(payload, dict):
            raise CaseValidationError("Request must be an object.")
        allowed = {"sim_ids", "status", "actor", "assigned_to", "note",
                   "dismissal_reason", "whitelist_expires_at", "expected_versions"}
        allowed.update(OUTCOME_FIELDS)
        if set(payload) - allowed:
            raise CaseValidationError("Unknown case update fields.")
        ids = payload.get("sim_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100 or
                any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids)):
            raise CaseValidationError("Provide 1–100 distinct SIM IDs.")
        if any(sid not in rows_by_id for sid in ids):
            raise CaseValidationError("Every SIM must exist in the current dataset.")
        actor = text_field(payload.get("actor"), "Reviewer name", 120, required=True)
        if actor.casefold() == "system":
            raise CaseValidationError("The reviewer name 'system' is reserved.")
        status = payload.get("status")
        if not isinstance(status, str) or status not in TRANSITIONS:
            raise CaseValidationError("Unknown case status.")
        note = text_field(payload.get("note", ""), "Note", 4000)
        owner = None if "assigned_to" not in payload else text_field(
            payload["assigned_to"], "Assigned owner", 120)
        versions = payload.get("expected_versions")
        if (not isinstance(versions, dict) or set(versions) != set(ids) or
                any(type(v) is not int or v < 0 for v in versions.values())):
            raise CaseValidationError("Provide the current version of every selected case.")
        now = self.now()
        timestamp = now.isoformat()
        reason = text_field(payload.get("dismissal_reason", ""), "Dismissal reason", 2000)
        expires = payload.get("whitelist_expires_at")
        if status == "DISMISSED":
            if not reason:
                raise CaseValidationError("Dismissing a case requires a reason.")
            try:
                expiry = datetime.fromisoformat(expires) if isinstance(expires, str) else None
                if expiry is None or expiry.tzinfo is None or expiry <= now:
                    raise ValueError()
            except ValueError:
                raise CaseValidationError("Whitelist expiry must be a future timestamp with timezone.")
            expires = expiry.astimezone(timezone.utc).isoformat()
        elif reason or expires is not None:
            raise CaseValidationError("Dismissal reason and expiry apply only to dismissed cases.")

        # Commit expiry separately so a rejected stale edit cannot undo reopening.
        self.list_cases(scope)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for sid in ids:
                row = db.execute("SELECT * FROM cases WHERE scope=? AND sim_id=?", (scope, sid)).fetchone()
                before = self.record(row) if row else self.empty_case(sid)
                if before["version"] != versions[sid]:
                    raise CaseConflictError(f"Case {sid} changed. Reload it before saving; your changes were not applied.")
                previous = before["status"]
                outcomes = {field: payload.get(field, before.get(field)) for field in OUTCOME_FIELDS}
                for field, value in outcomes.items():
                    if value is not None and (type(value) not in (int, float) or
                            not math.isfinite(value) or not 0 <= value <= 1e12):
                        raise CaseValidationError(f"{field} must be a nonnegative finite amount or null.")
                if outcomes["confirmed_leakage_zar"] is not None and status not in ("CONFIRMED", "RESOLVED"):
                    raise CaseValidationError("Confirmed leakage amounts require CONFIRMED or RESOLVED status. Clear the amount to reopen.")
                if any(outcomes[field] is not None for field in ("cash_recovered_zar", "cost_avoided_zar")) and status != "RESOLVED":
                    raise CaseValidationError("Recorded cash recovery and cost avoidance require RESOLVED status. Clear these amounts to reopen.")
                outcomes_changed = any(outcomes[field] != before.get(field) for field in OUTCOME_FIELDS)
                if outcomes_changed and not note:
                    raise CaseValidationError("Financial outcome changes require a note identifying the evidence and period.")
                if status != previous and status not in TRANSITIONS[previous]:
                    raise CaseValidationError(f"Cannot move {sid} from {previous} to {status}.")
                assigned = before["assigned_to"] if owner is None else owner
                if status in ("INVESTIGATING", "CONFIRMED", "RESOLVED") and not assigned:
                    raise CaseValidationError("Assign an owner before investigating, confirming or resolving.")
                if (status != previous and
                        (status in ("CONFIRMED", "RESOLVED") or previous in ("DISMISSED", "RESOLVED"))
                        and not note):
                    raise CaseValidationError("Confirmation, resolution and reopening require an explanation in the note.")
                if row is None:
                    evidence = {key: rows_by_id[sid].get(key) for key in (
                        "predicted_archetype_code", "investigation_summary", "owner_team",
                        "unbilled_retail_zar", "estimated_opportunity_zar", "primary_cell_id")}
                    db.execute("""INSERT INTO cases
                        (scope,sim_id,status,assigned_to,created_at,updated_at,dismissal_reason,
                         whitelist_expires_at,version,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                               (scope, sid, "NEW", "", timestamp, timestamp, "", None, 0,
                                json.dumps(evidence)))
                    before = self.record(db.execute("SELECT * FROM cases WHERE scope=? AND sim_id=?",
                                                   (scope, sid)).fetchone())
                    self.event(db, scope, sid, timestamp, actor, "CREATED", "", {}, before)
                changed = (status != previous or assigned != before["assigned_to"] or
                           reason != before["dismissal_reason"] or expires != before["whitelist_expires_at"] or outcomes_changed)
                if not changed and not note:
                    continue
                db.execute("""UPDATE cases SET status=?, assigned_to=?, updated_at=?,
                    dismissal_reason=?, whitelist_expires_at=?, version=version+1,
                    confirmed_leakage_zar=?, cash_recovered_zar=?, cost_avoided_zar=?
                    WHERE scope=? AND sim_id=?""",
                    (status, assigned, timestamp, reason, expires, *[outcomes[f] for f in OUTCOME_FIELDS], scope, sid))
                after = self.record(db.execute("SELECT * FROM cases WHERE scope=? AND sim_id=?",
                                              (scope, sid)).fetchone())
                self.event(db, scope, sid, timestamp, actor, "UPDATED", note, before, after)
        return [self.get_case(scope, sid) for sid in ids]
