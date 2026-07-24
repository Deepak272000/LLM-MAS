"""SPEED run preflight for all agents.

Checks harness/build readiness and emits a machine-readable report:
  results/speed_run_preflight.json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
OUT = RESULTS / "speed_run_preflight.json"

PY_AGENTS = [
    "paymentagent",
    "currencyagent",
    "emailserviceagent",
    "productcatalogagent",
    "recommendationagent",
    "adserviceagent",
    "shippingagent",
]

NATIVE_AGENTS = [
    {
        "name": "cart-agent",
        "build_cmd": ["dotnet", "build", str(ROOT / "cart-agent" / "src" / "cartservice.csproj"), "-nologo"],
        "tool": "dotnet",
    },
    {
        "name": "checkout-agent",
        "build_cmd": ["go", "build", "./..."],
        "cwd": str(ROOT / "checkout-agent"),
        "tool": "go",
    },
]


def _run(cmd: list[str], cwd: str | None = None) -> tuple[bool, str]:
    env = dict(**os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except Exception as exc:
        return False, str(exc)
    ok = proc.returncode == 0
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return ok, output.strip()[-3000:]


def check_python_agent(agent: str) -> dict:
    agent_dir = ROOT / agent
    harness = agent_dir / "test_fault_injection.py"
    result_json = agent_dir / f"{agent}_fault_results.json"

    row = {
        "agent": agent,
        "language": "python",
        "harness_exists": harness.exists(),
        "result_json_exists": result_json.exists(),
        "smoke_none_ok": False,
        "status": "missing",
        "notes": [],
    }

    if not harness.exists():
        row["notes"].append("missing test_fault_injection.py")
        return row

    ok, out = _run([sys.executable, "test_fault_injection.py", "NONE"], cwd=str(agent_dir))
    row["smoke_none_ok"] = ok
    row["smoke_output_tail"] = out

    if ok and result_json.exists():
        row["status"] = "ready"
    elif ok:
        row["status"] = "partial"
        row["notes"].append("harness ran but result json missing")
    else:
        row["status"] = "blocked"
        row["notes"].append("harness NONE run failed")

    return row


def check_native_agent(item: dict) -> dict:
    tool = item["tool"]
    tool_path = shutil.which(tool)
    row = {
        "agent": item["name"],
        "language": "csharp" if item["name"] == "cart-agent" else "go",
        "build_tool": tool,
        "tool_available": bool(tool_path),
        "build_ok": False,
        "status": "missing",
        "notes": [],
    }

    if not tool_path:
        row["status"] = "blocked"
        row["notes"].append(f"{tool} not found in PATH")
        return row

    ok, out = _run(item["build_cmd"], cwd=item.get("cwd"))
    row["build_ok"] = ok
    row["build_output_tail"] = out

    harness = ROOT / item["name"] / "test_fault_injection.py"
    result_json = ROOT / item["name"] / f"{item['name']}_fault_results.json"
    row["harness_exists"] = harness.exists()
    row["result_json_exists"] = result_json.exists()

    if ok and harness.exists():
        row["status"] = "ready" if result_json.exists() else "partial"
        if not result_json.exists():
            row["notes"].append("FI harness exists but no result json yet")
    elif ok:
        row["status"] = "partial"
        row["notes"].append("build passes but FI harness missing")
    else:
        row["status"] = "blocked"
        row["notes"].append("build failed")

    return row


def main() -> int:
    RESULTS.mkdir(exist_ok=True)

    rows = []
    rows.extend(check_python_agent(a) for a in PY_AGENTS)
    rows.extend(check_native_agent(a) for a in NATIVE_AGENTS)

    totals = {
        "ready": sum(1 for r in rows if r["status"] == "ready"),
        "partial": sum(1 for r in rows if r["status"] == "partial"),
        "blocked": sum(1 for r in rows if r["status"] == "blocked"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "rows": rows,
        "totals": totals,
    }

    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(totals, indent=2))
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
