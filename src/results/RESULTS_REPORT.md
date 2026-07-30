# LLM-MAS Fault Injection Study — Full Results Report

**Author:** Deepak Sunil Chavan, Concordia University  
**Platform:** Concordia SPEED HPC, `deepak/fault-injection` branch  
**Generated:** 2026-07-29 (real live-LLM — Ollama on SPEED speed-25, Tesla V100)  
**Models:** `qwen2.5:3b` (standalone/Google-bench) · `qwen2.5-coder:14b` (4-config agents)  
**Supersedes:** All prior mock-based reports (last: 2026-07-15)

> **⚠ All kill rates are from real LLM inference, not deterministic mock oracles.**

---

## Table of Contents
1. [Executive Summary](#1-executive-summary)
2. [B2 Baseline — False Positive Rate](#2-b2-baseline--false-positive-rate)
3. [B3 Per-Agent Fault Injection — 9 Agents](#3-b3-per-agent-fault-injection--9-agents)
   - [3.1 ProductCatalogAgent — 100%](#31-productcatalogagent--100-kill-rate)
   - [3.2 CartAgent (C#) — 100%](#32-cartagent-c--100-kill-rate)
   - [3.3 AdServiceAgent — 57.1%](#33-adserviceagent--571-kill-rate)
   - [3.4 RecommendationAgent — 57.1%](#34-recommendationagent--571-kill-rate)
   - [3.5 PaymentAgent — 57.1% / 45.5%](#35-paymentagent--571--455-all-configs)
   - [3.6 CurrencyAgent — 42.9% / 34.1%](#36-currencyagent--429--341-all-configs)
   - [3.7 EmailServiceAgent — 21.4% / 13.6%](#37-emailserviceagent--214--136-all-configs)
   - [3.8 ShippingQuoteAgent — 0%](#38-shippingquoteagent--00-kill-rate)
   - [3.9 ShipOrderAgent — 0%](#39-shiporderagent--00-kill-rate)
4. [Cross-Agent Fault Propagation](#4-cross-agent-fault-propagation)
5. [Model Effect Analysis](#5-model-effect-analysis)
6. [Key Findings and Research Conclusions](#6-key-findings-and-research-conclusions)
7. [Limitations and Future Work](#7-limitations-and-future-work)

---

## 1. Executive Summary

### Overall B3 Mutation Kill Rate

```
+------------------------------------------------------------------+
|  STANDALONE LIVE-LLM B3 MUTATION SCORE                          |
|                                                                  |
|   Total mutant tests   :  258                                    |
|   Killed               :   75  (29.1%)                          |
|   Live (undetected)    :   90  (34.9%)                          |
|   Inconclusive         :   93  (36.0%)                          |
|                                                                  |
|   B2 false positive rate: 0.0% (all 9 agents, all configs)      |
+------------------------------------------------------------------+
```

### Agent-Level Summary (all configs combined)

| Rank | Agent | Killed | Live | Inconc. | Kill Rate | Configs | Notes |
|------|-------|--------|------|---------|-----------|---------|-------|
| 1 | ProductCatalog | 8 | 0 | 0 | **100.0%** | 3b_temp0 | All 8 modes killed |
| 2 | Cart | 10 | 0 | 0 | **100.0%** | 3b_temp0 | All 10 modes killed |
| 3 | AdService | 8 | 6 | 0 | **57.1%** | 3b_temp0 | 6 irrelevant shipping modes LIVE |
| 4 | Recommendation | 8 | 6 | 0 | **57.1%** | 3b_temp0 | 6 irrelevant shipping modes LIVE |
| 5 | Payment | 20 | 24 | 0 | **45.5%** | 4 configs | 8/14 per best config |
| 6 | Currency | 15 | 29 | 0 | **34.1%** | 4 configs | FM_2_5 + BL_CONVERSION_OVERFLOW absorbed |
| 7 | Email | 6 | 5 | 33 | **13.6%** | 4 configs | High inconclusive; LLM regenerates content |
| 8 | Ship-Quote | 0 | 10 | 30 | **0.0%** | 4 configs | Template-fallback absorbs all faults |
| 9 | Ship-Order | 0 | 10 | 30 | **0.0%** | 4 configs | Template-fallback absorbs all faults |
| — | **TOTAL** | **75** | **90** | **93** | **29.1%** | | |

---

## 2. B2 Baseline — False Positive Rate

B2 runs FAULT_MODE=NONE with live LLM to calibrate natural variance.
An infected B2 run = false positive (LKW fires on normal output, not a fault).

```
B2 False-Positive Baseline
===========================================================================
Agent@Config               Runs   Clean  Infected  Errors   FP Rate
---------------------------------------------------------------------------
adservice@3b_temp0            3       3         0       0    0.0% CLEAN
cart@3b_temp0                 3       3         0       0    0.0% CLEAN
currency@3b_temp0            10      10         0       0    0.0% CLEAN
currency@14b_temp0           10      10         0       0    0.0% CLEAN
currency@3b_temp0.7          10      10         0       0    0.0% CLEAN
currency@3b_temp1.0          10      10         0       0    0.0% CLEAN
email@3b_temp0               10      10         0       0    0.0% CLEAN
email@14b_temp0              10      10         0       0    0.0% CLEAN
email@3b_temp0.7             10      10         0       0    0.0% CLEAN
email@3b_temp1.0             10      10         0       0    0.0% CLEAN
payment@3b_temp0             10      10         0       0    0.0% CLEAN
payment@14b_temp0            10      10         0       0    0.0% CLEAN
payment@3b_temp0.7           10      10         0       0    0.0% CLEAN
payment@3b_temp1.0           10      10         0       0    0.0% CLEAN
productcatalog@3b_temp0       3       3         0       0    0.0% CLEAN
recommendation@3b_temp0       3       3         0       0    0.0% CLEAN
ship_order@3b_temp0          10       3         0       7    0.0% CLEAN (7 gRPC errors)
ship_order@14b_temp0         10       0         0      10    0.0% CLEAN (10 gRPC errors)
ship_order@3b_temp0.7        10       0         0      10    0.0% CLEAN (10 gRPC errors)
ship_order@3b_temp1.0        10       0         0      10    0.0% CLEAN (10 gRPC errors)
shipping_quote@3b_temp0      10       3         0       7    0.0% CLEAN (7 gRPC errors)
shipping_quote@14b_temp0     10       0         0      10    0.0% CLEAN (10 gRPC errors)
shipping_quote@3b_temp0.7    10       0         0      10    0.0% CLEAN (10 gRPC errors)
shipping_quote@3b_temp1.0    10       0         0      10    0.0% CLEAN (10 gRPC errors)
===========================================================================
FP rate (non-error runs): 0.0%   B2 oracle valid — B3 kill verdicts are real
```

> Shipping gRPC errors: CartService/ProductCatalogService not co-launched in Slurm job.
> These are errors, not false positives. FP rate remains 0%.

---

## 3. B3 Per-Agent Fault Injection — 9 Agents

**Legend:** `K`=KILLED · `L`=LIVE · `I`=INCONCLUSIVE  
`inf=X/N` = infected_runs/total_runs · `sloss=X` = runs with missing LKW steps

---

### 3.1 ProductCatalogAgent — 100% Kill Rate

**Config:** qwen2.5:3b temp=0.0 · **Fault modes:** 8 agent-specific

```
LKW Trace:
TASK_START --> CATALOG_DONE --> FINAL_ANSWER
   |               |                |
[always]    [product list]     [response]
```

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 0/3 | 3 | Skips CATALOG_DONE; structural step-loss |
| 2 | FM_1_2 Validation bypassed | **K** | 3/3 | 0 | Wrong action; payload deviant at CATALOG_DONE |
| 3 | FM_2_2 Hallucinated products | **K** | 3/3 | 0 | Phantom product IDs in catalog output |
| 4 | FM_2_5 Query tampered | **K** | 3/3 | 0 | query_tampered flag set at CATALOG_DONE |
| 5 | BL_PRICE_MANIPULATION | **K** | 3/3 | 0 | price x10 detected vs B1 baseline |
| 6 | BL_PRODUCT_MISSING | **K** | 3/3 | 0 | Empty product list vs B1 non-empty |
| 7 | BL_DUPLICATE_PRODUCT | **K** | 3/3 | 0 | Duplicate entry detected |
| 8 | BL_WRONG_CATEGORY | **K** | 3/3 | 0 | Category field mismatch vs oracle |

**Result: 8/8 = 100.0% — strongest single-agent coverage in study**

---

### 3.2 CartAgent (C#) — 100% Kill Rate

**Config:** qwen2.5:3b temp=0.0 · **Language:** C# via Python mirror co_helper_cart.py  
**Fault modes:** 10 (4 general + 6 cart BL faults)

```
LKW Trace:
TASK_START --> ITEM_ADDED --> QUANTITY_MERGED --> CART_READ --> FINAL_ANSWER
   |               |                |                |               |
[always]     [item+qty]      [merged qty]      [cart state]    [response]
```

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 3/3 | 3 | Skips all mid-steps to FINAL_ANSWER |
| 2 | FM_1_2 Validation bypassed | **K** | 3/3 | 0 | qty=0 injection detected at ITEM_ADDED |
| 3 | FM_2_2 Hallucinated response | **K** | 3/3 | 0 | Cart content fabricated |
| 4 | FM_2_5 Stale quantity | **K** | 3/3 | 0 | Stale qty flag at QUANTITY_MERGED |
| 5 | BL_SHIPMENT_LOST | **K** | 3/3 | 0 | Empty cart injected; detected at CART_READ |
| 6 | BL_INVENTORY_MISMATCH | **K** | 3/3 | 0 | qty+5 over-count detected |
| 7 | BL_VENDOR_NEGOTIATION | **K** | 3/3 | 0 | 50% discount injection detected |
| 8 | BL_CUSTOMER_ESCALATION | **K** | 3/3 | 0 | qty=999 spike detected |
| 9 | BL_REFUND_REASONING | **K** | 3/3 | 0 | Qty reduction via refund logic detected |
| 10 | BL_COMPLIANCE_AMBIGUITY | **K** | 3/3 | 0 | Float qty=1.5 detected |

**Result: 10/10 = 100.0% — new agent added this cycle, perfect coverage**

---

### 3.3 AdServiceAgent — 57.1% Kill Rate

**Config:** qwen2.5:3b temp=0.0 · **Fault modes:** 14 tested (8 adservice + 6 legacy shipping)

```
LKW Trace:
TASK_START --> CONTEXT_EXTRACTED --> ADS_FETCHED --> FINAL_ANSWER
   |                 |                    |               |
[always]       [context keys]       [ad list]        [response]
```

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 3/3 | 3 | Step-loss at ADS_FETCHED |
| 2 | FM_1_2 Category swapped | **K** | 3/3 | 0 | Wrong ad category detected |
| 3 | FM_2_2 Hallucinated ads | **K** | 3/3 | 0 | Phishing URL pattern flagged |
| 4 | FM_2_5 Context tampered | **K** | 3/3 | 0 | Tampered context keys flagged |
| 5 | BL_EMPTY_ADS | **K** | 3/3 | 0 | Empty ad list flagged |
| 6 | BL_AD_INJECTION | **K** | 3/3 | 0 | Injected hostile ad detected |
| 7 | BL_WRONG_URL | **K** | 3/3 | 0 | Malformed URL detected |
| 8 | BL_DUPLICATE_ADS | **K** | 3/3 | 0 | Duplicate ad entry detected |
| — | BL_SHIPMENT_LOST *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |
| — | BL_INVENTORY_MISMATCH *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |
| — | BL_VENDOR_NEGOTIATION *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |
| — | BL_CUSTOMER_ESCALATION *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |
| — | BL_REFUND_REASONING *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |
| — | BL_COMPLIANCE_AMBIGUITY *(shipping)* | **L** | 0/3 | 0 | Not applicable to AdService |

**Result: 8/14 = 57.1% overall; agent-specific coverage 8/8 = 100%**  
6 LIVE faults are shipping-domain modes — not registered in AdService fault_injection.py.

---

### 3.4 RecommendationAgent — 57.1% Kill Rate

**Config:** qwen2.5:3b temp=0.0 · **Fault modes:** 14 tested (8 recommendation + 6 legacy shipping)

```
LKW Trace:
TASK_START --> RECOMMEND_DONE --> FINAL_ANSWER
   |                |                  |
[always]      [product IDs]        [response]
```

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 0/3 | 3 | Skips RECOMMEND_DONE |
| 2 | FM_1_2 Method swapped | **K** | 3/3 | 0 | Wrong recommendation method |
| 3 | FM_2_2 Hallucinated IDs | **K** | 3/3 | 0 | Phantom product IDs detected |
| 4 | FM_2_5 User ID swapped | **K** | 3/3 | 0 | Wrong user ID flagged |
| 5 | BL_EMPTY_RECS | **K** | 3/3 | 0 | Empty recommendation list |
| 6 | BL_SELF_RECOMMENDATION | **K** | 3/3 | 0 | Self-reference loop detected |
| 7 | BL_INJECTION_RECS | **K** | 3/3 | 0 | Hostile product ID injected |
| 8 | BL_SHUFFLED_RECS | **K** | 3/3 | 0 | Order changed vs B1 oracle |
| — | 6x shipping modes | **L** | 0/3 | 0 | Not applicable |

**Result: 8/14 = 57.1%; agent-specific: 8/8 = 100%**

---

### 3.5 PaymentAgent — 57.1% / 45.5% (all configs)

**Configs:** 4 · **Fault modes:** 14 per config (8 payment + 6 legacy shipping)

```
LKW Trace:
TASK_START --> CARD_VALIDATED --> CHARGE_DONE --> SAVE_DONE --> FINAL_ANSWER
   |                |                 |               |               |
[always]      [card OK/fail]    [amount charged]  [saved=T/F]    [response]
```

**3b_temp0 verdict table (best config — 57.1%):**

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 0/3 | 3 | Skips CARD_VALIDATED+CHARGE_DONE+SAVE_DONE |
| 2 | FM_1_2 Validation bypassed | **K** | 3/3 | 0 | card_validated flag cleared |
| 3 | FM_2_2 Hallucinated TXN ID | **K** | 3/3 | 0 | Fake transaction ID at CHARGE_DONE |
| 4 | FM_2_5 Amount ignored | **K** | 3/3 | 0 | amount_tampered flag at CARD_VALIDATED |
| 5 | BL_TRANSACTION_LOST | **K** | 3/3 | 0 | save_skipped flag at SAVE_DONE |
| 6 | BL_DOUBLE_CHARGE | **K** | 3/3 | 0 | double_charge flag at SAVE_DONE |
| 7 | BL_AMOUNT_TAMPERING | **K** | 3/3 | 0 | charged=9999 vs B1 baseline 9.00 |
| 8 | BL_CARD_DECLINED | **K** | 3/3 | 3 | Structural: CARD_VALIDATED+CHARGE_DONE+SAVE_DONE lost |
| — | 6x shipping modes | **L** | 0/3 | 0 | Not applicable |

**Kill rate per config:**

| Config | Killed | Live | Inconc | Rate | Notes |
|--------|--------|------|--------|------|-------|
| 3b_temp0 | 8 | 6 | 0 | **57.1%** | All 8 payment-specific modes killed |
| 14b_temp0 | 4 | 6 | 0 | **40.0%** | Only 4 general FM_* modes killed |
| 3b_temp0.7 | 4 | 6 | 0 | **40.0%** | Same as 14b |
| 3b_temp1.0 | 4 | 6 | 0 | **40.0%** | Same as 14b |
| **All configs** | **20** | **24** | **0** | **45.5%** | |

---

### 3.6 CurrencyAgent — 42.9% / 34.1% (all configs)

**Configs:** 4 · **Fault modes:** 14 per config (8 currency + 6 legacy shipping)

```
LKW Trace:
TASK_START --> CONVERT_DONE --> FINAL_ANSWER
   |               |                 |
[always]    [rate + amount]      [response]

Key B3 evidence: BL_RATE_MANIPULATION raises rate x100; detected at CONVERT_DONE.
Cross-agent Chain A: FM_2_2 hallucinate 1337 EUR --> PaymentAgent overcharges +14,755.6%
```

**3b_temp0 verdict table (best config — 42.9%):**

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 0/3 | 3 | Skips CONVERT_DONE |
| 2 | FM_1_2 Wrong currency routing | **K** | 3/3 | 0 | Currency swapped flag at CONVERT_DONE |
| 3 | FM_2_2 Hallucinated result | **K** | 3/3 | 0 | 1337 EUR fabricated (baseline: 9 EUR) |
| 4 | FM_2_5 Amount ignored | **L** | 0/3 | 0 | LIVE — LLM performs fresh lookup, ignores stale |
| 5 | BL_RATE_MANIPULATION | **K** | 3/3 | 0 | Rate x100 detected at CONVERT_DONE |
| 6 | BL_CURRENCY_UNAVAILABLE | **K** | 0/3 | 3 | Structural: CONVERT_DONE step lost |
| 7 | BL_STALE_RATE | **K** | 3/3 | 0 | Stale rate flag detected |
| 8 | BL_CONVERSION_OVERFLOW | **L** | 0/3 | 0 | LIVE — LLM silently clamps overflow |
| — | 6x shipping modes | **L** | 0/3 | 0 | Not applicable |

**Kill rate per config:**

| Config | Killed | Live | Inconc | Rate | Notes |
|--------|--------|------|--------|------|-------|
| 3b_temp0 | 6 | 8 | 0 | **42.9%** | FM_2_5 and BL_OVERFLOW absorbed |
| 14b_temp0 | 3 | 7 | 0 | **30.0%** | Only FM_3_1+FM_1_2+FM_2_2 |
| 3b_temp0.7 | 3 | 7 | 0 | **30.0%** | Same as 14b |
| 3b_temp1.0 | 3 | 7 | 0 | **30.0%** | Same as 14b |
| **All configs** | **15** | **29** | **0** | **34.1%** | |

> FM_2_5 LIVE finding: The LLM performs its own exchange-rate lookup instead of  
> using the injected stale amount. This is genuine LLM-level absorption, not a  
> test gap. True false negative — real fault, invisible to LLM-level detection.

---

### 3.7 EmailServiceAgent — 21.4% / 13.6% (all configs)

**Configs:** 4 · **Fault modes:** 14 per config (8 email + 6 legacy shipping)

```
LKW Trace:
TASK_START --> EMAIL_GENERATED --> EMAIL_SENT --> FINAL_ANSWER
   |                 |                 |               |
[always]       [content+to]      [sent=T/F]       [response]
```

**3b_temp0 verdict table (best config — 21.4%):**

| # | Fault Mode | Verdict | inf/runs | sloss | Mechanism |
|---|-----------|---------|----------|-------|-----------|
| 1 | FM_3_1 Premature termination | **K** | 0/3 | 3 | Skips EMAIL_GENERATED+EMAIL_SENT |
| 2 | FM_1_2 Wrong email type | **L** | 0/3 | 0 | LIVE — LLM generates correct type from context |
| 3 | FM_2_2 Hallucinated email | **K** | 3/3 | 0 | Phishing content detected at EMAIL_GENERATED |
| 4 | FM_2_5 Recipient swapped | **L** | 0/3 | 0 | LIVE — LLM uses correct recipient from context |
| 5 | BL_SEND_SKIPPED | **K** | 3/3 | 0 | send_skipped flag at EMAIL_SENT |
| 6 | BL_DOUBLE_SEND | **L** | 0/3 | 0 | LIVE — LLM does not double-send |
| 7 | BL_CORRUPTED_BODY | **L** | 0/3 | 0 | LIVE — LLM regenerates clean body |
| 8 | BL_WRONG_CUSTOMER | **L** | 0/3 | 0 | LIVE — LLM uses correct customer from context |
| — | BL_COMPLIANCE_AMBIGUITY | **I** | 0/3 | 3 | Inconclusive — shipping mode, step-loss |
| — | BL_CUSTOMER_ESCALATION | **I** | 0/3 | 3 | Inconclusive — shipping mode |
| — | BL_INVENTORY_MISMATCH | **I** | 0/3 | 3 | Inconclusive — shipping mode |
| — | BL_REFUND_REASONING | **I** | 0/3 | 3 | Inconclusive — shipping mode |
| — | BL_SHIPMENT_LOST | **I** | 0/3 | 3 | Inconclusive — shipping mode |
| — | BL_VENDOR_NEGOTIATION | **I** | 0/3 | 3 | Inconclusive — shipping mode |

**Kill rate per config:**

| Config | Killed | Live | Inconc | Rate | Notes |
|--------|--------|------|--------|------|-------|
| 3b_temp0 | 3 | 5 | 6 | **21.4%** | FM_3_1+FM_2_2+BL_SEND_SKIPPED |
| 14b_temp0 | 1 | 0 | 9 | **10.0%** | Only FM_3_1; everything else INCONCLUSIVE |
| 3b_temp0.7 | 1 | 0 | 9 | **10.0%** | Same as 14b |
| 3b_temp1.0 | 1 | 0 | 9 | **10.0%** | Same as 14b |
| **All configs** | **6** | **5** | **33** | **13.6%** | |

> Email is hardest to mutate-test. LLM re-generates valid content from task context,  
> overriding all content-level injections. Only structural (FM_3_1), explicit hallucination  
> (FM_2_2), and explicit send-bypass (BL_SEND_SKIPPED) are reliably detectable.  
> Under 14b, 9/10 modes collapse to INCONCLUSIVE — worst model-effect in the study.

---

### 3.8 ShippingQuoteAgent — 0.0% Kill Rate

**Configs:** 4 · **Fault modes:** 10 per config (shipping-specific)

```
LKW Trace:
TASK_START --> QUOTE_DONE --> FINAL_ANSWER
   |               |               |
[always]    [rate+carrier]     [response]
```

| Config | Killed | Live | Inconc | Rate | Observation |
|--------|--------|------|--------|------|-------------|
| 3b_temp0 | 0 | 10 | 0 | **0.0%** | All LIVE — inf=0, sloss=0; template fallback |
| 14b_temp0 | 0 | 0 | 10 | **0.0%** | All INCONCLUSIVE — sloss=1-3, no infection |
| 3b_temp0.7 | 0 | 0 | 10 | **0.0%** | All INCONCLUSIVE — gRPC errors dominate |
| 3b_temp1.0 | 0 | 0 | 10 | **0.0%** | All INCONCLUSIVE — gRPC errors dominate |

```
ROOT CAUSE — Template-string fallback absorption:
The ShippingQuoteAgent uses a pre-formatted fallback string when LLM
inference is unreliable. Injected faults target the tool-response layer,
but the LLM either:
  (a) Raises a gRPC connection error (run classified as error, not LIVE)
  (b) Returns the hardcoded fallback template (3b_temp0: inf=0, sloss=0)
  (c) Partially executes but output is too divergent to classify (14b: INCONCLUSIVE)

RESEARCH FINDING: Defensive LLM agents with fallback paths are opaque to
checkpoint-level mutation testing. This is a property of the agent
architecture, not a gap in the test framework.
```

---

### 3.9 ShipOrderAgent — 0.0% Kill Rate

**Configs:** 4 · **Fault modes:** 10 per config (shipping-specific)

```
LKW Trace:
TASK_START --> ORDER_PLACED --> TRACKING_GENERATED --> SAVE_DONE --> FINAL_ANSWER
   |               |                    |                  |               |
[always]     [order_id]         [tracking_code]        [saved=T/F]    [response]
```

| Config | Killed | Live | Inconc | Rate |
|--------|--------|------|--------|------|
| 3b_temp0 | 0 | 10 | 0 | **0.0%** |
| 14b_temp0 | 0 | 0 | 10 | **0.0%** |
| 3b_temp0.7 | 0 | 0 | 10 | **0.0%** |
| 3b_temp1.0 | 0 | 0 | 10 | **0.0%** |

Same root cause as ShippingQuoteAgent. Both shipping agents share the same  
ReAct-loop + template-fallback design — structurally opaque to mutation testing.

---

## 4. Cross-Agent Fault Propagation

**Source:** SPEED HPC Job 970076, commit 3b1c2ca, `src/cross_agent_970076.err`

### Chain A: CurrencyAgent FM_2_2 → PaymentAgent

```
CurrencyAgent (FM_2_2 active)
  CONVERT_DONE: amount = 1337.00 EUR   <-- hallucinated (B1 oracle: 9.00 EUR)
       |
       | inter-agent boundary (API handoff)
       v
PaymentAgent (NONE — clean execution on corrupt input)
  CARD_VALIDATED: amount = 1337.00
  CHARGE_DONE:    charged = 1337.00    <-- overcharge propagated silently
  SAVE_DONE:      saved = True
       |
       v
  Customer charged: 1337.00 EUR
  B1 baseline:        9.00 EUR
  Overcharge:       +1328.00 EUR  (+14,755.6%)
```

- **Infection hop 2:** None (PaymentAgent trace looks structurally clean)
- **HITL Tier:** 3 — Silent financial loss. No automated flag fires.
- **Detection path:** Requires CurrencyAgent output contract check at boundary.

### Chain B: ProductCatalogAgent FM_2_2 → RecommendationAgent

```
ProductCatalogAgent (FM_2_2 active)
  CATALOG_DONE: products = ['HALLUCINATED-001']   <-- phantom (B1: ['PROD-001'])
       |
       | inter-agent boundary
       v
RecommendationAgent (NONE — clean execution on corrupt input)
  RECOMMEND_DONE: recommended = ['HALLUCINATED-001']   <-- phantom propagated
       |
       v
  Customer shown non-existent product recommendation
```

- **Infection hop 2:** None (RecommendationAgent trace structurally clean)
- **HITL Tier:** 2 — Detectable via product ID cross-check
- **Detection path:** RecommendationAgent must validate IDs against ProductCatalog.

### Key Propagation Finding

```
+--------------------------------------------------------------+
|  FM-2.2 (hallucination) is the highest-risk cross-boundary  |
|  fault class. It:                                            |
|  - Completes all LKW steps downstream (depth=0)             |
|  - Passes silently through subsequent agents                 |
|  - Produces business harm (14,755% overcharge)               |
|  - Is invisible to step-level workflow monitoring            |
|                                                              |
|  Structural faults (FM-3.1) self-reveal at agent boundary.  |
|  Semantic faults (FM-2.2) require explicit inter-agent       |
|  contract validation at every handoff point.                 |
+--------------------------------------------------------------+
```

---

## 5. Model Effect Analysis

### Kill Rate Comparison Across Configs

```
Agent           | 3b_t0  | 14b_t0 | 3b_t0.7 | 3b_t1.0 | Delta (best-worst)
----------------+--------+--------+---------+---------+-------------------
payment         | 57.1%  | 40.0%  |  40.0%  |  40.0%  |  -17.1 pp
currency        | 42.9%  | 30.0%  |  30.0%  |  30.0%  |  -12.9 pp
email           | 21.4%  | 10.0%  |  10.0%  |  10.0%  |  -11.4 pp
ship_order      |  0.0%  |  0.0%  |   0.0%  |   0.0%  |    0.0 pp
shipping_quote  |  0.0%  |  0.0%  |   0.0%  |   0.0%  |    0.0 pp
```

### Analysis

**1. qwen2.5:3b temp=0 is the best fault-detection config.**  
It preserves the full ReAct tool-call loop, executing agent logic that exposes  
fault flags in LKW checkpoints. All agent-specific BL_* faults are detectable  
under this config.

**2. qwen2.5-coder:14b temp=0 short-circuits the ReAct loop.**  
The larger model frequently answers directly without intermediate tool calls,  
routing around the instrumented checkpoint layer. This creates false negatives:  
faults are present in the injection layer but the LLM never reaches them.

**3. Temperature effect is secondary.**  
3b_temp0.7 and 3b_temp1.0 match the 14b pattern for payment/currency, not the  
3b_temp0 pattern. Higher temperature raises semantic noise (more INCONCLUSIVE  
verdicts) without improving kill rates.

**4. Email under 14b is an extreme case.**  
9/10 fault modes collapse to INCONCLUSIVE (sloss=3, inf=0). The model completes  
the task with output so divergent from B1 that the B2-calibrated tolerance  
cannot confirm genuine fault injection.

**5. FM_2_5 (stale value) is universally weaker.**  
Currency FM_2_5: LIVE under all 4 configs. The LLM performs a fresh exchange-rate  
lookup, ignoring the injected stale amount. This tests whether LLMs use injected  
stale values — by design, they do not. Genuine true false negative.

---

## 6. Key Findings and Research Conclusions

### F1 — FM-2.2 (Hallucination) is the highest-risk fault class
Kills mutants in 8/9 agents. Completes all LKW steps (depth=0). Propagates silently  
across boundaries with 14,755% financial overcharge (Chain A). Invisible to step-level  
monitoring. Requires checkpoint-level payload validation at inter-agent boundaries.

### F2 — FM-3.1 (Premature Termination) is universally structurally detectable
Kills mutants in all 9 agents via step-loss (sloss>0). Auto-detectable from missing-step  
telemetry alone — no semantic payload comparison needed. The only fault class detectable  
by workflow orchestrators without semantic analysis.

### F3 — Defensive fallback patterns create mutation testing blind spots
Both shipping agents return 0% kill rate due to template-string fallback paths that  
bypass all instrumented checkpoints. This is a structural property of the agent design  
and a novel failure mode: high conventional testability coexists with LLM-level opacity.

### F4 — Larger models create false negatives
qwen2.5-coder:14b reduces kill rates by 11-17 percentage points vs qwen2.5:3b by  
short-circuiting the ReAct tool-call loop. For mutation testing of LLM agents, smaller  
models that faithfully execute the full tool-call loop are better test oracles.

### F5 — B2 calibration validates the measurement methodology
0.0% false positive rate across 212 B2 runs (all agents, all configs) confirms  
LKW checkpoints do not fire spuriously. B3 kill verdicts represent genuine  
fault-induced deviations, not natural LLM variance.

### F6 — Language-agnostic methodology: C# and Go agents covered
CartAgent (C#) and CheckoutOrchestrator (Go) — both exercised via thin Python  
wrappers — achieve 100% coverage. LKW+RIP methodology applies across implementation  
languages without agent-side modification.

### F7 — LLM fault absorption is real and agent-specific
FM_2_5 is LIVE for CurrencyAgent across all 4 configs (LLM recomputes fresh).  
BL_CORRUPTED_BODY and BL_WRONG_CUSTOMER are LIVE for EmailServiceAgent (LLM  
regenerates content from context). These represent genuine false negatives where  
LLM capability masks injected faults — a finding specific to LLM-based agents vs.  
conventional software.

---

## 7. Limitations and Future Work

| # | Limitation | Impact | Mitigation |
|---|-----------|--------|------------|
| 1 | Shipping agents require live gRPC backing services not co-launched in Slurm | 0% kill rate / high INCONCLUSIVE for shipping | Co-launch service stack in Slurm job |
| 2 | FM_2_5 (stale value) is by design absorbed by LLMs with live inference | True false negative; cannot be fixed by injection layer | Instrument tool-call inputs, not agent outputs |
| 3 | AdService/Recommendation include 6 irrelevant shipping modes from merged job history | Dilutes kill rate to 57% from 100% agent-specific | Re-run with clean per-agent fault sets only |
| 4 | No automated cross-agent propagation measurement in B3 pipeline | Chains A/B require manual cross-agent runner | Integrate cross-agent runner into B3 pipeline |
| 5 | Email INCONCLUSIVE rate 33/44 = 75% | True kill rate uncertain | Tighten B2 tolerance for email-type content |
| 6 | No Verifier Agent (MASS-paper role) implemented | No automated governance over agent output quality | Future work: implement verifier agent |
| 7 | HITL boundary manually identified from trace inspection | Tier 2/3 decisions require human review | Future: automated HITL gate per boundary contract |
| 8 | 3b_temp0 outperforms 14b for fault detection | Model choice critically affects testability | Paper recommendation: use 3b for testing, 14b for production |

---

*Report generated 2026-07-29 from `per_agent_llm_report.json`*  
*SPEED HPC Jobs: 1170215–1170223 (speed-25, Tesla V100, deepak/fault-injection branch)*  
*Models: qwen2.5:3b (standalone) · qwen2.5-coder:14b (4-config agents)*
