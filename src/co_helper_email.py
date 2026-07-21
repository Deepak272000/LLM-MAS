"""Email checkout helper — pure LLM EmailOrchestrator (ReAct loop, no gRPC)."""
import asyncio, json, os, sys, importlib
from pathlib import Path

SRC       = Path(__file__).parent
AGENT_DIR = SRC / "emailserviceagent"
sys.path.insert(0, str(AGENT_DIR))

payload     = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode  = payload.get("fault_mode",  "NONE")
model       = payload.get("model",       "qwen2.5:3b")
ollama_url  = payload.get("ollama_url",  "http://localhost:11434")
temperature = float(payload.get("temperature", 0.0))

os.environ["FAULT_MODE"]        = fault_mode
os.environ["LLAMA_BASE_URL"]    = ollama_url.rstrip("/") + "/v1"
os.environ["LLAMA_MODEL"]       = model
os.environ["LLAMA_TEMPERATURE"] = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.orchestrator as orch_mod
importlib.reload(orch_mod)

async def main():
    orch   = orch_mod.EmailOrchestrator()
    result = await orch.send_order_confirmation(
        order_id=payload.get("order_id",         "ORDER-B2-001"),
        customer_email=payload.get("email",      "customer@example.com"),
        customer_name=payload.get("user_name",   "Test Customer"),
        items=payload.get("items",               [{"product_id": "OLJCESPC7Z", "quantity": 2}]),
        total_cost=payload.get("total_cost",     {"currency_code": "USD", "units": 27, "nanos": 490000000}),
        shipping_tracking_id=payload.get("shipping_tracking_id", ""),
        capture_partial_trace=True,
    )
    lkw = result.get("_lkw", {}).get("checkpoints", [])
    print(json.dumps({"lkw": lkw, "status": "sent",
                      "message_id": result.get("message_id"),
                      "fault_mode": fault_mode}))

asyncio.run(main())
