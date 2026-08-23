"""
ShippingOrchestrator (Llama 3 variant)
=======================================
Replaces the Anthropic SDK with calls to a self-hosted Llama 3 instance
running as an OpenAI-compatible endpoint inside the Kubernetes cluster
(e.g. via vLLM, Ollama, or llama.cpp server).

Key difference from the Claude version:
  Llama 3 does not natively support tool-use in the same structured way.
  This orchestrator implements a **ReAct loop** (Reason → Act → Observe)
  using a strict prompt format that Llama 3 Instruct can reliably follow.

ReAct format used:
  Thought: <reasoning>
  Action: <tool_name>
  Action Input: <json args>
  ---
  Observation: <tool result injected by orchestrator>
  ... (repeat until)
  Final Answer: <json result>

Environment variables:
  LLAMA_BASE_URL   — OpenAI-compatible base URL, e.g. http://llama-service:8000/v1
  LLAMA_MODEL      — model name to pass in the request, e.g. "meta-llama/Meta-Llama-3-8B-Instruct"
  PORT             — gRPC port (default 50051)
  MONGO_URI        — MongoDB connection string
  MONGO_DB_NAME    — MongoDB database name (default: shipping_service)
"""

import json
import logging
import math
import os
import re
import sys
import time
import requests
from datetime import datetime, timezone
from typing import Optional

try:
    from langsmith import traceable  # type: ignore[import-not-found]
    from langsmith.wrappers import wrap_openai  # noqa: F401 — available but optional  # type: ignore[import-not-found]
    LANGSMITH_ENABLED = bool(os.getenv("LANGSMITH_API_KEY"))
except ImportError:
    LANGSMITH_ENABLED = False
    def traceable(*args, **kwargs):
        """No-op fallback when langsmith is not installed."""
        def decorator(fn):
            return fn
        return decorator if args and callable(args[0]) else decorator

from agents.quote_agent import QuoteAgent
from agents.carrier_agent import CarrierSelectionAgent
from agents.tracking_agent import TrackingAgent
from repository import save_quote, save_shipment
from config import LLAMA_BASE_URL, LLAMA_MODEL
import fault_injection as fi


# ── LKW (Last Known Well) Checkpoint Logger ───────────────────────────────────

class LKWCheckpoint:
    """
    Records the state at each well-defined step of the shipping workflow.
    Used by the RIP (Reachability → Infection → Propagation) test harness
    to detect where quality deviation first occurs and how far it spreads.

    Checkpoints:
      TASK_START      — inputs received, fault mode recorded
      QUOTE_DONE      — cost_usd returned by get_shipping_quote
      CARRIER_DONE    — carrier + service_level returned by select_carrier
      TRACKING_DONE   — tracking_id generated
      SAVE_DONE       — shipment persisted to MongoDB
      FINAL_ANSWER    — raw Final Answer string from LLM
    """

    EXPECTED_STEPS = [
        "TASK_START",
        "QUOTE_DONE",
        "CARRIER_DONE",
        "TRACKING_DONE",
        "SAVE_DONE",
        "FINAL_ANSWER",
    ]

    def __init__(self):
        self.checkpoints: list[dict] = []
        self.fault_mode = fi.active_fault()

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
        """Return steps that were not reached — indicates propagation depth."""
        reached = {c["step"] for c in self.checkpoints}
        return [s for s in self.EXPECTED_STEPS if s not in reached]

    def to_dict(self) -> dict:
        return {
            "fault_mode":     self.fault_mode,
            "checkpoints":    self.checkpoints,
            "missing_steps":  self.missing_steps(),
            "rip_summary":    self._rip_summary(),
        }

    def _rip_summary(self) -> dict:
        """
        RIP analysis:
          Reachability  — did the fault-injected path reach the same steps as baseline?
          Infection     — which checkpoint first shows deviated data?
          Propagation   — how many downstream steps were affected?
        """
        reached   = [c["step"] for c in self.checkpoints]
        missing   = self.missing_steps()
        infected  = None

        for c in self.checkpoints:
            d = c.get("data", {})
            # Detect infection signals
            if c["step"] == "QUOTE_DONE":
                cost = d.get("cost_usd", 1.0)
                # Detect real corruption: strictly negative, OR negative-zero (-0.0 from
                # BL_REFUND_REASONING), OR inventory inflation flag.
                # Plain 0.0 from non-JSON model output is NOT treated as infected.
                cost_infected = (
                    cost < 0
                    or (cost == 0.0 and math.copysign(1.0, cost) < 0)
                    or d.get("item_count_inflated", False)
                )
                if cost_infected:
                    infected = c["step"]
                    break
            if c["step"] == "CARRIER_DONE" and d.get("carrier") in (None, "SpeedyShip", "PremiumExpress"):
                infected = c["step"]
                break
            if c["step"] == "CARRIER_DONE" and d.get("ignored_downstream_quote"):
                infected = c["step"]
                break
            if c["step"] == "CARRIER_DONE" and d.get("forced_vendor"):
                infected = c["step"]
                break
            if c["step"] == "TRACKING_DONE" and "PREMATURE" in str(d.get("tracking_id", "")):
                infected = c["step"]
                break
            if c["step"] == "TRACKING_DONE" and "COMPLIANCE-FAILED" in str(d.get("tracking_id", "")):
                infected = c["step"]
                break
            if c["step"] == "ESCALATION_CHECK" and d.get("escalation_required"):
                infected = c["step"]
                break
            if c["step"] == "SAVE_DONE" and not d.get("saved", True) and d.get("reason") != "benchmark_mode":
                infected = c["step"]
                break

        propagation = len(missing)  # each missing step = one level of propagation

        return {
            "reachability": reached,
            "infection_point": infected,
            "propagation_depth": propagation,
            "missing_steps": missing,
        }


log = logging.getLogger(__name__)


MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "8"))
MAX_TOKENS     = int(os.getenv("MAX_TOKENS", "512"))
LLAMA_CONNECT_TIMEOUT = int(os.getenv("LLAMA_CONNECT_TIMEOUT", "60"))
LLAMA_READ_TIMEOUT    = int(os.getenv("LLAMA_READ_TIMEOUT", "300"))
LLAMA_CALL_RETRIES    = int(os.getenv("LLAMA_CALL_RETRIES", "1"))
LLAMA_RETRY_BACKOFF   = float(os.getenv("LLAMA_RETRY_BACKOFF", "2"))


TOOL_DESCRIPTIONS = """Tools available:
1. get_shipping_quote — estimates USD shipping cost
   Input: {"address":{"city":str,"country":str,"state":str,"zip_code":int},"items":[{"product_id":str,"quantity":int}]}
2. select_carrier — picks best carrier
   Input: {"address":{"country":str,"state":str,"zip_code":int},"cost_usd":float,"item_count":int}
3. generate_tracking_id — generates tracking ID
   Input: {"carrier":str,"address":{"city":str,"country":str},"item_count":int}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are a shipping agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


class ShippingOrchestrator:
    """
    Orchestrates shipping operations using Llama 3 (self-hosted) as the
    reasoning engine, via a ReAct prompting loop.
    Sub-agents are identical to the Claude version — fully reusable.
    """

    def __init__(self):
        self.base_url        = LLAMA_BASE_URL.rstrip("/")
        self.model           = LLAMA_MODEL
        self.quote_agent     = QuoteAgent()
        self.carrier_agent   = CarrierSelectionAgent()
        self.tracking_agent  = TrackingAgent()
        log.info(f"ShippingOrchestrator (Llama) ready — endpoint={self.base_url}, model={self.model}")

    # ── Llama inference call ──────────────────────────────────────────────────

    def _call_llama(self, messages: list, stop: list = None) -> str:
        payload = {
            "model":       self.model,
            "messages":    messages,
            "max_tokens":  MAX_TOKENS,
            "temperature": 0.0,
        }
        if stop:
            payload["stop"] = stop

        last_timeout = None  # type: Optional[Exception]
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

                usage         = data.get("usage", {})
                input_tokens  = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                total_tokens  = usage.get("total_tokens", input_tokens + output_tokens)

                # stderr, not stdout: this helper's stdout is a JSON-only
                # channel read by the checkout orchestrator.
                print(f"TOKEN_METRICS input={input_tokens} output={output_tokens} total={total_tokens}",
                      file=sys.stderr)

                with open(os.environ.get("TOKEN_LOG", "token_log.txt"), "a") as f:
                    f.write(f"{total_tokens}\n")

                return content
            except requests.exceptions.ReadTimeout as e:
                last_timeout = e
                if attempt < LLAMA_CALL_RETRIES:
                    wait_s = LLAMA_RETRY_BACKOFF * (attempt + 1)
                    log.warning(
                        "Llama read timeout (attempt %d/%d). Retrying in %.1fs...",
                        attempt + 1,
                        LLAMA_CALL_RETRIES + 1,
                        wait_s,
                    )
                    time.sleep(wait_s)
                    continue
                raise RuntimeError(f"Llama read timeout after {LLAMA_CALL_RETRIES + 1} attempt(s): {e}")
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(
                    f"Cannot reach Llama endpoint at {self.base_url}. "
                    f"Ensure the llama-service pod is running. Error: {e}"
                )
            except requests.exceptions.HTTPError as e:
                raise RuntimeError(f"Llama API HTTP error: {e} — {resp.text[:200]}")

        if last_timeout is not None:
            raise RuntimeError(f"Llama read timeout: {last_timeout}")
        raise RuntimeError("Llama call failed unexpectedly")

    # ── ReAct parser ──────────────────────────────────────────────────────────

    def _parse_react_output(self, text: str):
        fa_pos = text.find("Final Answer:")
        if fa_pos != -1:
            fa_text = text[fa_pos + len("Final Answer:"):]
            fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", fa_text)
            if fence_match:
                return ("final", fence_match.group(1).strip(), None)

            brace_start = fa_text.find("{")
            if brace_start != -1:
                start = brace_start
                depth, end = 0, start
                for i, ch in enumerate(fa_text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > start:
                    return ("final", fa_text[start:end].strip(), None)

        action_match = re.search(r"Action:\s*(\w+)", text)
        ai_start = re.search(r"Action Input\s*:?\s*(\{)", text)
        if ai_start is None:
            ai_start = re.search(r"\bInput:\s*(\{)", text)

        if action_match and ai_start is None:
            fenced_ai = re.search(r"Action Input\s*:?\s*```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
            if fenced_ai:
                tool_name = action_match.group(1).strip()
                try:
                    tool_input = json.loads(fenced_ai.group(1))
                except json.JSONDecodeError:
                    tool_input = {}
                return ("action", tool_name, tool_input)

        if action_match and ai_start:
            tool_name = action_match.group(1).strip()
            start = ai_start.start(1)
            depth, end = 0, start
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            raw_json = text[start:end]
            try:
                tool_input = json.loads(raw_json)
            except json.JSONDecodeError as e:
                log.warning(f"Failed to parse Action Input JSON: {e}\nRaw: {raw_json}")
                tool_input = {}
            return ("action", tool_name, tool_input)

        return ("unknown", text, None)

    # ── Tool dispatcher ───────────────────────────────────────────────────────

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        log.debug(f"Dispatching tool: {tool_name}, input={tool_input}")

        # FM-1.2: block carrier/tracking tools — task spec said they were not planned
        if fi.is_active(fi.FM_1_2) and tool_name in ("select_carrier", "generate_tracking_id"):
            log.warning("[FM-1.2] Tool '%s' blocked — not in corrupted task plan", tool_name)
            return json.dumps({"error": f"Tool '{tool_name}' is not part of the current plan."})

        if tool_name == "get_shipping_quote":
            result = self.quote_agent.estimate(
                tool_input.get("address", {}), tool_input.get("items", [])
            )
        elif tool_name == "select_carrier":
            quoted_cost = float(tool_input.get("cost_usd", 0))
            selection_cost = fi.maybe_ignore_quote_for_carrier(quoted_cost)
            result = self.carrier_agent.select(
                tool_input.get("address", {}),
                selection_cost,
                int(tool_input.get("item_count", 1)),
            )
            if fi.active_fault() == "FM_2_5" and isinstance(result, dict):
                result["ignored_downstream_quote"] = True
                result["quoted_cost_usd"] = quoted_cost
                result["used_cost_usd"] = selection_cost
            # FM-2.2: replace real result with hallucinated carrier data
            result = fi.maybe_hallucinate_carrier(result)
            # BL-VENDOR_NEGOTIATION: force expensive fallback carrier
            result = fi.maybe_force_vendor(result)
        elif tool_name == "generate_tracking_id":
            result = self.tracking_agent.generate(
                tool_input.get("carrier", "FedEx"),
                tool_input.get("address", {}),
                int(tool_input.get("item_count", 1)),
            )
        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        log.debug(f"Tool result: {result}")
        return json.dumps(result)

    # ── ReAct agentic loop ────────────────────────────────────────────────────

    def _run_agent_loop(self, task_prompt: str) -> str:
        messages = [
            {"role": "system", "content": REACT_SYSTEM_PROMPT},
            {"role": "user",   "content": TOOL_DESCRIPTIONS + "\n\n" + task_prompt},
        ]
        scratchpad = ""

        for iteration in range(MAX_ITERATIONS):
            log.debug(f"ReAct iteration {iteration + 1}/{MAX_ITERATIONS}")

            current_messages = messages.copy()
            if scratchpad:
                current_messages.append({"role": "assistant", "content": scratchpad})
                current_messages.append({"role": "user",      "content": "Continue."})

            llama_output = self._call_llama(current_messages, stop=["Observation:"])
            log.debug(f"Llama output:\n{llama_output}")
            scratchpad += "\n" + llama_output

            kind, value, tool_input = self._parse_react_output(llama_output)

            if kind == "final":
                # FM-3.1: intercept Final Answer and substitute premature termination.
                # Fires regardless of iteration count so single-pass models are covered.
                early = fi.maybe_inject_early_termination(iteration, scratchpad)
                if early is not None:
                    kind, value, tool_input = self._parse_react_output(early)
                # FM-1.2: null out carrier/tracking — incomplete task plan fault.
                value = fi.maybe_corrupt_fm12_final(value)
                # FM-2.2: replace carrier with hallucinated SpeedyShip data.
                value = fi.maybe_corrupt_fm22_final(value)
                # FM-2.5: overwrite cost_usd with stale value to simulate ignored quote.
                value = fi.maybe_corrupt_fm25_final(value)
                # BL-VENDOR_NEGOTIATION: force PremiumExpress in final answer.
                value = fi.maybe_force_vendor_final(value)
                log.info(f"Agent reached Final Answer after {iteration + 1} iterations")
                return value
            elif kind == "action":
                tool_result  = self._dispatch_tool(value, tool_input)
                observation  = f"\nObservation: {tool_result}\n"
                scratchpad  += observation
                log.debug(f"Appended observation: {observation.strip()}")
            else:
                log.warning(f"Unexpected Llama output (iteration {iteration+1}): {value[:200]}")
                scratchpad += (
                    "\nObservation: FORMAT_ERROR. Respond using exactly one of:\n"
                    "1) Action: <tool_name>\\nAction Input: <valid JSON>\n"
                    "2) Final Answer: <valid JSON>\n"
                )

        # BL-COMPLIANCE_AMBIGUITY: agent exhausted loop trying to resolve compliance;
        # return a marked fallback answer so the trace is valid and the fault is observable.
        if fi.is_active(fi.BL_COMPLIANCE_AMBIGUITY):
            log.warning(
                "[BL-COMPLIANCE_AMBIGUITY] Agent loop exhausted after %d iterations — "
                "compliance ambiguity caused complete workflow failure; returning fallback.",
                MAX_ITERATIONS,
            )
            return '{"tracking_id": "COMPLIANCE-FAILED-0000", "carrier": "Unknown", '\
                   '"service_level": "standard", "cost_usd": 0.0}'
        raise RuntimeError(
            f"ReAct loop exceeded {MAX_ITERATIONS} iterations without a Final Answer"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @traceable(name="shipping.get_quote")
    async def get_quote(self, address: dict, items: list, capture_partial_trace: bool = False) -> dict:
        """
        Estimate shipping cost using Llama 3 + ReAct, then save to MongoDB.
        Returns: {"cost_usd": float}
        """
        ckpt = LKWCheckpoint()
        ckpt.record("TASK_START", {"address": address, "item_count": len(items)})

        task = (
            f"Task: Estimate the shipping cost.\n"
            f"Address: {json.dumps(address)}\n"
            f"Items: {json.dumps(items)}\n"
            f"Use the get_shipping_quote tool, then return: "
            f'Final Answer: {{"cost_usd": <number>}}'
        )

        try:
            raw = self._run_agent_loop(task)
            log.info(f"GetQuote agent response: {raw}")
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("GetQuote failed during benchmark capture")
            ckpt.record("FINAL_ANSWER", {"raw": "", "error": str(exc)})
            log.info("[LKW] get_quote trace: %s", json.dumps(ckpt.to_dict()))
            return {"error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data     = json.loads(raw)
            cost_usd = float(data["cost_usd"])
        except Exception:
            match    = re.search(r"[\d]+\.?[\d]*", raw)
            cost_usd = float(match.group()) if match else 5.0

        ckpt.record("QUOTE_DONE", {"cost_usd": cost_usd})
        ckpt.record("FINAL_ANSWER", {"raw": raw})

        # ── Save quote to MongoDB ─────────────────────────────────────────────
        quote_result = self.quote_agent.estimate(address, items)
        if capture_partial_trace:
            ckpt.record("SAVE_DONE", {"saved": False, "reason": "benchmark_mode"})
        else:
            await save_quote(
                address   = address,
                items     = items,
                cost_usd  = cost_usd,
                breakdown = quote_result.get("breakdown", {}),
            )
            ckpt.record("SAVE_DONE", {"saved": True, "cost_usd": cost_usd})

        log.info("[LKW] get_quote trace: %s", json.dumps(ckpt.to_dict()))
        return {"cost_usd": cost_usd, "_lkw": ckpt.to_dict()}

    @traceable(name="shipping.ship_order")
    async def ship_order(self, address: dict, items: list, capture_partial_trace: bool = False) -> dict:
        """
        Orchestrate a full shipment using Llama 3 + ReAct:
        quote → carrier selection → tracking ID, then save to MongoDB.
        Returns: {"tracking_id": str}
        """
        ckpt = LKWCheckpoint()
        # BL-INVENTORY_MISMATCH: corrupt quantities before computing item_count
        items = fi.maybe_corrupt_items(items)
        item_count = sum(i.get("quantity", 1) for i in items)
        # BL-COMPLIANCE_AMBIGUITY: tag address with unknown jurisdiction
        address = fi.maybe_tag_compliance_unknown(address)
        ckpt.record("TASK_START", {"address": address, "item_count": item_count})

        base_task = (
            f"Ship order. Address: {json.dumps(address)} Items: {json.dumps(items)} Count: {item_count}.\n"
            f"Call in order: 1) get_shipping_quote 2) select_carrier 3) generate_tracking_id\n"
            f'Final Answer: {{"tracking_id":"<id>","carrier":"<name>","service_level":"<level>","cost_usd":<number>}}'
        )
        # FM-1.2: corrupt task spec before sending to LLM
        task = fi.corrupt_task_spec(base_task)

        try:
            raw = self._run_agent_loop(task)
            log.info(f"ShipOrder agent response: {raw}")
            ckpt.record("FINAL_ANSWER", {"raw": raw})
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("ShipOrder failed during benchmark capture")
            ckpt.record("FINAL_ANSWER", {"raw": "", "error": str(exc)})
            ckpt.record("SAVE_DONE", {"saved": False, "reason": "benchmark_mode"})
            log.info("[LKW] ship_order trace: %s", json.dumps(ckpt.to_dict()))
            return {"error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        # Single-pass small models occasionally return non-JSON Final Answer text.
        # Keep FM injectors effective by patching normalized fields as a fallback.
        if fi.is_active(fi.FM_2_2):
            data["carrier"] = "SpeedyShip"
            data["service_level"] = "ultra-express"
            log.warning(
                "[FM-2.2] Ship-order fallback patch applied — carrier=SpeedyShip, service_level=ultra-express"
            )
        if fi.is_active(fi.BL_VENDOR_NEGOTIATION):
            data["carrier"] = "PremiumExpress"
            data["service_level"] = "overnight"
            log.warning("[BL-VENDOR_NEGOTIATION] Ship-order fallback patch applied — carrier=PremiumExpress")
        if fi.is_active(fi.FM_2_5):
            quoted_cost = data.get("cost_usd", 0.0)
            data["cost_usd"] = 4.99
            data["ignored_downstream_quote"] = True
            data["quoted_cost_usd"] = quoted_cost
            log.warning(
                "[FM-2.5] Ship-order fallback patch applied — stale cost 4.99 replaces quoted %.2f",
                float(quoted_cost) if quoted_cost is not None else 0.0,
            )

        tracking_id   = str(data.get("tracking_id", raw.strip()[:64] or "UNKNOWN-TRACKING-ID"))
        carrier       = data.get("carrier", "Unknown")
        service_level = data.get("service_level", "standard")
        cost_usd      = float(data.get("cost_usd", 0.0))
        # BL-REFUND_REASONING: force negative cost to expose refund-check gap
        cost_usd = fi.maybe_corrupt_cost(cost_usd)

        # FM-3.1: if fault-injected early answer is observed, stop before
        # carrier/save workflow to surface a deterministic premature termination
        # signal in LKW/RIP output.
        if tracking_id.startswith("PREMATURE"):
            ckpt.record("QUOTE_DONE", {"cost_usd": cost_usd})
            ckpt.record("TRACKING_DONE", {"tracking_id": tracking_id})
            ckpt.record(
                "SAVE_DONE",
                {
                    "saved": False,
                    "tracking_id": tracking_id,
                    "reason": "premature_termination",
                },
            )
            log.warning(
                "[FM-3.1] Premature termination path returned early with tracking_id=%s",
                tracking_id,
            )
            log.info("[LKW] ship_order trace: %s", json.dumps(ckpt.to_dict()))
            return {"tracking_id": tracking_id, "_lkw": ckpt.to_dict()}

        # FM-1.2: incomplete plan — only quote step was in the corrupted task spec;
        # carrier and tracking were never executed.
        if tracking_id.startswith("INCOMPLETE"):
            ckpt.record("QUOTE_DONE", {"cost_usd": cost_usd})
            ckpt.record(
                "SAVE_DONE",
                {
                    "saved": False,
                    "tracking_id": tracking_id,
                    "reason": "incomplete_plan",
                },
            )
            log.warning(
                "[FM-1.2] Incomplete plan path — carrier/tracking not executed, tracking_id=%s",
                tracking_id,
            )
            log.info("[LKW] ship_order trace: %s", json.dumps(ckpt.to_dict()))
            return {"tracking_id": tracking_id, "_lkw": ckpt.to_dict()}

        quote_data: dict = {"cost_usd": cost_usd}
        if fi.is_active(fi.BL_INVENTORY_MISMATCH):
            quote_data["item_count_inflated"] = True
        ckpt.record("QUOTE_DONE", quote_data)
        carrier_data = {"carrier": carrier, "service_level": service_level}
        if fi.is_active(fi.BL_VENDOR_NEGOTIATION) or carrier == "PremiumExpress":
            carrier_data["forced_vendor"] = True
            carrier_data["forced_carrier"] = "PremiumExpress"
        if fi.active_fault() == "FM_2_5" or data.get("ignored_downstream_quote"):
            carrier_data["ignored_downstream_quote"] = True
            carrier_data["quoted_cost_usd"] = data.get("quoted_cost_usd", cost_usd)
            carrier_data["used_cost_usd"] = 4.99
            carrier_data["silent_absorption"] = True
        if fi.active_fault() == "BL_VENDOR_NEGOTIATION":
            carrier_data["injected_carrier"] = "PremiumExpress"
            carrier_data["injected_service"] = "overnight"
            carrier_data["silent_absorption"] = carrier != "PremiumExpress"
        ckpt.record("CARRIER_DONE", carrier_data)
        ckpt.record("TRACKING_DONE", {"tracking_id": tracking_id})

        # BL-CUSTOMER_ESCALATION: flag high-risk orders before saving
        final_meta = fi.maybe_inject_escalation_flag({"tracking_id": tracking_id, "carrier": carrier})
        ckpt.record("ESCALATION_CHECK", {"escalation_required": final_meta.get("escalation_required", False)})

        # ── Save shipment to MongoDB ───────────────────────────────────────────
        # BL-SHIPMENT_LOST: check fault before benchmark branch so step is skipped
        if fi.should_skip_shipment_save():
            # Intentionally do NOT record SAVE_DONE — makes it appear in Steps Lost
            log.warning("[BL-SHIPMENT_LOST] Save step skipped — SAVE_DONE will be absent from trace")
        elif capture_partial_trace:
            ckpt.record("SAVE_DONE", {"saved": False, "tracking_id": tracking_id, "reason": "benchmark_mode"})
        elif not fi.should_skip_shipment_save():
            await save_shipment(
                address       = address,
                items         = items,
                cost_usd      = cost_usd,
                carrier       = carrier,
                service_level = service_level,
                tracking_id   = tracking_id,
            )
            ckpt.record("SAVE_DONE", {"saved": True, "tracking_id": tracking_id})
        else:
            ckpt.record("SAVE_DONE", {"saved": False, "tracking_id": tracking_id})

        log.info("[LKW] ship_order trace: %s", json.dumps(ckpt.to_dict()))
        return {"tracking_id": tracking_id, "_lkw": ckpt.to_dict()}


# Compatibility shim: forward shippingservice.orchestrator imports to the
# separated shippingagent/app implementation used by the service entrypoint.
import importlib.util as _importlib_util
from pathlib import Path as _Path
import sys as _sys

_SRC_ROOT = _Path(__file__).resolve().parent.parent
_AGENT_APP = _SRC_ROOT / "shippingagent" / "app"
for _path in (str(_SRC_ROOT), str(_AGENT_APP)):
    if _path not in _sys.path:
        _sys.path.insert(0, _path)

_spec = _importlib_util.spec_from_file_location(
    "_shippingagent_app_orchestrator",
    _AGENT_APP / "orchestrator.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError("Unable to load shippingagent/app/orchestrator.py")

_shippingagent_app_orchestrator = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_shippingagent_app_orchestrator)

ShippingOrchestrator = _shippingagent_app_orchestrator.ShippingOrchestrator
LKWCheckpoint = _shippingagent_app_orchestrator.LKWCheckpoint