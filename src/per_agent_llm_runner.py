"""
Per-Agent LLM Fault Injection Runner
=====================================
Addresses professor's feedback: "w/o LLM inference, the work has no meaning"

Each Python agent is run INDIVIDUALLY with real Ollama LLM inference
(app.orchestrator, LangGraph, or equivalent live agent path) for B2
(natural variance) and B3 (fault injection).

Design (per professor's framework):
  B1 = gRPC microservice ground truth (deterministic oracle, from b1_oracle_runner.py)
  B2 = LLM agent, FAULT_MODE=NONE, N runs  → natural LLM variance per agent
  B3 = LLM agent, FAULT_MODE=<fault>, N runs → compared against B2 envelope

Agents covered — all Python agent roles evaluated in the repo:
    productcatalog → co_helper_productcatalog.py  (ProductCatalogOrchestrator, real LLM)
  currency       → co_helper_currency.py       (CurrencyOrchestrator,   real LLM)
  payment        → co_helper_payment.py        (PaymentOrchestrator,    real LLM)
  email          → co_helper_email.py          (EmailOrchestrator,      real LLM)
    recommendation → co_helper_recommendation.py (Recommendation graph,   live LLM)
    adservice      → co_helper_adservice.py      (AdService graph,        live LLM)
  shipping_quote → co_helper_shipping.py       (ShippingOrchestrator,   real LLM, get_quote)
  ship_order     → co_helper_shipping.py       (ShippingOrchestrator,   real LLM, ship_order)

Two benchmark configurations (professor's "two benchmarks"):
  Retail-bench:  qwen2.5-coder:14b @ temp=0.0  (label: 14b_temp0)
  Google-bench:  qwen2.5:3b        @ temp=0.0  (label: 3b_temp0)
                 qwen2.5:3b        @ temp=0.7  (label: 3b_temp0.7)
                 qwen2.5:3b        @ temp=1.0  (label: 3b_temp1.0)

Fault modes (10 total — same as b3_runner.py):
  General:  FM_3_1 FM_1_2 FM_2_2 FM_2_5
  Business: BL_SHIPMENT_LOST BL_INVENTORY_MISMATCH BL_VENDOR_NEGOTIATION
            BL_CUSTOMER_ESCALATION BL_REFUND_REASONING BL_COMPLIANCE_AMBIGUITY

Output:
  results/per_agent_llm/b2/<agent>_<cfg>_none_run<n>.json
  results/per_agent_llm/b3/<agent>_<cfg>_<fault>_run<n>.json
  results/per_agent_llm/per_agent_b2_variance.json
  results/per_agent_llm/per_agent_b3_mutations.json
  results/per_agent_llm/per_agent_llm_report.json

Usage (SPEED HPC — tcsh):
  setenv OLLAMA_URL  http://localhost:11434
  setenv LLAMA_MODEL qwen2.5-coder:14b
  setenv MODEL_3B    qwen2.5:3b

  # All agents, all configs, full B2+B3:
  $VENV/bin/python per_agent_llm_runner.py

  # Single agent:
  $VENV/bin/python per_agent_llm_runner.py --agent currency
  $VENV/bin/python per_agent_llm_runner.py --agent payment --cfg 14b_temp0

  # B2 only (run before B3 to calibrate variance):
  $VENV/bin/python per_agent_llm_runner.py --b2-only --b2-runs 10

  # B3 single fault mode:
  $VENV/bin/python per_agent_llm_runner.py --b3-only --fault-mode FM_3_1 --cfg 14b_temp0

  # Full run (default: 10 B2 runs, 3 B3 runs per fault):
  $VENV/bin/python per_agent_llm_runner.py --b2-runs 10 --b3-runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Paths ──────────────────────────────────────────────────────────────────────
SRC        = Path(__file__).parent
RESULTS    = SRC / "results" / "per_agent_llm"
B2_RAW_DIR = RESULTS / "b2"
B3_RAW_DIR = RESULTS / "b3"
RESULTS.mkdir(parents=True, exist_ok=True)
B2_RAW_DIR.mkdir(parents=True, exist_ok=True)
B3_RAW_DIR.mkdir(parents=True, exist_ok=True)

# ── Fault modes ───────────────────────────────────────────────────────────────
ALL_FAULT_MODES = [
    "FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5",
    "BL_SHIPMENT_LOST", "BL_INVENTORY_MISMATCH", "BL_VENDOR_NEGOTIATION",
    "BL_CUSTOMER_ESCALATION", "BL_REFUND_REASONING", "BL_COMPLIANCE_AMBIGUITY",
]

# ── B1 oracle — gRPC microservice ground truth per agent ─────────────────────
# These are the deterministic values a real microservice returns.
# LLM deviations from these in B3 that exceed the B2 envelope = killed mutant.
B1_ORACLE = {
    "productcatalog": {
        "expected_steps":    ["TASK_START", "CATALOG_DONE", "FINAL_ANSWER"],
        "key_fields":        {},
        "infection_flags":   ["hallucinated", "query_tampered", "action_swapped",
                              "products_missing", "price_manipulated", "duplicated",
                              "category_wrong"],
    },
    "currency": {
        "expected_steps":    ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"],
        "key_fields":        {"currency_code": "USD", "units": 19, "nanos": 990000000},
        "infection_flags":   ["hallucinated", "currency_swapped", "rate_manipulated", "stale_rate"],
    },
    "payment": {
        "expected_steps":    ["TASK_START", "CARD_VALIDATED", "CHARGE_DONE", "SAVE_DONE", "FINAL_ANSWER"],
        "key_fields":        {"success": True},
        "transaction_id_format": "uuid-v4",
        "infection_flags":   ["hallucinated", "amount_tampered", "double_charge",
                              "forced_decline", "save_skipped", "validation_bypassed"],
    },
    "email": {
        "expected_steps":    ["TASK_START", "EMAIL_GENERATED", "EMAIL_SENT", "FINAL_ANSWER"],
        "key_fields":        {"status": "sent"},
        "infection_flags":   ["send_skipped", "hallucinated", "wrong_recipient",
                              "empty_body", "subject_tampered"],
    },
    "recommendation": {
        "expected_steps":    ["TASK_START", "RECOMMEND_DONE", "FINAL_ANSWER"],
        "key_fields":        {},
        "infection_flags":   ["hallucinated", "user_id_swapped", "method_swapped",
                              "empty_recs", "self_rec", "injection", "shuffled"],
    },
    "adservice": {
        "expected_steps":    ["TASK_START", "CONTEXT_EXTRACTED", "ADS_FETCHED", "FINAL_ANSWER"],
        "key_fields":        {},
        "infection_flags":   ["premature_termination", "context_tampered", "category_swapped",
                              "hallucinated", "empty_ads", "injected", "wrong_url", "duplicated"],
    },
    "shipping_quote": {
        "expected_steps":    ["TASK_START", "FINAL_ANSWER"],
        "key_fields":        {},   # cost_usd is a float — compared via variance envelope
        "infection_flags":   ["hallucinated", "cost_zero", "overflow", "stale_rate"],
    },
    "ship_order": {
        "expected_steps":    ["TASK_START", "FINAL_ANSWER"],
        "key_fields":        {},   # tracking_id is random — checked for presence only
        "infection_flags":   ["hallucinated", "tracking_missing", "shipment_lost",
                              "premature_termination"],
    },
}

# ── Agent → co_helper + default payload ──────────────────────────────────────
def _make_payload(agent: str, fault_mode: str, model: str,
                  ollama_url: str, temperature: float) -> dict:
    base = {
        "fault_mode":  fault_mode,
        "model":       model,
        "ollama_url":  ollama_url,
        "temperature": temperature,
    }
    if agent == "productcatalog":
        base.update({
            "query":       "list sunglasses and accessories",
            "product_ids": ["PROD-001"],
        })
    elif agent == "currency":
        base.update({
            "query":         "convert 19.99 USD to USD",
            "action":        "convert",
            "from_currency": "USD",
            "units":         19,
            "nanos":         990000000,
            "to_currency":   "USD",
        })
    elif agent == "payment":
        base.update({
            "query":                        "charge the credit card",
            "currency_code":               "USD",
            "units":                        27,
            "nanos":                        490000000,
            "credit_card_number":          "4111111111111111",
            "credit_card_cvv":             123,
            "credit_card_expiration_year":  2030,
            "credit_card_expiration_month": 12,
        })
    elif agent == "email":
        base.update({
            "order_id":            "ORDER-B2-001",
            "email":               "customer@example.com",
            "user_name":           "Test Customer",
            "items":               [{"product_id": "PROD-001", "quantity": 2}],
            "total_cost":          {"currency_code": "USD", "units": 27, "nanos": 490000000},
            "shipping_tracking_id": "TRACK-001",
        })
    elif agent == "recommendation":
        base.update({
            "query":       "recommend related products for this cart",
            "user_id":     "user-001",
            "product_ids": ["PROD-001"],
        })
    elif agent == "adservice":
        base.update({
            "instruction": "show me some clothing ads",
            "context_keys": ["clothing"],
        })
    elif agent == "shipping_quote":
        base.update({
            "action":  "get_quote",
            "address": {"street_address": "123 Main St", "city": "Montreal",
                        "state": "QC", "country": "Canada", "zip_code": "H3A 0A1"},
            "items":   [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}],
        })
    elif agent == "ship_order":
        base.update({
            "action":  "ship_order",
            "address": {"street_address": "123 Main St", "city": "Montreal",
                        "state": "QC", "country": "Canada", "zip_code": "H3A 0A1"},
            "items":   [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}],
        })
    return base


def _co_helper_script(agent: str) -> Path:
    mapping = {
        "productcatalog": SRC / "co_helper_productcatalog.py",
        "currency":       SRC / "co_helper_currency.py",
        "payment":        SRC / "co_helper_payment.py",
        "email":          SRC / "co_helper_email.py",
        "recommendation": SRC / "co_helper_recommendation.py",
        "adservice":      SRC / "co_helper_adservice.py",
        "shipping_quote": SRC / "co_helper_shipping.py",
        "ship_order":     SRC / "co_helper_shipping.py",
    }
    return mapping[agent]


# Shipping agents run a multi-step ReAct loop (quote+carrier+tracking = 3+ LLM calls).
# Each 14b call on V100 can take 30-90s, so 90s is consistently too short.
_AGENT_TIMEOUT: dict[str, int] = {
    "shipping_quote": 600,
    "ship_order":     600,
}

# ── Core runner ───────────────────────────────────────────────────────────────
def run_co_helper(agent: str, payload: dict, timeout: int = 90) -> Optional[dict]:
    """Call the co_helper subprocess and return parsed JSON, or None on failure."""
    timeout = _AGENT_TIMEOUT.get(agent, timeout)
    script = _co_helper_script(agent)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), json.dumps(payload)],
            cwd=str(SRC),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip()[-500:], "lkw": [],
                    "fault_mode": payload["fault_mode"]}
        # co_helpers may print debug lines to stdout before the JSON payload;
        # find the last line that starts with '{' to extract the result dict.
        json_line = ""
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                json_line = line
                break
        if not json_line:
            stderr_hint = proc.stderr.strip()[-300:] if proc.stderr else ""
            return {"error": f"no JSON in stdout; stderr={stderr_hint}",
                    "lkw": [], "fault_mode": payload["fault_mode"]}
        return json.loads(json_line)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "lkw": [], "fault_mode": payload["fault_mode"]}
    except Exception as exc:
        return {"error": str(exc), "lkw": [], "fault_mode": payload["fault_mode"]}


def _infection_detected(result: dict, agent: str) -> Optional[str]:
    """Return the first infected LKW checkpoint step, or None if clean."""
    flags = B1_ORACLE.get(agent, {}).get("infection_flags", [])
    for cp in result.get("lkw", []):
        data = cp.get("data", {})
        if any(data.get(f) for f in flags):
            return cp.get("step")
    # also check top-level data dict from co_helper output
    for f in flags:
        if result.get(f):
            return "FINAL_ANSWER"
    return None


def _steps_reached(result: dict) -> list:
    return [cp.get("step") for cp in result.get("lkw", [])]


def _propagation_depth(result: dict, agent: str) -> int:
    expected = B1_ORACLE.get(agent, {}).get("expected_steps", [])
    reached  = set(_steps_reached(result))
    return sum(1 for s in expected if s not in reached)


# ── B2 phase ──────────────────────────────────────────────────────────────────
def run_b2_agent(agent: str, cfg: dict, n_runs: int) -> list[dict]:
    """Run FAULT_MODE=NONE N times for one agent/config. Returns list of run results."""
    results = []
    label   = cfg["label"]
    print(f"    [B2] {agent} @ {label}  ({n_runs} runs, NONE) ...", flush=True)
    for i in range(1, n_runs + 1):
        payload = _make_payload(agent, "NONE", cfg["model"], cfg["ollama_url"],
                                cfg["temperature"])
        result  = run_co_helper(agent, payload)
        if result is None:
            result = {"error": "null_result", "lkw": [], "fault_mode": "NONE"}

        out_path = B2_RAW_DIR / f"{agent}_{label}_none_run{i}.json"
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

        steps    = _steps_reached(result)
        infect   = _infection_detected(result, agent)
        prop     = _propagation_depth(result, agent)
        status   = "ERROR" if result.get("error") else ("INFECTED" if infect else "CLEAN")
        print(f"      run {i}/{n_runs} → {status}  steps={len(steps)}  "
              f"infection={infect}  prop={prop}", flush=True)

        results.append({
            "run": i, "agent": agent, "cfg": label, "fault_mode": "NONE",
            "steps_reached": steps, "infection_point": infect,
            "propagation_depth": prop, "status": status,
            "error": result.get("error"),
        })
    return results


# ── B3 phase ──────────────────────────────────────────────────────────────────
def run_b3_agent(agent: str, cfg: dict, fault_mode: str, n_runs: int) -> list[dict]:
    """Run one fault mode N times for one agent/config. Returns list of run results."""
    results = []
    label   = cfg["label"]
    for i in range(1, n_runs + 1):
        payload = _make_payload(agent, fault_mode, cfg["model"], cfg["ollama_url"],
                                cfg["temperature"])
        result  = run_co_helper(agent, payload)
        if result is None:
            result = {"error": "null_result", "lkw": [], "fault_mode": fault_mode}

        out_path = B3_RAW_DIR / f"{agent}_{label}_{fault_mode}_run{i}.json"
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

        steps  = _steps_reached(result)
        infect = _infection_detected(result, agent)
        prop   = _propagation_depth(result, agent)
        status = "ERROR" if result.get("error") else ("INFECTED" if infect else "CLEAN")

        results.append({
            "run": i, "agent": agent, "cfg": label, "fault_mode": fault_mode,
            "steps_reached": steps, "infection_point": infect,
            "propagation_depth": prop, "status": status,
            "error": result.get("error"),
        })
    return results


def _classify_mutant(b3_runs: list[dict], b2_clean_rate: float) -> str:
    """
    Killed mutant  = at least one B3 run shows infection or step loss
    Live mutant    = all B3 runs are CLEAN (fault hides inside B2 variance)
    Inconclusive   = all B3 runs errored
    """
    if all(r.get("status") == "ERROR" for r in b3_runs):
        return "INCONCLUSIVE"
    detected = any(
        r.get("infection_point") or r.get("propagation_depth", 0) > 0
        for r in b3_runs
    )
    return "KILLED" if detected else "LIVE"


# ── Summary builders ──────────────────────────────────────────────────────────
def build_b2_variance_report(all_b2: dict) -> dict:
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "agents": {}}
    for key, runs in all_b2.items():
        clean   = sum(1 for r in runs if r["status"] == "CLEAN")
        infect  = sum(1 for r in runs if r["status"] == "INFECTED")
        errors  = sum(1 for r in runs if r["status"] == "ERROR")
        total   = len(runs)
        clean_rate = round(clean / total, 3) if total else 0.0
        report["agents"][key] = {
            "runs": total, "clean": clean, "infected": infect, "errors": errors,
            "clean_rate": clean_rate,
            "comment": "natural LLM variance — infected_rate is false_positive_baseline",
        }
    return report


def build_b3_mutation_report(all_b3: dict, b2_variance: dict) -> dict:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fault_modes": {},
        "per_agent_kill_rates": {},
    }
    # Group by (agent, cfg, fault_mode)
    grouped: dict = {}
    for key, runs in all_b3.items():
        # key = "<agent>__<cfg>__<fault_mode>"
        for run in runs:
            gkey = f"{run['agent']}__{run['cfg']}__{run['fault_mode']}"
            grouped.setdefault(gkey, []).append(run)

    killed = live = inconclusive = 0
    per_agent: dict = {}
    for gkey, runs in grouped.items():
        agent, cfg, fm = gkey.split("__", 2)
        b2_key = f"{agent}__{cfg}"
        b2_clean_rate = b2_variance.get("agents", {}).get(b2_key, {}).get("clean_rate", 1.0)
        verdict = _classify_mutant(runs, b2_clean_rate)
        if verdict == "KILLED":
            killed += 1
        elif verdict == "LIVE":
            live += 1
        else:
            inconclusive += 1
        report["fault_modes"].setdefault(fm, {})[f"{agent}@{cfg}"] = {
            "verdict": verdict,
            "runs":    len(runs),
            "infected_runs": sum(1 for r in runs if r.get("infection_point")),
            "step_loss_runs": sum(1 for r in runs if r.get("propagation_depth", 0) > 0),
        }
        per_agent.setdefault(f"{agent}@{cfg}", {"killed": 0, "live": 0, "inconclusive": 0})
        per_agent[f"{agent}@{cfg}"][verdict.lower()] += 1

    total = killed + live + inconclusive
    report["summary"] = {
        "total_tests": total,
        "killed": killed,
        "live":   live,
        "inconclusive": inconclusive,
        "mutation_kill_rate": round(killed / total, 3) if total else 0.0,
    }
    report["per_agent_kill_rates"] = {
        k: round(v["killed"] / (v["killed"] + v["live"] + v["inconclusive"]), 3)
        for k, v in per_agent.items()
        if (v["killed"] + v["live"] + v["inconclusive"]) > 0
    }
    return report


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent",      default=None,
                        choices=list(B1_ORACLE.keys()),
                        help="Run a single agent only")
    parser.add_argument("--cfg",        default=None,
                        choices=["14b_temp0", "3b_temp0", "3b_temp0.7", "3b_temp1.0"],
                        help="Run a single model config only")
    parser.add_argument("--fault-mode", default=None,
                        help="B3: single fault mode only (implies --b3-only)")
    parser.add_argument("--b2-only",    action="store_true",
                        help="Run B2 baseline phase only")
    parser.add_argument("--b3-only",    action="store_true",
                        help="Run B3 fault injection phase only")
    parser.add_argument("--b2-runs",    type=int, default=10,
                        help="Number of NONE runs per agent per config (default: 10)")
    parser.add_argument("--b3-runs",    type=int, default=3,
                        help="Number of fault runs per agent per fault mode (default: 3)")
    parser.add_argument("--merge-report", action="store_true",
                        help="Scan all existing raw result files and regenerate combined report")
    args = parser.parse_args()

    # ── Merge-report mode: scan raw files → rebuild combined report ───────────
    if args.merge_report:
        import re as _re
        _known_agents = list(B1_ORACLE.keys())
        # Order matters: check longest names first to avoid prefix collisions
        _known_agents.sort(key=len, reverse=True)
        _known_cfgs   = ["14b_temp0", "3b_temp0.7", "3b_temp1.0", "3b_temp0"]
        # ↑ 3b_temp0.7/1.0 before 3b_temp0 so substring match doesn't mis-parse

        def _parse_raw_filename(stem: str):
            """Return (agent, cfg) or (None, None) if unparseable."""
            cfg = next((c for c in _known_cfgs if f"_{c}_" in stem), None)
            if cfg is None:
                return None, None
            agent = next((a for a in _known_agents if stem.startswith(a + "_")), None)
            return agent, cfg

        all_b2: dict = {}
        for f in sorted(B2_RAW_DIR.glob("*.json")):
            agent, cfg = _parse_raw_filename(f.stem)
            if agent is None:
                print(f"  [merge] skipping unrecognised B2 file: {f.name}")
                continue
            try:
                result = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            steps  = _steps_reached(result)
            infect = _infection_detected(result, agent)
            prop   = _propagation_depth(result, agent)
            status = "ERROR" if result.get("error") else ("INFECTED" if infect else "CLEAN")
            key    = f"{agent}__{cfg}"
            all_b2.setdefault(key, []).append({
                "run": 0, "agent": agent, "cfg": cfg, "fault_mode": "NONE",
                "steps_reached": steps, "infection_point": infect,
                "propagation_depth": prop, "status": status,
                "error": result.get("error"),
            })

        all_b3: dict = {}
        for f in sorted(B3_RAW_DIR.glob("*.json")):
            agent, cfg = _parse_raw_filename(f.stem)
            if agent is None:
                print(f"  [merge] skipping unrecognised B3 file: {f.name}")
                continue
            # fault mode is between cfg and _run{n}
            m = _re.search(rf"_{_re.escape(cfg)}_(.+)_run\d+$", f.stem)
            if m is None:
                continue
            fault_mode = m.group(1)
            try:
                result = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            steps  = _steps_reached(result)
            infect = _infection_detected(result, agent)
            prop   = _propagation_depth(result, agent)
            status = "ERROR" if result.get("error") else ("INFECTED" if infect else "CLEAN")
            key    = f"{agent}__{cfg}__{fault_mode}"
            all_b3.setdefault(key, []).append({
                "run": 0, "agent": agent, "cfg": cfg, "fault_mode": fault_mode,
                "steps_reached": steps, "infection_point": infect,
                "propagation_depth": prop, "status": status,
                "error": result.get("error"),
            })

        b2_report = build_b2_variance_report(all_b2)
        b3_report = build_b3_mutation_report(all_b3, b2_report)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "b2_variance":  b2_report.get("agents", {}),
            "b3_mutations": b3_report.get("summary", {}),
            "per_agent_kill_rates": b3_report.get("per_agent_kill_rates", {}),
            "fault_mode_verdicts":  b3_report.get("fault_modes", {}),
        }
        rpt_path = RESULTS / "per_agent_llm_report.json"
        rpt_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        b2_path = RESULTS / "per_agent_b2_variance.json"
        b2_path.write_text(json.dumps(b2_report, indent=2), encoding="utf-8")
        b3_path = RESULTS / "per_agent_b3_mutations.json"
        b3_path.write_text(json.dumps(b3_report, indent=2), encoding="utf-8")
        b2_count = sum(len(v) for v in all_b2.values())
        b3_count = sum(len(v) for v in all_b3.values())
        s = b3_report.get("summary", {})
        print(f"Merged {b2_count} B2 runs, {b3_count} B3 runs across "
              f"{len(set(k.split('__')[0] for k in all_b2))} agents")
        print(f"Kill rate: {s.get('killed',0)}/{s.get('total_tests',0)} = "
              f"{s.get('mutation_kill_rate',0.0):.1%}")
        print(f"Combined report → {rpt_path}")
        return


        args.b3_only = True

    # ── Model configs ──────────────────────────────────────────────────────────
    ollama_url = os.environ.get("OLLAMA_URL",  "http://localhost:11434")
    model_14b  = os.environ.get("LLAMA_MODEL", "qwen2.5-coder:14b")
    model_3b   = os.environ.get("MODEL_3B",    "qwen2.5:3b")
    all_cfgs = [
        {"label": "14b_temp0",  "model": model_14b, "temperature": 0.0, "ollama_url": ollama_url},
        {"label": "3b_temp0",   "model": model_3b,  "temperature": 0.0, "ollama_url": ollama_url},
        {"label": "3b_temp0.7", "model": model_3b,  "temperature": 0.7, "ollama_url": ollama_url},
        {"label": "3b_temp1.0", "model": model_3b,  "temperature": 1.0, "ollama_url": ollama_url},
    ]
    cfgs   = [c for c in all_cfgs if args.cfg is None or c["label"] == args.cfg]
    agents = [args.agent] if args.agent else list(B1_ORACLE.keys())
    faults = [args.fault_mode] if args.fault_mode else ALL_FAULT_MODES

    print("=" * 70)
    print("  PER-AGENT LLM FAULT INJECTION RUNNER")
    print("  Real Ollama inference — app.orchestrator (ReAct loop)")
    print(f"  Agents : {agents}")
    print(f"  Configs: {[c['label'] for c in cfgs]}")
    print(f"  B2 runs: {args.b2_runs} × NONE")
    print(f"  B3 runs: {args.b3_runs} × {len(faults)} fault modes")
    print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    all_b2: dict = {}
    all_b3: dict = {}

    # ── B2 phase ───────────────────────────────────────────────────────────────
    if not args.b3_only:
        print("\n  [PHASE B2] Natural LLM Variance Baseline (USE_LLM=true, FAULT_MODE=NONE)")
        for agent in agents:
            for cfg in cfgs:
                key   = f"{agent}__{cfg['label']}"
                runs  = run_b2_agent(agent, cfg, args.b2_runs)
                all_b2[key] = runs

        b2_report = build_b2_variance_report(all_b2)
        b2_path   = RESULTS / "per_agent_b2_variance.json"
        b2_path.write_text(json.dumps(b2_report, indent=2), encoding="utf-8")
        print(f"\n  B2 variance report  → {b2_path}")
    else:
        # Load existing B2 if running B3 only
        b2_path = RESULTS / "per_agent_b2_variance.json"
        b2_report = json.loads(b2_path.read_text()) if b2_path.exists() else {"agents": {}}

    # ── B3 phase ───────────────────────────────────────────────────────────────
    if not args.b2_only:
        print("\n  [PHASE B3] Fault Injection — Mutation Detection (USE_LLM=true, faults)")
        for agent in agents:
            for cfg in cfgs:
                print(f"\n  Agent: {agent} | Config: {cfg['label']}", flush=True)
                for fm in faults:
                    print(f"    [{fm}] {args.b3_runs} runs ...", flush=True)
                    key  = f"{agent}__{cfg['label']}__{fm}"
                    runs = run_b3_agent(agent, cfg, fm, args.b3_runs)
                    all_b3[key] = runs

                    infected = sum(1 for r in runs if r.get("infection_point"))
                    step_loss = sum(1 for r in runs if r.get("propagation_depth", 0) > 0)
                    verdict  = _classify_mutant(runs, 1.0)
                    print(f"    → {verdict}  infected={infected}/{args.b3_runs}  "
                          f"step_loss={step_loss}/{args.b3_runs}", flush=True)

        b3_report = build_b3_mutation_report(all_b3, b2_report)
        b3_path   = RESULTS / "per_agent_b3_mutations.json"
        b3_path.write_text(json.dumps(b3_report, indent=2), encoding="utf-8")
        print(f"\n  B3 mutation report  → {b3_path}")

        # ── Combined report ────────────────────────────────────────────────────
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "b2_variance":  b2_report.get("agents", {}),
            "b3_mutations": b3_report.get("summary", {}),
            "per_agent_kill_rates": b3_report.get("per_agent_kill_rates", {}),
            "fault_mode_verdicts":  b3_report.get("fault_modes", {}),
        }
        rpt_path = RESULTS / "per_agent_llm_report.json"
        rpt_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  Combined report     → {rpt_path}")

        # ── Console summary ────────────────────────────────────────────────────
        s = b3_report.get("summary", {})
        print("\n" + "=" * 70)
        print("  PER-AGENT LLM MUTATION SUMMARY")
        print("=" * 70)
        print(f"  Total tests    : {s.get('total_tests', 0)}")
        print(f"  Killed mutants : {s.get('killed', 0)}")
        print(f"  Live mutants   : {s.get('live', 0)}")
        print(f"  Inconclusive   : {s.get('inconclusive', 0)}")
        print(f"  Kill rate      : {s.get('mutation_kill_rate', 0.0):.1%}")
        print("\n  Per-agent kill rates:")
        for k, v in b3_report.get("per_agent_kill_rates", {}).items():
            print(f"    {k:<35} {v:.1%}")
        print(f"\n  Finished: {datetime.now(timezone.utc).isoformat()}")
        print("=" * 70)


if __name__ == "__main__":
    main()
