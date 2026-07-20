"""Email checkout helper — real LLM content generation via Ollama (USE_LLM=true)."""
import json, os, sys, importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).parent
AGENT_DIR = SRC / "emailserviceagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub gRPC (email microservice send call) and langgraph (not in shippingservice venv)
sys.modules.setdefault("grpc", MagicMock())
sys.modules.setdefault("demo_pb2", MagicMock())
sys.modules.setdefault("demo_pb2_grpc", MagicMock())
_lg = MagicMock()
_lg.END = "__end__"
sys.modules.setdefault("langgraph", MagicMock())
sys.modules["langgraph.graph"] = _lg

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model      = payload.get("model",      "qwen2.5-coder:14b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")

# Set env BEFORE any app imports — config.Settings reads USE_LLM at class definition time
os.environ["FAULT_MODE"]    = fault_mode
os.environ["MODEL_NAME"]    = model
os.environ["OLLAMA_BASE_URL"] = ollama_url
os.environ["USE_LLM"]       = "true"   # enable real LLM email generation

for _k in ("app.fault_injection", "app.config", "app.agent", "app.graph"):
    sys.modules.pop(_k, None)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)

MOCK_SEND = {"status": "sent"}

try:
    import app.graph as graph_mod
    importlib.reload(graph_mod)
    graph_mod.fi = fi_mod

    state = {
        "request": {
            "email":            payload.get("email",     "customer@example.com"),
            "order_id":         payload.get("order_id",  "ORDER-B2-001"),
            "user_name":        payload.get("user_name", "Test Customer"),
            "currency_code":    payload.get("currency_code", "USD"),
            "total":            payload.get("total",     27.49),
            "items":            payload.get("items",     [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}]),
            "shipping_address": payload.get("shipping_address", {}),
        },
        "handoff_contract": None,
        "email_type": "", "subject": "", "body": "",
        "llm_used": False, "microservice_status": "",
    }

    # Call graph nodes directly (no LangGraph runtime needed)
    state = graph_mod.run_agent_node(state)       # real LLM email generation here
    from app.grpc_client import EmailServiceClient
    with patch.object(EmailServiceClient, "send_confirmation_email", return_value=MOCK_SEND):
        state = graph_mod.send_via_microservice_node(state)

    lkw = fi_mod.get_lkw() if hasattr(fi_mod, "get_lkw") else getattr(fi_mod, "_global_lkw", [])
except Exception as e:
    import traceback; traceback.print_exc(file=sys.stderr)
    lkw = [{"step": "TASK_START", "data": {"fault_mode": fault_mode}},
           {"step": "FINAL_ANSWER", "data": {"error": str(e)}}]

print(json.dumps({"lkw": lkw, "status": "sent", "fault_mode": fault_mode}))
