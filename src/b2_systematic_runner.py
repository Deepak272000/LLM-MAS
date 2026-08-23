"""
B2 Systematic Runner — Full Checkout Orchestration (Google Shop)
================================================================
Tests the COMPLETE end-to-end checkout flow, not individual agents in isolation.
Implements the checkout orchestrator the professor identified as missing.

Checkout chain (mirrors checkout-agent/agent/agent.go tool sequence):
  ProductCatalog → Currency → ShippingQuote → Payment → ShipOrder → Email

Three model configurations:
  CFG-1  qwen2.5-coder:14b  @ temperature=0.0  (current baseline)
  CFG-2  [3B_MODEL]         @ temperature=0.0  (compact, low-temp)
  CFG-3  [3B_MODEL]         @ temperature=0.7  (compact, moderate-high-temp)
  CFG-4  [3B_MODEL]         @ temperature=1.0  (compact, maximum-temp)

LKW Checkpoints (cross-agent):
  CHECKOUT_INIT
  HANDOFF_INIT_TO_PRODUCTCATALOG
    [productcatalog internal: TASK_START → CATALOG_DONE → FINAL_ANSWER]
  HANDOFF_PRODUCTCATALOG_TO_CURRENCY
    [currency internal: TASK_START → CONVERT_DONE → FINAL_ANSWER]
  HANDOFF_CURRENCY_TO_SHIPPING_QUOTE
    [shipping_quote internal: TASK_START → ... → FINAL_ANSWER]
  HANDOFF_SHIPPING_QUOTE_TO_PAYMENT
    [payment internal: TASK_START → CARD_VALIDATED → CHARGE_DONE → SAVE_DONE → FINAL_ANSWER]
  HANDOFF_PAYMENT_TO_SHIP_ORDER
    [ship_order internal: TASK_START → ... → FINAL_ANSWER]
  HANDOFF_SHIP_ORDER_TO_EMAIL
    [email internal: TASK_START → EMAIL_GENERATED → EMAIL_SENT → FINAL_ANSWER]
  CHECKOUT_COMPLETE

RIP Analysis per agent:
  R = Reachability:  Was TASK_START reached for this agent?
  I = Infection:     Does any checkpoint data deviate from B1 baseline?
  P = Propagation:   Did infected data appear in the next agent's inputs?

Deviation from B1:
  Loads results/b2_equivalence_thresholds.json (from b2_baseline_runner.py pilot).
  Compares per-agent LKW sequences and data against B1 per-agent baselines.

Output:
  results/b2_systematic/
    raw/checkout_{cfg}_{fault_mode}_run{n}.json  — one file per run
  results/b2_systematic/b2_checkout_lkw_summary.json
  results/b2_systematic/b2_rip_analysis.json
  results/b2_systematic/b2_deviation_from_b1.json
  results/b2_systematic/b2_model_comparison.json

Usage (SPEED HPC — tcsh):
  setenv OLLAMA_URL  http://localhost:11434
  setenv LLAMA_MODEL qwen2.5-coder:14b
  setenv MODEL_3B    qwen2.5:3b
  $VENV/bin/python b2_systematic_runner.py --runs 3
  $VENV/bin/python b2_systematic_runner.py --runs 3 --fault-mode NONE
  $VENV/bin/python b2_systematic_runner.py --skip-llm   # dry-run deterministic only
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
SRC     = Path(__file__).parent
RESULTS = SRC / "results" / "b2_systematic"
RAW_DIR = RESULTS / "raw"
RESULTS.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

# MODEL_TAG namespaces the four aggregate reports written by this module.
# Unset -> unchanged filenames, so the published qwen2.5:3b artifacts are
# reproduced byte-identically and can never be clobbered by a tagged campaign.
_MODEL_TAG = os.environ.get("MODEL_TAG", "").strip()
TAG_SUFFIX = f"_{_MODEL_TAG}" if _MODEL_TAG else ""

# ── Checkout scenario constants ───────────────────────────────────────────────
CHECKOUT_ADDRESS = {
    "street_address": "123 Main St",
    "city":           "Montreal",
    "state":          "QC",
    "country":        "Canada",
    "zip_code":       "H3A 0A1",
}
CHECKOUT_ITEMS = [
    {"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5},
]
CHECKOUT_CARD = {
    "credit_card_number":           "4111111111111111",
    "credit_card_cvv":              123,
    "credit_card_expiration_year":  2030,
    "credit_card_expiration_month": 12,
}
MOCK_PRODUCT = {
    "id":   "PROD-001",
    "name": "Sunglasses",
    "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
}
MOCK_CONVERT = {"currency_code": "USD", "units": 19, "nanos": 990000000}

# ── Model configs ─────────────────────────────────────────────────────────────
DEFAULT_SMALL_MODEL = "qwen2.5:3b"


def build_model_configs():
    ollama_url = os.environ.get("OLLAMA_URL",  "http://localhost:11434")
    model_14b  = os.environ.get("LLAMA_MODEL", "qwen2.5-coder:14b")
    model_3b   = os.environ.get("MODEL_3B",    DEFAULT_SMALL_MODEL)

    # MODEL_TAG namespaces the compact-model labels so a campaign run with a
    # different MODEL_3B writes its own result filenames instead of clobbering
    # an earlier campaign's artifacts. Leaving MODEL_TAG empty reproduces the
    # original label scheme exactly, so existing results and every script that
    # reads them keep working unchanged.
    tag = os.environ.get("MODEL_TAG", "").strip()
    if not tag and model_3b != DEFAULT_SMALL_MODEL:
        raise SystemExit(
            "REFUSING TO RUN: MODEL_3B is set to a non-default model "
            f"({model_3b!r}) while MODEL_TAG is empty.\n"
            "  Results are written as b3_{fault_mode}_{label}_run{n}.json. The "
            "label would remain '3b_*', silently overwriting the existing "
            f"{DEFAULT_SMALL_MODEL} artifacts.\n"
            "  Re-run with a namespace, e.g. MODEL_TAG=llama32-3b"
        )
    small = tag or "3b"

    return [
        {"label": "14b_temp0",        "model": model_14b, "temperature": 0.0, "ollama_url": ollama_url},
        {"label": f"{small}_temp0",   "model": model_3b,  "temperature": 0.0, "ollama_url": ollama_url},
        {"label": f"{small}_temp0.7", "model": model_3b,  "temperature": 0.7, "ollama_url": ollama_url},
        {"label": f"{small}_temp1.0", "model": model_3b,  "temperature": 1.0, "ollama_url": ollama_url},
    ]

# ── B1 per-agent expected checkpoint sequences ────────────────────────────────
B1_EXPECTED_STEPS = {
    # Orchestrator agent (Python mirror of checkout-agent/agent/agent.go)
    "checkout_orchestrator": [
        "TASK_START",
        "PRODUCT_FETCHED",
        "CURRENCY_CONVERTED",
        "SHIPPING_QUOTED",
        "PAYMENT_CHARGED",
        "ORDER_SHIPPED",
        "CONFIRMATION_SENT",
        "FINAL_ANSWER",
    ],
    "productcatalog": ["TASK_START", "CATALOG_DONE", "FINAL_ANSWER"],
    "currency":       ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"],
    "shipping_quote": ["TASK_START", "FINAL_ANSWER"],
    "payment":        ["TASK_START", "CARD_VALIDATED", "CHARGE_DONE", "SAVE_DONE", "FINAL_ANSWER"],
    "ship_order":     ["TASK_START", "FINAL_ANSWER"],
    "email":          ["TASK_START", "EMAIL_GENERATED", "EMAIL_SENT", "FINAL_ANSWER"],
}

# ─────────────────────────────────────────────────────────────────────────────
# Inline helper scripts — written to SRC at runtime, run as subprocesses
# ─────────────────────────────────────────────────────────────────────────────

def _write_productcatalog_helper():
    """Write checkout helper for productcatalogagent — LLM classify_request via Ollama REST."""
    script = '''\
"""ProductCatalog checkout helper — LLM classify_request (Ollama REST) + agent.run()."""
import json, os, sys, importlib, requests
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).parent
AGENT_DIR = SRC / "productcatalogagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub gRPC/proto so imports don't fail without live microservices
sys.modules.setdefault("grpc", MagicMock())
for _m in ("app.clients", "app.clients.demo_pb2", "app.clients.demo_pb2_grpc"):
    sys.modules.setdefault(_m, MagicMock())

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
query      = payload.get("query",      "list all products")

os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

MOCK_PRODUCT = {
    "id": "PROD-001", "name": "Sunglasses",
    "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
}
MOCK_PRODUCT_2 = {
    "id": "PROD-002", "name": "Candle Holder",
    "price_usd": {"currency_code": "USD", "units": 12, "nanos": 500000000},
}
mock_client = MagicMock()
mock_client.list_products.return_value  = [MOCK_PRODUCT, MOCK_PRODUCT_2]
mock_client.get_product.return_value    = MOCK_PRODUCT
mock_client.search_products.return_value = [MOCK_PRODUCT, MOCK_PRODUCT_2]
agent_mod.client = mock_client

def _classify_catalog(query, model, ollama_url):
    """LLM routing call — mirrors graph.py classify_request node."""
    prompt = (
        "You are a router for a product catalog service.\\n"
        "Classify the user request into exactly one label:\\n"
        "- list_products\\n- search_products\\n- get_product\\n\\n"
        "User query: " + query + "\\n\\nReturn only one label."
    )
    try:
        resp = requests.post(
            ollama_url + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        label = resp.json().get("response", "list_products").strip().lower()
    except Exception:
        label = "list_products"
    if "list_products" in label: return "list_products"
    if "get_product"   in label: return "get_product"
    return "search_products"

route = _classify_catalog(query, model, ollama_url)

agent = agent_mod.ProductCatalogAgent()
if route == "get_product":
    result = agent.run(query=query, product_ids=["PROD-001"])
elif route == "list_products":
    result = agent.run(query="list all products")
else:
    result = agent.run(query=query)

lkw      = fi_mod.get_lkw()
data     = result.get("data", [])
products = data if isinstance(data, list) else [data]
print(json.dumps({"lkw": lkw, "products": products, "fault_mode": fault_mode}))
'''
    path = SRC / "co_helper_productcatalog.py"
    path.write_text(script, encoding="utf-8")
    return path


def _write_currency_helper():
    """Write checkout helper for currencyagent — LLM classify_request via Ollama REST."""
    script = '''\
"""Currency checkout helper — LLM classify_request (Ollama REST) + agent.run()."""
import json, os, sys, importlib, requests
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).parent
AGENT_DIR = SRC / "currencyagent"
sys.path.insert(0, str(AGENT_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
query      = payload.get("query",      "convert 19 USD to USD")

os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

converted = {
    "currency_code": payload.get("to_currency", "USD"),
    "units": payload.get("units", 19),
    "nanos": payload.get("nanos", 990000000),
}
mock_client = MagicMock()
mock_client.convert.return_value = dict(converted)
mock_client.get_supported_currencies.return_value = ["USD", "EUR", "GBP", "CAD"]
agent_mod.client = mock_client

def _classify_currency(query, model, ollama_url):
    """LLM routing call — mirrors graph.py classify_request node."""
    prompt = (
        "You are a router for a currency service.\\n"
        "Classify the user request into exactly one label:\\n"
        "- get_supported_currencies\\n- convert\\n\\n"
        "User query: " + query + "\\n\\nReturn only one label."
    )
    try:
        resp = requests.post(
            ollama_url + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        label = resp.json().get("response", "convert").strip().lower()
    except Exception:
        label = "convert"
    return "get_supported_currencies" if "get_supported" in label else "convert"

route = _classify_currency(query, model, ollama_url)

agent = agent_mod.CurrencyAgent()
if route == "get_supported_currencies":
    result = agent.run(query=query, action="get_supported_currencies")
else:
    result = agent.run(
        query=query,
        action="convert",
        from_currency=payload.get("from_currency", "USD"),
        units=payload.get("units", 19),
        nanos=payload.get("nanos", 990000000),
        to_currency=payload.get("to_currency", "USD"),
    )

lkw           = result.get("lkw", [])
converted_out = result.get("data", converted)
print(json.dumps({"lkw": lkw, "converted": converted_out, "fault_mode": fault_mode}))
'''
    path = SRC / "co_helper_currency.py"
    path.write_text(script, encoding="utf-8")
    return path


def _write_payment_helper():
    """Write checkout helper for paymentagent — LLM classify_request via Ollama REST."""
    script = '''\
"""Payment checkout helper — LLM classify_request (Ollama REST) + async agent.run()."""
import asyncio, json, os, sys, importlib, requests
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SRC = Path(__file__).parent
AGENT_DIR = SRC / "paymentagent"
sys.path.insert(0, str(AGENT_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
query      = payload.get("query",      "charge card")

os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

def _classify_payment(query, model, ollama_url):
    """LLM routing call — mirrors graph.py classify_request node."""
    prompt = (
        "You are a router for a payment service.\\n"
        "Classify the user request into exactly one label:\\n"
        "- charge\\n\\n"
        "User query: " + query + "\\n\\nReturn only one label."
    )
    try:
        requests.post(
            ollama_url + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
    except Exception:
        pass
    return "charge"  # payment always routes to charge

_classify_payment(query, model, ollama_url)

async def main():
    agent = agent_mod.PaymentAgent()
    with patch("app.agent.save_transaction", new_callable=AsyncMock) as ms:
        ms.return_value = "co-tx-mock-id"
        result = await agent.run(
            query=query,
            currency_code=payload.get("currency_code", "USD"),
            units=payload.get("units", 27),
            nanos=payload.get("nanos", 490000000),
            credit_card_number=payload.get("credit_card_number", "4111111111111111"),
            credit_card_cvv=payload.get("credit_card_cvv", 123),
            credit_card_expiration_year=payload.get("credit_card_expiration_year", 2030),
            credit_card_expiration_month=payload.get("credit_card_expiration_month", 12),
        )
    lkw            = result.get("lkw", [])
    data           = result.get("data", {})
    transaction_id = data.get("transaction_id", "mock-tx-123") if isinstance(data, dict) else "mock-tx-123"
    print(json.dumps({"lkw": lkw, "transaction_id": transaction_id,
                      "data": data, "fault_mode": fault_mode}))

asyncio.run(main())
'''
    path = SRC / "co_helper_payment.py"
    path.write_text(script, encoding="utf-8")
    return path


def _write_email_helper():
    """Write checkout helper for emailserviceagent — real LLM via Ollama (USE_LLM=true)."""
    script = '''\
"""Email checkout helper — real LLM content generation via Ollama (USE_LLM=true)."""
import json, os, sys, importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).parent
AGENT_DIR = SRC / "emailserviceagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub gRPC (email microservice send call) and langgraph (not in shippingservice venv)
sys.modules.setdefault("grpc", MagicMock())
sys.modules.setdefault("demo_pb2", MagicMock())
sys.modules.setdefault("demo_pb2_grpc", MagicMock())
_lg = MagicMock()
_lg.END = "__end__"
sys.modules.setdefault("langgraph", MagicMock())
sys.modules["langgraph.graph"] = _lg

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")

# Set env BEFORE any app imports — config.Settings reads USE_LLM at class definition time
os.environ["FAULT_MODE"]    = fault_mode
os.environ["MODEL_NAME"]    = model
os.environ["OLLAMA_BASE_URL"] = ollama_url
os.environ["USE_LLM"]       = "true"   # enable real LLM email generation
os.environ["TEMPERATURE"]   = str(payload.get("temperature", 0.0))

for _k in ("app.fault_injection", "app.config", "app.agent", "app.graph"):
    sys.modules.pop(_k, None)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)

MOCK_SEND = {"status": "sent"}

try:
    import app.graph as graph_mod
    importlib.reload(graph_mod)
    graph_mod.fi = fi_mod

    state = {
        "request": {
            "email":            payload.get("email",     "customer@example.com"),
            "order_id":         payload.get("order_id",  "ORDER-B2-001"),
            "user_name":        payload.get("user_name", "Test Customer"),
            "currency_code":    payload.get("currency_code", "USD"),
            "total":            payload.get("total",     27.49),
            "items":            payload.get("items",     [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}]),
            "shipping_address": payload.get("shipping_address", {}),
        },
        "handoff_contract": None,
        "email_type": "", "subject": "", "body": "",
        "llm_used": False, "microservice_status": "",
    }

    # Call graph nodes directly (no LangGraph runtime needed)
    state = graph_mod.run_agent_node(state)       # real LLM email generation here
    from app.grpc_client import EmailServiceClient
    with patch.object(EmailServiceClient, "send_confirmation_email", return_value=MOCK_SEND):
        state = graph_mod.send_via_microservice_node(state)

    lkw = fi_mod.get_lkw() if hasattr(fi_mod, "get_lkw") else getattr(fi_mod, "_global_lkw", [])
except Exception as e:
    import traceback; traceback.print_exc(file=sys.stderr)
    lkw = [{"step": "TASK_START", "data": {"fault_mode": fault_mode}},
           {"step": "FINAL_ANSWER", "data": {"error": str(e)}}]

print(json.dumps({"lkw": lkw, "status": "sent", "fault_mode": fault_mode}))
'''
    path = SRC / "co_helper_email.py"
    path.write_text(script, encoding="utf-8")
    return path


def _write_shipping_checkout_helper():
    """Write shipping helper supporting both get_quote and ship_order actions."""
    script = '''\
"""Shipping checkout helper — supports get_quote and ship_order actions."""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SRC      = Path(__file__).parent
SHIP_DIR = SRC / "shippingagent"
sys.path.insert(0, str(SHIP_DIR / "app"))
sys.path.insert(0, str(SHIP_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
action     = payload.get("action", "ship_order")
fault_mode = payload.get("fault_mode", "NONE")

os.environ["FAULT_MODE"] = fault_mode

for name in ("grpc", "motor", "motor.motor_asyncio", "pymongo", "dotenv"):
    sys.modules.setdefault(name, MagicMock())

config_mock = MagicMock()
config_mock.LLAMA_BASE_URL = payload.get("ollama_url", "http://localhost:11434") + "/v1"
config_mock.LLAMA_MODEL    = payload.get("model",      "qwen2.5-coder:14b")
config_mock.LLAMA_TEMPERATURE = float(payload.get("temperature", 0.0))
sys.modules["config"] = config_mock

from app.orchestrator import ShippingOrchestrator

ADDRESS = payload.get("address", {
    "street_address": "123 Main St", "city": "Montreal",
    "state": "QC", "country": "Canada", "zip_code": "H3A 0A1",
})
ITEMS = payload.get("items", [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}])

async def main():
    orch = ShippingOrchestrator()
    with patch("app.orchestrator.save_shipment", new_callable=AsyncMock) as ms, \\
         patch("app.orchestrator.save_quote",    new_callable=AsyncMock) as mq:
        ms.return_value = "co-ship-id"
        mq.return_value = "co-quote-id"
        if action == "get_quote":
            result = await orch.get_quote(address=ADDRESS, items=ITEMS,
                                          capture_partial_trace=True)
        else:
            result = await orch.ship_order(address=ADDRESS, items=ITEMS,
                                           capture_partial_trace=True)

    lkw_dict = result.get("_lkw", {})
    trace    = lkw_dict.get("checkpoints", [])
    cost_usd = result.get("cost_usd", None)
    tracking = result.get("tracking_id", None)
    error    = result.get("error", None)

    print(json.dumps({
        "lkw":          trace,
        "cost_usd":     cost_usd,
        "tracking_id":  tracking,
        "error":        error,
        "action":       action,
        "fault_mode":   fault_mode,
        "model":        payload.get("model", "qwen2.5-coder:14b"),
        "temperature":  payload.get("temperature", 0.0),
    }))

asyncio.run(main())
'''
    path = SRC / "co_helper_shipping.py"
    path.write_text(script, encoding="utf-8")
    return path


def _all_helpers_exist():
    names = ["co_helper_productcatalog.py", "co_helper_currency.py",
             "co_helper_payment.py",        "co_helper_email.py",
             "co_helper_shipping.py"]
    return all((SRC / n).exists() for n in names)


def write_all_helpers():
    _write_productcatalog_helper()
    _write_currency_helper()
    _write_payment_helper()
    _write_email_helper()
    _write_shipping_checkout_helper()
    print("[helpers] all checkout helpers written")


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_helper(helper_path, payload_dict, env_extra=None, timeout=300):
    """Run a checkout helper subprocess, return parsed JSON output."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)

    proc = subprocess.run(
        [sys.executable, str(helper_path), json.dumps(payload_dict)],
        cwd=SRC,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )

    if proc.returncode != 0:
        stderr = proc.stderr[-600:] if proc.stderr else "(no stderr)"
        raise RuntimeError(
            f"{helper_path.name} exited {proc.returncode}: {stderr}"
        )

    # Parse last valid JSON line from stdout (helpers may print progress)
    stdout = proc.stdout.strip()
    if not stdout:
        raise ValueError(f"{helper_path.name} produced no stdout")

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)

    raise ValueError(f"{helper_path.name} stdout not parseable: {stdout[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
# One full checkout run
# ─────────────────────────────────────────────────────────────────────────────

def _make_lkw_entry(step, agent, data):
    return {
        "step":      step,
        "agent":     agent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data":      data,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator-driven checkout (True B2/B3 mode)
# ─────────────────────────────────────────────────────────────────────────────

def _run_checkout_via_orchestrator(fault_mode: str, model_cfg: dict, run_idx: int,
                                   fault_agent: str = "all") -> dict:
    """
    Run one checkout through the LLM-driven checkout orchestrator agent,
    which mirrors the Go checkout-agent ReAct loop.  The LLM decides which
    tools to call and in what order — nothing is Python-hardcoded.

    Returns the same dict shape as run_checkout_once() so callers are unchanged.
    """
    run_id   = str(uuid.uuid4())[:8]
    model    = model_cfg["model"]
    temp     = model_cfg["temperature"]
    label    = model_cfg["label"]
    start_ts = datetime.now(timezone.utc)

    payload = {
        "fault_mode":    fault_mode,
        "fault_agent":   fault_agent,   # "all" = global; service name = targeted
        "model":         model,
        "temperature":   temp,
        "ollama_url":    model_cfg["ollama_url"],
        "order_id":      f"CO-{run_id[:6].upper()}",
        "email":         "customer@example.com",
        "user_name":     "Test Customer",
        "address":       CHECKOUT_ADDRESS,
        "items":         CHECKOUT_ITEMS,
        # Human-readable item list passed through to the email helper
        "items_desc":    [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}],
        **CHECKOUT_CARD,
    }

    orch_path = SRC / "co_helper_checkout_orchestrator.py"
    if not orch_path.exists():
        raise FileNotFoundError(
            f"Orchestrator helper missing at {orch_path}. "
            "Ensure co_helper_checkout_orchestrator.py is in src/."
        )

    try:
        orch_result = _run_helper(orch_path, payload, timeout=900)
    except Exception as exc:
        elapsed_ms = (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
        return {
            "run_id":         run_id,
            "run_idx":        run_idx,
            "fault_mode":     fault_mode,
            "model":          model,
            "temperature":    temp,
            "model_label":    label,
            "elapsed_ms":     round(elapsed_ms, 1),
            "success":        False,
            "errors":         {"checkout_orchestrator": str(exc)},
            "checkout_lkw":   [],
            "per_agent_lkw":  {"checkout_orchestrator": []},
            "rip":            {},
            "order_id":       payload["order_id"],
            "transaction_id": None,
            "tracking_id":    None,
        }

    # ── Unpack orchestrator output ─────────────────────────────────────────
    sub_agent_lkw = orch_result.get("per_agent_lkw", {})
    orch_lkw      = orch_result.get("lkw", [])

    # per_agent_lkw: orchestrator first, then each sub-agent
    per_agent_lkw = {"checkout_orchestrator": orch_lkw, **sub_agent_lkw}

    # Build unified checkout_lkw from orchestrator trace + sub-agent steps
    checkout_lkw: list = []
    for cp in orch_lkw:
        checkout_lkw.append(_make_lkw_entry(
            cp["step"], "checkout_orchestrator", cp.get("data", {})))
    for agent_name, agent_trace in sub_agent_lkw.items():
        for cp in agent_trace:
            checkout_lkw.append(_make_lkw_entry(
                f"{agent_name.upper()}_{cp['step']}", agent_name, cp.get("data", {})))

    # Errors: sub-agents with empty traces were likely never called by the LLM
    errors: dict = {}
    for agent_name, agent_trace in sub_agent_lkw.items():
        if not agent_trace:
            errors[agent_name] = "no trace — agent may not have been called by orchestrator"

    orch_status = orch_result.get("status", "unknown")
    success = orch_status in ("ok", "max_iterations") and not errors

    # Extract transaction_id and tracking_id from sub-agent traces
    transaction_id = "orch-tx-unknown"
    tracking_id    = None
    for cp in sub_agent_lkw.get("payment", []):
        td = cp.get("data", {}).get("transaction_id")
        if td:
            transaction_id = td
            break
    for cp in sub_agent_lkw.get("ship_order", []):
        ti = cp.get("data", {}).get("tracking_id")
        if ti:
            tracking_id = ti
            break

    elapsed_ms = (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
    rip        = _compute_rip(per_agent_lkw, errors)

    print(f"    [checkout_orchestrator] "
          f"status={orch_status} "
          f"iterations={orch_result.get('iterations', 0)} "
          f"steps={[c['step'] for c in orch_lkw]}")
    if orch_status == "error":
        error_detail = next(
            (cp.get("data", {}).get("error", "no detail")
             for cp in orch_lkw
             if cp.get("step") == "FINAL_ANSWER" and cp.get("data", {}).get("error")),
            "no error detail in FINAL_ANSWER checkpoint",
        )
        print(f"    [checkout_orchestrator] INFRA ERROR: {str(error_detail)[:300]}")

    return {
        "run_id":                  run_id,
        "run_idx":                 run_idx,
        "fault_mode":              fault_mode,
        "model":                   model,
        "temperature":             temp,
        "model_label":             label,
        "elapsed_ms":              round(elapsed_ms, 1),
        "success":                 success,
        "errors":                  errors,
        "checkout_lkw":            checkout_lkw,
        "per_agent_lkw":           per_agent_lkw,
        "rip":                     rip,
        "order_id":                payload["order_id"],
        "transaction_id":          transaction_id,
        "tracking_id":             tracking_id,
        "orchestrator_iterations": orch_result.get("iterations", 0),
        "orchestrator_status":     orch_status,
    }


def run_checkout_once(fault_mode, model_cfg, run_idx, skip_llm=False, fault_agent="all"):
    """
    Execute one full checkout flow.
    Returns a dict with unified checkout LKW + per-agent LKW + RIP summary.

    When skip_llm=False (default), routes through the LLM-driven checkout
    orchestrator agent (co_helper_checkout_orchestrator.py), which mirrors
    the Go checkout-agent ReAct loop — the LLM decides tool ordering.
    When skip_llm=True, falls back to the legacy hardcoded sequential helpers
    (dry-run / deterministic mode only).
    """
    # ── Orchestrator path — True B2/B3 mode ──────────────────────────────────
    if not skip_llm:
        return _run_checkout_via_orchestrator(fault_mode, model_cfg, run_idx, fault_agent)

    # ── Legacy fallback (skip_llm=True dry-run mode) ──────────────────────────
    run_id   = str(uuid.uuid4())[:8]
    model    = model_cfg["model"]
    temp     = model_cfg["temperature"]
    label    = model_cfg["label"]
    ship_env = {
        "FAULT_MODE":   fault_mode,
        "LLAMA_MODEL":  model,
        "OLLAMA_URL":   model_cfg["ollama_url"],
    }

    checkout_lkw  = []
    per_agent_lkw = {}
    errors        = {}
    start_ts      = datetime.now(timezone.utc)

    checkout_lkw.append(_make_lkw_entry(
        "CHECKOUT_INIT", "orchestrator",
        {"run_id": run_id, "fault_mode": fault_mode,
         "model": model, "temperature": temp,
         "address": CHECKOUT_ADDRESS, "items": CHECKOUT_ITEMS,
         "card_last4": CHECKOUT_CARD["credit_card_number"][-4:]}
    ))

    # ── Step 1: ProductCatalog ─────────────────────────────────────────────
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_INIT_TO_PRODUCTCATALOG", "orchestrator",
        {"product_ids": [i["product_id"] for i in CHECKOUT_ITEMS]}
    ))
    try:
        pc_payload = {"fault_mode": fault_mode, "query": "list all products",
                      "model": model, "ollama_url": model_cfg["ollama_url"]}
        pc_result  = _run_helper(SRC / "co_helper_productcatalog.py", pc_payload)
        per_agent_lkw["productcatalog"] = pc_result.get("lkw", [])
        products  = pc_result.get("products", [MOCK_PRODUCT])
        product   = products[0] if products else MOCK_PRODUCT
        price_usd = product.get("price_usd", MOCK_PRODUCT["price_usd"])
        for cp in per_agent_lkw["productcatalog"]:
            checkout_lkw.append(_make_lkw_entry(
                f"PRODUCTCATALOG_{cp['step']}", "productcatalog", cp.get("data", {})))
        print(f"    [productcatalog] steps={[c['step'] for c in per_agent_lkw['productcatalog']]}")
    except Exception as exc:
        errors["productcatalog"] = str(exc)
        price_usd = MOCK_PRODUCT["price_usd"]
        per_agent_lkw["productcatalog"] = []
        print(f"    [productcatalog] ERROR: {exc}")

    # ── Step 2: Currency Conversion ───────────────────────────────────────
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_PRODUCTCATALOG_TO_CURRENCY", "orchestrator",
        {"price_usd": price_usd,
         "from_currency": price_usd.get("currency_code", "USD"),
         "to_currency": "USD"}
    ))
    try:
        curr_payload = {
            "fault_mode":    fault_mode,
            "query":         f"convert {price_usd.get('units', 19)} USD to USD",
            "from_currency": price_usd.get("currency_code", "USD"),
            "units":         price_usd.get("units", 19),
            "nanos":         price_usd.get("nanos", 990000000),
            "to_currency":   "USD",
            "model":         model,
            "ollama_url":    model_cfg["ollama_url"],
        }
        curr_result = _run_helper(SRC / "co_helper_currency.py", curr_payload)
        per_agent_lkw["currency"] = curr_result.get("lkw", [])
        converted   = curr_result.get("converted", MOCK_CONVERT)
        if isinstance(converted, dict) and "units" not in converted:
            converted = MOCK_CONVERT
        for cp in per_agent_lkw["currency"]:
            checkout_lkw.append(_make_lkw_entry(
                f"CURRENCY_{cp['step']}", "currency", cp.get("data", {})))
        print(f"    [currency]       steps={[c['step'] for c in per_agent_lkw['currency']]}")
    except Exception as exc:
        errors["currency"] = str(exc)
        converted = MOCK_CONVERT
        per_agent_lkw["currency"] = []
        print(f"    [currency]       ERROR: {exc}")

    # ── Step 3: Shipping Quote ────────────────────────────────────────────
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_CURRENCY_TO_SHIPPING_QUOTE", "orchestrator",
        {"converted_price": converted, "address": CHECKOUT_ADDRESS, "items": CHECKOUT_ITEMS}
    ))
    cost_usd = 7.50
    if not skip_llm:
        try:
            sq_payload = {
                "action":      "get_quote",
                "fault_mode":  fault_mode,
                "model":       model,
                "temperature": temp,
                "ollama_url":  model_cfg["ollama_url"],
                "address":     CHECKOUT_ADDRESS,
                "items":       CHECKOUT_ITEMS,
            }
            sq_result = _run_helper(SRC / "co_helper_shipping.py", sq_payload,
                                    env_extra=ship_env, timeout=300)
            per_agent_lkw["shipping_quote"] = sq_result.get("lkw", [])
            cost_usd = sq_result.get("cost_usd") or 7.50
            for cp in per_agent_lkw["shipping_quote"]:
                checkout_lkw.append(_make_lkw_entry(
                    f"SHIPPING_QUOTE_{cp['step']}", "shipping_quote", cp.get("data", {})))
            print(f"    [shipping_quote] steps={[c['step'] for c in per_agent_lkw['shipping_quote']]}")
        except Exception as exc:
            errors["shipping_quote"] = str(exc)
            per_agent_lkw["shipping_quote"] = []
            print(f"    [shipping_quote] ERROR: {exc}")
    else:
        per_agent_lkw["shipping_quote"] = [
            {"step": "TASK_START",    "data": {"skipped": True}},
            {"step": "FINAL_ANSWER",  "data": {"cost_usd": cost_usd, "skip_llm": True}},
        ]
        for cp in per_agent_lkw["shipping_quote"]:
            checkout_lkw.append(_make_lkw_entry(
                f"SHIPPING_QUOTE_{cp['step']}", "shipping_quote", cp.get("data", {})))

    # ── Compute total (price + shipping) ──────────────────────────────────
    price_units = converted.get("units", 19) if isinstance(converted, dict) else 19
    price_nanos = converted.get("nanos",  990000000) if isinstance(converted, dict) else 990000000
    shipping_units = int(cost_usd) if cost_usd else 7
    total_units    = price_units + shipping_units
    total_nanos    = price_nanos

    # ── Step 4: Payment ───────────────────────────────────────────────────
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_SHIPPING_QUOTE_TO_PAYMENT", "orchestrator",
        {"total_units": total_units, "total_nanos": total_nanos,
         "currency_code": converted.get("currency_code", "USD") if isinstance(converted, dict) else "USD",
         "cost_usd": cost_usd}
    ))
    transaction_id = "co-tx-fallback"
    try:
        pay_payload = dict(CHECKOUT_CARD)
        pay_payload.update({
            "fault_mode":    fault_mode,
            "query":         "charge card for checkout order",
            "currency_code": converted.get("currency_code", "USD") if isinstance(converted, dict) else "USD",
            "units":         total_units,
            "nanos":         total_nanos,
            "model":         model,
            "ollama_url":    model_cfg["ollama_url"],
        })
        pay_result     = _run_helper(SRC / "co_helper_payment.py", pay_payload)
        per_agent_lkw["payment"] = pay_result.get("lkw", [])
        transaction_id = pay_result.get("transaction_id", "co-tx-fallback")
        for cp in per_agent_lkw["payment"]:
            checkout_lkw.append(_make_lkw_entry(
                f"PAYMENT_{cp['step']}", "payment", cp.get("data", {})))
        print(f"    [payment]        steps={[c['step'] for c in per_agent_lkw['payment']]}")
    except Exception as exc:
        errors["payment"] = str(exc)
        per_agent_lkw["payment"] = []
        print(f"    [payment]        ERROR: {exc}")

    # ── Step 5: Ship Order ────────────────────────────────────────────────
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_PAYMENT_TO_SHIP_ORDER", "orchestrator",
        {"transaction_id": transaction_id, "address": CHECKOUT_ADDRESS, "items": CHECKOUT_ITEMS}
    ))
    tracking_id = None
    if not skip_llm:
        try:
            so_payload = {
                "action":      "ship_order",
                "fault_mode":  fault_mode,
                "model":       model,
                "temperature": temp,
                "ollama_url":  model_cfg["ollama_url"],
                "address":     CHECKOUT_ADDRESS,
                "items":       CHECKOUT_ITEMS,
            }
            so_result   = _run_helper(SRC / "co_helper_shipping.py", so_payload,
                                      env_extra=ship_env, timeout=300)
            per_agent_lkw["ship_order"] = so_result.get("lkw", [])
            tracking_id = so_result.get("tracking_id")
            for cp in per_agent_lkw["ship_order"]:
                checkout_lkw.append(_make_lkw_entry(
                    f"SHIP_ORDER_{cp['step']}", "ship_order", cp.get("data", {})))
            print(f"    [ship_order]     steps={[c['step'] for c in per_agent_lkw['ship_order']]}")
        except Exception as exc:
            errors["ship_order"] = str(exc)
            per_agent_lkw["ship_order"] = []
            print(f"    [ship_order]     ERROR: {exc}")
    else:
        per_agent_lkw["ship_order"] = [
            {"step": "TASK_START",   "data": {"skipped": True}},
            {"step": "FINAL_ANSWER", "data": {"tracking_id": "SKIP-001", "skip_llm": True}},
        ]
        tracking_id = "SKIP-001"
        for cp in per_agent_lkw["ship_order"]:
            checkout_lkw.append(_make_lkw_entry(
                f"SHIP_ORDER_{cp['step']}", "ship_order", cp.get("data", {})))

    # ── Step 6: Email ─────────────────────────────────────────────────────
    order_id = f"CO-{run_id[:6].upper()}"
    checkout_lkw.append(_make_lkw_entry(
        "HANDOFF_SHIP_ORDER_TO_EMAIL", "orchestrator",
        {"tracking_id": tracking_id, "transaction_id": transaction_id, "order_id": order_id}
    ))
    try:
        em_payload = {
            "fault_mode":       fault_mode,
            "email":            "customer@example.com",
            "order_id":         order_id,
            "user_name":        "Test Customer",
            "currency_code":    converted.get("currency_code", "USD") if isinstance(converted, dict) else "USD",
            "total":            total_units + total_nanos / 1_000_000_000.0,
            "items":            [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}],
            "shipping_address": CHECKOUT_ADDRESS,
            "model":            model,
            "ollama_url":       model_cfg["ollama_url"],
            "temperature":      temp,
        }
        em_result = _run_helper(SRC / "co_helper_email.py", em_payload)
        per_agent_lkw["email"] = em_result.get("lkw", [])
        for cp in per_agent_lkw["email"]:
            checkout_lkw.append(_make_lkw_entry(
                f"EMAIL_{cp['step']}", "email", cp.get("data", {})))
        print(f"    [email]          steps={[c['step'] for c in per_agent_lkw['email']]}")
    except Exception as exc:
        errors["email"] = str(exc)
        per_agent_lkw["email"] = []
        print(f"    [email]          ERROR: {exc}")

    # ── Checkout Complete ──────────────────────────────────────────────────
    elapsed_ms = (datetime.now(timezone.utc) - start_ts).total_seconds() * 1000
    success    = len(errors) == 0
    checkout_lkw.append(_make_lkw_entry(
        "CHECKOUT_COMPLETE" if success else "CHECKOUT_FAILED",
        "orchestrator",
        {"order_id": order_id, "transaction_id": transaction_id,
         "tracking_id": tracking_id, "elapsed_ms": round(elapsed_ms, 1),
         "errors": errors, "success": success}
    ))

    # ── RIP Analysis ──────────────────────────────────────────────────────
    rip = _compute_rip(per_agent_lkw, errors)

    return {
        "run_id":         run_id,
        "run_idx":        run_idx,
        "fault_mode":     fault_mode,
        "model":          model,
        "temperature":    temp,
        "model_label":    label,
        "elapsed_ms":     round(elapsed_ms, 1),
        "success":        success,
        "errors":         errors,
        "checkout_lkw":   checkout_lkw,
        "per_agent_lkw":  per_agent_lkw,
        "rip":            rip,
        "order_id":       order_id,
        "transaction_id": transaction_id,
        "tracking_id":    tracking_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RIP Analysis
# ─────────────────────────────────────────────────────────────────────────────

_INFECTION_KEYS = {
    "hallucinated", "amount_tampered", "validation_bypassed", "save_skipped",
    "double_charge", "forced_decline", "premature_termination",
    "currency_swapped", "rate_manipulated", "stale_rate", "overflow",
    "unavailable", "query_tampered", "action_swapped", "price_manipulated",
    "duplicate_product", "wrong_category", "product_missing",
    "corrupted_body", "double_send", "send_skipped", "wrong_customer",
    "empty_recs", "shuffled_recs", "injection_recs", "self_recommendation",
}


def _is_infected(checkpoint):
    """Return True if a checkpoint's data shows fault injection markers."""
    data = checkpoint.get("data", {})
    if not isinstance(data, dict):
        return False
    # Check direct infection keys
    if any(data.get(k) for k in _INFECTION_KEYS):
        return True
    # Check for FAKE / PREMATURE markers in string values
    for v in data.values():
        if isinstance(v, str) and any(x in v for x in ("FAKE", "PREMATURE", "INJECT", "TAMPER")):
            return True
    return False


def _compute_rip(per_agent_lkw, errors):
    """
    Compute per-agent RIP (Reachability, Infection, Propagation).
    For NONE mode: all R=True, I=False, P=False is expected.
    Deviations indicate problems.
    Automatically includes checkout_orchestrator as first agent when present
    (orchestrator-driven runs via _run_checkout_via_orchestrator).
    """
    base_agents = ["productcatalog", "currency", "shipping_quote",
                   "payment", "ship_order", "email"]
    agents_ordered = (
        ["checkout_orchestrator"] + base_agents
        if "checkout_orchestrator" in per_agent_lkw
        else base_agents
    )
    rip = {}

    for idx, agent in enumerate(agents_ordered):
        lkw = per_agent_lkw.get(agent, [])
        steps = [cp["step"] for cp in lkw]

        # R — Reachability
        reached = "TASK_START" in steps and agent not in errors

        # I — Infection
        infected_at = None
        for cp in lkw:
            if _is_infected(cp):
                infected_at = cp["step"]
                break

        # P — Propagation
        # Check if infection propagated to the next agent's TASK_START data
        propagated_to = None
        if infected_at is not None and idx + 1 < len(agents_ordered):
            next_agent = agents_ordered[idx + 1]
            next_lkw   = per_agent_lkw.get(next_agent, [])
            for cp in next_lkw:
                if cp["step"] == "TASK_START" and _is_infected(cp):
                    propagated_to = next_agent
                    break

        rip[agent] = {
            "R": reached,
            "I": infected_at is not None,
            "P": propagated_to is not None,
            "infection_point": infected_at,
            "propagated_to":   propagated_to,
            "steps_reached":   steps,
            "steps_lost":      [s for s in B1_EXPECTED_STEPS.get(agent, []) if s not in steps],
        }

    return rip


# ─────────────────────────────────────────────────────────────────────────────
# Deviation from B1
# ─────────────────────────────────────────────────────────────────────────────

def _load_b1_thresholds():
    """Load per-agent B1 baselines from b2_baseline_runner.py output."""
    b1_path = SRC / "results" / "b2_equivalence_thresholds.json"
    if b1_path.exists():
        with open(b1_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def compute_deviation_from_b1(all_runs):
    """
    Compare B2 checkout per-agent LKW traces against B1 oracle using
    per-variable comparison relations (not naive sequence matching).
    Loads b1_oracle_values.json produced by b1_oracle_runner.py.
    """
    # Agent name mapping: systematic runner short names → oracle full names
    SYS_TO_ORACLE = {
        "productcatalog": "productcatalogagent",
        "currency":       "currencyagent",
        "payment":        "paymentagent",
        "email":          "emailserviceagent",
        "shipping_quote": "shippingagent_get_quote",
        "ship_order":     "shippingagent_ship_order",
    }

    try:
        sys.path.insert(0, str(SRC))
        from b1_oracle_runner import load_oracle, compare_lkw_trace_to_oracle
        oracle = load_oracle()
        oracle_available = True
    except Exception as exc:
        print(f"  [deviation] Oracle not available ({exc}); falling back to sequence check")
        oracle_available = False
        oracle = {}

    deviation = {}

    for run in all_runs:
        label = run["model_label"]
        if label not in deviation:
            deviation[label] = {}

        for sys_agent, lkw in run["per_agent_lkw"].items():
            if sys_agent not in deviation[label]:
                deviation[label][sys_agent] = {
                    "total_runs":         0,
                    "runs_with_deviation": 0,
                    "variable_deviations": {},   # field → count of deviating runs
                    "sequence_deviations": 0,    # fallback
                }
            d = deviation[label][sys_agent]
            d["total_runs"] += 1

            if oracle_available:
                oracle_agent = SYS_TO_ORACLE.get(sys_agent, sys_agent)
                results = compare_lkw_trace_to_oracle(oracle_agent, lkw, oracle)
                any_deviation = any(r["deviation"] for r in results)
                if any_deviation:
                    d["runs_with_deviation"] += 1
                for r in results:
                    fld = r["key"].split(".", 2)[-1]  # "agent.checkpoint.field" → "checkpoint.field"
                    if fld not in d["variable_deviations"]:
                        d["variable_deviations"][fld] = {"deviating_runs": 0, "severity": "none"}
                    if r["deviation"]:
                        d["variable_deviations"][fld]["deviating_runs"] += 1
                        d["variable_deviations"][fld]["severity"] = r.get("deviation_severity", "data")
            else:
                # Fallback: sequence check
                steps_got = [cp["step"] for cp in lkw]
                expected  = B1_EXPECTED_STEPS.get(sys_agent, [])
                if steps_got != expected:
                    d["sequence_deviations"] += 1
                    d["runs_with_deviation"] += 1

            d["last_steps"] = [cp["step"] for cp in lkw]

    # Compute rates
    for label in deviation:
        for agent in deviation[label]:
            d = deviation[label][agent]
            n = d["total_runs"]
            d["deviation_rate"] = round(d["runs_with_deviation"] / n, 4) if n > 0 else 0.0

    return deviation


# ─────────────────────────────────────────────────────────────────────────────
# Results aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_rip(all_runs):
    """Aggregate RIP across runs per model config."""
    agg = {}
    for run in all_runs:
        label = run["model_label"]
        if label not in agg:
            agg[label] = {}
        for agent, rip in run["rip"].items():
            if agent not in agg[label]:
                agg[label][agent] = {"R_count": 0, "I_count": 0, "P_count": 0,
                                     "total": 0, "infection_points": [],
                                     "steps_lost_all": []}
            a = agg[label][agent]
            a["total"]    += 1
            a["R_count"]  += 1 if rip["R"] else 0
            a["I_count"]  += 1 if rip["I"] else 0
            a["P_count"]  += 1 if rip["P"] else 0
            if rip["infection_point"]:
                a["infection_points"].append(rip["infection_point"])
            a["steps_lost_all"].extend(rip["steps_lost"])

    # Compute rates
    for label in agg:
        for agent in agg[label]:
            a = agg[label][agent]
            n = a["total"]
            a["R_rate"] = round(a["R_count"] / n, 4) if n > 0 else 0.0
            a["I_rate"] = round(a["I_count"] / n, 4) if n > 0 else 0.0
            a["P_rate"] = round(a["P_count"] / n, 4) if n > 0 else 0.0
    return agg


def build_model_comparison(all_runs):
    """Compare checkout success rate and per-agent reach rate across model configs."""
    comp = {}
    for run in all_runs:
        label = run["model_label"]
        if label not in comp:
            comp[label] = {"total": 0, "success": 0, "elapsed_ms_sum": 0.0,
                           "agent_reach": {}}
        c = comp[label]
        c["total"]          += 1
        c["success"]        += 1 if run["success"] else 0
        c["elapsed_ms_sum"] += run["elapsed_ms"]
        for agent, rip in run["rip"].items():
            if agent not in c["agent_reach"]:
                c["agent_reach"][agent] = {"reached": 0, "total": 0}
            c["agent_reach"][agent]["total"]   += 1
            c["agent_reach"][agent]["reached"] += 1 if rip["R"] else 0

    for label in comp:
        c     = comp[label]
        n     = c["total"]
        c["success_rate"]    = round(c["success"] / n, 4) if n > 0 else 0.0
        c["avg_elapsed_ms"]  = round(c["elapsed_ms_sum"] / n, 1) if n > 0 else 0.0
        for agent in c["agent_reach"]:
            ar = c["agent_reach"][agent]
            ar["reach_rate"] = round(ar["reached"] / ar["total"], 4) if ar["total"] > 0 else 0.0
    return comp


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate from raw directory (used when --cfg runs are done separately)
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_from_raw(fault_mode="NONE"):
    """
    Read all checkout_*.json files from RAW_DIR and rebuild all output reports.
    Use this after running multiple --cfg invocations to merge their results.
    """
    pattern  = f"checkout_*_{fault_mode}_run*.json"
    raw_files = sorted(RAW_DIR.glob(pattern))
    if not raw_files:
        print(f"No raw files matching {pattern} in {RAW_DIR}")
        return

    all_runs = []
    for p in raw_files:
        with open(p, encoding="utf-8") as f:
            all_runs.append(json.load(f))

    print(f"Loaded {len(all_runs)} raw runs from {RAW_DIR}")

    timestamp   = datetime.now(timezone.utc).isoformat()
    rip_agg     = aggregate_rip(all_runs)
    deviation   = compute_deviation_from_b1(all_runs)
    comp_report = build_model_comparison(all_runs)

    lkw_summary = {
        "generated_at":      timestamp,
        "fault_mode":        fault_mode,
        "total_runs":        len(all_runs),
        "b1_expected_steps": B1_EXPECTED_STEPS,
        "runs": [
            {k: v for k, v in r.items() if k != "checkout_lkw"}
            for r in all_runs
        ],
    }
    with open(RESULTS / f"b2_checkout_lkw_summary{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(lkw_summary, f, indent=2, default=str)
    with open(RESULTS / f"b2_rip_analysis{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": timestamp, "fault_mode": fault_mode,
                   "by_model_config": rip_agg}, f, indent=2, default=str)
    with open(RESULTS / f"b2_deviation_from_b1{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": timestamp, "fault_mode": fault_mode,
                   "by_model_config": deviation}, f, indent=2, default=str)
    with open(RESULTS / f"b2_model_comparison{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": timestamp, "fault_mode": fault_mode,
                   "model_comparison": comp_report}, f, indent=2, default=str)

    print("Rebuilt all 4 output files from raw runs.")
    for label, c in comp_report.items():
        print(f"  [{label}] success={c['success_rate']:.0%}  "
              f"avg_elapsed={c['avg_elapsed_ms']:.0f}ms  "
              f"runs={c['total']}")
        for agent, ar in c["agent_reach"].items():
            mark = "v" if ar["reach_rate"] == 1.0 else "x"
            print(f"    {mark} {agent}: {ar['reached']}/{ar['total']}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="B2 Systematic Checkout Orchestration Runner (Google Shop)"
    )
    parser.add_argument("--runs",        type=int,   default=3,
                        help="Runs per model configuration (default: 3)")
    parser.add_argument("--fault-mode",  default="NONE",
                        help="Fault mode to inject (default: NONE = baseline)")
    parser.add_argument("--skip-llm",    action="store_true",
                        help="Skip live-LLM steps (shipping) — dry-run mode")
    parser.add_argument("--cfg",         default=None,
                        help="Only run one model config label, e.g. 14b_temp0")
    parser.add_argument("--aggregate-from-raw", action="store_true",
                        help="Rebuild all output files from existing raw run files")
    args = parser.parse_args()

    if args.aggregate_from_raw:
        aggregate_from_raw(fault_mode=args.fault_mode)
        return

    N          = args.runs
    fault_mode = args.fault_mode
    skip_llm   = args.skip_llm

    model_configs = build_model_configs()
    if args.cfg:
        model_configs = [c for c in model_configs if c["label"] == args.cfg]
        if not model_configs:
            print(f"ERROR: no config with label '{args.cfg}'")
            sys.exit(1)

    print("=" * 60)
    print("B2 Systematic Runner — Google Shop Checkout Orchestration")
    print("=" * 60)
    print(f"  fault_mode   : {fault_mode}")
    print(f"  runs/config  : {N}")
    print(f"  skip_llm     : {skip_llm}")
    print(f"  configs      : {[c['label'] for c in model_configs]}")
    print()

    # Verify helpers exist (no longer written at runtime — use pre-built orchestrators)
    if not _all_helpers_exist():
        print("[ERROR] One or more co_helper files are missing.")
        print("        Expected: co_helper_{productcatalog,currency,payment,email,shipping}.py in src/")
        sys.exit(1)
    print("[helpers] all co_helper files present (using pre-built orchestrators)")
    print()

    all_runs = []

    for cfg in model_configs:
        print(f"── Model config: {cfg['label']} "
              f"(model={cfg['model']} temp={cfg['temperature']}) ──")
        for run_idx in range(1, N + 1):
            print(f"  run {run_idx}/{N} ...")
            try:
                result = run_checkout_once(
                    fault_mode=fault_mode,
                    model_cfg=cfg,
                    run_idx=run_idx,
                    skip_llm=skip_llm,
                )
                all_runs.append(result)

                # Save raw run file
                raw_file = RAW_DIR / f"checkout_{cfg['label']}_{fault_mode}_run{run_idx}.json"
                with open(raw_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, default=str)

                status = "OK" if result["success"] else "PARTIAL"
                print(f"  run {run_idx}/{N} → {status}  "
                      f"agents_ok={sum(1 for v in result['rip'].values() if v['R'])} "
                      f"elapsed={result['elapsed_ms']:.0f}ms")
            except Exception as exc:
                print(f"  run {run_idx}/{N} → FAILED: {exc}")

        print()

    if not all_runs:
        print("No runs completed — exiting.")
        sys.exit(1)

    # ── Aggregate and write outputs ────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).isoformat()

    # 1. LKW summary
    lkw_summary = {
        "generated_at":    timestamp,
        "fault_mode":      fault_mode,
        "total_runs":      len(all_runs),
        "b1_expected_steps": B1_EXPECTED_STEPS,
        "runs": [
            {k: v for k, v in r.items() if k != "checkout_lkw"}
            for r in all_runs
        ],
    }
    with open(RESULTS / f"b2_checkout_lkw_summary{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(lkw_summary, f, indent=2, default=str)
    print(f"  Written: results/b2_systematic/b2_checkout_lkw_summary{TAG_SUFFIX}.json")

    # 2. RIP analysis
    rip_agg = aggregate_rip(all_runs)
    rip_report = {
        "generated_at": timestamp,
        "fault_mode":   fault_mode,
        "description":  (
            "R=Reachability (agent reached TASK_START), "
            "I=Infection (fault marker detected in LKW), "
            "P=Propagation (infection reached next agent)"
        ),
        "by_model_config": rip_agg,
    }
    with open(RESULTS / f"b2_rip_analysis{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(rip_report, f, indent=2, default=str)
    print(f"  Written: results/b2_systematic/b2_rip_analysis{TAG_SUFFIX}.json")

    # 3. Deviation from B1
    deviation = compute_deviation_from_b1(all_runs)
    deviation_report = {
        "generated_at":     timestamp,
        "fault_mode":       fault_mode,
        "b1_source":        "results/b2_equivalence_thresholds.json",
        "by_model_config":  deviation,
    }
    with open(RESULTS / f"b2_deviation_from_b1{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(deviation_report, f, indent=2, default=str)
    print(f"  Written: results/b2_systematic/b2_deviation_from_b1{TAG_SUFFIX}.json")

    # 4. Model comparison
    comp_report = {
        "generated_at":    timestamp,
        "fault_mode":      fault_mode,
        "model_comparison": build_model_comparison(all_runs),
    }
    with open(RESULTS / f"b2_model_comparison{TAG_SUFFIX}.json", "w", encoding="utf-8") as f:
        json.dump(comp_report, f, indent=2, default=str)
    print(f"  Written: results/b2_systematic/b2_model_comparison{TAG_SUFFIX}.json")

    # ── Print summary ──────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("B2 Systematic Run Complete")
    print("=" * 60)
    for label, c in comp_report["model_comparison"].items():
        print(f"  [{label}] success={c['success_rate']:.0%}  "
              f"avg_elapsed={c['avg_elapsed_ms']:.0f}ms")
        for agent, ar in c["agent_reach"].items():
            mark = "✓" if ar["reach_rate"] == 1.0 else "✗"
            print(f"    {mark} {agent}: reached {ar['reached']}/{ar['total']}")
    print()


if __name__ == "__main__":
    main()
