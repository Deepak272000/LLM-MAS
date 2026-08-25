"""Build LKW verification tables from results/cross_model_lkw_matrix.json.

Read-only. Emits one table per agent: rows are (fault, field), columns are
model tags, cells are the per-run observed values with the B1 expectation.
"""
import argparse
import collections
import json
import os

MATRIX = os.path.join("results", "cross_model_lkw_matrix.json")
MAP_FILE = os.path.join("results", "checkpoint_variable_map.json")
ORACLE_FILE = os.path.join("results", "b1_oracle_values.json")
MODEL_ORDER = ["3b", "llama32-3b", "qwen25-1.5b", "qwen3-1.7b"]

CHECKOUT_AGENT_ORDER = [
    "checkout_orchestrator", "productcatalog", "currency",
    "shipping_quote", "payment", "ship_order", "email",
]

SYS_TO_ORACLE = {
    "productcatalog": "productcatalogagent",
    "currency":       "currencyagent",
    "payment":        "paymentagent",
    "email":          "emailserviceagent",
    "shipping_quote": "shippingagent_get_quote",
    "ship_order":     "shippingagent_ship_order",
}

STATE_LABEL = {
    "not_reached":  "PATH_INFEAS.",
    "not_targeted": "n/a (untargeted)",
    "absent":       "ABSENT",
}


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["runs"]


def cells(runs, agent, fault, field, model):
    """Per-run cell values for one (agent, fault, field, model)."""
    sel = sorted(
        [r for r in runs if r.get("model_tag") == model and r.get("fault_mode") == fault],
        key=lambda r: r.get("run_idx") or 0,
    )
    out = []
    for r in sel:
        st = (r.get("agents") or {}).get(agent) or {}
        state = st.get("state")
        if state != "compared":
            out.append(STATE_LABEL.get(state, str(state)))
            continue
        f = (st.get("fields") or {}).get(field)
        out.append("PASS" if f is None else f.get("observed"))
    return out


def b1_of(runs, agent, field):
    """Distinct B1 expectations seen for a field, to expose non-constant oracles."""
    seen = []
    for r in runs:
        st = (r.get("agents") or {}).get(agent) or {}
        f = (st.get("fields") or {}).get(field)
        if f is not None and f.get("b1") not in seen:
            seen.append(f.get("b1"))
    return seen


def fields_for(runs, agent):
    """Every field ever compared on this agent, grouped by fault."""
    by_fault = collections.defaultdict(set)
    for r in runs:
        st = (r.get("agents") or {}).get(agent) or {}
        if st.get("state") == "compared":
            for name in (st.get("fields") or {}):
                by_fault[r.get("fault_mode")].add(name)
    return by_fault


def classify(runs, agent, fault, field):
    """Separate fault signal from orchestrator input variance.

    Two independent conditions, both required. Attribution: the oracle's own
    `detects` list must name this fault, so the field is a designated detector
    and not incidental collateral. Discrimination: B1 was recorded in the
    isolated harness while B3 runs whatever order the orchestrator LLM composed,
    so a numeric deviation alone proves nothing -- the value must be a boolean
    fault indicator or land identically on every model and run, which a sampled
    quantity cannot do.
    """
    b1s, obs, attributed = [], [], False
    for r in runs:
        if r.get("fault_mode") != fault:
            continue
        st = (r.get("agents") or {}).get(agent) or {}
        if st.get("state") != "compared":
            continue
        f = (st.get("fields") or {}).get(field)
        if f is None:
            continue
        b1s.append(f.get("b1"))
        obs.append(f.get("observed"))
        if fault in (f.get("detects") or []):
            attributed = True
    if not obs:
        return "clean"

    if b1s and all(isinstance(b, bool) for b in b1s):
        discriminates = "bool"
    elif all(o == obs[0] for o in obs):
        discriminates = "invariant=%s" % (obs[0],)
    else:
        discriminates = ""

    # An invariant value proves nothing if the same value appears under faults the
    # field is not a detector for -- that makes it a constant of the pipeline
    # scenario (B1 was taken from the isolated harness) rather than injected signal.
    elsewhere = set()
    for r in runs:
        if r.get("fault_mode") == fault:
            continue
        st = (r.get("agents") or {}).get(agent) or {}
        if st.get("state") != "compared":
            continue
        f = (st.get("fields") or {}).get(field)
        if f is None or r.get("fault_mode") in (f.get("detects") or []):
            continue
        elsewhere.add(repr(f.get("observed")))
    specific = repr(obs[0]) not in elsewhere

    if attributed and discriminates:
        return "DETECTOR (%s)%s" % (discriminates,
                                    "" if specific else "  <-- NON-SPECIFIC")
    if not attributed and discriminates:
        return "unattributed (%s)" % discriminates
    if attributed:
        return "attributed, varies across runs/models"
    return "evidence"


def dump(runs, agent, fault, field, models, kind):
    b1 = b1_of(runs, agent, field)
    print("    %-18s B1=%-28s %s" % (field, b1 if len(b1) != 1 else b1[0], kind))
    for m in models:
        print("        %-14s %s" % (m, cells(runs, agent, fault, field, m)))


def table3(runs):
    """Def-use coverage and Type 1 / Type 2 classification.

    Type 1 = the fault moves a field that survives `classify` as a specific
    detector, so an oracle decides it. Type 2 = the fault was exercised on a
    reached agent but no field separates it, leaving only structural evidence.
    Deliberately excludes mutation score and propagation depth: those are scored
    against the pre-correction oracle and move when B3 is re-run.
    """
    cmap = json.load(open(MAP_FILE, encoding="utf-8"))
    oracle = json.load(open(ORACLE_FILE, encoding="utf-8"))["oracle"]

    du, valid = collections.Counter(), collections.Counter()
    for cp in cmap["checkpoints"]:
        key = cp["agent"]
        for var in cp["observed_variables"]:
            du[key] += 1
            entry = oracle.get("%s.%s.%s" % (key, cp["checkpoint"], var["field"]), {})
            if entry.get("oracle_type") != "absent" and entry.get("run_count"):
                valid[key] += 1

    rows, t1_tot, t2_tot = [], 0, 0
    for agent in CHECKOUT_AGENT_ORDER:
        okey = SYS_TO_ORACLE.get(agent, agent)
        by_fault = fields_for(runs, agent)
        t1, t2 = [], []
        for fault in sorted(by_fault):
            hit = any(
                classify(runs, agent, fault, f).startswith("DETECTOR")
                and "NON-SPECIFIC" not in classify(runs, agent, fault, f)
                for f in by_fault[fault]
            )
            (t1 if hit else t2).append(fault)
        t1_tot += len(t1)
        t2_tot += len(t2)
        rows.append((agent, du[okey], valid[okey], t1, t2))

    print("Table 3. LKW def-use coverage and detector classification")
    print("(oracle corrected at 7485d45; excludes mutation score and RIP depth)\n")
    print("  %-22s %5s %6s %8s %7s %7s  %s" %
          ("Agent", "DU", "B1ok", "Cells", "Type1", "Type2", "Type 1 faults"))
    for agent, d, v, t1, t2 in rows:
        print("  %-22s %5d %6d %8d %7d %7d  %s" %
              (agent, d, v, len(t1) + len(t2), len(t1), len(t2),
               ", ".join(f.replace("_", "-").replace("FM-", "FM ") for f in t1) or "-"))
    tot_du, tot_v = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print("  %-22s %5d %6d %8d %7d %7d" %
          ("TOTAL", tot_du, tot_v, t1_tot + t2_tot, t1_tot, t2_tot))
    print("\n  DU    = def-use pairs instrumented in checkpoint_variable_map.json")
    print("  B1ok  = pairs with a non-vacuous B1 baseline (run_count > 0)")
    print("  Cells = (agent, fault) pairs exercised, i.e. agent reached and compared")
    if tot_du:
        print("  B1 coverage: %d/%d = %.0f%%" % (tot_v, tot_du, 100.0 * tot_v / tot_du))
    if t1_tot + t2_tot:
        print("  Type 1: %d/%d = %.0f%% of exercised cells oracle-detectable"
              % (t1_tot, t1_tot + t2_tot, 100.0 * t1_tot / (t1_tot + t2_tot)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="currency")
    ap.add_argument("--matrix", default=MATRIX)
    ap.add_argument("--table3", action="store_true")
    args = ap.parse_args()

    if args.table3:
        table3(load(args.matrix))
        return

    runs = load(args.matrix)
    models = [m for m in MODEL_ORDER if any(r.get("model_tag") == m for r in runs)]
    by_fault = fields_for(runs, args.agent)

    print("agent: %s   models: %s\n" % (args.agent, ", ".join(models)))
    for fault in sorted(by_fault):
        scored, unscored = [], []
        for field in sorted(by_fault[fault]):
            kind = classify(runs, args.agent, fault, field)
            target = scored if kind.startswith("DETECTOR") else unscored
            target.append((field, kind))

        print("-- %s --" % fault)
        print("  [SCORED]")
        if scored:
            for field, kind in scored:
                dump(runs, args.agent, fault, field, models, kind)
        else:
            print("    none - no oracle field separates this fault from input variance")
        if unscored:
            print("  [EVIDENCE ONLY - inherits orchestrator input variance]")
            for field, kind in unscored:
                dump(runs, args.agent, fault, field, models, kind)
        print("")


if __name__ == "__main__":
    main()
