"""
EmailOrchestrator
=================
Pure LLM ReAct orchestrator for the email service.

Mirrors ShippingOrchestrator exactly:
  - No gRPC calls — template-based email generation + mock delivery
  - Llama/Ollama OpenAI-compatible endpoint
  - ReAct loop with LKW checkpoints and fault injection

Checkout flow:
  1. generate_email  → EmailGeneratorAgent
  2. send_email      → EmailDeliveryAgent
  Final Answer: {"message_id": ..., "sent_at": ..., "to": ...}
"""

import json
import logging
import os
import re
import time
import requests
from datetime import datetime, timezone

import app.fault_injection as fi

try:
    from boundary_validation import boundary_contract
except ImportError:
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

from app.agents.email_generator_agent import EmailGeneratorAgent
from app.agents.email_delivery_agent import EmailDeliveryAgent

log = logging.getLogger(__name__)

LLAMA_BASE_URL    = os.getenv("LLAMA_BASE_URL",    "http://localhost:11434/v1")
LLAMA_MODEL       = os.getenv("LLAMA_MODEL",       "qwen2.5:3b")
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.0"))

MAX_ITERATIONS        = int(os.getenv("MAX_ITERATIONS", "8"))
MAX_TOKENS            = int(os.getenv("MAX_TOKENS", "512"))
LLAMA_CONNECT_TIMEOUT = int(os.getenv("LLAMA_CONNECT_TIMEOUT", "60"))
LLAMA_READ_TIMEOUT    = int(os.getenv("LLAMA_READ_TIMEOUT", "300"))
LLAMA_CALL_RETRIES    = int(os.getenv("LLAMA_CALL_RETRIES", "1"))
LLAMA_RETRY_BACKOFF   = float(os.getenv("LLAMA_RETRY_BACKOFF", "2"))


# ── LKW Checkpoint Logger ────────────────────────────────────────────────────

class LKWCheckpoint:
    EXPECTED_STEPS = ["TASK_START", "EMAIL_GENERATED", "EMAIL_SENT", "FINAL_ANSWER"]

    def __init__(self):
        self.checkpoints: list[dict] = []
        self.fault_mode = fi.FAULT_MODE

    def record(self, step: str, data: dict):
        self.checkpoints.append({
            "step":       step,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "fault_mode": self.fault_mode,
            "data":       data,
        })
        log.info("[LKW] %s | fault=%s | data=%s", step, self.fault_mode, data)

    def missing_steps(self) -> list[str]:
        reached = {c["step"] for c in self.checkpoints}
        return [s for s in self.EXPECTED_STEPS if s not in reached]

    def to_dict(self) -> dict:
        reached  = [c["step"] for c in self.checkpoints]
        missing  = self.missing_steps()
        infected = None
        for c in self.checkpoints:
            d = c.get("data", {})
            if c["step"] == "EMAIL_GENERATED" and d.get("body_corrupted"):
                infected = c["step"]
            if c["step"] == "EMAIL_GENERATED" and d.get("wrong_customer"):
                infected = c["step"]
            if c["step"] == "EMAIL_SENT" and d.get("send_skipped"):
                infected = c["step"]
            if c["step"] == "EMAIL_SENT" and d.get("double_send"):
                infected = c["step"]
        return {
            "fault_mode":    self.fault_mode,
            "checkpoints":   self.checkpoints,
            "missing_steps": missing,
            "rip_summary": {
                "reachability":      reached,
                "infection_point":   infected,
                "propagation_depth": len(missing),
                "missing_steps":     missing,
            },
        }


TOOL_DESCRIPTIONS = """Tools available:
1. generate_email — generates order confirmation email content
   Input: {"order_id":str,"customer_name":str,"customer_email":str,"items":[...],"total_cost":{"currency_code":str,"units":int,"nanos":int},"shipping_tracking_id":str}
2. send_email — sends the generated email
   Input: {"subject":str,"body":str,"to":str,"from_address":str}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are an email agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


class EmailOrchestrator:
    """
    Orchestrates email delivery using a self-hosted LLM (ReAct loop).
    Replaces the gRPC-backed EmailServiceAgent for benchmark/fault-injection use.
    """

    def __init__(self):
        self.base_url        = LLAMA_BASE_URL.rstrip("/")
        self.model           = LLAMA_MODEL
        self.generator_agent = EmailGeneratorAgent()
        self.delivery_agent  = EmailDeliveryAgent()
        log.info("EmailOrchestrator ready — endpoint=%s model=%s",
                 self.base_url, self.model)

    def _call_llm(self, messages: list, stop: list = None) -> str:
        payload = {
            "model":       self.model,
            "messages":    messages,
            "max_tokens":  MAX_TOKENS,
            "temperature": LLAMA_TEMPERATURE,
        }
        if stop:
            payload["stop"] = stop
        last_exc = None
        for attempt in range(LLAMA_CALL_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=(LLAMA_CONNECT_TIMEOUT, LLAMA_READ_TIMEOUT),
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data    = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                usage   = data.get("usage", {})
                log.info("TOKEN_METRICS input=%d output=%d",
                         usage.get("prompt_tokens", 0),
                         usage.get("completion_tokens", 0))
                return content
            except requests.exceptions.ReadTimeout as e:
                last_exc = e
                if attempt < LLAMA_CALL_RETRIES:
                    time.sleep(LLAMA_RETRY_BACKOFF * (attempt + 1))
                    continue
                raise RuntimeError(f"LLM read timeout: {e}")
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(f"Cannot reach LLM at {self.base_url}: {e}")
            except requests.exceptions.HTTPError as e:
                raise RuntimeError(f"LLM HTTP error: {e} — {resp.text[:200]}")
        raise RuntimeError(f"LLM call failed: {last_exc}")

    def _parse_react(self, text: str):
        fa_pos = text.find("Final Answer:")
        if fa_pos != -1:
            fa_text = text[fa_pos + len("Final Answer:"):]
            fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", fa_text)
            if fence:
                return ("final", fence.group(1).strip(), None)
            brace = fa_text.find("{")
            if brace != -1:
                depth, end = 0, brace
                for i, ch in enumerate(fa_text[brace:], brace):
                    depth += 1 if ch == "{" else (-1 if ch == "}" else 0)
                    if depth == 0:
                        end = i + 1
                        break
                if end > brace:
                    return ("final", fa_text[brace:end].strip(), None)
        act = re.search(r"Action:\s*(\w+)", text)
        ai  = re.search(r"Action Input\s*:?\s*(\{)", text)
        if act and ai:
            start = ai.start(1)
            depth, end = 0, start
            for i, ch in enumerate(text[start:], start):
                depth += 1 if ch == "{" else (-1 if ch == "}" else 0)
                if depth == 0:
                    end = i + 1
                    break
            try:
                tool_input = json.loads(text[start:end])
            except json.JSONDecodeError:
                tool_input = {}
            return ("action", act.group(1).strip(), tool_input)
        return ("unknown", text, None)

    def _dispatch_tool(self, tool_name: str, tool_input: dict, ckpt: LKWCheckpoint) -> str:
        if tool_name == "generate_email":
            # FM_1_2: wrong email type classification (ignored here — generator always produces confirmation)
            email_type_wrong = fi.maybe_wrong_email_type("order_confirmation")

            result = self.generator_agent.generate(
                order_id=tool_input.get("order_id", "ORD-UNKNOWN"),
                customer_name=tool_input.get("customer_name", "Customer"),
                customer_email=tool_input.get("customer_email", ""),
                items=tool_input.get("items", []),
                total_cost=tool_input.get("total_cost", {}),
                shipping_tracking_id=tool_input.get("shipping_tracking_id", ""),
            )

            # FM_2_5: replace recipient address
            result["to"] = fi.maybe_swap_recipient(result["to"])
            # FM_2_2: hallucinate email content
            result = fi.maybe_hallucinate_email(result)
            # BL_CORRUPTED_BODY: truncate body
            result = fi.maybe_corrupt_body(result)
            # BL_WRONG_CUSTOMER: replace customer name in body
            result = fi.maybe_inject_wrong_customer(result)

            ckpt.record("EMAIL_GENERATED", {
                "subject":        result.get("subject", "")[:80],
                "to":             result.get("to"),
                "body_corrupted": fi.FAULT_MODE == "BL_CORRUPTED_BODY",
                "wrong_customer": fi.FAULT_MODE == "BL_WRONG_CUSTOMER",
                "hallucinated":   fi.FAULT_MODE == "FM_2_2",
            })

        elif tool_name == "send_email":
            # BL_SEND_SKIPPED: bypass send
            if fi.maybe_skip_send():
                result = {"status": "skipped", "message_id": None, "send_skipped": True}
                ckpt.record("EMAIL_SENT", {"send_skipped": True, "status": "skipped"})
            else:
                result = self.delivery_agent.send(
                    subject=tool_input.get("subject", ""),
                    body=tool_input.get("body", ""),
                    to=tool_input.get("to", ""),
                    from_address=tool_input.get("from_address", "noreply@onlineboutique.example"),
                )
                # BL_DOUBLE_SEND: add duplicate marker
                double = fi.maybe_inject_double_send()
                ckpt.record("EMAIL_SENT", {
                    "message_id": result.get("message_id"),
                    "to":         result.get("to"),
                    "status":     result.get("status"),
                    "double_send": double,
                })
        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result)

    def _run_agent_loop(self, task_prompt: str, ckpt: LKWCheckpoint) -> str:
        messages   = [
            {"role": "system", "content": REACT_SYSTEM_PROMPT},
            {"role": "user",   "content": TOOL_DESCRIPTIONS + "\n\n" + task_prompt},
        ]
        scratchpad = ""

        for iteration in range(MAX_ITERATIONS):
            cur = messages.copy()
            if scratchpad:
                cur.append({"role": "assistant", "content": scratchpad})
                cur.append({"role": "user",      "content": "Continue."})

            llm_out    = self._call_llm(cur, stop=["Observation:"])
            scratchpad += "\n" + llm_out
            kind, value, tool_input = self._parse_react(llm_out)

            if kind == "final":
                early = fi.maybe_inject_early_termination(iteration, scratchpad)
                if early is not None:
                    kind, value, tool_input = self._parse_react(early)
                log.info("EmailOrchestrator: Final Answer after %d iterations", iteration + 1)
                return value
            elif kind == "action":
                obs        = self._dispatch_tool(value, tool_input, ckpt)
                scratchpad += f"\nObservation: {obs}\n"
            else:
                scratchpad += (
                    "\nObservation: FORMAT_ERROR. Use Action/Action Input or Final Answer.\n"
                )

        raise RuntimeError(
            f"EmailOrchestrator: ReAct loop exceeded {MAX_ITERATIONS} iterations"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_order_confirmation(
        self,
        order_id: str = "",
        customer_email: str = "",
        customer_name: str = "Customer",
        items: list = None,
        total_cost: dict = None,
        shipping_tracking_id: str = "",
        capture_partial_trace: bool = False,
    ) -> dict:
        """
        Send an order confirmation email via LLM ReAct orchestration.
        Returns: {"message_id": str, "sent_at": str, "_lkw": dict}
        """
        ckpt = LKWCheckpoint()
        ckpt.record("TASK_START", {
            "order_id":      order_id,
            "customer_email": customer_email,
            "fault_mode":    fi.FAULT_MODE,
        })

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early:
            ckpt.record("FINAL_ANSWER", early)
            return {"message_id": None, "error": "premature_termination",
                    "_lkw": ckpt.to_dict()}

        task = (
            f"Task: Send order confirmation email.\n"
            f"Order ID: {order_id}\n"
            f"Customer: {customer_name} <{customer_email}>\n"
            f"Items: {json.dumps(items or [])}\n"
            f"Total: {json.dumps(total_cost or {})}\n"
            f"Tracking: {shipping_tracking_id}\n"
            f"Steps: 1) generate_email 2) send_email\n"
            f'Final Answer: {{"message_id":"<id>","sent_at":"<ts>","to":"<email>"}}'
        )

        try:
            raw = self._run_agent_loop(task, ckpt)
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("EmailOrchestrator.send_order_confirmation failed")
            ckpt.record("FINAL_ANSWER", {"error": str(exc)})
            return {"error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        ckpt.record("FINAL_ANSWER", {"raw": raw})
        log.info("[LKW] email trace: %s", json.dumps(ckpt.to_dict()))

        return {
            "message_id": data.get("message_id"),
            "sent_at":    data.get("sent_at"),
            "to":         data.get("to", customer_email),
            "_lkw":       ckpt.to_dict(),
        }
