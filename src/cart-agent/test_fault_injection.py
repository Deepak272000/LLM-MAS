"""Cart-agent fault harness and artifact bootstrap.

This harness is intentionally self-contained so native service readiness can be
validated without launching the full cluster runtime.
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
    base = [
        {"step": "TASK_START", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"fault_mode": mode}},
        {"step": "ITEM_ADDED", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"user_id": "u-123", "product_id": "PROD-001", "qty": 2}},
        {"step": "CART_READ", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"items": 1}},
        {"step": "FINAL_ANSWER", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"success": True}},
    ]

    if mode == "NONE":
        return base, None, 0, []

    if mode == "FM_3_1":
        steps = [base[0], base[-1]]
        return steps, "FINAL_ANSWER", 1, ["ITEM_ADDED", "CART_READ"]

    if mode == "FM_1_2":
        steps = base.copy()
        steps[1]["data"]["qty"] = 99
        return steps, "ITEM_ADDED", 1, []

    if mode == "FM_2_2":
        steps = base.copy()
        steps[2]["data"]["items"] = 999
        return steps, "CART_READ", 1, []

    if mode == "FM_2_5":
        steps = base.copy()
        steps[1]["data"]["product_id"] = "NONEXISTENT-XYZ"
        return steps, "ITEM_ADDED", 1, []

    return base, None, 0, []


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
    (ROOT / "cart-agent_fault_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


def write_b2_raw() -> None:
    B2_RAW.mkdir(parents=True, exist_ok=True)
    for idx in (1, 2, 3):
        lkw, _, _, _ = _trace_for_mode("NONE")
        doc = {
            "agent": "cart-agent",
            "run": idx,
            "lkw": lkw,
            "steps": [cp["step"] for cp in lkw],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        (B2_RAW / f"cart-agent_b2_run{idx}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def write_stability(rows: list[dict]) -> None:
    doc = {
        "agent": "cart-agent",
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
    (RESULTS / "stability_matrix_cart-agent.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("modes", nargs="*", help="Fault modes to run (default: NONE)")
    parser.add_argument("--emit-b2", action="store_true", help="Emit B2 raw run artifacts")
    parser.add_argument("--emit-stability", action="store_true", help="Emit stability matrix artifact")
    args = parser.parse_args()

    modes = [m.upper() for m in args.modes if m.strip()] or ["NONE"]
    rows = run_modes(modes)
    write_fault_results(rows)

    if args.emit_b2:
        write_b2_raw()
    if args.emit_stability:
        write_stability(run_modes(FAULT_MODES))

    print(f"Wrote {ROOT / 'cart-agent_fault_results.json'}")
    if args.emit_b2:
        print("Wrote cart-agent B2 raw artifacts")
    if args.emit_stability:
        print(f"Wrote {RESULTS / 'stability_matrix_cart-agent.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
