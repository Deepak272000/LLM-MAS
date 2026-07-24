"""
HITL (Human-in-the-Loop) Automated Detector
=============================================
Reads all *_lkw_rip_evidence.json files in this directory and automatically
classifies each fault scenario into one of three intervention tiers:

  TIER 1 — Silently Absorbed
    Flow completes, no flag or error. Undetectable from step trace alone.
    Highest latent business risk. Requires human checkpoint-data inspection.

  TIER 2 — Detectable via Metadata Flags
    Flow completes but a deviation flag is recorded in checkpoint data.
    Detectable, but agent does NOT self-heal. Requires human monitor/alert.

  TIER 3 — Structurally Disruptive / Workflow-Stopping
    Steps are missing from the trace OR the workflow fails entirely.
    Directly observable from step-trace diff. Requires human or architectural
    intervention.

Usage:
    python hitl_detector.py
    python hitl_detector.py --json   (machine-readable output)

Output:
    Console report + hitl_classification_report.json
"""

from __future__ import annotations

import json
import math
import os
import glob
import argparse
from datetime import datetime, timezone

# ── Known HITL flag keys in checkpoint data ────────────────────────────────────
HITL_FLAGS = {
    "escalation_required":        "Customer escalation flag set — high-risk order needs human review",
    "item_count_inflated":         "Item quantity inflated — inventory mismatch, fulfillment at risk",
    "forced_vendor":               "Carrier forced to non-preferred vendor — cost threshold breached",
    "ignored_downstream_quote":    "Stale cost used in carrier selection — financial data integrity risk",
    "premature_termination":       "Workflow terminated early — carrier/tracking steps skipped",
    "save_skipped":                "MongoDB save bypassed — shipment record not persisted",
    "compliance_failed":           "Compliance jurisdiction unresolvable — export restriction may apply",
    "incomplete_task":             "Task spec was corrupted/incomplete — carrier/tracking steps missing",
}

EXPECTED_STEPS = {
    "TASK_START", "QUOTE_DONE", "CARRIER_DONE",
    "TRACKING_DONE", "SAVE_DONE", "ESCALATION_CHECK", "FINAL_ANSWER"
}


def load_evidence_files(directory: str) -> list[dict]:
    pattern = os.path.join(directory, "*_lkw_rip_evidence.json")
    files = glob.glob(pattern)
    results = []
    for f in sorted(files):
        try:
            with open(f) as fh:
                data = json.load(fh)
            data["_source_file"] = os.path.basename(f)
            results.append(data)
        except Exception as e:
            print(f"  [WARN] Could not load {f}: {e}")
    return results


def extract_checkpoints(run: dict) -> list[dict]:
    """Pull checkpoints out of a run dict (handles both top-level and nested lkw)."""
    if isinstance(run, dict):
        if "lkw" in run and "checkpoints" in run["lkw"]:
            return run["lkw"]["checkpoints"]
        if "checkpoints" in run:
            return run["checkpoints"]
    return []


def get_steps_present(checkpoints: list[dict]) -> set[str]:
    return {cp["step"] for cp in checkpoints if "step" in cp}


def scan_flags(checkpoints: list[dict]) -> list[tuple[str, str]]:
    """Return list of (flag_key, description) found in any checkpoint's data."""
    found = []
    for cp in checkpoints:
        data = cp.get("data", {})
        for flag, desc in HITL_FLAGS.items():
            val = data.get(flag)
            if val is True or val == "true":
                found.append((flag, desc))
            # Check for negative-zero / negative cost (BL_REFUND_REASONING)
            if flag == "ignored_downstream_quote" and isinstance(data.get("cost_usd"), (int, float)):
                cost = data["cost_usd"]
                if math.copysign(1.0, cost) < 0:
                    found.append(("negative_cost", "Negative cost detected — refund/corruption condition present"))
            # Check for PREMATURE or INCOMPLETE tracking IDs
            tracking = data.get("tracking_id", "")
            if "PREMATURE" in str(tracking):
                found.append(("premature_termination", HITL_FLAGS["premature_termination"]))
            if "INCOMPLETE" in str(tracking):
                found.append(("incomplete_task", HITL_FLAGS["incomplete_task"]))
            if "COMPLIANCE-FAILED" in str(tracking):
                found.append(("compliance_failed", HITL_FLAGS["compliance_failed"]))
    # Deduplicate
    return list(dict.fromkeys(found))


def classify(fault_mode: str, fault_checkpoints: list[dict],
             baseline_steps: set[str], error: str | None) -> dict:
    fault_steps = get_steps_present(fault_checkpoints)
    steps_lost = baseline_steps - fault_steps
    flags = scan_flags(fault_checkpoints)

    if error and ("exceeded" in error.lower() or "failed" in error.lower()):
        tier = 3
        tier_label = "TIER 3 — Workflow-Stopping"
        hitl_reason = f"Workflow did not complete: {error}"
        auto_detectable = True
    elif steps_lost:
        tier = 3
        tier_label = "TIER 3 — Structurally Disruptive"
        hitl_reason = f"Steps missing from trace: {', '.join(sorted(steps_lost))}"
        auto_detectable = True
    elif flags:
        tier = 2
        tier_label = "TIER 2 — Detectable via Metadata Flags"
        hitl_reason = "; ".join(desc for _, desc in flags)
        auto_detectable = True
    else:
        tier = 1
        tier_label = "TIER 1 — Silently Absorbed"
        hitl_reason = "Flow completed with no missing steps and no deviation flags. Manual checkpoint-data inspection required."
        auto_detectable = False

    return {
        "fault_mode":       fault_mode,
        "tier":             tier,
        "tier_label":       tier_label,
        "hitl_required":    tier >= 2,
        "auto_detectable":  auto_detectable,
        "hitl_reason":      hitl_reason,
        "steps_lost":       sorted(steps_lost),
        "flags_found":      [f for f, _ in flags],
        "propagation_depth": len(steps_lost),
    }


def analyze_evidence(evidence: dict) -> dict | None:
    source = evidence.get("_source_file", "unknown")

    baseline = evidence.get("baseline", {})
    baseline_checkpoints = extract_checkpoints(baseline)
    baseline_steps = get_steps_present(baseline_checkpoints) or EXPECTED_STEPS

    # Find the fault run — stored under a scenario-specific key (e.g. "fm_3_1",
    # "bl_customer_escalation") — any key that is not a metadata field.
    SKIP_KEYS = {"generated_at", "scope", "baseline", "_source_file", "fault_mode", "runs"}
    fault_run = None
    fault_mode = evidence.get("fault_mode", "UNKNOWN")
    for key, val in evidence.items():
        if key in SKIP_KEYS:
            continue
        if isinstance(val, dict) and val.get("fault_mode") not in (None, "NONE"):
            fault_run = val
            fault_mode = val.get("fault_mode", key.upper())
            break

    if not fault_run:
        # Fallback: list of runs
        runs = evidence.get("runs", [])
        fault_runs = [r for r in runs if r.get("fault_mode") not in (None, "NONE")]
        if not fault_runs:
            return None
        fault_run = fault_runs[0]
        fault_mode = fault_run.get("fault_mode", "UNKNOWN")

    fault_checkpoints = extract_checkpoints(fault_run)
    error = fault_run.get("error")

    result = classify(fault_mode, fault_checkpoints, baseline_steps, error)
    result["source_file"] = source
    return result


def analyze_rip_results(path: str) -> list[dict]:
    """Parse lkw_rip_results.json which stores all scenarios in a flat list."""
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [WARN] Could not load {path}: {e}")
        return []

    results = []
    for entry in data.get("rip_analysis", []):
        fault_mode = entry.get("fault_mode", "UNKNOWN")
        error = entry.get("error")
        steps_lost = set(entry.get("steps_lost", []))
        propagation_depth = entry.get("propagation_depth", len(steps_lost))

        if error and ("exceeded" in str(error).lower() or "failed" in str(error).lower()):
            tier, tier_label = 3, "TIER 3 — Workflow-Stopping"
            hitl_reason = f"Workflow did not complete: {error}"
            auto_detectable = True
        elif steps_lost:
            tier, tier_label = 3, "TIER 3 — Structurally Disruptive"
            hitl_reason = f"Steps missing from trace: {', '.join(sorted(steps_lost))}"
            auto_detectable = True
        else:
            # No step loss and no error — silent unless we have flag data
            tier, tier_label = 1, "TIER 1 — Silently Absorbed"
            hitl_reason = "Flow completed with no missing steps. Manual checkpoint-data inspection required."
            auto_detectable = False

        results.append({
            "fault_mode":       fault_mode,
            "tier":             tier,
            "tier_label":       tier_label,
            "hitl_required":    tier >= 2,
            "auto_detectable":  auto_detectable,
            "hitl_reason":      hitl_reason,
            "steps_lost":       sorted(steps_lost),
            "flags_found":      [],
            "propagation_depth": propagation_depth,
            "source_file":      os.path.basename(path),
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="Automated HITL detector for ShippingService fault traces")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON only")
    parser.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)),
                        help="Directory containing *_lkw_rip_evidence.json files")
    args = parser.parse_args()

    evidence_files = load_evidence_files(args.dir)

    results = []
    seen_modes = set()

    # Primary: per-scenario evidence files (have flag-level detail)
    for ev in evidence_files:
        r = analyze_evidence(ev)
        if r:
            results.append(r)
            seen_modes.add(r["fault_mode"])

    # Secondary: lkw_rip_results.json covers all 10 scenarios
    rip_path = os.path.join(args.dir, "lkw_rip_results.json")
    if os.path.exists(rip_path):
        for r in analyze_rip_results(rip_path):
            if r["fault_mode"] not in seen_modes:
                results.append(r)
                seen_modes.add(r["fault_mode"])

    # Sort by tier descending (tier 3 first — most urgent)
    results.sort(key=lambda x: (-x["tier"], x["fault_mode"]))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_scenarios": len(results),
        "tier_counts": {
            "tier_1_silent":       sum(1 for r in results if r["tier"] == 1),
            "tier_2_flagged":      sum(1 for r in results if r["tier"] == 2),
            "tier_3_structural":   sum(1 for r in results if r["tier"] == 3),
        },
        "hitl_required_count": sum(1 for r in results if r["hitl_required"]),
        "classifications": results,
    }

    # Save JSON report
    out_path = os.path.join(args.dir, "hitl_classification_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    # ── Human-readable console report ─────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  HITL AUTOMATED DETECTION REPORT — ShippingService Fault Analysis")
    print("=" * 70)
    print(f"  Scenarios analyzed : {report['total_scenarios']}")
    print(f"  HITL required      : {report['hitl_required_count']} / {report['total_scenarios']}")
    print(f"  Tier 1 (silent)    : {report['tier_counts']['tier_1_silent']}")
    print(f"  Tier 2 (flagged)   : {report['tier_counts']['tier_2_flagged']}")
    print(f"  Tier 3 (structural): {report['tier_counts']['tier_3_structural']}")
    print("=" * 70)

    for r in results:
        hitl_marker = "⚠  HITL REQUIRED" if r["hitl_required"] else "✓  auto-observable"
        print(f"\n  [{r['tier_label']}]")
        print(f"  Scenario      : {r['fault_mode']}")
        print(f"  Status        : {hitl_marker}")
        print(f"  Reason        : {r['hitl_reason']}")
        if r["steps_lost"]:
            print(f"  Steps lost    : {', '.join(r['steps_lost'])}")
        if r["flags_found"]:
            print(f"  Flags found   : {', '.join(r['flags_found'])}")
        print(f"  Propagation   : {r['propagation_depth']}")
        print(f"  Source        : {r['source_file']}")

    print(f"\n  Report saved to: {out_path}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
