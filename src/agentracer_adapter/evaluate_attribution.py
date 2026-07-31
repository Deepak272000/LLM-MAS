"""
Phase 2 — Attribution Evaluation
==================================
Compares attributor output against ground truth for every trajectory.

Metrics reported
----------------
  agent_accuracy      — attributed_agent == ground_truth.error_agent
  step_accuracy       — attributed_step  == ground_truth.error_step
  tier_breakdown      — accuracy per HITL tier (Tier 1 easiest, Tier 3 hardest)
  cross_agent_results — separate breakdown for multi-agent chains
  confusion           — per-agent summary of hits / misses

Usage
-----
    from agentracer_adapter.evaluate_attribution import evaluate
    report = evaluate(trajectories_index, rb_results, llm_results)
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
def _match_agent(pred: Optional[str], truth: Optional[str]) -> bool:
    if pred is None and truth is None:
        return True
    if pred is None or truth is None:
        return False
    return pred.lower().strip() == truth.lower().strip()


def _match_step(pred: Optional[str], truth: Optional[str]) -> bool:
    if pred is None and truth is None:
        return True
    if pred is None or truth is None:
        return False
    return pred.upper().strip() == truth.upper().strip()


# ─────────────────────────────────────────────────────────────────────────────
def evaluate(
    trajectories:  list[dict],   # list of trajectory dicts (with ground_truth)
    rb_results:    list[dict],   # one result per trajectory from RuleBasedAttributor
    llm_results:   list[dict],   # one result per trajectory from LLMAttributor (may be empty)
) -> dict:
    """
    Parameters
    ----------
    trajectories : list of AgenTracer trajectory dicts (loaded from files)
    rb_results   : list of attribution results from RuleBasedAttributor,
                   same order as trajectories
    llm_results  : list of attribution results from LLMAttributor,
                   same order as trajectories (pass [] to skip LLM eval)

    Returns
    -------
    Full evaluation report dict
    """
    assert len(trajectories) == len(rb_results), \
        "rb_results length must match trajectories"
    use_llm = len(llm_results) == len(trajectories)

    # ── Accumulators ─────────────────────────────────────────────────────────
    tiers     = {0: [], 1: [], 2: [], 3: []}   # tier → list of bool (agent correct)
    agents_rb = {}   # agent_name → {hits, total}
    agents_llm= {}

    rb_agent_total   = rb_step_total   = 0
    rb_agent_correct = rb_step_correct = 0
    llm_agent_correct= llm_step_correct= 0
    llm_agent_total  = llm_step_total  = 0

    cross_agent_rows = []
    per_row          = []

    for idx, (traj, rb_res) in enumerate(zip(trajectories, rb_results)):
        gt           = traj.get("ground_truth", {})
        fault_mode   = traj.get("fault_mode", "NONE")
        outcome      = traj.get("outcome", "PASSED")
        tier         = gt.get("tier", 0)
        true_agent   = gt.get("error_agent")
        true_step    = gt.get("error_step")
        is_cross     = gt.get("cross_agent", False)

        # Skip PASSED (NONE) baselines from accuracy calc
        if outcome == "PASSED" or fault_mode == "NONE":
            continue

        # ── Rule-based ───────────────────────────────────────────────────────
        rb_agent_ok = _match_agent(rb_res.get("error_agent"), true_agent)
        rb_step_ok  = _match_step(rb_res.get("error_step"),   true_step)
        rb_agent_total   += 1;  rb_step_total   += 1
        rb_agent_correct += int(rb_agent_ok)
        rb_step_correct  += int(rb_step_ok)

        tier_list = tiers.setdefault(tier, [])
        tier_list.append(rb_agent_ok)

        ag_stats = agents_rb.setdefault(true_agent or "unknown",
                                        {"hits": 0, "misses": 0})
        if rb_agent_ok:
            ag_stats["hits"]   += 1
        else:
            ag_stats["misses"] += 1

        # ── LLM ──────────────────────────────────────────────────────────────
        llm_agent_ok = llm_step_ok = False
        if use_llm:
            llm_res      = llm_results[idx]
            llm_agent_ok = _match_agent(llm_res.get("error_agent"), true_agent)
            llm_step_ok  = _match_step(llm_res.get("error_step"),   true_step)
            llm_agent_total += 1;  llm_step_total += 1
            llm_agent_correct += int(llm_agent_ok)
            llm_step_correct  += int(llm_step_ok)

            ag_l = agents_llm.setdefault(true_agent or "unknown",
                                          {"hits": 0, "misses": 0})
            if llm_agent_ok:
                ag_l["hits"]   += 1
            else:
                ag_l["misses"] += 1

        row = {
            "trajectory_id":   traj["trajectory_id"],
            "fault_mode":      fault_mode,
            "tier":            tier,
            "true_agent":      true_agent,
            "true_step":       true_step,
            "rb_agent":        rb_res.get("error_agent"),
            "rb_step":         rb_res.get("error_step"),
            "rb_agent_ok":     rb_agent_ok,
            "rb_step_ok":      rb_step_ok,
            "rb_confidence":   rb_res.get("confidence"),
            "rb_reasoning":    rb_res.get("reasoning", "")[:120],
        }
        if use_llm:
            row.update({
                "llm_agent":     llm_results[idx].get("error_agent"),
                "llm_step":      llm_results[idx].get("error_step"),
                "llm_agent_ok":  llm_agent_ok,
                "llm_step_ok":   llm_step_ok,
                "llm_confidence":llm_results[idx].get("confidence"),
            })

        if is_cross:
            cross_agent_rows.append(row)
        per_row.append(row)

    # ── Tier breakdown ────────────────────────────────────────────────────────
    tier_labels = {0: "Baseline", 1: "Structural", 2: "Flag-Det.", 3: "Silent"}
    tier_summary = {}
    for t, bools in tiers.items():
        if not bools:
            continue
        tier_summary[tier_labels.get(t, str(t))] = {
            "total":    len(bools),
            "correct":  sum(bools),
            "accuracy": round(sum(bools) / len(bools), 3),
        }

    def _pct(n, d):
        return round(n / d, 3) if d else None

    report = {
        "summary": {
            "total_evaluated":       rb_agent_total,
            "rule_based": {
                "agent_accuracy": _pct(rb_agent_correct, rb_agent_total),
                "step_accuracy":  _pct(rb_step_correct,  rb_step_total),
            },
        },
        "tier_breakdown_rule_based": tier_summary,
        "per_agent_rule_based":      agents_rb,
        "cross_agent_results":       cross_agent_rows,
        "per_row":                   per_row,
    }

    if use_llm:
        report["summary"]["llm_based"] = {
            "agent_accuracy": _pct(llm_agent_correct, llm_agent_total),
            "step_accuracy":  _pct(llm_step_correct,  llm_step_total),
        }
        report["per_agent_llm_based"] = agents_llm

    return report


# ─────────────────────────────────────────────────────────────────────────────
def print_report(report: dict) -> None:
    """Pretty-print the key metrics to stdout."""
    s = report["summary"]
    print("\n" + "="*60)
    print("  AGENTRACER ATTRIBUTION EVALUATION REPORT")
    print("="*60)
    print(f"  Total scenarios evaluated : {s['total_evaluated']}")
    rb = s["rule_based"]
    print(f"\n  Rule-Based Attributor")
    print(f"    Agent-level accuracy  : {rb['agent_accuracy']:.1%}")
    print(f"    Step-level  accuracy  : {rb['step_accuracy']:.1%}")
    if "llm_based" in s:
        llm = s["llm_based"]
        print(f"\n  LLM Attributor (qwen2.5:3b)")
        print(f"    Agent-level accuracy  : {llm['agent_accuracy']:.1%}")
        print(f"    Step-level  accuracy  : {llm['step_accuracy']:.1%}")

    print("\n  Tier Breakdown (Rule-Based Agent Accuracy)")
    for tier, data in report.get("tier_breakdown_rule_based", {}).items():
        bar = "█" * int(data["accuracy"] * 20)
        print(f"    {tier:<14} : {bar:<20} {data['accuracy']:.1%}  "
              f"({data['correct']}/{data['total']})")

    print("\n  Per-Agent Accuracy (Rule-Based)")
    for agent, stats in report.get("per_agent_rule_based", {}).items():
        total = stats["hits"] + stats["misses"]
        acc   = stats["hits"] / total if total else 0
        print(f"    {agent:<25} : {acc:.1%}  ({stats['hits']}/{total})")

    cross = report.get("cross_agent_results", [])
    if cross:
        print(f"\n  Cross-Agent Chains : {len(cross)} evaluated")
        for row in cross:
            ok_rb  = "✓" if row.get("rb_agent_ok")  else "✗"
            ok_llm = ("  LLM:" + ("✓" if row.get("llm_agent_ok") else "✗")
                      if "llm_agent_ok" in row else "")
            print(f"    {row['trajectory_id']}")
            print(f"      true_agent={row['true_agent']}  "
                  f"RB:{ok_rb}(pred={row['rb_agent']}){ok_llm}")
    print("="*60)
