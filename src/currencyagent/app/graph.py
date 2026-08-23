import logging
import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from app.agent import CurrencyAgent
from app.llm.llama import get_llama_llm

logger = logging.getLogger(__name__)


class CurrencyState(TypedDict):
    query: str
    from_currency: Optional[str]
    to_currency: Optional[str]
    units: Optional[int]
    nanos: Optional[int]
    handoff_contract: Optional[dict]
    route: str
    result: dict
    total_input_tokens: int
    total_output_tokens: int
    total_llm_calls: int


agent = CurrencyAgent()
llm = get_llama_llm()


def classify_request(state: CurrencyState):
    logger.info(f"classify_request called | query='{state['query']}'")

    prompt = f"""
You are a router for a currency service.
Classify the user request into exactly one label:
- get_supported_currencies
- convert

Rules:
- If user wants to know which currencies are available or supported, return get_supported_currencies
- If user wants to convert an amount from one currency to another, return convert

User query: {state['query']}

Return only one label.
""".strip()

    logger.debug("Invoking LLM for classification")
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
    logger.info(f"LLM classification result: '{label}'")

    if "get_supported_currencies" in label:
        state["route"] = "get_supported_currencies"
    else:
        state["route"] = "convert"

    logger.info(f"Routing to: {state['route']}")
    return state


def run_agent(state: CurrencyState):
    logger.info(f"run_agent called | route={state['route']}")

    if state["route"] == "get_supported_currencies":
        state["result"] = agent.run(
            query=state["query"],
            action="get_supported_currencies"
        )
    else:
        state["result"] = agent.run(
            query=state["query"],
            action="convert",
            from_currency=state.get("from_currency", "USD"),
            units=state.get("units", 0),
            nanos=state.get("nanos", 0),
            to_currency=state.get("to_currency", "EUR"),
            handoff_contract=state.get("handoff_contract")
        )

    # ADD THIS BLOCK HERE (before return)
    state["result"]["total_input_tokens"] = state.get("total_input_tokens", 0)
    state["result"]["total_output_tokens"] = state.get("total_output_tokens", 0)
    state["result"]["total_llm_calls"] = state.get("total_llm_calls", 0)
    state["result"]["total_tokens"] = (
        state.get("total_input_tokens", 0) + state.get("total_output_tokens", 0)
    )

    logger.info(f"run_agent completed | action={state['result']['action']}")
    return state


def build_graph():
    logger.info("Building LangGraph for CurrencyAgent")
    graph = StateGraph(CurrencyState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("run_agent", run_agent)

    graph.set_entry_point("classify_request")
    graph.add_edge("classify_request", "run_agent")
    graph.add_edge("run_agent", END)

    logger.info("LangGraph compiled successfully")
    return graph.compile()