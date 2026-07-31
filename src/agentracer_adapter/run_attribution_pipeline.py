"""
Phase 3+4 — End-to-End Attribution Pipeline
=============================================
Runs all four phases in sequence and writes a final attribution_report.json.

    Phase 1  lkw_to_trajectory.py   — convert LKW data → trajectory files
    Phase 2a attributor.py          — rule-based attribution on all trajectories
    Phase 2b attributor.py          — LLM-based attribution (--no-llm to skip)
    Phase 3  evaluate_attribution.py — compare both attributors vs ground truth
    Phase 4  write results           — attribution_report.json + console summary

SPEED HPC usage (tcsh)
-----------------------
    setenv OLLAMA_URL http://localhost:11434
    setenv LLAMA_MODEL qwen2.5:3b
    $VENV/bin/python run_attribution_pipeline.py

Skip LLM (deterministic only):
    $VENV/bin/python run_attribution_pipeline.py --no-llm

Single agent debug:
    $VENV/bin/python run_attribution_pipeline.py --agent paymentagent --no-llm

Output
------
    src/results/agentracer/trajectories/         — trajectory files (Phase 1)
    src/results/agentracer/attribution_report.json — full evaluation report
    src/results/agentracer/attribution_summary.json — compact summary for paper
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── project root on sys.path ──────────────────────────────────────────────────
HERE = Path(__file__).parent
SRC  = HERE.parent
sys.path.insert(0, str(SRC))

from agentracer_adapter.lkw_to_trajectory   import convert_all
from agentracer_adapter.attributor          import RuleBasedAttributor, LLMAttributor
from agentracer_adapter.evaluate_attribution import evaluate, print_report

RESULTS  = SRC / "results"
OUT_DIR  = RESULTS / "agentracer"
TRAJ_DIR = OUT_DIR / "trajectories"


# ─────────────────────────────────────────────────────────────────────────────
def load_trajectories(agent_filter: str = None) -> list[dict]:
    """Load all trajectory JSON files from disk."""
    paths = sorted(TRAJ_DIR.rglob("*.json"))
    trajs = []
    for p in paths:
        try:
            t = json.loads(p.read_text())
            if agent_filter and t.get("agent_under_test") != agent_filter:
                continue
            trajs.append(t)
        except Exception:
            pass
    return trajs


# ─────────────────────────────────────────────────────────────────────────────
def run_rule_based(trajectories: list[dict]) -> list[dict]:
    rb = RuleBasedAttributor()
    results = []
    for traj in trajectories:
        results.append(rb.attribute(traj))
    print(f"[Phase 2a] Rule-based attribution done — {len(results)} trajectories")
    return results


# ─────────────────────────────────────────────────────────────────────────────
def run_llm_based(trajectories: list[dict], ollama_url: str,
                  model: str) -> list[dict]:
    llm = LLMAttributor(ollama_url=ollama_url, model=model)
    results = []
    failed  = 0
    for i, traj in enumerate(trajectories):
        try:
            res = llm.attribute(traj)
            results.append(res)
            status = "✓" if res["confidence"] > 0 else "✗"
            if (i + 1) % 10 == 0:
                print(f"[Phase 2b] LLM attribution {i+1}/{len(trajectories)} "
                      f"last={status}")
        except Exception as exc:
            results.append({
                "error_agent": None, "error_step": None,
                "confidence": 0.0, "method": "llm",
                "reasoning": f"Error: {exc}"
            })
            failed += 1
    print(f"[Phase 2b] LLM attribution done — "
          f"{len(results)} total, {failed} errors")
    return results


# ─────────────────────────────────────────────────────────────────────────────
def build_compact_summary(report: dict) -> dict:
    """Extract the key numbers needed for the paper."""
    s   = report["summary"]
    rb  = s.get("rule_based", {})
    llm = s.get("llm_based", {})
    tier = report.get("tier_breakdown_rule_based", {})

    return {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_evaluated":    s.get("total_evaluated"),
        "rule_based": {
            "agent_accuracy": rb.get("agent_accuracy"),
            "step_accuracy":  rb.get("step_accuracy"),
        },
        "llm_based": {
            "agent_accuracy": llm.get("agent_accuracy"),
            "step_accuracy":  llm.get("step_accuracy"),
        } if llm else None,
        "tier_breakdown": tier,
        "cross_agent_summary": {
            "total": len(report.get("cross_agent_results", [])),
            "rb_agent_correct": sum(
                1 for r in report.get("cross_agent_results", [])
                if r.get("rb_agent_ok")
            ),
            "llm_agent_correct": sum(
                1 for r in report.get("cross_agent_results", [])
                if r.get("llm_agent_ok")
            ),
        },
        "note": (
            "AgenTracer model weights not yet released. "
            "Rule-based uses LKW tier/flag evidence directly. "
            "LLM-based uses qwen2.5:3b prompt attribution — "
            "swap _llm_call() for AgenTracer weights when available."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="LLM-MAS × AgenTracer — end-to-end attribution pipeline"
    )
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM attribution (rule-based only)")
    parser.add_argument("--agent",  default=None,
                        help="Evaluate only this agent (e.g. paymentagent)")
    parser.add_argument("--ollama-url", default=None,
                        help="Ollama base URL (default: $OLLAMA_URL or localhost)")
    parser.add_argument("--model", default=None,
                        help="Model name (default: $LLAMA_MODEL or qwen2.5:3b)")
    parser.add_argument("--skip-convert", action="store_true",
                        help="Skip Phase 1 (use existing trajectory files)")
    args = parser.parse_args()

    ollama_url = (args.ollama_url
                  or os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    model      = (args.model
                  or os.environ.get("LLAMA_MODEL", "qwen2.5:3b"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    if not args.skip_convert:
        print("\n[Phase 1] Converting LKW checkpoints → AgenTracer trajectories …")
        index = convert_all()
        print(f"[Phase 1] {index['total']} trajectories written.")
    else:
        print("[Phase 1] Skipped (--skip-convert).")

    # ── Load trajectories ─────────────────────────────────────────────────────
    trajectories = load_trajectories(agent_filter=args.agent)
    print(f"\nLoaded {len(trajectories)} trajectories"
          + (f" (filtered: {args.agent})" if args.agent else ""))

    if not trajectories:
        print("ERROR: no trajectories found. Run Phase 1 first.")
        sys.exit(1)

    # ── Phase 2a — Rule-based ─────────────────────────────────────────────────
    print("\n[Phase 2a] Running rule-based attributor …")
    rb_results = run_rule_based(trajectories)

    # ── Phase 2b — LLM ───────────────────────────────────────────────────────
    llm_results = []
    if not args.no_llm:
        print(f"\n[Phase 2b] Running LLM attributor ({model} @ {ollama_url}) …")
        llm_results = run_llm_based(trajectories, ollama_url, model)
    else:
        print("\n[Phase 2b] Skipped (--no-llm).")

    # ── Phase 3 — Evaluate ────────────────────────────────────────────────────
    print("\n[Phase 3] Evaluating attribution accuracy …")
    report = evaluate(trajectories, rb_results, llm_results)

    # ── Phase 4 — Write results ───────────────────────────────────────────────
    elapsed = round(time.time() - t0, 1)
    report["meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec":  elapsed,
        "ollama_url":   ollama_url,
        "model":        model,
        "llm_used":     not args.no_llm,
        "agent_filter": args.agent,
    }

    report_path  = OUT_DIR / "attribution_report.json"
    summary_path = OUT_DIR / "attribution_summary.json"
    report_path.write_text(json.dumps(report, indent=2))
    summary_path.write_text(json.dumps(build_compact_summary(report), indent=2))

    print_report(report)
    print(f"\n[Phase 4] Reports written:")
    print(f"  Full   : {report_path}")
    print(f"  Summary: {summary_path}")
    print(f"  Total elapsed: {elapsed}s")


if __name__ == "__main__":
    main()
