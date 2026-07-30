"""Recommendation helper — live LLM graph path with optional boundary handoff."""

import importlib
import json
import os
import sys
import types
import urllib.request
from pathlib import Path

SRC = Path(__file__).parent
AGENT_DIR = SRC / "recommendationagent"
sys.path.insert(0, str(AGENT_DIR))

payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
model = payload.get("model", "qwen2.5:3b")
ollama_url = payload.get("ollama_url", "http://localhost:11434")
temperature = float(payload.get("temperature", 0.0))

os.environ["FAULT_MODE"] = fault_mode
os.environ["OLLAMA_BASE_URL"] = ollama_url
os.environ["OLLAMA_MODEL"] = model
os.environ["LLAMA_BASE_URL"] = ollama_url.rstrip("/") + "/v1"
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

    def add_conditional_edges(self, *_args, **_kwargs):
        pass

    def compile(self):
        return None


langgraph_graph_mod.StateGraph = _DummyStateGraph
langgraph_graph_mod.END = "__end__"
langgraph_mod.graph = langgraph_graph_mod
sys.modules.setdefault("langgraph", langgraph_mod)
sys.modules["langgraph.graph"] = langgraph_graph_mod

llm_pkg = types.ModuleType("app.llm")
llm_mod = types.ModuleType("app.llm.ollama")


def get_ollama_llm():
    return _ShimChatOllama(model, ollama_url, temperature)


llm_mod.get_ollama_llm = get_ollama_llm
llm_pkg.ollama = llm_mod
sys.modules.setdefault("app.llm", llm_pkg)
sys.modules["app.llm.ollama"] = llm_mod

import app.graph as graph_mod
importlib.reload(graph_mod)

state = {
    "query": payload.get("query", "recommend related products for this cart"),
    "user_id": payload.get("user_id", "user-001"),
    "product_ids": payload.get("product_ids", ["PROD-001"]),
    "handoff_contract": payload.get("handoff_contract"),
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_llm_calls": 0,
}
state = graph_mod.classify_request(state)
state = graph_mod.run_agent(state)
if graph_mod.should_enrich(state) == "enrich":
    state = graph_mod.enrich_with_llm(state)

lkw = []
raw_result = state.get("raw_result") or state.get("result") or {}
if isinstance(raw_result, dict):
    lkw = raw_result.get("lkw", [])
recommended = raw_result.get("recommended_product_ids", state.get("result", {}).get("recommended_product_ids", []))
print(json.dumps({"lkw": lkw, "recommended_product_ids": recommended, "fault_mode": fault_mode}))