"""AdService helper — live LLM graph path with optional boundary handoff."""

import importlib
import json
import os
import sys
import types
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
os.environ["LLAMA_MODEL"] = model
os.environ["LLAMA_TEMPERATURE"] = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)

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