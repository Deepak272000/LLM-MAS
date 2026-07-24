"""
ShippingAgent fault-injection harness.

Runs ship_order in benchmark mode (capture_partial_trace=True) for each fault mode
and writes shippingagent_fault_results.json in the same schema as other agents.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

BASE = Path(__file__).resolve().parent
APP_DIR = BASE / "app"

# Ensure local app package imports resolve first
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(BASE))

# Stub optional runtime deps so harness can run without external services.
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
import app.orchestrator as orch_mod


FAULT_MODES = [
    "NONE",
    "FM_3_1",
    "FM_1_2",
    "FM_2_2",
    "FM_2_5",
    "BL_SHIPMENT_LOST",
    "BL_INVENTORY_MISMATCH",
    "BL_VENDOR_NEGOTIATION",
    "BL_CUSTOMER_ESCALATION",
    "BL_REFUND_REASONING",
    "BL_COMPLIANCE_AMBIGUITY",
]

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


def _status_for_result(result: dict) -> str:
    if result.get("fault_mode") == "NONE":
        return "PASS"
    if result.get("infection_point") is None and result.get("propagation_depth", 0) == 0:
        return "FN"
    return "TP"


async def _run_one_fault(fault_mode: str) -> dict:
    os.environ["FAULT_MODE"] = fault_mode

    importlib.reload(orch_mod.fi)
    importlib.reload(orch_mod)
    orch_mod.fi.FAULT_MODE = fault_mode

    orchestrator = orch_mod.ShippingOrchestrator()

    # Keep the run deterministic and offline by bypassing remote LLM calls.
    orchestrator._call_llama = lambda _messages, stop=None: (
        'Final Answer: {"tracking_id":"TRACK-001","carrier":"FedEx",'
        '"service_level":"ground","cost_usd":12.34}'
    )

    started = datetime.now(timezone.utc)
    result = await orchestrator.ship_order(
        address=PAYLOAD_ADDRESS,
        items=PAYLOAD_ITEMS,
        capture_partial_trace=True,
    )
    elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    lkw_dict = result.get("_lkw", {})
    rip = lkw_dict.get("rip_summary", {})
    steps_reached = [cp.get("step") for cp in lkw_dict.get("checkpoints", [])]

    row = {
        "fault_mode": fault_mode,
        "elapsed_ms": round(elapsed_ms, 1),
        "status": "PASS" if fault_mode == "NONE" else "FAULT",
        "steps_reached": steps_reached,
        "steps_lost": rip.get("missing_steps", []),
        "infection_point": rip.get("infection_point"),
        "propagation_depth": rip.get("propagation_depth", 0),
        "failure_class": result.get("failure_class", "ok"),
        "lkw": lkw_dict.get("checkpoints", []),
        "data": {
            "tracking_id": result.get("tracking_id"),
            "fault_mode": fault_mode,
        },
    }
    row["classification"] = _status_for_result(row)
    return row


async def main() -> int:
    requested = [m.upper() for m in sys.argv[1:] if m.strip()]
    modes = requested or FAULT_MODES

    print(f"\nRunning {len(modes)} fault modes: {', '.join(modes)}")

    rows = []
    for mode in modes:
        print(f"  -> {mode} ... ", end="", flush=True)
        row = await _run_one_fault(mode)
        rows.append(row)
        print(f"done | steps={len(row['steps_reached'])} | infects={row['infection_point']}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": rows,
    }

    out_path = BASE / "shippingagent_fault_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("  SHIPPINGAGENT FAULT INJECTION - TEST REPORT")
    print("=" * 70)
    for row in rows:
        print(f"\n  [{row['classification']}] {row['fault_mode']}")
        print(f"  Steps   : {' -> '.join(row['steps_reached'])}")
        print(f"  Infects : {row['infection_point']}")
        print(f"  Prop.   : {row['propagation_depth']}")
    print("\n" + "=" * 70)
    print(f"  Results saved to: {out_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
