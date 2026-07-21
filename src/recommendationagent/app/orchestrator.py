"""
RecommendationOrchestrator
==========================
Pure LLM ReAct orchestrator for the recommendation service.

Mirrors ShippingOrchestrator exactly:
  - No gRPC calls — rule-based recommender sub-agent
  - Llama/Ollama OpenAI-compatible endpoint
  - ReAct loop with LKW checkpoints and fault injection

Checkout flow:
  1. get_recommendations → RecommenderAgent
  Final Answer: {"product_ids": [...]}
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

from app.agents.recommender_agent import RecommenderAgent

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
    EXPECTED_STEPS = ["TASK_START", "REC_DONE", "FINAL_ANSWER"]

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
            if c["step"] == "REC_DONE":
                if d.get("rec_count", 1) == 0 and fi.FAULT_MODE == "BL_EMPTY_RECS":
                    infected = c["step"]
                if d.get("self_recommendation"):
                    infected = c["step"]
                if d.get("injected"):
                    infected = c["step"]
                if d.get("shuffled"):
                    infected = c["step"]
                if d.get("hallucinated"):
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
1. get_recommendations — returns recommended product IDs based on cart contents
   Input: {"product_ids": [str, ...], "user_id": str}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are a recommendation agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


class RecommendationOrchestrator:
    """
    Orchestrates product recommendations using a self-hosted LLM (ReAct loop).
    Replaces the gRPC-backed RecommendationAgent for benchmark/fault-injection use.
    """

    def __init__(self):
        self.base_url         = LLAMA_BASE_URL.rstrip("/")
        self.model            = LLAMA_MODEL
        self.recommender      = RecommenderAgent()
        log.info("RecommendationOrchestrator ready — endpoint=%s model=%s",
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
        if tool_name == "get_recommendations":
            product_ids = tool_input.get("product_ids", [])
            user_id     = tool_input.get("user_id", "")

            # FM_2_5: replace user_id
            user_id = fi.maybe_swap_user_id(user_id)

            result = self.recommender.recommend(product_ids=product_ids, user_id=user_id)
            ids    = result.get("product_ids", [])

            # FM_2_2: hallucinate recommendation IDs
            ids = fi.maybe_hallucinate_rec_ids(ids)
            # BL_EMPTY_RECS: clear recommendations
            ids = fi.maybe_clear_recs(ids)
            # BL_SELF_RECOMMENDATION: echo input IDs
            ids = fi.maybe_self_recommend(ids, product_ids)
            # BL_INJECTION_RECS: prepend sponsored IDs
            ids, injected = fi.maybe_inject_sponsored(ids)
            # BL_SHUFFLED_RECS: reverse order
            ids, shuffled = fi.maybe_shuffle_recs(ids)

            result = {"product_ids": ids}
            ckpt.record("REC_DONE", {
                "rec_count":         len(ids),
                "self_recommendation": fi.FAULT_MODE == "BL_SELF_RECOMMENDATION",
                "injected":          injected,
                "shuffled":          shuffled,
                "hallucinated":      fi.FAULT_MODE == "FM_2_2",
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
                log.info("RecommendationOrchestrator: Final Answer after %d iterations",
                         iteration + 1)
                return value
            elif kind == "action":
                obs        = self._dispatch_tool(value, tool_input, ckpt)
                scratchpad += f"\nObservation: {obs}\n"
            else:
                scratchpad += (
                    "\nObservation: FORMAT_ERROR. Use Action/Action Input or Final Answer.\n"
                )

        raise RuntimeError(
            f"RecommendationOrchestrator: ReAct loop exceeded {MAX_ITERATIONS} iterations"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def recommend(
        self,
        product_ids: list = None,
        user_id: str = "",
        max_results: int = 5,
        capture_partial_trace: bool = False,
    ) -> dict:
        """
        Get product recommendations via LLM ReAct orchestration.
        Returns: {"product_ids": [...], "_lkw": dict}
        """
        ckpt = LKWCheckpoint()
        ckpt.record("TASK_START", {
            "product_ids": product_ids or [],
            "user_id":     user_id,
            "fault_mode":  fi.FAULT_MODE,
        })

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early:
            ckpt.record("FINAL_ANSWER", {"premature_termination": True})
            return {"product_ids": [], "error": "premature_termination",
                    "_lkw": ckpt.to_dict()}

        # FM_1_2: wrong method routing (log only — orchestrator still proceeds)
        fi.maybe_wrong_method_route("list_recommendations")

        task = (
            f"Task: Get product recommendations.\n"
            f"Cart products: {json.dumps(product_ids or [])}\n"
            f"User ID: {user_id}\n"
            f"Use get_recommendations tool.\n"
            f'Final Answer: {{"product_ids": [...]}}'
        )

        try:
            raw = self._run_agent_loop(task, ckpt)
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("RecommendationOrchestrator.recommend failed")
            ckpt.record("FINAL_ANSWER", {"error": str(exc)})
            return {"product_ids": [], "error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        ckpt.record("FINAL_ANSWER", {"raw": raw})
        log.info("[LKW] recommendation trace: %s", json.dumps(ckpt.to_dict()))

        return {
            "product_ids": data.get("product_ids", [])[:max_results],
            "_lkw":        ckpt.to_dict(),
        }
