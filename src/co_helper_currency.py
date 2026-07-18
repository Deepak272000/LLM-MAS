"""Currency checkout helper — called by b2_systematic_runner.py."""
import json, os, sys, importlib
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).parent
AGENT_DIR = SRC / "currencyagent"
sys.path.insert(0, str(AGENT_DIR))

payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
fault_mode = payload.get("fault_mode", "NONE")
os.environ["FAULT_MODE"] = fault_mode

import app.fault_injection as fi_mod
importlib.reload(fi_mod)
import app.agent as agent_mod
importlib.reload(agent_mod)

converted = {
    "currency_code": payload.get("to_currency", "USD"),
    "units":  payload.get("units", 19),
    "nanos":  payload.get("nanos", 990000000),
}
mock_client = MagicMock()
mock_client.convert.return_value = dict(converted)
mock_client.get_supported_currencies.return_value = ["USD", "EUR", "GBP", "CAD"]
agent_mod.client = mock_client

agent = agent_mod.CurrencyAgent()
result = agent.run(
    query=payload.get("query", "convert 19 USD to USD"),
    action="convert",
    from_currency=payload.get("from_currency", "USD"),
    units=payload.get("units", 19),
    nanos=payload.get("nanos", 990000000),
    to_currency=payload.get("to_currency", "USD"),
)
lkw = result.get("lkw", [])
converted_out = result.get("data", converted)
print(json.dumps({"lkw": lkw, "converted": converted_out, "fault_mode": fault_mode}))
