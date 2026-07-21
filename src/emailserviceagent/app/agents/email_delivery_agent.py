"""
EmailDeliveryAgent
==================
Sub-agent that simulates sending an email.

In the research benchmark context there is no real SMTP server;
this agent records a delivery receipt and returns success.
The send is observable via LKW checkpoints.

Invoked as a tool by EmailOrchestrator.
"""
import uuid
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class EmailDeliveryAgent:
    """Simulates email delivery and returns a delivery receipt."""

    def send(self, subject: str, body: str, to: str, from_address: str) -> dict:
        """
        Simulate sending an email.

        Args:
            subject:      email subject line
            body:         email body text
            to:           recipient address
            from_address: sender address

        Returns:
            dict with keys:
              - message_id: str  (simulated)
              - sent_at:    str  (ISO timestamp)
              - to:         str
              - status:     "sent"
        """
        message_id = str(uuid.uuid4())
        sent_at    = datetime.now(timezone.utc).isoformat()

        log.info(
            "EmailDeliveryAgent: sent message_id=%s to=%s subject='%s'",
            message_id, to, subject[:60]
        )

        return {
            "message_id": message_id,
            "sent_at":    sent_at,
            "to":         to,
            "status":     "sent",
        }
