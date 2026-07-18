"""Run the live boundary validation demo.

This clears results/boundary_events.jsonl, enables boundary event emission,
then runs the evidence scripts that produce the professor-facing examples.
Open boundary_dashboard.py in another terminal to watch events appear live.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
EVENTS = RESULTS / "boundary_events.jsonl"
RECOVERY_SUMMARY = RESULTS / "recovery_demo_summary.json"
SCRIPTS = [
    "cross_agent_propagation.py",
    "boundary_detection_runner.py",
    "repo_hitl_audit.py",
]


def load_events() -> list[dict]:
    if not EVENTS.exists():
        return []
    events = []
    with EVENTS.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def write_recovery_summary(events: list[dict]) -> dict:
    cross_path = RESULTS / "cross_agent_propagation.json"
    boundary_path = RESULTS / "boundary_detection_summary.json"
    cross = json.loads(cross_path.read_text(encoding="utf-8")) if cross_path.exists() else {}
    boundary = json.loads(boundary_path.read_text(encoding="utf-8")) if boundary_path.exists() else {}

    chains = cross.get("chains", [])
    chain_a = chains[0] if len(chains) > 0 else {}
    chain_b = chains[1] if len(chains) > 1 else {}
    shipping = {item.get("scenario"): item for item in boundary.get("shipping", [])}
    shipping_fm22 = shipping.get("shipping_fm_2_2", {})
    shipping_fm25 = shipping.get("shipping_fm_2_5", {})

    carrier_boundary = next(
        (
            check for check in shipping_fm22.get("boundary_checks", [])
            if check.get("boundary") == "carrier_to_tracking"
        ),
        {},
    )
    quote_selection = shipping_fm25.get("boundary_check", {})

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_counts": {
            "total": len(events),
            "alerts": sum(1 for event in events if event.get("alert")),
            "clean": sum(1 for event in events if not event.get("alert")),
        },
        "cases": [
            {
                "case": "Payment overcharge",
                "boundary": "currency_to_payment",
                "flag_seen": f"expected={chain_a.get('baseline_units')} observed={chain_a.get('propagated_units')}",
                "recovery_action": (chain_a.get("boundary_contract", {}).get("recovery") or {}).get("action"),
                "applied": bool(chain_a.get("charge_blocked")),
                "outcome": f"charge_blocked={chain_a.get('charge_blocked')} prevented_loss_eur={chain_a.get('prevented_loss_eur')}",
            },
            {
                "case": "Catalog hallucination",
                "boundary": "catalog_to_recommendation",
                "flag_seen": f"expected={chain_b.get('baseline_product_ids')} observed={chain_b.get('propagated_product_ids')}",
                "recovery_action": (chain_b.get("boundary_contract", {}).get("recovery") or {}).get("action"),
                "applied": bool(chain_b.get("recovered_product_ids")),
                "outcome": f"recovered_product_ids={chain_b.get('recovered_product_ids')}",
            },
            {
                "case": "Shipping carrier hallucination",
                "boundary": "carrier_to_tracking",
                "flag_seen": f"observed={carrier_boundary.get('observed')}",
                "recovery_action": (carrier_boundary.get("recovery") or {}).get("action"),
                "applied": "RECOVERY_ACTION" in shipping_fm22.get("rip_summary", {}).get("reachability", []),
                "outcome": f"corrected_payload={(carrier_boundary.get('recovery') or {}).get('corrected_payload')}",
            },
            {
                "case": "Shipping quote selection mismatch",
                "boundary": "quote_to_carrier_selection",
                "flag_seen": f"difference={quote_selection.get('difference')}",
                "recovery_action": (quote_selection.get("recovery") or {}).get("action"),
                "applied": quote_selection.get("alert") is True,
                "outcome": "retry_current_step decision recorded for carrier selection",
            },
        ],
    }
    RECOVERY_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    EVENTS.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["BOUNDARY_EVENTS_ENABLED"] = "1"
    env["BOUNDARY_EVENTS_FILE"] = str(EVENTS)

    print("Live boundary demo")
    print(f"Events file: {EVENTS}")
    print("Dashboard command: python boundary_dashboard.py")
    print()

    for script in SCRIPTS:
        print("=" * 78)
        print(f"Running {script}")
        print("=" * 78)
        completed = subprocess.run([sys.executable, script], cwd=ROOT, env=env)
        if completed.returncode != 0:
            return completed.returncode

    events = load_events()
    alerts = [event for event in events if event.get("alert")]
    recovery_summary = write_recovery_summary(events)
    recovery_counts = Counter((event.get("recovery") or {}).get("action", "n/a") for event in events)
    print("=" * 78)
    print("LIVE BOUNDARY EVENT SUMMARY")
    print("=" * 78)
    print(f"Total events : {len(events)}")
    print(f"Alerts       : {len(alerts)}")
    print(f"Clean checks : {len(events) - len(alerts)}")
    print("Recovery actions:")
    for action, count in sorted(recovery_counts.items()):
        print(f"  {action}: {count}")
    for event in alerts:
        recovery = event.get("recovery") or {}
        print(
            f"- {event.get('boundary')} | status={event.get('status')} | "
            f"recovery={recovery.get('action')} | difference={event.get('difference')}"
        )
    print("\nExecution recovery evidence:")
    for case in recovery_summary["cases"]:
        print(
            f"- {case['case']} | action={case['recovery_action']} | "
            f"applied={case['applied']} | {case['outcome']}"
        )
    print(f"\nOpen http://127.0.0.1:8765 to inspect the event table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())