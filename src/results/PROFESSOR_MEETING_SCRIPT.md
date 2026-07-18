# Professor Meeting Script — LKW Instrumentation to Present Day
**Date of meeting:** July 16, 2026  
**Author:** Deepak Sunil Chavan  
**Starting point:** Fault injection was already set up and discussed. This script covers everything from when we designed and inserted the LKW checkpoint flags onward.  
**Use:** Read naturally, in your own words. Sections marked [PAUSE] = wait for reaction before continuing.

---

## OPENING — Where We Left Off (1 min)

"So in our last meeting we had the fault injection modes set up — the environment variables, the fault functions, the MAST taxonomy. But at that point we had no measurement. We could *inject* a fault but we had no formal way to answer: did the agent detect it? Where exactly did things go wrong? Did it spread?

That was the gap. And everything I'm going to walk you through today is what we built to fill that gap — starting from the LKW checkpoint flags we inserted into the code."

---

## CHAPTER 1 — The Core Idea: What an LKW Flag Is (3 min)

"The first thing we did was design what we call **LKW checkpoints** — Last Known Well points. The idea is simple: at every meaningful step inside an agent's execution, we emit a structured flag into a list. It's not free-form logging. Each flag has a fixed schema:

```json
{
  "step": "ADS_FETCHED",
  "timestamp": "2026-06-18T12:09:21Z",
  "fault_mode": "FM_2_2",
  "data": {
    "count": 3,
    "hallucinated": true,
    "injected": false
  }
}
```

There are three things to notice here. First, the **step name** — it's not a log message, it's a named milestone like `TASK_START`, `ADS_FETCHED`, or `FINAL_ANSWER`. Second, the **fault_mode** is stamped onto every record, so we always know what injection was active. Third, the **data block** contains boolean flags that directly describe the fault — `hallucinated: true`, `amount_tampered: true`, `save_skipped: true`.

This gives us a machine-readable trace for every single agent run."

[PAUSE]

---

## CHAPTER 2 — How We Actually Inserted the Flags Into the Code (4 min)

"Let me show you exactly how this was wired. Every agent follows a LangGraph node pipeline — `input_node` → one or more domain nodes → `output_node`. We inserted `record_checkpoint` calls at the boundary of each node, not inside the fault functions themselves.

For AdServiceAgent, the graph looks like this:

```
input_node
  → fi.record_checkpoint("TASK_START", { instruction, fault_mode })
  → [fault functions run: maybe_tamper_context, maybe_swap_category]
  → fi.record_checkpoint("CONTEXT_EXTRACTED", { context_keys, context_tampered, category_swapped })
  → [FM_3_1 guard: if premature termination, emit FINAL_ANSWER and exit]

ad_lookup_node
  → [fault functions: maybe_hallucinate_ads, maybe_empty_ads, maybe_inject_ads, ...]
  → fi.record_checkpoint("ADS_FETCHED", { count, hallucinated, empty_ads, injected, ... })

output_node
  → fi.record_checkpoint("FINAL_ANSWER", { ads_count })
```

And we did this for every agent — PaymentAgent gets `TASK_START → CARD_VALIDATED → CHARGE_DONE → SAVE_DONE → FINAL_ANSWER`. ShippingService gets seven checkpoints including `QUOTE_DONE`, `CARRIER_DONE`, `TRACKING_DONE`, `ESCALATION_CHECK`, `SAVE_DONE`.

The module itself — `fault_injection.py` inside each agent — has a `_global_lkw` list, a `record_checkpoint` function that appends to it, and a `get_lkw` function that returns the full trace. We call `clear_lkw` before each run so traces don't bleed between tests.

The key design principle was: **the fault function injects the corruption, and the LKW flag witnesses it**. The flag doesn't stop the fault — it records it. That separation is what allows us to measure what actually happened versus what should have happened."

[PAUSE]

---

## CHAPTER 3 — RIP Analysis: What We Compute From the Flags (3 min)

"Once we had the traces, we needed a way to extract meaning from them automatically. That's the **RIP layer** — Reachability, Infection, Propagation.

We define a `EXPECTED_STEPS` list per agent. For AdServiceAgent that's `['TASK_START', 'CONTEXT_EXTRACTED', 'ADS_FETCHED', 'FINAL_ANSWER']`. The baseline NONE run should always produce exactly those four steps.

Now for a fault run, RIP computes:

- **Reachability** — which of the expected steps actually appeared in the trace?
- **Infection Point** — at which step did the data first deviate from the healthy baseline? We detect this by comparing boolean flags against the NONE baseline. If `hallucinated: true` appears at `ADS_FETCHED` and wasn't there in NONE, that's the infection point.
- **Propagation Depth** — how many expected downstream steps are *missing* after the infection point?

So for FM_3_1 on AdServiceAgent: `TASK_START` fires, `CONTEXT_EXTRACTED` fires, then the premature termination guard kicks in and logs `FINAL_ANSWER` immediately. `ADS_FETCHED` is never reached. The RIP output is: infection_point=`FINAL_ANSWER`, propagation_depth=1, steps_lost=['ADS_FETCHED'].

For FM_2_2 hallucination: all four steps fire — depth=0, steps_lost=[] — but the data at `ADS_FETCHED` carries `hallucinated: true`. Infection point is `ADS_FETCHED`, but nothing downstream is structurally missing. The agent completed correctly. The corruption is purely in the data values.

That distinction — depth=0 but infected — is critical. It's what we later call a **Tier 3 silent fault**."

---

## CHAPTER 4 — Reproducibility: Running It 3 Times Each, 100% Stable (3 min)

"Once the flags were in and RIP was computing, the first question we had to answer was: are these measurements consistent? If we run the same fault mode three times, do we get the exact same LKW fingerprint every time?

We ran every fault mode three times independently on SPEED HPC — Concordia's compute cluster. That's 9 fault modes × 3 runs × 6 agents = 162 individual runs for the deterministic agents. Plus the ShippingService with the real LLM for another 30 runs.

We defined three stability labels:
- `STABLE_PASS` — the NONE baseline: clean across all three runs
- `STABLE_FAULT` — fault mode: identical LKW fingerprint in all three runs — same infection point, same steps lost, same depth
- `UNSTABLE` — at least one run produced a different fingerprint

**Result: zero UNSTABLE entries across all 64 mode-runs. 100% stability.**

This is the formal proof that our LKW instrumentation is not just working — it's producing *publishable-quality* measurements. Every fault we inject lands in exactly the same place in the trace, every single time. The JSON output — `stability_summary.json` — is in the results folder and that's what we cite in the paper."

[PAUSE]

---

## CHAPTER 5 — The HITL Tier Classification (3 min)

"With 58 classified fault modes across 7 agents, the next question was: *which faults can a monitoring system catch automatically, and which need a human?*

We wrote `hitl_detector.py` — a fully automated tool that reads every fault result JSON and assigns a tier based purely on what the LKW flags say.

**Tier 1 — Structural.** `propagation_depth > 0`. Steps are missing from the trace. You can detect this by doing a simple step count against the expected list. No semantic analysis. **11 faults are Tier 1.** Example: FM_3_1 on PaymentAgent — `CARD_VALIDATED`, `CHARGE_DONE`, `SAVE_DONE` all missing — depth=3. Any alert rule that counts checkpoints catches this instantly.

**Tier 2 — Flag-Detectable.** All steps reached, depth=0, but the LKW data at some checkpoint carries a new boolean flag that wasn't in the baseline. Like `amount_tampered: true` at `CARD_VALIDATED`, or `double_charge: true` at `SAVE_DONE`. You need a monitoring rule — 'alert if this field is true' — but no semantic reasoning. **40 faults are Tier 2.** This is the largest category.

**Tier 3 — Silent.** All steps reached, depth=0, no operational flag at all — but the *values* are semantically wrong. FM_2_2 hallucination falls here. `hallucinated: true` is a flag, but the actual corrupted value — like `transaction_id = 'FAKE-TXN-abc123'` — is not detectable without checking the value content. **7 faults are Tier 3.**

The practical summary: if you just count steps, you catch 11 faults automatically. If you add flag monitoring rules, you catch 51. If you want to catch all 58, you need semantic validation — which is the hardest problem and the motivation for everything that came next."

---

## CHAPTER 6 — Cross-Agent Propagation: Why Single-Agent LKW Isn't Enough (4 min)

"This was the result that made the whole measurement framework matter. We had been measuring one agent at a time. But in a real microservice system, agents pass data to each other. So we designed cross-agent propagation experiments.

We picked FM_2_2 — hallucination — specifically because it's Tier 3: depth=0, all steps clean. It's the hardest fault to catch. We injected it at an upstream agent and let the downstream agent run with no fault at all.

**Chain A.** CurrencyAgent with FM_2_2 hallucination passes its result to PaymentAgent.

CurrencyAgent should convert 9 EUR. With FM_2_2, it instead returns **1337 EUR**. The LKW trace at CurrencyAgent: infection_point=`CONVERT_DONE`, depth=0, all steps present. Clean trace except for the corrupted value.

PaymentAgent receives 1337 EUR. It validates the card. It charges 1337 EUR. It saves the transaction. It returns success. **All five of PaymentAgent's checkpoints fire. Infection_point is null. Depth is zero.** PaymentAgent's LKW trace is completely clean.

The overcharge is 14,755 percent. And there is no alert anywhere in the system.

**Chain B.** ProductCatalogAgent with FM_2_2 passes a phantom product ID to RecommendationAgent.

CatalogAgent returns `HALLUCINATED-001`. RecommendationAgent's LKW: all 3 steps clean, infection_point null. It generates recommendations for a product that doesn't exist. No alert.

The finding is: **a structurally-correct downstream agent cannot self-detect or recover from upstream hallucination**. Single-agent LKW is necessary but not sufficient. This is the formal justification for why we needed the next layer."

[PAUSE — this is the most important result, let it land]

---

## CHAPTER 7 — Boundary Validation: Extending LKW to Agent Handoffs (3 min)

"The response to the cross-agent propagation finding was to add a new type of LKW checkpoint — the `BOUNDARY_CHECK` step.

The idea: at the point where one agent's output becomes another agent's input, we define a *contract* — what the value should look like, what ranges are valid, what entities should exist. When the code reaches that handoff, it calls `boundary_contract(boundary_name, expected, observed)` and the result is recorded as a `BOUNDARY_CHECK` checkpoint in the LKW trace.

For example, in AdServiceAgent's `ad_lookup_node`, after all the fault functions run and before we record `ADS_FETCHED`, the code now does:

```python
boundary = boundary_contract(
    "ad_lookup_to_response",
    expected_ads,        # what we saw in the baseline
    observed_ads,        # what the agent actually produced
)
fi.record_checkpoint("BOUNDARY_CHECK", {
    "boundary": boundary["boundary"],
    "alert": boundary["alert"],
    "status": boundary["status"],
    "expected": boundary["expected"],
    "observed": boundary["observed"],
    "violations": boundary["violations"],
})
```

If `alert=True`, the handoff was a semantic violation — even if all downstream steps fired normally.

Now when Chain A runs, the `currency_to_payment` boundary fires: expected=9, observed=1337, delta=1328, alert=True. **The hallucination is machine-detectable at the boundary before it reaches PaymentAgent.**

We have five boundary contracts implemented: `currency_to_payment`, `catalog_to_recommendation`, `carrier_to_tracking`, `quote_to_carrier_selection`, and `email_body_to_send`.

We also built a live dashboard — `boundary_dashboard.py` — that shows each BOUNDARY_CHECK event in real time as the scenario runs. When I ran it locally: 13 total events, 8 alerts, 5 clean. You can open it at port 8765 and watch the flags appear."

---

## CHAPTER 8 — Recovery: Adding RECOVERY_ACTION Into the LKW Trace (3 min)

"Detecting a boundary violation is not enough. We needed a defined response — and that response also had to be visible in the LKW trace.

So we built a policy layer in `boundary_recovery.py` with seven recovery policies:
1. **block_and_request_hitl** — for financial mismatches. We will not auto-correct a charge. Human must approve.
2. **fallback_to_last_known_good** — for semantic hallucinations like a fake carrier name or phantom product. Substitute the last verified value.
3. **retry_current_step** — for transient failures.
4. **continue_with_flag** — for low-risk anomalies that just need logging.

And a catch-all for anything that doesn't match — marks it `UNHANDLED_BOUNDARY` and routes to HITL. No silent pass-through.

When a recovery fires, we add a **`RECOVERY_ACTION` step** to the LKW trace. So the trace for a recovered Currency/Payment chain now looks like:

```
TASK_START → BOUNDARY_CHECK [alert=True, delta=1328]
→ RECOVERY_ACTION [action=block_and_request_hitl, charge_blocked=True]
→ FINAL_ANSWER
```

The recovery is visible. It's auditable. It's in the same trace format as every other step.

We wired this to four agents — CurrencyAgent, ProductCatalogAgent, AdServiceAgent, EmailServiceAgent — and wrote 19 test cases in `test_recovery_wiring.py`. Each test checks that the right steps appear in the LKW trace:

```python
_lkw_steps = [cp["step"] for cp in _currency_result.get("lkw", [])]
assert "RECOVERY_ACTION" in _lkw_steps
assert "FINAL_ANSWER" in _lkw_steps
assert "CONVERT_DONE" not in _lkw_steps  # blocked before charge
```

**All 19 pass. 19 out of 19.**

Concrete outcome: the 1337 EUR charge is blocked — `charge_blocked=True`, `prevented_loss_eur=1328`. The phantom product is replaced — `recovered_product_ids=['PROD-001']`."

[PAUSE]

---

## CHAPTER 9 — How This All Became a Paper (2 min)

"Everything I just described is written up as a formal research paper targeting ACM SIGSOFT FSE 2027 — the Foundation for Software Engineering conference — using the full ACM double-column sigconf format.

Because of your earlier question specifically about measurement design — how do we know the checkpoints are measuring the right things — we added a dedicated **Section 5: LKW Instrumentation Design**. That section didn't exist before your feedback. It now formally defines:
- Five checkpoint placement classes — at task entry, at domain decision points, at output commits, at agent boundaries, at failure intercepts
- A per-agent checkpoint table showing exactly which steps each of the 7 agents emits
- The formal infection equation — mathematically defining what 'infection point' means
- A per-failure signature table mapping each MAST fault class to which LKW steps it affects and which flags it sets

That section is the answer to the question 'how precisely did you design the measurement.' The answer is: we designed it formally, we specified it per-agent, and we proved it reproducible at 100% stability across 192 runs."

---

## CHAPTER 10 — Measurement Quality and Honest Limitations (2 min)

"Two things I want to be upfront about.

**On quality:** Our ground truth is fully controlled — we inject the fault, so we know what the correct infection_point, depth, and flag should be. We wrote a formal scoring rubric with four criteria: Detection, Location, Flag, Depth — each worth one point per fault mode. All 58 modes score 4/4 on the automated evaluation except the one confirmed False Negative. The stability matrices, the HITL report, the boundary events, the recovery demo — all are JSON files in the results folder with exact timestamps from SPEED HPC jobs. These are real evidence files, not mockups.

**On honest limitations:** Six of our seven agents are deterministic — no LLM in the loop. They validate that the instrumentation framework works correctly, not LLM fault detection capability. ShippingService is the one real-LLM agent, running qwen2.5-coder:14b on a SPEED A100. One fault mode — compliance ambiguity — is a confirmed False Negative: the 14-billion parameter model resolved the injected ambiguity gracefully without producing any fault signal. We document that openly in the paper as a limitation and also as a novel finding — bigger models are more resilient to semantic ambiguity injection."

---

## CLOSING — One-Sentence Summary

"So in summary: we took an existing fault injection setup, designed a structured checkpoint instrumentation layer called LKW, inserted it into every agent node in the code, built RIP analysis on top of it, proved 100% reproducibility across 192 runs on SPEED HPC, classified 58 fault modes by HITL tier, proved that hallucinations propagate silently cross-agent with 14,000-percent financial overcharge and zero structural alert, added BOUNDARY_CHECK and RECOVERY_ACTION as two new checkpoint types to extend the measurement to agent handoffs, wired recovery policies with 19 passing tests, and wrote up the entire thing as an FSE 2027 paper with a formal instrumentation design section added specifically based on your feedback."

---

## ANTICIPATED PROFESSOR QUESTIONS + BRIEF ANSWERS

**Q: "Why did you embed the fault flags inside the LKW data rather than a separate log?"**
A: "Because we need both in the same trace. If we log faults separately, we have to correlate two streams to compute infection point. By embedding `hallucinated: true` directly at the checkpoint where the fault lands, the RIP analysis can read a single ordered list and find the first deviation point in one pass. It also means the ground truth label — the fault_mode field — is stamped on every checkpoint record, so there's never ambiguity about which run produced which trace."

**Q: "How do you know the BOUNDARY_CHECK checkpoint isn't adding false positives?"**
A: "Good question. The boundary contract compares observed against expected using typed validators — delta bounds for financial values, entity existence checks for product IDs, schema checks for carrier names. We set the expected value from the NONE baseline run, not from a hardcoded constant. So a legitimate rate change wouldn't trigger a false alert because we'd update the baseline. In the cross-agent experiments, we ran the baseline chains first to set the expected values, then ran the infected chains. No false positives in any of the 13 boundary events recorded."

**Q: "The 14,755 percent overcharge — is that realistic? Would a real system let that through?"**
A: "Without the boundary contracts, yes — exactly. A payment system that trusts the currency service completely and doesn't range-check incoming amounts would process 1337 EUR exactly the same as 9 EUR. That's the point. The LKW trace at both agents looks healthy. The only protection is the boundary contract, which we added precisely because of this experiment. After the contracts are in, `currency_to_payment` catches it with alert=True and blocks the charge."

**Q: "Why did you pick FM_2_2 hallucination for the cross-agent experiment?"**
A: "Because it's the worst case. FM_2_2 has depth=0 — all checkpoints fire, the agent completes, the downstream system sees a healthy trace. It's the only fault class that is structurally invisible to RIP analysis. If we had used FM_3_1 premature termination, the downstream agent would receive an incomplete payload and its own step count would drop — detectable. FM_2_2 propagates silently. Choosing the hardest case for the cross-agent experiment makes the finding stronger."

**Q: "What's left to do before the paper is ready?"**
A: "Three things. Replace 13 stub citations with real papers — we have arXiv IDs for all of them. Add your name and Peyman's name to the author block. And do a final LaTeX compile check. The content is complete."

---

*End of script. Estimated delivery time: 18–22 minutes with pauses.*

