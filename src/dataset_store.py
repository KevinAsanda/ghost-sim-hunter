"""Immutable validated inputs and atomically published results, addressed by content hash."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from src.reconciliation import ENGINE_VERSION, detect
from src.ingestion import UploadError


class DatasetStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                created_at TEXT NOT NULL, config_json TEXT NOT NULL, report_json TEXT NOT NULL,
                tables_json TEXT NOT NULL, result_json TEXT, last_successful_run TEXT,
                warnings_acknowledged_at TEXT, engine_version TEXT NOT NULL)""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def stage(self, name, source, tables, config, report):
        canonical = {k: sorted(v, key=lambda r: json.dumps(r, sort_keys=True)) for k, v in tables.items()}
        encoded = json.dumps(dict(tables=canonical, config=config, source=source, engine=ENGINE_VERSION),
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        dataset_id = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connection() as db:
            db.execute("""INSERT OR IGNORE INTO datasets
                (id,name,source,created_at,config_json,report_json,tables_json,engine_version)
                VALUES (?,?,?,?,?,?,?,?)""", (dataset_id, name, source, datetime.now(timezone.utc).isoformat(),
                    json.dumps(config), json.dumps(report), json.dumps(canonical), ENGINE_VERSION))
        return self.get(dataset_id, include_data=False)

    @staticmethod
    def unpack(row, include_data):
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["quality"] = json.loads(result.pop("report_json"))
        result["status"] = "COMPLETED" if result.get("last_successful_run") else "READY_FOR_REVIEW"
        if include_data:
            result["tables"] = json.loads(result.pop("tables_json"))
            result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        else:
            result.pop("tables_json", None)
            result.pop("result_json", None)
        return result

    def get(self, dataset_id, include_data=True):
        with self.connection() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if not row:
                raise KeyError("Dataset not found.")
            return self.unpack(row, include_data)

    def list(self):
        with self.connection() as db:
            rows = db.execute("""SELECT id,name,source,created_at,config_json,report_json,last_successful_run,
                warnings_acknowledged_at,engine_version FROM datasets ORDER BY created_at DESC""").fetchall()
            return [self.unpack(row, False) for row in rows]

    def run(self, dataset_id, acknowledge_warnings=False):
        dataset = self.get(dataset_id)
        if dataset["engine_version"] != ENGINE_VERSION:
            raise UploadError("This dataset uses an older engine. Re-upload to create a new version without changing old cases.")
        if dataset["result"] is not None:
            return self.get(dataset_id, False)  # Identical input/config/version reuses the successful run.
        if not dataset["quality"]["valid"]:
            raise UploadError("Correct validation errors before running detection.")
        if dataset["quality"]["warning_count"] and acknowledge_warnings is not True:
            raise UploadError("Review and acknowledge the quality warnings before running detection.")
        result = detect(dataset["tables"], dataset["config"], synthetic=dataset["source"] == "synthetic")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            # Concurrent repeated requests may compute twice, but only publish one immutable result.
            db.execute("""UPDATE datasets SET result_json=?,last_successful_run=?,warnings_acknowledged_at=?
                WHERE id=? AND result_json IS NULL""", (json.dumps(result, allow_nan=False), now,
                now if dataset["quality"]["warning_count"] else None, dataset_id))
        return self.get(dataset_id, False)
