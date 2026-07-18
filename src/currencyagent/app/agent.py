import logging
import importlib
from typing import Optional
from app.grpc_client import CurrencyGrpcClient
import app.fault_injection as fi

try:
    from boundary_validation import boundary_contract
except ImportError:  # pragma: no cover - fallback for isolated agent execution
    def boundary_contract(name, expected, observed, **_kwargs):
        return {
            "boundary": name,
            "alert": expected != observed,
            "status": "signal_escape" if expected != observed else "clean",
            "expected": expected,
            "observed": observed,
            "difference": None,
            "detail": None,
            "violations": [],
        }

logger = logging.getLogger(__name__)

client = CurrencyGrpcClient()


class CurrencyAgent:
    def run(self, query: str, action: str = "get_supported_currencies",
            from_currency: str = "USD", units: int = 0,
            nanos: int = 0, to_currency: str = "EUR",
            handoff_contract: Optional[dict] = None):

        logger.info(f"CurrencyAgent.run called | action={action} | query='{query}'")
        lkw = fi.LKWCheckpoint()

        lkw.record("TASK_START", {
            "action": action,
            "from_currency": from_currency,
            "to_currency": to_currency,
            "units": units,
            "nanos": nanos,
            "fault_mode": fi.FAULT_MODE,
        })

        # FM_3_1: premature termination before gRPC call
        early = fi.maybe_premature_termination()
        if early:
            lkw.record("FINAL_ANSWER", early)
            logger.warning(f"[LKW RIP] {lkw.rip_summary()}")
            return {"mode": "agent", "action": action, "data": early, "lkw": lkw.checkpoints}

        q = query.lower().strip()

        if action == "convert" or "convert" in q or "to" in q:
            # FM_2_5: tamper amount before conversion
            units, nanos = fi.maybe_tamper_amount(units, nanos)
            # FM_1_2: swap target currency
            to_currency = fi.maybe_swap_currency(to_currency)

            # BL_CURRENCY_UNAVAILABLE: simulate gRPC error
            err = fi.maybe_simulate_unavailable()
            if err:
                lkw.record("FINAL_ANSWER", {
                    "unavailable": True,
                    "error": str(err),
                })
                logger.info(f"[LKW RIP] {lkw.rip_summary()}")
                return {"mode": "agent", "action": "convert", "data": {"error": str(err)}, "lkw": lkw.checkpoints}

            logger.info(f"Executing convert: {units}.{nanos} {from_currency} -> {to_currency}")
            data = client.convert(
                currency_code=from_currency,
                units=units,
                nanos=nanos,
                to_code=to_currency,
            )

            # FM_2_2: hallucinate result
            data = fi.maybe_hallucinate_result(data)
            # BL_RATE_MANIPULATION: inflate rate
            data = fi.maybe_manipulate_rate(data)
            # BL_STALE_RATE: replace with stale value
            data = fi.maybe_inject_stale_rate(data)
            # BL_CONVERSION_OVERFLOW: inject overflow
            data = fi.maybe_overflow_result(data)

            if handoff_contract is not None:
                boundary = boundary_contract(
                    handoff_contract.get("boundary", "currency_to_payment"),
                    handoff_contract.get("expected"),
                    data.get("units"),
                    validators={"__value__": lambda value: isinstance(value, (int, float)) and value >= 0},
                )
                lkw.record("BOUNDARY_CHECK", {
                    "boundary": boundary["boundary"],
                    "alert": boundary["alert"],
                    "status": boundary["status"],
                    "expected": boundary["expected"],
                    "observed": boundary["observed"],
                    "difference": boundary["difference"],
                    "detail": boundary["detail"],
                    "violations": boundary["violations"],
                })
                recovery = boundary.get("recovery") or {}
                if recovery.get("action") == "block_and_request_hitl":
                    lkw.record("RECOVERY_ACTION", recovery)
                    lkw.record("FINAL_ANSWER", {
                        "blocked": True,
                        "reason": recovery.get("reason"),
                        "requires_hitl": recovery.get("requires_hitl", True),
                    })
                    logger.warning("[CurrencyAgent] Boundary recovery blocked unsafe conversion: %s", recovery.get("reason"))
                    return {
                        "mode": "agent",
                        "action": "convert",
                        "data": {
                            "blocked": True,
                            "requires_hitl": recovery.get("requires_hitl", True),
                            "reason": recovery.get("reason"),
                        },
                        "lkw": lkw.checkpoints,
                    }

            lkw.record("CONVERT_DONE", {
                "from_currency": from_currency,
                "to_currency": to_currency,
                "units_in": units,
                "units_out": data.get("units"),
                "hallucinated": data.get("hallucinated", False),
                "rate_manipulated": data.get("rate_manipulated", False),
                "stale_rate": data.get("stale_rate", False),
                "overflow": data.get("overflow", False),
                "amount_tampered": fi.FAULT_MODE == "FM_2_5",
                "currency_swapped": fi.FAULT_MODE == "FM_1_2",
            })

            lkw.record("FINAL_ANSWER", {"result": data})
            logger.info(f"[LKW RIP] {lkw.rip_summary()}")
            return {"mode": "agent", "action": "convert", "data": data, "lkw": lkw.checkpoints}

        logger.info("Executing get_supported_currencies")
        data = client.get_supported_currencies()
        lkw.record("CONVERT_DONE", {"currencies_count": len(data)})
        lkw.record("FINAL_ANSWER", {"currencies": data})
        logger.info(f"[LKW RIP] {lkw.rip_summary()}")
        return {"mode": "agent", "action": "get_supported_currencies", "data": data, "lkw": lkw.checkpoints}