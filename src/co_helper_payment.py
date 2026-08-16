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
        "You are a router for a payment service.\n"
        "Classify the user request into exactly one label:\n"
        "- charge\n\n"
        "User query: " + query + "\n\nReturn only one label."
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
