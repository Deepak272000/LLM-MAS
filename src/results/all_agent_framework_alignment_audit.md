# All-Agent Framework Alignment Audit

Date: 2026-07-24
Scope: Align ongoing experiments with professor storyline and complete full-agent coverage before adding AgenTracer.

## 1) Non-negotiable Alignment Targets

1. Keep experiments aligned to the 4-phase narrative:
- Phase 1: static migration/risk perspective
- Phase 2: temporal propagation evidence
- Phase 3: predicate acceptance outcomes
- Phase 4: governance/HITL decisions

2. Prove system-wide validity with full-agent coverage:
- Every agent/service path gets at least one business fault and one system/agent fault
- HITL classification and propagation evidence are captured per run

## 2) Current Coverage Snapshot (Evidence from Repo)

### A. Pipelines that are already present

- Stability sweep (6 deterministic agent folders):
  - File: src/stability_analysis.py
  - Agents hardcoded: paymentagent, currencyagent, emailserviceagent, productcatalogagent, recommendationagent, adserviceagent
  - Outputs: src/results/stability_matrix_<agent>.json + src/results/stability_summary.json

- HITL classification on top of stability sweep:
  - File: src/hitl_detector.py (invoked by src/run_all_agents.sh)
  - Output: src/results/hitl_classification_report.json

- Cross-agent propagation campaign:
  - File: src/cross_agent_propagation.py
  - Output: src/results/cross_agent_propagation.json

- B2 systematic checkout orchestrator campaign:
  - File: src/b2_systematic_runner.py
  - Checkout chain in code: checkout_orchestrator -> productcatalog -> currency -> shipping_quote -> payment -> ship_order -> email
  - Output: src/results/b2_systematic/*

- B3 fault-injection campaign over checkout chain:
  - File: src/b3_runner.py
  - Output: src/results/b3/*

### B. Agent folders with standalone fault harness + fault result JSON

Present:
- paymentagent (has test_fault_injection.py + paymentagent_fault_results.json)
- currencyagent (has test_fault_injection.py + currencyagent_fault_results.json)
- emailserviceagent (has test_fault_injection.py + emailserviceagent_fault_results.json)
- productcatalogagent (has test_fault_injection.py + productcatalogagent_fault_results.json)
- recommendationagent (has test_fault_injection.py + recommendationagent_fault_results.json)
- adserviceagent (has test_fault_injection.py + adserviceagent_fault_results.json)

Missing standalone harness/results:
- shippingagent (orchestrator + fault_injection exists, but no test_fault_injection.py or <agent>_fault_results.json)
- cart-agent (no Python-style fault harness; C# implementation)
- checkout-agent (Go implementation; no standalone fault harness in current FI pattern)

### C. Service inventory in manifests

From kubernetes-manifests/*service.yaml:
- adservice
- cartservice
- checkoutservice
- currencyservice
- emailservice
- paymentservice
- productcatalogservice
- recommendationservice
- shippingservice

Interpretation:
- Current detailed FI campaign strongly covers payment/currency/email/catalog/recommendation/ad + shipping through B2/B3 path.
- Structural gap remains for cart/checkout in the same "standalone agent fault harness" style.

## 3) Risk to Professor Alignment (Current)

1. Storyline alignment is good in paper and code for propagation + HITL.
2. Full-agent proof is incomplete if interpreted by service inventory (cart + checkout do not yet have equivalent standalone FI artifacts).
3. Folder/harness style is heterogeneous:
- Python agents use app/agent.py + test_fault_injection.py + <agent>_fault_results.json
- checkout-agent (Go) and cart-agent (C#) are not normalized to this evidence pattern.

## 4) Standardization Target (What "same way" means)

For each agent/service path, produce the same evidence tuple:

1. Injection spec:
- At least one system/agent fault mode
- At least one business fault mode
- Explicit target agent and expected infection checkpoint

2. Run artifact schema:
- run-level JSON with LKW checkpoints, steps_reached, infection_point, propagation_depth
- status classification: TP / PARTIAL_TP / FN / INCONCLUSIVE

3. Stability artifact schema:
- 3-run fingerprint for same mode
- STABLE_PASS / STABLE_FAULT / UNSTABLE

4. HITL schema:
- Tier 1 / Tier 2 / Tier 3
- auto-resolve vs human-required flag

## 5) Execution Plan (No Shortcuts)

### Stage A — Evidence Reconciliation (must finish first)

1. Build a single machine-readable coverage matrix (agent x benchmark x artifact status):
- B1 oracle presence
- B2 systematic traces
- B3 fault traces
- stability matrix presence
- HITL classification presence

2. Lock canonical fault taxonomy used across all agents/services:
- FM classes + BL classes
- mapping file with target agent, expected first infection checkpoint, expected RIP depth range, expected HITL tier

3. Freeze folder/evidence conventions:
- one result root per campaign
- one summary JSON per agent per benchmark

### Stage B — Close Coverage Gaps (cart/checkout first)

1. Implement/bridge cart-agent and checkout-agent into the same FI evidence format
- keep language-native runtime (C#/Go), but emit normalized JSON schema
- do not fork schema by language

2. Add same fault pair policy:
- >=1 business + >=1 system fault each

3. Add to stability and HITL aggregation pipelines
- include in stability_summary and hitl_classification_report generation

### Stage C — SPEED Execution Strategy

Option 1 (safe, recommended first pass):
- sequential per-agent runs to validate harness correctness
- then full all-agent batch

Option 2 (after harness confidence):
- parallel batches grouped by dependency and resource profile
- keep cross-agent propagation chains isolated in dedicated jobs

Both options require:
- fixed environment variables snapshot
- job metadata logging (model, temp, fault mode, commit hash)

## 6) Definition of Done Before Paper Update

1. Every service/agent path has both business and system fault evidence.
2. Every run family has LKW + RIP + HITL outputs in normalized schema.
3. Stability reproducibility report includes all targeted agents/services.
4. Cross-agent propagation includes all required chains and boundary checks.
5. Only then update paper tables/claims.

## 7) Immediate Next Work Item

Create and commit a coverage_matrix.json generator that scans current artifacts and emits:
- covered / partial / missing per agent-service per benchmark
- exact missing files to produce
- run commands to close each gap
