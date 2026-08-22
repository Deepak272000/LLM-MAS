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


def run_model(base_url, model, max_turns, num_ctx, temperature=0.7):
    out = {
        "model": model, "tool_calling": False, "turns": 0,
        "tools_called": [], "prompt_evals": [], "final_answer": False,
        "max_ctx": advertised_context(base_url, model),
        "truncation": False, "cache_reuse": False, "errors": [],
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_MSG},
    ]
    options = {"temperature": temperature}
    if num_ctx:
        options["num_ctx"] = num_ctx
    effective = num_ctx or 4096
    out["effective_ctx"] = effective

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
            # A drop in prompt_eval_count is NOT evidence of eviction by itself:
            # Ollama counts only newly-evaluated tokens when a prompt prefix is
            # served from its KV cache, which makes later turns look smaller.
            # Treat a drop as eviction only when we were already near the window,
            # since you cannot evict from a window that is mostly empty.
            if out["prompt_evals"] and pe < max(out["prompt_evals"]):
                if max(out["prompt_evals"]) >= 0.80 * effective:
                    out["truncation"] = True
                else:
                    out["cache_reuse"] = True
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
    if out["prompt_evals"] and max(out["prompt_evals"]) >= 0.95 * effective:
        out["truncation"] = True
    return out


def distinct_tools(r):
    return {t for t in r["tools_called"] if t in EXPECTED_TOOLS}


def aggregate_verdict(reps):
    """Verdict over n samples. Temperature 0.7 is stochastic, so a single draw
    cannot separate a model that *cannot* do something from one that merely
    did not on that draw."""
    n = len(reps)
    tc = sum(1 for r in reps if r["tool_calling"])
    complete = sum(1 for r in reps if len(distinct_tools(r)) == len(EXPECTED_TOOLS))
    trunc = sum(1 for r in reps if r["truncation"])

    if tc == 0:
        return "FAIL", (f"never emitted tool_calls in {n}/{n} reps - "
                        f"would yield 100% INCONCLUSIVE")
    if tc < n:
        return "WARN", f"tool calling flaky: only {tc}/{n} reps emitted tool_calls"
    if trunc:
        return "WARN", f"genuine context overflow in {trunc}/{n} reps"
    if complete == 0:
        missing = sorted(set(EXPECTED_TOOLS) - set().union(*(distinct_tools(r) for r in reps)))
        return "WARN", f"never completed 6/6 in {n} reps; never called {missing}"
    if complete < n:
        return "WARN", f"completed 6/6 in only {complete}/{n} reps (unstable)"
    return "PASS", f"all 6 tools in {n}/{n} reps"


def main():
    ap = argparse.ArgumentParser(description="Pre-flight model smoke test")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--max-turns", type=int, default=15,
                    help="Match the orchestrator's max_iters (default: 15)")
    ap.add_argument("--num-ctx", type=int, default=None,
                    help="Override context window; omit for production parity")
    ap.add_argument("--reps", type=int, default=3,
                    help="Samples per model; temp 0.7 is stochastic (default: 3)")
    ap.add_argument("--temperature", type=float, default=0.7,
                    help="Sampling temperature (default: 0.7, matches B3)")
    args = ap.parse_args()

    base = args.ollama_url.rstrip("/")
    print("=" * 78)
    print("MODEL SMOKE TEST -- tool calling, multi-turn, context overflow")
    print(f"  ollama    : {base}")
    print(f"  max turns : {args.max_turns}")
    print(f"  num_ctx   : {args.num_ctx or 'unset (production parity)'}")
    print(f"  reps      : {args.reps} per model @ temperature {args.temperature}")
    print(f"  tools     : {len(TOOL_DEFS)} imported from the live orchestrator")
    print("=" * 78)

    results = []
    for model in args.models:
        print(f"\n--- {model} ---", flush=True)
        reps = []
        for i in range(1, args.reps + 1):
            r = run_model(base, model, args.max_turns, args.num_ctx,
                          args.temperature)
            reps.append(r)
            peak = max(r["prompt_evals"]) if r["prompt_evals"] else 0
            flags = []
            if r["truncation"]:
                flags.append("TRUNCATED")
            if r["cache_reuse"]:
                flags.append("cache-reuse")
            flags += [f"ERR {e[:60]}" for e in r["errors"]]
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  rep {i}: {len(distinct_tools(r))}/6 tools, "
                  f"{r['turns']} turns, peak {peak}{suffix}", flush=True)
            print(f"         order: {r['tools_called'] or 'NONE'}", flush=True)

        v, why = aggregate_verdict(reps)
        agg = {
            "model": model,
            "advertised_ctx": reps[0]["max_ctx"],
            "effective_ctx": reps[0]["effective_ctx"],
            "best_tools": max(len(distinct_tools(r)) for r in reps),
            "peak_ctx": max((max(r["prompt_evals"]) if r["prompt_evals"] else 0)
                            for r in reps),
            "verdict": v, "why": why, "reps": reps,
        }
        results.append(agg)
        print(f"  advertised ctx : {agg['advertised_ctx']} "
              f"(effective {agg['effective_ctx']})")
        print(f"  => {v}: {why}")

    print("\n" + "=" * 78)
    print(f"{'MODEL':<24}{'VERDICT':<9}{'BEST':<8}{'PEAK CTX':<10}NOTE")
    print("-" * 78)
    for r in results:
        print(f"{r['model']:<24}{r['verdict']:<9}{r['best_tools']}/6     "
              f"{r['peak_ctx']:<10}{r['why'][:30]}")
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
