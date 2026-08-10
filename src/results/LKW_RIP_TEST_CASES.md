# LKW/RIP Test Cases, Criteria, and Rationales

## 1. Scope

This appendix enumerates the test obligations declared in
`checkpoint_variable_map.json`:

- 38 guarded-variable checks.
- 21 named checkpoints.
- Eight agent surfaces.
- Four inter-agent def-use handoffs.

The **declared fault coverage** column records which mutations each mapping entry was
designed to expose. Some declared mutations are outside the completed 10-fault B3
matrix. A declared mapping is therefore a test design obligation, not evidence that
the corresponding mutation was executed in the reported campaign. Executed outcomes
remain controlled by the paired B3 summary artifacts and Section 13 of the results
report.

## 2. Data-Flow Criteria

| Criterion | Operational meaning | Why selected |
|---|---|---|
| All-Uses | Exercise every selected definition to each reachable C-use and P-use | Covers both value computation and control decisions without requiring every possible path |
| All-DU-Paths | Exercise the complete observable path from a definition to its uses | Reserved for high-impact values where an intermediate mutation must not be hidden |
| C-use | Variable is computed, assigned, passed as an argument, persisted, or rendered | Captures data corruption that can preserve normal control flow |
| P-use | Variable controls a branch, validation gate, escalation, or presence decision | Captures faults that alter which operation executes |

## 3. Comparison Criteria

| Relation | Pass condition | General rationale |
|---|---|---|
| `exact_string` | Observed string equals the declared B1 value | Stable categorical values must remain unchanged |
| `exact_bool` | Observed boolean equals the declared B1 value | Binary state has no legitimate intermediate value |
| `exact_integer` | Observed integer equals B1 or the corresponding input | Deterministic counts and units require identity |
| `numeric_tolerance_pct` | Absolute percentage deviation is at most the declared tolerance | Permits bounded rounding while rejecting material changes |
| `set_equality` | Sets contain the same members, independent of order | Ordering does not change ID-set semantics |
| `set_membership` | Observed value is in the declared approved set | Multiple categorical outputs may all be valid |
| `schema_regex` | Generated value matches the declared regular expression | Format is stable even when identifier value is intentionally unique |
| `range_check` | Minimum is less than or equal to the observed value, which is less than or equal to the maximum | Bounded LLM outputs may vary without being faulty |
| `non_negative_float` | Value is non-negative and negative zero is rejected | Monetary values must not be negative when no exact oracle is available |
| Required checkpoint | Every B1-required checkpoint is present in order | Missing structure exposes termination and skipped side effects |

## 4. Complete Guarded-Variable Inventory

### 4.1 PaymentAgent: 10 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| PAY-01 | `TASK_START.currency_code` | C/P | All-Uses; exact string `USD` | None assigned | Payment currency is a stable request category |
| PAY-02 | `TASK_START.units` | C | All-Uses; exact checkout input units | `FM_2_5`, `BL_AMOUNT_TAMPERING` | Requested units define the amount later charged |
| PAY-03 | `TASK_START.nanos` | C | All-Uses; exact checkout input nanos | `FM_2_5`, `BL_AMOUNT_TAMPERING` | Fractional amount must survive the charge path unchanged |
| PAY-04 | `CARD_VALIDATED.validation_bypassed` | P | All-Uses; exact boolean `false` | `FM_1_2` | A bypass changes the payment validation branch |
| PAY-05 | `CARD_VALIDATED.amount_tampered` | P | All-Uses; exact boolean `false` | `FM_2_5`, `BL_AMOUNT_TAMPERING` | Records whether mutation occurred before charging |
| PAY-06 | `CHARGE_DONE.transaction_id` | C | All-Uses; UUID-v4 schema | `FM_2_2` | IDs vary per run, but fabricated IDs violate the stable schema |
| PAY-07 | `CHARGE_DONE.units_charged` | C | All-DU-Paths; exact requested units | `FM_2_5`, `BL_AMOUNT_TAMPERING` | Financial amount must match from input through charge and save |
| PAY-08 | `CHARGE_DONE.hallucinated` | P | All-Uses; exact boolean `false` | `FM_2_2` | Controlled ground truth distinguishes a fabricated transaction |
| PAY-09 | `SAVE_DONE.saved` | P | All-Uses; exact boolean `true` | `BL_TRANSACTION_LOST` | Successful charging is incomplete unless persistence occurs |
| PAY-10 | `SAVE_DONE.double_charge` | P | All-Uses; exact boolean `false` | `BL_DOUBLE_CHARGE` | Duplicate financial side effects are never valid |

### 4.2 CurrencyAgent: 4 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| CUR-01 | `TASK_START.units` | C | All-Uses; exact input units | `FM_2_5` | Conversion must use the requested amount |
| CUR-02 | `TASK_START.to_currency` | C | All-Uses; exact scenario target currency | `FM_1_2` | Target currency is categorical and scenario-defined |
| CUR-03 | `CONVERT_DONE.units_out` | C | All-Uses; B1 value 9 with 1% tolerance | `FM_2_2`, `FM_2_5`, `BL_RATE_MANIPULATION`, `BL_STALE_RATE`, `BL_CONVERSION_OVERFLOW` | Small rounding is acceptable; large rate or hallucination errors are not |
| CUR-04 | `CONVERT_DONE.currency_swapped` | P | All-Uses; exact boolean `false` | `FM_1_2` | A substituted target currency changes transaction meaning |

### 4.3 EmailServiceAgent: 6 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| EML-01 | `TASK_START.email` | C | All-Uses; exact `customer@example.com` | `FM_2_5` | Confirmation must reach the requested recipient |
| EML-02 | `EMAIL_GENERATED.email_type` | C/P | All-Uses; exact `order_confirmation` | `FM_1_2` | Checkout requires the correct message template and route |
| EML-03 | `EMAIL_GENERATED.body_len` | C | All-Uses; range 40 through 500 | `BL_CORRUPTED_BODY` | Allows normal LLM wording variation while rejecting severe truncation |
| EML-04 | `EMAIL_GENERATED.hallucinated` | P | All-Uses; exact boolean `false` | `FM_2_2` | Controlled ground truth marks fabricated or malicious content |
| EML-05 | `EMAIL_SENT.status` | C/P | All-Uses; exact `sent` | `BL_SEND_SKIPPED` | Generation alone is insufficient; delivery must complete |
| EML-06 | `EMAIL_SENT.send_skipped` | P | All-Uses; exact boolean `false` | `BL_SEND_SKIPPED` | Detects a silent bypass of the send operation |

### 4.4 ProductCatalogAgent: 4 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| CAT-01 | `TASK_START.action` | P | All-Uses; exact `list_products` for checkout | `FM_1_2` | The request determines one stable dispatch branch |
| CAT-02 | `CATALOG_DONE.product_ids` | C | All-Uses; set equality with `PROD-001`, `PROD-002` | `FM_2_2`, `BL_PRODUCT_MISSING`, `BL_DUPLICATE_PRODUCT` | Membership matters, while product order does not |
| CAT-03 | `CATALOG_DONE.count` | P | All-Uses; exact integer 2 | `BL_PRODUCT_MISSING`, `BL_DUPLICATE_PRODUCT`, `FM_2_2` | Count independently exposes empty or extra catalog results |
| CAT-04 | `CATALOG_DONE.price_manipulated` | C | All-DU-Paths; exact boolean `false` | `BL_PRICE_MANIPULATION` | Price flows into conversion and payment, so the full path is high impact |

### 4.5 RecommendationAgent: 4 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| REC-01 | `TASK_START.user_id` | C | All-Uses; exact `user-abc123` | `FM_2_5` | Personalization must use the requesting user |
| REC-02 | `RECOMMEND_DONE.count` | C/P | All-Uses; range 1 through 20 | `BL_EMPTY_RECS`, `FM_3_1` | Recommendation count may vary but must remain useful and bounded |
| REC-03 | `RECOMMEND_DONE.hallucinated` | C | All-Uses; exact boolean `false` | `FM_2_2` | Phantom IDs can fail silently during later catalog resolution |
| REC-04 | `RECOMMEND_DONE.injection` | P | All-Uses; exact boolean `false` | `BL_INJECTION_RECS` | Undeclared sponsored content is not valid recommendation output |

### 4.6 AdServiceAgent: 3 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| ADS-01 | `CONTEXT_EXTRACTED.context_keys` | C | All-Uses; set equality with `clothing` | `FM_2_5`, `FM_1_2` | Context order is irrelevant, but category membership determines lookup |
| ADS-02 | `ADS_FETCHED.count` | C/P | All-Uses; range 1 through 10 | `BL_EMPTY_ADS`, `BL_DUPLICATE_ADS`, `BL_AD_INJECTION` | Ad count may vary but empty or excessive output is invalid |
| ADS-03 | `ADS_FETCHED.hallucinated` | C | All-Uses; exact boolean `false` | `FM_2_2` | Fabricated redirect targets create a direct security risk |

### 4.7 ShippingQuoteAgent: 2 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| SHQ-01 | `TASK_START.item_count` | C | All-Uses; exact integer 1 | `BL_INVENTORY_MISMATCH` | Quantity is the input definition that drives quote cost |
| SHQ-02 | `QUOTE_DONE.cost_usd` | C/P | All-DU-Paths; range 0 through 50 plus non-negative-float check | `BL_REFUND_REASONING`, `BL_INVENTORY_MISMATCH`, `FM_2_5` | Cost may vary, but negative, negative-zero, or implausible values are invalid and affect payment |

### 4.8 ShipOrderAgent: 5 checks

| ID | Checkpoint.field | Use | Criterion and expected relation | Declared fault coverage | Rationale |
|---|---|---|---|---|---|
| SHO-01 | `CARRIER_DONE.carrier` | C | All-Uses; membership in declared carrier set | `FM_2_2`, `BL_VENDOR_NEGOTIATION` | Carrier selection may vary but must remain approved |
| SHO-02 | `CARRIER_DONE.ignored_downstream_quote` | P | All-Uses; exact boolean `false` | `FM_2_5` | Carrier selection must use the current quote rather than stale data |
| SHO-03 | `TRACKING_DONE.tracking_id` | C | All-Uses; alphanumeric schema excluding `PREMATURE` and `INCOMPLETE` prefixes | `FM_3_1`, `FM_1_2` | Tracking IDs vary, while malformed termination markers are invalid |
| SHO-04 | `ESCALATION_CHECK.escalation_required` | P | All-Uses; exact boolean `false` for the normal scenario | `BL_CUSTOMER_ESCALATION` | A true value must alter HITL control flow |
| SHO-05 | `SAVE_DONE.saved` | C/P | All-DU-Paths; required checkpoint and exact boolean `true` | `BL_SHIPMENT_LOST` | Persistence is a mandatory side effect; absence is itself the signal |

## 5. RIP Handoff Test Cases

| ID | Def-to-use handoff | Reachability criterion | Infection criterion | Propagation criterion | Rationale |
|---|---|---|---|---|---|
| RIP-01 | `CurrencyAgent.CONVERT_DONE.units_out` to `PaymentAgent.TASK_START.units` | Both checkpoints occur | Currency output violates its B1/tolerance relation | Payment input carries the same invalid amount | Financial value corruption can cause direct overcharge |
| RIP-02 | `ProductCatalogAgent.CATALOG_DONE.product_ids` to `RecommendationAgent.TASK_START.product_ids` | Both checkpoints occur | Catalog ID set violates expected membership | Recommendation receives missing or phantom IDs | Invalid catalog context can silently corrupt personalization |
| RIP-03 | `ShippingQuoteAgent.QUOTE_DONE.cost_usd` to `PaymentAgent.TASK_START.units` | Quote and payment-input checkpoints occur | Quote violates range/non-negative criteria | Payment receives an amount derived from the invalid quote | Quote corruption crosses into a financial side effect |
| RIP-04 | `ShipOrderAgent.TRACKING_DONE.tracking_id` to `EmailServiceAgent.TASK_START` order context | Tracking and email-start checkpoints occur | Tracking ID violates its schema | Invalid tracking context reaches customer notification | A malformed shipment identifier can escape into external communication |

## 6. RIP Decision Procedure

For each test path:

1. **Reachability:** mark the handoff reachable only when the relevant source and sink
   checkpoints occur.
2. **Infection:** select the earliest missing required checkpoint or guarded field that
   fails its fixed relation.
3. **Propagation:** require a corresponding later value or boundary-contract deviation;
   later deviation alone is not sufficient when the fault was injected globally.
4. **Outcome:** classify the run as TP when at least two compared agents deviate,
   Partial TP when exactly one deviates, FN when no compared check fails, and
   inconclusive when infrastructure prevents judgment.

Diagnostic booleans are retained as experiment ground truth. They do not replace the
independent checkpoint-presence, value-relation, or handoff-consistency criteria.