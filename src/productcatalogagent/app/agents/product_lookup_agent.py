"""
ProductCatalogAgent (sub-agent)
================================
Deterministic sub-agent that serves product data from a static catalog.

Provides three operations: list_products, search_products, get_product.
No LLM, no gRPC — invoked as a tool by ProductCatalogOrchestrator.
"""
import logging

log = logging.getLogger(__name__)

# Static product catalog — mirrors the Google Online Boutique demo products
CATALOG: list[dict] = [
    {
        "id": "OLJCESPC7Z",
        "name": "Sunglasses",
        "description": "Add a modern touch to your outfits with these sleek aviator sunglasses.",
        "picture": "/static/img/products/sunglasses.jpg",
        "price_usd": {"currency_code": "USD", "units": 19, "nanos": 990000000},
        "categories": ["accessories"],
    },
    {
        "id": "66VCHSJNUP",
        "name": "Tank Top",
        "description": "Perfectly cropped cotton tank, with a scooped neckline.",
        "picture": "/static/img/products/tank-top.jpg",
        "price_usd": {"currency_code": "USD", "units": 18, "nanos": 990000000},
        "categories": ["clothing", "tops"],
    },
    {
        "id": "1YMWWN1N4O",
        "name": "Watch",
        "description": "This stainless steel watch is perfect for those who want a clean look.",
        "picture": "/static/img/products/watch.jpg",
        "price_usd": {"currency_code": "USD", "units": 109, "nanos": 990000000},
        "categories": ["accessories"],
    },
    {
        "id": "L9ECAV7KIM",
        "name": "Loafers",
        "description": "A perfect kick-back shoe, but not too casual for the office.",
        "picture": "/static/img/products/loafers.jpg",
        "price_usd": {"currency_code": "USD", "units": 89, "nanos": 990000000},
        "categories": ["footwear"],
    },
    {
        "id": "2ZYFJ3GM2N",
        "name": "Hairdryer",
        "description": "This lightweight blow dryer has 3 heat and speed settings.",
        "picture": "/static/img/products/hairdryer.jpg",
        "price_usd": {"currency_code": "USD", "units": 24, "nanos": 990000000},
        "categories": ["hair", "beauty"],
    },
    {
        "id": "0PUK6V6EV0",
        "name": "Candle Holder",
        "description": "This modern candle holder creates a beautiful warm glow.",
        "picture": "/static/img/products/candle-holder.jpg",
        "price_usd": {"currency_code": "USD", "units": 18, "nanos": 990000000},
        "categories": ["decor"],
    },
    {
        "id": "LS4PSXUNUM",
        "name": "Salt & Pepper Shakers",
        "description": "These pretty glass shakers make a classic kitchen staple.",
        "picture": "/static/img/products/salt-and-pepper-shakers.jpg",
        "price_usd": {"currency_code": "USD", "units": 18, "nanos": 490000000},
        "categories": ["kitchen"],
    },
    {
        "id": "9SIQT8TOJO",
        "name": "Bamboo Glass Jar",
        "description": "This bamboo glass jar is perfect for storing dry goods.",
        "picture": "/static/img/products/bamboo-glass-jar.jpg",
        "price_usd": {"currency_code": "USD", "units": 5, "nanos": 490000000},
        "categories": ["kitchen"],
    },
    {
        "id": "6E92ZMYYFZ",
        "name": "Mug",
        "description": "A simple mug with a steadily increasing angle as it extends upward.",
        "picture": "/static/img/products/mug.jpg",
        "price_usd": {"currency_code": "USD", "units": 8, "nanos": 990000000},
        "categories": ["kitchen"],
    },
]

_CATALOG_BY_ID: dict[str, dict] = {p["id"]: p for p in CATALOG}


class ProductLookupAgent:
    """Serves product data from the static catalog."""

    def list_products(self) -> dict:
        log.info("ProductLookupAgent: listing all %d products", len(CATALOG))
        return {"products": CATALOG}

    def search_products(self, query: str) -> dict:
        q = query.lower().strip()
        results = [
            p for p in CATALOG
            if q in p["name"].lower()
            or q in p["description"].lower()
            or any(q in c.lower() for c in p.get("categories", []))
        ]
        log.info(
            "ProductLookupAgent: search '%s' → %d results", query, len(results)
        )
        return {"products": results}

    def get_product(self, product_id: str) -> dict:
        product = _CATALOG_BY_ID.get(product_id)
        if product is None:
            log.warning("ProductLookupAgent: product not found: %s", product_id)
            return {"error": f"Product not found: {product_id}", "product": None}
        log.info("ProductLookupAgent: found product %s", product_id)
        return {"product": product}
