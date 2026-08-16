"""
Extract per-agent live-LLM B2 slices from b2/raw/ pipeline runs.

Reads b2/raw/b2_3b_temp0_run*.json (10 runs, live qwen2.5:3b, FAULT_MODE=NONE)
and writes per-agent isolated B2 files to results/b2_live_llm/.

Also prints variance summary for each agent/checkpoint/field — this is
the noise envelope that goes into Tables A–G B2 cells.

Agents covered: productcatalog, currency, payment, email, shipping_quote, ship_order
NOT covered here: recommendationagent, adserviceagent (not in checkout pipeline)
"""

import json
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone

SRC = Path(__file__).parent
B2_RAW = SRC / "results" / "b2" / "raw"
OUT_DIR = SRC / "results" / "b2_live_llm"
OUT_DIR.mkdir(exist_ok=True)

AGENT_MAP = {
    "productcatalog": "productcatalogagent",
    "currency": "currencyagent",
    "payment": "paymentagent",
    "email": "emailserviceagent",
    "shipping_quote": "shippingagent_quote",
    "ship_order": "shippingagent_ship",
}

CONFIG = "3b_temp0"   # primary extraction config — deterministic LLM, no fault


def extract_config(config_label: str):
    runs = sorted(B2_RAW.glob(f"b2_{config_label}_run*.json"))
    if not runs:
        print(f"  No runs found for config={config_label}")
        return {}

    # per_agent -> list of per-run lkw trace
    per_agent_runs = defaultdict(list)
    for run_file in runs:
        d = json.loads(run_file.read_text())
        for pipeline_key, agent_key in AGENT_MAP.items():
            lkw = d.get("per_agent_lkw", {}).get(pipeline_key, [])
            per_agent_runs[agent_key].append({
                "run_idx": d.get("run_idx", 0),
                "model_label": d.get("model_label"),
                "model": d.get("model"),
                "temperature": d.get("temperature"),
                "source_file": run_file.name,
                "lkw": lkw,
                "steps": [c["step"] for c in lkw],
            })
    return per_agent_runs


def compute_variance(per_agent_runs: dict) -> dict:
    """Per agent: per checkpoint: per field: list of observed values across runs."""
    variance = {}
    for agent_key, runs in per_agent_runs.items():
        agent_var = defaultdict(lambda: defaultdict(list))
        for run in runs:
            for cp in run["lkw"]:
                step = cp["step"]
                data = cp.get("data", {})
                for field, val in data.items():
                    agent_var[step][field].append(val)
        variance[agent_key] = agent_var
    return variance


def summarize_field(values: list) -> dict:
    unique = list({str(v): v for v in values}.values())
    all_same = len(unique) == 1
    return {
        "n_runs": len(values),
        "all_same": all_same,
        "unique_count": len(unique),
        "values": values,
        "consensus": unique[0] if all_same else None,
        "note": "zero variance (B2=B1)" if all_same else "variance present",
    }


def print_variance_report(variance: dict):
    print("\n" + "="*70)
    print("LIVE-LLM B2 VARIANCE REPORT  (config: 3b_temp0, 10 runs)")
    print("="*70)
    for agent in sorted(variance.keys()):
        print(f"\n--- {agent} ---")
        ckpts = variance[agent]
        for step in sorted(ckpts.keys()):
            fields = ckpts[step]
            for field in sorted(fields.keys()):
                summary = summarize_field(fields[field])
                flag = "  " if summary["all_same"] else "* "
                print(f"  {flag}{step}.{field}: {summary['note']}")
                if not summary["all_same"]:
                    print(f"      values: {summary['values']}")


def write_per_agent_files(per_agent_runs: dict, config_label: str):
    written = []
    for agent_key, runs in per_agent_runs.items():
        for i, run in enumerate(runs, 1):
            out = {
                "agent": agent_key,
                "b2_source": "live_llm_pipeline",
                "source_config": config_label,
                "extraction_note": (
                    "Extracted from b2/raw/ pipeline run (b2_systematic_runner.py). "
                    "This is a live qwen2.5:3b call with FAULT_MODE=NONE — "
                    "NOT a deterministic agent.run() call. "
                    "Replaces the old b2_raw_runs/ files which used no LLM."
                ),
                "run_idx": run["run_idx"],
                "model_label": run["model_label"],
                "model": run["model"],
                "temperature": run["temperature"],
                "source_file": run["source_file"],
                "lkw": run["lkw"],
                "steps": run["steps"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            fname = OUT_DIR / f"{agent_key}_livellm_b2_run{i}.json"
            fname.write_text(json.dumps(out, indent=2))
            written.append(str(fname.name))
    return written


def main():
    print(f"Extracting from: {B2_RAW}")
    print(f"Output dir:      {OUT_DIR}")

    per_agent_runs = extract_config(CONFIG)
    if not per_agent_runs:
        print("ERROR: no runs extracted")
        return

    # Show run counts
    print("\nExtracted runs per agent:")
    for agent, runs in sorted(per_agent_runs.items()):
        print(f"  {agent}: {len(runs)} runs, steps={runs[0]['steps']}")

    # Variance report
    variance = compute_variance(per_agent_runs)
    print_variance_report(variance)

    # Write files
    written = write_per_agent_files(per_agent_runs, CONFIG)
    print(f"\nWrote {len(written)} files to {OUT_DIR}")
    for f in written[:6]:
        print(f"  {f}")
    if len(written) > 6:
        print(f"  ... and {len(written)-6} more")

    # Write combined variance summary JSON
    summary_data = {}
    for agent, ckpts in variance.items():
        summary_data[agent] = {}
        for step, fields in ckpts.items():
            summary_data[agent][step] = {}
            for field, vals in fields.items():
                summary_data[agent][step][field] = summarize_field(vals)

    summary_file = SRC / "results" / "b2_live_llm_variance_summary.json"
    summary_file.write_text(json.dumps(summary_data, indent=2))
    print(f"\nVariance summary: {summary_file.name}")


if __name__ == "__main__":
    main()
