"""Top-view animation of the intersection: local rules vs broker, same traffic.

    python experiments/animate.py      # writes results/figures/animation.gif
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aozsim import MineModel, SimConfig  # noqa: E402
from aozsim.vehicles import HaulTruck  # noqa: E402

SURFACE, INK2, ROAD, ZONE, ZONE_BUSY = "#fcfcfb", "#52514e", "#d9d8d2", "#bdbcb5", "#f2c4b0"
KIND_COLORS = {"A": "#2a78d6", "B": "#eb6834", "manual": "#4a3aa7"}
VIEW = 80.0       # m shown on each side of the zone centre
T0, T1 = 1230, 1400  # a busy window: three trucks per brand and three manual crossings
HALF_ZONE = 10.0
DIAG = 1 / math.sqrt(2)

# lane -> (origin of the lane's zone centre axis, unit heading, lateral offset vector)
LANE_AXES = {
    "A_haul": ((0, -3), (1, 0)),
    "A_return": ((0, 3), (-1, 0)),
    "B_haul": ((3, 0), (0, 1)),
    "B_return": ((-3, 0), (0, -1)),
    "M": ((0, 0), (DIAG, DIAG)),
}


def snapshot(model: MineModel) -> dict:
    vehicles = []
    for v in model.vehicles_on_lanes():
        kind = v.brand if isinstance(v, HaulTruck) else "manual"
        d = v.pos - v.lane.zone_start - HALF_ZONE   # front bumper, relative to zone centre
        granted = isinstance(v, HaulTruck) and v.grant is not None and v.grant.leg == v.leg and not v.cleared
        vehicles.append((v.lane.name, d, v.spec.length, kind, granted))
    return {"t": model.t, "vehicles": vehicles, "busy": not model.intersection.is_free,
            "loads": sum(model.deliveries.values()) + len(model.delivery_log) * 0}


def record(strategy: str) -> list[dict]:
    cfg = SimConfig(strategy=strategy, seed=4, warmup=0, horizon=T1, manual_rate_per_hour=20)
    m = MineModel(cfg)
    while m.t < T0:
        m.step()
    frames = []
    while m.t < T1:
        m.step()
        frames.append(snapshot(m))
    return frames


def vehicle_polygon(lane: str, d: float, length: float, width: float) -> list[tuple]:
    (ox, oy), (hx, hy) = LANE_AXES[lane]
    nx, ny = -hy, hx
    pts = []
    for along, lat in ((d, -width / 2), (d, width / 2), (d - length, width / 2), (d - length, -width / 2)):
        pts.append((ox + hx * along + nx * lat, oy + hy * along + ny * lat))
    return pts


def draw_static(ax, title: str) -> None:
    ax.set_xlim(-VIEW, VIEW)
    ax.set_ylim(-VIEW * 0.7, VIEW * 0.7)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((-VIEW, -7), 2 * VIEW, 14, color=ROAD, lw=0))
    ax.add_patch(Rectangle((-7, -VIEW), 14, 2 * VIEW, color=ROAD, lw=0))
    w = 3.5
    ax.add_patch(Polygon([(-VIEW - w, -VIEW + w), (-VIEW + w, -VIEW - w), (VIEW + w, VIEW - w), (VIEW - w, VIEW + w)],
                         closed=True, color=ROAD, lw=0, alpha=0.8))
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")
    style = dict(fontsize=7.5, color=INK2)
    ax.text(VIEW - 2, -12, "to crusher A", ha="right", **style)
    ax.text(-VIEW + 2, 10, "to loader A", ha="left", **style)
    ax.text(10, VIEW * 0.7 - 3, "to crusher B", ha="left", va="top", **style)
    ax.text(-10, -VIEW * 0.7 + 3, "to loader B", ha="right", va="bottom", **style)
    ax.text(-30, -50, "service road\n(manual vehicles)", ha="right", **style)


def main() -> None:
    runs = {"local rules (no communication)": record("local"), "broker (1 s latency, 5 % loss)": record("broker")}
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), facecolor=SURFACE)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.86, bottom=0.08, wspace=0.04)
    dynamic: list = []

    for ax, title in zip(axes, runs):
        ax.set_facecolor(SURFACE)
        draw_static(ax, title)
    clock = fig.text(0.5, 0.95, "", ha="center", fontsize=11, color=INK2)
    fig.text(0.5, 0.02, "blue: brand A truck   orange: brand B truck   violet: manual vehicle   "
             "black outline: holds a broker grant   tinted square: zone occupied",
             ha="center", fontsize=8, color=INK2)

    def draw(i: int):
        for artist in dynamic:
            artist.remove()
        dynamic.clear()
        for ax, frames in zip(axes, runs.values()):
            f = frames[i]
            zone = Rectangle((-HALF_ZONE, -HALF_ZONE), 2 * HALF_ZONE, 2 * HALF_ZONE,
                             color=ZONE_BUSY if f["busy"] else ZONE, lw=0, zorder=1)
            dynamic.append(ax.add_patch(zone))
            for lane, d, length, kind, granted in f["vehicles"]:
                if d < -VIEW - 20 or d - length > VIEW + 20:
                    continue
                width = 4.0 if kind != "manual" else 2.4
                poly = Polygon(vehicle_polygon(lane, d, length, width), closed=True, zorder=3,
                               facecolor=KIND_COLORS[kind], edgecolor="#0b0b0b" if granted else "none", lw=1.2)
                dynamic.append(ax.add_patch(poly))
        clock.set_text(f"t = {runs[next(iter(runs))][i]['t'] - T0:5.0f} s   (same arrivals in both panels)")
        return dynamic

    n = min(len(v) for v in runs.values())
    anim = FuncAnimation(fig, draw, frames=n, interval=100)
    out = ROOT / "results" / "figures" / "animation.gif"
    anim.save(out, writer=PillowWriter(fps=10), dpi=72)
    print("saved", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
