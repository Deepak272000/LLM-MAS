"""ProductCatalog checkout helper — pure LLM ProductCatalogOrchestrator (ReAct loop, no gRPC)."""
import json, os, sys, importlib
from pathlib import Path

SRC       = Path(__file__).parent
AGENT_DIR = SRC / "productcatalogagent"
sys.path.insert(0, str(AGENT_DIR))

payload     = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode  = payload.get("fault_mode",  "NONE")
model       = payload.get("model",       "qwen2.5:3b")
ollama_url  = payload.get("ollama_url",  "http://localhost:11434")
query       = payload.get("query",       "list all products")
temperature = float(payload.get("temperature", 0.0))
product_ids = payload.get("product_ids", None)

os.environ["FAULT_MODE"]        = fault_mode
os.environ["LLAMA_BASE_URL"]    = ollama_url.rstrip("/") + "/v1"
os.environ["LLAMA_MODEL"]       = model
os.environ["LLAMA_TEMPERATURE"]  = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.orchestrator as orch_mod
importlib.reload(orch_mod)

orch   = orch_mod.ProductCatalogOrchestrator()
result = orch.run(
    query=query,
    product_ids=product_ids,
    capture_partial_trace=True,
)

lkw      = result.get("_lkw", {}).get("checkpoints", [])
products = result.get("products", [])
print(json.dumps({"lkw": lkw, "products": products, "fault_mode": fault_mode}))
