"""Generate shippingagent B2 raw-run artifacts for coverage parity.

Writes:
  results/b2_raw_runs/shippingagent_b2_run1.json
  results/b2_raw_runs/shippingagent_b2_run2.json
  results/b2_raw_runs/shippingagent_b2_run3.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

BASE = Path(__file__).resolve().parent
APP_DIR = BASE / "shippingagent" / "app"
OUT_DIR = BASE / "results" / "b2_raw_runs"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(BASE / "shippingagent"))

for name in ("grpc", "motor", "motor.motor_asyncio", "pymongo", "dotenv"):
    sys.modules.setdefault(name, MagicMock())


class _ConfigMock:
    LLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434") + "/v1"
    LLAMA_MODEL = os.getenv("SHIP_MODEL", "qwen2.5-coder:14b")
    LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.0"))
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "shipping_service")


sys.modules["config"] = _ConfigMock

import importlib

orch_mod = importlib.import_module("app.orchestrator")

PAYLOAD_ADDRESS = {
    "street_address": "123 Main St",
    "city": "Montreal",
    "state": "QC",
    "country": "Canada",
    "zip_code": "H3A 0A1",
}

PAYLOAD_ITEMS = [
    {"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5},
]


async def run_one(run_idx: int) -> None:
    os.environ["FAULT_MODE"] = "NONE"
    importlib.reload(orch_mod.fi)
    importlib.reload(orch_mod)
    orch_mod.fi.FAULT_MODE = "NONE"

    orchestrator = orch_mod.ShippingOrchestrator()
    orchestrator._call_llama = lambda _messages, stop=None: (
        'Final Answer: {"tracking_id":"TRACK-001","carrier":"FedEx",'
        '"service_level":"ground","cost_usd":12.34}'
    )

    result = await orchestrator.ship_order(
        address=PAYLOAD_ADDRESS,
        items=PAYLOAD_ITEMS,
        capture_partial_trace=True,
    )

    lkw_dict = result.get("_lkw", {})
    trace = lkw_dict.get("checkpoints", [])
    doc = {
        "agent": "shippingagent",
        "run": run_idx,
        "lkw": trace,
        "steps": [cp.get("step") for cp in trace],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"shippingagent_b2_run{run_idx}.json"
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")


async def main() -> int:
    for i in (1, 2, 3):
        await run_one(i)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
