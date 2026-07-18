"""Reusable boundary validation helpers for cross-agent handoffs."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from boundary_recovery import decide_recovery


def _events_file() -> Path | None:
    configured = os.getenv("BOUNDARY_EVENTS_FILE")
    enabled = os.getenv("BOUNDARY_EVENTS_ENABLED", "0").lower() in {"1", "true", "yes"}
    if not configured and not enabled:
        return None
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "results" / "boundary_events.jsonl"


def _record_boundary_event(result: dict) -> None:
    path = _events_file()
    if path is None:
        return
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")


def _finalize_boundary_result(result: dict) -> dict:
    result = deepcopy(result)
    result["recovery"] = decide_recovery(result)
    _record_boundary_event(result)
    return result


def boundary_contract(
    name: str,
    expected: Any,
    observed: Any,
    *,
    required_keys: list[str] | None = None,
    validators: dict[str, Callable[[Any], bool]] | None = None,
    allow_extra_keys: bool = True,
) -> dict:
    """Summarize whether a handoff boundary preserved the upstream payload.

    When `expected`/`observed` are dicts, the helper can also validate required
    keys and field-level predicates. This is used for stricter handoff checks
    at agent boundaries and internal shipping workflow boundaries.
    """
    violations: list[dict] = []

    strict_checks = bool(required_keys or validators or not allow_extra_keys)

    if expected == observed and not strict_checks:
        result = {
            "boundary": name,
            "alert": False,
            "status": "clean",
            "expected": expected,
            "observed": observed,
            "difference": None,
            "violations": [],
        }
        return _finalize_boundary_result(result)

    if isinstance(expected, list) and isinstance(observed, list):
        missing = [item for item in expected if item not in observed]
        extra = [item for item in observed if item not in expected]
        difference = {"missing": missing, "extra": extra}
        detail = f"missing={missing}, extra={extra}"
        if missing:
            violations.append({"type": "missing_items", "items": missing})
        if extra:
            violations.append({"type": "extra_items", "items": extra})
    elif isinstance(expected, dict) and isinstance(observed, dict):
        missing = [key for key in expected if key not in observed]
        extra = [key for key in observed if key not in expected]
        if missing:
            violations.append({"type": "missing_keys", "keys": missing})
        if extra and not allow_extra_keys:
            violations.append({"type": "extra_keys", "keys": extra})

        field_mismatches = []
        for key, expected_value in expected.items():
            if key not in observed:
                continue
            observed_value = observed[key]
            if observed_value != expected_value:
                field_mismatches.append({
                    "field": key,
                    "expected": expected_value,
                    "observed": observed_value,
                })
        if field_mismatches:
            violations.append({"type": "field_mismatch", "fields": field_mismatches})

        if required_keys:
            absent_required = [key for key in required_keys if key not in observed]
            if absent_required:
                violations.append({"type": "required_keys_missing", "keys": absent_required})

        if validators:
            failed_validators = []
            for key, predicate in validators.items():
                if key in observed:
                    subject = observed[key]
                elif key in ("__value__", "__list__"):
                    subject = observed
                else:
                    continue
                try:
                    ok = bool(predicate(subject))
                except Exception as exc:
                    ok = False
                    failed_validators.append({"field": key, "reason": str(exc)})
                else:
                    if not ok:
                        failed_validators.append({"field": key, "reason": "predicate returned false"})
            if failed_validators:
                violations.append({"type": "predicate_failed", "fields": failed_validators})

        difference = {
            "missing": missing,
            "extra": extra,
            "field_mismatches": field_mismatches,
        }
        detail = f"missing={missing}, extra={extra}, field_mismatches={field_mismatches}"
    else:
        delta = (
            observed - expected
            if isinstance(expected, (int, float)) and isinstance(observed, (int, float))
            else None
        )
        difference = {"delta": delta}
        detail = f"delta={delta}" if delta is not None else f"expected={expected}, observed={observed}"
        if delta is not None and delta != 0:
            violations.append({"type": "delta", "value": delta})

    if required_keys and not isinstance(observed, dict):
        violations.append({"type": "expected_mapping", "reason": "required_keys supplied for non-mapping payload"})

    if validators and not isinstance(observed, dict):
        failed_validators = []
        for key, predicate in validators.items():
            if key not in ("__value__", "__list__"):
                continue
            try:
                ok = bool(predicate(observed))
            except Exception as exc:
                ok = False
                failed_validators.append({"field": key, "reason": str(exc)})
            else:
                if not ok:
                    failed_validators.append({"field": key, "reason": "predicate returned false"})
        if failed_validators:
            violations.append({"type": "predicate_failed", "fields": failed_validators})

    if not violations and expected == observed and strict_checks:
        detail = "strict validation passed"

    result = {
        "boundary": name,
        "alert": bool(violations) or expected != observed,
        "status": "signal_escape" if (violations or expected != observed) else "clean",
        "expected": expected,
        "observed": observed,
        "difference": difference,
        "detail": detail,
        "violations": violations,
    }
    return _finalize_boundary_result(result)


def summarize_boundary_results(chains: list[dict]) -> dict:
    """Aggregate boundary-detection outcomes across all cross-agent chains."""
    boundary_alerts = sum(1 for chain in chains if chain.get("boundary_contract", {}).get("alert"))
    signal_escapes = sum(1 for chain in chains if chain.get("signal_escape"))
    downstream_clean = sum(1 for chain in chains if chain.get("hop2_infection") is None)
    return {
        "total_chains": len(chains),
        "boundary_alerts": boundary_alerts,
        "signal_escapes": signal_escapes,
        "downstream_clean": downstream_clean,
        "manual_review_candidates": signal_escapes,
    }


def classify_failure(exc: Exception | str | None, fault_mode: str | None = None) -> str:
    """Classify runtime failures separately from fault-induced outcomes."""
    if exc is None:
        return "ok"

    message = str(exc).lower()
    if "read timeout" in message or "timeout" in message:
        return "infra_timeout"
    if "connection" in message or "cannot reach" in message:
        return "infra_connection"
    if fault_mode and fault_mode != "NONE":
        return "fault_induced"
    return "unknown_failure"
