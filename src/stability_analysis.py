"""
Multi-Agent Stability Analysis — 3-Run Fault Injection Repeatability Study
===========================================================================
Runs every agent's fault injection test 3 times and computes a stability matrix.

Stability categories (matching paper RQ5):
  STABLE_PASS   — Baseline NONE: clean across all 3 runs (no infection)
  STABLE_FAULT  — Fault mode: identical infection_point + steps_reached across all 3 runs
  UNSTABLE      — Results differ across runs (non-determinism detected)

Output files written to: results/stability_matrix_<agent>.json
Combined summary:        results/stability_summary.json

Usage:
    python stability_analysis.py
    python stability_analysis.py paymentagent currencyagent
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent

AGENTS = {
    "paymentagent":       BASE / "paymentagent",
    "currencyagent":      BASE / "currencyagent",
    "emailserviceagent":  BASE / "emailserviceagent",
    "productcatalogagent": BASE / "productcatalogagent",
    "recommendationagent": BASE / "recommendationagent",
    "adserviceagent":     BASE / "adserviceagent",
    "shippingagent":      BASE / "shippingagent",
}

RESULTS_DIR = BASE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

RUNS = 3


def run_agent_test(agent_dir: Path) -> dict:
    """Run an agent's test_fault_injection.py and return parsed JSON results."""
    result_json = agent_dir / f"{agent_dir.name}_fault_results.json"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(
        [sys.executable, "test_fault_injection.py"],
        cwd=agent_dir,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    with open(result_json) as f:
        return json.load(f)


def build_run_fingerprint(result: dict) -> dict:
    """Extract comparable fields for stability check."""
    return {
        "fault_mode":       result.get("fault_mode"),
        "steps_reached":    result.get("steps_reached", []),
        "steps_lost":       result.get("steps_lost", []),
        "infection_point":  result.get("infection_point"),
        "propagation_depth": result.get("propagation_depth", 0),
    }


def classify(fault_mode: str, fingerprints: list[dict]) -> str:
    """Classify stability across runs."""
    if fault_mode == "NONE":
        all_clean = all(fp["infection_point"] is None for fp in fingerprints)
        return "STABLE_PASS" if all_clean else "UNSTABLE"
    ref = fingerprints[0]
    for fp in fingerprints[1:]:
        if (fp["infection_point"] != ref["infection_point"] or
                fp["steps_reached"] != ref["steps_reached"] or
                fp["propagation_depth"] != ref["propagation_depth"]):
            return "UNSTABLE"
    return "STABLE_FAULT"


def analyse_agent(agent_name: str, agent_dir: Path) -> dict:
    print(f"\n{'='*60}")
    print(f"  Analysing: {agent_name}  ({RUNS} runs)")
    print(f"{'='*60}")

    # Collect 3 runs
    all_runs: list[list[dict]] = []
    for run_num in range(1, RUNS + 1):
        print(f"  Run {run_num}/{RUNS} ... ", end="", flush=True)
        try:
            data = run_agent_test(agent_dir)
            all_runs.append(data["results"])
            print(f"done  ({len(data['results'])} modes)")
        except subprocess.CalledProcessError as e:
            print(f"FAILED:\n{e.stderr}")
            return {"agent": agent_name, "error": str(e)}

    # Index by fault_mode
    fault_modes = [r["fault_mode"] for r in all_runs[0] if "steps_reached" in r]

    stability_rows = []
    for mode in fault_modes:
        fps = []
        for run in all_runs:
            match = next((r for r in run if r.get("fault_mode") == mode and "steps_reached" in r), None)
            if match:
                fps.append(build_run_fingerprint(match))
        if len(fps) < RUNS or not all("steps_reached" in fp for fp in fps):
            stability_rows.append({"fault_mode": mode, "stability": "INCOMPLETE"})
            continue
        label = classify(mode, fps)
        ref = fps[0]
        stability_rows.append({
            "fault_mode":        mode,
            "stability":         label,
            "infection_point":   ref["infection_point"],
            "propagation_depth": ref["propagation_depth"],
            "steps_reached":     ref["steps_reached"],
            "steps_lost":        ref["steps_lost"],
            "run_fingerprints":  fps,
        })
        symbol = "✓" if label == "STABLE_PASS" else ("⚡" if label == "STABLE_FAULT" else "✗")
        print(f"  {symbol} {mode:<30} → {label}")

    stable_pass  = sum(1 for r in stability_rows if r["stability"] == "STABLE_PASS")
    stable_fault = sum(1 for r in stability_rows if r["stability"] == "STABLE_FAULT")
    unstable     = sum(1 for r in stability_rows if r["stability"] == "UNSTABLE")

    result = {
        "agent":        agent_name,
        "runs":         RUNS,
        "total_modes":  len(stability_rows),
        "stable_pass":  stable_pass,
        "stable_fault": stable_fault,
        "unstable":     unstable,
        "stability_rate": round((stable_pass + stable_fault) / len(stability_rows) * 100, 1),
        "rows":         stability_rows,
    }

    out = RESULTS_DIR / f"stability_matrix_{agent_name}.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Stability rate: {result['stability_rate']}% "
          f"(STABLE_PASS={stable_pass}, STABLE_FAULT={stable_fault}, UNSTABLE={unstable})")
    print(f"  Saved: {out}")
    return result


def print_summary_table(summaries: list[dict]):
    print("\n" + "=" * 72)
    print("  CROSS-AGENT STABILITY SUMMARY  (RQ5 — 3-run repeatability)")
    print("=" * 72)
    print(f"  {'Agent':<25} {'Modes':>5}  {'PASS':>5}  {'FAULT':>5}  {'UNSTABLE':>8}  {'Rate':>6}")
    print(f"  {'-'*25}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*8}  {'-'*6}")
    for s in summaries:
        if "error" in s:
            print(f"  {s['agent']:<25}  ERROR: {s['error']}")
            continue
        print(f"  {s['agent']:<25} {s['total_modes']:>5}  {s['stable_pass']:>5}  "
              f"{s['stable_fault']:>5}  {s['unstable']:>8}  {s['stability_rate']:>5}%")
    total_modes   = sum(s.get("total_modes", 0) for s in summaries)
    total_stable  = sum(s.get("stable_pass", 0) + s.get("stable_fault", 0) for s in summaries)
    overall_rate  = round(total_stable / total_modes * 100, 1) if total_modes else 0
    print(f"  {'─'*65}")
    print(f"  {'TOTAL':<25} {total_modes:>5}  {'':>5}  {'':>5}  {'':>8}  {overall_rate:>5}%")
    print("=" * 72 + "\n")


def main():
    target_agents = sys.argv[1:] if len(sys.argv) > 1 else list(AGENTS.keys())
    agents_to_run = {k: v for k, v in AGENTS.items() if k in target_agents}

    print(f"\n{'='*72}")
    print(f"  LLM-MAS Stability Analysis — {RUNS}-run fault injection repeatability")
    print(f"  Agents: {list(agents_to_run.keys())}")
    print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*72}")

    summaries = []
    for agent_name, agent_dir in agents_to_run.items():
        summaries.append(analyse_agent(agent_name, agent_dir))

    print_summary_table(summaries)

    summary_path = RESULTS_DIR / "stability_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs_per_agent": RUNS,
            "agents": summaries,
        }, f, indent=2)
    print(f"  Summary saved: {summary_path}\n")


if __name__ == "__main__":
    main()
