"""Recommendation helper — live LLM orchestrator path with optional boundary handoff."""

import importlib
import json
import os
import sys
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
os.environ["LLAMA_BASE_URL"] = ollama_url.rstrip("/") + "/v1"
os.environ["LLAMA_MODEL"] = model
os.environ["LLAMA_TEMPERATURE"] = str(temperature)

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.orchestrator as orch_mod
importlib.reload(orch_mod)

orch = orch_mod.RecommendationOrchestrator()
result = orch.recommend(
    product_ids=payload.get("product_ids", ["PROD-001"]),
    user_id=payload.get("user_id", "user-001"),
    capture_partial_trace=True,
)

lkw = result.get("_lkw", {}).get("checkpoints", [])
recommended = result.get("product_ids", [])
print(json.dumps({"lkw": lkw, "recommended_product_ids": recommended, "fault_mode": fault_mode}))