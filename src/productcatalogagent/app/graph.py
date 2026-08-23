import os
from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, END
from app.agent import ProductCatalogAgent
from app.llm.qwen import get_qwen_llm


class CatalogState(TypedDict):
    query: str
    product_ids: Optional[List[str]]
    handoff_contract: Optional[dict]
    route: str
    result: dict
    total_input_tokens: int
    total_output_tokens: int
    total_llm_calls: int


agent = ProductCatalogAgent()
llm = get_qwen_llm()


def classify_request(state: CatalogState):
    if state.get("product_ids"):
        state["route"] = "get_product"
        state["total_input_tokens"] = state.get("total_input_tokens", 0)
        state["total_output_tokens"] = state.get("total_output_tokens", 0)
        state["total_llm_calls"] = state.get("total_llm_calls", 0)
        return state

    prompt = f"""
You are a router for a product catalog service.
Classify the user request into exactly one label:
- list_products
- search_products
- get_product

Rules:
- If user wants all items or full catalog, return list_products
- If user is looking for a product by words like sunglasses, mug, watch, return search_products
- If a product id is provided, return get_product

User query: {state['query']}

Return only one label.
""".strip()

    response = llm.invoke(prompt)
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

    label = response.content.strip().lower() 

    if "list_products" in label:
        state["route"] = "list_products"
    elif "get_product" in label:
        state["route"] = "get_product"
    else:
        state["route"] = "search_products"

        state["result"]["total_input_tokens"] = state.get("total_input_tokens", 0)
        state["result"]["total_output_tokens"] = state.get("total_output_tokens", 0)
        state["result"]["total_llm_calls"] = state.get("total_llm_calls", 0)
        state["result"]["total_tokens"] = (
    state.get("total_input_tokens", 0) + state.get("total_output_tokens", 0)
)

    return state


def run_agent(state: CatalogState):
    if state["route"] == "get_product":
        state["result"] = agent.run(
            query=state["query"],
            product_ids=state.get("product_ids"),
            handoff_contract=state.get("handoff_contract"),
        )
    elif state["route"] == "list_products":
        state["result"] = agent.run(
            query="list all products",
            product_ids=None,
            handoff_contract=state.get("handoff_contract"),
        )
    else:
        state["result"] = agent.run(
            query=state["query"],
            product_ids=None,
            handoff_contract=state.get("handoff_contract"),
        )

    return state


def build_graph():
    graph = StateGraph(CatalogState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("run_agent", run_agent)

    graph.set_entry_point("classify_request")
    graph.add_edge("classify_request", "run_agent")
    graph.add_edge("run_agent", END)

    return graph.compile()