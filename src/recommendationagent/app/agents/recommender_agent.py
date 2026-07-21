"""
RecommenderAgent
================
Deterministic sub-agent that generates product recommendations.

Given a list of product IDs currently in the cart, returns a set of
related products from the static catalog. No LLM, no gRPC.

Invoked as a tool by RecommendationOrchestrator.
"""
import random
import logging

log = logging.getLogger(__name__)

# Category adjacency — products in these categories cross-recommend
_CATEGORY_GRAPH: dict[str, list[str]] = {
    "accessories":  ["clothing", "footwear"],
    "clothing":     ["accessories", "footwear", "tops"],
    "tops":         ["clothing", "accessories"],
    "footwear":     ["clothing", "accessories"],
    "hair":         ["beauty"],
    "beauty":       ["hair"],
    "kitchen":      ["decor"],
    "decor":        ["kitchen"],
}

# Same static catalog as ProductCatalogAgent (kept in sync)
_CATALOG: list[dict] = [
    {"id": "OLJCESPC7Z", "categories": ["accessories"]},
    {"id": "66VCHSJNUP", "categories": ["clothing", "tops"]},
    {"id": "1YMWWN1N4O", "categories": ["accessories"]},
    {"id": "L9ECAV7KIM", "categories": ["footwear"]},
    {"id": "2ZYFJ3GM2N", "categories": ["hair", "beauty"]},
    {"id": "0PUK6V6EV0", "categories": ["decor"]},
    {"id": "LS4PSXUNUM", "categories": ["kitchen"]},
    {"id": "9SIQT8TOJO", "categories": ["kitchen"]},
    {"id": "6E92ZMYYFZ", "categories": ["kitchen"]},
]


class RecommenderAgent:
    """Returns product recommendations based on cart contents."""

    MAX_RECOMMENDATIONS = 5

    def recommend(self, product_ids: list, user_id: str = "") -> dict:
        """
        Generate recommendations.

        Args:
            product_ids: list of product IDs currently in the cart
            user_id:     optional user identifier (not used — stateless)

        Returns:
            dict with keys:
              - product_ids: list[str]  (recommended IDs, excluding cart items)
        """
        cart_set = set(product_ids or [])

        # Find categories of cart items
        related_categories: set[str] = set()
        for item in _CATALOG:
            if item["id"] in cart_set:
                for cat in item["categories"]:
                    related_categories.add(cat)
                    for adj in _CATEGORY_GRAPH.get(cat, []):
                        related_categories.add(adj)

        # Score candidates
        candidates = []
        for item in _CATALOG:
            if item["id"] in cart_set:
                continue
            score = sum(1 for c in item["categories"] if c in related_categories)
            if score > 0:
                candidates.append((score, item["id"]))

        candidates.sort(key=lambda x: -x[0])
        recs = [pid for _, pid in candidates[: self.MAX_RECOMMENDATIONS]]

        # Fallback: fill remaining slots with random non-cart products
        if len(recs) < self.MAX_RECOMMENDATIONS:
            remaining = [
                p["id"] for p in _CATALOG
                if p["id"] not in cart_set and p["id"] not in recs
            ]
            random.shuffle(remaining)
            recs += remaining[: self.MAX_RECOMMENDATIONS - len(recs)]

        log.info(
            "RecommenderAgent: cart=%s → %d recs", list(cart_set), len(recs)
        )
        return {"product_ids": recs}
