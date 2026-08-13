"""
parse_lkw_payment_evidence.py
================================
Reads real execution artifacts and maps them to TC-PAY-XX DU-pair test cases
for PaymentAgent, following the same pattern as parse_lkw_currency_evidence.py.

Data sources:
  B1 (clean baseline, no fault, real LKW trace):
      b2_raw_runs/paymentagent_b2_run{1,2,3}.json

  B2 (direct agent.run(), no LLM, all fault modes):
      ../paymentagent/paymentagent_fault_results.json

  B3 (live LLM temp=1.0, with fault injection, 3 runs per fault):
      b3/raw/b3_<FAULT>_3b_temp1.0_run{1,2,3}.json
      NOTE: BL_TRANSACTION_LOST, BL_DOUBLE_CHARGE, BL_CARD_DECLINED
            are payment-specific and have no B3 files — marked NOT_MEASURED.

Run: python parse_lkw_payment_evidence.py
Outputs: lkw_payment_evidence.json + printed table
"""

import json
import os
import glob
from pathlib import Path

RESULTS_DIR  = Path(__file__).parent
B2_RAW_DIR   = RESULTS_DIR / "b2_raw_runs"
B3_RAW_DIR   = RESULTS_DIR / "b3" / "raw"
B2_TRUE_FILE = Path(__file__).parent.parent / "paymentagent" / "paymentagent_fault_results.json"

# ---------------------------------------------------------------------------
# DU-pair test case definitions
# ---------------------------------------------------------------------------
TC_DEFINITIONS = [
    {
        "tc_id":      "TC-PAY-01",
        "fault_mode": None,
        "checkpoint": "TASK_START",
        "field":      "units",
        "desc":       "units param → c-use at TASK_START",
        "group":      "info",
        "def_loc":    "agent.py:23 (param)",
        "use_loc":    "agent.py:40 c-use (TASK_START)",
        "def_clear":  "L23→L38→L40",
        "note":       "fault fires after this use; always equals input",
    },
    {
        "tc_id":      "TC-PAY-02",
        "fault_mode": "FM_2_5",
        "checkpoint": "CHARGE_DONE",
        "field":      "units_charged",
        "desc":       "units after tamper (×3) → CHARGE_DONE.units_charged c-use",
        "group":      1,
        "def_loc":    "agent.py:98 (after tamper)",
        "use_loc":    "agent.py:132 c-use (CHARGE_DONE)",
        "def_clear":  "L98→L100→L121→L132",
        "note":       "FM_2_5 tamper: units*3 before charge_payment; units_charged logs tampered value",
    },
    {
        "tc_id":      "TC-PAY-03",
        "fault_mode": "FM_3_1",
        "checkpoint": "CHARGE_DONE",
        "field":      "transaction_id",
        "desc":       "transaction_id from charge_payment (L121) → CHARGE_DONE c-use — path killed",
        "group":      2,
        "def_loc":    "agent.py:121 (charge_payment return)",
        "use_loc":    "agent.py:132 c-use (CHARGE_DONE)",
        "def_clear":  "L121→L124→L132",
        "note":       "FM_3_1 early return at L91-95: charge_payment never called; CHARGE_DONE absent",
    },
    {
        "tc_id":      "TC-PAY-04",
        "fault_mode": "FM_2_2",
        "checkpoint": "CHARGE_DONE",
        "field":      "hallucinated",
        "desc":       "hallucinated flag (L124) → CHARGE_DONE c-use",
        "group":      1,
        "def_loc":    "agent.py:124 (maybe_hallucinate_transaction)",
        "use_loc":    "agent.py:132 c-use (CHARGE_DONE)",
        "def_clear":  "L124→L132",
        "note":       "FM_2_2: transaction_id replaced with FAKE-TXN-XXXX; hallucinated=True at CHARGE_DONE",
    },
    {
        "tc_id":      "TC-PAY-05",
        "fault_mode": "FM_1_2",
        "checkpoint": "CARD_VALIDATED",
        "field":      "validation_bypassed",
        "desc":       "validation_bypassed flag (L113) → CARD_VALIDATED p-use",
        "group":      1,
        "def_loc":    "agent.py:113 (maybe_bypass_validation)",
        "use_loc":    "agent.py:126 p-use (CARD_VALIDATED)",
        "def_clear":  "L113→L122→L126",
        "note":       "FM_1_2: validation skipped entirely; validation_bypassed=True at CARD_VALIDATED",
    },
    {
        "tc_id":      "TC-PAY-06",
        "fault_mode": "BL_TRANSACTION_LOST",
        "checkpoint": "SAVE_DONE",
        "field":      "save_skipped",
        "desc":       "save_skipped flag (L151) → SAVE_DONE c-use",
        "group":      1,
        "def_loc":    "agent.py:151 (maybe_skip_save)",
        "use_loc":    "agent.py:155 c-use (SAVE_DONE)",
        "def_clear":  "L151→L153→L155",
        "note":       "BL_TRANSACTION_LOST: MongoDB save silently bypassed; save_skipped=True; no B3 data",
        "b3_note":    "NOT_MEASURED — no B3 campaign for this payment-specific fault",
    },
    {
        "tc_id":      "TC-PAY-07",
        "fault_mode": "BL_DOUBLE_CHARGE",
        "checkpoint": "SAVE_DONE",
        "field":      "double_charge",
        "desc":       "double_charge flag (L148) → SAVE_DONE c-use",
        "group":      1,
        "def_loc":    "agent.py:148 (maybe_inject_double_charge)",
        "use_loc":    "agent.py:155 c-use (SAVE_DONE)",
        "def_clear":  "L148→L155",
        "note":       "BL_DOUBLE_CHARGE: duplicate charge marker injected into save payload; no B3 data",
        "b3_note":    "NOT_MEASURED — no B3 campaign for this payment-specific fault",
    },
    {
        "tc_id":      "TC-PAY-08",
        "fault_mode": "BL_AMOUNT_TAMPERING",
        "checkpoint": "CHARGE_DONE",
        "field":      "units_charged",
        "desc":       "units after tamper (×3) → CHARGE_DONE.units_charged c-use — BL_AMOUNT_TAMPERING",
        "group":      1,
        "def_loc":    "agent.py:98 (after tamper)",
        "use_loc":    "agent.py:132 c-use (CHARGE_DONE)",
        "def_clear":  "L98→L100→L121→L132",
        "note":       "BL_AMOUNT_TAMPERING: same tamper path as FM_2_5 (units*3); confirms BL-level detection",
    },
    {
        "tc_id":      "TC-PAY-09",
        "fault_mode": "BL_CARD_DECLINED",
        "checkpoint": "CARD_VALIDATED",
        "field":      "validation_bypassed",
        "desc":       "force_decline (L118) raises CreditCardError — CARD_VALIDATED path killed",
        "group":      2,
        "def_loc":    "agent.py:118 (maybe_force_decline)",
        "use_loc":    "agent.py:126 p-use (CARD_VALIDATED)",
        "def_clear":  "L118→CreditCardError→L126 unreachable",
        "note":       "BL_CARD_DECLINED: exception before charge_payment; CARD_VALIDATED/CHARGE_DONE/SAVE_DONE absent; no B3 data",
        "b3_note":    "NOT_MEASURED — no B3 campaign for this payment-specific fault",
    },
]

# ---------------------------------------------------------------------------
# Load B1 LKW traces from b2_raw_runs (FAULT_MODE=NONE)
# ---------------------------------------------------------------------------
def load_b1_traces():
    traces = []
    for fname in sorted(glob.glob(str(B2_RAW_DIR / "paymentagent_b2_run*.json"))):
        with open(fname) as f:
            d = json.load(f)
        trace = {}
        for cp in d.get("lkw", []):
            step = cp["step"]
            for field, val in cp.get("data", {}).items():
                trace[f"{step}.{field}"] = val
        traces.append(trace)
    return traces  # list of 3 dicts


# ---------------------------------------------------------------------------
# Load B2 fault results (direct agent.run(), no LLM)
# Returns {fault_mode -> {"CHECKPOINT.field": value, "steps_reached": [...], "steps_lost": [...]}}
# ---------------------------------------------------------------------------
def load_b2():
    with open(B2_TRUE_FILE) as f:
        d = json.load(f)
    result = {}
    for r in d.get("results", []):
        fm = r["fault_mode"]
        trace = {}
        for cp in r.get("lkw", []):
            step = cp["step"]
            for field, val in cp.get("data", {}).items():
                trace[f"{step}.{field}"] = val
        result[fm] = {
            "trace":         trace,
            "steps_reached": r.get("steps_reached", []),
            "steps_lost":    r.get("steps_lost", []),
            "infection":     r.get("infection_point"),
        }
    return result


# ---------------------------------------------------------------------------
# Load B3 observed values for a given fault and field
# Returns list of observed values [run1, run2, run3] or None if no file
# ---------------------------------------------------------------------------
def load_b3_observed(fault_mode, checkpoint, field, temp="1.0"):
    label = temp.replace(".", "")  # "1.0" -> "10" ... not needed; use raw temp string
    values = []
    found = False
    for run in [1, 2, 3]:
        fname = B3_RAW_DIR / f"b3_{fault_mode}_3b_temp{temp}_run{run}.json"
        if not fname.exists():
            values.append("FILE_NOT_FOUND")
            continue
        found = True
        with open(fname) as f:
            d = json.load(f)
        # Check RIP steps for this agent
        pay_rip = d.get("rip", {}).get("payment", {})
        steps_reached = pay_rip.get("steps_reached", [])
        steps_lost = pay_rip.get("steps_lost", [])

        if checkpoint in steps_lost:
            values.append(None)  # PATH_INFEASIBLE
            continue

        # Try mutation per_agent payment deviating_detail
        per_agent = d.get("mutation", {}).get("per_agent", {}).get("payment", {})
        devs = per_agent.get("deviating_detail", [])
        found_field = False
        for dev in devs:
            if dev.get("field") == field:
                values.append(dev.get("observed"))
                found_field = True
                break
        if not found_field:
            # Field not deviating — check TASK_START for input values
            if checkpoint == "TASK_START":
                # Look in lkw traces if available
                pass
            values.append("NOT_MEASURED")

    return values if found else None


# ---------------------------------------------------------------------------
# Compute B1 oracle for a checkpoint.field
# ---------------------------------------------------------------------------
def b1_oracle(traces, checkpoint, field):
    key = f"{checkpoint}.{field}"
    vals = [t.get(key) for t in traces]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if len(set(str(v) for v in vals)) == 1:
        return vals[0]
    return {"values": vals, "consistent": False}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    b1_traces = load_b1_traces()
    b2_data   = load_b2()

    sep = "=" * 90
    print(sep)
    print("  LKW DU-PAIR EVIDENCE TABLE — PaymentAgent")
    print(f"  B1 source : b2_raw_runs/paymentagent_b2_run{{1,2,3}}.json  (fault_mode=NONE, no LLM)")
    print(f"  B2 source : paymentagent/paymentagent_fault_results.json  (direct agent.run(), no LLM)")
    print(f"  B3 source : b3/raw/b3_<FAULT>_3b_temp1.0_run{{1,2,3}}.json  (LLM temp=1.0 + fault)")
    print(sep)

    evidence = []

    for tc in TC_DEFINITIONS:
        tc_id      = tc["tc_id"]
        fault_mode = tc["fault_mode"]
        checkpoint = tc["checkpoint"]
        field      = tc["field"]
        group      = tc["group"]
        note       = tc.get("note", "")

        # B1 oracle
        b1_val  = b1_oracle(b1_traces, checkpoint, field)
        b1_meas = [b1_traces[i].get(f"{checkpoint}.{field}") for i in range(len(b1_traces))]

        # B2 observed
        b2_val     = "N/A"
        b2_verdict = "N/A"
        b2_steps   = []
        b2_lost    = []
        if fault_mode and fault_mode in b2_data:
            b2 = b2_data[fault_mode]
            b2_steps = b2["steps_reached"]
            b2_lost  = b2["steps_lost"]
            if checkpoint in b2_lost:
                b2_val     = None  # PATH_INFEASIBLE
                b2_verdict = "PATH_INFEASIBLE"
            else:
                b2_val = b2["trace"].get(f"{checkpoint}.{field}")
                if b2_val is None and checkpoint not in b2_steps:
                    b2_verdict = "PATH_INFEASIBLE"
                else:
                    # Compare to B1
                    if b2_val == b1_val or b2_val == b1_meas[0]:
                        b2_verdict = "PASS"
                    else:
                        b2_verdict = "FAIL"

        # B3 observed
        b3_vals   = None
        b3_verdict = "NOT_MEASURED"
        b3_note    = tc.get("b3_note", "")
        if fault_mode and not b3_note:
            b3_vals = load_b3_observed(fault_mode, checkpoint, field)
            if b3_vals is not None:
                non_none = [v for v in b3_vals if v is not None and v != "NOT_MEASURED" and v != "FILE_NOT_FOUND"]
                if all(v is None for v in b3_vals):
                    b3_verdict = "PATH_INFEASIBLE"
                elif any(v != b1_val and v is not None and v != "NOT_MEASURED" for v in b3_vals):
                    b3_verdict = "FAIL"
                elif all(v == b1_val for v in b3_vals if v is not None):
                    b3_verdict = "PASS"
                else:
                    b3_verdict = "PARTIAL_FAIL"

        # LKW verdict
        if group == "info":
            lkw_verdict = "PASS"
        elif group == 2:
            lkw_verdict = "PATH_INFEASIBLE"
        elif b2_verdict == "FAIL":
            lkw_verdict = "FAIL in B2" + (" and B3" if b3_verdict == "FAIL" else "")
        elif b2_verdict == "PATH_INFEASIBLE":
            lkw_verdict = "PATH_INFEASIBLE"
        else:
            lkw_verdict = b2_verdict

        # Print
        grp_label = "Group info" if group == "info" else f"Group {group}"
        print(f"\n{tc_id}  [{grp_label}]  fault={fault_mode or 'N/A'}  field={checkpoint}.{field}")
        print(f"  B1 oracle  : {b1_val}")
        print(f"  B1 measured: {b1_meas}  (from b2_raw_runs LKW trace)")
        if fault_mode:
            print(f"  B2 true    : observed={b2_val}  verdict={b2_verdict}  (no LLM, direct agent.run())")
            if b3_note:
                print(f"  B3         : {b3_note}")
            elif b3_vals is not None:
                print(f"  B3 temp1.0 : observed={b3_vals}  verdict={b3_verdict}")
        print(f"  Note: {note}")

        # Build evidence record
        rec = {
            "tc_id":           tc_id,
            "group":           group,
            "fault_mode":      fault_mode,
            "def_loc":         tc.get("def_loc"),
            "use_loc":         tc.get("use_loc"),
            "def_clear_path":  tc.get("def_clear"),
            "checkpoint_field": f"{checkpoint}.{field}",
            "b1_oracle":       b1_val,
            "b1_observed":     b1_meas,
            "b2_observed":     b2_val,
            "b2_verdict":      b2_verdict,
            "b3_observed":     b3_vals,
            "b3_verdict":      b3_verdict,
            "b3_note":         b3_note or None,
            "lkw_verdict":     lkw_verdict,
            "note":            note,
            "source_b1":       [f"paymentagent_b2_run{i}.json" for i in range(1, 4)],
            "source_b2":       "paymentagent_fault_results.json",
            "source_b3":       (
                [f"b3_{fault_mode}_3b_temp1.0_run{i}.json" for i in range(1, 4)]
                if fault_mode and not b3_note else None
            ),
        }
        evidence.append(rec)

    # Group summary
    print("\n" + sep)
    g1 = [r["tc_id"] for r in evidence if r["group"] == 1]
    g2 = [r["tc_id"] for r in evidence if r["group"] == 2]
    print(f"GROUP SUMMARY")
    print("-" * 50)
    print(f"Group 1 (LKW catches via wrong value at use point) : {g1}")
    print(f"Group 2 (checkpoint-only detection, path killed)   : {g2}")
    for r in evidence:
        if r["group"] == 2:
            print(f"  {r['tc_id']} ({r['fault_mode']}) — B2 observed={r['b2_observed']}  B3 observed={r['b3_observed']}")
            print(f"    => Use point never reached; checkpoint detects missing {r['checkpoint_field'].split('.')[0]} step")
    print(sep)

    # Save
    out_path = RESULTS_DIR / "lkw_payment_evidence.json"
    with open(out_path, "w") as f:
        json.dump({"agent": "paymentagent", "evidence": evidence}, f, indent=2)
    print(f"\nEvidence written to: {out_path}")


if __name__ == "__main__":
    main()
