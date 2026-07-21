"""
ChargeAgent
===========
Deterministic sub-agent that computes a payment charge.

Takes an amount (units + nanos in a given currency) and returns a
transaction_id and confirmation. No LLM, no gRPC — pure Python math.

Invoked as a tool by PaymentOrchestrator.
"""
import uuid
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class ChargeAgent:
    """Processes a payment charge and returns a transaction record."""

    def charge(
        self,
        currency_code: str,
        units: int,
        nanos: int,
        card_last4: str,
        card_type: str,
    ) -> dict:
        """
        Process a charge.

        Args:
            currency_code: ISO 4217 currency code, e.g. "USD"
            units:         whole units of the currency
            nanos:         fractional nanoseconds (0–999_999_999)
            card_last4:    last 4 digits of card (for logging only)
            card_type:     "visa" or "mastercard"

        Returns:
            dict with keys:
              - transaction_id: str  (UUID)
              - amount_usd: float
              - currency_code: str
              - card_last4: str
              - charged_at: str  (ISO timestamp)
              - status: "success"
        """
        # Convert to float for logging / oracle comparison
        amount_decimal = round(units + nanos / 1_000_000_000, 9)

        transaction_id = str(uuid.uuid4())
        charged_at     = datetime.now(timezone.utc).isoformat()

        log.info(
            "ChargeAgent: charged %s %.9f %s — txn=%s",
            card_type, amount_decimal, currency_code, transaction_id
        )

        return {
            "transaction_id": transaction_id,
            "amount":         amount_decimal,
            "currency_code":  currency_code,
            "card_last4":     card_last4,
            "charged_at":     charged_at,
            "status":         "success",
        }
