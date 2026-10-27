import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.case_store import CaseStore, CaseValidationError, CaseConflictError


class CaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cases.sqlite3"
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.store = CaseStore(self.path, now=lambda: self.now)
        self.rows = {sid: {"sim_id": sid, "investigation_summary": "Evidence"}
                     for sid in ("SIM-1", "SIM-2")}
        self.scope = "demo:test:42"

    def update(self, status="INVESTIGATING", sid="SIM-1", **extra):
        case = self.store.get_case(self.scope, sid)
        body = dict(sim_ids=[sid], status=status, actor="Kevin", assigned_to="RA Team",
                    expected_versions={sid: case["version"]})
        body.update(extra)
        return self.store.update_cases(self.scope, self.rows, body)[0]

    def test_lifecycle_notes_assignment_and_reopen(self):
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["version"], 0)
        self.update(note="Review started")
        self.update(assigned_to="Billing Team", note="Assigned for reconciliation")
        self.update("CONFIRMED", note="Billing mismatch verified")
        self.update("RESOLVED", note="Rating corrected; verified on next batch")
        case = self.update(note="New usage requires another review")
        self.assertEqual(case["status"], "INVESTIGATING")
        self.assertEqual(case["version"], 5)
        self.assertEqual(len(case["history"]), 6)
        self.assertEqual(case["history"][2]["before"]["assigned_to"], "RA Team")
        self.assertEqual(case["history"][2]["after"]["assigned_to"], "Billing Team")

    def test_persists_in_a_fresh_python_process(self):
        case = self.update(note="Keep this decision")
        script = ("import json,sys; from src.case_store import CaseStore; "
                  "print(json.dumps(CaseStore(sys.argv[1]).get_case(sys.argv[2], 'SIM-1')))")
        saved = json.loads(subprocess.check_output(
            [sys.executable, "-c", script, str(self.path), self.scope], text=True))
        self.assertEqual(saved, case)

    def test_whitelist_expiry_reopens_once_and_keeps_reason_in_history(self):
        expiry = (self.now + timedelta(days=1)).isoformat()
        case = self.update("DISMISSED", dismissal_reason="Valid corporate pool", whitelist_expires_at=expiry)
        self.assertEqual(case["status"], "DISMISSED")
        self.now += timedelta(days=1)
        case = self.store.get_case(self.scope, "SIM-1")
        self.assertEqual(case["status"], "INVESTIGATING")
        self.assertIsNone(case["whitelist_expires_at"])
        self.assertEqual(case["history"][-1]["event_type"], "WHITELIST_EXPIRED")
        self.assertEqual(case["history"][-1]["before"]["dismissal_reason"], "Valid corporate pool")
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["version"], case["version"])

    def test_bad_dismissals_leave_no_case_or_audit_event(self):
        for extra in ({}, {"dismissal_reason": "Reason"},
                      {"dismissal_reason": "Reason", "whitelist_expires_at": self.now.isoformat()},
                      {"dismissal_reason": "Reason", "whitelist_expires_at": "2027-01-01"}):
            with self.subTest(extra=extra), self.assertRaises(CaseValidationError):
                self.update("DISMISSED", **extra)
        self.assertEqual(self.store.list_cases(self.scope), {})
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["history"], [])

    def test_dispositions_and_reopen_require_notes(self):
        self.update()
        with self.assertRaises(CaseValidationError):
            self.update("CONFIRMED")
        self.update("CONFIRMED", note="Evidence verified")
        with self.assertRaises(CaseValidationError):
            self.update("RESOLVED")
        self.update("RESOLVED", note="Fixed")
        with self.assertRaises(CaseValidationError):
            self.update()

    def test_illegal_transition_and_missing_owner_rejected(self):
        for status, owner in (("RESOLVED", "RA"), ("CONFIRMED", "RA"), ("INVESTIGATING", "")):
            with self.subTest(status=status), self.assertRaises(CaseValidationError):
                self.update(status, assigned_to=owner)
        self.assertEqual(self.store.list_cases(self.scope), {})

    def test_stale_review_cannot_overwrite_another_reviewer(self):
        self.update()
        second_store = CaseStore(self.path)
        with self.assertRaises(CaseConflictError):
            second_store.update_cases(self.scope, self.rows, dict(
                sim_ids=["SIM-1"], status="INVESTIGATING", actor="Teammate",
                assigned_to="Other team", expected_versions={"SIM-1": 0}))
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["assigned_to"], "RA Team")

    def test_batch_failure_rolls_back_earlier_updates_and_events(self):
        self.update(sid="SIM-2")
        with self.assertRaises(CaseConflictError):
            self.store.update_cases(self.scope, self.rows, dict(
                sim_ids=["SIM-1", "SIM-2"], status="INVESTIGATING", actor="Kevin",
                assigned_to="RA", expected_versions={"SIM-1": 0, "SIM-2": 0}))
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["history"], [])
        self.assertEqual(self.store.get_case(self.scope, "SIM-2")["version"], 1)

    def test_expired_case_stays_reopened_after_stale_edit(self):
        self.update("DISMISSED", dismissal_reason="Valid pool",
                    whitelist_expires_at=(self.now + timedelta(days=1)).isoformat())
        self.now += timedelta(days=2)
        with self.assertRaises(CaseConflictError):
            self.update(expected_versions={"SIM-1": 1}, note="Stale edit")
        self.assertEqual(self.store.get_case(self.scope, "SIM-1")["status"], "INVESTIGATING")

    def test_dataset_scopes_do_not_share_decisions(self):
        self.update()
        self.assertEqual(self.store.get_case("demo:other:99", "SIM-1")["status"], "NEW")

    def test_invalid_payloads_are_rejected(self):
        for payload in (None, [], {}, {"sim_ids": ["UNKNOWN"]},
                        {"sim_ids": ["SIM-1", "SIM-1"]}, {"sim_ids": [["SIM-1"]]}):
            with self.subTest(payload=payload), self.assertRaises(CaseValidationError):
                self.store.update_cases(self.scope, self.rows, payload)
        for extra in ({"actor": ""}, {"actor": "system"}, {"status": []},
                      {"expected_versions": {"SIM-1": True}}, {"unexpected": 1},
                      {"note": "x" * 4001}):
            with self.subTest(extra=extra), self.assertRaises(CaseValidationError):
                self.update(**extra)

    def test_notes_are_preserved_literally_and_noop_does_not_add_event(self):
        note = "<script>alert('test')</script>\nO'Brien's review"
        saved = self.update(note=note)
        self.assertEqual(saved["history"][-1]["note"], note)
        self.assertEqual(self.update()["version"], saved["version"])

    def test_history_cannot_be_updated_or_deleted(self):
        self.update()
        with self.store.connection() as db:
            for statement in ("DELETE FROM case_events", "UPDATE case_events SET actor='Changed'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)

    def test_financial_outcomes_lifecycle_and_validation(self):
        self.update("INVESTIGATING", note="Initial review")
        # Confirmed leakage cannot be recorded while still INVESTIGATING
        with self.assertRaises(CaseValidationError):
            self.update("INVESTIGATING", note="Too early", confirmed_leakage_zar=100.0)
        # Confirming with confirmed_leakage_zar succeeds when a note is supplied
        case = self.update("CONFIRMED", note="Verified OCS rating gap", confirmed_leakage_zar=250.5)
        self.assertEqual(case["confirmed_leakage_zar"], 250.5)
        # Cash recovered and cost avoided require RESOLVED status
        with self.assertRaises(CaseValidationError):
            self.update("CONFIRMED", note="Not resolved yet", cash_recovered_zar=100.0)
        # Updating financial outcomes without a note is rejected
        with self.assertRaises(CaseValidationError):
            self.update("CONFIRMED", note="", confirmed_leakage_zar=300.0)
        # Resolving with recovered cash and avoided cost succeeds
        case = self.update(
            "RESOLVED",
            note="Back-billed invoice and blocked SIM box trunk",
            confirmed_leakage_zar=250.5,
            cash_recovered_zar=150.0,
            cost_avoided_zar=75.25,
        )
        self.assertEqual(case["cash_recovered_zar"], 150.0)
        self.assertEqual(case["cost_avoided_zar"], 75.25)
        # Reopening requires clearing outcome amounts; history preserves previous amounts
        with self.assertRaises(CaseValidationError):
            self.update("INVESTIGATING", note="Reopen without clearing amounts")
        reopened = self.update(
            "INVESTIGATING",
            note="Reopening for new period review",
            confirmed_leakage_zar=None,
            cash_recovered_zar=None,
            cost_avoided_zar=None,
        )
        self.assertIsNone(reopened["confirmed_leakage_zar"])
        self.assertIsNone(reopened["cash_recovered_zar"])
        self.assertIsNone(reopened["cost_avoided_zar"])
        self.assertEqual(reopened["history"][-2]["after"]["cash_recovered_zar"], 150.0)

    def test_v1_schema_migrates_to_v2_preserving_existing_cases(self):
        v1_path = Path(self.tmp.name) / "v1.sqlite3"
        con = sqlite3.connect(v1_path)
        con.executescript("""
            CREATE TABLE cases (
                scope TEXT NOT NULL, sim_id TEXT NOT NULL,
                status TEXT NOT NULL, assigned_to TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                dismissal_reason TEXT NOT NULL, whitelist_expires_at TEXT,
                version INTEGER NOT NULL, evidence_json TEXT NOT NULL,
                PRIMARY KEY(scope, sim_id)
            );
            CREATE TABLE case_events (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, sim_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, actor TEXT NOT NULL, event_type TEXT NOT NULL,
                note TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL
            );
            INSERT INTO cases VALUES (
                'demo:v1', 'SIM-1', 'INVESTIGATING', 'RA Team',
                '2026-10-27T00:00:00+00:00', '2026-10-27T00:00:00+00:00',
                '', NULL, 1, '{"sim_id":"SIM-1"}'
            );
            PRAGMA user_version = 1;
        """)
        con.close()
        migrated = CaseStore(v1_path, now=lambda: self.now)
        case = migrated.get_case("demo:v1", "SIM-1")
        self.assertEqual(case["status"], "INVESTIGATING")
        self.assertIsNone(case["confirmed_leakage_zar"])
        self.assertIsNone(case["cash_recovered_zar"])
        self.assertIsNone(case["cost_avoided_zar"])


if __name__ == "__main__":
    unittest.main()
