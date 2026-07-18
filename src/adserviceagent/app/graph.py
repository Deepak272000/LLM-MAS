from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END

from app.agent import prepare_agent_input
from app.grpc_client import AdServiceClient
from app.llm.qwen import get_qwen_llm
import app.fault_injection as fi
import os

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

llm = get_qwen_llm()

class AdAgentState(TypedDict, total=False):
    instruction: str
    context_keys: List[str]
    reasoning: str
    ads: List[Dict[str, Any]]
    handoff_contract: Dict[str, Any]
    final_response: Dict[str, Any]


def input_node(state: AdAgentState) -> AdAgentState:
    fi.clear_lkw()

    fi.record_checkpoint("TASK_START", {
        "instruction": state.get("instruction", "")[:80],
        "fault_mode": fi.FAULT_MODE,
    })

    prepared = prepare_agent_input(state)
    instruction = prepared["instruction"]

    # ✅ Only call LLM if enabled
    if os.getenv("USE_LLM", "false").lower() == "true":
        prompt = f"""
You are an ad selection assistant.

User request: {instruction}

Extract relevant ad categories from this request.
Return a comma separated list like:
electronics, clothing, footwear

Only return categories.
""".strip()

        response = llm.invoke(prompt)

        #  TOKEN LOGGING
        input_tokens = response.usage_metadata.get("input_tokens", 0)
        output_tokens = response.usage_metadata.get("output_tokens", 0)
        total_tokens = response.usage_metadata.get("total_tokens", 0)

        print(f"TOKEN_METRICS input={input_tokens} output={output_tokens} total={total_tokens}")

        with open("token_log.txt", "a") as f:
            f.write(f"{total_tokens}\n")

        categories = [c.strip() for c in response.content.lower().split(",")]

        state["context_keys"] = categories
        state["reasoning"] = "LLM-based category extraction"
    else:
        state["context_keys"] = prepared["context_keys"]
        state["reasoning"] = prepared["reasoning"]

    state["instruction"] = instruction

    # FM_2_5: tamper context keys
    state["context_keys"], context_tampered = fi.maybe_tamper_context(state["context_keys"])
    # FM_1_2: swap categories
    state["context_keys"], category_swapped = fi.maybe_swap_category(state["context_keys"])

    fi.record_checkpoint("CONTEXT_EXTRACTED", {
        "context_keys": state["context_keys"],
        "context_tampered": context_tampered,
        "category_swapped": category_swapped,
    })

    # FM_3_1: mark premature termination in state
    if fi.maybe_premature_termination():
        state["ads"] = []
        state["final_response"] = {"premature_termination": True}
        fi.record_checkpoint("FINAL_ANSWER", {"premature_termination": True})

    return state


def ad_lookup_node(state: AdAgentState) -> AdAgentState:
    # FM_3_1 guard: skip if premature termination already flagged
    if state.get("final_response", {}).get("premature_termination"):
        return state

    client = AdServiceClient()
    ads = client.get_ads(state.get("context_keys", []))

    # FM_2_2: hallucinate ads
    ads, hallucinated = fi.maybe_hallucinate_ads(ads)
    # BL_EMPTY_ADS
    ads, empty_ads = fi.maybe_empty_ads(ads)
    # BL_AD_INJECTION
    ads, injected = fi.maybe_inject_ads(ads)
    # BL_WRONG_URL
    ads, wrong_url = fi.maybe_wrong_url(ads)
    # BL_DUPLICATE_ADS
    ads, duplicated = fi.maybe_duplicate_ad(ads)

    state["ads"] = ads

    observed_ads = [
        {
            "redirect_url": ad.get("redirect_url"),
            "text": ad.get("text"),
        }
        for ad in ads
        if isinstance(ad, dict)
    ]
    handoff_contract = state.get("handoff_contract")
    if handoff_contract is not None:
        boundary = boundary_contract(
            handoff_contract.get("boundary", "ad_lookup_to_response"),
            handoff_contract.get("expected", observed_ads),
            observed_ads,
            validators={
                "__list__": lambda value: all(
                    isinstance(item, dict)
                    and isinstance(item.get("redirect_url"), str)
                    and item.get("redirect_url")
                    and isinstance(item.get("text"), str)
                    and item.get("text")
                    for item in value
                )
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
        })
        recovery = boundary.get("recovery") or {}
        if recovery.get("action") == "fallback_to_last_known_good":
            corrected = recovery.get("corrected_payload", [])
            fi.record_checkpoint("RECOVERY_ACTION", recovery)
            state["ads"] = corrected if isinstance(corrected, list) else []

    fi.record_checkpoint("ADS_FETCHED", {
        "count": len(ads),
        "observed_ads": observed_ads,
        "hallucinated": hallucinated,
        "empty_ads": empty_ads,
        "injected": injected,
        "wrong_url": wrong_url,
        "duplicated": duplicated,
    })
    return state


def output_node(state: AdAgentState) -> AdAgentState:
    # FM_3_1 guard
    if state.get("final_response", {}).get("premature_termination"):
        return state

    state["final_response"] = {
        "ads": state.get("ads", []),
        "used_context_keys": state.get("context_keys", []),
        "reasoning": state.get("reasoning", "Ad selection completed.")
    }
    fi.record_checkpoint("FINAL_ANSWER", {"ads_count": len(state.get("ads", []))})
    return state


def build_graph():
    graph = StateGraph(AdAgentState)

    graph.add_node("input_node", input_node)
    graph.add_node("ad_lookup_node", ad_lookup_node)
    graph.add_node("output_node", output_node)

    graph.set_entry_point("input_node")
    graph.add_edge("input_node", "ad_lookup_node")
    graph.add_edge("ad_lookup_node", "output_node")
    graph.add_edge("output_node", END)

    return graph.compile()