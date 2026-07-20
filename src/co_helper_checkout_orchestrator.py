"""
Checkout Orchestrator Agent Helper
====================================
Python mirror of checkout-agent/agent/agent.go — runs an Ollama ReAct loop
with tool-calling so the LLM decides which tools to call and in what order,
rather than Python hard-coding the sequence.

This implements the orchestrator agent the professor identified as missing from
the B2/B3 pipeline.  It maps to checkout-agent/tools/tools.go:

  get_product             → co_helper_productcatalog.py
  convert_currency        → co_helper_currency.py
  quote_shipping          → co_helper_shipping.py  (action=get_quote)
  charge_card             → co_helper_payment.py
  ship_order              → co_helper_shipping.py  (action=ship_order)
  send_order_confirmation → co_helper_email.py

LKW Checkpoints emitted by this agent:
  TASK_START          — checkout request received (fault_mode, model, order_id)
  PRODUCT_FETCHED     — get_product tool call completed
  CURRENCY_CONVERTED  — convert_currency tool call completed
  SHIPPING_QUOTED     — quote_shipping tool call completed
  PAYMENT_CHARGED     — charge_card tool call completed
  ORDER_SHIPPED       — ship_order tool call completed
  CONFIRMATION_SENT   — send_order_confirmation tool call completed
  FINAL_ANSWER        — LLM produced final order summary JSON

Fault injection on the orchestrator itself:
  FM_3_1  — premature termination: max ReAct iterations capped to 3, causing
             the orchestrator to stop before all 6 tools are called.
  Others  — passed through unchanged to sub-agent helpers.

Output JSON (printed to stdout, consumed by _run_helper in b2_systematic_runner.py):
  {
    "lkw":            [...],   # orchestrator checkpoints
    "per_agent_lkw":  {...},   # sub-agent LKW traces keyed by agent name
    "order_summary":  {...},   # final JSON from LLM (order_id, tracking_id, ...)
    "fault_mode":     str,
    "model":          str,
    "temperature":    float,
    "iterations":     int,
    "status":         "ok" | "max_iterations" | "error",
  }

Usage (called as subprocess by b2_systematic_runner.py):
  python co_helper_checkout_orchestrator.py '<JSON payload>'
"""

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ── HTTP helper (requests preferred, urllib fallback) ─────────────────────────
try:
    import requests as _requests

    def _http_post(url: str, body: dict, timeout: int = 120) -> dict:
        r = _requests.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

except ImportError:
    import urllib.request

    def _http_post(url: str, body: dict, timeout: int = 120) -> dict:  # type: ignore[misc]
        data = json.dumps(body).encode()
        req  = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())


SRC = Path(__file__).parent

# ── Tool definitions (mirror checkout-agent/tools/tools.go) ──────────────────

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Fetches product details (name, price in USD) for a product ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product unique identifier (e.g. PROD-001)."
                    }
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": (
                "Converts an amount from one currency to another. "
                "Returns converted_units and converted_nanos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount_units":  {"type": "integer", "description": "Whole-unit part of the amount."},
                    "amount_nanos":  {"type": "integer", "description": "Fractional part in nanoseconds (1e-9)."},
                    "from_currency": {"type": "string",  "description": "Source currency code, e.g. USD."},
                    "to_currency":   {"type": "string",  "description": "Target currency code, e.g. USD."},
                },
                "required": ["amount_units", "from_currency", "to_currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quote_shipping",
            "description": "Returns a shipping cost estimate (cost_usd) for delivering items to an address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Delivery address: {street_address, city, state, country, zip_code}.",
                    },
                    "items": {
                        "type": "array",
                        "description": "Items to ship: [{product_id, quantity, weight_kg}].",
                    },
                },
                "required": ["address", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "charge_card",
            "description": "Charges a credit card and returns a transaction_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "credit_card_number":           {"type": "string",  "description": "16-digit card number."},
                    "credit_card_cvv":              {"type": "integer", "description": "3-digit CVV."},
                    "credit_card_expiration_year":  {"type": "integer", "description": "Expiry year, e.g. 2030."},
                    "credit_card_expiration_month": {"type": "integer", "description": "Expiry month 1–12."},
                    "currency_code":                {"type": "string",  "description": "Currency code, e.g. USD."},
                    "units":                        {"type": "integer", "description": "Whole-unit charge amount."},
                    "nanos":                        {"type": "integer", "description": "Fractional charge (nanoseconds)."},
                },
                "required": ["credit_card_number", "currency_code", "units"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ship_order",
            "description": "Ships the order and returns a tracking_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "description": "Delivery address."},
                    "items":   {"type": "array",  "description": "Items to ship."},
                },
                "required": ["address", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_order_confirmation",
            "description": "Sends an order confirmation email to the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email":        {"type": "string", "description": "Customer email address."},
                    "order_id":     {"type": "string", "description": "Order identifier."},
                    "tracking_id":  {"type": "string", "description": "Shipment tracking ID."},
                    "total_amount": {"type": "number", "description": "Total amount charged (float USD)."},
                },
                "required": ["email", "order_id"],
            },
        },
    },
]

# ── LKW checkpoint step names for each tool ───────────────────────────────────
_TOOL_TO_STEP = {
    "get_product":             "PRODUCT_FETCHED",
    "convert_currency":        "CURRENCY_CONVERTED",
    "quote_shipping":          "SHIPPING_QUOTED",
    "charge_card":             "PAYMENT_CHARGED",
    "ship_order":              "ORDER_SHIPPED",
    "send_order_confirmation": "CONFIRMATION_SENT",
}

# ── Sub-agent subprocess runner ───────────────────────────────────────────────

def _run_sub_helper(helper_name: str, payload: dict, timeout: int = 300) -> dict:
    """Run a co_helper subprocess and return its parsed JSON output."""
    helper_path = SRC / helper_name
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, str(helper_path), json.dumps(payload)],
        cwd=str(SRC),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )

    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr[-400:] if proc.stderr else "(no stderr)"
        raise RuntimeError(f"{helper_name} produced no output. stderr={stderr}")

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)

    raise ValueError(f"{helper_name} output not parseable: {stdout[:200]}")


# ── Tool dispatcher functions ─────────────────────────────────────────────────

def _dispatch_get_product(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "fault_mode": fm,
        "query":      f"get product {args.get('product_id', 'PROD-001')}",
        "model":      m,
        "ollama_url": url,
    }
    result = _run_sub_helper("co_helper_productcatalog.py", payload)
    pal["productcatalog"] = result.get("lkw", [])
    products = result.get("products", [])
    product  = products[0] if products else {
        "id":    "PROD-001",
        "name":  "Sunglasses",
        "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
    }
    return json.dumps(product)


def _dispatch_convert_currency(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "fault_mode":    fm,
        "query":         f"convert {args.get('amount_units', 19)} to {args.get('to_currency', 'USD')}",
        "from_currency": args.get("from_currency", "USD"),
        "units":         args.get("amount_units", 19),
        "nanos":         args.get("amount_nanos", 990000000),
        "to_currency":   args.get("to_currency", "USD"),
        "model":         m,
        "ollama_url":    url,
    }
    result    = _run_sub_helper("co_helper_currency.py", payload)
    pal["currency"] = result.get("lkw", [])
    converted = result.get(
        "converted",
        {"currency_code": "USD", "units": 19, "nanos": 990000000},
    )
    return json.dumps(converted)


def _dispatch_quote_shipping(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "action":      "get_quote",
        "fault_mode":  fm,
        "model":       m,
        "temperature": t,
        "ollama_url":  url,
        "address":     args.get("address", base.get("address", {})),
        "items":       args.get("items",   base.get("items", [])),
    }
    result = _run_sub_helper("co_helper_shipping.py", payload, timeout=300)
    pal["shipping_quote"] = result.get("lkw", [])
    return json.dumps({"cost_usd": result.get("cost_usd", 7.50)})


def _dispatch_charge_card(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "fault_mode":                   fm,
        "query":                        "charge card for checkout order",
        "model":                        m,
        "ollama_url":                   url,
        "credit_card_number":           args.get("credit_card_number",           base.get("credit_card_number")),
        "credit_card_cvv":              args.get("credit_card_cvv",              base.get("credit_card_cvv")),
        "credit_card_expiration_year":  args.get("credit_card_expiration_year",  base.get("credit_card_expiration_year")),
        "credit_card_expiration_month": args.get("credit_card_expiration_month", base.get("credit_card_expiration_month")),
        "currency_code":                args.get("currency_code", "USD"),
        "units":                        args.get("units", 26),
        "nanos":                        args.get("nanos", 990000000),
    }
    result = _run_sub_helper("co_helper_payment.py", payload)
    pal["payment"] = result.get("lkw", [])
    return json.dumps({"transaction_id": result.get("transaction_id", "orch-tx-fallback")})


def _dispatch_ship_order(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "action":      "ship_order",
        "fault_mode":  fm,
        "model":       m,
        "temperature": t,
        "ollama_url":  url,
        "address":     args.get("address", base.get("address", {})),
        "items":       args.get("items",   base.get("items", [])),
    }
    result = _run_sub_helper("co_helper_shipping.py", payload, timeout=300)
    pal["ship_order"] = result.get("lkw", [])
    return json.dumps({"tracking_id": result.get("tracking_id", "orch-track-fallback")})


def _dispatch_send_confirmation(
    args: dict, base: dict, pal: dict, m: str, url: str, t: float, fm: str
) -> str:
    payload = {
        "fault_mode":       fm,
        "email":            args.get("email",        base.get("email",    "customer@example.com")),
        "order_id":         args.get("order_id",     base.get("order_id", "ORCH-001")),
        "user_name":        base.get("user_name",    "Test Customer"),
        "currency_code":    "USD",
        "total":            args.get("total_amount", base.get("total", 27.49)),
        "items":            base.get("items_desc",   [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}]),
        "shipping_address": base.get("address",      {}),
        "model":            m,
        "ollama_url":       url,
        "temperature":      t,
    }
    result = _run_sub_helper("co_helper_email.py", payload)
    pal["email"] = result.get("lkw", [])
    return json.dumps({"status": result.get("status", "sent")})


_DISPATCH = {
    "get_product":             _dispatch_get_product,
    "convert_currency":        _dispatch_convert_currency,
    "quote_shipping":          _dispatch_quote_shipping,
    "charge_card":             _dispatch_charge_card,
    "ship_order":              _dispatch_ship_order,
    "send_order_confirmation": _dispatch_send_confirmation,
}

# ── LKW checkpoint helper ─────────────────────────────────────────────────────

def _ckpt(step: str, data: dict) -> dict:
    return {
        "step":      step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data":      data,
    }


# ── Main orchestrator (ReAct loop) ────────────────────────────────────────────

def run_orchestrator(payload: dict) -> None:
    """
    Run the LLM ReAct loop, dispatching each tool call to the appropriate
    co_helper subprocess, and emit JSON to stdout.
    """
    fault_mode = payload.get("fault_mode",  "NONE")
    model      = payload.get("model",       "qwen2.5-coder:14b")
    temp       = float(payload.get("temperature", 0.0))
    ollama_url = payload.get("ollama_url",  "http://localhost:11434")
    order_id   = payload.get("order_id",   "ORCH-ORDER-001")

    lkw:           list = []
    per_agent_lkw: dict = {}

    # ── TASK_START checkpoint ─────────────────────────────────────────────────
    lkw.append(_ckpt("TASK_START", {
        "fault_mode":  fault_mode,
        "model":       model,
        "temperature": temp,
        "order_id":    order_id,
        "items":       payload.get("items",   []),
        "address":     payload.get("address", {}),
    }))

    # FM_3_1 = premature termination → cap to 3 ReAct iterations
    max_iters = 3 if fault_mode == "FM_3_1" else 15

    system_prompt = (
        "You are a checkout orchestrator agent for an e-commerce platform.\n"
        "Complete the checkout by calling the tools in this order:\n"
        "  1. get_product — fetch product details\n"
        "  2. convert_currency — convert the product price to the order currency\n"
        "  3. quote_shipping — get a shipping cost estimate\n"
        "  4. charge_card — charge payment\n"
        "  5. ship_order — ship the items\n"
        "  6. send_order_confirmation — send confirmation email\n\n"
        "After ALL tools are called, output ONLY a JSON object:\n"
        '{"order_id": "...", "shipping_tracking_id": "...", "total_charged": 0.0}\n'
        "No extra text, no markdown."
    )
    user_msg = (
        f"Complete checkout for:\n"
        f"  order_id      = {order_id}\n"
        f"  email         = {payload.get('email', 'customer@example.com')}\n"
        f"  items         = {json.dumps(payload.get('items', []))}\n"
        f"  address       = {json.dumps(payload.get('address', {}))}\n"
        f"  card_last4    = {str(payload.get('credit_card_number', '4111111111111111'))[-4:]}\n"
        f"  currency      = USD"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_msg},
    ]

    final_content: str | None = None
    iterations:    int        = 0
    status:        str        = "ok"

    try:
        for i in range(max_iters):
            iterations = i + 1

            resp_data = _http_post(
                f"{ollama_url}/api/chat",
                {
                    "model":    model,
                    "messages": messages,
                    "tools":    TOOL_DEFS,
                    "stream":   False,
                    "options":  {"temperature": temp},
                },
                timeout=120,
            )

            reply      = resp_data.get("message", {})
            tool_calls = reply.get("tool_calls") or []
            messages.append(reply)

            if not tool_calls:
                # LLM produced its final answer — no more tool calls
                final_content = reply.get("content", "")
                lkw.append(_ckpt("FINAL_ANSWER", {
                    "content":    (final_content[:500] if final_content else None),
                    "iterations": iterations,
                }))
                break

            for tc in tool_calls:
                fn        = tc.get("function", {})
                tool_name = fn.get("name", "")
                raw_args  = fn.get("arguments", {})

                # Ollama may return arguments as a JSON string or as a dict
                if isinstance(raw_args, str):
                    try:
                        tool_args = json.loads(raw_args)
                    except Exception:
                        tool_args = {}
                else:
                    tool_args = raw_args or {}

                dispatcher = _DISPATCH.get(tool_name)
                if dispatcher is None:
                    tool_result = json.dumps({"error": f"unknown tool: {tool_name}"})
                else:
                    tool_result = dispatcher(
                        tool_args, payload, per_agent_lkw, model, ollama_url, temp, fault_mode
                    )

                # Emit a LKW checkpoint for each completed tool call
                step_name = _TOOL_TO_STEP.get(tool_name, f"TOOL_{tool_name.upper()}_DONE")
                try:
                    result_data = (
                        json.loads(tool_result)
                        if isinstance(tool_result, str)
                        else tool_result
                    )
                except Exception:
                    result_data = {"raw": str(tool_result)[:200]}

                lkw.append(_ckpt(step_name, {
                    "tool":   tool_name,
                    "args":   tool_args,
                    "result": result_data,
                }))

                messages.append({
                    "role":    "tool",
                    "content": (
                        tool_result
                        if isinstance(tool_result, str)
                        else json.dumps(tool_result)
                    ),
                })

        else:
            # Exhausted max_iters without a text final answer
            status = "max_iterations"
            lkw.append(_ckpt("FINAL_ANSWER", {
                "content":    None,
                "iterations": iterations,
                "note":       "hit max_iterations without LLM final answer",
            }))

    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        status = "error"
        lkw.append(_ckpt("FINAL_ANSWER", {
            "content": None,
            "error":   str(exc),
        }))

    # Try to extract structured order summary from the LLM final answer
    order_summary: dict = {}
    if final_content:
        try:
            start = final_content.find("{")
            if start != -1:
                order_summary = json.loads(final_content[start:])
        except Exception:
            order_summary = {"raw": final_content[:200]}

    print(json.dumps({
        "lkw":            lkw,
        "per_agent_lkw":  per_agent_lkw,
        "order_summary":  order_summary,
        "fault_mode":     fault_mode,
        "model":          model,
        "temperature":    temp,
        "iterations":     iterations,
        "status":         status,
    }))


if __name__ == "__main__":
    _payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    run_orchestrator(_payload)
