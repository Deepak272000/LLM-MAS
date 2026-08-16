"""AdService helper — live LLM graph path with optional boundary handoff."""

import importlib
import json
import os
import sys
import types
import urllib.request
from pathlib import Path

SRC = Path(__file__).parent
AGENT_DIR = SRC / "adserviceagent"
sys.path.insert(0, str(AGENT_DIR))

payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model = payload.get("model", "qwen2.5:3b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
temperature = float(payload.get("temperature", 0.0))

os.environ["FAULT_MODE"] = fault_mode
os.environ["USE_LLM"] = "true"
os.environ["OLLAMA_BASE_URL"] = ollama_url
os.environ["OLLAMA_MODEL"] = model
os.environ["LLAMA_MODEL"] = model
os.environ["LLAMA_TEMPERATURE"] = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)


class _ShimResponse:
    def __init__(self, content: str, prompt_tokens: int = 0, eval_tokens: int = 0):
        self.content = content
        self.usage_metadata = {
            "input_tokens": prompt_tokens,
            "output_tokens": eval_tokens,
            "total_tokens": prompt_tokens + eval_tokens,
        }


class _ShimChatOllama:
    def __init__(self, model_name: str, base_url: str, temp: float):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.temp = temp

    def invoke(self, prompt: str):
        body = json.dumps({
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temp},
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload_data = json.loads(response.read().decode("utf-8"))
        return _ShimResponse(
            payload_data.get("response", ""),
            int(payload_data.get("prompt_eval_count") or 0),
            int(payload_data.get("eval_count") or 0),
        )

langgraph_mod = types.ModuleType("langgraph")
langgraph_graph_mod = types.ModuleType("langgraph.graph")


class _DummyStateGraph:
    def __init__(self, *_args, **_kwargs):
        pass

    def add_node(self, *_args, **_kwargs):
        pass

    def set_entry_point(self, *_args, **_kwargs):
        pass

    def add_edge(self, *_args, **_kwargs):
        pass

    def compile(self):
        return None


langgraph_graph_mod.StateGraph = _DummyStateGraph
langgraph_graph_mod.END = "__end__"
langgraph_mod.graph = langgraph_graph_mod
sys.modules.setdefault("langgraph", langgraph_mod)
sys.modules["langgraph.graph"] = langgraph_graph_mod

llm_pkg = types.ModuleType("app.llm")
llm_mod = types.ModuleType("app.llm.qwen")


def get_qwen_llm():
    return _ShimChatOllama(model, ollama_url, temperature)


llm_mod.get_qwen_llm = get_qwen_llm
llm_pkg.qwen = llm_mod
sys.modules.setdefault("app.llm", llm_pkg)
sys.modules["app.llm.qwen"] = llm_mod

# Mock gRPC dependencies — AdService server is not available on SPEED/local runs.
# The LLM path (context key extraction) still runs live; only the ad fetch is mocked.
_MOCK_ADS = [
    {"redirect_url": "https://shop.example.com/hats", "text": "Buy stylish hats!"},
    {"redirect_url": "https://shop.example.com/shoes", "text": "New shoe collection"},
]

_grpc_client_mod = types.ModuleType("app.grpc_client")


class _MockAdServiceClient:
    def get_ads(self, context_keys):
        return list(_MOCK_ADS)


_grpc_client_mod.AdServiceClient = _MockAdServiceClient
sys.modules["app.grpc_client"] = _grpc_client_mod
sys.modules.setdefault("grpc", types.ModuleType("grpc"))
sys.modules.setdefault("app.clients", types.ModuleType("app.clients"))
sys.modules.setdefault("app.clients.demo_pb2", types.ModuleType("app.clients.demo_pb2"))
sys.modules.setdefault("app.clients.demo_pb2_grpc", types.ModuleType("app.clients.demo_pb2_grpc"))

import app.graph as graph_mod
importlib.reload(graph_mod)

state = {
    "instruction": payload.get("instruction", "show me some clothing ads"),
    "context_keys": payload.get("context_keys", []),
    "handoff_contract": payload.get("handoff_contract"),
}

state = graph_mod.input_node(state)
state = graph_mod.ad_lookup_node(state)
state = graph_mod.output_node(state)

lkw = fi_mod.get_lkw()
final_response = state.get("final_response", {}) if isinstance(state, dict) else {}
ads = final_response.get("ads", state.get("ads", []) if isinstance(state, dict) else [])
print(json.dumps({"lkw": lkw, "ads": ads, "fault_mode": fault_mode}))