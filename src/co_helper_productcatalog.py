"""ProductCatalog checkout helper — LLM classify_request (Ollama REST) + agent.run()."""
import json, os, sys, importlib, requests
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).parent
AGENT_DIR = SRC / "productcatalogagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub gRPC/proto so imports don't fail without live microservices
sys.modules.setdefault("grpc", MagicMock())
for _m in ("app.clients", "app.clients.demo_pb2", "app.clients.demo_pb2_grpc"):
    sys.modules.setdefault(_m, MagicMock())

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
query      = payload.get("query",      "list all products")

os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

MOCK_PRODUCT = {
    "id": "PROD-001", "name": "Sunglasses",
    "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
}
MOCK_PRODUCT_2 = {
    "id": "PROD-002", "name": "Candle Holder",
    "price_usd": {"currency_code": "USD", "units": 12, "nanos": 500000000},
}
mock_client = MagicMock()
mock_client.list_products.return_value  = [MOCK_PRODUCT, MOCK_PRODUCT_2]
mock_client.get_product.return_value    = MOCK_PRODUCT
mock_client.search_products.return_value = [MOCK_PRODUCT, MOCK_PRODUCT_2]
agent_mod.client = mock_client

def _classify_catalog(query, model, ollama_url):
    """LLM routing call — mirrors graph.py classify_request node."""
    prompt = (
        "You are a router for a product catalog service.\n"
        "Classify the user request into exactly one label:\n"
        "- list_products\n- search_products\n- get_product\n\n"
        "User query: " + query + "\n\nReturn only one label."
    )
    try:
        resp = requests.post(
            ollama_url + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        label = resp.json().get("response", "list_products").strip().lower()
    except Exception:
        label = "list_products"
    if "list_products" in label: return "list_products"
    if "get_product"   in label: return "get_product"
    return "search_products"

route = _classify_catalog(query, model, ollama_url)

agent = agent_mod.ProductCatalogAgent()
if route == "get_product":
    result = agent.run(query=query, product_ids=["PROD-001"])
elif route == "list_products":
    result = agent.run(query="list all products")
else:
    result = agent.run(query=query)

lkw      = fi_mod.get_lkw()
data     = result.get("data", [])
products = data if isinstance(data, list) else [data]
print(json.dumps({"lkw": lkw, "products": products, "fault_mode": fault_mode}))
