"""Bounded file parsing and cross-table validation before any detection run."""
import base64
import csv
import io
import math
from collections import Counter
from datetime import date

from src.schema import APN_CATALOG, CELL_SITES

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_ROWS = 100000
REQUIRED = {
    "subscribers": ["sim_id", "plan_type", "hlr_status", "billing_status", "tariff_code", "activation_date"],
    "usage_daily": ["sim_id", "usage_date", "data_mb", "voice_mins", "sms_count",
                    "primary_apn", "primary_cell_id", "rated_amount_zar"],
    "billing_events": ["sim_id", "billing_date", "invoice_billed_zar", "parent_account_allocated_zar"],
}
NUMBERS = {
    "subscribers": {"entitlement_bundle_mb": 0, "entitlement_voice_min": 0},
    "usage_daily": {"data_mb": 0, "voice_mins": 0, "sms_count": 0, "rated_amount_zar": 0,
                    "session_count": 0, "night_usage_ratio": 0, "unique_destinations": 0,
                    "distinct_cells": 0, "distinct_imeis": 0},
    "billing_events": {"invoice_billed_zar": 0, "parent_account_allocated_zar": 0,
                       "topup_zar": 0, "bundle_purchase_zar": 0},
}
COUNTS = {"sms_count", "session_count", "unique_destinations", "distinct_cells", "distinct_imeis"}
RATES = {"data_rate_zar_per_mb": 0.29, "voice_rate_zar_per_min": 1.20, "sms_rate_zar_per_msg": 0.50}
OPTIONAL = {
    "subscribers": ["imsi", "msisdn", "account_id", "parent_account_id", "channel", "is_enterprise_child",
                    "entitlement_valid_from", "entitlement_valid_to", "entitlement_apn", "ground_truth_label"],
    "usage_daily": ["device_type", "retail_equivalent_zar", *RATES],
    "billing_events": ["billing_event_id", "usage_period_start", "usage_period_end", "total_revenue_zar"],
}


class UploadError(ValueError):
    pass


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use YYYY-MM-DD")
    return date.fromisoformat(value)


def parse_file(file):
    if not isinstance(file, dict) or not isinstance(file.get("name"), str):
        raise UploadError("Each file needs a name and base64 content.")
    try:
        encoded = file.get("content_base64", "")
        if not isinstance(encoded, str) or len(encoded) > MAX_FILE_BYTES * 4 // 3 + 8:
            raise UploadError("Each file must be at most 15 MB.")
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise UploadError("Invalid file encoding.") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise UploadError("Each file must be at most 15 MB.")
    name = file["name"].lower()
    if name.endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
            original = reader.fieldnames or []
            headers = [h.strip() for h in original]
            if not headers or len(headers) != len(set(headers)) or any(not h for h in headers):
                raise UploadError("CSV headers must be present, nonempty and unique.")
            reader.fieldnames = headers
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise UploadError(f"CSV record near line {reader.line_num} has the wrong number of columns.")
                rows.append(row)
                if len(rows) > MAX_ROWS:
                    raise UploadError("Each table is limited to 100,000 rows.")
        except (UnicodeError, csv.Error) as exc:
            raise UploadError("Read failed. Use a UTF-8 CSV with consistent columns.") from exc
    elif name.endswith(".parquet"):
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise UploadError("Parquet support requires pyarrow on the server. Export UTF-8 CSV instead.") from exc
        try:
            file_reader = pq.ParquetFile(io.BytesIO(raw))
            if file_reader.metadata.num_rows > MAX_ROWS or file_reader.metadata.num_columns > 100:
                raise UploadError("Parquet exceeds the 100,000 row or 100 column limit.")
            if sum(file_reader.metadata.row_group(i).total_byte_size
                   for i in range(file_reader.metadata.num_row_groups)) > 100 * 1024 * 1024:
                raise UploadError("Parquet expands beyond the 100 MB decoded limit.")
            table = file_reader.read()
            headers, rows = table.column_names, table.to_pylist()
            if len(headers) != len(set(headers)):
                raise UploadError("Duplicate Parquet columns.")
        except UploadError:
            raise
        except Exception as exc:
            raise UploadError("Unable to read the Parquet file.") from exc
    else:
        raise UploadError("Use .csv or .parquet files.")
    if len(headers) > 100:
        raise UploadError("Each table is limited to 100 columns.")
    return rows, headers


def validate_tables(tables, headers, config):
    """Normalize only supported columns. Blocking errors never silently discard rows."""
    issues, counts = [], Counter()

    def issue(severity, table, row, column, message):
        counts[severity] += 1
        if len(issues) < 200:
            issues.append(dict(severity=severity, table=table, row=row, column=column, message=message))

    try:
        as_of = iso_date(config.get("as_of_date"))
        grace = config.get("billing_grace_days", 3)
        if type(grace) is not int or not 0 <= grace <= 60:
            raise ValueError()
    except (ValueError, TypeError):
        raise UploadError("Choose an as-of date (YYYY-MM-DD) and billing delay allowance from 0 to 60 days.")
    normalized = {}
    for name, required in REQUIRED.items():
        rows = tables.get(name, [])
        if not rows and name != "billing_events":
            issue("error", name, None, "", "At least one data row is required.")
        for col in set(required) - set(headers.get(name, [])):
            issue("error", name, None, col, "Required column missing.")
        supported = set(required) | set(NUMBERS[name]) | set(OPTIONAL[name])
        extra = set(headers.get(name, [])) - supported
        if extra:
            issue("warning", name, None, "", "Ignored columns: " + ", ".join(sorted(extra)))
        normalized[name] = []
        for index, original in enumerate(rows, 2):
            row = {k: "" if original.get(k) is None else str(original[k]).strip() for k in supported if k in original}
            for col in required:
                if not row.get(col):
                    issue("error", name, index, col, "Required value missing.")
            for col, default in {**NUMBERS[name], **(RATES if name == "usage_daily" else {})}.items():
                try:
                    # A supplied blank is an error; an absent optional column has a documented default.
                    value = float(row[col]) if col in row else float(default)
                    if not math.isfinite(value) or not 0 <= value <= 1e12:
                        raise ValueError()
                    if col in COUNTS and not value.is_integer():
                        raise ValueError()
                    if col == "night_usage_ratio" and value > 1:
                        raise ValueError()
                    row[col] = value
                except (ValueError, TypeError):
                    issue("error", name, index, col, "Expected a finite nonnegative number; counts must be whole numbers and night ratio at most 1.")
                    row[col] = 0.0
            date_cols = {"subscribers": ["activation_date", "entitlement_valid_from", "entitlement_valid_to"],
                         "usage_daily": ["usage_date"], "billing_events": ["billing_date", "usage_period_start", "usage_period_end"]}[name]
            for col in date_cols:
                if row.get(col):
                    try:
                        iso_date(row[col])
                    except (ValueError, TypeError):
                        issue("error", name, index, col, "Expected a valid YYYY-MM-DD date.")
            if name == "subscribers":
                flag = row.get("is_enterprise_child", "false").lower()
                if flag not in ("true", "false", "1", "0"):
                    issue("error", name, index, "is_enterprise_child", "Use true/false or 1/0.")
                row["is_enterprise_child"] = flag in ("true", "1")
                if row["is_enterprise_child"] and not row.get("parent_account_id"):
                    issue("error", name, index, "parent_account_id", "Enterprise child requires a parent account.")
                if row["entitlement_bundle_mb"] or row["entitlement_voice_min"]:
                    if not row.get("entitlement_valid_from") or not row.get("entitlement_valid_to"):
                        issue("error", name, index, "entitlement_valid_from", "Positive entitlements require valid-from and valid-to dates.")
                start, end = row.get("entitlement_valid_from"), row.get("entitlement_valid_to")
                if start and end and start > end:
                    issue("error", name, index, "entitlement_valid_to", "Entitlement ends before it begins.")
            if name == "billing_events":
                start, end = row.get("usage_period_start"), row.get("usage_period_end")
                if (row["invoice_billed_zar"] or row["parent_account_allocated_zar"]) and (not start or not end):
                    issue("error", name, index, "usage_period_start", "Invoice/parent coverage requires its usage period start and end.")
                if bool(start) != bool(end) or (start and end and start > end):
                    issue("error", name, index, "usage_period_end", "Provide a complete, ordered usage period.")
                if row.get("billing_date", "") > as_of.isoformat():
                    issue("warning", name, index, "billing_date", "Event after the as-of date will not count as coverage yet.")
            normalized[name].append(row)

    subscribers = {}
    for index, row in enumerate(normalized["subscribers"], 2):
        sid = row.get("sim_id", "")
        if sid in subscribers:
            issue("error", "subscribers", index, "sim_id", "Duplicate subscriber ID.")
        subscribers[sid] = row
    apns = {r["apn"] for r in APN_CATALOG}
    cells = {r["cell_id"] for r in CELL_SITES}
    usage_ids, billing_ids, dates, unknown_apns, unknown_cells = set(), set(), [], set(), set()
    for name in ("usage_daily", "billing_events"):
        seen = set()
        for index, row in enumerate(normalized[name], 2):
            sid = row.get("sim_id", "")
            if sid not in subscribers:
                issue("error", name, index, "sim_id", "No matching subscriber ID.")
            if name == "usage_daily":
                key = tuple(row.get(k) for k in ("sim_id", "usage_date", "primary_apn", "primary_cell_id"))
                usage_ids.add(sid)
                day = row.get("usage_date", "")
                try:
                    iso_date(day)
                    dates.append(day)
                    if day > as_of.isoformat():
                        issue("error", name, index, "usage_date", "Usage cannot be after the as-of date.")
                    if day < subscribers.get(sid, {}).get("activation_date", ""):
                        issue("error", name, index, "usage_date", "Usage is before subscriber activation.")
                except ValueError:
                    pass
                if row.get("primary_apn") not in apns:
                    unknown_apns.add(row.get("primary_apn", ""))
                if row.get("primary_cell_id") not in cells:
                    unknown_cells.add(row.get("primary_cell_id", ""))
            else:
                key = ("event", row["billing_event_id"]) if row.get("billing_event_id") else tuple(
                    row.get(k) for k in ("sim_id", "billing_date", "usage_period_start", "usage_period_end"))
                if row.get("billing_date", "") <= as_of.isoformat():
                    billing_ids.add(sid)
                if row["parent_account_allocated_zar"] and not subscribers.get(sid, {}).get("parent_account_id"):
                    issue("error", name, index, "parent_account_allocated_zar", "Parent allocation has no linked parent account.")
            if key in seen:
                issue("error", name, index, "sim_id", "Duplicate record key; aggregate usage by SIM/date/APN/cell or give distinct billing events unique billing_event_id values.")
            seen.add(key)
    if dates and (iso_date(max(dates)) - iso_date(min(dates))).days > 30:
        issue("error", "usage_daily", None, "usage_date", "MVP analysis window is limited to 31 days. Split longer exports.")
    if usage_ids - billing_ids:
        issue("warning", "billing_events", None, "sim_id", f"{len(usage_ids - billing_ids)} used SIM(s) have no billing snapshot as of the chosen date; residuals will be marked missing billing evidence.")
    if set(subscribers) - usage_ids:
        issue("warning", "usage_daily", None, "sim_id", f"{len(set(subscribers) - usage_ids)} subscriber(s) have no usage rows; they remain visible as no usage supplied.")
    if unknown_apns:
        issue("warning", "usage_daily", None, "primary_apn", f"{len(unknown_apns)} unknown APN(s); no zero-rating inferred for them.")
    if unknown_cells:
        issue("warning", "usage_daily", None, "primary_cell_id", f"{len(unknown_cells)} unknown cell(s); location and area type remain unknown.")
    if not set(RATES).issubset(headers.get("usage_daily", [])):
        issue("warning", "usage_daily", None, "rates", "Missing rate columns use illustrative reference rates (R0.29/MB, R1.20/min, R0.50/SMS); residual value is an estimate, not an invoice.")
    missing_features = set(NUMBERS["usage_daily"]) - set(REQUIRED["usage_daily"]) - set(headers.get("usage_daily", []))
    if missing_features:
        issue("warning", "usage_daily", None, "features", "Missing optional behavior features default to zero: " + ", ".join(sorted(missing_features)))
    for name, rows in normalized.items():
        for index, row in enumerate(rows, 2):
            for col, value in row.items():
                if isinstance(value, str) and (len(value) > 256 or any(ord(ch) < 32 for ch in value)):
                    issue("error", name, index, col, "Text must be at most 256 characters with no control characters.")
    return normalized, dict(valid=counts["error"] == 0, error_count=counts["error"],
        warning_count=counts["warning"], issues=issues, issues_truncated=sum(counts.values()) > len(issues),
        row_counts={k: len(v) for k, v in normalized.items()},
        window_start=min(dates) if dates else None, window_end=max(dates) if dates else None)
