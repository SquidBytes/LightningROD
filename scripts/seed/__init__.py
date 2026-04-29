"""Seed/demo data package — modular, contract-driven, time-shifting.

Replaces the legacy seed scripts (seed.py, seed_sample.py, seed_review_queue.py,
generate_sample_data.py). Activated by DEMO_MODE=true env var or via direct CLI.

CLI:
    uv run python -m scripts.seed.main --all
    uv run python -m scripts.seed.main --module=<name>
    uv run python -m scripts.seed.main --dry-run

Modules (run in this FK-respecting order):
    vehicle           — sample F-150 Lightning (active demo vehicle)
    locations         — Home / Work / Tesla SC / EA Costco
    networks          — extras (Blink, Home) on top of migration-seeded networks
    evse_stalls       — Home L2 / Work L2 / Tesla SC / EA Costco
    subscriptions     — Tesla Premium / EA+ / ChargePoint Free
    charging_sessions — 90-day mix of AC home/work + DC public sessions
    battery_status    — readings correlated to sessions + intermediates
    trip_metrics      — 60-80 trips with realistic distance/energy/regen
    vehicle_status    — telemetry snapshots; defines EXPECTED_CONTRACTS used by gap report
    gps_location      — GPS time-series along trip windows
    review_queue      — flagged sessions with cross-source dedup pairs
    gas_prices        — 18 months history + 30 days readings

Shared facilities (`base.py`):
    compute_global_offset       — single offset across all timestamp tables
    apply_offset_to_table       — bulk shift via UPDATE
    ContractDrivenSeeder        — realistic-value generator + gap recorder
    write_contracts_gap_report  — emit copy-paste-ready FieldContract blocks
    load_declared_contracts     — load FIELD_CONTRACTS from the HA Fordpass adapter
"""
