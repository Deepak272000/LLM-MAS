#!/usr/bin/env python3
"""
Pre-flight smoke test for compact models before a multi-model B3 campaign.

Runs three checks per model, in increasing severity:

  1. TOOL CALLING   -- does the model emit `tool_calls` at all? A model without
                       Ollama tool support returns a plain message, the
                       orchestrator loop breaks on iteration 1, and every B3 run
                       becomes INCONCLUSIVE. Hard gate.

  2. MULTI-TURN     -- replays a full checkout-shaped exchange using the real
                       TOOL_DEFS imported from co_helper_checkout_orchestrator,
                       appending {"role": "tool"} results exactly as the
                       orchestrator does, for up to --max-turns iterations.

  3. CONTEXT        -- tracks `prompt_eval_count` per turn against the model's
                       advertised context length. Detects the silent failure
                       mode that matters for small-context models such as
                       granite3-dense:2b (4K): once the accumulated tool results
                       exceed the window, Ollama drops the oldest messages, the
                       system prompt goes with them, and the model starts
                       repeating or abandoning tools -- while still returning
                       HTTP 200.

Production parity: by default no `num_ctx` is sent, because the orchestrator
does not send one either. Use --num-ctx to see whether an explicit window fixes
a model that fails check 3.

Usage:
    python model_smoke_test.py --models llama3.2:3b granite3-dense:2b \\
                                        qwen2.5:1.5b smollm2:1.7b
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

try:
    from co_helper_checkout_orchestrator import TOOL_DEFS
except Exception as exc:  # pragma: no cover - import guard
    print(f"FATAL: cannot import TOOL_DEFS from co_helper_checkout_orchestrator: {exc}")
    sys.exit(2)

EXPECTED_TOOLS = [
    "get_product", "convert_currency", "quote_shipping",
    "charge_card", "ship_order", "send_order_confirmation",
]

SYSTEM_PROMPT = (
    "You are a checkout orchestrator agent for an e-commerce platform.\n"
    "Complete the checkout by calling the tools in this order:\n"
    "  1. get_product - fetch product details\n"
    "  2. convert_currency - convert the product price to the order currency\n"
    "  3. quote_shipping - get a shipping cost estimate\n"
    "  4. charge_card - charge payment\n"
    "  5. ship_order - ship the items\n"
    "  6. send_order_confirmation - send confirmation email\n\n"
    "After ALL tools are called, output ONLY a JSON object:\n"
    '{"order_id": "...", "shipping_tracking_id": "...", "total_charged": 0.0}\n'
    "No extra text, no markdown."
)

USER_MSG = (
    "Complete checkout for:\n"
    "  order_id      = CO-SMOKE1\n"
    "  email         = customer@example.com\n"
    '  items         = [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}]\n'
    '  address       = {"street_address": "123 Main St", "city": "Montreal", '
    '"state": "QC", "country": "Canada", "zip_code": "H3G1M8"}\n'
    "  card_last4    = 1111\n"
    "  currency      = USD"
)

# Results sized like the real dispatcher outputs so context growth is realistic.
MOCK_RESULTS = {
    "get_product": {
        "id": "PROD-001", "name": "Sunglasses",
        "description": "Add a modern touch to your outfits with these sleek aviator sunglasses.",
        "picture": "/static/img/products/sunglasses.jpg",
        "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
        "categories": ["accessories"],
    },
    "convert_currency": {
        "currency_code": "USD", "units": 19, "nanos": 990000000,
        "from_currency": "USD", "to_currency": "USD", "rate_applied": 1.0,
    },
    "quote_shipping": {
        "action": "get_quote",
        "cost_usd": {"currency_code": "USD", "units": 8, "nanos": 990000000},
        "carrier": "StandardPost", "estimated_days": 5,
    },
    "charge_card": {
        "transaction_id": "b7f3c2a1-4d5e-4f60-9a8b-1c2d3e4f5a6b",
        "amount_charged": {"currency_code": "USD", "units": 48, "nanos": 970000000},
        "card_last4": "1111", "status": "approved",
    },
    "ship_order": {
        "action": "ship_order", "tracking_id": "TRK-90210-CO-SMOKE1",
        "carrier": "StandardPost", "status": "shipped",
    },
    "send_order_confirmation": {
        "status": "sent", "recipient": "customer@example.com",
        "subject": "Your order CO-SMOKE1 is confirmed",
        "body_preview": "Thank you for your order. Your items will arrive in about 5 days.",
    },
}


def post(url, payload, timeout=180):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def advertised_context(base_url, model):
    """Max context length the model reports, or None."""
    try:
        info = post(f"{base_url}/api/show", {"model": model}, timeout=60)
    except Exception:
        return None
    for key, val in (info.get("model_info") or {}).items():
        if key.endswith(".context_length") and isinstance(val, int):
            return val
    return None


def run_model(base_url, model, max_turns, num_ctx):
    out = {
        "model": model, "tool_calling": False, "turns": 0,
        "tools_called": [], "prompt_evals": [], "final_answer": False,
        "max_ctx": advertised_context(base_url, model),
        "truncation": False, "errors": [],
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_MSG},
    ]
    options = {"temperature": 0.7}
    if num_ctx:
        options["num_ctx"] = num_ctx

    for turn in range(1, max_turns + 1):
        out["turns"] = turn
        try:
            data = post(f"{base_url}/api/chat", {
                "model": model, "messages": messages,
                "tools": TOOL_DEFS, "stream": False, "options": options,
            })
        except urllib.error.HTTPError as exc:
            out["errors"].append(f"turn {turn}: HTTP {exc.code} {exc.read()[:200]!r}")
            break
        except Exception as exc:
            out["errors"].append(f"turn {turn}: {exc}")
            break

        pe = data.get("prompt_eval_count")
        if isinstance(pe, int):
            # A drop means Ollama evicted earlier messages: the window overflowed.
            if out["prompt_evals"] and pe < max(out["prompt_evals"]):
                out["truncation"] = True
            out["prompt_evals"].append(pe)

        reply = data.get("message", {}) or {}
        tool_calls = reply.get("tool_calls") or []
        messages.append(reply)

        if not tool_calls:
            out["final_answer"] = True
            break

        out["tool_calling"] = True
        for tc in tool_calls:
            name = (tc.get("function") or {}).get("name", "")
            out["tools_called"].append(name)
            result = MOCK_RESULTS.get(name, {"status": "ok", "tool": name})
            messages.append({"role": "tool", "content": json.dumps(result)})

    # Plateau against the effective window is the other overflow signature.
    effective = num_ctx or 4096
    if out["prompt_evals"] and max(out["prompt_evals"]) >= 0.95 * effective:
        out["truncation"] = True
    out["effective_ctx"] = effective
    return out


def verdict(r):
    if not r["tool_calling"]:
        return "FAIL", "never emitted tool_calls - would yield 100% INCONCLUSIVE"
    distinct = [t for t in EXPECTED_TOOLS if t in r["tools_called"]]
    if r["truncation"]:
        return "WARN", (f"context overflow: prompt_eval peaked at "
                        f"{max(r['prompt_evals'])} vs effective window "
                        f"{r['effective_ctx']}")
    if len(distinct) < len(EXPECTED_TOOLS):
        missing = [t for t in EXPECTED_TOOLS if t not in r["tools_called"]]
        return "WARN", f"only {len(distinct)}/6 tools called; missing {missing}"
    return "PASS", f"all 6 tools called in {r['turns']} turns"


def main():
    ap = argparse.ArgumentParser(description="Pre-flight model smoke test")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--max-turns", type=int, default=15,
                    help="Match the orchestrator's max_iters (default: 15)")
    ap.add_argument("--num-ctx", type=int, default=None,
                    help="Override context window; omit for production parity")
    args = ap.parse_args()

    base = args.ollama_url.rstrip("/")
    print("=" * 78)
    print("MODEL SMOKE TEST -- tool calling, multi-turn, context overflow")
    print(f"  ollama    : {base}")
    print(f"  max turns : {args.max_turns}")
    print(f"  num_ctx   : {args.num_ctx or 'unset (production parity)'}")
    print(f"  tools     : {len(TOOL_DEFS)} imported from the live orchestrator")
    print("=" * 78)

    results = []
    for model in args.models:
        print(f"\n--- {model} ---", flush=True)
        r = run_model(base, model, args.max_turns, args.num_ctx)
        v, why = verdict(r)
        r["verdict"], r["why"] = v, why
        results.append(r)

        print(f"  advertised ctx : {r['max_ctx']}")
        print(f"  turns          : {r['turns']}")
        print(f"  tools called   : {r['tools_called'] or 'NONE'}")
        print(f"  prompt_eval    : {r['prompt_evals']}")
        for e in r["errors"]:
            print(f"  ERROR          : {e}")
        print(f"  => {v}: {why}")

    print("\n" + "=" * 78)
    print(f"{'MODEL':<24}{'VERDICT':<9}{'TURNS':<7}{'TOOLS':<7}{'PEAK CTX':<10}NOTE")
    print("-" * 78)
    for r in results:
        peak = max(r["prompt_evals"]) if r["prompt_evals"] else 0
        distinct = len({t for t in r["tools_called"] if t in EXPECTED_TOOLS})
        print(f"{r['model']:<24}{r['verdict']:<9}{r['turns']:<7}"
              f"{distinct}/6    {peak:<10}{r['why'][:28]}")
    print("=" * 78)

    out_path = SRC / "results" / "model_smoke_test.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Written: {out_path}")

    if any(r["verdict"] == "FAIL" for r in results):
        print("\nAt least one model FAILED. Do not launch the campaign with it.")
        sys.exit(1)
    if any(r["verdict"] == "WARN" for r in results):
        print("\nWarnings present. Re-run with --num-ctx 8192 to test whether an "
              "explicit window resolves them before launching.")
        sys.exit(3)


if __name__ == "__main__":
    main()
