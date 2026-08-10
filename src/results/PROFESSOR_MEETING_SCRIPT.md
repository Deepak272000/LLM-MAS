# Professor Meeting Cue Sheet

**Use with:** `src/results/RESULTS_REPORT.md` and `src/results/paper_updated.pdf`
**Target length:** 12-15 minutes plus code questions

## Opening

The project studies silent data-flow faults in agentic microservices. A workflow can
finish successfully while carrying the wrong amount, product ID, email recipient,
shipping quote, or tracking state. The measurement approach places named LKW
checkpoints at agent, helper, and service-interface boundaries and interprets the
resulting traces with Reachability, Infection, and Propagation analysis.

The complete B3 result is 51 detections among 58 conclusive runs, or 87.9%, with a
Wilson 95% confidence interval of 77.1% to 94.0%. Two additional runs were
inconclusive.

## 1. Architecture

The agent layer uses Ollama for routing, reasoning, or tool choice. Controlled helpers
translate the decision into service-shaped requests. The systematic campaign uses
mock clients for repeatable service responses, so it measures fault observability at
controlled interfaces rather than resilience of a fully deployed gRPC system.

Show the architecture figure in the paper, then open:

1. `src/currencyagent/app/graph.py` for LangGraph routing.
2. `src/currencyagent/app/agent.py` for the service-facing action.
3. `src/currencyagent/app/fault_injection.py` for mutation and LKW recording.

## 2. What Each File Layer Does

- `main.py`: creates the FastAPI application.
- `router.py`: receives the request and invokes the graph.
- `graph.py`: stores workflow state and uses the LLM to route the task.
- `agent.py`: performs the domain operation through the service client.
- `fault_injection.py`: activates one configured fault and records checkpoints.
- `co_helper_*.py`: provides a controlled entry point for experiment runners.
- `b1_oracle_runner.py`: creates expected checkpoint state.
- `b2_variance_runner.py` and `b2_systematic_runner.py`: characterize no-fault
  variation.
- `b3_runner.py`: executes one fault per run and compares the trace with B1/B2.

The full per-agent map is in Section 5 of the results report.

## 3. LKW and Flags

Each checkpoint stores a named step and selected business values. For example,
`CHARGE_DONE` records the charged amount and transaction outcome; `CONVERT_DONE`
records the requested and returned currency values.

The configured fault mode and diagnostic booleans are experiment ground truth. They
show which mutation was active, but they are not sufficient production detection
signals. Detection comes from a missing required step or a guarded value outside its
fixed B1/B2 relation.

This distinction matters for HITL: production automation must infer corruption from
real values rather than trust a test-only `fault_injected` field.

## 4. B1, B2, and B3

### B1

B1 defines expected checkpoint state. Six service agents use deterministic controlled
helpers, and shipping uses a selected no-fault systematic baseline. Volatile values,
such as transaction IDs, are checked by schema rather than exact identity.

### B2

B2 repeats no-fault execution. It identifies stable business fields and normal
variation before any B3 result is judged. Numeric, categorical, set, schema, and
boolean relations are selected per field.

### B3

The complete matrix contains 10 faults, 2 configurations, and 3 repetitions:

```text
10 x 2 x 3 = 60 runs
```

- Temperature 0.7: 28/30 detected, or 93.3%.
- Temperature 1.0: 23/28 conclusive runs detected, or 82.1%; two were inconclusive.
- Pooled: 51/58 conclusive runs detected, or 87.9%.

The weakest fault was corrupted email body, with one detection among five conclusive
runs and one additional inconclusive run.

## 5. Cross-Agent Chains

Open `src/results/cross_agent_propagation.json`.

### Chain A: Currency to Payment

The expected boundary amount was 9 units. FM-2.2 produced 1337 units. The boundary
measured a difference of 1328 units, raised an alert, blocked the charge, and requested
HITL. The 1328-unit amount is prevented loss, not realized loss.

### Chain B: Catalog to Recommendation

The expected product ID disappeared from the catalog payload. The boundary raised an
alert, restored the last-known-good product ID, and allowed RecommendationAgent to
continue without HITL.

Both predefined policies executed. One of two avoided HITL. This is feasibility
evidence from two chains, not a general recovery-rate estimate.

## 6. HITL Result

Open `src/results/hitl_classification_report.json`.

The artifact contains 48 injected isolated-agent cases:

- 8 Tier 1 structural cases: detectable from missing steps.
- 34 Tier 2 flag-detectable cases: experiment fields show the issue, but production
  needs equivalent runtime predicates.
- 6 Tier 3 silent semantic cases: require value-level semantic validation.

All 48 are marked as requiring HITL in the saved artifact. Only the eight structural
cases are marked automatically detectable. Detection is not the same as resolution.
There is no automated verifier agent yet; final trace review is manual.

## 7. AgentTracer-Compatible Work

Open `src/agentracer_adapter/lkw_to_trajectory.py` and
`src/results/agentracer/trajectories_index.json`.

The adapter created 67 compatible files: 65 reconstructed single-agent trajectories
and two converted cross-agent trajectories. The single-agent files use B1 values and
HITL classifications with synthetic deviation markers. They are not 65 independent
executions.

No saved attribution prediction report supports an accuracy claim. The current result
is conversion readiness. The next experiment must run an attributor against independent
measured trajectories and compare predictions with held-out ground truth.

## 8. Main Findings

1. Workflow success does not guarantee correct business data.
2. Value-level checkpoints expose faults that step monitoring misses.
3. B1/B2 calibration is necessary before B3 comparison.
4. Detection changed with model temperature.
5. Email semantic validation is the clearest current weakness.
6. Globally injected faults do not support causal propagation claims by themselves.
7. Boundary contracts can block or repair unsafe handoffs.
8. HITL automation and independent attribution evaluation remain future work.

## 9. Next Development Step

The immediate engineering step is to replace experiment-only diagnostic flags with
production validators for amounts, product IDs, recipients, quote consistency, and
tracking state. Boundary recovery should also emit an `ATTRIBUTION_HINT` so blocking a
fault does not erase evidence about its source.

The next research step is a Verifier Agent that checks trace and boundary evidence,
selects only explicitly safe recovery actions, and escalates ambiguous or irreversible
decisions. It must be evaluated on a held-out campaign, separately from the pipeline
that generates its ground truth.

## Closing

The current result is not simply a mutation score. It shows where agentic workflows
need value-aware contracts: at the points where one agent or service hands a business
value to the next component. The evidence supports detection and two containment
examples; the next phase is production-grade validation, controlled HITL automation,
and independent attribution evaluation.