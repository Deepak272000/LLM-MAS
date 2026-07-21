"""
CardValidationAgent
===================
Deterministic sub-agent that validates a credit card.

Checks:
  - Card number is digits only, 12–19 chars
  - Card type is VISA or MasterCard
  - Expiry has not passed
  - CVV is 3 digits

No LLM needed — invoked as a tool by PaymentOrchestrator.
"""
import re
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class CardValidationAgent:
    """Validates credit card data without any LLM or gRPC call."""

    ACCEPTED_TYPES = {"visa", "mastercard"}

    def _get_card_type(self, number: str) -> str:
        n = number.replace("-", "").replace(" ", "")
        if re.match(r"^4", n):
            return "visa"
        if re.match(r"^5[1-5]", n):
            return "mastercard"
        if re.match(r"^3[47]", n):
            return "amex"
        return "unknown"

    def validate(
        self,
        credit_card_number: str,
        credit_card_cvv: int,
        credit_card_expiration_year: int,
        credit_card_expiration_month: int,
    ) -> dict:
        """
        Validate a credit card.

        Returns:
            dict with keys:
              - valid: bool
              - card_type: str
              - card_last4: str
              - error: str or None
        """
        number = str(credit_card_number).replace("-", "").replace(" ", "")

        if not number.isdigit() or not (12 <= len(number) <= 19):
            log.warning("CardValidationAgent: invalid card number format")
            return {"valid": False, "card_type": "unknown",
                    "card_last4": number[-4:] if len(number) >= 4 else "????",
                    "error": "Credit card info is invalid"}

        card_type = self._get_card_type(number)
        if card_type not in self.ACCEPTED_TYPES:
            log.warning("CardValidationAgent: unsupported card type %s", card_type)
            return {"valid": False, "card_type": card_type,
                    "card_last4": number[-4:],
                    "error": f"Sorry, we cannot process {card_type} credit cards. "
                             "Only VISA or MasterCard is accepted."}

        now = datetime.utcnow()
        exp_year  = int(credit_card_expiration_year)
        exp_month = int(credit_card_expiration_month)
        if (exp_year < now.year) or (exp_year == now.year and exp_month < now.month):
            log.warning("CardValidationAgent: card expired %d/%d", exp_month, exp_year)
            return {"valid": False, "card_type": card_type,
                    "card_last4": number[-4:],
                    "error": f"Your credit card (ending {number[-4:]}) expired on "
                             f"{exp_month}/{exp_year}"}

        cvv_str = str(credit_card_cvv)
        if not cvv_str.isdigit() or len(cvv_str) != 3:
            log.warning("CardValidationAgent: invalid CVV")
            return {"valid": False, "card_type": card_type,
                    "card_last4": number[-4:],
                    "error": "Credit card CVV is invalid"}

        log.info("CardValidationAgent: card %s valid (%s)", number[-4:], card_type)
        return {
            "valid":      True,
            "card_type":  card_type,
            "card_last4": number[-4:],
            "error":      None,
        }
