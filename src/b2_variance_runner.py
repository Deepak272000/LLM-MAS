"""
B2 Natural Variance Runner — True B2 Baseline Measurement
=========================================================
Runs the FULL checkout pipeline with FAULT_MODE=NONE and real LLM inference
across N repetitions to measure the natural LKW checkpoint variance envelope.

This is the TRUE B2 baseline Yan requires:
  - B1 = deterministic microservice oracle (ground truth)
  - B2 = LLM agent stack, NO fault injection, natural variance measured here
  - B3 = LLM agent stack WITH fault injection, compared against B1 and B2

The "natural variance envelope" from B2 tells us: how much deviation from B1
is NORMAL for this model+temperature. B3 faults must exceed this envelope to
be classified as True Positives (not hidden in natural LLM noise = live mutant).

Usage (SPEED HPC):
  setenv OLLAMA_URL http://localhost:11434
  setenv LLAMA_MODEL qwen2.5-coder:14b
  setenv MODEL_3B qwen2.5:3b

  # Retail-bench B2 (14b, temp=0.0):
  $VENV/bin/python b2_variance_runner.py --cfg 14b_temp0 --runs 10

  # Google-bench B2 (3b, low temp):
  $VENV/bin/python b2_variance_runner.py --cfg 3b_temp0 --runs 10

  # Google-bench B2 (3b, moderate-high temp):
  $VENV/bin/python b2_variance_runner.py --cfg 3b_temp0.7 --runs 10

  # Google-bench B2 (3b, maximum temp):
  $VENV/bin/python b2_variance_runner.py --cfg 3b_temp1.0 --runs 10

  # All configs at once (14b_temp0 + 3b_temp0 + 3b_temp0.7 + 3b_temp1.0):
  $VENV/bin/python b2_variance_runner.py --all --runs 10

Output:
  results/b2/raw/b2_{cfg}_run{n}.json        — raw per-run LKW trace + oracle comparison
  results/b2/b2_{cfg}_variance_summary.json  — aggregated variance stats per agent/field
  results/b2/b2_full_variance_report.json    — all configs combined
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).parent
sys.path.insert(0, str(SRC))

from b1_oracle_runner import load_oracle, compare_lkw_trace_to_oracle
from b2_systematic_runner import (
    run_checkout_once,
    write_all_helpers,
    build_model_configs,
)

RESULTS = SRC / "results" / "b2"
RAW_DIR = RESULTS / "raw"
RESULTS.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Same mapping as b3_runner.py
SYS_TO_ORACLE = {
    "checkout_orchestrator": "checkout_orchestrator",  # structural RIP only; no B1 oracle
    "productcatalog": "productcatalogagent",
    "currency":       "currencyagent",
    "payment":        "paymentagent",
    "email":          "emailserviceagent",
    "shipping_quote": "shippingagent_get_quote",
    "ship_order":     "shippingagent_ship_order",
}

CHECKOUT_AGENT_ORDER = [
    "checkout_orchestrator",
    "productcatalog", "currency", "shipping_quote",
    "payment", "ship_order", "email",
]


def run_b2_once(model_cfg: dict, run_idx: int, oracle: dict) -> dict:
    """Run one NONE-mode checkout with real LLM and compare to B1 oracle."""
    try:
        result = run_checkout_once(
            fault_mode="NONE",
            model_cfg=model_cfg,
            run_idx=run_idx,
            skip_llm=False,
        )
    except Exception as exc:
        return {
            "run_idx":            run_idx,
            "model_label":        model_cfg["label"],
            "model":              model_cfg["model"],
            "temperature":        model_cfg["temperature"],
            "status":             "ERROR",
            "error":              str(exc),
            "any_deviation":      False,
            "total_fields":       0,
            "total_deviating":    0,
            "overall_deviation_rate": 0.0,
            "per_agent_lkw":      {},
            "oracle_comparisons": {},
            "elapsed_ms":         None,
        }

    # Compare each agent's LKW trace to B1 oracle
    oracle_comparisons = {}
    any_deviation = False

    for agent in CHECKOUT_AGENT_ORDER:
        lkw          = result.get("per_agent_lkw", {}).get(agent, [])
        oracle_agent = SYS_TO_ORACLE.get(agent, agent)
        comparisons  = compare_lkw_trace_to_oracle(oracle_agent, lkw, oracle)
        deviating    = [c for c in comparisons if c["deviation"]]

        oracle_comparisons[agent] = {
            "total_fields":     len(comparisons),
            "deviating_fields": len(deviating),
            "deviation_rate":   round(len(deviating) / max(len(comparisons), 1), 4),
            "deviations": [
                {
                    "key":      c["key"],
                    "b1_value": c.get("b1_value"),
                    "observed": c.get("observed_value"),
                    "reason":   c.get("reason"),
                }
                for c in deviating
            ],
            "steps_reached": [cp.get("step") for cp in lkw],
        }
        if deviating:
            any_deviation = True

    total_fields    = sum(v["total_fields"]     for v in oracle_comparisons.values())
    total_deviating = sum(v["deviating_fields"] for v in oracle_comparisons.values())

    return {
        "run_idx":                run_idx,
        "model_label":            model_cfg["label"],
        "model":                  model_cfg["model"],
        "temperature":            model_cfg["temperature"],
        "status":                 "OK",
        "any_deviation":          any_deviation,
        "total_fields":           total_fields,
        "total_deviating":        total_deviating,
        "overall_deviation_rate": round(total_deviating / max(total_fields, 1), 4),
        "per_agent_lkw":          result.get("per_agent_lkw", {}),
        "oracle_comparisons":     oracle_comparisons,
        "elapsed_ms":             result.get("elapsed_ms"),
        "rip":                    result.get("rip"),
        "success":                result.get("success", True),
        "errors":                 result.get("errors", {}),
    }


def aggregate_b2_runs(model_label: str, runs: list) -> dict:
    """
    Aggregate N B2 baseline runs into a variance summary.

    Returns:
      natural_variance_rate  — fraction of runs where ANY field deviates from B1
      per_agent_stats        — per-agent deviation rates and deviating fields
      step_coverage          — fraction of runs each checkpoint step was reached
      field_variance_table   — per-field deviation summary (for paper Table)
    """
    ok_runs  = [r for r in runs if r["status"] == "OK"]
    total    = len(runs)
    ok_count = len(ok_runs)

    if ok_count == 0:
        return {
            "model_label":           model_label,
            "total_runs":            total,
            "ok_runs":               0,
            "natural_variance_rate": 0.0,
            "runs_with_deviation":   0,
            "per_agent_stats":       {},
            "step_coverage":         {},
            "field_variance_table":  [],
            "generated_at":          datetime.now(timezone.utc).isoformat(),
        }

    # Overall: how many runs had ANY deviation from B1?
    runs_with_deviation   = sum(1 for r in ok_runs if r["any_deviation"])
    natural_variance_rate = round(runs_with_deviation / ok_count, 4)

    # ── Per-agent stats ────────────────────────────────────────────────────
    per_agent_stats = {}
    field_variance_table = []

    for agent in CHECKOUT_AGENT_ORDER:
        agent_deviation_rates = []
        field_deviation_counts = defaultdict(int)
        field_observed_values  = defaultdict(list)

        for run in ok_runs:
            agent_data = run["oracle_comparisons"].get(agent, {})
            dr = agent_data.get("deviation_rate", 0.0)
            agent_deviation_rates.append(dr)

            for dev in agent_data.get("deviations", []):
                key = dev["key"]
                field_deviation_counts[key] += 1
                field_observed_values[key].append(dev["observed"])

        # Step coverage
        step_counts = Counter()
        for run in ok_runs:
            steps = run["oracle_comparisons"].get(agent, {}).get("steps_reached", [])
            for s in steps:
                if s:
                    step_counts[s] += 1

        mean_dr = (sum(agent_deviation_rates) / ok_count) if agent_deviation_rates else 0.0
        runs_any = sum(1 for d in agent_deviation_rates if d > 0)

        per_agent_stats[agent] = {
            "mean_deviation_rate":       round(mean_dr, 4),
            "runs_with_any_deviation":   runs_any,
            "step_coverage":             {s: round(c / ok_count, 2) for s, c in step_counts.items()},
            "frequently_deviating_fields": {
                k: {
                    "count":          v,
                    "deviation_rate": round(v / ok_count, 4),
                    "sample_values":  field_observed_values[k][:3],
                }
                for k, v in sorted(field_deviation_counts.items(), key=lambda x: -x[1])
                if v > 0
            },
        }

        # Contribute to field_variance_table (for paper)
        for field_key, count in field_deviation_counts.items():
            parts = field_key.split(".")
            checkpoint = parts[1] if len(parts) > 1 else "?"
            field_name  = parts[2] if len(parts) > 2 else parts[-1]
            field_variance_table.append({
                "agent":          agent,
                "checkpoint":     checkpoint,
                "field":          field_name,
                "deviation_count": count,
                "deviation_rate": round(count / ok_count, 4),
                "total_runs":     ok_count,
                "sample_values":  field_observed_values[field_key][:3],
            })

    # Sort table: most frequent deviations first
    field_variance_table.sort(key=lambda x: -x["deviation_rate"])

    # Step coverage across all agents
    step_coverage = {
        agent: per_agent_stats[agent]["step_coverage"]
        for agent in CHECKOUT_AGENT_ORDER
    }

    elapsed_vals = [r["elapsed_ms"] for r in ok_runs if r.get("elapsed_ms")]
    avg_elapsed  = round(sum(elapsed_vals) / len(elapsed_vals), 1) if elapsed_vals else 0.0

    return {
        "model_label":           model_label,
        "total_runs":            total,
        "ok_runs":               ok_count,
        "natural_variance_rate": natural_variance_rate,
        "runs_with_deviation":   runs_with_deviation,
        "per_agent_stats":       per_agent_stats,
        "step_coverage":         step_coverage,
        "field_variance_table":  field_variance_table,
        "avg_elapsed_ms":        avg_elapsed,
        "generated_at":          datetime.now(timezone.utc).isoformat(),
    }


def print_variance_report(summary: dict):
    """Print a human-readable variance report to stdout."""
    label = summary["model_label"]
    print(f"\n{'='*60}")
    print(f"  B2 VARIANCE REPORT — {label}")
    print(f"{'='*60}")
    print(f"  Runs:                   {summary['ok_runs']}/{summary['total_runs']}")
    print(f"  Natural variance rate:  {summary['natural_variance_rate']:.0%}  "
          f"({summary['runs_with_deviation']} runs with any B1 deviation)")
    print()

    # Per-agent summary
    print("  Per-agent deviation rates:")
    for agent, stats in summary["per_agent_stats"].items():
        dr     = stats["mean_deviation_rate"]
        runs_d = stats["runs_with_any_deviation"]
        ok     = summary["ok_runs"]
        steps  = list(stats.get("step_coverage", {}).keys())
        print(f"    {agent:20}  mean_dr={dr:.0%}  runs_deviated={runs_d}/{ok}  "
              f"steps={steps}")
        for field_key, fdata in list(stats.get("frequently_deviating_fields", {}).items())[:3]:
            print(f"      ↳ {field_key.split('.')[-1]:30}  dr={fdata['deviation_rate']:.0%}  "
                  f"samples={fdata['sample_values'][:2]}")

    # Table summary
    table = summary.get("field_variance_table", [])
    if table:
        print(f"\n  Top deviating fields (paper table input):")
        print(f"  {'Agent':20} {'Checkpoint':15} {'Field':25} {'Rate':6} {'Samples'}")
        print(f"  {'-'*20} {'-'*15} {'-'*25} {'-'*6} {'-'*20}")
        for row in table[:10]:
            samples = str(row["sample_values"][:2])[:30]
            print(f"  {row['agent']:20} {row['checkpoint']:15} {row['field']:25} "
                  f"{row['deviation_rate']:.0%}    {samples}")
    print()


def aggregate_from_raw(cfg_label: str) -> dict:
    """Load all saved raw run files for cfg_label and return aggregated summary."""
    raw_files = sorted(RAW_DIR.glob(f"b2_{cfg_label}_run*.json"))
    if not raw_files:
        raise FileNotFoundError(f"No raw files found in {RAW_DIR} for cfg={cfg_label!r}")
    runs = []
    for f in raw_files:
        with open(f, encoding="utf-8") as fh:
            runs.append(json.load(fh))
    print(f"  Loaded {len(runs)} raw files for {cfg_label}")
    return aggregate_b2_runs(cfg_label, runs)


def main():
    parser = argparse.ArgumentParser(
        description="B2 Natural Variance Runner — True B2 Baseline"
    )
    parser.add_argument("--cfg",  default=None,
                        help="Model config label, e.g. 14b_temp0, 3b_temp0, 3b_temp0.8")
    parser.add_argument("--runs", type=int, default=10,
                        help="Number of NONE-mode runs per config (default: 10)")
    parser.add_argument("--all",  action="store_true",
                        help="Run all model configs")
    parser.add_argument("--from-raw", action="store_true",
                        help="Rebuild summary from existing raw files instead of re-running")
    args = parser.parse_args()

    model_configs = build_model_configs()
    if not args.all:
        if not args.cfg:
            parser.error("Provide --cfg LABEL or --all")
        model_configs = [c for c in model_configs if c["label"] == args.cfg]
        if not model_configs:
            labels = [c["label"] for c in build_model_configs()]
            print(f"ERROR: unknown config '{args.cfg}'. Available: {labels}")
            sys.exit(1)

    # ── --from-raw: rebuild summaries from saved raw files, no new runs ───
    if args.from_raw:
        print("=" * 60)
        print("B2 Variance — rebuilding summary from existing raw files")
        print("=" * 60)
        all_summaries = []
        cfg_labels = [c["label"] for c in model_configs] if not args.all else \
                     [c["label"] for c in build_model_configs()]
        for label in cfg_labels:
            try:
                summary = aggregate_from_raw(label)
                summary_file = RESULTS / f"b2_{label}_variance_summary.json"
                with open(summary_file, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, default=str)
                print_variance_report(summary)
                all_summaries.append(summary)
            except FileNotFoundError as e:
                print(f"  SKIP {label}: {e}")
        if all_summaries:
            tag = os.environ.get("MODEL_TAG", "").strip()
            full_report = {
                "generated_at":  datetime.now(timezone.utc).isoformat(),
                "model_tag":     tag or None,
                "all_summaries": all_summaries,
            }
            suffix = f"_{tag}" if tag else ""
            with open(RESULTS / f"b2_full_variance_report{suffix}.json", "w", encoding="utf-8") as f:
                json.dump(full_report, f, indent=2, default=str)
            print(f"\nResults saved to: {RESULTS}")
        return

    print("=" * 60)
    print("B2 Natural Variance Runner — True B2 Baseline")
    print("=" * 60)
    print(f"  configs      : {[c['label'] for c in model_configs]}")
    print(f"  runs/config  : {args.runs}")
    print(f"  fault_mode   : NONE (baseline — no fault injection)")
    print()

    write_all_helpers()

    try:
        oracle = load_oracle()
        print(f"  Oracle loaded: {len(oracle)} entries")
    except FileNotFoundError as exc:
        print(f"  WARNING: {exc}")
        print("  Run b1_oracle_runner.py first. Deviations will be empty.")
        oracle = {}
    print()

    all_summaries = []

    for cfg in model_configs:
        label = cfg["label"]
        print(f"── B2 Variance: {label}  ({args.runs} runs × FAULT_MODE=NONE) ──")
        runs = []

        for run_idx in range(1, args.runs + 1):
            print(f"  run {run_idx}/{args.runs} ...", end=" ", flush=True)
            result = run_b2_once(cfg, run_idx, oracle)
            runs.append(result)

            # Save raw file
            raw_file = RAW_DIR / f"b2_{label}_run{run_idx}.json"
            with open(raw_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)

            dr = result.get("overall_deviation_rate", 0)
            elapsed = result.get("elapsed_ms") or 0
            print(f"status={result['status']}  deviation={dr:.0%}  elapsed={elapsed:.0f}ms")

        summary = aggregate_b2_runs(label, runs)
        all_summaries.append(summary)

        # Save summary
        summary_file = RESULTS / f"b2_{label}_variance_summary.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

        print_variance_report(summary)

    # Full variance report — namespaced by MODEL_TAG so concurrent multi-model
    # jobs cannot overwrite each other or the untagged baseline report.
    tag = os.environ.get("MODEL_TAG", "").strip()
    full_report = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "model_tag":     tag or None,
        "all_summaries": all_summaries,
    }
    suffix = f"_{tag}" if tag else ""
    report_path = RESULTS / f"b2_full_variance_report{suffix}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2, default=str)

    print("=" * 60)
    print("FINAL B2 SUMMARY")
    print("=" * 60)
    for s in all_summaries:
        print(f"  {s['model_label']:15}  natural_variance={s['natural_variance_rate']:.0%}  "
              f"ok={s['ok_runs']}/{s['total_runs']}")
    print(f"\nResults saved to: {RESULTS}")


if __name__ == "__main__":
    main()
