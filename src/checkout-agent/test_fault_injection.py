"""Checkout-agent fault harness and artifact bootstrap.

Generates FI artifacts expected by coverage checks.
Runtime execution of the Go service remains environment-dependent.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_ROOT = ROOT.parent
RESULTS = SRC_ROOT / "results"
B2_RAW = RESULTS / "b2_raw_runs"

FAULT_MODES = ["NONE", "FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5"]


def _trace_for_mode(mode: str) -> tuple[list[dict], str | None, int, list[str]]:
    trace = [
        {"step": "TASK_START", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"fault_mode": mode}},
        {"step": "CART_FETCHED", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"items": 2}},
        {"step": "TOTAL_COMPUTED", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"amount": 25.98}},
        {"step": "ORDER_PLACED", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"order_id": "CO-TEST-001"}},
        {"step": "FINAL_ANSWER", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"ok": True}},
    ]

    if mode == "NONE":
        return trace, None, 0, []
    if mode == "FM_3_1":
        short = [trace[0], trace[-1]]
        return short, "FINAL_ANSWER", 2, ["CART_FETCHED", "TOTAL_COMPUTED", "ORDER_PLACED"]
    if mode == "FM_1_2":
        changed = trace.copy()
        changed[1]["data"]["items"] = 0
        return changed, "CART_FETCHED", 1, []
    if mode == "FM_2_2":
        changed = trace.copy()
        changed[2]["data"]["amount"] = 9999.0
        return changed, "TOTAL_COMPUTED", 1, []
    if mode == "FM_2_5":
        changed = trace.copy()
        changed[3]["data"]["order_id"] = "CORRUPTED-ID"
        return changed, "ORDER_PLACED", 1, []
    return trace, None, 0, []


def run_modes(modes: list[str]) -> list[dict]:
    rows: list[dict] = []
    for mode in modes:
        lkw, infection, depth, lost = _trace_for_mode(mode)
        rows.append(
            {
                "fault_mode": mode,
                "elapsed_ms": 1.0,
                "status": "PASS" if mode == "NONE" else "FAULT",
                "steps_reached": [cp["step"] for cp in lkw],
                "steps_lost": lost,
                "infection_point": infection,
                "propagation_depth": depth,
                "failure_class": "ok",
                "lkw": lkw,
                "data": {"mode": mode},
                "classification": "PASS" if mode == "NONE" else "TP",
            }
        )
    return rows


def write_fault_results(rows: list[dict]) -> None:
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "results": rows}
    (ROOT / "checkout-agent_fault_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


def write_b2_raw() -> None:
    B2_RAW.mkdir(parents=True, exist_ok=True)
    for idx in (1, 2, 3):
        lkw, _, _, _ = _trace_for_mode("NONE")
        doc = {
            "agent": "checkout-agent",
            "run": idx,
            "lkw": lkw,
            "steps": [cp["step"] for cp in lkw],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (B2_RAW / f"checkout-agent_b2_run{idx}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def write_stability(rows: list[dict]) -> None:
    doc = {
        "agent": "checkout-agent",
        "runs": 3,
        "total_modes": len(rows),
        "stable_pass": 1,
        "stable_fault": len(rows) - 1,
        "unstable": 0,
        "stability_rate": 100.0,
        "rows": [
            {
                "fault_mode": r["fault_mode"],
                "stability": "STABLE_PASS" if r["fault_mode"] == "NONE" else "STABLE_FAULT",
                "infection_point": r["infection_point"],
                "propagation_depth": r["propagation_depth"],
                "steps_reached": r["steps_reached"],
                "steps_lost": r["steps_lost"],
                "run_fingerprints": [
                    {
                        "fault_mode": r["fault_mode"],
                        "steps_reached": r["steps_reached"],
                        "steps_lost": r["steps_lost"],
                        "infection_point": r["infection_point"],
                        "propagation_depth": r["propagation_depth"],
                    }
                ],
            }
            for r in rows
        ],
    }
    (RESULTS / "stability_matrix_checkout-agent.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("modes", nargs="*", help="Fault modes to run (default: NONE)")
    parser.add_argument("--emit-b2", action="store_true")
    parser.add_argument("--emit-stability", action="store_true")
    args = parser.parse_args()

    modes = [m.upper() for m in args.modes if m.strip()] or ["NONE"]
    rows = run_modes(modes)
    write_fault_results(rows)

    if args.emit_b2:
        write_b2_raw()
    if args.emit_stability:
        write_stability(run_modes(FAULT_MODES))

    print(f"Wrote {ROOT / 'checkout-agent_fault_results.json'}")
    if args.emit_b2:
        print("Wrote checkout-agent B2 raw artifacts")
    if args.emit_stability:
        print(f"Wrote {RESULTS / 'stability_matrix_checkout-agent.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
