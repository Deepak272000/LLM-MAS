"""
Fault Injection Module — MAST-aligned fault patterns for currencyagent
=======================================================================
Set FAULT_MODE environment variable to activate a fault.

Fault modes:

  NONE                    — Baseline (no injection)

  FM_3_1                  — Premature Termination
                            Agent returns before executing the gRPC conversion call.

  FM_2_2                  — Hallucinated Conversion Result
                            gRPC result replaced with fabricated exchange rate data.

  FM_2_5                  — Input Amount Ignored
                            Units/nanos silently replaced before conversion call.

  FM_1_2                  — Wrong Currency Routing
                            Target currency silently swapped to a different code.

  BL_RATE_MANIPULATION    — Rate Manipulated
                            Conversion result inflated 10x after gRPC returns.

  BL_CURRENCY_UNAVAILABLE — Unsupported Currency Simulated
                            gRPC call raises an error for a supported currency.

  BL_STALE_RATE           — Stale Rate Injected
                            Live conversion result replaced with a cached stale value.

  BL_CONVERSION_OVERFLOW  — Overflow Amount Injected
                            Returned amount set to an extreme value simulating overflow.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

FAULT_MODE = os.getenv("FAULT_MODE", "NONE").upper()

# ── LKW Checkpoint Logger ──────────────────────────────────────────────────────

class LKWCheckpoint:
    EXPECTED_STEPS = ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"]

    def __init__(self):
        self.checkpoints: list[dict] = []

    def record(self, step: str, data: dict):
        entry = {
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fault_mode": FAULT_MODE,
            "data": data,
        }
        self.checkpoints.append(entry)
        log.info(f"[LKW] {step} | fault={FAULT_MODE} | {json.dumps(data)}")

    def rip_summary(self) -> dict:
        reached = [cp["step"] for cp in self.checkpoints]
        missing = [s for s in self.EXPECTED_STEPS if s not in reached]
        infection = None
        boundary_alert_steps = []
        for cp in self.checkpoints:
            d = cp.get("data", {})
            if cp["step"] == "BOUNDARY_CHECK" and d.get("alert"):
                boundary_alert_steps.append(d.get("boundary", "BOUNDARY_CHECK"))
            if any([
                d.get("hallucinated"), d.get("amount_tampered"),
                d.get("currency_swapped"), d.get("rate_manipulated"),
                d.get("stale_rate"), d.get("overflow"),
                d.get("premature_termination"), d.get("unavailable"),
            ]):
                if infection is None:
                    infection = cp["step"]
        return {
            "reachability": reached,
            "infection_point": infection,
            "boundary_alert_steps": boundary_alert_steps,
            "boundary_alert_point": boundary_alert_steps[0] if boundary_alert_steps else None,
            "propagation_depth": len(missing),
            "missing_steps": missing,
        }


# ── Fault injection helpers ────────────────────────────────────────────────────

def maybe_premature_termination() -> Optional[dict]:
    """FM_3_1: return before gRPC call executes."""
    if FAULT_MODE == "FM_3_1":
        log.warning("[FAULT FM_3_1] Premature termination — returning before conversion")
        return {
            "currency_code": "PREMATURE",
            "units": 0,
            "nanos": 0,
            "premature_termination": True,
        }
    return None


def maybe_tamper_amount(units: int, nanos: int) -> tuple[int, int]:
    """FM_2_5: replace amount with wrong value before conversion."""
    if FAULT_MODE == "FM_2_5":
        tampered = units * 5 if units > 0 else 9999
        log.warning(f"[FAULT FM_2_5] Amount tampered: {units}.{nanos} → {tampered}.{nanos}")
        return tampered, nanos
    return units, nanos


def maybe_swap_currency(to_currency: str) -> str:
    """FM_1_2: route to wrong target currency."""
    if FAULT_MODE == "FM_1_2":
        wrong = "JPY" if to_currency != "JPY" else "BRL"
        log.warning(f"[FAULT FM_1_2] Currency swapped: {to_currency} → {wrong}")
        return wrong
    return to_currency


def maybe_hallucinate_result(real_result: dict) -> dict:
    """FM_2_2: replace real conversion with fabricated data."""
    if FAULT_MODE == "FM_2_2":
        log.warning("[FAULT FM_2_2] Hallucinated conversion result injected")
        return {
            "currency_code": real_result.get("currency_code", "EUR"),
            "units": 1337,
            "nanos": 420000000,
            "hallucinated": True,
            "original_suppressed": real_result,
        }
    return real_result


def maybe_manipulate_rate(result: dict) -> dict:
    """BL_RATE_MANIPULATION: inflate converted amount 10x."""
    if FAULT_MODE == "BL_RATE_MANIPULATION":
        original = result.get("units", 0)
        try:
            # A str would concatenate rather than multiply: "19" * 10 -> "1919...".
            inflated = int(original) * 10
        except (TypeError, ValueError):
            # Flag it rather than return silently, so a skipped injection is not
            # scored as the detector missing a fault that was really applied.
            log.error(f"[FAULT BL_RATE_MANIPULATION] Non-numeric units {original!r}; injection skipped")
            result = dict(result)
            result["rate_injection_failed"] = True
            return result
        log.warning(f"[FAULT BL_RATE_MANIPULATION] Rate inflated: {original} → {inflated}")
        result = dict(result)
        result["units"] = inflated
        result["rate_manipulated"] = True
        result["original_units"] = original
    return result


def maybe_inject_stale_rate(result: dict) -> dict:
    """BL_STALE_RATE: replace live result with a hardcoded stale value."""
    if FAULT_MODE == "BL_STALE_RATE":
        log.warning("[FAULT BL_STALE_RATE] Stale cached rate injected")
        return {
            "currency_code": result.get("currency_code", "EUR"),
            "units": 0,
            "nanos": 850000000,
            "stale_rate": True,
            "cached_at": "2026-01-01T00:00:00Z",
        }
    return result


def maybe_overflow_result(result: dict) -> dict:
    """BL_CONVERSION_OVERFLOW: set amount to extreme overflow value."""
    if FAULT_MODE == "BL_CONVERSION_OVERFLOW":
        log.warning("[FAULT BL_CONVERSION_OVERFLOW] Overflow value injected")
        result = dict(result)
        result["units"] = 2**53
        result["nanos"] = 999999999
        result["overflow"] = True
    return result


def maybe_simulate_unavailable() -> Optional[Exception]:
    """BL_CURRENCY_UNAVAILABLE: simulate gRPC error for supported currency."""
    if FAULT_MODE == "BL_CURRENCY_UNAVAILABLE":
        log.warning("[FAULT BL_CURRENCY_UNAVAILABLE] Simulating unsupported currency error")
        return RuntimeError("Currency conversion unavailable: unsupported currency pair (fault injection)")
    return None
