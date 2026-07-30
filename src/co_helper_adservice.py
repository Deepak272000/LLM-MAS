"""AdService helper — live LLM graph path with optional boundary handoff."""

import importlib
import json
import os
import sys
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
import app.graph as graph_mod
importlib.reload(graph_mod)

graph = graph_mod.build_graph()
state = {
    "instruction": payload.get("instruction", "show me some clothing ads"),
    "context_keys": payload.get("context_keys", []),
    "handoff_contract": payload.get("handoff_contract"),
}
result = graph.invoke(state)

lkw = fi_mod.get_lkw()
final_response = result.get("final_response", {}) if isinstance(result, dict) else {}
ads = final_response.get("ads", result.get("ads", []) if isinstance(result, dict) else [])
print(json.dumps({"lkw": lkw, "ads": ads, "fault_mode": fault_mode}))