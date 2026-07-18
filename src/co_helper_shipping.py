"""Shipping checkout helper — supports get_quote and ship_order actions."""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SRC      = Path(__file__).parent
SHIP_DIR = SRC / "shippingagent"
sys.path.insert(0, str(SHIP_DIR / "app"))
sys.path.insert(0, str(SHIP_DIR))

payload    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
action     = payload.get("action", "ship_order")
fault_mode = payload.get("fault_mode", "NONE")

os.environ["FAULT_MODE"] = fault_mode

for name in ("grpc", "motor", "motor.motor_asyncio", "pymongo", "dotenv"):
    sys.modules.setdefault(name, MagicMock())

config_mock = MagicMock()
config_mock.LLAMA_BASE_URL = payload.get("ollama_url", "http://localhost:11434") + "/v1"
config_mock.LLAMA_MODEL    = payload.get("model",      "qwen2.5-coder:14b")
config_mock.LLAMA_TEMPERATURE = float(payload.get("temperature", 0.0))
sys.modules["config"] = config_mock

from app.orchestrator import ShippingOrchestrator

ADDRESS = payload.get("address", {
    "street_address": "123 Main St", "city": "Montreal",
    "state": "QC", "country": "Canada", "zip_code": "H3A 0A1",
})
ITEMS = payload.get("items", [{"product_id": "PROD-001", "quantity": 2, "weight_kg": 1.5}])

async def main():
    orch = ShippingOrchestrator()
    with patch("app.orchestrator.save_shipment", new_callable=AsyncMock) as ms, \
         patch("app.orchestrator.save_quote",    new_callable=AsyncMock) as mq:
        ms.return_value = "co-ship-id"
        mq.return_value = "co-quote-id"
        if action == "get_quote":
            result = await orch.get_quote(address=ADDRESS, items=ITEMS,
                                          capture_partial_trace=True)
        else:
            result = await orch.ship_order(address=ADDRESS, items=ITEMS,
                                           capture_partial_trace=True)

    lkw_dict = result.get("_lkw", {})
    trace    = lkw_dict.get("checkpoints", [])
    cost_usd = result.get("cost_usd", None)
    tracking = result.get("tracking_id", None)
    error    = result.get("error", None)

    print(json.dumps({
        "lkw":          trace,
        "cost_usd":     cost_usd,
        "tracking_id":  tracking,
        "error":        error,
        "action":       action,
        "fault_mode":   fault_mode,
        "model":        payload.get("model", "qwen2.5-coder:14b"),
        "temperature":  payload.get("temperature", 0.0),
    }))

asyncio.run(main())
