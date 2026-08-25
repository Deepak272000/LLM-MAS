#!/usr/bin/env python3
"""
Extract a cross-model LKW matrix from existing B3 raw run artifacts.

Read-only over `results/b3/raw/`: no fault campaign is executed and no raw file
is modified. Emits one consolidated artifact that carries everything needed to
fill the per-agent Stage-1 verification tables (appendix Tables A-H) for every
model tag that has been run.

Also recomputes run classification with a reachability gate. `b3_runner.py`
labels a run FN whenever no field deviated, including when the injected fault's
target agent never executed -- a fault that never ran cannot manifest, so that
is PATH_INFEASIBLE, not a detection failure. Both labels are reported side by
side; nothing is overwritten.

Usage (from the repo's `src/` directory):
    python3 extract_cross_model_lkw.py                  # temp 1.0, paper-8 modes
    python3 extract_cross_model_lkw.py --temp all       # every temperature
    python3 extract_cross_model_lkw.py --all-faults     # all 13 modes
"""

import argparse
import collections
import glob
import json
import os
import sys

RAW_DIR = os.path.join("results", "b3", "raw")
OUT_PATH = os.path.join("results", "cross_model_lkw_matrix.json")

CHECKOUT_AGENT_ORDER = [
    "checkout_orchestrator",
    "productcatalog", "currency", "shipping_quote",
    "payment", "ship_order", "email",
]

# The orchestrator hosts the injection and therefore always executes; only the
# agents it dispatches to carry evidence that a fault had a chance to manifest.
DOWNSTREAM_AGENTS = [a for a in CHECKOUT_AGENT_ORDER if a != "checkout_orchestrator"]

# Business-logic faults are injected into a single owning agent; general (FM_*)
# faults go to every agent, so their "target" is the whole pipeline.
FAULT_TARGET = {
    "BL_PRICE_MANIPULATION": "productcatalog",
    "BL_RATE_MANIPULATION":  "currency",
    "BL_AMOUNT_TAMPERING":   "payment",
    "BL_TRANSACTION_LOST":   "payment",
    "BL_DOUBLE_CHARGE":      "payment",
    "BL_CARD_DECLINED":      "payment",
    "BL_INVENTORY_MISMATCH": "shipping_quote",
    "BL_SHIPMENT_LOST":      "ship_order",
    "BL_CORRUPTED_BODY":     "email",
}

PAPER8_FAULT_MODES = [
    "FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5",
    "BL_PRICE_MANIPULATION", "BL_RATE_MANIPULATION",
    "BL_AMOUNT_TAMPERING", "BL_CORRUPTED_BODY",
]


def agent_state(per_agent, agent, fault_mode=None):
    """Collapse one agent's mutation entry into a table-ready verdict."""
    entry = (per_agent or {}).get(agent)
    if entry is None:
        return {"state": "absent", "fields": {}}
    if entry.get("not_reached"):
        return {"state": "not_reached", "fields": {}}
    if entry.get("not_targeted"):
        return {"state": "not_targeted", "fields": {}}

    # Some runs were produced by a build lacking b3_runner's is_targeted skip, so
    # they compared untargeted agents against the cross-model oracle and scored
    # model variance as fault signal. Re-apply the skip so every model is judged
    # by one policy.
    target = FAULT_TARGET.get(fault_mode)
    if target and agent not in (target, "checkout_orchestrator"):
        return {"state": "not_targeted", "fields": {}}

    fields = {}
    for detail in entry.get("deviating_detail") or []:
        fields[detail.get("field")] = {
            "b1":       detail.get("b1_value"),
            "observed": detail.get("observed"),
            "severity": detail.get("severity"),
            "detects":  detail.get("detects") or [],
            "verdict":  "FAIL",
        }
    for name in entry.get("uncomputable_fields") or []:
        fields.setdefault(name, {"verdict": "UNCOMPUTABLE"})

    return {
        "state":  "compared",
        "verdict": "FAIL" if entry.get("any_deviation") else "PASS",
        "fields": fields,
    }


def reclassify(run, reached):
    """Return (corrected_status, reason). Only FN runs are ever revised."""
    status = run.get("status")
    if status != "FN":
        return status, None

    fault_mode = run.get("fault_mode")
    target = FAULT_TARGET.get(fault_mode)

    if target is not None:
        if not reached.get(target):
            return "PATH_INFEASIBLE", "target agent %s never executed" % target
        return "FN", None

    # General fault: injected into every agent, so nothing ran at all.
    live = [a for a in DOWNSTREAM_AGENTS if reached.get(a)]
    if not live:
        if fault_mode == "FM_3_1":
            # Terminating before any downstream call is the fault succeeding
            # outright; the field oracle is blind because no field is produced.
            return ("TP_STRUCTURAL",
                    "chain terminated before any downstream agent (0/%d)"
                    % len(DOWNSTREAM_AGENTS))
        return ("PATH_INFEASIBLE",
                "no downstream agent executed (0/%d)" % len(DOWNSTREAM_AGENTS))
    return "FN", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--temp", default="1.0",
                    help="temperature suffix to include, or 'all'")
    ap.add_argument("--all-faults", action="store_true",
                    help="include all 13 modes instead of the paper's 8")
    args = ap.parse_args()

    if not os.path.isdir(args.raw_dir):
        sys.exit("raw dir not found: %s (run from the repo's src/ directory)"
                 % args.raw_dir)

    pattern = "b3_*_run*.json" if args.temp == "all" \
        else "b3_*_temp%s_run*.json" % args.temp
    paths = sorted(glob.glob(os.path.join(args.raw_dir, pattern)))
    if not paths:
        sys.exit("no run files matched %s in %s" % (pattern, args.raw_dir))

    wanted = None if args.all_faults else set(PAPER8_FAULT_MODES)

    runs = []
    skipped = 0
    for path in paths:
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            skipped += 1
            continue

        fault_mode = data.get("fault_mode")
        if wanted is not None and fault_mode not in wanted:
            continue

        rip = data.get("rip") or {}
        reached = {a: bool((rip.get(a) or {}).get("R"))
                   for a in CHECKOUT_AGENT_ORDER}
        per_agent = (data.get("mutation") or {}).get("per_agent") or {}
        corrected, reason = reclassify(data, reached)

        runs.append({
            "file":              os.path.basename(path),
            "model_tag":         (data.get("model_label") or "").rsplit("_temp", 1)[0],
            "model":             data.get("model"),
            "model_label":       data.get("model_label"),
            "temperature":       data.get("temperature"),
            "fault_mode":        fault_mode,
            "fault_category":    data.get("fault_category"),
            "fault_target":      FAULT_TARGET.get(fault_mode, "all"),
            "run_idx":           data.get("run_idx"),
            "status_reported":   data.get("status"),
            "status_corrected":  corrected,
            "reclass_reason":    reason,
            "reached":           reached,
            "agents":            {a: agent_state(per_agent, a, fault_mode)
                                  for a in CHECKOUT_AGENT_ORDER},
            "orchestrator_error": data.get("orchestrator_error"),
            "type_repairs":      data.get("type_repairs") or {},
            "injection_failures": data.get("injection_failures") or [],
        })

    tags = sorted({r["model_tag"] for r in runs})
    faults = sorted({r["fault_mode"] for r in runs})

    report = {
        "_meta": {
            "source":       os.path.abspath(args.raw_dir),
            "run_files":    len(runs),
            "unreadable":   skipped,
            "temperature":  args.temp,
            "fault_scope":  "all13" if args.all_faults else "paper8",
            "model_tags":   tags,
            "fault_modes":  faults,
            "note": ("status_corrected applies a reachability gate absent from "
                     "b3_runner.py: an FN whose injected fault never executed "
                     "is PATH_INFEASIBLE, since detection was never tested."),
        },
        "runs": runs,
    }

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)

    # ── console summary ───────────────────────────────────────────────────────
    print("wrote %s  (%d runs, %d model tags)" % (args.out, len(runs), len(tags)))

    print("\n=== status: reported -> corrected ===")
    changed = collections.Counter()
    for r in runs:
        if r["status_reported"] != r["status_corrected"]:
            changed[(r["model_tag"], r["status_reported"], r["status_corrected"])] += 1
    if changed:
        for (tag, old, new), n in sorted(changed.items()):
            print("  %-14s %-6s -> %-16s %d" % (tag, old, new, n))
    else:
        print("  no reclassification")

    print("\n=== per-model status tally (corrected) ===")
    tally = collections.Counter((r["model_tag"], r["status_corrected"]) for r in runs)
    for tag in tags:
        row = " ".join("%s:%d" % (s, n) for (t, s), n in sorted(tally.items()) if t == tag)
        print("  %-14s %s" % (tag, row))

    print("\n=== agent reachability (corrected-scored runs only) ===")
    hit = collections.Counter()
    tot = collections.Counter()
    for r in runs:
        if r["status_corrected"] == "INFRA_ERROR":
            continue
        for a in CHECKOUT_AGENT_ORDER:
            tot[(r["model_tag"], a)] += 1
            hit[(r["model_tag"], a)] += r["reached"][a]
    print("  %-22s%s" % ("agent", "".join("%-16s" % t for t in tags)))
    for a in CHECKOUT_AGENT_ORDER:
        cells = ""
        for t in tags:
            d = tot[(t, a)]
            cells += "%-16s" % ("%d/%d" % (hit[(t, a)], d) if d else "-")
        print("  %-22s%s" % (a, cells))

    print("\n=== observed field values per agent (fault x model) ===")
    for fault in faults:
        print("\n-- %s --" % fault)
        for agent in CHECKOUT_AGENT_ORDER:
            seen = False
            lines = []
            for tag in tags:
                cells = []
                for r in runs:
                    if r["fault_mode"] != fault or r["model_tag"] != tag:
                        continue
                    st = r["agents"][agent]
                    if st["state"] == "compared" and st["fields"]:
                        cells.append({k: v.get("observed") for k, v in st["fields"].items()})
                        seen = True
                    elif st["state"] == "compared":
                        cells.append("PASS")
                    elif st["state"] == "not_reached":
                        cells.append("PATH_INFEAS")
                    elif st["state"] == "not_targeted":
                        cells.append("skipped")
                if cells:
                    lines.append("     %-14s %s" % (tag, cells))
            if seen:
                print("   %s" % agent)
                for line in lines:
                    print(line)


if __name__ == "__main__":
    main()
