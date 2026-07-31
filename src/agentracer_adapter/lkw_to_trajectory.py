"""
Phase 1 — LKW-to-Trajectory Converter
======================================
Converts all LLM-MAS LKW checkpoint data into AgenTracer-compatible trajectory
format.  Ground truth (error_agent, error_step) is derived from the fault
injection setup — we KNOW which agent was injected and at which checkpoint the
infection first appeared.

Sources consumed
----------------
  src/results/hitl_classification_report.json   — 6 agents × 9 fault modes
  src/results/stability_matrix_shippingagent.json — shipping (11 fault modes)
  src/results/cross_agent_propagation.json        — 2 cross-agent chains
  src/results/b1_oracle_values.json               — oracle values per checkpoint

Output
------
  src/results/agentracer/trajectories/
      single_agent/    one JSON per (agent, fault_mode)
      cross_agent/     one JSON per chain
  src/results/agentracer/trajectories_index.json  — manifest of all trajectories

Trajectory schema (AgenTracer-compatible)
-----------------------------------------
{
  "trajectory_id":   str,
  "agent_under_test": str,         # "multi_agent" for cross-agent chains
  "fault_mode":      str,
  "outcome":         "FAILED" | "PASSED" | "INCONCLUSIVE",
  "trajectory": [
    {
      "step_id":     int,
      "agent":       str,
      "action":      str,          # checkpoint name (TASK_START, CHARGE_DONE …)
      "observation": dict          # all LKW data fields at that checkpoint
    }
  ],
  "missing_steps":   list[str],    # steps that never fired (Tier 1 signal)
  "ground_truth": {
    "error_agent":      str,       # agent where fault was injected
    "error_step":       str | null,# LKW checkpoint where infection first appeared
    "tier":             int,       # 0=baseline, 1=structural, 2=flag, 3=silent
    "tier_label":       str,
    "infection_point":  str | null,
    "flags_found":      list[str],
    "steps_lost":       list[str],
    "cross_agent":      bool,
    "overcharge_pct":   float | null
  }
}
"""

import json
from pathlib import Path
from datetime import datetime, timezone

RESULTS = Path(__file__).parent.parent / "results"
OUT_DIR  = RESULTS / "agentracer" / "trajectories"
OUT_SINGLE = OUT_DIR / "single_agent"
OUT_CROSS  = OUT_DIR / "cross_agent"

# ── B1 oracle: lookup {agent.CHECKPOINT.field: oracle_value} ─────────────────
def _load_oracle() -> dict:
    path = RESULTS / "b1_oracle_values.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: v.get("oracle_value") for k, v in raw.get("oracle", {}).items()}

# ── Synthetic observation built from B1 oracle values ────────────────────────
def _build_observation(agent: str, step: str, oracle: dict) -> dict:
    """Return a dict of field→value for this checkpoint, drawn from B1 oracle."""
    prefix = f"{agent}.{step}."
    obs = {k[len(prefix):]: v for k, v in oracle.items() if k.startswith(prefix)}
    return obs or {"checkpoint": step}

# ── Single-agent trajectory from HITL classification entry ───────────────────
def _single_agent_trajectory(agent: str, entry: dict, oracle: dict,
                              run_idx: int = 1) -> dict:
    fault_mode      = entry["fault_mode"]
    tier            = entry.get("tier", 0)
    tier_label      = entry.get("tier_label", "BASELINE")
    infection_point = entry.get("infection_point")
    steps_lost      = list(entry.get("steps_lost") or [])
    flags_found     = list(entry.get("flags_found") or [])

    # All expected checkpoints for this agent (from B1 oracle keys)
    all_steps_ordered = _expected_steps(agent, oracle)

    # Build the trajectory: only steps that were actually reached
    reached = [s for s in all_steps_ordered if s not in steps_lost]
    steps = []
    for idx, step in enumerate(reached):
        obs = _build_observation(agent, step, oracle)
        # For the infection step inject a synthetic deviation marker
        if step == infection_point and fault_mode != "NONE":
            obs["__fault_injected"] = True
            obs["__fault_mode"]     = fault_mode
            for flag in flags_found:
                obs[flag] = True
        steps.append({
            "step_id":     idx,
            "agent":       agent,
            "action":      step,
            "observation": obs,
        })

    outcome = "PASSED" if fault_mode == "NONE" else (
        "INCONCLUSIVE" if (not infection_point and steps_lost == [] and tier == 0)
        else "FAILED"
    )

    return {
        "trajectory_id":   f"{agent}__{fault_mode}__run{run_idx:02d}",
        "agent_under_test": agent,
        "fault_mode":       fault_mode,
        "outcome":          outcome,
        "trajectory":       steps,
        "missing_steps":    steps_lost,
        "ground_truth": {
            "error_agent":     agent if fault_mode != "NONE" else None,
            "error_step":      infection_point,
            "tier":            tier,
            "tier_label":      tier_label,
            "infection_point": infection_point,
            "flags_found":     flags_found,
            "steps_lost":      steps_lost,
            "cross_agent":     False,
            "overcharge_pct":  None,
        },
    }

# ── Cross-agent trajectory from propagation chain entry ──────────────────────
def _cross_agent_trajectory(chain: dict, oracle: dict, run_idx: int = 1) -> dict:
    chain_id   = chain["chain"]           # "A" or "B"
    hop1_fault = chain["hop1_fault"]
    hop1_rip   = chain.get("hop1_rip", {})
    hop2_rip   = chain.get("hop2_rip", {})

    # Derive agent names from description, e.g. "CurrencyAgent FM_2_2 -> PaymentAgent NONE"
    desc = chain.get("description", "")
    parts = desc.split("->")
    hop1_agent_raw = parts[0].strip().split()[0].lower() if parts else "unknown"
    hop2_agent_raw = parts[1].strip().split()[0].lower() if len(parts) > 1 else "unknown"
    # Normalise: "CurrencyAgent" → "currencyagent"
    hop1_agent = hop1_agent_raw
    hop2_agent = hop2_agent_raw

    hop1_infection  = hop1_rip.get("infection_point")
    hop1_steps      = list(hop1_rip.get("reachability") or [])
    hop2_steps      = list(hop2_rip.get("reachability") or [])

    traj_steps = []
    # Hop 1 steps
    for idx, step in enumerate(hop1_steps):
        obs = _build_observation(hop1_agent, step, oracle)
        if step == hop1_infection:
            obs["__fault_injected"] = True
            obs["__fault_mode"]     = hop1_fault
            obs["hallucinated"]     = True
            obs["units"]            = chain.get("propagated_units", "?")
        traj_steps.append({"step_id": idx, "agent": hop1_agent,
                            "action": step, "observation": obs})
    offset = len(traj_steps)
    # Hop 2 steps (clean — no direct injection)
    for idx, step in enumerate(hop2_steps):
        obs = _build_observation(hop2_agent, step, oracle)
        traj_steps.append({"step_id": offset + idx, "agent": hop2_agent,
                            "action": step, "observation": obs})

    return {
        "trajectory_id":    f"cross_chain_{chain_id}__{hop1_fault}__run{run_idx:02d}",
        "agent_under_test": "multi_agent",
        "fault_mode":       hop1_fault,
        "outcome":          "FAILED",
        "trajectory":       traj_steps,
        "missing_steps":    [],
        "ground_truth": {
            "error_agent":     hop1_agent,
            "error_step":      hop1_infection,
            "tier":            3,
            "tier_label":      "TIER 3 — Silent (cross-agent)",
            "infection_point": hop1_infection,
            "flags_found":     ["hallucinated"],
            "steps_lost":      [],
            "cross_agent":     True,
            "overcharge_pct":  chain.get("overcharge_pct"),
        },
    }

# ── Derive ordered checkpoint list from oracle keys ──────────────────────────
_STEP_ORDER = {
    "paymentagent":       ["TASK_START","CARD_VALIDATED","CHARGE_DONE","SAVE_DONE","FINAL_ANSWER"],
    "currencyagent":      ["TASK_START","CONVERT_DONE","FINAL_ANSWER"],
    "emailserviceagent":  ["TASK_START","PREPARE_DONE","SEND_DONE","FINAL_ANSWER"],
    "productcatalogagent":["TASK_START","CATALOG_DONE","FINAL_ANSWER"],
    "recommendationagent":["TASK_START","RECOMMEND_DONE","FINAL_ANSWER"],
    "adserviceagent":     ["TASK_START","CONTEXT_EXTRACTED","ADS_FETCHED","FINAL_ANSWER"],
    "shippingagent":      ["TASK_START","QUOTE_DONE","FINAL_ANSWER"],
}

def _expected_steps(agent: str, oracle: dict) -> list:
    if agent in _STEP_ORDER:
        return _STEP_ORDER[agent]
    # Fallback: derive from oracle keys
    steps = sorted({k.split(".")[1] for k in oracle if k.startswith(agent + ".")})
    return steps or ["TASK_START", "FINAL_ANSWER"]

# ── Main conversion ───────────────────────────────────────────────────────────
def convert_all() -> dict:
    OUT_SINGLE.mkdir(parents=True, exist_ok=True)
    OUT_CROSS.mkdir(parents=True, exist_ok=True)

    oracle = _load_oracle()
    index  = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "total": 0, "single_agent": [], "cross_agent": []}

    # ── 1. Single-agent: HITL report (6 agents × 9 modes) ────────────────────
    hitl_path = RESULTS / "hitl_classification_report.json"
    if hitl_path.exists():
        hitl = json.loads(hitl_path.read_text())
        for agent, entries in hitl.get("agents", {}).items():
            for entry in entries:
                traj = _single_agent_trajectory(agent, entry, oracle)
                out  = OUT_SINGLE / f"{traj['trajectory_id']}.json"
                out.write_text(json.dumps(traj, indent=2))
                index["single_agent"].append({
                    "id":           traj["trajectory_id"],
                    "agent":        agent,
                    "fault_mode":   traj["fault_mode"],
                    "outcome":      traj["outcome"],
                    "tier":         traj["ground_truth"]["tier"],
                    "error_agent":  traj["ground_truth"]["error_agent"],
                    "error_step":   traj["ground_truth"]["error_step"],
                    "file":         str(out.relative_to(RESULTS.parent.parent)),
                })
    print(f"[Phase 1] Single-agent trajectories: {len(index['single_agent'])}")

    # ── 2. Shipping: stability matrix (11 fault modes, mostly blind-spot) ─────
    ship_path = RESULTS / "stability_matrix_shippingagent.json"
    if ship_path.exists():
        ship = json.loads(ship_path.read_text())
        for row in ship.get("rows", []):
            entry = {
                "fault_mode":       row.get("fault_mode", "NONE"),
                "tier":             row.get("tier", 0),
                "tier_label":       row.get("tier_label", "BASELINE"),
                "infection_point":  row.get("infection_point"),
                "steps_lost":       row.get("steps_lost") or [],
                "flags_found":      row.get("flags_found") or [],
            }
            traj = _single_agent_trajectory("shippingagent", entry, oracle)
            out  = OUT_SINGLE / f"{traj['trajectory_id']}.json"
            out.write_text(json.dumps(traj, indent=2))
            index["single_agent"].append({
                "id":          traj["trajectory_id"],
                "agent":       "shippingagent",
                "fault_mode":  traj["fault_mode"],
                "outcome":     traj["outcome"],
                "tier":        traj["ground_truth"]["tier"],
                "error_agent": traj["ground_truth"]["error_agent"],
                "error_step":  traj["ground_truth"]["error_step"],
                "file":        str(out.relative_to(RESULTS.parent.parent)),
            })
    print(f"[Phase 1] After shipping: {len(index['single_agent'])} total single-agent")

    # ── 3. Cross-agent chains ─────────────────────────────────────────────────
    cross_path = RESULTS / "cross_agent_propagation.json"
    if cross_path.exists():
        cross = json.loads(cross_path.read_text())
        for chain in cross.get("chains", []):
            traj = _cross_agent_trajectory(chain, oracle)
            out  = OUT_CROSS / f"{traj['trajectory_id']}.json"
            out.write_text(json.dumps(traj, indent=2))
            index["cross_agent"].append({
                "id":          traj["trajectory_id"],
                "chain":       chain.get("chain"),
                "fault_mode":  traj["fault_mode"],
                "outcome":     traj["outcome"],
                "error_agent": traj["ground_truth"]["error_agent"],
                "error_step":  traj["ground_truth"]["error_step"],
                "overcharge":  traj["ground_truth"]["overcharge_pct"],
                "file":        str(out.relative_to(RESULTS.parent.parent)),
            })
    print(f"[Phase 1] Cross-agent chains: {len(index['cross_agent'])}")

    index["total"] = len(index["single_agent"]) + len(index["cross_agent"])
    idx_path = RESULTS / "agentracer" / "trajectories_index.json"
    idx_path.write_text(json.dumps(index, indent=2))
    print(f"[Phase 1] DONE — {index['total']} trajectories written to {OUT_DIR}")
    return index


if __name__ == "__main__":
    convert_all()
