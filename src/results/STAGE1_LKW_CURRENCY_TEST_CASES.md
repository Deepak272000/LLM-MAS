# Stage 1 LKW DU-Pair Test Cases — CurrencyAgent

**Purpose:** Formal data-flow test cases derived from static analysis of the actual CurrencyAgent source code.  
These are not test obligations. Each row is a concrete test case with a real def location, a real use location,  
a def-clear path through the code, a concrete test input, and an expected value at the use point.

**Criterion:** All-Uses (All-P-Uses + All-C-Uses), Def-clear path (Laski–Korel–Weyuker 1983; Rapps–Weyuker 1982).  
**Use-type key:** c-use = computation use (value placed in expression/dict/argument); p-use = predicate use (value tested in branch condition).

**Files read for this analysis (commit: deepak/fault-injection branch):**
| File | Line range read |
|---|---|
| `src/currencyagent/app/agent.py` | 1–145 |
| `src/currencyagent/app/fault_injection.py` | 1–260 |
| `src/currencyagent/app/graph.py` | 1–120 |
| `src/currencyagent/app/grpc_client.py` | 1–80 |

---

## 1. Variable Definition Map

Exact locations where each tracked variable is defined or redefined in source code.

| Var | Def ID | Location | Expression |
|---|---|---|---|
| `units` | D1 | `agent.py:29` | `units: int = 0` — function parameter |
| `units` | D2 | `agent.py:56` | `units, nanos = fi.maybe_tamper_amount(units, nanos)` — conditional reassignment (FM_2_5 fires `fi.py:108`) |
| `to_currency` | D1 | `agent.py:30` | `to_currency: str = "EUR"` — function parameter |
| `to_currency` | D2 | `agent.py:58` | `to_currency = fi.maybe_swap_currency(to_currency)` — conditional reassignment (FM_1_2 fires `fi.py:117`) |
| `data["units"]` | D1 | `agent.py:71` | `data = client.convert(...)` — gRPC response dict, key `"units"` = real converted amount |
| `data["units"]` | D2 | `agent.py:79` | `data = fi.maybe_hallucinate_result(data)` — sets `"units": 1337` when FM_2_2 |
| `data["units"]` | D3 | `agent.py:81` | `data = fi.maybe_manipulate_rate(data)` — sets `"units": original * 10` when BL_RATE_MANIPULATION |
| `data["units"]` | D4 | `agent.py:83` | `data = fi.maybe_inject_stale_rate(data)` — sets `"units": 0` when BL_STALE_RATE |
| `data["units"]` | D5 | `agent.py:85` | `data = fi.maybe_overflow_result(data)` — sets `"units": 2^53` when BL_CONVERSION_OVERFLOW |
| `currency_swapped` | D1 | `agent.py:134` | `"currency_swapped": fi.FAULT_MODE == "FM_1_2"` — boolean expr in CONVERT_DONE dict |

---

## 2. DU-Pair Test Cases

### Variable: `units`

---

**TC-CUR-01** — `units` D1 → c-use at TASK_START checkpoint

| Field | Value |
|---|---|
| Variable | `units` |
| Definition (D1) | `agent.py:29` — `units: int = 0` (parameter) |
| Use (c-use) | `agent.py:40` — `"units": units` inside `lkw.record("TASK_START", {...})` |
| Def-clear path | L29 → L36 → L40 (no redefinition of `units` between L29 and L40) |
| Test input | `units=10, nanos=0, from_currency="USD", to_currency="EUR", action="convert"` |
| Expected at use | `TASK_START["units"] == 10` |
| B1 observed (NONE) | **10** — PASS |
| B2 observed (FM_2_5) | **10** — PASS (tamper fires at L56, *after* this use; D1 not yet redefined) |
| B2 observed (FM_3_1) | **10** — PASS (early return fires at L47–50, *after* this use) |
| B3 observed | **10** — PASS (LLM classification at graph.py:29–57 does not affect L36–40) |
| LKW verdict (B1 vs B2) | PASS — fault not yet visible at this use point |
| Group | Informational; does not discriminate any fault |

---

**TC-CUR-02** — `units` D1 → c-use as argument to tamper function

| Field | Value |
|---|---|
| Variable | `units` |
| Definition (D1) | `agent.py:29` — parameter |
| Use (c-use) | `agent.py:56` RHS — `fi.maybe_tamper_amount(units, nanos)` — D1 value passed as argument |
| Def-clear path | L29 → L36–43 → L46–50 → L52 → L54 → L56 RHS (no redefinition before L56 RHS) |
| Test input | `units=10, action="convert"` |
| Expected at use | `maybe_tamper_amount` receives `units=10` |
| B1 observed (NONE) | Receives **10** → returns 10 unchanged → D2 = 10 |
| B2 observed (FM_2_5) | Receives **10** (D1 value intact at this use) → `tampered = 10 * 5 = 50` (`fi.py:108`) → returns 50 → D2 = 50 |
| B3 observed (FM_2_5, LLM routes to convert) | Same as B2: D2 = 50 |
| B3 observed (FM_2_5, LLM routes to get_supported_currencies) | **Path infeasible** — `if action == "convert"` (L54) not entered; L56 never reached |
| LKW verdict | PASS at use point; fault value appears in D2 (return), not at this use |
| Note | LKW test for this pair passes in B1 and B2 at the use point; the injected value is observable in D2, covered by TC-CUR-03 |

---

**TC-CUR-03** — `units` D2 → c-use in gRPC call  *(FM_2_5 detection path)*

| Field | Value |
|---|---|
| Variable | `units` |
| Definition (D2) | `agent.py:56` — `units, nanos = fi.maybe_tamper_amount(units, nanos)` |
| Use (c-use) | `agent.py:73` — `units=units,` inside `client.convert(...)` |
| Def-clear path | L56 → L58 → L61 → (L62–68 if err, early return) → L70 → L71 → L73 (no redefinition of `units` between L56 and L73) |
| Test input | `units=10, action="convert"` |
| Expected at use (B1) | `client.convert` receives `units=10` |
| B1 observed (NONE) | `client.convert` receives **10** — PASS |
| B2 observed (FM_2_5) | `client.convert` receives **50** (= 10 × 5) — **FAIL** — value at use differs from expected |
| B3 observed (FM_2_5, LLM routes to convert) | `client.convert` receives **50** — **FAIL** |
| B3 observed (FM_2_5, LLM routes elsewhere) | Path infeasible — L73 not reached |
| LKW verdict | **FAIL in B2** — LKW catches FM_2_5 via this DU path |
| **Group** | **Group 1** — standard DU-path test detects FM_2_5; no added checkpoint needed for this fault |

---

**TC-CUR-04** — `units` D2 → c-use in CONVERT_DONE checkpoint

| Field | Value |
|---|---|
| Variable | `units` |
| Definition (D2) | `agent.py:56` |
| Use (c-use) | `agent.py:127` — `"units_in": units` inside `lkw.record("CONVERT_DONE", {...})` |
| Def-clear path | L56 → L58 → L61 → L70 → L73 → L79 → L81 → L83 → L85 → L87–122 → L127 (no redefinition of `units` in this span) |
| Test input | `units=10, action="convert"` |
| Expected at use (B1) | `CONVERT_DONE["units_in"] == 10` |
| B1 observed (NONE) | **10** — PASS |
| B2 observed (FM_2_5) | **50** — **FAIL** (tampered value propagated to checkpoint) |
| B3 observed | **50** if LLM routes to convert; path infeasible otherwise |
| LKW verdict | **FAIL in B2 (FM_2_5)** — redundant with TC-CUR-03; confirms tampered value persists to checkpoint |
| **Group** | **Group 1** — FM_2_5 already detectable at TC-CUR-03; checkpoint records confirm propagation |

---

### Variable: `to_currency`

---

**TC-CUR-05** — `to_currency` D1 → c-use at TASK_START checkpoint

| Field | Value |
|---|---|
| Variable | `to_currency` |
| Definition (D1) | `agent.py:30` — `to_currency: str = "EUR"` (parameter) |
| Use (c-use) | `agent.py:39` — `"to_currency": to_currency` inside `lkw.record("TASK_START", {...})` |
| Def-clear path | L30 → L36 → L39 |
| Test input | `to_currency="EUR", action="convert"` |
| Expected at use | `TASK_START["to_currency"] == "EUR"` |
| B1 observed (NONE) | **"EUR"** — PASS |
| B2 observed (FM_1_2) | **"EUR"** — PASS (swap fires at L58, *after* this use) |
| LKW verdict | PASS — fault not yet visible at this use |
| Group | Informational |

---

**TC-CUR-06** — `to_currency` D1 → c-use as argument to swap function

| Field | Value |
|---|---|
| Variable | `to_currency` |
| Definition (D1) | `agent.py:30` |
| Use (c-use) | `agent.py:58` RHS — `fi.maybe_swap_currency(to_currency)` |
| Def-clear path | L30 → L36–43 → L46–50 → L52 → L54 → L56 → L58 RHS |
| Test input | `to_currency="EUR", action="convert"` |
| Expected at use | `maybe_swap_currency` receives `"EUR"` |
| B1 observed (NONE) | Receives **"EUR"** → returns "EUR" → D2 = "EUR" |
| B2 observed (FM_1_2) | Receives **"EUR"** → `wrong = "JPY"` (`fi.py:117`, since "EUR" != "JPY") → returns "JPY" → D2 = "JPY" |
| LKW verdict | PASS at use point (D1 value intact); fault value in D2, covered by TC-CUR-07 |

---

**TC-CUR-07** — `to_currency` D2 → c-use in gRPC call  *(FM_1_2 detection path)*

| Field | Value |
|---|---|
| Variable | `to_currency` |
| Definition (D2) | `agent.py:58` — `to_currency = fi.maybe_swap_currency(to_currency)` |
| Use (c-use) | `agent.py:75` — `to_code=to_currency,` inside `client.convert(...)` |
| Def-clear path | L58 → L61 → (L62–68 if err) → L70 → L71 → L75 |
| Test input | `to_currency="EUR", action="convert"` |
| Expected at use (B1) | `client.convert` receives `to_code="EUR"` |
| B1 observed (NONE) | `client.convert` receives **"EUR"** — PASS |
| B2 observed (FM_1_2) | `client.convert` receives **"JPY"** — **FAIL** |
| B3 observed (FM_1_2, LLM routes to convert) | `client.convert` receives **"JPY"** — **FAIL** |
| LKW verdict | **FAIL in B2** — LKW catches FM_1_2 via this DU path |
| **Group** | **Group 1** — standard DU-path test detects FM_1_2 |

---

**TC-CUR-08** — `to_currency` D2 → c-use in CONVERT_DONE checkpoint

| Field | Value |
|---|---|
| Variable | `to_currency` |
| Definition (D2) | `agent.py:58` |
| Use (c-use) | `agent.py:126` — `"to_currency": to_currency` inside `lkw.record("CONVERT_DONE", {...})` |
| Def-clear path | L58 → L61 → L70 → L73–76 → L79 → L81 → L83 → L85 → L87–122 → L126 |
| Test input | `to_currency="EUR", action="convert"` |
| Expected at use (B1) | `CONVERT_DONE["to_currency"] == "EUR"` |
| B1 observed (NONE) | **"EUR"** — PASS |
| B2 observed (FM_1_2) | **"JPY"** — **FAIL** |
| LKW verdict | **FAIL in B2 (FM_1_2)** — redundant with TC-CUR-07; confirms swapped currency persists |
| **Group** | **Group 1** |

---

### Variable: `data["units"]`  (units_out)

The `data` dict is defined at `agent.py:71` by `client.convert()` and conditionally redefined by fault injection functions on lines 79, 81, 83, 85. The tracked "variable" is the `"units"` key of this dict.

---

**TC-CUR-09** — `data["units"]` D1 → c-use in CONVERT_DONE  *(fault-free path; FM_3_1 kills this path)*

| Field | Value |
|---|---|
| Variable | `data["units"]` |
| Definition (D1) | `agent.py:71` — `data = client.convert(...)` (gRPC returns dict with `"units"` = live converted amount) |
| Use (c-use) | `agent.py:128` — `"units_out": data.get("units")` inside `lkw.record("CONVERT_DONE", {...})` |
| Def-clear path (NONE mode) | L71 → L79 → L81 → L83 → L85 → L87–122 → L128 (no conditional mutation fires; D2–D5 all pass through) |
| Test input | `units=10, from_currency="USD", to_currency="EUR", action="convert"` |
| Expected at use (B1) | `CONVERT_DONE["units_out"] > 0` (live gRPC result; approximately 9 for 10 USD→EUR) |
| B1 observed (NONE) | **~9** (live gRPC rate) — PASS |
| B2 observed (FM_3_1) | **PATH NOT REACHED** — agent returns at L47–50 before `client.convert()` at L71; CONVERT_DONE checkpoint never recorded |
| B3 observed (FM_3_1) | Same as B2: CONVERT_DONE absent from LKW trace |
| LKW verdict | **PATH INFEASIBLE under FM_3_1** — D1 is never defined; U at L128 is never reached; fault detected via missing checkpoint step, not via wrong value at an observed use |
| **Group** | **Group 2** — FM_3_1 kills the def-use path through absence. A DU-path test for D1→U2 cannot produce a wrong value; it can only detect coverage drop. The LKW checkpoint explicitly records EXPECTED_STEPS = `["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"]`; under FM_3_1, CONVERT_DONE is missing. *This is why the checkpoint contribution is non-trivial for FM_3_1.* |

---

**TC-CUR-10** — `data["units"]` D2 → c-use in CONVERT_DONE  *(FM_2_2 hallucination path)*

| Field | Value |
|---|---|
| Variable | `data["units"]` |
| Definition (D2) | `agent.py:79` — `data = fi.maybe_hallucinate_result(data)` — when FM_2_2, sets `data["units"] = 1337` (`fi.py:129`) |
| Use (c-use) | `agent.py:128` — `"units_out": data.get("units")` |
| Def-clear path | L79 → L81 → L83 → L85 → L87–122 → L128 (D3–D5 do not fire when FM_2_2 is active; single fault model) |
| Test input | `units=10, from_currency="USD", to_currency="EUR"`, FAULT_MODE=FM_2_2 |
| Expected at use (B1 oracle) | `CONVERT_DONE["units_out"] ≈ 9` (live gRPC result) |
| B2 observed (FM_2_2) | `CONVERT_DONE["units_out"] == 1337` — **FAIL** |
| B3 observed (FM_2_2, LLM routes to convert) | **1337** — **FAIL** |
| LKW verdict | **FAIL in B2** — value at use point is wrong (1337 vs ~9); LKW catches FM_2_2 |
| **Group** | **Group 1** — standard DU-path test detects FM_2_2 |

---

**TC-CUR-11** — `data["units"]` D3 → c-use in CONVERT_DONE  *(BL_RATE_MANIPULATION path)*

| Field | Value |
|---|---|
| Variable | `data["units"]` |
| Definition (D3) | `agent.py:81` — `data = fi.maybe_manipulate_rate(data)` — when BL_RATE_MANIPULATION, sets `data["units"] = original * 10` (`fi.py:144`) |
| Use (c-use) | `agent.py:128` — `"units_out": data.get("units")` |
| Def-clear path | L81 → L83 → L85 → L87–122 → L128 |
| Test input | `units=10, from_currency="USD", to_currency="EUR"`, FAULT_MODE=BL_RATE_MANIPULATION |
| Expected at use (B1 oracle) | `CONVERT_DONE["units_out"] ≈ 9` |
| B2 observed (BL_RATE_MANIPULATION) | `CONVERT_DONE["units_out"] ≈ 90` (= ~9 × 10) — **FAIL** |
| LKW verdict | **FAIL in B2** — LKW catches BL_RATE_MANIPULATION |
| **Group** | **Group 1** |

---

### Variable: `currency_swapped`  (infection flag)

This is not a standalone Python variable. It is a boolean expression `fi.FAULT_MODE == "FM_1_2"` computed inline at the CONVERT_DONE checkpoint (`agent.py:134`) and then read by `rip_summary()` in `fault_injection.py:74`.

---

**TC-CUR-12** — `currency_swapped` flag D1 → p-use in rip_summary infection test

| Field | Value |
|---|---|
| Variable | `currency_swapped` (CONVERT_DONE dict field) |
| Definition (D1) | `agent.py:134` — `"currency_swapped": fi.FAULT_MODE == "FM_1_2"` — evaluated when CONVERT_DONE is recorded |
| Use (p-use) | `fault_injection.py:74` — `d.get("currency_swapped")` inside `any([...])` branch predicate in `rip_summary()` |
| Def-clear path | L134 (value stored in checkpoint dict) → `rip_summary()` called at `agent.py:138` → `fi.py:63` → `fi.py:68` (loop over checkpoints) → `fi.py:72–79` → `fi.py:74` |
| Test input | `to_currency="EUR"`, FAULT_MODE=FM_1_2 |
| Expected at use (B1) | `d.get("currency_swapped") == False` → `infection_point == None` |
| B1 observed (NONE) | `False` → no infection flagged — PASS |
| B2 observed (FM_1_2) | `True` → `infection_point == "CONVERT_DONE"` — infection correctly identified |
| B3 observed (FM_1_2, LLM routes to convert) | `True` — infection flagged |
| B3 observed (FM_1_2, LLM routes away) | **CONVERT_DONE never recorded** — `rip_summary` sees no `currency_swapped` flag at all |
| LKW verdict | **FAIL in B2** — boolean predicate at use point differs from B1; LKW catches FM_1_2 via infection flag |
| **Group** | **Group 1** (redundant with TC-CUR-07; this is the checkpoint-layer confirmation) |

---

## 3. Fault Group Classification

This is the key table the professor requested. For each fault mode: which DU path detects it, whether standard LKW alone suffices, and whether the checkpoint adds non-redundant value.

| Fault Mode | Detecting DU Pair | Detecting TC | Wrong value at use? | LKW alone sufficient? | Group | Checkpoint adds value? |
|---|---|---|---|---|---|---|
| FM_2_5 (tamper amount) | `units` D2→U at L73 | TC-CUR-03 | Yes — gRPC receives 50 instead of 10 | **Yes** | **1** | Redundant (checkpoint confirms, doesn't reveal) |
| FM_1_2 (swap currency) | `to_currency` D2→U at L75 | TC-CUR-07 | Yes — gRPC receives "JPY" instead of "EUR" | **Yes** | **1** | Redundant |
| FM_2_2 (hallucinate result) | `data["units"]` D2→U at L128 | TC-CUR-10 | Yes — CONVERT_DONE records 1337 instead of ~9 | **Yes** | **1** | Redundant |
| BL_RATE_MANIPULATION | `data["units"]` D3→U at L128 | TC-CUR-11 | Yes — CONVERT_DONE records ~90 instead of ~9 | **Yes** | **1** | Redundant |
| **FM_3_1 (premature term.)** | `data["units"]` D1→U at L128 | TC-CUR-09 | **No — use point never reached** | **No** | **2** | **Non-redundant: checkpoint detects missing CONVERT_DONE step** |

---

## 4. Justification for Checkpoint Contribution

### Group 1 faults (FM_2_5, FM_1_2, FM_2_2, BL_RATE_MANIPULATION)

Standard DU-path testing detects all four. The checkpoints (`lkw.record("CONVERT_DONE")`) confirm the values observed but do not provide the primary detection mechanism. These faults are listed for completeness and to show coverage of the def-use pairs; they do **not** justify the checkpoint contribution as an independent methodological claim.

### Group 2 fault (FM_3_1)

FM_3_1 injects early return at `agent.py:47–50`, before `client.convert()` at L71. This kills all def-use paths that require D1 (the gRPC result definition) to be reached. Specifically:

- `data["units"]` D1 is never defined
- The use at L128 (`CONVERT_DONE["units_out"]`) is never reached  
- No DU-path test for D1→U128 can produce a wrong-value failure because the path is infeasible under FM_3_1

**The LKW checkpoint detects FM_3_1 via a mechanism outside standard def-use coverage:**  
`LKWCheckpoint.EXPECTED_STEPS = ["TASK_START", "CONVERT_DONE", "FINAL_ANSWER"]`  
Under FM_3_1, CONVERT_DONE is absent from the recorded trace. The `rip_summary()` function reports `"missing_steps": ["CONVERT_DONE"]` and `"propagation_depth": 1`. This is a structural trace invariant check, not a wrong-value oracle — and it is precisely what makes the checkpoint a complementary technique rather than a redundant one.

**This is the concrete justification for the checkpoint contribution for FM_3_1:**  
> A DU-path test suite with oracle "what value does CONVERT_DONE record for units_out" cannot produce a failing oracle for FM_3_1 because CONVERT_DONE is never written. Only a trace-completeness check (are all expected steps reached?) catches premature termination. The checkpoint provides that check.

---

## 5. B3 (LLM) Qualification

The LLM classification step at `graph.py:29–57` introduces a probabilistic gate before any def-use path in the conversion branch is exercised. The LLM must route the query to `"convert"` (not `"get_supported_currencies"`) for the `if action == "convert"` block at `agent.py:54` to be entered.

**Effect on DU-pair coverage in B3:**

| Fault | LLM routes correctly | LLM misroutes |
|---|---|---|
| FM_2_5 | TC-CUR-03 path exercised; FAIL observable | Path infeasible; fault masked by misrouting |
| FM_1_2 | TC-CUR-07 path exercised; FAIL observable | Path infeasible |
| FM_2_2 | TC-CUR-10 path exercised; FAIL observable | Path infeasible |
| BL_RATE_MANIPULATION | TC-CUR-11 path exercised; FAIL observable | Path infeasible |
| FM_3_1 | CONVERT_DONE missing from trace (Group 2 detection) | CONVERT_DONE *also* missing — misrouting and premature termination produce same trace signature |

The last row is the key B3 complication: in B3 under FM_3_1, a misrouted LLM and a premature-termination fault produce *identical* trace signatures (CONVERT_DONE absent). This is why HITL annotation is needed for B3 FM_3_1 runs — the trace alone is insufficient to distinguish the two causes.

---

*Analysis date: 2026-08-10. Source code read from `deepak/fault-injection` branch.*
