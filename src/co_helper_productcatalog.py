"""ProductCatalog checkout helper — called by b2_systematic_runner.py."""
import json, os, sys, importlib
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).parent
AGENT_DIR = SRC / "productcatalogagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub gRPC and proto at module level
sys.modules.setdefault("grpc", MagicMock())
_mc = MagicMock()
sys.modules.setdefault("app.clients", _mc)
sys.modules.setdefault("app.clients.demo_pb2", MagicMock())
sys.modules.setdefault("app.clients.demo_pb2_grpc", MagicMock())

payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

MOCK_PRODUCT = {
    "id": "PROD-001", "name": "Sunglasses",
    "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
}
mock_client = MagicMock()
mock_client.list_products.return_value = [MOCK_PRODUCT]
mock_client.get_product.return_value = MOCK_PRODUCT
mock_client.search_products.return_value = [MOCK_PRODUCT]
agent_mod.client = mock_client

agent = agent_mod.ProductCatalogAgent()
result = agent.run(query=payload.get("query", "list all products"))
lkw = fi_mod.get_lkw()

data = result.get("data", [])
products = data if isinstance(data, list) else [data]
print(json.dumps({"lkw": lkw, "products": products, "fault_mode": fault_mode}))
