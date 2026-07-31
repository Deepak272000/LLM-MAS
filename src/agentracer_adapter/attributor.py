"""
Phase 2 — Failure Attribution
==============================
Two attributors that read an AgenTracer-format trajectory and predict:
  i* — which agent caused the failure
  t* — which step was the decisive error step

AttributorA: RuleBasedAttributor
---------------------------------
Uses the LKW evidence directly — tier, missing steps, flags.
No LLM call. Deterministic.  Acts as the "perfect baseline" since it
effectively re-reads the same evidence that HITL classifier uses.

AttributorB: LLMAttributor
----------------------------
Uses the same Ollama backend (qwen2.5:3b) as the agents themselves.
Reads the full trajectory JSON as text and prompts the model to identify
who caused the failure and at which step.  This mirrors what AgenTracer's
trained Failure Attribution Reasoner does — except we use a prompt instead
of fine-tuned weights (weights not yet released by AgenTracer authors).

When AgenTracer releases model weights, replace _llm_call() with their
inference endpoint.  Everything else (trajectory format, evaluation) stays.

Usage
-----
    from agentracer_adapter.attributor import RuleBasedAttributor, LLMAttributor
    rb  = RuleBasedAttributor()
    llm = LLMAttributor(ollama_url="http://localhost:11434", model="qwen2.5:3b")

    result = rb.attribute(trajectory_dict)
    # {"error_agent": "paymentagent", "error_step": "FINAL_ANSWER",
    #  "confidence": 1.0, "method": "rule_based", "reasoning": "..."}
"""

import json
import re
import urllib.request
import urllib.error
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
#  Shared output schema
# ─────────────────────────────────────────────────────────────────────────────
def _result(error_agent, error_step, confidence, method, reasoning):
    return {
        "error_agent": error_agent,
        "error_step":  error_step,
        "confidence":  round(float(confidence), 3),
        "method":      method,
        "reasoning":   reasoning,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Attributor A — Rule-Based
# ─────────────────────────────────────────────────────────────────────────────
class RuleBasedAttributor:
    """
    Attribution logic mirrors the three HITL tiers:

    Tier 1 (structural):   first missing step after TASK_START is the
                           decisive step; agent = the agent whose steps vanished.
    Tier 2 (flag-based):   step with a truthy diagnostic flag is the
                           decisive step.
    Tier 3 (silent):       the step whose observation contains __fault_injected
                           is the decisive step.
    Cross-agent:           agent with __fault_injected in any step is the
                           error agent; that step is the decisive step.
    """

    def attribute(self, traj: dict) -> dict:
        outcome = traj.get("outcome", "PASSED")
        if outcome == "PASSED":
            return _result(None, None, 1.0, "rule_based",
                           "No fault injected — PASSED trajectory.")

        steps        = traj.get("trajectory", [])
        missing      = traj.get("missing_steps", [])
        agent_under  = traj.get("agent_under_test", "unknown")

        # ── Tier 1: missing steps ────────────────────────────────────────────
        if missing:
            # The agent that owns the missing steps
            error_agent = self._agent_of_missing(steps, missing, agent_under)
            # Decisive step = the first step that was lost
            error_step  = missing[0] if missing else None
            return _result(
                error_agent, error_step, 0.95, "rule_based",
                f"Tier 1 — {len(missing)} step(s) absent: {missing}. "
                f"First lost step '{error_step}' is the decisive point."
            )

        # ── Tier 2 / 3: scan steps for injected flag ─────────────────────────
        for step in steps:
            obs = step.get("observation", {})
            if obs.get("__fault_injected"):
                agent      = step["agent"]
                step_name  = step["action"]
                flags      = [k for k, v in obs.items()
                              if v is True and not k.startswith("__")]
                tier_hint  = "Tier 2" if flags else "Tier 3"
                return _result(
                    agent, step_name, 0.90, "rule_based",
                    f"{tier_hint} — fault marker found in {agent}:{step_name}. "
                    f"Diagnostic flags set: {flags or 'none (silent payload deviation)'}."
                )

        # ── Fallback: cannot attribute ────────────────────────────────────────
        return _result(
            agent_under, None, 0.30, "rule_based",
            "No structural or flag evidence found. "
            "Trajectory looks clean — possible LIVE mutant or INCONCLUSIVE run."
        )

    @staticmethod
    def _agent_of_missing(steps, missing, default):
        """Find which agent 'owns' the first missing step by looking at nearby steps."""
        # The step immediately before the first missing step tells us the agent
        if not steps:
            return default
        return steps[-1]["agent"] if steps else default


# ─────────────────────────────────────────────────────────────────────────────
#  Attributor B — LLM-based (Ollama)
# ─────────────────────────────────────────────────────────────────────────────
_ATTRIBUTION_PROMPT = """\
You are an expert failure attribution analyst for LLM multi-agent systems.

You are given an agent execution trajectory in JSON format.
Each step has: step_id, agent (who acted), action (checkpoint name), observation (data at that point).

Your task:
1. Identify WHICH agent caused the failure (error_agent).
2. Identify AT WHICH step the failure was first introduced (error_step — the checkpoint name).
3. Give a brief one-sentence reasoning.

Rules:
- If a step has observation fields like "hallucinated: true", "validation_bypassed: true",
  "amount_tampered: true", or "__fault_injected: true" — that is the infection point.
- If steps are MISSING from the trajectory (compare to expected flow), the first missing
  step is the decisive point and the last-seen agent is responsible.
- For cross-agent trajectories: the error may be in the FIRST agent even if the SECOND
  agent's trace looks structurally clean.
- If the trajectory looks completely clean (PASSED), set both to null.

Respond ONLY with valid JSON, no markdown, no explanation outside the JSON:
{
  "error_agent": "<agent_name or null>",
  "error_step": "<checkpoint_name or null>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<one sentence>"
}

TRAJECTORY:
"""

class LLMAttributor:
    def __init__(self, ollama_url: str = "http://localhost:11434",
                 model: str = "qwen2.5:3b", timeout: int = 60):
        self.ollama_url = ollama_url.rstrip("/")
        self.model      = model
        self.timeout    = timeout

    def attribute(self, traj: dict) -> dict:
        outcome = traj.get("outcome", "PASSED")
        if outcome == "PASSED":
            return _result(None, None, 1.0, "llm",
                           "PASSED trajectory — no attribution needed.")

        # Build a compact but complete trajectory text
        traj_text = self._compact_traj(traj)
        prompt    = _ATTRIBUTION_PROMPT + traj_text

        try:
            raw = self._llm_call(prompt)
            parsed = self._parse_response(raw)
            return _result(
                parsed.get("error_agent"),
                parsed.get("error_step"),
                parsed.get("confidence", 0.5),
                "llm",
                parsed.get("reasoning", raw[:200]),
            )
        except Exception as exc:
            return _result(
                traj.get("agent_under_test", "unknown"), None, 0.0, "llm",
                f"LLM attribution failed: {exc}"
            )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _compact_traj(self, traj: dict) -> str:
        """Build a concise trajectory string for the prompt."""
        lines = [
            f"trajectory_id: {traj.get('trajectory_id')}",
            f"fault_mode: {traj.get('fault_mode')}",
            f"outcome: {traj.get('outcome')}",
            f"missing_steps: {traj.get('missing_steps', [])}",
            "steps:",
        ]
        for s in traj.get("trajectory", []):
            # Strip internal __ keys to avoid prompt injection leakage
            obs = {k: v for k, v in s.get("observation", {}).items()
                   if not k.startswith("__")}
            lines.append(
                f"  [{s['step_id']}] agent={s['agent']} "
                f"action={s['action']} obs={json.dumps(obs, separators=(',', ':'))}"
            )
        return "\n".join(lines)

    def _llm_call(self, prompt: str) -> str:
        """POST to Ollama /api/generate and return the response text."""
        payload = json.dumps({
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }).encode()
        req = urllib.request.Request(
            f"{self.ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read())
            return body.get("response", "")

    def _parse_response(self, raw: str) -> dict:
        """Extract JSON from the LLM response (handles markdown code fences)."""
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?", "", raw).strip()
        # Find the first {...} block
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"No JSON found in LLM response: {raw[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
#  Quick smoke-test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = {
        "trajectory_id": "paymentagent__FM_3_1__run01",
        "agent_under_test": "paymentagent",
        "fault_mode": "FM_3_1",
        "outcome": "FAILED",
        "missing_steps": ["CARD_VALIDATED", "CHARGE_DONE", "SAVE_DONE"],
        "trajectory": [
            {"step_id": 0, "agent": "paymentagent",
             "action": "TASK_START", "observation": {"units": 10}},
            {"step_id": 1, "agent": "paymentagent",
             "action": "FINAL_ANSWER", "observation": {"result": "done"}},
        ],
    }
    rb  = RuleBasedAttributor()
    out = rb.attribute(sample)
    print("Rule-based result:", json.dumps(out, indent=2))
