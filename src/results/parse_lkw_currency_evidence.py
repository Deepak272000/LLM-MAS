"""
parse_lkw_currency_evidence.py
================================
Reads real execution artifacts and maps them to the TC-CUR-XX DU-pair test cases
defined in STAGE1_LKW_CURRENCY_TEST_CASES.md.

Data sources used:
  B1 (clean baseline, no fault, real LKW trace):
      b2_raw_runs/currencyagent_b2_run{1,2,3}.json

  B3-deterministic (greedy LLM temp=0, with fault injection, comparison results):
      b3/raw/b3_<FAULT>_3b_temp0_run{1,2,3}.json

  B3-nondeterministic (LLM temp=1.0, with fault injection, comparison results):
      b3/raw/b3_<FAULT>_3b_temp1.0_run{1,2,3}.json

  B1 oracle (consensus from 3 B1 runs):
      b1_oracle_values.json

Run: python parse_lkw_currency_evidence.py
Outputs: lkw_currency_evidence.json and a printed table
"""

import json
import os
import glob
from pathlib import Path

RESULTS_DIR    = Path(__file__).parent
B2_RAW_DIR     = RESULTS_DIR / "b2_raw_runs"
B3_RAW_DIR     = RESULTS_DIR / "b3" / "raw"
B2_TRUE_FILE   = RESULTS_DIR / "b2_currency_true_b2.json"

# ---------------------------------------------------------------------------
# DU-pair test case definitions
# Each entry maps a TC-ID to:
#   fault_mode   : the fault that makes this TC interesting
#   agent        : "currency"
#   checkpoint   : which LKW checkpoint holds the use-point value
#   field        : the field name inside the checkpoint's data dict
#   b1_key       : the key in b1_oracle_values.json (None if not present)
#   desc         : short human-readable description
#   group        : 1 = LKW catches, 2 = checkpoint-only detection
# ---------------------------------------------------------------------------
TC_DEFINITIONS = [
    {
        "tc_id": "TC-CUR-01",
        "fault_mode": None,
        "agent": "currency",
        "checkpoint": "TASK_START",
        "field": "units",
        "b1_key": "currencyagent.TASK_START.units",
        "desc": "units D1→c-use at TASK_START",
        "group": "info",
        "note": "fault fires after this use; always equals input",
    },
    {
        "tc_id": "TC-CUR-03",
        "fault_mode": "FM_2_5",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "units_in",
        "b1_key": None,  # units_in not in oracle comparison — read B1 LKW trace directly
        "desc": "units D2→c-use in gRPC call (via units_in in CONVERT_DONE)",
        "group": 1,
        "note": "FM_2_5 tamper: units*5 before client.convert(); not tracked in oracle comparison",
    },
    {
        "tc_id": "TC-CUR-04",
        "fault_mode": "FM_2_5",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "units_in",
        "b1_key": None,
        "desc": "units D2→c-use at CONVERT_DONE[units_in]",
        "group": 1,
        "note": "Same field as TC-CUR-03; confirms tampered value persists to checkpoint",
    },
    {
        "tc_id": "TC-CUR-05",
        "fault_mode": None,
        "agent": "currency",
        "checkpoint": "TASK_START",
        "field": "to_currency",
        "b1_key": "currencyagent.TASK_START.to_currency",
        "desc": "to_currency D1→c-use at TASK_START",
        "group": "info",
        "note": "fault fires after this use; always equals input",
    },
    {
        "tc_id": "TC-CUR-07",
        "fault_mode": "FM_1_2",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "to_currency",
        "b1_key": "currencyagent.TASK_START.to_currency",  # oracle uses TASK_START value
        "desc": "to_currency D2→c-use in gRPC call (via to_currency in CONVERT_DONE)",
        "group": 1,
        "note": "FM_1_2 swap: to_currency→JPY/BRL before client.convert(); comparison at TASK_START level",
    },
    {
        "tc_id": "TC-CUR-08",
        "fault_mode": "FM_1_2",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "to_currency",
        "b1_key": "currencyagent.TASK_START.to_currency",
        "desc": "to_currency D2→c-use at CONVERT_DONE",
        "group": 1,
        "note": "Redundant with TC-CUR-07; confirms swapped currency propagates to checkpoint",
    },
    {
        "tc_id": "TC-CUR-09",
        "fault_mode": "FM_3_1",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "units_out",
        "b1_key": "currencyagent.CONVERT_DONE.units_out",
        "desc": "data[units] D1→c-use at CONVERT_DONE[units_out] — Group 2 (path killed by FM_3_1)",
        "group": 2,
        "note": "FM_3_1 early return: client.convert() never called; CONVERT_DONE absent; units_out=None",
    },
    {
        "tc_id": "TC-CUR-10",
        "fault_mode": "FM_2_2",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "units_out",
        "b1_key": "currencyagent.CONVERT_DONE.units_out",
        "desc": "data[units] D2→c-use at CONVERT_DONE[units_out] — FM_2_2 hallucination",
        "group": 1,
        "note": "FM_2_2 hallucinate: units_out hardcoded to 1337",
    },
    {
        "tc_id": "TC-CUR-11",
        "fault_mode": "BL_RATE_MANIPULATION",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "units_out",
        "b1_key": "currencyagent.CONVERT_DONE.units_out",
        "desc": "data[units] D3→c-use at CONVERT_DONE[units_out] — BL_RATE_MANIPULATION",
        "group": 1,
        "note": "BL_RATE_MANIPULATION: units_out inflated 10x; no temp0 runs available; temp0.7 used",
        "alt_temp": "temp0.7",
    },
    {
        "tc_id": "TC-CUR-12",
        "fault_mode": "FM_1_2",
        "agent": "currency",
        "checkpoint": "CONVERT_DONE",
        "field": "currency_swapped",
        "b1_key": "currencyagent.CONVERT_DONE.currency_swapped",
        "desc": "currency_swapped flag D1→p-use in rip_summary",
        "group": 1,
        "note": "FM_1_2: currency_swapped=True when swap fires",
    },
]

# ---------------------------------------------------------------------------
# Load true B2 results (no LLM, direct agent.run(), mocked gRPC)
# Returns {fault_mode -> {"CHECKPOINT.field": value}}
# ---------------------------------------------------------------------------
def load_b2_true():
    if not B2_TRUE_FILE.exists():
        return {}
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
        result[fm] = {"trace": trace, "steps_reached": r.get("steps_reached", [])}
    return result


# ---------------------------------------------------------------------------
# Load B1 oracle
# ---------------------------------------------------------------------------
def load_b1_oracle():
    path = RESULTS_DIR / "b1_oracle_values.json"
    with open(path) as f:
        data = json.load(f)
    return data["oracle"]


# ---------------------------------------------------------------------------
# Read B1 LKW traces directly from b2_raw_runs (fault_mode=NONE)
# Returns dict: {field_path -> [v1, v2, v3]} where field_path = "CHECKPOINT.field"
# ---------------------------------------------------------------------------
def load_b1_lkw_traces():
    traces = []
    for fname in sorted(glob.glob(str(B2_RAW_DIR / "currencyagent_b2_run*.json"))):
        with open(fname) as f:
            d = json.load(f)
        trace = {}
        for step in d.get("lkw", []):
            checkpoint = step["step"]
            for field, value in step.get("data", {}).items():
                trace[f"{checkpoint}.{field}"] = value
        traces.append({"file": os.path.basename(fname), "trace": trace})
    return traces


# ---------------------------------------------------------------------------
# Read B3 raw run comparison data for a given fault and temperature label
# Returns list of per-run dicts: {field -> {b1_value, observed, severity, source_file}}
# ---------------------------------------------------------------------------
def load_b3_currency_observations(fault_mode: str, temp_label: str):
    pattern = str(B3_RAW_DIR / f"b3_{fault_mode}_3b_{temp_label}_run*.json")
    results = []
    for fname in sorted(glob.glob(pattern)):
        with open(fname) as f:
            d = json.load(f)
        cur = d.get("mutation", {}).get("per_agent", {}).get("currency", {})
        details = cur.get("deviating_detail", [])
        field_map = {}
        for item in details:
            field_map[item["field"]] = {
                "b1_value": item.get("b1_value"),
                "observed":  item.get("observed"),
                "severity":  item.get("severity"),
            }
        # Also note steps_reached for CONVERT_DONE presence check
        steps_reached = d.get("steps_per_agent", {}).get("currency", [])
        results.append({
            "source_file": os.path.basename(fname),
            "fields": field_map,
            "steps_reached": steps_reached,
            "status": d.get("status"),
        })
    return results


# ---------------------------------------------------------------------------
# Check if a specific fault+field was detectable in B3 runs
# Returns summary: {observed_values, verdict, all_steps_reached}
# ---------------------------------------------------------------------------
def summarize_b3_field(fault_mode: str, field: str, b1_oracle_val, temp_label="temp0"):
    runs = load_b3_currency_observations(fault_mode, temp_label)
    if not runs:
        return {"runs": 0, "observed": [], "overall_verdict": "NO_RUNS", "verdict_per_run": [], "steps_reached_per_run": []}

    observed_list = []
    verdicts = []
    steps_all = []

    for run in runs:
        steps_all.append(run["steps_reached"])
        convert_done_reached = "CONVERT_DONE" in run["steps_reached"]

        if field in run["fields"]:
            obs = run["fields"][field]["observed"]
            sev = run["fields"][field]["severity"]
            observed_list.append(obs)
            if sev == "missing" or obs is None:
                verdicts.append("PATH_INFEASIBLE")
            else:
                # Compare observed to b1_oracle
                if isinstance(b1_oracle_val, dict):
                    # numeric range oracle: check if observed is within range
                    mn = b1_oracle_val.get("min", b1_oracle_val.get("mean"))
                    mx = b1_oracle_val.get("max", b1_oracle_val.get("mean"))
                    if obs is not None and mn is not None and abs(float(obs) - float(mn)) < 0.5:
                        verdicts.append("PASS")
                    else:
                        verdicts.append("FAIL")
                else:
                    verdicts.append("PASS" if obs == b1_oracle_val else "FAIL")
        else:
            # Field not in deviating_detail — check if CONVERT_DONE reached
            if not convert_done_reached:
                observed_list.append(None)
                verdicts.append("PATH_INFEASIBLE")
            elif b1_oracle_val == "see_b1_trace":
                # Field exists in LKW trace but was not included in oracle comparison
                observed_list.append("NOT_IN_ORACLE_COMPARISON")
                verdicts.append("NOT_MEASURED")
            else:
                observed_list.append("(matches_b1_not_deviating)")
                verdicts.append("PASS")

    return {
        "runs": len(runs),
        "observed": observed_list,
        "verdict_per_run": verdicts,
        "overall_verdict": (
            "FAIL" if "FAIL" in verdicts
            else "PATH_INFEASIBLE" if "PATH_INFEASIBLE" in verdicts
            else "PASS"
        ),
        "steps_reached_per_run": steps_all,
    }


# ---------------------------------------------------------------------------
# Main: build evidence table
# ---------------------------------------------------------------------------
def main():
    oracle = load_b1_oracle()
    b1_traces = load_b1_lkw_traces()

    # Build B1 observed values directly from LKW traces
    b1_direct = {}
    for t in b1_traces:
        for fpath, val in t["trace"].items():
            if fpath not in b1_direct:
                b1_direct[fpath] = []
            b1_direct[fpath].append(val)

    b2_true = load_b2_true()

    print("=" * 90)
    print("LKW DU-PAIR EVIDENCE TABLE — CurrencyAgent")
    print("B1 source : b2_raw_runs/currencyagent_b2_run{1,2,3}.json  (fault_mode=NONE, no LLM)")
    print("B2 source : b2_currency_true_b2.json  (direct agent.run(), no LLM, mocked gRPC)")
    print("B3 source : b3/raw/b3_<FAULT>_3b_temp1.0_run{1,2,3}.json  (LLM temp=1.0 + fault)")
    print("=" * 90)

    rows = []

    for tc in TC_DEFINITIONS:
        tc_id      = tc["tc_id"]
        fault      = tc["fault_mode"]
        ckpt       = tc["checkpoint"]
        field      = tc["field"]
        b1_key_oc  = tc["b1_key"]
        group      = tc["group"]
        desc       = tc["desc"]
        note       = tc["note"]

        # --- B1 oracle value ---
        if b1_key_oc and b1_key_oc in oracle:
            b1_oracle_val = oracle[b1_key_oc]["oracle_value"]
        else:
            b1_oracle_val = "see_b1_trace"

        # --- B1 directly observed values from LKW trace ---
        direct_key = f"{ckpt}.{field}"
        b1_observed = b1_direct.get(direct_key, ["not_in_trace"])

        # --- B2 true observed (no LLM, direct call) ---
        if fault and fault in b2_true:
            b2_entry = b2_true[fault]
            direct_key = f"{ckpt}.{field}"
            b2_steps = b2_entry["steps_reached"]
            if direct_key in b2_entry["trace"]:
                raw_b2_val = b2_entry["trace"][direct_key]
                if isinstance(b1_oracle_val, dict):
                    mn = b1_oracle_val.get("min", b1_oracle_val.get("mean"))
                    b2_verdict = "PASS" if raw_b2_val is not None and abs(float(raw_b2_val) - float(mn)) < 0.5 else "FAIL"
                elif b1_oracle_val == "see_b1_trace":
                    b1_ref = b1_direct.get(direct_key, [None])[0]
                    b2_verdict = "PASS" if raw_b2_val == b1_ref else "FAIL"
                else:
                    b2_verdict = "PASS" if raw_b2_val == b1_oracle_val else "FAIL"
            elif ckpt not in b2_steps:
                raw_b2_val = None
                b2_verdict = "PATH_INFEASIBLE"
            else:
                raw_b2_val = "(matches_b1)"
                b2_verdict = "PASS"
        else:
            raw_b2_val = "N/A"
            b2_verdict = "N/A"

        # --- B3 observed ---
        if fault:
            b3_summary = summarize_b3_field(fault, field, b1_oracle_val, temp_label="temp1.0")
        else:
            b3_summary = {"runs": 0, "observed": ["N/A"], "overall_verdict": "N/A", "steps_reached_per_run": []}

        row = {
            "tc_id":              tc_id,
            "group":              group,
            "fault_mode":         fault or "N/A",
            "checkpoint_field":   f"{ckpt}.{field}",
            "b1_oracle":          b1_oracle_val,
            "b1_observed":        b1_observed,
            "b2_true_observed":   raw_b2_val,
            "b2_true_verdict":    b2_verdict,
            "b3_temp1.0_observed": b3_summary["observed"],
            "b3_temp1.0_verdict": b3_summary["overall_verdict"],
            "note": note,
            "source_b1":  [t["file"] for t in b1_traces],
            "source_b2":  "b2_currency_true_b2.json",
            "source_b3":  [f"b3_{fault}_3b_temp1.0_run{i}.json" for i in [1,2,3]] if fault else [],
        }
        rows.append(row)

        # Print row
        print(f"\n{tc_id}  [Group {group}]  fault={fault or 'N/A'}  field={ckpt}.{field}")
        print(f"  B1 oracle  : {b1_oracle_val}")
        print(f"  B1 measured: {b1_observed}  (from b2_raw_runs LKW trace)")
        if fault:
            print(f"  B2 true    : observed={raw_b2_val}  verdict={b2_verdict}  (no LLM, mocked gRPC)")
            print(f"  B3 temp1.0 : observed={b3_summary['observed']}  verdict={b3_summary['overall_verdict']}")
        print(f"  Note: {note}")

    # --- Save JSON evidence file ---
    out_path = RESULTS_DIR / "lkw_currency_evidence.json"
    out = {
        "generated_by": "parse_lkw_currency_evidence.py",
        "b1_source":    "b2_raw_runs/currencyagent_b2_run{1,2,3}.json",
        "b2_source":    "b2_currency_true_b2.json",
        "b3_source":    "b3/raw/b3_<FAULT>_3b_temp1.0_run{1,2,3}.json",
        "tc_rows": rows,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n" + "=" * 90)
    print(f"Evidence written to: {out_path}")
    print("\nGROUP SUMMARY")
    print("-" * 50)
    group2 = [r for r in rows if r["group"] == 2]
    group1 = [r for r in rows if r["group"] == 1]
    print(f"Group 1 (LKW catches via wrong value at use point) : {[r['tc_id'] for r in group1]}")
    print(f"Group 2 (checkpoint-only detection, path killed)   : {[r['tc_id'] for r in group2]}")
    for r in group2:
        print(f"  {r['tc_id']} ({r['fault_mode']}) — B2 true observed={r['b2_true_observed']}  B3 temp1.0 observed={r['b3_temp1.0_observed']}")
        print(f"    => Use point never reached; checkpoint detects missing CONVERT_DONE step")


if __name__ == "__main__":
    main()
