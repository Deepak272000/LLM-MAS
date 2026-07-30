"""Run live-LLM recovery/HITL evidence scenarios for recommendation and adservice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
OUTPUT = RESULTS / "remaining_agents_live_boundary.json"

SCENARIOS = [
    {
        "name": "recommendation_boundary_recovery",
        "agent": "recommendation",
        "helper": ROOT / "co_helper_recommendation.py",
        "payload": {
            "fault_mode": "FM_2_2",
            "query": "recommend related products for this cart",
            "user_id": "user-001",
            "product_ids": ["HALLUCINATED-001"],
            "handoff_contract": {
                "boundary": "catalog_to_recommendation",
                "expected": ["PROD-001"],
            },
        },
    },
    {
        "name": "adservice_boundary_recovery",
        "agent": "adservice",
        "helper": ROOT / "co_helper_adservice.py",
        "payload": {
            "fault_mode": "BL_AD_INJECTION",
            "instruction": "show me some clothing ads",
            "context_keys": ["clothing"],
            "handoff_contract": {
                "boundary": "ad_lookup_to_response",
                "expected": [
                    {
                        "redirect_url": "https://shop.example.com/hats",
                        "text": "Buy stylish hats!",
                    }
                ],
            },
        },
    },
]


def _run_helper(helper: Path, payload: dict) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(helper), json.dumps(payload)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        return {
            "error": completed.stderr.strip()[-500:] or completed.stdout.strip()[-500:],
            "lkw": [],
        }

    json_line = ""
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            json_line = line
            break
    if not json_line:
        return {"error": "no JSON in helper output", "lkw": []}
    return json.loads(json_line)


def _summarize_lkw(lkw: list[dict]) -> dict:
    steps = [cp.get("step") for cp in lkw]
    boundary = next((cp.get("data", {}) for cp in lkw if cp.get("step") == "BOUNDARY_CHECK"), {})
    recovery = next((cp.get("data", {}) for cp in lkw if cp.get("step") == "RECOVERY_ACTION"), {})
    final_answer = next((cp.get("data", {}) for cp in reversed(lkw) if cp.get("step") == "FINAL_ANSWER"), {})
    return {
        "steps": steps,
        "boundary_check": boundary,
        "recovery_action": recovery,
        "final_answer": final_answer,
    }


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("MODEL_3B") or os.environ.get("LLAMA_MODEL", "qwen2.5:3b")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ollama_url": ollama_url,
        "model": model,
        "scenarios": [],
    }

    for scenario in SCENARIOS:
        payload = {
            **scenario["payload"],
            "ollama_url": ollama_url,
            "model": model,
            "temperature": 0.0,
        }
        result = _run_helper(scenario["helper"], payload)
        summary = _summarize_lkw(result.get("lkw", []))
        report["scenarios"].append({
            "name": scenario["name"],
            "agent": scenario["agent"],
            "fault_mode": payload["fault_mode"],
            "error": result.get("error"),
            **summary,
        })

    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote live boundary evidence to {OUTPUT}")
    for scenario in report["scenarios"]:
        print(
            f"- {scenario['agent']} | fault={scenario['fault_mode']} | "
            f"boundary={scenario['boundary_check'].get('boundary')} | "
            f"action={scenario['recovery_action'].get('action') or 'none'} | "
            f"error={scenario['error'] or 'none'}"
        )
    return 0 if all(not scenario.get("error") for scenario in report["scenarios"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())