from typing import TypedDict, Dict, Any, List
from langgraph.graph import StateGraph, END

from app.agent import generate_email_content
from app.grpc_client import EmailServiceClient
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


class EmailGraphState(TypedDict):
    request: Dict[str, Any]
    handoff_contract: Dict[str, Any]
    email_type: str
    subject: str
    body: str
    llm_used: bool
    microservice_status: str


def run_agent_node(state: EmailGraphState) -> EmailGraphState:
    fi.clear_lkw()
    request = state["request"]

    fi.record_checkpoint("TASK_START", {
        "email": request.get("email", ""),
        "order_id": request.get("order_id", ""),
        "fault_mode": fi.FAULT_MODE,
    })

    # FM_3_1: premature termination — skip generation entirely
    if fi.maybe_premature_termination():
        state["email_type"] = "PREMATURE_TERMINATION"
        state["subject"] = ""
        state["body"] = ""
        state["llm_used"] = False
        fi.record_checkpoint("FINAL_ANSWER", {"premature_termination": True})
        return state

    # FM_2_5: swap recipient before processing
    swapped_email = fi.maybe_swap_recipient(request.get("email", ""))
    if swapped_email != request.get("email", ""):
        request = dict(request)
        request["email"] = swapped_email
        state["request"] = request

    result = generate_email_content(request)

    # FM_2_2: hallucinate email content
    result = fi.maybe_hallucinate_email(result)

    # FM_1_2: wrong email type
    result["email_type"] = fi.maybe_wrong_email_type(result["email_type"])

    # BL_CORRUPTED_BODY: truncate body
    result["body"] = fi.maybe_corrupt_body(result["body"])

    # BL_WRONG_CUSTOMER: replace customer name
    result["body"], wrong_customer = fi.maybe_wrong_customer(result["body"])

    state["email_type"] = result["email_type"]
    state["subject"] = result["subject"]
    state["body"] = result["body"]
    state["llm_used"] = result.get("llm_used", False)

    observed_email = {
        "email": request.get("email", ""),
        "email_type": state["email_type"],
        "subject": state["subject"],
        "body": state["body"],
    }
    handoff_contract = state.get("handoff_contract")
    if handoff_contract is not None:
        boundary = boundary_contract(
            handoff_contract.get("boundary", "email_generation_to_send"),
            handoff_contract.get("expected", observed_email),
            observed_email,
            required_keys=["email", "email_type", "subject", "body"],
            validators={
                "email": lambda value: isinstance(value, str) and "@" in value,
                "email_type": lambda value: isinstance(value, str) and bool(value.strip()),
                "subject": lambda value: isinstance(value, str) and bool(value.strip()),
                "body": lambda value: isinstance(value, str) and bool(value.strip()),
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
        if recovery.get("action") == "block_and_request_hitl":
            fi.record_checkpoint("RECOVERY_ACTION", recovery)
            state["boundary_blocked"] = True
            state["boundary_block_reason"] = recovery.get("reason")

    fi.record_checkpoint("EMAIL_GENERATED", {
        "email_type": state["email_type"],
        "subject": state["subject"][:60],
        "body_len": len(state["body"]),
        "hallucinated": result.get("hallucinated", False),
        "type_wrong": fi.FAULT_MODE == "FM_1_2",
        "recipient_swapped": fi.FAULT_MODE == "FM_2_5",
        "corrupted": fi.FAULT_MODE == "BL_CORRUPTED_BODY",
        "wrong_customer": wrong_customer,
    })
    return state


def send_via_microservice_node(state: EmailGraphState) -> EmailGraphState:
    # FM_3_1 guard: if premature termination flagged, skip send
    if state.get("email_type") == "PREMATURE_TERMINATION":
        state["microservice_status"] = "skipped_premature"
        return state

    # Boundary recovery guard: block send if boundary detected corrupted/misrouted email
    if state.get("boundary_blocked"):
        state["microservice_status"] = "blocked_by_boundary"
        fi.record_checkpoint("FINAL_ANSWER", {
            "blocked": True,
            "reason": state.get("boundary_block_reason"),
            "requires_hitl": True,
        })
        return state

    # BL_SEND_SKIPPED: bypass gRPC
    if fi.maybe_skip_send():
        state["microservice_status"] = "skipped_fault"
        double_send = fi.maybe_double_send_marker()
        fi.record_checkpoint("EMAIL_SENT", {"send_skipped": True, "double_send": double_send})
        fi.record_checkpoint("FINAL_ANSWER", {"status": "skipped_fault"})
        return state

    double_send = fi.maybe_double_send_marker()

    client = EmailServiceClient()
    result = client.send_confirmation_email(state["request"])
    state["microservice_status"] = result["status"]

    fi.record_checkpoint("EMAIL_SENT", {
        "status": result["status"],
        "send_skipped": False,
        "double_send": double_send,
    })
    fi.record_checkpoint("FINAL_ANSWER", {"status": result["status"]})
    return state


def build_graph():
    graph = StateGraph(EmailGraphState)
    graph.add_node("run_agent", run_agent_node)
    graph.add_node("send_via_microservice", send_via_microservice_node)

    graph.set_entry_point("run_agent")
    graph.add_edge("run_agent", "send_via_microservice")
    graph.add_edge("send_via_microservice", END)

    return graph.compile()