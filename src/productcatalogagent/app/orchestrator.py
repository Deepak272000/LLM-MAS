"""
ProductCatalogOrchestrator
==========================
Pure LLM ReAct orchestrator for the product catalog service.

Mirrors ShippingOrchestrator exactly:
  - No gRPC calls — static product catalog
  - Llama/Ollama OpenAI-compatible endpoint
  - ReAct loop with LKW checkpoints and fault injection

Supported operations:
  list_products    → ProductLookupAgent.list_products()
  search_products  → ProductLookupAgent.search_products()
  get_product      → ProductLookupAgent.get_product()
  Final Answer: {"products": [...]} or {"product": {...}}
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

from app.agents.product_lookup_agent import ProductLookupAgent

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
    EXPECTED_STEPS = ["TASK_START", "CATALOG_DONE", "FINAL_ANSWER"]

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
            if c["step"] == "CATALOG_DONE":
                if d.get("product_count", 1) == 0 and fi.FAULT_MODE == "BL_PRODUCT_MISSING":
                    infected = c["step"]
                if d.get("prices_inflated"):
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
1. list_products — returns all products in the catalog
   Input: {}
2. search_products — searches catalog by keyword
   Input: {"query": str}
3. get_product — returns a single product by ID
   Input: {"product_id": str}

Format (use EXACTLY):
Thought: <brief reason>
Action: <tool name>
Action Input: <JSON>
...or...
Final Answer: <JSON>
"""

REACT_SYSTEM_PROMPT = (
    "You are a product catalog agent. Use tools step by step. "
    "Output only: Thought/Action/Action Input blocks, then Final Answer. No extra text."
)


class ProductCatalogOrchestrator:
    """
    Orchestrates product catalog queries using a self-hosted LLM (ReAct loop).
    Replaces the gRPC-backed ProductCatalogAgent for benchmark/fault-injection use.
    """

    def __init__(self):
        self.base_url      = LLAMA_BASE_URL.rstrip("/")
        self.model         = LLAMA_MODEL
        self.lookup_agent  = ProductLookupAgent()
        log.info("ProductCatalogOrchestrator ready — endpoint=%s model=%s",
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
        if tool_name == "list_products":
            # FM_2_5: tamper query (not applicable for list, but log anyway)
            result = self.lookup_agent.list_products()
            products = result.get("products", [])
            # FM_1_2: swap action to search instead of list
            # (handled at loop level; if wrong action was called we still serve)
            # FM_2_2: hallucinate product data
            result = fi.maybe_hallucinate_products(result)
            # BL_PRODUCT_MISSING: return empty list
            result = fi.maybe_clear_products(result)
            # BL_PRICE_MANIPULATION: inflate prices
            result = fi.maybe_inflate_prices(result)
            # BL_DUPLICATE_PRODUCT: duplicate first product
            result = fi.maybe_duplicate_product(result)
            # BL_WRONG_CATEGORY: replace categories
            result = fi.maybe_replace_categories(result)
            final_products = result.get("products", [])
            ckpt.record("CATALOG_DONE", {
                "action":          "list",
                "product_count":   len(final_products),
                "prices_inflated": fi.FAULT_MODE == "BL_PRICE_MANIPULATION",
                "hallucinated":    fi.FAULT_MODE == "FM_2_2",
            })

        elif tool_name == "search_products":
            query = tool_input.get("query", "")
            # FM_2_5: tamper query
            query = fi.maybe_tamper_query_str(query)
            result = self.lookup_agent.search_products(query)
            result = fi.maybe_hallucinate_products(result)
            result = fi.maybe_clear_products(result)
            result = fi.maybe_inflate_prices(result)
            final_products = result.get("products", [])
            ckpt.record("CATALOG_DONE", {
                "action":        "search",
                "query":         query,
                "product_count": len(final_products),
                "hallucinated":  fi.FAULT_MODE == "FM_2_2",
            })

        elif tool_name == "get_product":
            product_id = tool_input.get("product_id", "")
            result = self.lookup_agent.get_product(product_id)
            result = fi.maybe_hallucinate_products(result)
            result = fi.maybe_inflate_prices(result)
            ckpt.record("CATALOG_DONE", {
                "action":     "get",
                "product_id": product_id,
                "found":      result.get("product") is not None,
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
                log.info("ProductCatalogOrchestrator: Final Answer after %d iterations",
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
            f"ProductCatalogOrchestrator: ReAct loop exceeded {MAX_ITERATIONS} iterations"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        query: str = "list products",
        product_ids: list = None,
        capture_partial_trace: bool = False,
    ) -> dict:
        """
        Serve a product catalog request via LLM ReAct orchestration.
        Returns: {"products": [...], "_lkw": dict}
        """
        ckpt   = LKWCheckpoint()
        action = "get_product" if product_ids else (
            "list_products" if ("all products" in query.lower() or
                                "list" in query.lower() or query.lower() == "catalog")
            else "search_products"
        )

        ckpt.record("TASK_START", {
            "query":       query,
            "product_ids": product_ids,
            "action":      action,
            "fault_mode":  fi.FAULT_MODE,
        })

        # FM_3_1: premature termination
        early = fi.maybe_premature_termination()
        if early:
            ckpt.record("FINAL_ANSWER", {"premature_termination": True})
            return {"products": [], "error": "premature_termination",
                    "_lkw": ckpt.to_dict()}

        # FM_1_2: swap action routing
        action = fi.maybe_swap_action(action)

        if product_ids:
            task = (
                f"Task: Fetch products by ID.\n"
                f"Product IDs: {product_ids}\n"
                f"Use get_product for each ID.\n"
                f'Final Answer: {{"products": [...]}}'
            )
        elif action == "list_products":
            task = (
                "Task: List all products.\n"
                "Use list_products tool.\n"
                'Final Answer: {"products": [...]}'
            )
        else:
            task = (
                f"Task: Search the product catalog.\n"
                f"Query: {query}\n"
                f"Use search_products tool.\n"
                f'Final Answer: {{"products": [...]}}'
            )

        try:
            raw = self._run_agent_loop(task, ckpt)
        except Exception as exc:
            if not capture_partial_trace:
                raise
            log.exception("ProductCatalogOrchestrator.run failed")
            ckpt.record("FINAL_ANSWER", {"error": str(exc)})
            return {"products": [], "error": str(exc), "_lkw": ckpt.to_dict()}

        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        ckpt.record("FINAL_ANSWER", {"raw": raw})
        log.info("[LKW] catalog trace: %s", json.dumps(ckpt.to_dict()))

        products = data.get("products", [])
        if "product" in data and data["product"]:
            products = [data["product"]]

        return {
            "products": products,
            "action":   action,
            "_lkw":     ckpt.to_dict(),
        }
