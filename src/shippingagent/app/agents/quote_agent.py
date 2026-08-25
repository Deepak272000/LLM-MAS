"""
QuoteAgent
==========
Deterministic sub-agent that estimates shipping cost in USD.

Algorithm mirrors the original Go implementation:
  - Base cost depends on total item count
  - Distance surcharge based on zip code or country
  - Returns a float USD amount
"""

import json
import logging
import math

log = logging.getLogger(__name__)


def _as_items(items):
    """Identity for a well-formed list of dicts; salvages anything else.

    Normally helper_payload repairs these upstream, so this only covers a call
    path that reaches the agent without passing through the orchestrator.
    """
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError:
            log.warning("QuoteAgent: unparseable items %r; treating as empty", items)
            return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _as_address(address):
    if isinstance(address, str):
        try:
            address = json.loads(address)
        except ValueError:
            return {}
    return address if isinstance(address, dict) else {}


class QuoteAgent:
    """
    Estimates the shipping cost for a given address and list of cart items.

    This agent is intentionally deterministic  no LLM needed here.
    It is invoked as a tool by the Claude orchestrator.
    """

    # Cost per item (USD)
    COST_PER_ITEM = 2.00
    # Base handling fee (USD)
    BASE_FEE = 3.50

    # Country surcharge map (flat surcharge in USD)
    COUNTRY_SURCHARGES = {
        "US": 0.00,
        "CA": 1.50,
        "GB": 3.00,
        "AU": 5.00,
        "DE": 4.00,
        "FR": 4.00,
        "JP": 6.00,
        "CN": 5.50,
        "IN": 4.50,
        "BR": 7.00,
        "MX": 3.50,
    }

    def estimate(self, address: dict, items: list) -> dict:
        """
        Estimate shipping cost.

        Args:
            address: dict with keys: street_address, city, state, country, zip_code
            items:   list of dicts with keys: product_id, quantity

        Returns:
            dict with keys:
              - cost_usd: float
              - breakdown: dict with fee components
        """
        total_items = sum(item.get("quantity", 1) for item in _as_items(items))
        country = (_as_address(address).get("country") or "US").upper().strip()

        item_cost = total_items * self.COST_PER_ITEM
        country_surcharge = self.COUNTRY_SURCHARGES.get(country, 8.00)

        # Weight-based estimate: simulate cubic weight from item count
        cubic_weight = math.ceil(total_items / 3) * 0.5  # kg equivalent
        weight_cost = cubic_weight * 0.75

        total = round(self.BASE_FEE + item_cost + country_surcharge + weight_cost, 2)

        log.info(
            f"QuoteAgent: {total_items} items ? ${total} USD "
            f"(country={country}, surcharge=${country_surcharge})"
        )

        return {
            "cost_usd": total,
            "breakdown": {
                "base_fee": self.BASE_FEE,
                "item_cost": round(item_cost, 2),
                "country_surcharge": country_surcharge,
                "weight_cost": round(weight_cost, 2),
            },
        }
