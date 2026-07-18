import importlib
from app.grpc_client import ProductCatalogGrpcClient
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

client = ProductCatalogGrpcClient()


class ProductCatalogAgent:
    def run(self, query: str, product_ids=None, handoff_contract: dict | None = None):
        fi.clear_lkw()
        q = query.lower().strip()
        action = "get_product" if product_ids else (
            "list_products" if ("all products" in q or "list products" in q
                                or "show products" in q or q == "catalog")
            else "search_products"
        )

        fi.record_checkpoint("TASK_START", {
            "query": query,
            "product_ids": product_ids,
            "action": action,
            "fault_mode": fi.FAULT_MODE,
        })

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early is not None:
            fi.record_checkpoint("FINAL_ANSWER", {"premature_termination": True})
            return {"mode": "agent", "action": action, "data": early,
                    "lkw": fi.get_lkw()}

        # FM_2_5: tamper query
        query, query_tampered = fi.maybe_tamper_query(query)
        q = query.lower().strip()

        # FM_1_2: swap action
        action = fi.maybe_swap_action(action)

        if product_ids:
            data = [client.get_product(pid) for pid in product_ids]
        elif action == "list_products":
            data = client.list_products()
        else:
            data = client.search_products(query)

        # FM_2_2: hallucinate product data
        data, hallucinated = fi.maybe_hallucinate_products(data)
        # BL_PRODUCT_MISSING
        data, products_missing = fi.maybe_remove_products(data)
        # BL_PRICE_MANIPULATION
        data, price_manipulated = fi.maybe_manipulate_prices(data)
        # BL_DUPLICATE_PRODUCT
        data, duplicated = fi.maybe_duplicate_product(data)
        # BL_WRONG_CATEGORY
        data, category_wrong = fi.maybe_wrong_category(data)

        observed_ids = [item.get("id") for item in data if isinstance(item, dict)]
        if handoff_contract is not None:
            boundary = boundary_contract(
                handoff_contract.get("boundary", "catalog_to_recommendation"),
                handoff_contract.get("expected"),
                observed_ids,
                validators={"__list__": lambda value: all(isinstance(item, str) and item for item in value)},
            )
            fi.record_checkpoint("BOUNDARY_CHECK", {
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
            if recovery.get("action") == "fallback_to_last_known_good":
                corrected = recovery.get("corrected_payload")
                if isinstance(corrected, list):
                    fi.record_checkpoint("RECOVERY_ACTION", recovery)
                    corrected_set = set(corrected)
                    data = [item for item in data if isinstance(item, dict) and item.get("id") in corrected_set]
                    observed_ids = [item.get("id") for item in data]

        fi.record_checkpoint("CATALOG_DONE", {
            "action": action,
            "count": len(data),
            "product_ids": observed_ids,
            "hallucinated": hallucinated,
            "query_tampered": query_tampered,
            "action_swapped": fi.FAULT_MODE == "FM_1_2",
            "products_missing": products_missing,
            "price_manipulated": price_manipulated,
            "duplicated": duplicated,
            "category_wrong": category_wrong,
        })

        fi.record_checkpoint("FINAL_ANSWER", {"count": len(data)})
        return {"mode": "agent", "action": action, "data": data,
                "lkw": fi.get_lkw()}