"""Payment checkout helper — pure LLM PaymentOrchestrator (ReAct loop, no gRPC)."""
import asyncio, json, os, sys, importlib
from pathlib import Path

SRC       = Path(__file__).parent
AGENT_DIR = SRC / "paymentagent"
sys.path.insert(0, str(AGENT_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5:3b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
query      = payload.get("query",      "charge card")
temperature = float(payload.get("temperature", 0.0))

os.environ["FAULT_MODE"]       = fault_mode
os.environ["LLAMA_BASE_URL"]   = ollama_url.rstrip("/") + "/v1"
os.environ["LLAMA_MODEL"]      = model
os.environ["LLAMA_TEMPERATURE"] = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.orchestrator as orch_mod
importlib.reload(orch_mod)

async def main():
    orch   = orch_mod.PaymentOrchestrator()
    result = await orch.charge(
        query=query,
        currency_code=payload.get("currency_code", "USD"),
        units=payload.get("units", 27),
        nanos=payload.get("nanos", 490000000),
        credit_card_number=payload.get("credit_card_number", "4111111111111111"),
        credit_card_cvv=payload.get("credit_card_cvv", 123),
        credit_card_expiration_year=payload.get("credit_card_expiration_year", 2030),
        credit_card_expiration_month=payload.get("credit_card_expiration_month", 12),
        capture_partial_trace=True,
    )
    lkw            = result.get("_lkw", {}).get("checkpoints", [])
    transaction_id = result.get("transaction_id")
    print(json.dumps({"lkw": lkw, "transaction_id": transaction_id,
                      "data": result, "fault_mode": fault_mode}))

asyncio.run(main())
