"""Command line entry point.

    python -m aozsim --strategy broker --trucks 4 --latency 2 --loss 0.1 --manual 12
"""

from __future__ import annotations

import argparse

from . import SimConfig, run


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulate one Autonomous Operation Zone run.")
    ap.add_argument("--strategy", choices=["local", "central", "broker"], default="broker")
    ap.add_argument("--trucks", type=int, default=4, help="trucks per brand")
    ap.add_argument("--latency", type=float, default=1.0, help="broker one-way latency (s)")
    ap.add_argument("--loss", type=float, default=0.05, help="broker message loss probability")
    ap.add_argument("--manual", type=float, default=6.0, help="manual crossings per hour")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hours", type=float, default=2.0, help="measured duration (h)")
    a = ap.parse_args()

    cfg = SimConfig(strategy=a.strategy, trucks_per_brand=a.trucks, comm_latency=a.latency,
                    comm_loss=a.loss, manual_rate_per_hour=a.manual, seed=a.seed,
                    horizon=a.hours * 3600)
    res = run(cfg)
    width = max(len(k) for k in res)
    for k, v in res.items():
        print(f"{k:<{width}}  {v:.3f}" if isinstance(v, float) else f"{k:<{width}}  {v}")


if __name__ == "__main__":
    main()
