"""Example upload files containing no customer data."""
def example_tables():
    subscribers, usage, billing = [], [], []
    for suffix in ("A", "B", "C", "D"):
        sid = f"EXAMPLE-{suffix}"
        subscribers.append(dict(sim_id=sid, plan_type="ENTERPRISE" if suffix == "B" else "PREPAID",
            hlr_status="ACTIVE", billing_status="ACTIVE", tariff_code="EXAMPLE_STANDARD",
            activation_date="2026-01-01", parent_account_id="EXAMPLE-PARENT" if suffix == "B" else "",
            is_enterprise_child=suffix == "B", entitlement_bundle_mb=90 if suffix == "A" else 0,
            entitlement_voice_min=0, entitlement_valid_from="2026-10-01", entitlement_valid_to="2026-10-31"))
        record = dict(sim_id=sid, usage_date="2026-10-26" if suffix == "D" else "2026-10-20",
            data_mb=100, voice_mins=0, sms_count=0, primary_apn="internet",
            primary_cell_id="JHB-SANDTON-001", rated_amount_zar=0,
            session_count=1, night_usage_ratio=.2, unique_destinations=1, distinct_cells=1, distinct_imeis=1,
            data_rate_zar_per_mb=1, voice_rate_zar_per_min=1, sms_rate_zar_per_msg=.5)
        usage.append(record)
        if suffix == "C":
            usage.append(dict(record, primary_apn="mtn.zero.edu"))
        billing.append(dict(sim_id=sid, billing_date="2026-10-27", invoice_billed_zar=0,
            parent_account_allocated_zar=50 if suffix == "B" else 0,
            usage_period_start="2026-10-01", usage_period_end="2026-10-27"))
    return dict(subscribers=subscribers, usage_daily=usage, billing_events=billing)
