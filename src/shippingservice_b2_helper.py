"""ShippingService B2 single-run helper — called by b2_baseline_runner.py."""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SRC = Path(__file__).parent
SHIP_DIR = SRC / "shippingagent"
sys.path.insert(0, str(SHIP_DIR / "app"))  # makes 'agents' package findable
sys.path.insert(0, str(SHIP_DIR))

os.environ["FAULT_MODE"] = "NONE"
ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
model      = os.getenv("SHIP_MODEL", "qwen2.5-coder:14b")

# Stub gRPC / Motor / dotenv
for name in ("grpc", "motor", "motor.motor_asyncio", "pymongo", "dotenv"):
    sys.modules.setdefault(name, MagicMock())

config_mock = MagicMock()
config_mock.LLAMA_BASE_URL = ollama_url + "/v1"
config_mock.LLAMA_MODEL    = model
sys.modules["config"] = config_mock

# LKWCheckpoint is defined in orchestrator.py — import after path is set
from app.orchestrator import ShippingOrchestrator

PAYLOAD = {
    "address": {
        "street_address": "123 Main St",
        "city":           "Montreal",
        "state":          "QC",
        "country":        "Canada",
        "zip_code":       "H3A 0A1",
    },
    "items": [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}],
}

async def main():
    orch = ShippingOrchestrator()
    with patch("app.orchestrator.save_shipment", new_callable=AsyncMock) as ms, \
         patch("app.orchestrator.save_quote",    new_callable=AsyncMock) as mq:
        ms.return_value = "b2-ship-id"
        mq.return_value = "b2-quote-id"
        result = await orch.ship_order(**PAYLOAD)

    # LKW trace is returned as result["_lkw"]["checkpoints"]
    lkw_dict = result.get("_lkw", {})
    trace = lkw_dict.get("checkpoints", [])

    out = {
        "fault_mode":    "NONE",
        "lkw":           trace,
        "steps_reached": [c["step"] for c in trace],
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }
    out_path = SRC / "results" / "shippingservice_b2_run.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out["steps_reached"]))

asyncio.run(main())
