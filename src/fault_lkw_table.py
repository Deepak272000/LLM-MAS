"""
fault_lkw_table.py
==================
Build the Fault × LKW × Checkpoint × Service mapping table.

Reads:
  results/checkpoint_variable_map.json  — LKW variable definitions
  results/b1_oracle_values.json         — oracle comparison types
  results/b3/b3_*_summary.json          — B3 detection results per fault × config

Outputs:
  results/fault_lkw_mapping.json        — full structured table
  Prints a formatted ASCII table to stdout

Usage:
  python fault_lkw_table.py
"""

from __future__ import annotations
import json
from pathlib import Path

ROOT    = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
B3_DIR  = RESULTS / "b3"

# ── Faults we ran in the final B3 campaign ────────────────────────────────────
TARGETED_FAULT_MATRIX = [
    ("FM_3_1",                "all",            "system"),
    ("FM_1_2",                "all",            "system"),
    ("FM_2_2",                "all",            "system"),
    ("FM_2_5",                "all",            "system"),
    ("BL_PRICE_MANIPULATION", "productcatalog", "business"),
    ("BL_RATE_MANIPULATION",  "currency",       "business"),
    ("BL_AMOUNT_TAMPERING",   "payment",        "business"),
    ("BL_INVENTORY_MISMATCH", "shipping_quote", "business"),
    ("BL_SHIPMENT_LOST",      "ship_order",     "business"),
    ("BL_CORRUPTED_BODY",     "email",          "business"),
]

CONFIGS = ["3b_temp0.7", "3b_temp1.0"]

# ── Agent name normalisation (cvm uses full names; b3 uses short names) ───────
ORACLE_TO_SHORT = {
    "paymentagent":          "payment",
    "productcatalogagent":   "productcatalog",
    "currencyagent":         "currency",
    "emailserviceagent":     "email",
    "shippingagent_get_quote":  "shipping_quote",
    "shippingagent_ship_order": "ship_order",
    "adserviceagent":           "adservice",
    "recommendationagent":      "recommendation",
}


def load_cvm() -> list[dict]:
    return json.load(open(RESULTS / "checkpoint_variable_map.json"))["checkpoints"]


def load_oracle() -> dict:
    return json.load(open(RESULTS / "b1_oracle_values.json"))["oracle"]


def load_b3_summaries() -> dict:
    """Return {(fault_mode, model_label): summary_dict}."""
    results = {}
    for f in B3_DIR.glob("b3_*_summary.json"):
        try:
            s = json.load(open(f))
            key = (s["fault_mode"], s["model_label"])
            results[key] = s
        except Exception:
            pass
    return results


def build_rows(cvm, oracle, b3) -> list[dict]:
    """
    One row per (fault, agent, checkpoint, field) triplet where the fault
    appears in that variable's mutation_faults_detected list.
    """
    rows = []
    # Build lookup: oracle_agent.checkpoint.field → oracle entry
    oracle_lookup = {}
    for key, entry in oracle.items():
        oracle_lookup[key] = entry

    # Build lookup: (fault, agent_short) → target_service, category
    fault_target = {fm: (tgt, cat) for fm, tgt, cat in TARGETED_FAULT_MATRIX}

    for cp in cvm:
        agent_full  = cp["agent"]
        checkpoint  = cp["checkpoint"]
        agent_short = ORACLE_TO_SHORT.get(agent_full, agent_full)

        for var in cp["observed_variables"]:
            detected_by = var.get("mutation_faults_detected", [])
            if not detected_by:
                continue

            oracle_key  = f"{agent_full}.{checkpoint}.{var['field']}"
            oracle_entry = oracle_lookup.get(oracle_key, {})

            for fault in detected_by:
                target_service, category = fault_target.get(fault, ("all", "other"))

                # B3 results for the two configs
                b3_results = {}
                for cfg in CONFIGS:
                    s = b3.get((fault, cfg))
                    if s:
                        b3_results[cfg] = {
                            "result":        s["overall_classification"],
                            "detect_pct":    round(s["detection_rate"] * 100),
                            "infected_at":   s.get("infection_agent") or "-",
                            "prop_depth":    s.get("max_propagation_depth", 0),
                        }
                    else:
                        b3_results[cfg] = None

                rows.append({
                    "fault":            fault,
                    "category":         category,
                    "target_service":   target_service,
                    "agent":            agent_short,
                    "agent_full":       agent_full,
                    "checkpoint":       checkpoint,
                    "field":            var["field"],
                    "field_type":       var.get("type", "?"),
                    "lkw_criterion":    var.get("lkw_criterion", "All-Uses"),
                    "use_type":         var.get("use_type", []),
                    "comparison":       oracle_entry.get("comparison_relation", var.get("comparison_relation", "?")),
                    "b3":               b3_results,
                })

    # Sort: category → fault → agent → checkpoint → field
    rows.sort(key=lambda r: (r["category"], r["fault"], r["agent"], r["checkpoint"], r["field"]))
    return rows


def print_table(rows: list[dict]) -> None:
    # Group by fault for cleaner display
    from itertools import groupby

    print("=" * 110)
    print(f"{'Fault × LKW × Checkpoint × Service Mapping Table':^110}")
    print("=" * 110)

    header = (
        f"{'Fault':<28} {'Cat':<8} {'Target':<16} "
        f"{'Service':<16} {'Checkpoint':<18} {'Field':<26} "
        f"{'Cmp':<12} {'0.7 Det%':>8} {'1.0 Det%':>8}"
    )
    print(header)
    print("-" * 110)

    prev_fault = None
    for r in rows:
        if r["fault"] != prev_fault:
            if prev_fault is not None:
                print()
            prev_fault = r["fault"]

        d07 = r["b3"].get("3b_temp0.7")
        d10 = r["b3"].get("3b_temp1.0")
        det07 = f"{d07['detect_pct']}% {d07['result']}" if d07 else "—"
        det10 = f"{d10['detect_pct']}% {d10['result']}" if d10 else "—"

        print(
            f"{r['fault']:<28} {r['category']:<8} {r['target_service']:<16} "
            f"{r['agent']:<16} {r['checkpoint']:<18} {r['field']:<26} "
            f"{r['comparison']:<12} {det07:>10} {det10:>10}"
        )

    print("=" * 110)


def service_coverage_matrix(b3: dict) -> None:
    """
    Print a service × fault coverage matrix based directly on B3 summary
    infection_agent and overall_classification fields (3b_temp0.7).
    """
    services   = ["productcatalog", "currency", "payment",
                  "shipping_quote", "ship_order", "email"]
    sys_faults = ["FM_3_1", "FM_1_2", "FM_2_2", "FM_2_5"]
    bl_faults  = ["BL_PRICE_MANIPULATION", "BL_RATE_MANIPULATION",
                  "BL_AMOUNT_TAMPERING",   "BL_INVENTORY_MISMATCH",
                  "BL_SHIPMENT_LOST",      "BL_CORRUPTED_BODY"]
    all_faults = sys_faults + bl_faults
    cfg        = "3b_temp0.7"

    # service → set of faults where this service was the infection point
    svc_infected_by: dict[str, set] = {s: set() for s in services}
    for (fault, model_label), s in b3.items():
        if model_label != cfg:
            continue
        inf = s.get("infection_agent")
        cls = s.get("overall_classification", "")
        if inf and cls in ("TP", "PARTIAL_TP") and inf in svc_infected_by:
            svc_infected_by[inf].add(fault)

    col_w = 8
    print()
    print(f"Service Coverage Matrix — {cfg}")
    print("-" * (18 + col_w * len(all_faults) + 2))
    hdr = f"{'Service':<18}" + "".join(f" {f[:6]:^{col_w}}" for f in all_faults)
    print(hdr)
    print("-" * (18 + col_w * len(all_faults) + 2))
    for svc in services:
        cell = f"{svc:<18}"
        for fault in all_faults:
            hit = fault in svc_infected_by.get(svc, set())
            cell += f" {'  TP  ' if hit else '  --  ':^{col_w}}"
        print(cell)

    # Summary: which services have both system + business fault coverage
    print()
    print(f"{'Service':<18} {'System faults':>14} {'Business faults':>16} {'Full coverage':>14}")
    print("-" * 66)
    for svc in services:
        hits = svc_infected_by.get(svc, set())
        sys_hit = sum(1 for f in sys_faults if f in hits)
        bl_hit  = sum(1 for f in bl_faults  if f in hits)
        full    = "YES" if (sys_hit > 0 and bl_hit > 0) else ("sys only" if sys_hit else "biz only" if bl_hit else "NONE")
        print(f"{svc:<18} {sys_hit:>14} {bl_hit:>16} {full:>14}")
    print()


def main() -> None:
    print("Loading data...")
    cvm    = load_cvm()
    oracle = load_oracle()
    b3     = load_b3_summaries()

    print(f"  CVM checkpoints: {len(cvm)}  |  oracle entries: {len(oracle)}  |  B3 summaries: {len(b3)}\n")

    rows = build_rows(cvm, oracle, b3)
    print(f"  Mapping rows: {len(rows)}\n")

    print_table(rows)
    service_coverage_matrix(b3)

    # Save full structured output
    out_path = RESULTS / "fault_lkw_mapping.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "description": "Fault × LKW × Checkpoint × Service mapping table",
            "generated_from": ["checkpoint_variable_map.json", "b1_oracle_values.json", "b3 summaries"],
            "configs": CONFIGS,
            "total_rows": len(rows),
            "rows": rows,
        }, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
