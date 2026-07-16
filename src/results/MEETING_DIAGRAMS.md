# LKW Instrumentation Journey — Meeting Diagrams
**Project:** Failure Injection and Propagation Analysis in Agentic Microservice Workflows  
**Author:** Deepak Sunil Chavan, Concordia University  
**Date:** 2026-07-16  
**Purpose:** Visual companion to the professor meeting script — covers everything from LKW flag design to policy-based recovery.  
**Companion file:** `PROFESSOR_MEETING_SCRIPT.md` (script), `diagrams_report.md` (architecture + cross-agent chains)

> All Mermaid diagrams render natively on GitHub. Numeric values sourced directly from JSON result files in this folder.

---

## Table of Contents

| # | Diagram | Covers Script Chapter |
|---|---|---|
| 1 | [LKW Checkpoint Flag Schema](#1-lkw-checkpoint-flag-schema) | Chapter 1 — What a flag is |
| 2 | [Flag Placement in the LangGraph Node Pipeline](#2-flag-placement-in-the-langgraph-node-pipeline) | Chapter 2 — How flags were inserted |
| 3 | [All 7 Agents — Checkpoint Chains](#3-all-7-agents--checkpoint-chains) | Chapter 2 — Per-agent chains |
| 4 | [RIP Computation Pipeline](#4-rip-computation-pipeline) | Chapter 3 — What RIP computes |
| 5 | [Stability Matrix — 64 Mode-Runs, 0 UNSTABLE](#5-stability-matrix--64-mode-runs-0-unstable) | Chapter 4 — Reproducibility proof |
| 6 | [HITL Tier: What the Flags Tell You](#6-hitl-tier-what-the-flags-tell-you) | Chapter 5 — HITL classification |
| 7 | [BOUNDARY_CHECK — New Checkpoint Type](#7-boundarycheck--new-checkpoint-type) | Chapter 7 — Boundary extension |
| 8 | [RECOVERY_ACTION — Full LKW Trace With Recovery](#8-recovery_action--full-lkw-trace-with-recovery) | Chapter 8 — Recovery in the trace |
| 9 | [End-to-End Journey: LKW Flag → Recovery](#9-end-to-end-journey-lkw-flag--recovery) | All chapters — summary |

---

## 1. LKW Checkpoint Flag Schema

**What a single LKW checkpoint record looks like — the unit of measurement.**

```mermaid
block-beta
  columns 1
  block:schema["Single LKW Checkpoint Record (JSON)"]
    A["step: &quot;ADS_FETCHED&quot;
    ─────────────────────────────────
    Named milestone — one of EXPECTED_STEPS
    Compared against baseline to detect missing steps"]
    B["timestamp: &quot;2026-06-18T12:09:21Z&quot;
    ─────────────────────────────────
    ISO-8601 UTC — used for elapsed_ms computation"]
    C["fault_mode: &quot;FM_2_2&quot;
    ─────────────────────────────────
    Stamped on every record — ties trace to experiment
    Eliminates ambiguity between runs"]
    D["data: { count: 3, hallucinated: true,
              injected: false, empty_ads: false }
    ─────────────────────────────────
    Boolean fault flags + domain values
    Compared vs baseline to find infection point
    hallucinated=true here → Tier 3 Silent fault"]
  end
```

**Three purposes of the boolean flags in `data`:**

```mermaid
flowchart LR
  F["Boolean flag<br/>e.g. hallucinated: true"]
  F --> P1["1. Infection detection<br/>Flag appeared vs baseline?<br/>→ this is the infection point"]
  F --> P2["2. HITL tier assignment<br/>Is it a structural flag? operational flag?<br/>→ determines Tier 1 / 2 / 3"]
  F --> P3["3. Recovery trigger<br/>Does the recovery policy match this flag?<br/>→ determines action to take"]
```

---

## 2. Flag Placement in the LangGraph Node Pipeline

**How `record_checkpoint()` calls are positioned relative to fault functions in the actual graph nodes.**  
Example: AdServiceAgent (`src/adserviceagent/app/graph.py`)

```mermaid
flowchart TD
  subgraph input_node["input_node — LangGraph Node 1"]
    direction TB
    I1["fi.clear_lkw()
    ─── reset before each run ───"]
    I2["fi.record_checkpoint('TASK_START', {
      instruction, fault_mode
    })
    ─── P1: Task entry checkpoint ───"]
    I3["[fault functions run]
    fi.maybe_tamper_context()
    fi.maybe_swap_category()
    ─── faults mutate state ───"]
    I4["fi.record_checkpoint('CONTEXT_EXTRACTED', {
      context_keys,
      context_tampered: bool,
      category_swapped: bool
    })
    ─── P2: after domain decision ───"]
    I5{"FM_3_1 guard?"}
    I6["fi.record_checkpoint('FINAL_ANSWER', {
      premature_termination: true
    })  → EXIT early"]
    I1 --> I2 --> I3 --> I4 --> I5
    I5 -->|"premature=True"| I6
    I5 -->|"normal"| NEXT1["→ ad_lookup_node"]
  end

  subgraph ad_lookup_node["ad_lookup_node — LangGraph Node 2"]
    direction TB
    A1["[fault functions run]
    fi.maybe_hallucinate_ads()
    fi.maybe_empty_ads()
    fi.maybe_inject_ads()
    fi.maybe_wrong_url()
    fi.maybe_duplicate_ad()
    ─── faults mutate ads list ───"]
    A2{"boundary contract
    present?"}
    A3["boundary_contract(name, expected, observed)
    fi.record_checkpoint('BOUNDARY_CHECK', {
      alert: bool,
      expected, observed,
      violations
    })
    ─── P4: agent boundary checkpoint ───"]
    A4{"recovery action
    = fallback_lkg?"}
    A5["fi.record_checkpoint('RECOVERY_ACTION', recovery)
    ─── P5: failure intercept checkpoint ───"]
    A6["fi.record_checkpoint('ADS_FETCHED', {
      count, hallucinated, empty_ads,
      injected, wrong_url, duplicated
    })
    ─── P2: after domain commit ───"]
    A1 --> A2
    A2 -->|"yes"| A3 --> A4
    A2 -->|"no"| A6
    A4 -->|"yes"| A5 --> A6
    A4 -->|"no"| A6
  end

  subgraph output_node["output_node — LangGraph Node 3"]
    direction TB
    O1["fi.record_checkpoint('FINAL_ANSWER', {
      ads_count: int
    })
    ─── P3: output commit checkpoint ───"]
  end

  NEXT1 --> A1
  A6 --> O1

  style I2 fill:#c8e6c9,stroke:#388e3c
  style I4 fill:#c8e6c9,stroke:#388e3c
  style I6 fill:#ffcdd2,stroke:#d32f2f
  style A3 fill:#e3f2fd,stroke:#1976d2
  style A5 fill:#fff9c4,stroke:#f9a825
  style A6 fill:#c8e6c9,stroke:#388e3c
  style O1 fill:#c8e6c9,stroke:#388e3c
```

**Checkpoint placement classes (P1–P5):**

| Class | Name | When it fires | Example |
|---|---|---|---|
| P1 | Task Entry | First line of `input_node`, after `clear_lkw()` | `TASK_START` |
| P2 | Domain Decision | After fault functions, before returning state | `CONTEXT_EXTRACTED`, `ADS_FETCHED`, `CONVERT_DONE` |
| P3 | Output Commit | Final node, after building response | `FINAL_ANSWER` |
| P4 | Agent Boundary | When a handoff contract is evaluated | `BOUNDARY_CHECK` |
| P5 | Failure Intercept | When a recovery policy fires | `RECOVERY_ACTION` |

---

## 3. All 7 Agents — Checkpoint Chains

**The full `EXPECTED_STEPS` list for each agent — the baseline a healthy run must match.**

```mermaid
flowchart TD
  subgraph PA["PaymentAgent — 5 checkpoints"]
    direction LR
    p1[TASK_START] --> p2[CARD_VALIDATED] --> p3[CHARGE_DONE] --> p4[SAVE_DONE] --> p5[FINAL_ANSWER]
  end
  subgraph CA["CurrencyAgent — 3 checkpoints"]
    direction LR
    c1[TASK_START] --> c2[CONVERT_DONE] --> c3[FINAL_ANSWER]
  end
  subgraph EA["EmailServiceAgent — 4 checkpoints"]
    direction LR
    e1[TASK_START] --> e2[EMAIL_GENERATED] --> e3[EMAIL_SENT] --> e4[FINAL_ANSWER]
  end
  subgraph PC["ProductCatalogAgent — 3 checkpoints"]
    direction LR
    pc1[TASK_START] --> pc2[CATALOG_DONE] --> pc3[FINAL_ANSWER]
  end
  subgraph RA["RecommendationAgent — 3 checkpoints"]
    direction LR
    r1[TASK_START] --> r2[RECOMMEND_DONE] --> r3[FINAL_ANSWER]
  end
  subgraph AA["AdServiceAgent — 4 checkpoints"]
    direction LR
    a1[TASK_START] --> a2[CONTEXT_EXTRACTED] --> a3[ADS_FETCHED] --> a4[FINAL_ANSWER]
  end
  subgraph SS["ShippingService — 7 checkpoints (real LLM)"]
    direction LR
    s1[TASK_START] --> s2[QUOTE_DONE] --> s3[CARRIER_DONE] --> s4[TRACKING_DONE] --> s5[ESCALATION_CHECK] --> s6[FINAL_ANSWER] --> s7[SAVE_DONE]
  end

  style s1 fill:#fbe9e7,stroke:#e64a19
  style s2 fill:#fbe9e7,stroke:#e64a19
  style s3 fill:#fbe9e7,stroke:#e64a19
  style s4 fill:#fbe9e7,stroke:#e64a19
  style s5 fill:#fbe9e7,stroke:#e64a19
  style s6 fill:#fbe9e7,stroke:#e64a19
  style s7 fill:#fbe9e7,stroke:#e64a19
```

> ShippingService (orange) is the only agent that runs a live LLM — qwen2.5-coder:14b on SPEED HPC A100.  
> All other agents are deterministic Python (no LLM in loop) — used to validate the instrumentation framework.

---

## 4. RIP Computation Pipeline

**How the three RIP metrics are derived from the raw LKW trace list.**

```mermaid
flowchart TD
  INPUT["Raw LKW trace list — ordered checkpoint records
  e.g. FM_2_2 on PaymentAgent:
  [{step:TASK_START}, {step:CARD_VALIDATED},
   {step:CHARGE_DONE, data:{hallucinated:true}},
   {step:SAVE_DONE}, {step:FINAL_ANSWER}]"]

  INPUT --> R
  INPUT --> I
  INPUT --> P

  subgraph R["R — Reachability"]
    R1["steps_reached = [cp.step for cp in lkw]
    → ['TASK_START','CARD_VALIDATED',
       'CHARGE_DONE','SAVE_DONE','FINAL_ANSWER']
    Compare against EXPECTED_STEPS"]
    R2["steps_lost = set(EXPECTED_STEPS) − set(steps_reached)
    FM_2_2 result: steps_lost = []
    All 5 steps present → Reachability = 5/5"]
  end

  subgraph I["I — Infection Point"]
    I1["For each checkpoint in order,
    compare data flags vs NONE baseline:
    • NONE baseline has hallucinated=False
    • FM_2_2 has hallucinated=True at CHARGE_DONE
    → infection_point = 'CHARGE_DONE'"]
    I2["If no flag differs from baseline
    → infection_point = None  (Tier 3 case:
       value is wrong but no boolean flag raised)"]
  end

  subgraph P["P — Propagation Depth"]
    P1["depth = len(steps_lost)
    FM_2_2: depth = 0 (all steps present)
    FM_3_1 PaymentAgent: depth = 3
    (CARD_VALIDATED, CHARGE_DONE, SAVE_DONE lost)"]
    P2["depth > 0 → auto-detectable → TIER 1
    depth = 0 with flags → TIER 2
    depth = 0, no flags → TIER 3"]
  end

  R2 & I1 & P1 --> OUT["Result JSON per fault-mode run:
  {
    infection_point: 'CHARGE_DONE',
    propagation_depth: 0,
    steps_lost: [],
    steps_reached: ['TASK_START',...,'FINAL_ANSWER'],
    flags_found: ['hallucinated']
  }"]

  style I2 fill:#ffcdd2,stroke:#c62828
  style P2 fill:#fff9c4,stroke:#f9a825
```

---

## 5. Stability Matrix — 64 Mode-Runs, 0 UNSTABLE

**Each cell = one fault mode run three independent times. Color = stability label.**  
**Data source:** `results/stability_summary.json` — SPEED HPC job 970059

```mermaid
quadrantChart
  title Stability Results — All 64 Mode-Runs (6 mock agents + ShippingService)
  x-axis "Fault Modes (NONE + 8 BL/FM per agent)" --> "Mode 9"
  y-axis "STABLE_PASS / STABLE_FAULT" --> "UNSTABLE"
  quadrant-1 "Unstable zone (none observed)"
  quadrant-2 "Unstable zone (none observed)"
  quadrant-3 "STABLE_FAULT zone: 57 mode-runs"
  quadrant-4 "STABLE_PASS zone: 7 baselines"
  PaymentAgent_NONE: [0.05, 0.05]
  PaymentAgent_FM31: [0.15, 0.05]
  PaymentAgent_FM22: [0.25, 0.05]
  CurrencyAgent_NONE: [0.05, 0.20]
  CurrencyAgent_FM31: [0.15, 0.20]
  EmailAgent_NONE: [0.05, 0.35]
  ProductCatalog_NONE: [0.05, 0.50]
  RecAgent_NONE: [0.05, 0.65]
  AdAgent_NONE: [0.05, 0.80]
  ShippingService_NONE: [0.05, 0.95]
```

**Stability table (exact numbers):**

```mermaid
xychart-beta
  title "Stability Rate per Agent — 100% across all 7"
  x-axis ["PaymentAgent", "CurrencyAgent", "EmailService", "ProductCatalog", "Recommendation", "AdService", "ShippingService"]
  y-axis "Stability Rate (%)" 0 --> 100
  bar [100, 100, 100, 100, 100, 100, 100]
```

| Agent | Modes run | STABLE_PASS | STABLE_FAULT | UNSTABLE | Rate |
|---|---|---|---|---|---|
| PaymentAgent | 9 | 1 | 8 | 0 | **100%** |
| CurrencyAgent | 9 | 1 | 8 | 0 | **100%** |
| EmailServiceAgent | 9 | 1 | 8 | 0 | **100%** |
| ProductCatalogAgent | 9 | 1 | 8 | 0 | **100%** |
| RecommendationAgent | 9 | 1 | 8 | 0 | **100%** |
| AdServiceAgent | 9 | 1 | 8 | 0 | **100%** |
| ShippingService | 10 | 1 | 9 | 0 | **100%** |
| **TOTAL** | **64** | **7** | **57** | **0** | **100%** |

> Each mode-run = 3 independent executions. `STABLE_FAULT` means the LKW fingerprint was **identical** across all 3 runs — same infection_point, same steps_lost, same depth. Zero non-determinism observed.

---

## 6. HITL Tier: What the Flags Tell You

**How the boolean flags in the LKW data map to the three HITL tiers — and what action each tier requires.**

```mermaid
flowchart TD
  LKW_TRACE["LKW trace from one fault-mode run
  (58 fault modes classified total)"]

  LKW_TRACE --> Q1{"propagation_depth > 0?
  i.e. some EXPECTED_STEPS missing?"}

  Q1 -->|YES| T1["TIER 1 — Structural  (11 faults)
  ──────────────────────────────────
  Auto-detectable by step count alone
  No semantic analysis needed
  ──────────────────────────────────
  Examples:
  FM_3_1 PaymentAgent: CARD_VALIDATED,
    CHARGE_DONE, SAVE_DONE missing (depth=3)
  BL_CARD_DECLINED: depth=3
  FM_3_1 ShippingService: CARRIER_DONE missing
  ──────────────────────────────────
  ACTION: Alert immediately → assess severity"]

  Q1 -->|NO - all steps reached| Q2

  Q2{"New operational flags in data?
  e.g. amount_tampered: true
       double_charge: true
       send_skipped: true"}

  Q2 -->|YES| T2["TIER 2 — Flag-Detectable  (40 faults)
  ──────────────────────────────────
  Requires flag monitor / alert rule
  All steps in trace — no structural signal
  ──────────────────────────────────
  Examples:
  FM_2_5 PaymentAgent: amount_tampered=True
  BL_DOUBLE_SEND EmailAgent: double_send=True
  BL_AD_INJECTION: injected=True
  ──────────────────────────────────
  ACTION: Flag monitor triggers → HITL review"]

  Q2 -->|NO - no operational flags| Q3

  Q3{"Only hallucinated=True
  or pure value corruption?"}

  Q3 -->|YES| T3["TIER 3 — Silent  (7 faults)
  ──────────────────────────────────
  No structural signal, no boolean flag
  Requires semantic validation of values
  ──────────────────────────────────
  Examples:
  FM_2_2 on any agent: all steps clean,
    data values semantically wrong
  CurrencyAgent FM_2_2: returns 1337 EUR
    instead of 9 EUR — zero trace signal
  ──────────────────────────────────
  ACTION: Boundary contract or manual audit"]

  Q3 -->|NO| T0["TIER 0 — Baseline
  Healthy run, no fault"]

  style T1 fill:#ffcdd2,stroke:#c62828
  style T2 fill:#ffe0b2,stroke:#e65100
  style T3 fill:#ef9a9a,stroke:#b71c1c
  style T0 fill:#c8e6c9,stroke:#2e7d32
```

---

## 7. BOUNDARY_CHECK — New Checkpoint Type

**How `BOUNDARY_CHECK` was added to the LKW trace to catch silent Tier-3 hallucinations at agent handoffs.**

```mermaid
sequenceDiagram
  participant CA as CurrencyAgent
  participant LKW as LKW Logger
  participant BC as boundary_contract()
  participant PA as PaymentAgent

  Note over CA,PA: BEFORE boundary contracts (original design)
  CA->>LKW: TASK_START
  CA->>LKW: CONVERT_DONE {hallucinated:true, units:1337}
  CA->>LKW: FINAL_ANSWER
  CA->>PA: passes 1337 EUR silently
  Note over PA: PaymentAgent has no way to know
  PA->>LKW: all 5 steps clean — no alert

  Note over CA,PA: AFTER boundary contracts (new design)
  CA->>LKW: TASK_START
  CA->>LKW: CONVERT_DONE {hallucinated:true, units:1337}
  CA->>BC: boundary_contract("currency_to_payment",<br/>expected=9, observed=1337)
  BC-->>CA: {alert:True, delta:1328, status:"signal_escape"}
  CA->>LKW: BOUNDARY_CHECK {alert:True, expected:9,<br/>observed:1337, violations:[{type:delta,value:1328}]}
  CA->>LKW: RECOVERY_ACTION {action:block_and_request_hitl,<br/>charge_blocked:True}
  CA->>LKW: FINAL_ANSWER
  Note over PA: PaymentAgent never receives the 1337 EUR
  Note over LKW: Hallucination is now machine-detectable<br/>in the LKW trace at the boundary
```

**Five boundary contracts implemented — from `boundary_detection_summary.json`:**

```mermaid
flowchart LR
  CA["CurrencyAgent"] -->|"currency_to_payment
  checks: delta ≤ threshold
  alert if: observed >> expected"| PA["PaymentAgent"]

  PC["ProductCatalogAgent"] -->|"catalog_to_recommendation
  checks: all IDs exist in catalog
  alert if: phantom ID returned"| RA["RecommendationAgent"]

  SS["ShippingService"] -->|"carrier_to_tracking
  checks: carrier name is real carrier
  alert if: hallucinated carrier"| TR["TrackingLayer"]

  SS -->|"quote_to_carrier_selection
  checks: selected carrier matches quote
  alert if: ignored_downstream_quote"| CS["CarrierSelection"]

  ES["EmailServiceAgent"] -->|"email_body_to_send
  checks: body is non-truncated
  alert if: body truncated (BL_CORRUPT_BODY)"| SMTP["Email Send"]

  style CA fill:#e8f5e9,stroke:#388e3c
  style PC fill:#e8f5e9,stroke:#388e3c
  style SS fill:#fbe9e7,stroke:#e64a19
  style ES fill:#e8f5e9,stroke:#388e3c
```

> Live evidence: `results/boundary_events.jsonl` — 13 events, 8 alerts, 5 clean.  
> Live dashboard: `src/boundary_dashboard.py` at `http://127.0.0.1:8765`

---

## 8. RECOVERY_ACTION — Full LKW Trace With Recovery

**The complete LKW trace evolution: from baseline → fault (no detection) → with boundary → with recovery.**

```mermaid
flowchart TD
  subgraph BASE["BASELINE — CurrencyAgent NONE"]
    direction LR
    b1[TASK_START] --> b2["CONVERT_DONE
    units: 9 EUR
    hallucinated: false"] --> b3[FINAL_ANSWER]
  end

  subgraph FAULT["FM_2_2 — no boundary contract"]
    direction LR
    f1[TASK_START] --> f2["CONVERT_DONE ← infection point
    units: 1337 EUR
    hallucinated: true"] --> f3["FINAL_ANSWER
    returns 1337 EUR
    depth=0, no alert"]
  end

  subgraph BCHECK["FM_2_2 — with BOUNDARY_CHECK added"]
    direction LR
    bc1[TASK_START] --> bc2["CONVERT_DONE
    hallucinated: true"] --> bc3["BOUNDARY_CHECK ← new step
    alert: true
    expected: 9, observed: 1337
    delta: 1328"] --> bc4[FINAL_ANSWER]
  end

  subgraph RECOVERY["FM_2_2 — with BOUNDARY_CHECK + RECOVERY_ACTION"]
    direction LR
    r1[TASK_START] --> r2["CONVERT_DONE
    hallucinated: true"] --> r3["BOUNDARY_CHECK
    alert: true, delta: 1328"] --> r4["RECOVERY_ACTION ← new step
    action: block_and_request_hitl
    charge_blocked: True
    prevented_loss_eur: 1328
    requires_hitl: true"] --> r5[FINAL_ANSWER]
  end

  BASE --> FAULT
  FAULT -->|"add boundary contract"| BCHECK
  BCHECK -->|"add recovery policy"| RECOVERY

  style f2 fill:#ffcdd2,stroke:#c62828
  style f3 fill:#ffcdd2,stroke:#c62828
  style bc3 fill:#e3f2fd,stroke:#1976d2
  style r3 fill:#e3f2fd,stroke:#1976d2
  style r4 fill:#c8e6c9,stroke:#388e3c
```

**Recovery wiring test results — from `test_recovery_wiring.py`:**

| Agent | Scenario | LKW Check | Result |
|---|---|---|---|
| CurrencyAgent | FM_2_2 → delta=1328 | `RECOVERY_ACTION` in lkw_steps | ✅ PASS |
| CurrencyAgent | FM_2_2 → delta=1328 | `FINAL_ANSWER` in lkw_steps | ✅ PASS |
| CurrencyAgent | FM_2_2 → delta=1328 | `CONVERT_DONE` NOT in lkw_steps (blocked) | ✅ PASS |
| ProductCatalogAgent | phantom ID | `RECOVERY_ACTION` in lkw_steps | ✅ PASS |
| ProductCatalogAgent | phantom ID | recovered_ids == ['PROD-001'] | ✅ PASS |
| AdServiceAgent | BL_AD_INJECTION | `RECOVERY_ACTION` in lkw_steps | ✅ PASS |
| EmailServiceAgent | BL_CORRUPT_BODY | boundary_blocked=True | ✅ PASS |
| **Total** | | | **19/19 PASS** |

---

## 9. End-to-End Journey: LKW Flag → Recovery

**The complete timeline of what was built, in order, from first checkpoint to final recovery test.**

```mermaid
timeline
  title LKW Instrumentation Journey — July 2026
  section Phase 1 — Flag Design
    Design LKW schema : record_checkpoint() in fault_injection.py
                      : EXPECTED_STEPS per agent
                      : clear_lkw() / get_lkw() helpers
  section Phase 2 — Code Insertion
    Insert flags in graph.py : TASK_START at input_node entry
                             : Domain steps at node boundaries
                             : FINAL_ANSWER at output_node
                             : FM_3_1 guard path emits early FINAL_ANSWER
  section Phase 3 — RIP Analysis
    Build RIP layer : Reachability — steps_reached vs EXPECTED_STEPS
                    : Infection — first step with new boolean flag
                    : Propagation — depth = len(steps_lost)
                    : Output fault_results.json per agent
  section Phase 4 — Reproducibility
    Run on SPEED HPC : 3 runs × 9 modes × 6 agents = 162 individual runs
                     : ShippingService 10 modes × 3 = 30 runs
                     : 0 UNSTABLE — 100% stability
                     : stability_summary.json as evidence
  section Phase 5 — HITL Classification
    Automate hitl_detector.py : Tier 1 — depth > 0 → auto-detectable
                               : Tier 2 — operational flags → flag monitor
                               : Tier 3 — hallucinated only → semantic review
                               : 11 + 40 + 7 = 58 fault modes classified
  section Phase 6 — Cross-Agent
    Cross-agent propagation : FM_2_2 upstream → NONE downstream
                            : Chain A: 14,755% overcharge, zero alert
                            : Chain B: phantom product, zero alert
                            : Proves single-agent LKW not sufficient
  section Phase 7 — Boundary Contracts
    Add BOUNDARY_CHECK : New P4 checkpoint at agent handoffs
                       : boundary_contract() validates expected vs observed
                       : 5 contracts: currency, catalog, carrier, quote, email
                       : 13 events, 8 alerts confirmed in boundary_events.jsonl
  section Phase 8 — Recovery Wiring
    Add RECOVERY_ACTION : New P5 checkpoint when policy fires
                        : 7 recovery policies in boundary_recovery.py
                        : Wired to 4 agents
                        : test_recovery_wiring.py — 19/19 PASS
  section Phase 9 — Paper
    FSE 2027 submission : Section 5 added — LKW Instrumentation Design
                        : RQ6 added — policy-based recovery
                        : 2430 lines, ACM sigconf format
```

---

## Summary: What Each Checkpoint Type Measures

```mermaid
flowchart LR
  subgraph types["LKW Checkpoint Types — Purpose Map"]
    direction TB
    P1["TASK_START
    (P1 — Task Entry)
    ─────────────
    Marks run start
    Stamps fault_mode
    Enables clear_lkw reset"]

    P2a["Domain step
    e.g. CONVERT_DONE
    ADS_FETCHED
    CARD_VALIDATED
    (P2 — Decision Point)
    ─────────────
    Carries fault boolean flags
    Source of infection_point
    Source of flag-based HITL tier"]

    P3["FINAL_ANSWER
    (P3 — Output Commit)
    ─────────────
    Closes the trace
    Absence = depth > 0
    Early presence = premature term."]

    P4["BOUNDARY_CHECK
    (P4 — Agent Boundary)
    ─────────────
    Compares expected vs observed
    alert=True → Tier 3 detectable
    Contains violations list"]

    P5["RECOVERY_ACTION
    (P5 — Failure Intercept)
    ─────────────
    Records policy fired
    charge_blocked / corrected payload
    Auditable — visible in trace"]
  end

  P1 --> P2a --> P3
  P2a --> P4 --> P5

  style P1 fill:#c8e6c9,stroke:#388e3c
  style P2a fill:#fff9c4,stroke:#f9a825
  style P3 fill:#c8e6c9,stroke:#388e3c
  style P4 fill:#e3f2fd,stroke:#1976d2
  style P5 fill:#fce4ec,stroke:#c2185b
```

---

## How to Use This File

**GitHub:** All Mermaid diagrams render natively — share the file link directly with Professor.

**Slides:** Copy any diagram's Mermaid code into [mermaid.live](https://mermaid.live) → export PNG → paste into slides.

**Paper (LaTeX):** TikZ equivalents of the cross-agent chains and HITL tier tree are in `paper_updated.tex` Sections 11 and 9. This file supplements with the instrumentation-level detail not in the paper.
