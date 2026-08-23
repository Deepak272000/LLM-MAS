from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from app.agent import PaymentAgent
from app.llm.llama import get_llama_llm
import logging 
import os

logger = logging.getLogger("payment-agent")


class PaymentState(TypedDict):
    query: str
    currency_code: Optional[str]
    units: Optional[int]
    nanos: Optional[int]
    credit_card_number: Optional[str]
    credit_card_cvv: Optional[int]
    credit_card_expiration_year: Optional[int]
    credit_card_expiration_month: Optional[int]
    route: str
    result: dict
    total_input_tokens: int
    total_output_tokens: int
    total_llm_calls: int
    handoff_contract: Optional[dict]

agent = PaymentAgent()
llm = get_llama_llm()


def classify_request(state: PaymentState):
    logger.info(f"[Router] Classifying query: {state['query']}")
    prompt = f"""
    You are a router for a payment service.
    Classify the user request into exactly one label:
    - charge

    Rules:
    - If the user wants to make a payment or charge a credit card, return charge

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

    if "charge" in label:
        state["route"] = "charge"
    else:
        state["route"] = "charge"

    return state


async def run_agent(state: PaymentState):
    logger.info("[Graph] Executing PaymentAgent")

    state["result"] = await agent.run(
        query=state["query"],
        currency_code=state.get("currency_code", "USD"),
        units=state.get("units", 0),
        nanos=state.get("nanos", 0),
        credit_card_number=state.get("credit_card_number", ""),
        credit_card_cvv=state.get("credit_card_cvv", 0),
        credit_card_expiration_year=state.get("credit_card_expiration_year", 0),
        credit_card_expiration_month=state.get("credit_card_expiration_month", 0),
        handoff_contract=state.get("handoff_contract"),
    )

    state["result"]["total_input_tokens"] = state.get("total_input_tokens", 0)
    state["result"]["total_output_tokens"] = state.get("total_output_tokens", 0)
    state["result"]["total_llm_calls"] = state.get("total_llm_calls", 0)
    state["result"]["total_tokens"] = (
     state.get("total_input_tokens", 0) + state.get("total_output_tokens", 0)
)

    
    logger.info("[Graph] Execution completed")

    return state


def build_graph():
    graph = StateGraph(PaymentState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("run_agent", run_agent)

    graph.set_entry_point("classify_request")
    graph.add_edge("classify_request", "run_agent")
    graph.add_edge("run_agent", END)

    return graph.compile()