"""Currency checkout helper — pure LLM CurrencyOrchestrator (ReAct loop, no gRPC)."""
import json, os, sys, importlib
from pathlib import Path

SRC       = Path(__file__).parent
AGENT_DIR = SRC / "currencyagent"
sys.path.insert(0, str(AGENT_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode  = payload.get("fault_mode",  "NONE")
model       = payload.get("model",       "qwen2.5:3b")
ollama_url  = payload.get("ollama_url",  "http://localhost:11434")
query       = payload.get("query",       "convert 19 USD to USD")
temperature = float(payload.get("temperature", 0.0))
action      = payload.get("action",      "convert")

os.environ["FAULT_MODE"]        = fault_mode
os.environ["LLAMA_BASE_URL"]    = ollama_url.rstrip("/") + "/v1"
os.environ["LLAMA_MODEL"]       = model
os.environ["LLAMA_TEMPERATURE"]  = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.orchestrator as orch_mod
importlib.reload(orch_mod)

orch   = orch_mod.CurrencyOrchestrator()
result = orch.convert(
    query=query,
    action=action,
    from_currency=payload.get("from_currency", "USD"),
    units=payload.get("units", 19),
    nanos=payload.get("nanos", 990000000),
    to_currency=payload.get("to_currency", "USD"),
    capture_partial_trace=True,
)

lkw           = result.get("_lkw", {}).get("checkpoints", [])
converted_out = {k: result[k] for k in ("currency_code", "units", "nanos") if k in result}
print(json.dumps({"lkw": lkw, "converted": converted_out, "fault_mode": fault_mode}))
