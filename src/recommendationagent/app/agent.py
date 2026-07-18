import importlib
from app.grpc_client import RecommendationGrpcClient
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

client = RecommendationGrpcClient()


class RecommendationAgent:
    def _fetch(self, user_id: str, product_ids: list[str], action: str,
               handoff_contract: dict | None = None) -> dict:
        fi.clear_lkw()
        fi.record_checkpoint("TASK_START", {
            "user_id": user_id,
            "product_ids": product_ids,
            "action": action,
            "fault_mode": fi.FAULT_MODE,
        })

        if handoff_contract is not None:
            boundary = boundary_contract(
                handoff_contract.get("boundary", "upstream_handoff"),
                handoff_contract.get("expected"),
                product_ids,
                required_keys=[],
                validators={
                    "__value__": lambda value: isinstance(value, list) and all(
                        isinstance(item, str) and item.startswith("PROD-")
                        for item in value
                    ),
                },
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
                "recovery": boundary.get("recovery"),
            })
            recovery = boundary.get("recovery") or {}
            if recovery.get("action") == "fallback_to_last_known_good":
                corrected = recovery.get("corrected_payload")
                if isinstance(corrected, list):
                    fi.record_checkpoint("RECOVERY_ACTION", recovery)
                    product_ids = corrected

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early is not None:
            fi.record_checkpoint("FINAL_ANSWER", {"premature_termination": True})
            return {"mode": "agent", "action": action, "user_id": user_id,
                    "input_product_ids": product_ids, "recommended_product_ids": early,
                    "lkw": fi.get_lkw()}

        # FM_2_5: swap user_id
        user_id, user_id_swapped = fi.maybe_swap_user_id(user_id)

        recommended_ids = client.list_recommendations(
            user_id=user_id,
            product_ids=product_ids,
        )

        # FM_2_2: hallucinate IDs
        recommended_ids, hallucinated = fi.maybe_hallucinate_recs(recommended_ids)
        # BL_EMPTY_RECS
        recommended_ids, empty_recs = fi.maybe_empty_recs(recommended_ids)
        # BL_SELF_RECOMMENDATION
        recommended_ids, self_rec = fi.maybe_self_recommendation(recommended_ids, product_ids)
        # BL_INJECTION_RECS
        recommended_ids, injection = fi.maybe_inject_sponsored(recommended_ids)
        # BL_SHUFFLED_RECS
        recommended_ids, shuffled = fi.maybe_shuffle_recs(recommended_ids)

        fi.record_checkpoint("RECOMMEND_DONE", {
            "action": action,
            "count": len(recommended_ids),
            "hallucinated": hallucinated,
            "user_id_swapped": user_id_swapped,
            "method_swapped": fi.FAULT_MODE == "FM_1_2",
            "empty_recs": empty_recs,
            "self_rec": self_rec,
            "injection": injection,
            "shuffled": shuffled,
        })
        fi.record_checkpoint("FINAL_ANSWER", {"count": len(recommended_ids)})

        return {
            "mode": "agent",
            "action": action,
            "user_id": user_id,
            "input_product_ids": product_ids,
            "recommended_product_ids": recommended_ids,
            "lkw": fi.get_lkw(),
        }

    def get_recommendations(self, user_id: str, product_ids: list[str],
                            handoff_contract: dict | None = None) -> dict:
        """Fetch recommended product IDs for a user given their current product context."""
        action = "get_recommendations"
        if fi.FAULT_MODE == "FM_1_2":
            action = "explain_recommendations"
        return self._fetch(user_id, product_ids, action, handoff_contract=handoff_contract)

    def explain_recommendations(self, user_id: str, product_ids: list[str],
                                handoff_contract: dict | None = None) -> dict:
        """
        Fetch recommendations and return raw data so the LLM node can
        generate a human-readable explanation downstream.
        """
        action = "explain_recommendations"
        if fi.FAULT_MODE == "FM_1_2":
            action = "get_recommendations"
        return self._fetch(user_id, product_ids, action, handoff_contract=handoff_contract)