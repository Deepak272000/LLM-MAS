"""Email checkout helper — called by b2_systematic_runner.py."""
import json, os, sys, importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).parent
AGENT_DIR = SRC / "emailserviceagent"
sys.path.insert(0, str(AGENT_DIR))

# Stub langgraph and gRPC before any imports
_mock_langgraph = MagicMock()
_mock_langgraph.END = "__end__"
_mock_langgraph.StateGraph = MagicMock()
sys.modules.setdefault("langgraph", MagicMock())
sys.modules["langgraph.graph"] = _mock_langgraph
sys.modules.setdefault("grpc", MagicMock())
sys.modules.setdefault("demo_pb2", MagicMock())
sys.modules.setdefault("demo_pb2_grpc", MagicMock())

payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
os.environ["FAULT_MODE"] = fault_mode

sys.modules.pop("app.fault_injection", None)
import app.fault_injection as fi_mod

MOCK_GENERATE = {
    "email_type": "order_confirmation",
    "subject": "Your order has been confirmed",
    "body": "Hello, your order is confirmed.",
    "llm_used": False,
}
MOCK_SEND = {"status": "sent"}

try:
    import app.graph as graph_mod
    graph_mod.fi = fi_mod

    initial_state = {
        "request": {
            "email":            payload.get("email", "customer@example.com"),
            "order_id":         payload.get("order_id", "ORDER-B2-001"),
            "user_name":        payload.get("user_name", "Test Customer"),
            "currency_code":    payload.get("currency_code", "USD"),
            "total":            payload.get("total", 27.49),
            "items":            payload.get("items", [{"name": "Sunglasses", "quantity": 2, "price": "19.99"}]),
            "shipping_address": payload.get("shipping_address", {}),
        },
        "email_type": "", "subject": "", "body": "",
        "llm_used": False, "microservice_status": "",
    }

    with patch.object(graph_mod, "generate_email_content", return_value=MOCK_GENERATE),          patch.object(graph_mod, "send_via_microservice", return_value=MOCK_SEND):
        from app.graph import task_start_node, generate_email_node, send_via_microservice_node, final_answer_node
        state = initial_state.copy()
        state = task_start_node(state)
        state = generate_email_node(state)
        state = send_via_microservice_node(state)
        state = final_answer_node(state)

    lkw = fi_mod.get_lkw() if hasattr(fi_mod, "get_lkw") else fi_mod._global_lkw
except Exception as e:
    lkw = [{"step": "TASK_START", "data": {"fault_mode": fault_mode}},
           {"step": "FINAL_ANSWER", "data": {"error": str(e)}}]

print(json.dumps({"lkw": lkw, "status": "sent", "fault_mode": fault_mode}))
