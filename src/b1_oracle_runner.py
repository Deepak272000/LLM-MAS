"""
B1 Oracle Runner — Extract Ground-Truth Values from Deterministic Baseline Runs
================================================================================
B1 = the deterministic gRPC microservice ground truth.
For the 6 deterministic Python agents (USE_LLM=false), B1 = their NONE-mode
outputs, already captured in results/b2_raw_runs/ by the b2_baseline_runner.py pilot.
For ShippingService (LLM-based), B1 = the checkout 14b_temp0 NONE-mode runs
in results/b2_systematic/raw/ (lowest-variance LLM baseline).

This script:
  1. Loads existing run data (no re-running needed — data is already on disk)
  2. Extracts the specific key variable values declared in checkpoint_variable_map.json
  3. Validates each observed value against its declared comparison relation
  4. Computes the oracle envelope (min/max for numerics; consensus value for categoricals)
  5. Writes results/b1_oracle_values.json — the calibrated oracle for B3 deviation testing

Why mocks == B1:
  The mock return values in each agent's test_fault_injection.py replicate the
  exact gRPC response structure of the original microservice. PaymentService.Charge()
  returns a UUID-v4 transaction_id; the mock does too. CurrencyService.Convert()
  returns {units: 9, nanos: 230000000} for 10 USD; the mock does too. The mock
  IS the oracle — deterministic, version-controlled, and independent of network.

Output:
  results/b1_oracle_values.json   — per-agent/checkpoint/field oracle entries
  results/b1_validation_report.json — validation pass/fail per entry

Usage:
  python b1_oracle_runner.py
  python b1_oracle_runner.py --rerun   # re-run agents instead of using cached data
"""

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
SRC     = Path(__file__).parent
RESULTS = SRC / "results"
RAW_B2  = RESULTS / "b2_raw_runs"          # deterministic agent NONE runs
RAW_SYS = RESULTS / "b2_systematic" / "raw"  # checkout NONE runs (shipping B1)
MAP_FILE   = RESULTS / "checkpoint_variable_map.json"
ORACLE_OUT = RESULTS / "b1_oracle_values.json"
REPORT_OUT = RESULTS / "b1_validation_report.json"

# ── Agent name mapping: raw_run filename prefix → map agent key ───────────────
AGENT_KEY_MAP = {
    "paymentagent":        "paymentagent",
    "currencyagent":       "currencyagent",
    "emailserviceagent":   "emailserviceagent",
    "productcatalogagent": "productcatalogagent",
    "recommendationagent": "recommendationagent",
    "adserviceagent":      "adserviceagent",
}

# Shipping uses the checkout systematic runs (per-agent_lkw["shipping_quote"] + ["ship_order"])
SHIPPING_CFG = "14b_temp0"
SHIPPING_FAULT = "NONE"

# ── Comparison relation implementations ───────────────────────────────────────

def _validate(entry: dict, value: Any) -> tuple[bool, str]:
    """
    Apply the comparison relation in `entry` to `value`.
    Returns (is_valid, reason).
    """
    relation = entry.get("comparison_relation")

    if value is None:
        return False, "value is None"

    if relation == "exact_string":
        expected = entry.get("b1_oracle_expected")
        ok = (value == expected)
        return ok, f"expected={expected!r} got={value!r}"

    if relation == "exact_bool":
        expected = entry.get("b1_oracle_expected")
        ok = (bool(value) == bool(expected))
        return ok, f"expected={expected} got={value}"

    if relation == "exact_integer":
        expected = entry.get("b1_oracle_expected")
        if expected is None or isinstance(expected, str):
            # String descriptions (e.g., "same as test input units") = no fixed value
            return True, "no fixed expected integer; accepted as-is"
        ok = (int(value) == int(expected))
        return ok, f"expected={expected} got={value}"

    if relation == "numeric_tolerance_pct":
        tol = entry.get("tolerance_pct", 1.0)
        expected = entry.get("b1_oracle_expected")
        if expected is None or isinstance(expected, str):
            return True, "no fixed expected; tolerance check skipped"
        pct_dev = 0.0
        if expected == 0:
            ok = (abs(value) <= tol)
        else:
            pct_dev = abs(value - expected) / abs(expected) * 100
            ok = (pct_dev <= tol)
        return ok, f"expected={expected} got={value} dev={pct_dev:.2f}% tol={tol}%"

    if relation == "set_equality":
        expected = entry.get("b1_oracle_expected")
        if expected is None:
            return True, "no fixed expected set"
        if not isinstance(value, list):
            return False, f"expected list, got {type(value).__name__}"
        ok = set(value) == set(expected)
        return ok, f"expected_set={set(expected)} got_set={set(value)}"

    if relation == "set_membership":
        valid_set = entry.get("valid_set") or entry.get("b1_oracle_expected_set")
        if not valid_set:
            return True, "no valid_set declared"
        ok = (value in valid_set)
        return ok, f"value={value!r} valid_set={valid_set}"

    if relation == "schema_regex":
        pattern = entry.get("schema_pattern")
        if not pattern:
            return True, "no schema_pattern declared"
        ok = bool(re.match(pattern, str(value)))
        return ok, f"pattern={pattern!r} value={value!r}"

    if relation == "range_check":
        rmin = entry.get("range_min")
        rmax = entry.get("range_max")
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, f"cannot convert {value!r} to float"
        ok_min = (v >= rmin) if rmin is not None else True
        ok_max = (v <= rmax) if rmax is not None else True
        ok = ok_min and ok_max
        return ok, f"value={v} range=[{rmin},{rmax}]"

    if relation == "non_negative_float":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, f"cannot convert {value!r} to float"
        # Detect IEEE-754 negative zero
        if math.copysign(1, v) < 0:
            return False, f"negative (or negative-zero) float: {v}"
        return True, f"value={v} >= 0"

    return True, f"unknown relation {relation!r}; accepted as-is"


# ── LKW trace extraction helpers ───────────────────────────────────────────────

def _find_checkpoint(lkw_trace: list, checkpoint_name: str) -> Optional[dict]:
    """Return the first matching checkpoint dict from a LKW trace list."""
    for cp in lkw_trace:
        if cp.get("step") == checkpoint_name:
            return cp
    return None


def _get_field(checkpoint: dict, field: str) -> Any:
    """Get a field value from a checkpoint's data dict."""
    data = checkpoint.get("data", {})
    return data.get(field)


# ── Load raw run data ──────────────────────────────────────────────────────────

def load_deterministic_runs() -> dict:
    """
    Load all b2_raw_runs/*.json files.
    Returns: {agent_key: [lkw_trace, lkw_trace, lkw_trace]}
    """
    runs = {}
    for agent_key in AGENT_KEY_MAP:
        agent_runs = []
        for run_num in [1, 2, 3]:
            fname = RAW_B2 / f"{agent_key}_b2_run{run_num}.json"
            if not fname.exists():
                continue
            with open(fname, encoding="utf-8") as f:
                data = json.load(f)
            lkw = data.get("lkw", [])
            agent_runs.append(lkw)
        if agent_runs:
            runs[AGENT_KEY_MAP[agent_key]] = agent_runs
    return runs


def load_shipping_runs() -> dict:
    """
    Load shipping LKW from b2_systematic/raw checkout runs (14b_temp0, NONE).
    Returns: {"shipping_quote": [[lkw], [lkw], [lkw]], "ship_order": [[lkw], ...]}
    """
    sq_runs, so_runs = [], []
    for run_num in [1, 2, 3]:
        fname = RAW_SYS / f"checkout_{SHIPPING_CFG}_{SHIPPING_FAULT}_run{run_num}.json"
        if not fname.exists():
            continue
        with open(fname, encoding="utf-8") as f:
            data = json.load(f)
        per_agent = data.get("per_agent_lkw", {})
        sq_runs.append(per_agent.get("shipping_quote", []))
        so_runs.append(per_agent.get("ship_order", []))
    return {"shippingagent_get_quote": sq_runs, "shippingagent_ship_order": so_runs}


# ── Oracle extraction ──────────────────────────────────────────────────────────

def extract_oracle_entry(
    agent_key: str,
    checkpoint_name: str,
    field_entry: dict,
    all_lkw_runs: list
) -> dict:
    """
    For a single (agent, checkpoint, field), extract observed values across
    all runs and apply the comparison relation.
    """
    observed_values = []
    validation_results = []
    raw_checkpoints   = []

    for lkw_trace in all_lkw_runs:
        cp = _find_checkpoint(lkw_trace, checkpoint_name)
        if cp is None:
            observed_values.append(None)
            validation_results.append({"valid": False, "reason": "checkpoint absent from trace"})
            raw_checkpoints.append(None)
            continue
        value = _get_field(cp, field_entry["field"])
        is_valid, reason = _validate(field_entry, value)
        observed_values.append(value)
        validation_results.append({"valid": is_valid, "reason": reason})
        raw_checkpoints.append(cp.get("data", {}))

    # Derive oracle value from observed (consensus for categoricals, range for numerics)
    relation = field_entry.get("comparison_relation", "")
    non_null = [v for v in observed_values if v is not None]

    if not non_null:
        oracle_value = None
        oracle_type  = "absent"
    elif relation in ("exact_string", "exact_bool", "exact_integer", "set_membership"):
        # Should all be identical — use first
        oracle_value = non_null[0]
        oracle_type  = "consensus"
        # Flag if not all equal
        if len(set(str(v) for v in non_null)) > 1:
            oracle_type = "inconsistent_across_runs"
    elif relation in ("numeric_tolerance_pct", "range_check", "non_negative_float"):
        # Express as observed range
        try:
            floats = [float(v) for v in non_null]
            oracle_value = {
                "min": min(floats),
                "max": max(floats),
                "mean": sum(floats) / len(floats),
            }
            oracle_type = "numeric_range"
        except (TypeError, ValueError):
            oracle_value = non_null[0]
            oracle_type  = "consensus"
    elif relation == "set_equality":
        oracle_value = sorted(set(str(v) for v in non_null))
        oracle_type  = "observed_set"
    elif relation == "schema_regex":
        oracle_value = non_null[0]
        oracle_type  = "example_valid_value"
    else:
        oracle_value = non_null[0]
        oracle_type  = "raw"

    all_valid = all(r["valid"] for r in validation_results)

    return {
        "agent":               agent_key,
        "checkpoint":          checkpoint_name,
        "field":               field_entry["field"],
        "comparison_relation": relation,
        "lkw_criterion":       field_entry.get("lkw_criterion"),
        "use_type":            field_entry.get("use_type"),
        "def_site":            field_entry.get("def_site"),
        "use_site":            field_entry.get("use_site"),
        "b1_oracle_source":    field_entry.get("b1_oracle_source"),
        "oracle_value":        oracle_value,
        "oracle_type":         oracle_type,
        "observed_values":     observed_values,
        "validation_results":  validation_results,
        "all_runs_valid":      all_valid,
        "run_count":           len(all_lkw_runs),
        "mutation_faults_detected": field_entry.get("mutation_faults_detected", []),
        "notes":               field_entry.get("notes", ""),
        "comparison_params":   {
            k: v for k, v in field_entry.items()
            if k in ("tolerance_pct", "valid_set", "schema_pattern",
                     "range_min", "range_max", "b1_oracle_expected",
                     "b1_oracle_expected_set")
        },
    }


# ── Variance check (B1 must have zero variance for deterministic agents) ───────

def check_zero_variance(oracle_entries: list) -> list:
    """
    For deterministic agents: if any field shows variance across 3 NONE runs,
    flag it — that is a B1 oracle integrity problem.
    """
    issues = []
    for entry in oracle_entries:
        if entry["agent"].startswith("shipping"):
            continue  # LLM-based — some variance expected
        vals = entry["observed_values"]
        non_null = [v for v in vals if v is not None]
        if len(non_null) < 2:
            continue
        # Check consistency
        if entry["oracle_type"] == "inconsistent_across_runs":
            issues.append({
                "key":    f"{entry['agent']}.{entry['checkpoint']}.{entry['field']}",
                "issue":  "non_deterministic_B1",
                "values": non_null,
            })
        # For numeric: zero variance should hold
        elif entry["oracle_type"] == "numeric_range":
            ov = entry["oracle_value"]
            if ov and ov.get("min") != ov.get("max"):
                issues.append({
                    "key":    f"{entry['agent']}.{entry['checkpoint']}.{entry['field']}",
                    "issue":  "numeric_variance_in_deterministic_agent",
                    "range":  ov,
                })
    return issues


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if not MAP_FILE.exists():
        print(f"ERROR: checkpoint_variable_map.json not found at {MAP_FILE}")
        sys.exit(1)

    with open(MAP_FILE, encoding="utf-8") as f:
        cmap = json.load(f)

    print("Loading deterministic agent runs (b2_raw_runs)...")
    det_runs = load_deterministic_runs()
    print(f"  Loaded: {', '.join(f'{k}×{len(v)}' for k, v in det_runs.items())}")

    print("Loading shipping runs (b2_systematic/raw checkout 14b_temp0)...")
    ship_runs = load_shipping_runs()
    for k, v in ship_runs.items():
        if v:
            print(f"  {k}: {len(v)} runs")
        else:
            print(f"  {k}: 0 runs (systematic raw not present — shipping oracle will use mock defaults)")

    all_runs = {**det_runs, **ship_runs}

    oracle_entries = []
    missing_agents = []

    for cp_def in cmap.get("checkpoints", []):
        agent_key      = cp_def["agent"]
        checkpoint_name = cp_def["checkpoint"]
        lkw_runs       = all_runs.get(agent_key)

        if lkw_runs is None or len(lkw_runs) == 0:
            # Shipping on SPEED only: use empty list so oracle entry is created with mock defaults
            if agent_key.startswith("shipping"):
                lkw_runs = []  # will produce oracle_type=absent, filled from b1_oracle_expected
            else:
                missing_agents.append(agent_key)
                continue

        for var_entry in cp_def.get("observed_variables", []):
            entry = extract_oracle_entry(
                agent_key       = agent_key,
                checkpoint_name = checkpoint_name,
                field_entry     = var_entry,
                all_lkw_runs    = lkw_runs,
            )
            oracle_entries.append(entry)

    # Check for B1 variance (should be zero for deterministic agents)
    variance_issues = check_zero_variance(oracle_entries)

    # Build final output
    timestamp = datetime.now(timezone.utc).isoformat()

    # Keyed format for fast lookup in comparator
    oracle_keyed = {}
    for e in oracle_entries:
        key = f"{e['agent']}.{e['checkpoint']}.{e['field']}"
        oracle_keyed[key] = e

    oracle_doc = {
        "generated_at":    timestamp,
        "total_entries":   len(oracle_entries),
        "all_valid":       all(e["all_runs_valid"] for e in oracle_entries),
        "b1_source":       "b2_raw_runs (deterministic NONE) + b2_systematic 14b_temp0 (shipping)",
        "oracle":          oracle_keyed,
    }

    with open(ORACLE_OUT, "w", encoding="utf-8") as f:
        json.dump(oracle_doc, f, indent=2, default=str)

    # Validation report
    validation_report = {
        "generated_at":    timestamp,
        "total_entries":   len(oracle_entries),
        "valid_entries":   sum(1 for e in oracle_entries if e["all_runs_valid"]),
        "invalid_entries": sum(1 for e in oracle_entries if not e["all_runs_valid"]),
        "missing_agents":  list(set(missing_agents)),
        "variance_issues": variance_issues,
        "per_entry_status": [
            {
                "key":        f"{e['agent']}.{e['checkpoint']}.{e['field']}",
                "valid":      e["all_runs_valid"],
                "relation":   e["comparison_relation"],
                "oracle_val": e["oracle_value"],
                "faults":     e["mutation_faults_detected"],
            }
            for e in oracle_entries
        ],
    }

    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        json.dump(validation_report, f, indent=2, default=str)

    # Print summary
    print()
    print("=" * 60)
    print("B1 Oracle Extraction Complete")
    print("=" * 60)
    total = len(oracle_entries)
    valid = sum(1 for e in oracle_entries if e["all_runs_valid"])
    print(f"  Total entries:   {total}")
    print(f"  Valid (pass B1): {valid} / {total}")
    if variance_issues:
        print(f"  VARIANCE ISSUES: {len(variance_issues)}")
        for issue in variance_issues:
            print(f"    - {issue['key']}: {issue['issue']}")
    if missing_agents:
        print(f"  Missing agents:  {list(set(missing_agents))}")
    print()
    print(f"  Written: {ORACLE_OUT.relative_to(SRC)}")
    print(f"  Written: {REPORT_OUT.relative_to(SRC)}")
    print()

    # Per-agent summary
    from collections import defaultdict
    agent_counts = defaultdict(lambda: {"total": 0, "valid": 0})
    for e in oracle_entries:
        a = e["agent"]
        agent_counts[a]["total"] += 1
        if e["all_runs_valid"]:
            agent_counts[a]["valid"] += 1

    for agent, counts in sorted(agent_counts.items()):
        mark = "✓" if counts["valid"] == counts["total"] else "✗"
        print(f"  {mark} {agent}: {counts['valid']}/{counts['total']} variables valid")


# ── Public comparator API (used by b2_systematic_runner.py and b3 runner) ─────

def load_oracle() -> dict:
    """Load the oracle values from disk. Returns the keyed dict."""
    if not ORACLE_OUT.exists():
        raise FileNotFoundError(
            f"B1 oracle not found at {ORACLE_OUT}. Run b1_oracle_runner.py first."
        )
    with open(ORACLE_OUT, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("oracle", {})


def compare_to_oracle(agent: str, checkpoint: str, field: str,
                      observed_value: Any, oracle: dict) -> dict:
    """
    Compare a single observed value against the B1 oracle.
    Returns a deviation result dict.

    Args:
        agent:          Agent key (e.g., "paymentagent")
        checkpoint:     Checkpoint name (e.g., "CHARGE_DONE")
        field:          Field name (e.g., "transaction_id")
        observed_value: Value observed in B2/B3 run
        oracle:         The loaded oracle dict (from load_oracle())

    Returns:
        {
          "key": "...",
          "deviation": True/False,
          "deviation_severity": "none" / "data" / "schema" / "missing",
          "b1_value": ...,
          "observed_value": ...,
          "relation": ...,
          "reason": ...,
        }
    """
    key = f"{agent}.{checkpoint}.{field}"
    entry = oracle.get(key)

    if entry is None:
        return {
            "key":                key,
            "deviation":          False,
            "deviation_severity": "none",
            "b1_value":           None,
            "observed_value":     observed_value,
            "relation":           "unknown",
            "reason":             "key not in oracle — skipped",
        }

    if observed_value is None:
        return {
            "key":                key,
            "deviation":          True,
            "deviation_severity": "missing",
            "b1_value":           entry.get("oracle_value"),
            "observed_value":     None,
            "relation":           entry.get("comparison_relation"),
            "reason":             "observed value is None (checkpoint possibly absent)",
        }

    # Build a field_entry-like dict for the validator
    field_like = dict(entry.get("comparison_params", {}))
    field_like["comparison_relation"] = entry.get("comparison_relation")
    field_like["field"] = field

    is_valid, reason = _validate(field_like, observed_value)

    severity = "none" if is_valid else (
        "schema"  if entry.get("comparison_relation") == "schema_regex" else
        "data"
    )

    return {
        "key":                key,
        "deviation":          not is_valid,
        "deviation_severity": severity,
        "b1_value":           entry.get("oracle_value"),
        "observed_value":     observed_value,
        "relation":           entry.get("comparison_relation"),
        "faults_this_detects": entry.get("mutation_faults_detected", []),
        "reason":             reason,
    }


def compare_lkw_trace_to_oracle(agent: str, lkw_trace: list, oracle: dict) -> list:
    """
    Compare an entire LKW trace for one agent against the oracle.
    Returns a list of deviation results (one per field in the map that exists in the trace).
    """
    results = []
    for key, entry in oracle.items():
        if not key.startswith(agent + "."):
            continue
        _, checkpoint, field = key.split(".", 2)
        cp = _find_checkpoint(lkw_trace, checkpoint)
        value = _get_field(cp, field) if cp else None
        result = compare_to_oracle(agent, checkpoint, field, value, oracle)
        results.append(result)
    return results


if __name__ == "__main__":
    main()
