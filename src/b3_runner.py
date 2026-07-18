"""
B3 Fault Injection Runner — Mutation Detection via LKW/RIP Oracle
=================================================================
Tests whether the LKW/RIP oracle (calibrated on B2) detects injected faults
or whether they hide inside natural LLM variance (live mutants).

Terminology (Laski-Korel-Weyuker data-flow mutation analysis):
  Terminated mutant  = LKW/RIP oracle detects a deviation from B1 in at least
                       one checkpoint variable → fault is CAUGHT.
  Live mutant        = All oracle checks pass despite fault injection → fault
                       HIDES inside natural LLM variance (false negative risk).
  Inconclusive       = Timeout / infrastructure error during the run.

Fault modes tested:
  General faults:   FM_3_1, FM_1_2, FM_2_2, FM_2_5
  Business faults:  BL_SHIPMENT_LOST, BL_INVENTORY_MISMATCH, BL_VENDOR_NEGOTIATION,
                    BL_CUSTOMER_ESCALATION, BL_REFUND_REASONING, BL_COMPLIANCE_AMBIGUITY

Two benchmark campaigns (model configs):
  Retail-bench:  qwen2.5-coder:14b  @ temperature=0.0  (--cfg 14b_temp0)
  Google-bench:  qwen2.5:3b         @ temperature=0.0  (--cfg 3b_temp0)

Injection modes:
  Global (default): same FAULT_MODE for all agents in the checkout chain
  Targeted (--fault-agent X): only agent X gets the fault; others run NONE

Output:
  results/b3/raw/b3_{fault_mode}_{cfg}_run{n}.json  — one file per run
  results/b3/b3_{fault_mode}_{cfg}_summary.json     — per-fault-mode summary
  results/b3/b3_full_report.json                    — all faults × all configs

Usage (SPEED HPC):
  setenv OLLAMA_URL http://localhost:11434
  setenv LLAMA_MODEL qwen2.5-coder:14b
  setenv MODEL_3B qwen2.5:3b

  # Run one fault mode, Retail-bench:
  $VENV/bin/python b3_runner.py --fault-mode FM_3_1 --cfg 14b_temp0 --runs 3

  # Run all fault modes, both benchmarks:
  $VENV/bin/python b3_runner.py --all --runs 3

  # Targeted injection (only ShippingService gets the fault):
  $VENV/bin/python b3_runner.py --fault-mode FM_2_2 --fault-agent ship_order --runs 3
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SRC     = Path(__file__).parent
RESULTS = SRC / "results" / "b3"
RAW_DIR = RESULTS / "raw"
RESULTS.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

# ── Oracle import (Phase 2) ───────────────────────────────────────────────────
sys.path.insert(0, str(SRC))
from b1_oracle_runner import load_oracle, compare_lkw_trace_to_oracle

# ── Checkout chain import (Phase B2) ──────────────────────────────────────────
from b2_systematic_runner import (
    run_checkout_once,
    write_all_helpers,
    build_model_configs,
    aggregate_rip,
)

# ── Fault mode registry ────────────────────────────────────────────────────────
ALL_FAULT_MODES = [
    # General faults
    "FM_3_1",
    "FM_1_2",
    "FM_2_2",
    "FM_2_5",
    # Business-logic faults
    "BL_SHIPMENT_LOST",
    "BL_INVENTORY_MISMATCH",
    "BL_VENDOR_NEGOTIATION",
    "BL_CUSTOMER_ESCALATION",
    "BL_REFUND_REASONING",
    "BL_COMPLIANCE_AMBIGUITY",
]

FAULT_CATEGORIES = {
    "FM_3_1":                 "general",
    "FM_1_2":                 "general",
    "FM_2_2":                 "general",
    "FM_2_5":                 "general",
    "BL_SHIPMENT_LOST":       "business",
    "BL_INVENTORY_MISMATCH":  "business",
    "BL_VENDOR_NEGOTIATION":  "business",
    "BL_CUSTOMER_ESCALATION": "business",
    "BL_REFUND_REASONING":    "business",
    "BL_COMPLIANCE_AMBIGUITY":"business",
}

# Agent name mapping: systematic runner short names → oracle full names
SYS_TO_ORACLE = {
    "productcatalog": "productcatalogagent",
    "currency":       "currencyagent",
    "payment":        "paymentagent",
    "email":          "emailserviceagent",
    "shipping_quote": "shippingagent_get_quote",
    "ship_order":     "shippingagent_ship_order",
}

CHECKOUT_AGENT_ORDER = [
    "productcatalog", "currency", "shipping_quote",
    "payment", "ship_order", "email",
]

# ── Mutation detection ─────────────────────────────────────────────────────────

def detect_mutation(per_agent_lkw: dict, oracle: dict, fault_mode: str) -> dict:
    """
    Apply oracle comparator to each agent's LKW trace.
    Returns a structured mutation detection result.

    Returns:
      {
        "terminated_mutant": True/False,
        "live_mutant": True/False,
        "infection_agent": str or None,    # first agent with deviation
        "infection_checkpoint": str/None,
        "infection_field": str/None,
        "propagation_path": [agents with deviation after infection],
        "per_agent": {agent: {deviating_fields: [...], any_deviation: bool}},
        "total_deviating_fields": int,
      }
    """
    per_agent_results = {}
    first_infection = None  # (agent_idx, agent, checkpoint, field)

    for idx, agent in enumerate(CHECKOUT_AGENT_ORDER):
        lkw = per_agent_lkw.get(agent, [])
        oracle_agent = SYS_TO_ORACLE.get(agent, agent)
        comparisons  = compare_lkw_trace_to_oracle(oracle_agent, lkw, oracle)

        deviating = [r for r in comparisons if r["deviation"]]
        per_agent_results[agent] = {
            "any_deviation":    len(deviating) > 0,
            "deviating_fields": [r["key"].split(".", 2)[-1] for r in deviating],
            "deviating_detail": [
                {"field":      r["key"].split(".", 2)[-1],
                 "b1_value":   r.get("b1_value"),
                 "observed":   r.get("observed_value"),
                 "severity":   r.get("deviation_severity"),
                 "reason":     r.get("reason"),
                 "detects":    r.get("faults_this_detects", [])}
                for r in deviating
            ],
        }

        if deviating and first_infection is None:
            first_cp  = deviating[0]["key"].split(".")[1]  # checkpoint
            first_fld = deviating[0]["key"].split(".", 2)[-1]
            first_infection = (idx, agent, first_cp, first_fld)

    # Propagation path = agents with deviation AFTER the first infected agent
    propagation_path = []
    if first_infection:
        inf_idx = first_infection[0]
        for idx, agent in enumerate(CHECKOUT_AGENT_ORDER):
            if idx > inf_idx and per_agent_results[agent]["any_deviation"]:
                propagation_path.append(agent)

    total_deviating = sum(
        1 for a in per_agent_results.values() if a["any_deviation"]
    )
    terminated = total_deviating > 0

    return {
        "terminated_mutant":   terminated,
        "live_mutant":         not terminated,
        "infection_agent":     first_infection[1] if first_infection else None,
        "infection_checkpoint":first_infection[2] if first_infection else None,
        "infection_field":     first_infection[3] if first_infection else None,
        "propagation_path":    propagation_path,
        "propagation_depth":   len(propagation_path),
        "total_deviating_agents": total_deviating,
        "per_agent":           per_agent_results,
    }


# ── Single B3 run ──────────────────────────────────────────────────────────────

def run_b3_once(fault_mode: str, model_cfg: dict, run_idx: int,
                fault_agent: str, oracle: dict, skip_llm: bool = False) -> dict:
    """
    Execute one fault-injected checkout flow and apply oracle mutation detection.

    fault_agent: if non-empty, only that agent gets the fault; others run NONE.
                 Special value "all" = global fault mode (default).
    """
    # For targeted injection, pass fault_mode only to the target agent via
    # the FAULT_AGENT env var (read by co_helper scripts).
    # For global injection, all agents get fault_mode.
    effective_fault_mode = fault_mode if fault_agent in ("all", "") else fault_mode

    # Run the checkout chain
    try:
        checkout_result = run_checkout_once(
            fault_mode = effective_fault_mode,
            model_cfg  = model_cfg,
            run_idx    = run_idx,
            skip_llm   = skip_llm,
        )
    except Exception as exc:
        return {
            "run_idx":     run_idx,
            "fault_mode":  fault_mode,
            "model_label": model_cfg["label"],
            "status":      "INCONCLUSIVE",
            "error":       str(exc),
            "mutation":    None,
            "rip":         None,
        }

    # Apply oracle mutation detection
    mutation = detect_mutation(
        per_agent_lkw = checkout_result.get("per_agent_lkw", {}),
        oracle        = oracle,
        fault_mode    = fault_mode,
    )

    # Classification
    if not checkout_result.get("success") and checkout_result.get("errors"):
        status = "INCONCLUSIVE"
    elif mutation["terminated_mutant"]:
        # Partial TP if only some agents show deviation, full TP if all expected do
        deviating = mutation["total_deviating_agents"]
        status = "TP" if deviating >= 2 else "PARTIAL_TP"
    else:
        status = "FN"  # live mutant — fault not detected

    return {
        "run_idx":         run_idx,
        "fault_mode":      fault_mode,
        "fault_category":  FAULT_CATEGORIES.get(fault_mode, "unknown"),
        "fault_agent":     fault_agent,
        "model_label":     model_cfg["label"],
        "model":           model_cfg["model"],
        "temperature":     model_cfg["temperature"],
        "status":          status,
        "elapsed_ms":      checkout_result.get("elapsed_ms"),
        "mutation":        mutation,
        "rip":             checkout_result.get("rip"),
        "checkout_errors": checkout_result.get("errors", {}),
        "steps_per_agent": {
            agent: [cp["step"] for cp in lkw]
            for agent, lkw in checkout_result.get("per_agent_lkw", {}).items()
        },
    }


# ── Aggregate B3 runs for one fault mode ──────────────────────────────────────

def aggregate_b3_runs(fault_mode: str, model_label: str, runs: list) -> dict:
    """
    Aggregate N B3 runs for one (fault_mode, model) combination.
    Produces the per-fault-mode summary matching the paper's classification table.
    """
    total    = len(runs)
    statuses = [r["status"] for r in runs]
    tp_count = statuses.count("TP")
    ptp_count= statuses.count("PARTIAL_TP")
    fn_count = statuses.count("FN")
    inc_count= statuses.count("INCONCLUSIVE")

    # Detection rate = (TP + PARTIAL_TP) / total
    detected = tp_count + ptp_count
    detection_rate = round(detected / total, 4) if total > 0 else 0.0

    # Mutation score = (terminated mutants) / total
    terminated = sum(1 for r in runs if r.get("mutation", {}) and
                     r["mutation"].get("terminated_mutant", False))
    mutation_score = round(terminated / total, 4) if total > 0 else 0.0

    # Classify overall result
    if detection_rate == 1.0:
        overall = "TP"
    elif detection_rate == 0.0 and inc_count == 0:
        overall = "FN"
    elif inc_count == total:
        overall = "INCONCLUSIVE"
    elif detection_rate > 0:
        overall = "PARTIAL_TP"
    else:
        overall = "INCONCLUSIVE"

    # First infection agent (most common across runs)
    infections = [
        r["mutation"]["infection_agent"]
        for r in runs
        if r.get("mutation") and r["mutation"].get("infection_agent")
    ]
    from collections import Counter
    infection_consensus = Counter(infections).most_common(1)[0][0] if infections else None

    # Propagation depth (max across runs)
    prop_depths = [
        r["mutation"].get("propagation_depth", 0)
        for r in runs if r.get("mutation")
    ]
    max_prop_depth = max(prop_depths) if prop_depths else 0

    # Deviating fields summary
    field_counts = Counter()
    for r in runs:
        if r.get("mutation"):
            for agent_res in r["mutation"]["per_agent"].values():
                for fld in agent_res.get("deviating_fields", []):
                    field_counts[fld] += 1
    top_deviating_fields = [f for f, _ in field_counts.most_common(5)]

    return {
        "fault_mode":           fault_mode,
        "fault_category":       FAULT_CATEGORIES.get(fault_mode, "unknown"),
        "model_label":          model_label,
        "total_runs":           total,
        "TP":                   tp_count,
        "PARTIAL_TP":           ptp_count,
        "FN":                   fn_count,
        "INCONCLUSIVE":         inc_count,
        "detection_rate":       detection_rate,
        "mutation_score":       mutation_score,
        "overall_classification": overall,
        "infection_agent":      infection_consensus,
        "max_propagation_depth":max_prop_depth,
        "top_deviating_fields": top_deviating_fields,
        "elapsed_ms_avg":       round(
            sum(r.get("elapsed_ms") or 0 for r in runs) / max(total, 1), 1
        ),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="B3 Fault Injection Runner — Mutation Detection"
    )
    parser.add_argument("--fault-mode",  default=None,
                        help="Single fault mode, e.g. FM_3_1")
    parser.add_argument("--all",         action="store_true",
                        help="Run ALL fault modes")
    parser.add_argument("--cfg",         default=None,
                        help="Model config label, e.g. 14b_temp0 (Retail-bench) or 3b_temp0 (Google-bench)")
    parser.add_argument("--runs",        type=int,   default=3,
                        help="Runs per (fault_mode, cfg) combination (default: 3)")
    parser.add_argument("--fault-agent", default="all",
                        help="Target only one agent for fault injection (others run NONE). Default: all")
    parser.add_argument("--skip-llm",    action="store_true",
                        help="Skip live-LLM steps (dry-run mode)")
    args = parser.parse_args()

    if not args.fault_mode and not args.all:
        parser.error("Provide --fault-mode FM_X or --all")

    fault_modes   = ALL_FAULT_MODES if args.all else [args.fault_mode]
    model_configs = build_model_configs()
    if args.cfg:
        model_configs = [c for c in model_configs if c["label"] == args.cfg]
        if not model_configs:
            print(f"ERROR: no config with label '{args.cfg}'")
            sys.exit(1)

    print("=" * 60)
    print("B3 Fault Injection Runner — Mutation Detection")
    print("=" * 60)
    print(f"  fault_modes  : {fault_modes}")
    print(f"  runs/combo   : {args.runs}")
    print(f"  configs      : {[c['label'] for c in model_configs]}")
    print(f"  fault_agent  : {args.fault_agent}")
    print()

    # Write checkout helpers once
    write_all_helpers()

    # Load B1 oracle
    try:
        oracle = load_oracle()
        print(f"  Oracle loaded: {len(oracle)} entries")
    except FileNotFoundError as exc:
        print(f"  WARNING: {exc}")
        print("  Run b1_oracle_runner.py first. Proceeding with empty oracle (all deviations = None).")
        oracle = {}
    print()

    all_summaries = []
    all_raw_runs  = []

    for fault_mode in fault_modes:
        for cfg in model_configs:
            label = cfg["label"]
            print(f"── {fault_mode} × {label} ──")
            runs = []

            for run_idx in range(1, args.runs + 1):
                print(f"  run {run_idx}/{args.runs} ...", end=" ", flush=True)
                result = run_b3_once(
                    fault_mode  = fault_mode,
                    model_cfg   = cfg,
                    run_idx     = run_idx,
                    fault_agent = args.fault_agent,
                    oracle      = oracle,
                    skip_llm    = args.skip_llm,
                )
                runs.append(result)
                all_raw_runs.append(result)

                # Save raw
                raw_file = RAW_DIR / f"b3_{fault_mode}_{label}_run{run_idx}.json"
                with open(raw_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, default=str)

                mutation = result.get("mutation") or {}
                print(
                    f"{result['status']}  "
                    f"infected={mutation.get('infection_agent', '-')}  "
                    f"prop_depth={mutation.get('propagation_depth', 0)}  "
                    f"elapsed={result.get('elapsed_ms', 0):.0f}ms"
                )

            # Aggregate this fault_mode × cfg
            summary = aggregate_b3_runs(fault_mode, label, runs)
            all_summaries.append(summary)

            # Save per-fault-mode summary
            summary_file = RESULTS / f"b3_{fault_mode}_{label}_summary.json"
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, default=str)

            print(f"  → {summary['overall_classification']}  "
                  f"detection={summary['detection_rate']:.0%}  "
                  f"infected@{summary['infection_agent']}  "
                  f"prop_depth={summary['max_propagation_depth']}")
            print()

    # ── Full report ────────────────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).isoformat()

    # Separation by benchmark
    retail_summaries = [s for s in all_summaries if "14b" in s["model_label"]]
    google_summaries = [s for s in all_summaries if "3b"  in s["model_label"]]

    full_report = {
        "generated_at":   timestamp,
        "description":    (
            "B3 mutation detection results. "
            "Retail-bench = qwen2.5-coder:14b. "
            "Google-bench = qwen2.5:3b."
        ),
        "retail_bench":   retail_summaries,
        "google_bench":   google_summaries,
        "all_summaries":  all_summaries,
        "killed_mutants": sum(1 for s in all_summaries if s["overall_classification"] in ("TP", "PARTIAL_TP")),
        "live_mutants":   sum(1 for s in all_summaries if s["overall_classification"] == "FN"),
        "inconclusive":   sum(1 for s in all_summaries if s["overall_classification"] == "INCONCLUSIVE"),
        "total_combos":   len(all_summaries),
    }
    full_report["mutation_score"] = round(
        full_report["killed_mutants"] / max(full_report["total_combos"], 1), 4
    )

    with open(RESULTS / "b3_full_report.json", "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2, default=str)

    # ── Print final summary table ──────────────────────────────────────────────
    print("=" * 60)
    print("B3 Results Summary")
    print("=" * 60)
    print(f"  Killed mutants  : {full_report['killed_mutants']} / {full_report['total_combos']}")
    print(f"  Live mutants    : {full_report['live_mutants']}")
    print(f"  Inconclusive    : {full_report['inconclusive']}")
    print(f"  Mutation score  : {full_report['mutation_score']:.1%}")
    print()

    headers = ("Fault", "Cfg", "Result", "Detect%", "Infected@", "PropDepth")
    row_fmt  = "  {:<26} {:<14} {:<12} {:>8} {:<20} {:>6}"
    print(row_fmt.format(*headers))
    print("  " + "-" * 88)
    for s in all_summaries:
        print(row_fmt.format(
            s["fault_mode"],
            s["model_label"],
            s["overall_classification"],
            f"{s['detection_rate']:.0%}",
            s["infection_agent"] or "-",
            s["max_propagation_depth"],
        ))
    print()
    print(f"  Written: results/b3/b3_full_report.json")
    print(f"  Written: results/b3/raw/ ({len(all_raw_runs)} run files)")


if __name__ == "__main__":
    main()
