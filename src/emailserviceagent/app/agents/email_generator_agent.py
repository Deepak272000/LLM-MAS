"""
EmailGeneratorAgent
===================
Sub-agent that generates order-confirmation email content.

Uses a template (no LLM needed here — the LLM orchestrator handles
routing). The generated body is passed to EmailDeliveryAgent.

Invoked as a tool by EmailOrchestrator.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class EmailGeneratorAgent:
    """Generates a formatted order-confirmation email."""

    def generate(
        self,
        order_id: str,
        customer_name: str,
        customer_email: str,
        items: list,
        total_cost: dict,
        shipping_tracking_id: str = "",
    ) -> dict:
        """
        Generate order confirmation email content.

        Args:
            order_id:             order identifier
            customer_name:        recipient name
            customer_email:       recipient email address
            items:                list of order items (dicts with product, quantity, cost)
            total_cost:           dict with currency_code, units, nanos
            shipping_tracking_id: optional tracking ID

        Returns:
            dict with keys:
              - subject: str
              - body: str
              - to: str
              - from_address: str
        """
        currency = total_cost.get("currency_code", "USD")
        units    = total_cost.get("units", 0)
        nanos    = total_cost.get("nanos", 0)
        amount   = f"{units}.{nanos // 10_000_000:02d}"

        item_lines = "\n".join(
            f"  - {item.get('product_id', 'item')} × {item.get('quantity', 1)}"
            for item in (items or [])
        )

        tracking_line = (
            f"\nTracking ID: {shipping_tracking_id}"
            if shipping_tracking_id
            else ""
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        subject = f"Your Order Confirmation #{order_id}"
        body = (
            f"Hi {customer_name},\n\n"
            f"Thank you for your order! Here's your confirmation:\n\n"
            f"Order ID:   {order_id}\n"
            f"Date:       {timestamp}\n"
            f"Total:      {currency} {amount}\n"
            f"{tracking_line}\n\n"
            f"Items ordered:\n{item_lines}\n\n"
            f"If you have any questions, reply to this email.\n\n"
            f"Best regards,\nOnline Boutique"
        )

        log.info(
            "EmailGeneratorAgent: generated confirmation for order=%s to=%s",
            order_id, customer_email
        )

        return {
            "subject":      subject,
            "body":         body,
            "to":           customer_email,
            "from_address": "noreply@onlineboutique.example",
        }
