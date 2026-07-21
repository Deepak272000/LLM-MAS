"""
CurrencyOrchestrator
====================
Pure LLM ReAct orchestrator for the currency service.

Mirrors ShippingOrchestrator exactly:
  - No gRPC calls — static exchange-rate table
  - Llama/Ollama OpenAI-compatible endpoint
  - ReAct loop with LKW checkpoints and fault injection

Supported operations:
  convert_currency  → ConversionAgent
  list_currencies   → SupportedCurrenciesAgent
  Final Answer: {"currency_code":..., "units":..., "nanos":...}
             or {"currency_codes": [...]}
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

from app.agents.conversion_agent import ConversionAgent, SupportedCurrenciesAgent

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
    EXPECTED_STEPS = ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"]

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
        reached  = [c["step"] for c in self.checkpoints]
        missing  = self.missing_steps()
        infected = None
        for c in self.checkpoints:
            d = c.get("data", {})
            if c["step"] == "CONVERT_DONE":
                if d.get("rate_manipulated") or d.get("stale_rate") or d.get("overflow"):
                    infected = c["step"]
                if d.get("error"):
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
1. convert_currency — converts a money amount between currencies
   Input: {"from_currency":str,"units":int,"nanos":int,"to_currency":str}
2. list_currencies — returns all supported currency codes
   Input: {}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are a currency agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


class CurrencyOrchestrator:
    """
    Orchestrates currency operations using a self-hosted LLM (ReAct loop).
    Replaces the gRPC-backed CurrencyAgent for benchmark/fault-injection use.
    """

    def __init__(self):
        self.base_url           = LLAMA_BASE_URL.rstrip("/")
        self.model              = LLAMA_MODEL
        self.conversion_agent   = ConversionAgent()
        self.currencies_agent   = SupportedCurrenciesAgent()
        log.info("CurrencyOrchestrator ready — endpoint=%s model=%s",
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
        if tool_name == "convert_currency":
            from_c = tool_input.get("from_currency", "USD")
            to_c   = tool_input.get("to_currency",   "EUR")
            units  = tool_input.get("units", 0)
            nanos  = tool_input.get("nanos", 0)

            # FM_1_2: swap target currency
            to_c = fi.maybe_swap_currency(to_c)
            # FM_2_5: tamper input amount
            units, nanos = fi.maybe_tamper_amount(units, nanos)

            result = self.conversion_agent.convert(from_c, units, nanos, to_c)

            # FM_2_2: hallucinate result
            result = fi.maybe_hallucinate_result(result)
            # BL_RATE_MANIPULATION: inflate rate
            result = fi.maybe_inflate_rate(result)
            # BL_STALE_RATE: inject stale cached rate
            result = fi.maybe_inject_stale_rate(result)
            # BL_CONVERSION_OVERFLOW: inject overflow value
            result = fi.maybe_inject_overflow(result)

            ckpt.record("CONVERT_DONE", {
                "from_currency":    from_c,
                "to_currency":      result.get("currency_code"),
                "units":            result.get("units"),
                "nanos":            result.get("nanos"),
                "rate_used":        result.get("rate_used"),
                "rate_manipulated": fi.FAULT_MODE == "BL_RATE_MANIPULATION",
                "stale_rate":       fi.FAULT_MODE == "BL_STALE_RATE",
                "overflow":         fi.FAULT_MODE == "BL_CONVERSION_OVERFLOW",
            })

        elif tool_name == "list_currencies":
            result = self.currencies_agent.list_currencies()
            ckpt.record("CONVERT_DONE", {"action": "list", "count": len(result.get("currency_codes", []))})
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
                log.info("CurrencyOrchestrator: Final Answer after %d iterations", iteration + 1)
                return value
            elif kind == "action":
                obs        = self._dispatch_tool(value, tool_input, ckpt)
                scratchpad += f"\nObservation: {obs}\n"
            else:
                scratchpad += (
                    "\nObservation: FORMAT_ERROR. Use Action/Action Input or Final Answer.\n"
                )

        raise RuntimeError(
            f"CurrencyOrchestrator: ReAct loop exceeded {MAX_ITERATIONS} iterations"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def convert(
        self,
        query: str = "convert currency",
        action: str = "convert",
        from_currency: str = "USD",
        units: int = 0,
        nanos: int = 0,
        to_currency: str = "EUR",
        capture_partial_trace: bool = False,
    ) -> dict:
        """
        Convert currency or list supported currencies via LLM ReAct.
        Returns: {"currency_code":..., "units":..., "nanos":..., "_lkw": dict}
        """
        ckpt = LKWCheckpoint()
        ckpt.record("TASK_START", {
            "action":        action,
            "from_currency": from_currency,
            "to_currency":   to_currency,
            "units":         units,
            "nanos":         nanos,
            "fault_mode":    fi.FAULT_MODE,
        })

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early:
            ckpt.record("FINAL_ANSWER", early)
            return {"error": "premature_termination", "_lkw": ckpt.to_dict()}

        if action == "get_supported_currencies":
            task = (
                "Task: List all supported currencies.\n"
                "Use the list_currencies tool.\n"
                'Final Answer: {"currency_codes": [...]}'
            )
        else:
            task = (
                f"Task: Convert currency.\n"
                f"From: {from_currency}, Amount: {units}.{nanos:09d}\n"
                f"To: {to_currency}\n"
                f"Use convert_currency tool.\n"
                f'Final Answer: {{"currency_code":"<code>","units":<int>,"nanos":<int>}}'
            )

        try:
            raw = self._run_agent_loop(task, ckpt)
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("CurrencyOrchestrator.convert failed")
            ckpt.record("FINAL_ANSWER", {"error": str(exc)})
            return {"error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        # BL_CURRENCY_UNAVAILABLE: simulate unavailable error
        err = fi.maybe_simulate_unavailable()
        if err:
            ckpt.record("FINAL_ANSWER", {"unavailable": True, "error": str(err)})
            return {"error": str(err), "_lkw": ckpt.to_dict()}

        ckpt.record("FINAL_ANSWER", {"raw": raw})
        log.info("[LKW] convert trace: %s", json.dumps(ckpt.to_dict()))

        result = {
            "currency_code": data.get("currency_code", to_currency),
            "units":         data.get("units", 0),
            "nanos":         data.get("nanos", 0),
            "_lkw":          ckpt.to_dict(),
        }
        if "currency_codes" in data:
            result["currency_codes"] = data["currency_codes"]
        return result
