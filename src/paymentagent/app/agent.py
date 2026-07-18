import logging
from app.repository import save_transaction
from app.tools import charge_payment, CreditCardError
import app.fault_injection as fi

try:
    from boundary_validation import boundary_contract
except ImportError:  # pragma: no cover - fallback for isolated agent execution
    def boundary_contract(name, expected, observed):
        return {
            "boundary": name,
            "alert": expected != observed,
            "status": "signal_escape" if expected != observed else "clean",
            "expected": expected,
            "observed": observed,
            "difference": None,
        }

logger = logging.getLogger("payment-agent")


class PaymentAgent:
    async def run(
        self,
        query: str,
        currency_code: str = "USD",
        units: int = 0,
        nanos: int = 0,
        credit_card_number: str = "",
        credit_card_cvv: int = 0,
        credit_card_expiration_year: int = 0,
        credit_card_expiration_month: int = 0,
        handoff_contract: dict | None = None,
    ):
        logger.info(f"[PaymentAgent] Received query: {query}")
        lkw = fi.LKWCheckpoint()

        lkw.record("TASK_START", {
            "currency_code": currency_code,
            "units": units,
            "nanos": nanos,
            "card_last4": credit_card_number[-4:] if credit_card_number else "****",
            "fault_mode": fi.FAULT_MODE,
        })

        if handoff_contract is not None:
            boundary = boundary_contract(
                handoff_contract.get("boundary", "upstream_handoff"),
                handoff_contract.get("expected"),
                {"currency_code": currency_code, "units": units, "nanos": nanos},
                required_keys=["currency_code", "units", "nanos"],
                validators={
                    "units": lambda value: isinstance(value, int) and value >= 0,
                    "nanos": lambda value: isinstance(value, int) and 0 <= value < 1_000_000_000,
                    "currency_code": lambda value: isinstance(value, str) and len(value) == 3,
                },
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
                "recovery": boundary.get("recovery"),
            })
            recovery = boundary.get("recovery") or {}
            if recovery.get("action") == "block_and_request_hitl":
                lkw.record("RECOVERY_ACTION", recovery)
                lkw.record("FINAL_ANSWER", {
                    "blocked": True,
                    "reason": recovery.get("reason"),
                    "requires_hitl": recovery.get("requires_hitl", True),
                })
                logger.warning("[PaymentAgent] Boundary recovery blocked unsafe charge: %s", recovery.get("reason"))
                return {
                    "mode": "agent",
                    "action": "charge",
                    "data": {
                        "blocked": True,
                        "requires_hitl": recovery.get("requires_hitl", True),
                        "reason": recovery.get("reason"),
                    },
                    "lkw": lkw.checkpoints,
                }

        # FM_3_1: premature termination before any charge
        early = fi.maybe_premature_termination()
        if early:
            lkw.record("FINAL_ANSWER", early)
            logger.warning(f"[LKW RIP] {lkw.rip_summary()}")
            return {"mode": "agent", "action": "charge", "data": early, "lkw": lkw.checkpoints}

        # FM_2_5 / BL_AMOUNT_TAMPERING: replace amount before charge
        units, nanos = fi.maybe_tamper_amount(units, nanos)

        request = {
            "amount": {
                "currency_code": currency_code,
                "units": units,
                "nanos": nanos,
            },
            "credit_card": {
                "credit_card_number": credit_card_number,
                "credit_card_cvv": credit_card_cvv,
                "credit_card_expiration_year": credit_card_expiration_year,
                "credit_card_expiration_month": credit_card_expiration_month,
            },
            # FM_1_2: signal tools.py to skip validation
            "_bypass_validation": fi.maybe_bypass_validation(credit_card_number),
        }

        try:
            # BL_CARD_DECLINED: force decline before calling charge_payment
            if fi.maybe_force_decline():
                raise CreditCardError("Payment declined by issuer (fault injection)")

            result = charge_payment(request)

            # FM_2_2: replace transaction_id with hallucinated value
            result = fi.maybe_hallucinate_transaction(result)

            lkw.record("CARD_VALIDATED", {
                "card_last4": credit_card_number[-4:] if credit_card_number else "****",
                "validation_bypassed": request.get("_bypass_validation", False),
                "amount_tampered": fi.FAULT_MODE in ("FM_2_5", "BL_AMOUNT_TAMPERING"),
            })

            lkw.record("CHARGE_DONE", {
                "transaction_id": result.get("transaction_id"),
                "hallucinated": result.get("hallucinated", False),
                "units_charged": units,
                "nanos_charged": nanos,
            })

            # BL_TRANSACTION_LOST: skip save silently
            save_payload = {
                "transaction_id": result["transaction_id"],
                "currency_code": currency_code,
                "units": units,
                "nanos": nanos,
                "credit_card_last4": credit_card_number[-4:] if credit_card_number else "****",
                "status": "success",
            }
            save_payload = fi.maybe_inject_double_charge(save_payload)

            saved = False
            if not fi.maybe_skip_save():
                await save_transaction(**{k: v for k, v in save_payload.items()})
                saved = True

            lkw.record("SAVE_DONE", {
                "saved": saved,
                "transaction_id": result["transaction_id"],
                "save_skipped": not saved,
                "double_charge": save_payload.get("double_charge", False),
            })

            final = {
                "success": True,
                "transaction_id": result["transaction_id"],
            }
            lkw.record("FINAL_ANSWER", final)
            logger.info(f"[LKW RIP] {lkw.rip_summary()}")

            return {
                "mode": "agent",
                "action": "charge",
                "data": final,
                "lkw": lkw.checkpoints,
            }

        except CreditCardError as e:
            logger.warning(f"[PaymentAgent] Payment failed: {str(e)}")
            lkw.record("FINAL_ANSWER", {
                "success": False,
                "error": str(e),
                "forced_decline": fi.FAULT_MODE == "BL_CARD_DECLINED",
                "transaction_id": None,
            })
            logger.info(f"[LKW RIP] {lkw.rip_summary()}")

            return {
                "mode": "agent",
                "action": "charge",
                "data": {
                    "success": False,
                    "error": str(e),
                    "transaction_id": None,
                },
                "lkw": lkw.checkpoints,
            }