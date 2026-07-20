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
        "You are a router for a currency service.\n"
        "Classify the user request into exactly one label:\n"
        "- get_supported_currencies\n- convert\n\n"
        "User query: " + query + "\n\nReturn only one label."
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
