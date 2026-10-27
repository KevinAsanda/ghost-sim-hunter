import base64
import csv
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from src.case_store import CaseStore
from src.dataset_store import DatasetStore
from src.sample_data import example_tables


def b64_csv(rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")


class CaseApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        with patch.dict(
            os.environ,
            {
                "GHOST_SIM_CASE_DB": str(Path(cls.tmp.name) / "startup_cases.sqlite3"),
                "GHOST_SIM_DATA_DB": str(Path(cls.tmp.name) / "startup_datasets.sqlite3"),
            },
        ):
            cls.server = importlib.import_module("server")

    def setUp(self):
        self.tmp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_case.cleanup)
        self.server.CASE_STORE = CaseStore(Path(self.tmp_case.name) / "cases.sqlite3")
        self.server.DATASETS = DatasetStore(Path(self.tmp_case.name) / "datasets.sqlite3")
        self.client = TestClient(self.server.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

        # Upload and run the example dataset as our fast test fixture
        tables = example_tables()
        upload_resp = self.client.post(
            "/api/datasets/upload",
            json={
                "name": "API test upload",
                "as_of_date": "2026-10-27",
                "billing_grace_days": 3,
                "files": {
                    k: {"name": f"{k}.csv", "content_base64": b64_csv(v)}
                    for k, v in tables.items()
                },
            },
        )
        self.assertEqual(upload_resp.status_code, 200)
        self.dataset_id = upload_resp.json()["dataset"]["id"]
        run_resp = self.client.post(
            "/api/datasets/run",
            json={"dataset_id": self.dataset_id, "acknowledge_warnings": True},
        )
        self.assertEqual(run_resp.status_code, 200)
        self.sid = "EXAMPLE-C"

    def update(self, **extra):
        payload = dict(
            dataset_id=self.dataset_id,
            sim_ids=[self.sid],
            status="INVESTIGATING",
            actor="Reviewer A",
            assigned_to="Billing",
            note="Investigating mismatch",
            expected_versions={self.sid: 0},
        )
        payload.update(extra)
        return self.client.post("/api/action", json=payload)

    def test_round_trip_summary_detail_outcomes_and_export_after_store_reopens(self):
        self.assertEqual(self.update().status_code, 200)
        self.assertEqual(
            self.update(
                status="CONFIRMED",
                note="Confirmed internet APN residual",
                confirmed_leakage_zar=100.0,
                expected_versions={self.sid: 1},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.update(
                status="RESOLVED",
                note="Re-rated and invoiced",
                confirmed_leakage_zar=100.0,
                cash_recovered_zar=80.0,
                cost_avoided_zar=20.0,
                expected_versions={self.sid: 2},
            ).status_code,
            200,
        )
        self.server.CASE_STORE = CaseStore(self.server.CASE_STORE.path)
        detail = self.client.get(
            f"/api/sim/{self.sid}?dataset_id={self.dataset_id}"
        ).json()
        self.assertEqual(detail["case"]["status"], "RESOLVED")
        self.assertEqual(detail["case"]["confirmed_leakage_zar"], 100.0)
        self.assertEqual(detail["case"]["cash_recovered_zar"], 80.0)
        self.assertEqual(detail["case"]["cost_avoided_zar"], 20.0)
        self.assertEqual(detail["case"]["history"][-1]["actor"], "Reviewer A")
        self.assertTrue(len(detail["deviation_indicators"]) > 0)

        # Filter summary and CSV export to status=RESOLVED
        summary = self.client.get(
            f"/api/summary?dataset_id={self.dataset_id}&status=RESOLVED"
        ).json()
        self.assertEqual(summary["matching_count"], 1)
        self.assertEqual(summary["queue"][0]["sim_id"], self.sid)
        self.assertEqual(summary["totals"]["confirmed_leakage_zar"], 100.0)
        self.assertEqual(summary["totals"]["cash_recovered_zar"], 80.0)
        self.assertEqual(summary["totals"]["cost_avoided_zar"], 20.0)

        exported = list(
            csv.DictReader(
                io.StringIO(
                    self.client.get(
                        f"/api/export/queue.csv?dataset_id={self.dataset_id}&status=RESOLVED"
                    ).text
                )
            )
        )
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["sim_id"], self.sid)
        self.assertEqual(exported[0]["assigned_to"], "Billing")
        self.assertEqual(exported[0]["case_status"], "RESOLVED")
        self.assertEqual(float(exported[0]["cash_recovered_zar"]), 80.0)

    def test_conflict_returns_409_and_preserves_original_note(self):
        self.assertEqual(self.update().status_code, 200)
        response = self.update(note="Stale overwrite")
        self.assertEqual(response.status_code, 409)
        history = self.client.get(
            f"/api/sim/{self.sid}?dataset_id={self.dataset_id}"
        ).json()["case"]["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["note"], "Investigating mismatch")

    def test_invalid_requests_return_controlled_errors(self):
        for body in (
            [],
            {},
            {"dataset_id": self.dataset_id, "sim_ids": ["NOT-A-SIM"]},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post("/api/action", json=body).status_code, 400
                )
        self.assertEqual(self.client.post("/api/action", content="{").status_code, 400)
        self.assertEqual(self.update(status="TICKETED_TO_SME").status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/sim/missing?dataset_id={self.dataset_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/summary?dataset_id={self.dataset_id}&top_k=bad").status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/summary").status_code, 400)

    def test_formula_like_owner_is_escaped_in_csv(self):
        self.assertEqual(self.update(assigned_to="=1+1").status_code, 200)
        exported = list(
            csv.DictReader(
                io.StringIO(
                    self.client.get(
                        f"/api/export/queue.csv?dataset_id={self.dataset_id}"
                    ).text
                )
            )
        )
        row = next(r for r in exported if r["sim_id"] == self.sid)
        self.assertEqual(row["assigned_to"], "'=1+1")

    def test_dismissal_is_visible_and_has_no_confirmed_outcomes(self):
        result = self.update(
            status="DISMISSED",
            dismissal_reason="Pool verified",
            whitelist_expires_at="2099-01-01T00:00:00+00:00",
        )
        self.assertEqual(result.status_code, 200)
        summary = self.client.get(
            f"/api/summary?dataset_id={self.dataset_id}&status=DISMISSED"
        ).json()
        self.assertEqual(summary["queue"][0]["case_status"], "DISMISSED")
        self.assertIsNone(summary["totals"]["confirmed_leakage_zar"])

    def test_templates_and_bad_upload_return_expected_responses(self):
        for table in ("subscribers", "usage_daily", "billing_events"):
            resp = self.client.get(f"/api/templates/{table}.csv")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("sim_id", resp.text)

        sample_resp = self.client.post("/api/datasets/sample", json={})
        self.assertEqual(sample_resp.status_code, 200)
        self.assertTrue(sample_resp.json()["dataset"]["quality"]["valid"])

        summary_resp = self.client.get(f"/api/summary?dataset_id={self.dataset_id}").json()
        self.assertIn("cell_hotspots", summary_resp)
        self.assertTrue(len(summary_resp["cell_hotspots"]) >= 1)

        bad_tables = example_tables()
        bad_tables["usage_daily"][0]["sim_id"] = "UNKNOWN-SIM"
        bad_resp = self.client.post(
            "/api/datasets/upload",
            json={
                "name": "Invalid upload",
                "as_of_date": "2026-10-27",
                "billing_grace_days": 3,
                "files": {
                    k: {"name": f"{k}.csv", "content_base64": b64_csv(v)}
                    for k, v in bad_tables.items()
                },
            },
        )
        self.assertEqual(bad_resp.status_code, 422)
        self.assertFalse(bad_resp.json()["quality"]["valid"])


if __name__ == "__main__":
    unittest.main()
