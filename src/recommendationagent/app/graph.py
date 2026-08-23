import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from app.agent import RecommendationAgent
from app.llm.ollama import get_ollama_llm


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class RecommendationState(TypedDict):
    query: str                              # free-text user query (optional context)
    user_id: str                            # required: user to get recommendations for
    product_ids: list[str]                  # required: products currently in cart/view
    route: str                              # classify_request sets this
    raw_result: dict                        # run_agent sets this
    result: dict  
    total_input_tokens: int
    total_output_tokens: int
    total_llm_calls: int                          # final response (possibly LLM-enriched)
    handoff_contract: Optional[dict]


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

agent = RecommendationAgent()
llm = get_ollama_llm()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def add_token_metrics(state: RecommendationState, response):
    usage = getattr(response, "usage_metadata", {}) or {}

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

    print(f"TOKEN_METRICS input={input_tokens} output={output_tokens} total={total_tokens}")

    state["total_input_tokens"] = state.get("total_input_tokens", 0) + input_tokens
    state["total_output_tokens"] = state.get("total_output_tokens", 0) + output_tokens
    state["total_llm_calls"] = state.get("total_llm_calls", 0) + 1

    with open(os.environ.get("TOKEN_LOG", "token_log.txt"), "a") as f:
        f.write(f"{total_tokens}\n")

def attach_token_metrics(state: RecommendationState):
    state["result"]["total_input_tokens"] = state.get("total_input_tokens", 0)
    state["result"]["total_output_tokens"] = state.get("total_output_tokens", 0)
    state["result"]["total_llm_calls"] = state.get("total_llm_calls", 0)
    state["result"]["total_tokens"] = (
        state.get("total_input_tokens", 0) + state.get("total_output_tokens", 0)
    )

def classify_request(state: RecommendationState) -> RecommendationState:
    """
    Use the LLM to decide whether the caller wants:
      - just the raw recommended product IDs  → 'get_recommendations'
      - a human-friendly explanation           → 'explain_recommendations'

    Falls back to 'get_recommendations' when product_ids are supplied without
    a natural-language query.
    """
    query = state.get("query", "").strip()

    # Fast path: no query text → plain fetch
    if not query:
        state["route"] = "get_recommendations"
        state["total_input_tokens"] = state.get("total_input_tokens", 0)
        state["total_output_tokens"] = state.get("total_output_tokens", 0)
        state["total_llm_calls"] = state.get("total_llm_calls", 0)
        return state

    prompt = f"""
You are a router for a product recommendation service.
Classify the user request into exactly one label:
- get_recommendations   (caller wants a list of recommended product IDs)
- explain_recommendations (caller wants a human-readable explanation of why products are recommended)

Rules:
- If the query asks for reasons, explanations, descriptions, or "why", use explain_recommendations
- Otherwise default to get_recommendations

User query: {query}

Return only one label, nothing else.
""".strip()

    response = llm.invoke(prompt)

    add_token_metrics(state, response)

    label = response.content.strip().lower()

    if "explain" in label:
        state["route"] = "explain_recommendations"
    else:
        state["route"] = "get_recommendations"

    return state


def run_agent(state: RecommendationState) -> RecommendationState:
    """Dispatch to the appropriate agent method based on route."""
    user_id = state["user_id"]
    product_ids = state["product_ids"]
    handoff_contract = state.get("handoff_contract")

    if state["route"] == "explain_recommendations":
        raw = agent.explain_recommendations(
            user_id=user_id,
            product_ids=product_ids,
            handoff_contract=handoff_contract,
        )
        state["raw_result"] = raw
    else:
        raw = agent.get_recommendations(
            user_id=user_id,
            product_ids=product_ids,
            handoff_contract=handoff_contract,
        )
        state["raw_result"] = raw
        state["result"] = raw   # no LLM enrichment needed

    return state


def enrich_with_llm(state: RecommendationState) -> RecommendationState:
    """
    Only reached for 'explain_recommendations' route.
    Asks the LLM to produce a friendly explanation using the raw recommendation data.
    """
    raw = state["raw_result"]
    input_ids = raw.get("input_product_ids", [])
    recommended_ids = raw.get("recommended_product_ids", [])
    user_id = raw.get("user_id", "unknown")

    prompt = f"""
You are a helpful shopping assistant.

A customer (user_id: {user_id}) is viewing these products: {input_ids}.

The recommendation engine suggests they might also like: {recommended_ids}.

Write a short, friendly paragraph (2-4 sentences) explaining why these additional
products could be a good match for the customer. Be concise and positive.
""".strip()

    response = llm.invoke(prompt)

    add_token_metrics(state, response)
    
    explanation = response.content.strip()

    state["result"] = {
        **raw,
        "explanation": explanation,
    }
    return state


def should_enrich(state: RecommendationState) -> str:
    """Conditional edge: only call enrich_with_llm for explain route."""
    return "enrich" if state["route"] == "explain_recommendations" else "end"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(RecommendationState)

    graph.add_node("classify_request", classify_request)
    graph.add_node("run_agent", run_agent)
    graph.add_node("enrich_with_llm", enrich_with_llm)

    graph.set_entry_point("classify_request")
    graph.add_edge("classify_request", "run_agent")

    graph.add_conditional_edges(
        "run_agent",
        should_enrich,
        {
            "enrich": "enrich_with_llm",
            "end": END,
        },
    )
    graph.add_edge("enrich_with_llm", END)

    return graph.compile()