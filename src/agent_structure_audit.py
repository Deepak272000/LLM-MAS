#!/usr/bin/env python3
"""Audit agent folder layout consistency.

This script does not modify files. It reports which agents match the expected
layout so structural drift is easy to spot before commits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass
class AgentLayoutResult:
    agent: str
    kind: str
    missing: list[str]
    extra: list[str]
    status: str


def check_required(base: Path, required: list[str]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    for rel in required:
        if not (base / rel).exists():
            missing.append(rel)

    extra: list[str] = []
    if (base / "agent.go.new").exists():
        extra.append("agent.go.new")
    return missing, extra


def audit_python_agent(name: str) -> AgentLayoutResult:
    base = ROOT / name
    required = [
        "app",
        "requirements.txt",
        "app/config.py",
        "app/fault_injection.py",
    ]

    optional_expected = ["test_fault_injection.py", "app/main.py", "app/agent.py"]
    missing, extra = check_required(base, required)

    optional_missing = [r for r in optional_expected if not (base / r).exists()]
    missing.extend([f"optional:{r}" for r in optional_missing])

    status = "ok" if not [m for m in missing if not m.startswith("optional:")] and not extra else "needs_attention"
    return AgentLayoutResult(agent=name, kind="python-agent", missing=missing, extra=extra, status=status)


def audit_checkout_agent() -> AgentLayoutResult:
    name = "checkout-agent"
    base = ROOT / name
    required = [
        "go.mod",
        "main.go",
        "Dockerfile",
        "agent/agent.go",
        "tools/tools.go",
    ]
    missing, extra = check_required(base, required)
    status = "ok" if not missing and not extra else "needs_attention"
    return AgentLayoutResult(agent=name, kind="go-agent", missing=missing, extra=extra, status=status)


def audit_cart_agent() -> AgentLayoutResult:
    name = "cart-agent"
    base = ROOT / name
    required = [
        "Dockerfile",
        "src/cartservice.csproj",
        "src/program.cs",
        "src/cartserviceimpl.cs",
    ]
    missing, extra = check_required(base, required)

    duplicate_candidates = [
        "program.cs",
        "cartserviceimpl.cs",
        "cartservice.csproj",
        "demo.proto",
        "k8s-manifest.yaml",
        "ollamacartagent.cs",
    ]
    for rel in duplicate_candidates:
        if (base / rel).exists() and (base / "src" / rel).exists():
            extra.append(f"duplicate:{rel}")

    status = "ok" if not missing and not extra else "needs_attention"
    return AgentLayoutResult(agent=name, kind="dotnet-agent", missing=missing, extra=extra, status=status)


def main() -> None:
    python_agents = [
        "adserviceagent",
        "currencyagent",
        "emailserviceagent",
        "paymentagent",
        "productcatalogagent",
        "recommendationagent",
        "shippingagent",
    ]

    results: list[AgentLayoutResult] = [audit_python_agent(a) for a in python_agents]
    results.append(audit_cart_agent())
    results.append(audit_checkout_agent())

    payload = {
        "repo": "LLM-MAS",
        "results": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r.status == "ok"),
            "needs_attention": sum(1 for r in results if r.status != "ok"),
        },
    }

    out = ROOT / "results" / "agent_structure_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload["summary"], indent=2))
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
