"""
PaymentOrchestrator
===================
Pure LLM ReAct orchestrator for the payment service.

Mirrors ShippingOrchestrator exactly:
  - No gRPC calls
  - Llama/Ollama OpenAI-compatible endpoint
  - ReAct loop: Thought → Action → Action Input → Observation → Final Answer
  - LKW checkpoints at every observable step
  - Fault injection hooks from app.fault_injection

Checkout flow:
  1. validate_card   → CardValidationAgent
  2. charge_payment  → ChargeAgent
  Final Answer: {"transaction_id": ..., "amount": ..., "currency_code": ...}
"""

import json
import logging
import os
import re
import time
import requests
from datetime import datetime, timezone
from typing import Optional

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

from app.agents.card_validation_agent import CardValidationAgent
from app.agents.charge_agent import ChargeAgent

log = logging.getLogger(__name__)

# ── Config from environment (same pattern as shippingagent) ──────────────────

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
    EXPECTED_STEPS = [
        "TASK_START",
        "CARD_VALIDATED",
        "CHARGE_DONE",
        "SAVE_DONE",
        "FINAL_ANSWER",
    ]

    def __init__(self):
        self.checkpoints: list[dict] = []
        self.fault_mode = fi.FAULT_MODE

    def record(self, step: str, data: dict):
        entry = {
            "step":       step,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "fault_mode": self.fault_mode,
            "data":       data,
        }
        self.checkpoints.append(entry)
        log.info("[LKW] %s | fault=%s | data=%s", step, self.fault_mode, data)

    def missing_steps(self) -> list[str]:
        reached = {c["step"] for c in self.checkpoints}
        return [s for s in self.EXPECTED_STEPS if s not in reached]

    def to_dict(self) -> dict:
        reached = [c["step"] for c in self.checkpoints]
        missing = self.missing_steps()
        infected = None
        for c in self.checkpoints:
            d = c.get("data", {})
            if c["step"] == "CARD_VALIDATED" and not d.get("valid", True):
                if infected is None:
                    infected = c["step"]
            if c["step"] == "CHARGE_DONE" and d.get("status") != "success":
                if infected is None:
                    infected = c["step"]
            if c["step"] == "CHARGE_DONE" and d.get("double_charge"):
                if infected is None:
                    infected = c["step"]
            if c["step"] == "SAVE_DONE" and not d.get("saved", True):
                if infected is None:
                    infected = c["step"]
        return {
            "fault_mode":       self.fault_mode,
            "checkpoints":      self.checkpoints,
            "missing_steps":    missing,
            "rip_summary": {
                "reachability":      reached,
                "infection_point":   infected,
                "propagation_depth": len(missing),
                "missing_steps":     missing,
            },
        }


# ── Tool / prompt definitions ─────────────────────────────────────────────────

TOOL_DESCRIPTIONS = """Tools available:
1. validate_card — validates credit card details
   Input: {"credit_card_number":str,"credit_card_cvv":int,"credit_card_expiration_year":int,"credit_card_expiration_month":int}
2. charge_payment — processes the charge
   Input: {"currency_code":str,"units":int,"nanos":int,"card_last4":str,"card_type":str}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are a payment agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


# ── PaymentOrchestrator ───────────────────────────────────────────────────────

class PaymentOrchestrator:
    """
    Orchestrates payment processing using a self-hosted LLM (ReAct loop).
    Replaces the gRPC-backed PaymentAgent for benchmark/fault-injection use.
    """

    def __init__(self):
        self.base_url         = LLAMA_BASE_URL.rstrip("/")
        self.model            = LLAMA_MODEL
        self.card_validator   = CardValidationAgent()
        self.charge_agent     = ChargeAgent()
        log.info("PaymentOrchestrator ready — endpoint=%s model=%s",
                 self.base_url, self.model)

    # ── LLM call ──────────────────────────────────────────────────────────────

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
                raise RuntimeError(f"LLM read timeout after {LLAMA_CALL_RETRIES+1} attempts: {e}")
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(
                    f"Cannot reach LLM at {self.base_url}. "
                    f"Ensure Ollama is running. Error: {e}"
                )
            except requests.exceptions.HTTPError as e:
                raise RuntimeError(f"LLM HTTP error: {e} — {resp.text[:200]}")
        if last_exc:
            raise RuntimeError(f"LLM call failed: {last_exc}")
        raise RuntimeError("LLM call failed unexpectedly")

    # ── ReAct parser ──────────────────────────────────────────────────────────

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

        act  = re.search(r"Action:\s*(\w+)", text)
        ai   = re.search(r"Action Input\s*:?\s*(\{)", text)
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

    # ── Tool dispatcher ───────────────────────────────────────────────────────

    def _dispatch_tool(self, tool_name: str, tool_input: dict, ckpt: LKWCheckpoint) -> str:
        if tool_name == "validate_card":
            # FM_1_2: bypass validation entirely
            if fi.maybe_bypass_validation(tool_input.get("credit_card_number", "")):
                result = {
                    "valid": True, "card_type": "visa",
                    "card_last4": str(tool_input.get("credit_card_number", ""))[-4:],
                    "error": None, "_bypassed": True,
                }
            else:
                result = self.card_validator.validate(
                    credit_card_number=tool_input.get("credit_card_number", ""),
                    credit_card_cvv=tool_input.get("credit_card_cvv", 0),
                    credit_card_expiration_year=tool_input.get("credit_card_expiration_year", 0),
                    credit_card_expiration_month=tool_input.get("credit_card_expiration_month", 0),
                )
                # BL_CARD_DECLINED: force decline on valid card
                result = fi.maybe_force_card_decline(result)
            ckpt.record("CARD_VALIDATED", result)

        elif tool_name == "charge_payment":
            units = tool_input.get("units", 0)
            nanos = tool_input.get("nanos", 0)
            # FM_2_5 / BL_AMOUNT_TAMPERING: replace amount
            units, nanos = fi.maybe_tamper_amount(units, nanos)
            result = self.charge_agent.charge(
                currency_code=tool_input.get("currency_code", "USD"),
                units=units,
                nanos=nanos,
                card_last4=tool_input.get("card_last4", "****"),
                card_type=tool_input.get("card_type", "visa"),
            )
            # FM_2_2: replace transaction_id with hallucinated one
            result = fi.maybe_hallucinate_transaction_id(result)
            # BL_TRANSACTION_LOST: mark save as skipped
            result = fi.maybe_mark_transaction_lost(result)
            # BL_DOUBLE_CHARGE: add duplicate marker
            result = fi.maybe_inject_double_charge(result)
            ckpt.record("CHARGE_DONE", {
                "transaction_id": result.get("transaction_id"),
                "amount":         result.get("amount"),
                "currency_code":  result.get("currency_code"),
                "status":         result.get("status"),
                "double_charge":  result.get("double_charge", False),
            })
        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result)

    # ── ReAct loop ────────────────────────────────────────────────────────────

    def _run_agent_loop(self, task_prompt: str, ckpt: LKWCheckpoint) -> str:
        messages = [
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
                # FM_3_1: intercept final answer for premature termination
                early = fi.maybe_inject_early_termination(iteration, scratchpad)
                if early is not None:
                    kind, value, tool_input = self._parse_react(early)
                log.info("PaymentOrchestrator: Final Answer after %d iterations", iteration + 1)
                return value

            elif kind == "action":
                obs        = self._dispatch_tool(value, tool_input, ckpt)
                scratchpad += f"\nObservation: {obs}\n"

            else:
                scratchpad += (
                    "\nObservation: FORMAT_ERROR. Use:\n"
                    "Action: <tool_name>\nAction Input: <valid JSON>\n"
                    "OR\nFinal Answer: <valid JSON>\n"
                )

        raise RuntimeError(
            f"PaymentOrchestrator: ReAct loop exceeded {MAX_ITERATIONS} iterations"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def charge(
        self,
        query: str = "charge card",
        currency_code: str = "USD",
        units: int = 0,
        nanos: int = 0,
        credit_card_number: str = "",
        credit_card_cvv: int = 0,
        credit_card_expiration_year: int = 0,
        credit_card_expiration_month: int = 0,
        capture_partial_trace: bool = False,
    ) -> dict:
        """
        Process a payment charge via LLM ReAct orchestration.
        Returns: {"transaction_id": str, "amount": float, "_lkw": dict}
        """
        ckpt = LKWCheckpoint()
        ckpt.record("TASK_START", {
            "currency_code": currency_code,
            "units":         units,
            "nanos":         nanos,
            "card_last4":    credit_card_number[-4:] if credit_card_number else "****",
            "fault_mode":    fi.FAULT_MODE,
        })

        # FM_3_1: premature termination before any LLM call
        early = fi.maybe_premature_termination()
        if early:
            ckpt.record("FINAL_ANSWER", early)
            return {"transaction_id": None, "error": "premature_termination",
                    "_lkw": ckpt.to_dict()}

        task = (
            f"Task: Process a payment charge.\n"
            f"Credit card number: {credit_card_number}\n"
            f"CVV: {credit_card_cvv}\n"
            f"Expiry: {credit_card_expiration_month}/{credit_card_expiration_year}\n"
            f"Amount: {units}.{nanos:09d} {currency_code}\n"
            f"Steps: 1) validate_card 2) charge_payment\n"
            f'Final Answer: {{"transaction_id":"<id>","amount":<float>,"currency_code":"<code>"}}'
        )

        try:
            raw = self._run_agent_loop(task, ckpt)
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("PaymentOrchestrator.charge failed")
            ckpt.record("FINAL_ANSWER", {"error": str(exc)})
            return {"error": str(exc), "_lkw": ckpt.to_dict()}

        # FM_2_2: hallucinate transaction_id in final answer string
        raw = fi.maybe_corrupt_transaction_final(raw)

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        transaction_id = data.get("transaction_id")
        amount         = data.get("amount")

        ckpt.record("SAVE_DONE", {"saved": not fi.maybe_skip_transaction_save(),
                                  "transaction_id": transaction_id})
        ckpt.record("FINAL_ANSWER", {"raw": raw})

        log.info("[LKW] charge trace: %s", json.dumps(ckpt.to_dict()))
        return {
            "transaction_id": transaction_id,
            "amount":         amount,
            "currency_code":  currency_code,
            "_lkw":           ckpt.to_dict(),
        }
