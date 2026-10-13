"""Build the figures in results/figures/ from the CSV files written by run_experiments.py."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.transforms  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aozsim import MineModel, SimConfig  # noqa: E402

RES, FIG = ROOT / "results", ROOT / "results" / "figures"

# Validated categorical palette (fixed order, identity follows the strategy).
COLORS = {"central": "#2a78d6", "local": "#eb6834", "broker": "#1baf7a", "broker (ideal link)": "#eda100"}
KIND_COLORS = {"A": "#2a78d6", "B": "#eb6834", "manual": "#4a3aa7"}
LOSS_SHADES = {0.0: "#7fd8b5", 0.1: "#1baf7a", 0.3: "#0b6b49"}  # sequential, one hue
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "lines.linewidth": 2.0, "lines.markersize": 5, "legend.frameon": False,
})


def ci95(x: pd.Series) -> float:
    return 1.96 * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else 0.0


def place_end_labels(ax, items, min_gap=0.07):
    """Direct labels at the right end of lines, pushed apart so they never overlap."""
    if not items:
        return
    ax.figure.canvas.draw()
    to_axes = ax.transAxes.inverted()
    pts = []
    for text, x, y in items:
        _, fy = to_axes.transform(ax.transData.transform((x, y)))
        pts.append([fy, text, x])
    pts.sort()
    for i in range(1, len(pts)):
        pts[i][0] = max(pts[i][0], pts[i - 1][0] + min_gap)
    overflow = pts[-1][0] - 0.98
    if overflow > 0:
        for p in pts:
            p[0] -= overflow
    trans = matplotlib.transforms.blended_transform_factory(ax.transData, ax.transAxes)
    for fy, text, x in pts:
        ax.annotate(text, (x, fy), xycoords=trans, xytext=(8, 0), textcoords="offset points",
                    va="center", color=INK2, fontsize=9, annotation_clip=False)


def line_with_ci(ax, df, x, y, group, colors, label_fmt="{}"):
    labels = []
    for key, sub in df.groupby(group, sort=False, observed=True):
        g = sub.groupby(x)[y].agg(["mean", ci95]).reset_index()
        c = colors[key]
        ax.plot(g[x], g["mean"], marker="o", color=c, label=label_fmt.format(key))
        ax.fill_between(g[x], g["mean"] - g["ci95"], g["mean"] + g["ci95"], color=c, alpha=0.15, lw=0)
        labels.append((label_fmt.format(key), g[x].iloc[-1], g["mean"].iloc[-1]))
    return labels


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, dpi=160)
    plt.close(fig)
    print("saved", name)


def fig_fleet():
    df = pd.read_csv(RES / "E1.csv")
    order = ["central", "broker", "local"]
    df["strategy"] = pd.Categorical(df["strategy"], order, ordered=True)
    df = df.sort_values("strategy")

    fig, ax = plt.subplots(figsize=(6.4, 4))
    labels = line_with_ci(ax, df.assign(u=df.loader_utilisation * 100), "trucks_per_brand", "u", "strategy", COLORS)
    ax.set(title="Loader utilisation vs fleet size", xlabel="Trucks per brand", ylabel="Loader utilisation (%)",
           ylim=(0, 108), xlim=(0.8, 6.2))
    ax.legend(loc="lower right")
    place_end_labels(ax, labels)
    save(fig, "E1_loader_utilisation.png")

    fig, ax = plt.subplots(figsize=(6.4, 4))
    labels = line_with_ci(ax, df, "trucks_per_brand", "truck_delay_mean_s", "strategy", COLORS)
    ax.set(title="Intersection delay per truck crossing", xlabel="Trucks per brand",
           ylabel="Mean delay per crossing (s)", ylim=(0, None), xlim=(0.8, 6.2))
    ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.62))
    place_end_labels(ax, labels)
    save(fig, "E1_intersection_delay.png")


def fig_comm():
    df = pd.read_csv(RES / "E2.csv")
    brk = df[df.tag == "broker"]
    fig, ax = plt.subplots(figsize=(6.4, 4))
    for loss, sub in brk.groupby("comm_loss"):
        g = sub.groupby("comm_latency")["truck_delay_mean_s"].agg(["mean", ci95]).reset_index()
        c = LOSS_SHADES[loss]
        ax.plot(g.comm_latency, g["mean"], marker="o", color=c, label=f"broker, {loss:.0%} message loss")
        ax.fill_between(g.comm_latency, g["mean"] - g.ci95, g["mean"] + g.ci95, color=c, alpha=0.15, lw=0)
    for ref in ("local", "central"):
        m = df[df.tag == ref]["truck_delay_mean_s"].mean()
        ax.axhline(m, ls="--", lw=1.5, color=COLORS[ref])
        ax.annotate(f"{ref} (no communication)" if ref == "local" else f"{ref} (ideal, sees everything)",
                    (8, m), xytext=(0, 5), textcoords="offset points", ha="right", color=INK2, fontsize=9)
    ax.set(title="Broker sensitivity to communication quality", xlabel="One-way latency (s)",
           ylabel="Mean delay per crossing (s)", ylim=(0, None))
    ax.legend(loc="upper left")
    save(fig, "E2_communication.png")


def fig_manual():
    df = pd.read_csv(RES / "E3.csv")
    order = ["central", "broker (ideal link)", "broker", "local"]
    for metric, title, ylabel, name, legend_loc in [
        ("interventions_per_h", "Safety-layer interventions vs manual traffic",
         "Interventions per hour", "E3_interventions.png", "upper left"),
        ("truck_delay_mean_s", "Truck delay vs manual traffic", "Mean delay per crossing (s)",
         "E3_truck_delay.png", "center left"),
    ]:
        fig, ax = plt.subplots(figsize=(6.4, 4))
        sub = df.assign(tag=pd.Categorical(df.tag, order, ordered=True)).sort_values("tag")
        labels = line_with_ci(ax, sub, "manual_rate", metric, "tag", COLORS)
        ax.set(title=title, xlabel="Manual light-vehicle crossings per hour", ylabel=ylabel,
               ylim=(0, None), xlim=(-2, 50))
        ax.legend(loc=legend_loc)
        place_end_labels(ax, labels)
        save(fig, name)


def fig_tornado():
    df = pd.read_csv(RES / "E4.csv")
    base = df[df.tag == "baseline"]["truck_delay_mean_s"].mean()
    rows = []
    for tag, sub in df[df.tag != "baseline"].groupby("tag"):
        param, level, value = tag.split("|")
        rows.append((param, level, float(value), sub["truck_delay_mean_s"].mean() - base))
    t = pd.DataFrame(rows, columns=["param", "level", "value", "delta"])
    span = t.groupby("param")["delta"].apply(lambda s: s.abs().max()).sort_values()
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    y = np.arange(len(span))
    for i, p in enumerate(span.index):
        for level, color, off in (("low", "#2a78d6", -0.18), ("high", "#eb6834", 0.18)):
            r = t[(t.param == p) & (t.level == level)].iloc[0]
            ax.barh(i + off, r.delta, height=0.34, color=color, label=level if i == 0 else None)
            ax.annotate(f"{r.value:g}", (r.delta, i + off), xytext=(4 if r.delta >= 0 else -4, 0),
                        textcoords="offset points", va="center", ha="left" if r.delta >= 0 else "right",
                        fontsize=8, color=INK2)
    ax.set_axisbelow(True)
    ax.axvline(0, color=INK2, lw=1)
    ax.set_yticks(y, [p.replace("_", " ") for p in span.index])
    ax.set(title=f"What drives intersection delay? (broker, baseline {base:.1f} s)",
           xlabel="Change in mean delay per crossing vs baseline (s)")
    ax.grid(axis="y", visible=False)
    ax.legend(title="parameter value", loc="lower right")
    save(fig, "E4_sensitivity.png")


def fig_time_space():
    """Trajectories near the zone: local stop-and-go vs broker flow."""
    t0, t1 = 2400, 2760
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, strategy in zip(axes, ("local", "broker")):
        m = MineModel(SimConfig(strategy=strategy, seed=4, warmup=0, horizon=t1, trace=True,
                                manual_rate_per_hour=12)).run()
        tr = pd.DataFrame(m.trace, columns=["t", "vid", "lane", "d", "kind"])
        tr = tr[(tr.t >= t0) & (tr.t <= t1) & (tr.d.between(-120, 40))]
        for (vid, kind), g in tr.groupby(["vid", "kind"]):
            # split a trajectory when the vehicle leaves and re-enters the window
            cuts = [0, *(np.flatnonzero(np.diff(g.t.to_numpy()) > 1.5) + 1), len(g)]
            for a, b in zip(cuts[:-1], cuts[1:]):
                seg = g.iloc[a:b]
                ax.plot(seg.t - t0, seg.d, color=KIND_COLORS[kind], lw=1.4)
        ax.axhspan(0, 20, color=GRID, alpha=0.9, lw=0, zorder=0)
        ax.set(title=f"{strategy}", xlabel="Time (s)", xlim=(0, t1 - t0))
    axes[0].set_ylabel("Distance to zone entry (m)")
    handles = [plt.Line2D([], [], color=c, lw=2) for c in KIND_COLORS.values()]
    handles.append(matplotlib.patches.Patch(color=GRID))
    fig.legend(handles, ["brand A truck", "brand B truck", "manual vehicle", "conflict zone"],
               loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Time-space diagram near the intersection (6 min, identical traffic). "
                 "Flat segments = vehicle stopped at the line", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(FIG / "time_space_diagram.png", dpi=160)
    plt.close(fig)
    print("saved time_space_diagram.png")


BRAND_COLORS = {"A": "#2a78d6", "B": "#eb6834"}
RULE_LABELS = {"fifo": "FIFO", "loaded_first": "loaded first", "brand_priority": "brand A first"}


def fig_priority():
    df = pd.read_csv(RES / "E5.csv")
    df[["strat", "rule"]] = df.tag.str.split("|", expand=True)
    df["gap"] = df.truck_delay_B_s - df.truck_delay_A_s
    rules, strategies = list(RULE_LABELS), ["central", "broker", "local"]
    fig, ax = plt.subplots(figsize=(6.8, 4))
    width = 0.26
    for j, strat in enumerate(strategies):
        g = df[df.strat == strat].groupby("rule")["gap"].agg(["mean", ci95]).reindex(rules)
        x = np.arange(len(rules)) + (j - 1) * width
        ax.bar(x, g["mean"], width=width - 0.03, color=COLORS[strat], label=strat,
               yerr=g["ci95"], error_kw=dict(ecolor=INK2, lw=1, capsize=2))
    ax.set_axisbelow(True)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(np.arange(len(rules)), [RULE_LABELS[r] for r in rules])
    ax.set(title="Who pays for the priority rule?", ylabel="Brand B delay minus brand A delay (s)",
           xlabel="Site priority rule (intersection)")
    ax.grid(axis="x", visible=False)
    ax.legend(title="strategy", loc="upper left")
    save(fig, "E5_priority_fairness.png")


def fig_outage():
    ts = pd.read_csv(RES / "E6_timeseries.csv")
    start, end = 2700.0, 3300.0
    order = ["no outage", "outage, fallback to local rules", "outage, fail-safe stop"]
    colors = {order[0]: "#2a78d6", order[1]: "#1baf7a", order[2]: "#eb6834"}

    # Cumulative loads lost compared with the no-outage run of the same seed.
    piv = ts.pivot_table(index=["seed", "t_bin"], columns="tag", values="loads").sort_index()
    cum = piv.groupby(level="seed").cumsum()
    fig, ax = plt.subplots(figsize=(6.8, 4))
    labels = []
    for tag in order[1:]:
        lost = (cum[order[0]] - cum[tag]).groupby(level="t_bin").mean()
        x = (lost.index + 120) / 60
        ax.plot(x, lost.values, color=colors[tag], label=tag)
        labels.append((tag.replace("outage, ", ""), x[-1], lost.values[-1]))
    ax.axvspan(start / 60, end / 60, color=GRID, lw=0, zorder=0)
    ax.annotate("broker down\n(10 min)", ((start + end) / 120, 0.45), xycoords=("data", "axes fraction"),
                ha="center", va="center", fontsize=9, color=INK2)
    ax.set(title="Production lost during a broker outage", xlabel="Time (min)",
           ylabel="Loads lost vs no outage (cumulative)", xlim=(30, None))
    ax.legend(loc="center right")
    place_end_labels(ax, labels)
    save(fig, "E6_outage_production.png")

    fig, ax = plt.subplots(figsize=(6.8, 4))
    for tag in order[:2]:
        g = ts[ts.tag == tag].groupby("t_bin")["delay_mean_s"].mean()
        ax.plot((g.index + 60) / 60, g.values, color=colors[tag], label=tag)
    ax.axvspan(start / 60, end / 60, color=GRID, lw=0, zorder=0)
    ax.annotate("broker down (10 min).\nWith a fail-safe stop instead,\nno truck crosses at all here.",
                (end / 60 + 1.5, 0.93), xycoords=("data", "axes fraction"), ha="left", va="top",
                fontsize=9, color=INK2)
    ax.set(title="Intersection delay over time (2-min bins)", xlabel="Time (min)",
           ylabel="Mean delay per crossing (s)", xlim=(30, None), ylim=(0, None))
    ax.legend(loc="center right")
    save(fig, "E6_outage_delay.png")


def fig_shared_crusher():
    df = pd.read_csv(RES / "E7.csv")
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    labels = []
    for rule, ls in (("fifo", "-"), ("brand_priority", "--")):
        sub = df[df.tag == rule]
        for b in ("A", "B"):
            g = sub.groupby("dump_time").agg(u=("crusher_utilisation", "mean"),
                                              w=(f"crusher_wait_{b}_s", "mean"),
                                              ci=(f"crusher_wait_{b}_s", ci95))
            name = f"brand {b}, {RULE_LABELS[rule]}"
            ax.plot(g.u * 100, g.w, ls=ls, marker="o", color=BRAND_COLORS[b], label=name)
            ax.fill_between(g.u * 100, g.w - g.ci, g.w + g.ci, color=BRAND_COLORS[b], alpha=0.12, lw=0)
            labels.append((name, g.u.iloc[-1] * 100, g.w.iloc[-1]))
    ax.set(title="Waiting at a crusher shared by both brands", xlabel="Crusher utilisation (%)",
           ylabel="Mean wait before dumping (s)", ylim=(0, None), xlim=(64, 100))
    ax.legend(loc="upper left")
    place_end_labels(ax, labels, min_gap=0.075)
    save(fig, "E7_shared_crusher.png")


def summary_table():
    df = pd.read_csv(RES / "E3.csv")
    sub = df[df.manual_rate == 6]
    cols = ["truck_delay_mean_s", "truck_delay_p95_s", "manual_delay_mean_s", "interventions_per_h",
            "zone_utilisation", "throughput_per_h"]
    table = sub.groupby("tag")[cols].mean().round(2)
    table.to_csv(RES / "summary_baseline.csv")
    print(table.to_string())


if __name__ == "__main__":
    FIG.mkdir(parents=True, exist_ok=True)
    fig_fleet()
    fig_comm()
    fig_manual()
    fig_tornado()
    fig_time_space()
    fig_priority()
    fig_outage()
    fig_shared_crusher()
    summary_table()
