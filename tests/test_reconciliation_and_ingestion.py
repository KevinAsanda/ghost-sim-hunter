import base64
import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path

from src.dataset_store import DatasetStore
from src.ingestion import UploadError, parse_file, validate_tables
from src.reconciliation import detect
from src.sample_data import example_tables


def to_b64_csv(rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")


class ReconciliationAndIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tables = example_tables()
        self.headers = {k: list(v[0]) for k, v in self.tables.items()}
        self.config = {"as_of_date": "2026-10-27", "billing_grace_days": 3}

    def test_example_tables_reconcile_partial_bundle_parent_mixed_apn_and_delayed_billing(self):
        norm, report = validate_tables(self.tables, self.headers, self.config)
        self.assertTrue(report["valid"])
        self.assertEqual(report["error_count"], 0)
        result = detect(norm, self.config, synthetic=False)
        by_id = {r["sim_id"]: r for r in result["rows"]}

        # EXAMPLE-A: 90 MB bundle on 100 MB usage at R1/MB -> R90 covered, R10 residual preserved
        ex_a = by_id["EXAMPLE-A"]
        self.assertEqual(ex_a["entitlement_covered_zar"], 90.0)
        self.assertEqual(ex_a["unbilled_retail_zar"], 10.0)
        self.assertEqual(ex_a["estimated_opportunity_zar"], 10.0)
        self.assertFalse(ex_a["is_benign_exempt"])

        # EXAMPLE-B: R50 parent allocation on R100 usage -> R50 covered, R50 residual preserved
        ex_b = by_id["EXAMPLE-B"]
        self.assertEqual(ex_b["parent_covered_zar"], 50.0)
        self.assertEqual(ex_b["unbilled_retail_zar"], 50.0)
        self.assertEqual(ex_b["estimated_opportunity_zar"], 50.0)
        self.assertFalse(ex_b["is_benign_exempt"])

        # EXAMPLE-C: mixed APN (100 MB on internet + 100 MB on mtn.zero.edu) -> R100 zero-rated, R100 residual
        ex_c = by_id["EXAMPLE-C"]
        self.assertEqual(ex_c["zero_rated_covered_zar"], 100.0)
        self.assertEqual(ex_c["unbilled_retail_zar"], 100.0)
        self.assertEqual(ex_c["observed_apns"], ["internet", "mtn.zero.edu"])

        # EXAMPLE-D: usage on 2026-10-26 with as_of 2026-10-27 and 3-day grace -> R100 awaiting billing, R0 mature residual
        ex_d = by_id["EXAMPLE-D"]
        self.assertEqual(ex_d["pending_billing_zar"], 100.0)
        self.assertEqual(ex_d["unbilled_retail_zar"], 0.0)
        self.assertEqual(ex_d["predicted_archetype_code"], "AWAITING_BILLING")

        # Non-synthetic runs do not fabricate benchmark accuracy or ground-truth labels
        self.assertIsNone(result["benchmark"])
        self.assertNotIn("ground_truth_label", ex_a)

    def test_expired_entitlement_and_missing_billing_evidence_are_tracked_separately(self):
        tables = copy.deepcopy(self.tables)
        # Set EXAMPLE-A entitlement validity before the usage date (2026-10-20)
        tables["subscribers"][0]["entitlement_valid_from"] = "2026-09-01"
        tables["subscribers"][0]["entitlement_valid_to"] = "2026-09-30"
        # Remove billing event for EXAMPLE-A so it has no billing snapshot as of 2026-10-27
        tables["billing_events"] = [b for b in tables["billing_events"] if b["sim_id"] != "EXAMPLE-A"]

        norm, report = validate_tables(tables, self.headers, self.config)
        self.assertTrue(report["valid"])
        self.assertGreaterEqual(report["warning_count"], 1)
        result = detect(norm, self.config, synthetic=False)
        ex_a = next(r for r in result["rows"] if r["sim_id"] == "EXAMPLE-A")
        self.assertEqual(ex_a["entitlement_covered_zar"], 0.0)
        self.assertEqual(ex_a["missing_billing_evidence_zar"], 100.0)
        self.assertEqual(ex_a["unbilled_retail_zar"], 0.0)
        self.assertEqual(ex_a["predicted_archetype_code"], "MISSING_BILLING_EVIDENCE")

    def test_validation_catches_duplicates_dates_unmatched_ids_and_invalid_numbers(self):
        bad = copy.deepcopy(self.tables)
        # Duplicate subscriber
        bad["subscribers"].append(dict(bad["subscribers"][0]))
        # Unmatched SIM in usage, usage before activation, and invalid night ratio > 1
        bad["usage_daily"].append({
            **bad["usage_daily"][0],
            "sim_id": "UNMATCHED-SIM",
            "usage_date": "2025-01-01",
            "night_usage_ratio": 1.5,
        })
        # Duplicate billing record key
        bad["billing_events"].append(dict(bad["billing_events"][0]))

        _, report = validate_tables(bad, self.headers, self.config)
        self.assertFalse(report["valid"])
        messages = " | ".join(i["message"] for i in report["issues"])
        self.assertIn("Duplicate subscriber ID", messages)
        self.assertIn("No matching subscriber ID", messages)
        self.assertIn("night ratio at most 1", messages)
        self.assertIn("Duplicate record key", messages)

    def test_parse_file_validates_csv_headers_and_row_structure(self):
        rows, headers = parse_file({
            "name": "subscribers.csv",
            "content_base64": to_b64_csv(self.tables["subscribers"]),
        })
        self.assertEqual(len(rows), 4)
        self.assertIn("sim_id", headers)

        bad_csv = base64.b64encode(b"sim_id,sim_id\n1,2\n").decode("ascii")
        with self.assertRaises(UploadError):
            parse_file({"name": "dup.csv", "content_base64": bad_csv})

    def test_dataset_store_requires_warning_acknowledgment_and_preserves_content_hash(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = DatasetStore(Path(tmp.name) / "datasets.sqlite3")

        # Create a warning by omitting optional rate columns
        tables = copy.deepcopy(self.tables)
        for u in tables["usage_daily"]:
            u.pop("data_rate_zar_per_mb", None)
        headers = {k: list(v[0]) for k, v in tables.items()}
        norm, report = validate_tables(tables, headers, self.config)
        self.assertTrue(report["valid"])
        self.assertGreater(report["warning_count"], 0)

        staged = store.stage("Test upload", "uploaded", norm, self.config, report)
        self.assertEqual(staged["status"], "READY_FOR_REVIEW")

        # Running without acknowledging warnings fails
        with self.assertRaises(UploadError):
            store.run(staged["id"], acknowledge_warnings=False)

        completed = store.run(staged["id"], acknowledge_warnings=True)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertIsNotNone(completed["last_successful_run"])
        self.assertIsNotNone(completed["warnings_acknowledged_at"])

        # Re-staging identical tables/config returns the same content-addressed dataset ID
        staged_again = store.stage("Test upload", "uploaded", norm, self.config, report)
        self.assertEqual(staged_again["id"], staged["id"])
        self.assertEqual(staged_again["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
