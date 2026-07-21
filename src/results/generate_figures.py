"""
Generate all 6 paper figures for LLM-MAS fault injection study.
Run from: e:\Summer ai Agent Project\LLM-MAS\src\results\
Output:   figures/*.pdf  (included via \includegraphics in paper)
"""

import json, os, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

OUT = "figures"
os.makedirs(OUT, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Shared style
# ──────────────────────────────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "figure.dpi": 150,
})

TIER_COLORS = {0: "#d9d9d9", 1: "#e74c3c", 2: "#f39c12", 3: "#95a5a6"}
TIER_LABELS = {0: "Tier 0 – Baseline", 1: "Tier 1 – Structural",
               2: "Tier 2 – Flag", 3: "Tier 3 – Silent"}

# ──────────────────────────────────────────────────────────────────────────────
# Figure 1: B2 Natural Variance Heatmap
# ──────────────────────────────────────────────────────────────────────────────
def fig_b2_heatmap():
    # Data extracted from raw/ runs
    agents_order = ["productcatalog", "currency", "shipping_quote",
                    "payment", "ship_order", "email"]
    agent_labels = ["ProductCatalog", "Currency", "ShippingQuote",
                    "Payment", "ShipOrder", "Email"]
    configs = ["3b_temp0", "3b_temp0.7", "3b_temp1.0"]
    cfg_labels = ["qwen2.5:3b\ntemp=0", "qwen2.5:3b\ntemp=0.7", "qwen2.5:3b\ntemp=1.0"]

    # Reach rates from experimental results
    reach = {
        "3b_temp0":   [1.0, 1.0, 1.0, 1.0, 0.0, 0.5],
        "3b_temp0.7": [1.0, 1.0, 0.9, 0.9, 0.6, 0.1],
        "3b_temp1.0": [1.0, 1.0, 0.9, 0.9, 0.5, 0.3],
    }

    data = np.array([reach[c] for c in configs])

    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    im = ax.imshow(data, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")

    ax.set_xticks(range(len(agents_order)))
    ax.set_xticklabels(agent_labels, rotation=30, ha="right")
    ax.set_yticks(range(len(configs)))
    ax.set_yticklabels(cfg_labels)
    ax.set_title("B2 Baseline — Agent Reach Rate by Temperature Config\n"
                 "(10 runs each, no fault injection; green=1.0, red=0.0)")

    for i in range(len(configs)):
        for j in range(len(agents_order)):
            v = data[i, j]
            col = "black" if 0.3 < v < 0.8 else "white"
            ax.text(j, i, f"{v:.0%}", ha="center", va="center",
                    fontsize=9, color=col, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("Reach rate", fontsize=9)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_b2_heatmap.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


# ──────────────────────────────────────────────────────────────────────────────
# Figure 2: HITL Tier Distribution Stacked Bar
# ──────────────────────────────────────────────────────────────────────────────
def fig_hitl_stacked():
    with open("hitl_classification_report.json") as f:
        hitl = json.load(f)

    agents_map = {
        "paymentagent":        "Payment",
        "currencyagent":       "Currency",
        "emailserviceagent":   "Email",
        "productcatalogagent": "ProductCatalog",
        "recommendationagent": "Recommendation",
        "adserviceagent":      "AdService",
        "shippingagent":       "Shipping",
    }

    agent_order = ["shippingagent", "paymentagent", "currencyagent",
                   "emailserviceagent", "productcatalogagent",
                   "recommendationagent", "adserviceagent"]

    counts = {}  # agent -> {tier: count}
    for ag in agent_order:
        entries = hitl["agents"].get(ag, [])
        tc = defaultdict(int)
        for e in entries:
            t = e["tier"]
            if t != 0:  # skip baseline
                tc[t] += 1
        counts[ag] = tc

    labels = [agents_map.get(a, a) for a in agent_order]
    tiers = [1, 2, 3]
    tier_data = {t: [counts[a].get(t, 0) for a in agent_order] for t in tiers}

    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    bottoms = np.zeros(len(agent_order))
    for t in tiers:
        vals = np.array(tier_data[t], dtype=float)
        ax.bar(labels, vals, bottom=bottoms, color=TIER_COLORS[t],
               label=TIER_LABELS[t], edgecolor="white", linewidth=0.5)
        for i, (v, b) in enumerate(zip(vals, bottoms)):
            if v > 0:
                ax.text(i, b + v / 2, str(int(v)), ha="center",
                        va="center", fontsize=8, color="black", fontweight="bold")
        bottoms += vals

    ax.set_ylabel("Number of fault scenarios")
    ax.set_title("HITL Tier Distribution Across 7 Agents")
    ax.legend(loc="upper right", framealpha=0.9)
    ax.set_ylim(0, bottoms.max() * 1.2)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    fig.tight_layout()
    path = os.path.join(OUT, "fig_hitl_stacked.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


# ──────────────────────────────────────────────────────────────────────────────
# Figure 3: Fault Detection Stability Scatterplot
# ──────────────────────────────────────────────────────────────────────────────
def fig_stability_scatter():
    with open("fault_lkw_mapping.json") as f:
        flm = json.load(f)
    rows = flm["rows"]

    with open("hitl_classification_report.json") as f:
        hitl = json.load(f)

    # Build fault -> tier lookup
    fault_tier = {}
    for ag_entries in hitl["agents"].values():
        for e in ag_entries:
            fm = e["fault_mode"]
            t  = e["tier"]
            # normalise key
            key = fm.replace("_", "_")
            if key not in fault_tier or fault_tier[key] > t:
                fault_tier[key] = t

    # Aggregate detection rate per (fault, agent)
    fa_det = defaultdict(list)
    for r in rows:
        for cfg, b3 in r.get("b3", {}).items():
            if b3:
                fa_det[(r["fault"], r["agent"])].append(b3["detect_pct"])

    # Unique (fault, agent) points
    points = []
    for (fault, agent), dets in fa_det.items():
        avg = sum(dets) / len(dets)
        tier = fault_tier.get(fault, fault_tier.get(fault.replace("FM_", "FM_"), 3))
        points.append({"fault": fault, "agent": agent, "avg_det": avg, "tier": tier})

    # Sort by fault name for consistent x-axis
    fault_names = sorted(set(p["fault"] for p in points))
    fault_idx = {f: i for i, f in enumerate(fault_names)}

    fig, ax = plt.subplots(figsize=(7.5, 3.8))

    plotted_tiers = set()
    for p in points:
        t = p["tier"]
        color = TIER_COLORS.get(t, TIER_COLORS[3])
        label = TIER_LABELS.get(t, f"Tier {t}") if t not in plotted_tiers else None
        ax.scatter(fault_idx[p["fault"]], p["avg_det"],
                   color=color, s=55, alpha=0.85, edgecolors="white",
                   linewidths=0.4, label=label, zorder=3)
        plotted_tiers.add(t)

    ax.axhline(100, color="green", lw=0.8, linestyle="--", alpha=0.5, label="100% detect")
    ax.axhline(0, color="red", lw=0.8, linestyle="--", alpha=0.5, label="0% detect")

    short_labels = [f.replace("BL_","BL-").replace("FM_","FM-").replace("_",".") for f in fault_names]
    ax.set_xticks(range(len(fault_names)))
    ax.set_xticklabels(short_labels, rotation=55, ha="right", fontsize=7.5)
    ax.set_ylabel("Avg detection rate (%)")
    ax.set_title("Fault Detection Stability — Avg Detection Rate per Fault Mode")
    ax.set_ylim(-5, 115)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="lower right", framealpha=0.9, fontsize=8)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_stability_scatter.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


# ──────────────────────────────────────────────────────────────────────────────
# Figure 4: Cross-Agent FM-2.2 Propagation Chain (horizontal flow)
# ──────────────────────────────────────────────────────────────────────────────
def fig_cross_agent_chain():
    """A lane diagram showing Currency→boundary→Payment propagation for FM-2.2."""
    with open("cross_agent_propagation.json") as f:
        cap = json.load(f)

    chain_a = cap["chains"][0]  # Currency → Payment

    fig, ax = plt.subplots(figsize=(7.5, 2.8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis("off")

    # Draw agent boxes
    boxes = [
        (0.3, 1.2, "CurrencyAgent\n(FM-2.2 injected)", "#d5e8d4", "#82b366"),
        (3.5, 1.2, "Boundary Check\ncurrency→payment", "#ffe6cc", "#d6b656"),
        (6.8, 1.2, "PaymentAgent\n(charge blocked)", "#f8cecc", "#b85450"),
        (9.0, 1.2, "HITL\nEscalation", "#dae8fc", "#6c8ebf"),
    ]
    for (x, y, label, fc, ec) in boxes:
        rect = mpatches.FancyBboxPatch(
            (x - 0.8, y - 0.45), 1.6, 0.9,
            boxstyle="round,pad=0.05", facecolor=fc,
            edgecolor=ec, linewidth=1.5)
        ax.add_patch(rect)
        ax.text(x, y, label, ha="center", va="center",
                fontsize=8, fontweight="bold")

    # Arrows between boxes
    arrows = [
        (0.8, 1.2, 2.7, 1.2, "units=1337\n(hallucinated)", "#e74c3c"),
        (4.3, 1.2, 6.0, 1.2, "delta=1328\nalert=True", "#d6b656"),
        (7.6, 1.2, 8.2, 1.2, "block_and\n_request_hitl", "#b85450"),
    ]
    for (x1, y1, x2, y2, lbl, col) in arrows:
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->", color=col, lw=2.0))
        mx, my = (x1 + x2) / 2, y1 + 0.28
        ax.text(mx, my, lbl, ha="center", va="bottom",
                fontsize=7.5, color=col, style="italic")

    # LKW checkpoint labels below
    ckpts = [
        (0.3, 0.55, "CONVERT_DONE\n(P2 c-use)"),
        (3.5, 0.55, "BOUNDARY_CHECK\n(P4 p-use)"),
        (6.8, 0.55, "TASK_START\n(P1 blocked)"),
    ]
    for (x, y, lbl) in ckpts:
        ax.text(x, y, lbl, ha="center", va="center",
                fontsize=7, color="#555", style="italic",
                bbox=dict(facecolor="#f5f5f5", edgecolor="#ccc",
                          boxstyle="round,pad=0.1", linewidth=0.8))

    ax.set_title(
        "Cross-Agent FM-2.2 Propagation Chain A: Currency → Boundary → Payment\n"
        "(baseline=9 EUR, hallucinated=1337 EUR; charge blocked by recovery policy)",
        fontsize=9.5)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_cross_agent_chain.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


# ──────────────────────────────────────────────────────────────────────────────
# Figure 5: B1 vs B3 LKW Trace Diff (PaymentAgent BL-AMOUNT-TAMPER)
# ──────────────────────────────────────────────────────────────────────────────
def fig_lkw_trace_diff():
    b1_ckpts = [
        ("TASK_START",    "units=9\ncurr=EUR", "P1", "clean"),
        ("CARD_VALIDATED","validated=True\nbypass=False", "P2", "clean"),
        ("CHARGE_DONE",   "charged=9.00\ntampered=False", "P3", "clean"),
        ("SAVE_DONE",     "skipped=False\nrecord_id=TXN-…", "P3", "clean"),
        ("FINAL_ANSWER",  "status=success", "P5", "clean"),
    ]
    b3_ckpts = [
        ("TASK_START",    "units=9\ncurr=EUR", "P1", "clean"),
        ("CARD_VALIDATED","validated=True\nbypass=False", "P2", "clean"),
        ("CHARGE_DONE",   "charged=9999.00\ntampered=True", "P3", "infected"),
        ("SAVE_DONE",     "skipped=False\nrecord_id=TXN-…", "P3", "propagated"),
        ("FINAL_ANSWER",  "status=success\n⚠ corrupt amount", "P5", "propagated"),
    ]

    state_color = {
        "clean":     "#d5e8d4",
        "infected":  "#f8cecc",
        "propagated":"#ffe6cc",
    }
    state_edge = {
        "clean":     "#82b366",
        "infected":  "#b85450",
        "propagated":"#d6b656",
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.5, 3.0),
                                    gridspec_kw={"wspace": 0.35})

    def draw_trace(ax, ckpts, title):
        ax.set_xlim(0, 4)
        ax.set_ylim(-0.5, len(ckpts) * 1.2)
        ax.axis("off")
        ax.set_title(title, fontsize=9.5, fontweight="bold")
        for i, (step, data, cls, state) in enumerate(ckpts):
            y = (len(ckpts) - i - 1) * 1.2
            # box
            rect = mpatches.FancyBboxPatch(
                (0.05, y - 0.38), 3.9, 0.76,
                boxstyle="round,pad=0.04",
                facecolor=state_color[state],
                edgecolor=state_edge[state], linewidth=1.5)
            ax.add_patch(rect)
            ax.text(0.2, y + 0.1, f"[{cls}] {step}", fontsize=8.5,
                    fontweight="bold", va="center")
            ax.text(0.2, y - 0.18, data, fontsize=7.5,
                    va="center", color="#333", style="italic")
            # arrow to next
            if i < len(ckpts) - 1:
                ax.annotate("", xy=(2.0, y - 0.42),
                            xytext=(2.0, y - 0.80),
                            arrowprops=dict(arrowstyle="->",
                                            color="#888", lw=1.2))

    draw_trace(ax1, b1_ckpts, "B1 Oracle (USE_LLM=false)\nBL-AMOUNT-TAMPER inactive")
    draw_trace(ax2, b3_ckpts, "B3 Fault Run (USE_LLM=false)\nBL-AMOUNT-TAMPER active")

    # Legend
    patches = [
        mpatches.Patch(facecolor=state_color["clean"],     edgecolor=state_edge["clean"],     label="Clean (B1 match)"),
        mpatches.Patch(facecolor=state_color["infected"],  edgecolor=state_edge["infected"],  label="Infected (first deviation)"),
        mpatches.Patch(facecolor=state_color["propagated"],edgecolor=state_edge["propagated"],label="Propagated"),
    ]
    fig.legend(handles=patches, loc="lower center", ncol=3,
               fontsize=8, framealpha=0.9,
               bbox_to_anchor=(0.5, -0.08))
    fig.suptitle("LKW Trace: B1 Oracle vs B3 Fault Run — PaymentAgent BL-AMOUNT-TAMPER",
                 fontsize=10, y=1.01)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_lkw_trace_diff.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


# ──────────────────────────────────────────────────────────────────────────────
# Figure 6: RIP Causality Chain (FM-2.2 across agents)
# ──────────────────────────────────────────────────────────────────────────────
def fig_rip_causality():
    """Show how FM-2.2 hits different checkpoints per agent and the HITL tier."""
    agents = [
        ("CurrencyAgent",   "CONVERT_DONE",        "P2 c-use", "Tier 3 Silent",  3),
        ("PaymentAgent",    "CHARGE_DONE",          "P2 c-use", "Tier 3 Silent",  3),
        ("EmailAgent",      "EMAIL_GENERATED",      "P2 p-use", "Tier 3 Silent",  3),
        ("ProductCatalog",  "CATALOG_DONE",         "P2 c-use", "Tier 3 Silent",  3),
        ("Recommendation",  "RECOMMEND_DONE",       "P2 c-use", "Tier 3 Silent",  3),
        ("AdService",       "ADS_FETCHED",          "P2 c-use", "Tier 3 Silent",  3),
        ("ShipOrder",       "CARRIER_DONE",         "P2 c-use", "Tier 3 Silent",  3),
    ]

    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    ax.set_xlim(0, 11)
    ax.set_ylim(-0.8, len(agents) * 0.9 + 0.2)
    ax.axis("off")

    col_x = [0.8, 3.2, 5.8, 8.2, 10.2]
    col_headers = ["Agent", "FM-2.2\nInject Site", "Infection\nCheckpoint",
                   "LKW Use\nType", "HITL Tier"]

    # Column headers
    for x, h in zip(col_x, col_headers):
        ax.text(x, len(agents) * 0.9, h, ha="center", va="bottom",
                fontsize=9, fontweight="bold", color="#333")
    ax.axhline(len(agents) * 0.9 - 0.1, color="#999", lw=1.0)

    for i, (agent, ckpt, use_type, tier_label, tier) in enumerate(agents):
        y = (len(agents) - i - 1) * 0.9

        # Row background
        fc = "#fafafa" if i % 2 == 0 else "white"
        ax.barh(y, 11, left=0, height=0.78, color=fc,
                align="center", alpha=0.5, zorder=0)

        # Agent name
        ax.text(col_x[0], y, agent, ha="center", va="center",
                fontsize=8.5, fontweight="bold")
        # Inject site (P2 checkpoint of each agent)
        inject_label = f"P2 output\n({ckpt.split('_')[0]})"
        ax.text(col_x[1], y, inject_label, ha="center", va="center",
                fontsize=8, style="italic", color="#666")
        # Infection checkpoint
        ax.text(col_x[2], y, ckpt, ha="center", va="center",
                fontsize=8, fontweight="bold",
                bbox=dict(facecolor="#f8cecc", edgecolor="#b85450",
                          boxstyle="round,pad=0.12", linewidth=0.9))
        # Use type
        ax.text(col_x[3], y, use_type, ha="center", va="center",
                fontsize=8, color="#555")
        # HITL tier badge
        tc = TIER_COLORS[tier]
        ax.text(col_x[4], y, tier_label, ha="center", va="center",
                fontsize=8,
                bbox=dict(facecolor=tc, edgecolor=state_edge_for_tier(tier),
                          boxstyle="round,pad=0.12", linewidth=0.9))

    ax.set_title(
        "RIP Causality: FM-2.2 (Hallucinated Output) — Per-Agent Infection Checkpoint",
        fontsize=10, pad=8)
    fig.tight_layout()
    path = os.path.join(OUT, "fig_rip_causality.pdf")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("Saved:", path)


def state_edge_for_tier(t):
    return {1: "#b85450", 2: "#d6b656", 3: "#888888", 0: "#999999"}.get(t, "#999")


# ──────────────────────────────────────────────────────────────────────────────
# Run all
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating figures...")
    fig_b2_heatmap()
    fig_hitl_stacked()
    fig_stability_scatter()
    fig_cross_agent_chain()
    fig_lkw_trace_diff()
    fig_rip_causality()
    print("Done. All figures saved to", OUT)
