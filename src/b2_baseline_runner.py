"""
B2 Natural Variance Baseline Runner
=====================================
Runs every agent's FAULT_MODE=NONE scenario N times via subprocess and
computes the natural variance envelope at each LKW checkpoint.

Purpose (per Laski-Korel-Weyuker mutation analysis):
  B1 = oracle values from deterministic microservice (USE_LLM=false)
  B2 = natural LLM output variance (USE_LLM=true, FAULT_MODE=NONE, N runs)
  B3 = fault-injected runs (USE_LLM=true, FAULT_MODE=<fault>)

  Equivalence tolerances (δ_ε, S, Σ) are calibrated from B2 BEFORE any
  B3 analysis (evaluation/calibration separation).
  A B3 deviation that exceeds the B2 envelope = killed mutant.
  A B3 deviation that falls inside the B2 envelope = live mutant.

For the 6 deterministic agents (USE_LLM=false):
  B2 = B1 (zero variance by construction).
  This script runs them N times to confirm zero variance and formally
  record B1 oracle checkpoint values.

For ShippingService (USE_LLM=true, Ollama/qwen2.5-coder:14b):
  B2 captures real LLM output variance. Requires Ollama running and GPU.
  Set OLLAMA_URL and SHIP_MODEL env vars before running on SPEED HPC.

Implementation strategy:
  Uses subprocess calls to each agent's test_fault_injection.py (same
  pattern as stability_analysis.py) to avoid module-isolation issues on
  SPEED HPC Python 3.9. Each subprocess writes results to its own JSON
  file; this script reads and aggregates them.

Output:
  results/b2_variance_report.json       — per-agent, per-checkpoint stats
  results/b2_equivalence_thresholds.json — calibrated tolerances (δ, S, Σ)
  results/b2_raw_runs/                  — raw per-run JSON snapshots

Usage:
  python b2_baseline_runner.py                 # all agents, 3 runs each
  python b2_baseline_runner.py --runs 5        # 5 runs each
  python b2_baseline_runner.py --agent payment # single agent
  python b2_baseline_runner.py --skip-shipping # skip live-LLM agent
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
SRC = Path(__file__).parent
RESULTS = SRC / "results"
RAW_DIR = RESULTS / "b2_raw_runs"
RESULTS.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

# ── Agent registry ─────────────────────────────────────────────────────────────
DETERMINISTIC_AGENTS = {
    "paymentagent":        SRC / "paymentagent",
    "currencyagent":       SRC / "currencyagent",
    "emailserviceagent":   SRC / "emailserviceagent",
    "productcatalogagent": SRC / "productcatalogagent",
    "recommendationagent": SRC / "recommendationagent",
    "adserviceagent":      SRC / "adserviceagent",
}

# Result JSON filename written by each agent's test_fault_injection.py
RESULT_FILENAMES = {
    "paymentagent":        "paymentagent_fault_results.json",
    "currencyagent":       "currencyagent_fault_results.json",
    "emailserviceagent":   "emailserviceagent_fault_results.json",
    "productcatalogagent": "productcatalogagent_fault_results.json",
    "recommendationagent": "recommendationagent_fault_results.json",
    "adserviceagent":      "adserviceagent_fault_results.json",
    "shippingservice":     "shippingservice_b2_run.json",
}

# ── LKW expected steps per agent ──────────────────────────────────────────────
EXPECTED_STEPS = {
    "paymentagent":        ["TASK_START", "CARD_VALIDATED", "CHARGE_DONE", "SAVE_DONE", "FINAL_ANSWER"],
    "currencyagent":       ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"],
    "emailserviceagent":   ["TASK_START", "EMAIL_GENERATED", "EMAIL_SENT", "FINAL_ANSWER"],
    "productcatalogagent": ["TASK_START", "CATALOG_DONE", "FINAL_ANSWER"],
    "recommendationagent": ["TASK_START", "RECOMMEND_DONE", "FINAL_ANSWER"],
    "adserviceagent":      ["TASK_START", "CONTEXT_EXTRACTED", "ADS_FETCHED", "FINAL_ANSWER"],
    "shippingservice":     ["TASK_START", "QUOTE_DONE", "CARRIER_DONE", "TRACKING_DONE",
                            "SAVE_DONE", "ESCALATION_CHECK", "FINAL_ANSWER"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Subprocess runner — mirrors stability_analysis.py approach
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_none_once(agent_name: str, agent_dir: Path) -> list[dict]:
    """
    Run test_fault_injection.py NONE in a subprocess and return the LKW trace.
    The NONE argument tells each runner to execute only the baseline mode.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["FAULT_MODE"]       = "NONE"
    env["USE_LLM"]          = "false"

    result_file = agent_dir / RESULT_FILENAMES[agent_name]

    proc = subprocess.run(
        [sys.executable, "test_fault_injection.py", "NONE"],
        cwd=agent_dir,
        capture_output=True,
        text=True,
        env=env,
    )

    if proc.returncode != 0:
        stderr_snip = proc.stderr[-500:] if proc.stderr else "(no stderr)"
        raise RuntimeError(
            f"test_fault_injection.py exited {proc.returncode}: {stderr_snip}"
        )

    if not result_file.exists():
        raise FileNotFoundError(
            f"Expected result file not found: {result_file}"
        )

    with open(result_file, encoding="utf-8") as f:
        data = json.load(f)

    # Result file format: {"generated_at": ..., "results": [{"fault_mode": ...}, ...]}
    # Some agents write a plain list; handle both.
    if isinstance(data, dict) and "results" in data:
        rows = data["results"]
    elif isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and data.get("fault_mode") == "NONE":
        rows = [data]
    else:
        rows = []

    none_entry = next((r for r in rows if r.get("fault_mode") == "NONE"), None)

    if none_entry is None:
        raise ValueError(f"No NONE entry in {result_file}")

    return none_entry.get("lkw", [])


# ─────────────────────────────────────────────────────────────────────────────
# ShippingService runner (live Ollama via subprocess)
# ─────────────────────────────────────────────────────────────────────────────

SHIPPING_B2_HELPER = SRC / "shippingservice_b2_helper.py"

def _write_shipping_helper():
    """
    Write a minimal standalone script that runs ShippingService NONE once
    and writes LKW trace to shippingservice_b2_run.json.
    Called at startup if the helper does not exist.
    """
    script = '''\
"""ShippingService B2 single-run helper — called by b2_baseline_runner.py."""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SRC = Path(__file__).parent
SHIP_DIR = SRC / "shippingagent"
sys.path.insert(0, str(SHIP_DIR / "app"))  # makes 'agents' package findable
sys.path.insert(0, str(SHIP_DIR))

os.environ["FAULT_MODE"] = "NONE"
ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
model      = os.getenv("SHIP_MODEL", "qwen2.5-coder:14b")

# Stub gRPC / Motor / dotenv
for name in ("grpc", "motor", "motor.motor_asyncio", "pymongo", "dotenv"):
    sys.modules.setdefault(name, MagicMock())

config_mock = MagicMock()
config_mock.LLAMA_BASE_URL = ollama_url + "/v1"
config_mock.LLAMA_MODEL    = model
sys.modules["config"] = config_mock

# LKWCheckpoint is defined in orchestrator.py — import after path is set
from app.orchestrator import ShippingOrchestrator

PAYLOAD = {
    "address": {
        "street_address": "123 Main St",
        "city":           "Montreal",
        "state":          "QC",
        "country":        "Canada",
        "zip_code":       "H3A 0A1",
    },
    "items": [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}],
}

async def main():
    orch = ShippingOrchestrator()
    with patch("app.orchestrator.save_shipment", new_callable=AsyncMock) as ms, \\
         patch("app.orchestrator.save_quote",    new_callable=AsyncMock) as mq:
        ms.return_value = "b2-ship-id"
        mq.return_value = "b2-quote-id"
        result = await orch.ship_order(**PAYLOAD)

    # LKW trace is returned as result["_lkw"]["checkpoints"]
    lkw_dict = result.get("_lkw", {})
    trace = lkw_dict.get("checkpoints", [])

    out = {
        "fault_mode":    "NONE",
        "lkw":           trace,
        "steps_reached": [c["step"] for c in trace],
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }
    out_path = SRC / "results" / "shippingservice_b2_run.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out["steps_reached"]))

asyncio.run(main())
'''
    SHIPPING_B2_HELPER.write_text(script, encoding="utf-8")


def run_shipping_none_once() -> list[dict]:
    """Run the shipping B2 helper as a subprocess and return the LKW trace."""
    # Always regenerate the helper to pick up any fixes
    _write_shipping_helper()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["FAULT_MODE"]       = "NONE"
    env["OLLAMA_URL"]       = env.get("OLLAMA_URL", "http://localhost:11434")
    env["SHIP_MODEL"]       = env.get("SHIP_MODEL", "qwen2.5-coder:14b")

    proc = subprocess.run(
        [sys.executable, str(SHIPPING_B2_HELPER)],
        cwd=SRC,
        capture_output=True,
        text=True,
        env=env,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"ShippingService B2 helper exited {proc.returncode}: "
            f"{proc.stderr[-500:]}"
        )

    result_file = RESULTS / "shippingservice_b2_run.json"
    if not result_file.exists():
        raise FileNotFoundError(f"ShippingService result not written: {result_file}")

    with open(result_file, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("lkw", [])


# ─────────────────────────────────────────────────────────────────────────────
# Variance computation
# ─────────────────────────────────────────────────────────────────────────────

def _extract_numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "units" in value:
        return float(value["units"]) + (value.get("nanos") or 0) / 1_000_000_000
    return None


def compute_variance(runs_data: list[list[dict]]) -> dict:
    """Given N LKW traces, compute per-checkpoint variance stats."""
    step_payloads: dict[str, list[Any]] = {}
    for run in runs_data:
        for cp in run:
            step_payloads.setdefault(cp["step"], []).append(cp.get("data"))

    result: dict = {}
    for step, payloads in step_payloads.items():
        sample = next((p for p in payloads if p is not None), None)
        entry: dict = {"values": payloads}

        if sample is None:
            entry.update({"type": "null", "all_identical": True, "delta": 0})

        elif isinstance(sample, bool):
            bvals = [bool(p) for p in payloads]
            entry.update({
                "type": "flag",
                "flag_always_false": not any(bvals),
                "all_identical":     len(set(bvals)) == 1,
                "delta": 0,
            })

        elif isinstance(sample, (int, float)) and not isinstance(sample, bool):
            valid = [_extract_numeric(p) for p in payloads if _extract_numeric(p) is not None]
            mn, mx = (min(valid), max(valid)) if valid else (None, None)
            entry.update({
                "type": "numeric",
                "min": mn, "max": mx,
                "delta": round(mx - mn, 6) if mn is not None and mx is not None else 0,
                "all_identical": len(set(valid)) == 1 if valid else True,
            })

        elif isinstance(sample, list):
            sets = [frozenset(str(x) for x in p) if isinstance(p, list) else frozenset()
                    for p in payloads]
            entry.update({
                "type": "list",
                "set_stable":    len(set(sets)) == 1,
                "union":         sorted(set().union(*sets)),
                "all_identical": len(set(sets)) == 1,
                "delta": 0,
            })

        elif isinstance(sample, dict):
            num = _extract_numeric(sample)
            if num is not None:
                valid = [_extract_numeric(p) for p in payloads
                         if _extract_numeric(p) is not None]
                mn, mx = (min(valid), max(valid)) if valid else (None, None)
                entry.update({
                    "type": "numeric",
                    "min": mn, "max": mx,
                    "delta": round(mx - mn, 6) if mn is not None and mx is not None else 0,
                    "all_identical": len({round(v, 6) for v in valid}) == 1 if valid else True,
                })
            else:
                entry.update({
                    "type": "struct",
                    "all_identical": all(p == payloads[0] for p in payloads),
                    "delta": 0,
                })

        else:
            entry.update({
                "type": "text",
                "all_identical": len({str(p) for p in payloads}) == 1,
                "delta": 0,
            })

        result[step] = entry
    return result


def derive_equivalence_thresholds(variance: dict) -> dict:
    """
    Derive equivalence criterion per checkpoint from B2 observations.
    Fixed BEFORE any B3 runs (calibration/evaluation separation).

    Numeric  → δ_ε = max(observed_delta × 1.5, 0.01)
    List     → set equality S
    Flag     → flag conformance Σ (reference = False)
    Text     → schema conformance
    Struct   → exact match
    """
    thresholds: dict = {}
    for step, stats in variance.items():
        vtype = stats["type"]
        t: dict = {"step": step, "type": vtype}

        if vtype == "numeric":
            delta = stats.get("delta", 0)
            t.update({
                "criterion":         "delta_epsilon",
                "delta_epsilon":     round(max(delta * 1.5, 0.01), 4),
                "b2_observed_delta": delta,
                "note": ("zero_variance_deterministic" if delta == 0
                         else f"LLM_delta={delta:.4f} → threshold={max(delta*1.5,0.01):.4f}"),
            })

        elif vtype == "list":
            stable = stats.get("set_stable", True)
            t.update({
                "criterion":     "set_equality",
                "set_stable":    stable,
                "reference_set": stats.get("union", []),
                "note": ("S: identical set required" if stable
                         else "WARN: set unstable in B2 — manual review needed"),
            })

        elif vtype == "flag":
            t.update({
                "criterion":        "flag_conformance",
                "reference_value":  False,
                "flag_always_false": stats.get("flag_always_false", True),
                "note": "Σ: any True in B3 not in B2 is a p-use violation",
            })

        elif vtype == "text":
            t.update({
                "criterion": "schema_conformance",
                "note":      "output must match expected schema structure",
            })

        else:
            t.update({"criterion": "exact_match", "note": "exact match required"})

        thresholds[step] = t
    return thresholds


# ─────────────────────────────────────────────────────────────────────────────
# Per-agent collectors
# ─────────────────────────────────────────────────────────────────────────────

def collect_b2_deterministic(agent_name: str, agent_dir: Path, runs: int) -> dict:
    print(f"  [{agent_name}] {runs}× NONE (USE_LLM=false) ...", flush=True)
    all_traces: list[list[dict]] = []
    errors: list[str] = []

    for i in range(runs):
        try:
            trace = run_agent_none_once(agent_name, agent_dir)
            all_traces.append(trace)
            # Save raw snapshot
            snap_path = RAW_DIR / f"{agent_name}_b2_run{i+1}.json"
            with open(snap_path, "w") as f:
                json.dump({"agent": agent_name, "run": i+1,
                           "lkw": trace,
                           "steps": [c["step"] for c in trace]}, f, indent=2)
            print(f"    run {i+1}/{runs}: {[c['step'] for c in trace]}", flush=True)
        except Exception as exc:
            errors.append(f"run {i+1}: {exc}")
            print(f"    run {i+1}/{runs}: ERROR — {exc}", flush=True)

    variance   = compute_variance(all_traces) if all_traces else {}
    thresholds = derive_equivalence_thresholds(variance)

    return {
        "agent":                  agent_name,
        "use_llm":                False,
        "fault_mode":             "NONE",
        "runs_attempted":         runs,
        "runs_succeeded":         len(all_traces),
        "errors":                 errors,
        "b2_type":                "deterministic_b2_equals_b1",
        "variance":               variance,
        "equivalence_thresholds": thresholds,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
    }


def collect_b2_shipping(runs: int) -> dict:
    print(f"  [shippingservice] {runs}× NONE (USE_LLM=true, live Ollama) ...", flush=True)
    all_traces: list[list[dict]] = []
    errors: list[str] = []

    for i in range(runs):
        try:
            trace = run_shipping_none_once()
            all_traces.append(trace)
            snap_path = RAW_DIR / f"shippingservice_b2_run{i+1}.json"
            with open(snap_path, "w") as f:
                json.dump({"agent": "shippingservice", "run": i+1,
                           "lkw": trace,
                           "steps": [c["step"] for c in trace]}, f, indent=2)
            print(f"    run {i+1}/{runs}: {[c['step'] for c in trace]}", flush=True)
        except Exception as exc:
            errors.append(f"run {i+1}: {exc}")
            print(f"    run {i+1}/{runs}: ERROR — {exc}", flush=True)

    variance   = compute_variance(all_traces) if all_traces else {}
    thresholds = derive_equivalence_thresholds(variance)

    return {
        "agent":                  "shippingservice",
        "use_llm":                True,
        "model":                  os.getenv("SHIP_MODEL", "qwen2.5-coder:14b"),
        "fault_mode":             "NONE",
        "runs_attempted":         runs,
        "runs_succeeded":         len(all_traces),
        "errors":                 errors,
        "b2_type":                "live_llm_variance",
        "variance":               variance,
        "equivalence_thresholds": thresholds,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(runs: int, agent_filter: Optional[str], skip_shipping: bool):
    ts = datetime.now(timezone.utc).isoformat()
    print("=" * 72, flush=True)
    print("  LLM-MAS B2 Natural Variance Baseline Runner", flush=True)
    print(f"  Runs per agent : {runs}", flush=True)
    print(f"  Started        : {ts}", flush=True)
    print("=" * 72, flush=True)

    all_reports: dict = {}

    # ── Deterministic agents ─────────────────────────────────────────────────
    for name, agent_dir in DETERMINISTIC_AGENTS.items():
        if agent_filter and agent_filter not in name:
            continue
        if not agent_dir.exists():
            print(f"  SKIP {name}: directory not found", flush=True)
            continue
        report = collect_b2_deterministic(name, agent_dir, runs)
        all_reports[name] = report
        print(f"  [{name}] {report['runs_succeeded']}/{report['runs_attempted']} runs OK", flush=True)

    # ── ShippingService (live LLM) ──────────────────────────────────────────
    if not skip_shipping and (agent_filter is None or "shipping" in (agent_filter or "")):
        report = collect_b2_shipping(runs)
        all_reports["shippingservice"] = report
        print(f"  [shippingservice] {report['runs_succeeded']}/{report['runs_attempted']} runs OK",
              flush=True)

    # ── Write variance report ────────────────────────────────────────────────
    variance_path = RESULTS / "b2_variance_report.json"
    with open(variance_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2, default=str)
    print(f"\n  Variance report    : {variance_path}", flush=True)

    # ── Write flat equivalence thresholds ────────────────────────────────────
    flat = {agent: rep["equivalence_thresholds"] for agent, rep in all_reports.items()}
    thresh_path = RESULTS / "b2_equivalence_thresholds.json"
    with open(thresh_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, indent=2, default=str)
    print(f"  Equiv. thresholds  : {thresh_path}", flush=True)

    # ── Console summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 72, flush=True)
    print("  B2 EQUIVALENCE THRESHOLDS (calibrated before B3)", flush=True)
    print("=" * 72, flush=True)
    for agent, report in all_reports.items():
        ok   = report["runs_succeeded"]
        tot  = report["runs_attempted"]
        llm  = report["use_llm"]
        b2t  = report["b2_type"]
        print(f"\n  {agent}  (use_llm={llm}, {ok}/{tot} runs, {b2t})", flush=True)
        for step, t in report["equivalence_thresholds"].items():
            crit = t.get("criterion", "?")
            note = t.get("note", "")
            print(f"    {step:28s}  [{crit}]  {note}", flush=True)
    print("\n  Done.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="B2 Natural Variance Baseline Runner")
    parser.add_argument("--runs",          type=int,  default=3,
                        help="NONE-mode runs per agent (default: 3)")
    parser.add_argument("--agent",         type=str,  default=None,
                        help="Run only agents whose name contains this substring")
    parser.add_argument("--skip-shipping", action="store_true",
                        help="Skip ShippingService (Ollama not available)")
    args = parser.parse_args()

    main(
        runs=args.runs,
        agent_filter=args.agent,
        skip_shipping=args.skip_shipping,
    )
