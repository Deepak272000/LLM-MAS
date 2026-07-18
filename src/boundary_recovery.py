"""Policy decisions for boundary alert recovery.

This layer turns boundary detection into a next-step decision. It does not
silently mutate production state; high-risk flows still escalate to HITL.
"""

from __future__ import annotations

from typing import Any


def decide_recovery(boundary_result: dict[str, Any]) -> dict[str, Any]:
    """Return the safest recovery action for a boundary check result."""
    if not boundary_result.get("alert"):
        return {
            "action": "continue",
            "should_continue": True,
            "requires_hitl": False,
            "reason": "boundary check is clean",
        }

    boundary = boundary_result.get("boundary")
    expected = boundary_result.get("expected")

    if boundary == "currency_to_payment":
        return {
            "action": "block_and_request_hitl",
            "should_continue": False,
            "requires_hitl": True,
            "reason": "financial amount mismatch must not be auto-corrected before charge",
        }

    if boundary == "catalog_to_recommendation":
        return {
            "action": "fallback_to_last_known_good",
            "should_continue": True,
            "requires_hitl": False,
            "reason": "invalid product IDs can be replaced with the last validated catalog payload",
            "corrected_payload": expected,
        }

    if boundary == "carrier_to_tracking":
        return {
            "action": "fallback_to_last_known_good",
            "should_continue": True,
            "requires_hitl": False,
             "reason": "invalid carrier/service level can fall back to the last valid shipping selection",
            "corrected_payload": {"carrier": "FedEx", "service_level": "ground"},
        }

    if boundary == "quote_to_carrier_selection":
        return {
            "action": "retry_current_step",
            "should_continue": False,
            "requires_hitl": False,
            "reason": "carrier selection should be retried because downstream quote selection changed unexpectedly",
        }

    if boundary == "quote_to_carrier":
        return {
            "action": "retry_current_step",
            "should_continue": False,
            "requires_hitl": False,
            "reason": "quote mismatch is recoverable by rerunning quote capture before carrier selection",
        }

    if boundary == "ad_lookup_to_response":
        return {
            "action": "fallback_to_last_known_good",
            "should_continue": True,
            "requires_hitl": False,
            "reason": "invalid ads replaced with empty list to avoid serving injected or malformed ads",
            "corrected_payload": [],
        }

    if boundary == "email_generation_to_send":
        return {
            "action": "block_and_request_hitl",
            "should_continue": False,
            "requires_hitl": True,
            "reason": "corrupted or wrong-recipient email must not be auto-sent; human review required",
        }

    return {
        "action": "block_and_request_hitl",
        "should_continue": False,
        "requires_hitl": True,
        "reason": "unknown boundary alert requires human review before continuing",
    }