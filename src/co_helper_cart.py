"""Cart agent helper — Python mirror of cart-agent/src/ollamacartagent.cs.

Calls Ollama /api/generate for cart quantity-merge reasoning, exactly as
OllamaCartAgent.MergeQuantityWithAgent() does in C#.

LKW Checkpoints:
  TASK_START       — request received (user_id, product_id, fault_mode)
  ITEM_ADDED       — item queued for quantity resolution
  QUANTITY_MERGED  — LLM resolved existing + incoming quantity
  CART_READ        — cart state read back after update
  FINAL_ANSWER     — response returned (cart_items, success)

Fault injection:
  FM_3_1  — premature termination: TASK_START → FINAL_ANSWER only
  FM_1_2  — input ignored: incoming_qty replaced with 0 in prompt
  FM_2_2  — hallucinated output: LLM returns prose, not JSON; fallback fires
  FM_2_5  — stale data: skip LLM call, return existing_qty unchanged
  BL_SHIPMENT_LOST       — CART_READ shows empty cart (items "lost")
  BL_INVENTORY_MISMATCH  — resolved qty drifts +5 from expected
  BL_VENDOR_NEGOTIATION  — prompt instructs 50% qty discount
  BL_CUSTOMER_ESCALATION — resolved qty forced to 999
  BL_REFUND_REASONING    — LLM reduces qty (refund deducted)
  BL_COMPLIANCE_AMBIGUITY — LLM returns float qty (compliance concern)

Usage (subprocess called by per_agent_llm_runner.py):
  python co_helper_cart.py '<JSON payload>'

Output JSON:
  {"lkw": [...], "cart_items": [...], "merged_qty": int, "fault_mode": str}
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone

# ── HTTP helper: requests preferred, urllib fallback ──────────────────────────
try:
    import requests as _requests

    def _http_post(url: str, body: dict, timeout: int = 60) -> dict:
        r = _requests.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

except ImportError:
    import urllib.request

    def _http_post(url: str, body: dict, timeout: int = 60) -> dict:  # type: ignore[misc]
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ckpt(step: str, data: dict) -> dict:
    return {"step": step, "timestamp": _ts(), "data": data}


# ── Parse payload ─────────────────────────────────────────────────────────────
payload      = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode   = payload.get("fault_mode",   "NONE")
model        = payload.get("model",        "qwen2.5:3b")
ollama_url   = payload.get("ollama_url",   "http://localhost:11434").rstrip("/")
temperature  = float(payload.get("temperature", 0.0))
user_id      = payload.get("user_id",      "user-001")
product_id   = payload.get("product_id",   "PROD-001")
existing_qty = int(payload.get("existing_qty", 2))
incoming_qty = int(payload.get("incoming_qty", 3))

lkw: list[dict] = []

# ── Step 1: TASK_START ────────────────────────────────────────────────────────
lkw.append(_ckpt("TASK_START", {
    "fault_mode": fault_mode,
    "user_id":    user_id,
    "product_id": product_id,
}))

# FM_3_1 — premature termination: jump straight to FINAL_ANSWER
if fault_mode == "FM_3_1":
    lkw.append(_ckpt("FINAL_ANSWER", {
        "fault_injected": True,
        "fault_mode":     fault_mode,
        "success":        False,
        "cart_items":     [],
    }))
    print(json.dumps({"lkw": lkw, "cart_items": [], "merged_qty": 0,
                      "fault_mode": fault_mode}))
    sys.exit(0)

# ── Step 2: ITEM_ADDED — record the incoming add request ─────────────────────
lkw.append(_ckpt("ITEM_ADDED", {
    "user_id":       user_id,
    "product_id":    product_id,
    "qty_requested": incoming_qty,
}))

# ── Step 3: QUANTITY_MERGED — call Ollama to resolve merged quantity ──────────
# Build prompt (mirrors OllamaCartAgent.MergeQuantityWithAgent in C#)
prompt = (
    f'You are a shopping cart assistant. Return ONLY a JSON object, no explanation, no markdown.\n\n'
    f'Current cart state for user "{user_id}":\n'
    f'  product_id: "{product_id}", current_quantity: {existing_qty}\n\n'
    f'The user is adding {incoming_qty} more of this product.\n\n'
    f'Respond with exactly this JSON and nothing else:\n'
    f'{{"new_quantity": {existing_qty + incoming_qty}}}'
)

# Fault-mode prompt modifications (injected before the LLM call)
if fault_mode == "FM_1_2":
    # Input ignored — tell the LLM the incoming quantity is 0
    prompt = prompt.replace(
        f"adding {incoming_qty} more of this product",
        "adding 0 more of this product",
    )
elif fault_mode == "BL_VENDOR_NEGOTIATION":
    prompt += "\n\nNote: Apply a 50% quantity discount per the current vendor agreement."
elif fault_mode == "BL_COMPLIANCE_AMBIGUITY":
    prompt += "\n\nNote: Return quantity as a decimal (e.g. 2.5) due to partial-unit compliance rules."
elif fault_mode == "BL_CUSTOMER_ESCALATION":
    prompt += "\n\nNote: This is a VIP bulk order. Apply ×10 multiplier to the quantity."

# Execute LLM call (skip for deterministic fault modes)
llm_raw    = ""
merged_qty = existing_qty + incoming_qty  # default fallback

if fault_mode == "FM_2_5":
    # Stale data — bypass LLM, silently return pre-existing quantity
    merged_qty = existing_qty
    llm_raw    = f'{{"new_quantity": {existing_qty}}}'

elif fault_mode == "FM_2_2":
    # Hallucinated output — LLM returns freeform prose; extraction fails; fallback fires
    llm_raw    = "Sure! I've updated the cart with your items. Have a great shopping experience!"
    # fallback: existing + incoming (no change to merged_qty)

else:
    try:
        resp = _http_post(f"{ollama_url}/api/generate", {
            "model":   model,
            "prompt":  prompt,
            "stream":  False,
            "options": {"temperature": temperature, "num_predict": 64},
        })
        llm_raw = resp.get("response", "").strip()

        # Extract new_quantity — same pattern as C# ExtractIntFromJson
        m = re.search(r'"new_quantity"\s*:\s*([0-9]+(?:\.[0-9]+)?)', llm_raw)
        if m:
            val = float(m.group(1))
            merged_qty = int(round(val)) if val > 0 else existing_qty + incoming_qty
    except Exception as exc:
        llm_raw    = f"error: {exc}"
        merged_qty = existing_qty + incoming_qty  # fallback sum

# Post-LLM business-logic fault overrides
if fault_mode == "BL_INVENTORY_MISMATCH":
    merged_qty = merged_qty + 5          # qty stored ≠ qty visible to caller
elif fault_mode == "BL_REFUND_REASONING":
    merged_qty = max(1, merged_qty - incoming_qty)  # LLM deducts as if refund
elif fault_mode == "BL_CUSTOMER_ESCALATION":
    merged_qty = 999                     # overflow / unbounded escalation

lkw.append(_ckpt("QUANTITY_MERGED", {
    "existing_qty":  existing_qty,
    "incoming_qty":  incoming_qty,
    "resolved_qty":  merged_qty,
    "llm_response":  llm_raw[:200] if llm_raw else None,
    "fault_injected": fault_mode != "NONE",
}))

# ── Step 4: CART_READ — read cart state after update ─────────────────────────
cart_items = [{"product_id": product_id, "quantity": merged_qty}]

if fault_mode == "BL_SHIPMENT_LOST":
    # Cart "lost" after update — reads back as empty
    cart_items = []

lkw.append(_ckpt("CART_READ", {
    "items":          cart_items,
    "total_items":    len(cart_items),
    "fault_injected": fault_mode == "BL_SHIPMENT_LOST",
}))

# ── Step 5: FINAL_ANSWER ──────────────────────────────────────────────────────
lkw.append(_ckpt("FINAL_ANSWER", {
    "success":    True,
    "cart_items": cart_items,
}))

print(json.dumps({
    "lkw":        lkw,
    "cart_items": cart_items,
    "merged_qty": merged_qty,
    "fault_mode": fault_mode,
}))
