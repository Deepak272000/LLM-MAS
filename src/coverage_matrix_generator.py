"""Generate an all-agent coverage matrix from existing artifacts.

This script scans current repository outputs and emits a normalized matrix to:
  src/results/coverage_matrix.json

Goal:
- Show covered/partial/missing status across B1/B2/B3 + stability + HITL evidence
- Highlight gaps for services that exist in manifests but lack equivalent FI artifacts
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
RESULTS = ROOT / "results"
K8S_MANIFESTS = REPO / "kubernetes-manifests"

# Coverage is PER-MODEL, never pooled: blending models would erase the very
# differences this experiment exists to measure. MODEL_TAG scopes both the
# inputs read and the matrix written.
_MODEL_TAG = os.environ.get("MODEL_TAG", "").strip()
TAG_SUFFIX = f"_{_MODEL_TAG}" if _MODEL_TAG else ""

OUT = RESULTS / f"coverage_matrix{TAG_SUFFIX}.json"


def _b3_config_labels() -> list[str]:
    """Exact B3 config labels this matrix is scoped to.

    With MODEL_TAG set we match that model's labels and nothing else. Matching
    is by exact suffix, not substring, so one model's summaries can never be
    counted toward another's coverage.
    """
    if _MODEL_TAG:
        return [f"{_MODEL_TAG}_temp0",
                f"{_MODEL_TAG}_temp0.7",
                f"{_MODEL_TAG}_temp1.0"]
    return ["14b_temp0", "3b_temp0", "3b_temp0.7", "3b_temp1.0"]


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _service_inventory() -> list[str]:
    if not K8S_MANIFESTS.exists():
        return []
    names = []
    for p in sorted(K8S_MANIFESTS.glob("*service.yaml")):
        names.append(p.stem)
    return names


def _detect_systematic_agents() -> list[str]:
    """Read all systematic checkout raw runs and union per_agent_lkw keys."""
    raw_dir = RESULTS / "b2_systematic" / "raw"
    if not raw_dir.exists():
        return []
    keys = set()
    for p in sorted(raw_dir.glob("checkout_*_NONE_run*.json")):
        doc = _load_json(p)
        if isinstance(doc, dict):
            per_agent = doc.get("per_agent_lkw", {})
            if isinstance(per_agent, dict):
                keys.update(per_agent.keys())
    return sorted(keys)


def _detect_b3_fault_modes() -> list[str]:
    doc = _load_json(RESULTS / "b3" / f"b3_full_report{TAG_SUFFIX}.json")
    if not isinstance(doc, dict):
        return []

    # Compatible with both list/dict report layouts
    modes = set()
    rows = doc.get("rows") or doc.get("results") or []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("fault_mode"):
                modes.add(row["fault_mode"])

    if not modes:
        # Fallback: infer from summary filenames by stripping an EXACT config
        # suffix. A previous regex used a non-greedy group and would match
        # b3_BL_SHIPMENT_LOST_llama32-3b_temp0.7_summary against "3b_temp0.7",
        # yielding the corrupted mode "BL_SHIPMENT_LOST_llama32". Exact
        # prefix/suffix matching cannot do that.
        for p in (RESULTS / "b3").glob("b3_*_summary.json"):
            stem = p.stem
            for cfg in _b3_config_labels():
                suffix = f"_{cfg}_summary"
                if stem.startswith("b3_") and stem.endswith(suffix):
                    mode = stem[len("b3_"):-len(suffix)]
                    if mode:
                        modes.add(mode)
                    break

    return sorted(modes)


def _agent_row(agent: dict, systematic_agents: list[str], b3_modes: list[str]) -> dict:
    name = agent["name"]
    folder = ROOT / agent["folder"]

    test_fault = (folder / "test_fault_injection.py").exists()
    fault_json = (folder / f"{name}_fault_results.json").exists()
    stability_json = (RESULTS / f"stability_matrix_{name}.json").exists()

    # B2 raw deterministic artifacts for 6 python agents
    b2_raw_count = len(list((RESULTS / "b2_raw_runs").glob(f"{name}_b2_run*.json")))

    # B2 systematic chain participation (checkout pipeline)
    systematic_keys = agent.get("systematic_keys", [])
    requires_systematic = len(systematic_keys) > 0
    in_b2_systematic = any(k in systematic_agents for k in systematic_keys)

    # B3 participation by targeted mapping expectations
    b3_expected_modes = agent.get("b3_expected_modes", [])
    requires_b3 = len(b3_expected_modes) > 0
    b3_mode_hits = [m for m in b3_expected_modes if m in b3_modes]

    hitl_report_exists = (RESULTS / "hitl_classification_report.json").exists()
    cross_agent_exists = (RESULTS / "cross_agent_propagation.json").exists()

    signals = {
        "harness_test_fault_injection": test_fault,
        "fault_result_json": fault_json,
        "stability_matrix": stability_json,
        "b2_raw_runs_count": b2_raw_count,
        "b2_systematic_participation": in_b2_systematic,
        "b3_expected_modes_found": b3_mode_hits,
        "hitl_report_exists": hitl_report_exists,
        "cross_agent_report_exists": cross_agent_exists,
    }

    score = 0
    score += 1 if test_fault else 0
    score += 1 if fault_json else 0
    score += 1 if stability_json else 0
    score += 1 if b2_raw_count > 0 else 0
    score += 1 if (in_b2_systematic or not requires_systematic) else 0
    score += 1 if (len(b3_mode_hits) > 0 or not requires_b3) else 0

    if score >= 5:
        status = "covered"
    elif score >= 3:
        status = "partial"
    else:
        status = "missing"

    missing = []
    if not test_fault:
        missing.append("test_fault_injection.py")
    if not fault_json:
        missing.append(f"{name}_fault_results.json")
    if not stability_json:
        missing.append(f"results/stability_matrix_{name}.json")
    if b2_raw_count == 0:
        missing.append(f"results/b2_raw_runs/{name}_b2_run*.json")
    if requires_systematic and not in_b2_systematic:
        missing.append("b2_systematic participation")
    if requires_b3 and not b3_mode_hits:
        missing.append("b3 fault-mode participation")

    # Do not mark as covered while actionable gaps remain.
    if status == "covered" and missing:
        status = "partial"

    return {
        "agent": name,
        "folder": agent["folder"],
        "language": agent["language"],
        "status": status,
        "signals": signals,
        "missing_items": missing,
    }


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)

    service_inventory = _service_inventory()
    systematic_agents = _detect_systematic_agents()
    b3_modes = _detect_b3_fault_modes()

    # Canonical campaign scope in current repo
    agents = [
        {
            "name": "paymentagent",
            "folder": "paymentagent",
            "language": "python",
            "systematic_keys": ["payment"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5", "BL_AMOUNT_TAMPERING"],
        },
        {
            "name": "currencyagent",
            "folder": "currencyagent",
            "language": "python",
            "systematic_keys": ["currency"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5", "BL_RATE_MANIPULATION"],
        },
        {
            "name": "emailserviceagent",
            "folder": "emailserviceagent",
            "language": "python",
            "systematic_keys": ["email"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5", "BL_CORRUPTED_BODY"],
        },
        {
            "name": "productcatalogagent",
            "folder": "productcatalogagent",
            "language": "python",
            "systematic_keys": ["productcatalog"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5", "BL_PRICE_MANIPULATION"],
        },
        {
            "name": "recommendationagent",
            "folder": "recommendationagent",
            "language": "python",
            "systematic_keys": [],
            "b3_expected_modes": ["FM_2_2", "FM_2_5"],
        },
        {
            "name": "adserviceagent",
            "folder": "adserviceagent",
            "language": "python",
            "systematic_keys": [],
            "b3_expected_modes": ["FM_2_2", "FM_2_5"],
        },
        {
            "name": "shippingagent",
            "folder": "shippingagent",
            "language": "python",
            "systematic_keys": ["shipping_quote", "ship_order"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5", "BL_INVENTORY_MISMATCH", "BL_SHIPMENT_LOST"],
        },
        {
            "name": "cart-agent",
            "folder": "cart-agent",
            "language": "csharp",
            "systematic_keys": [],
            "b3_expected_modes": [],
        },
        {
            "name": "checkout-agent",
            "folder": "checkout-agent",
            "language": "go",
            "systematic_keys": ["checkout_orchestrator"],
            "b3_expected_modes": ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5"],
        },
    ]

    rows = [_agent_row(a, systematic_agents, b3_modes) for a in agents]

    totals = {
        "covered": sum(1 for r in rows if r["status"] == "covered"),
        "partial": sum(1 for r in rows if r["status"] == "partial"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
    }

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_inventory_from_manifests": service_inventory,
        "systematic_agents_detected": systematic_agents,
        "b3_fault_modes_detected": b3_modes,
        "totals": totals,
        "rows": rows,
    }

    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
