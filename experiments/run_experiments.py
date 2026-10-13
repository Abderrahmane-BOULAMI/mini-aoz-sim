"""Run the experiment campaign and save one CSV per experiment in results/.

Usage:
    python experiments/run_experiments.py              # full campaign (20 seeds)
    python experiments/run_experiments.py --seeds 5    # quick check
    python experiments/run_experiments.py --only E2

Experiments
    E1  Fleet size        - how many trucks per brand are needed to keep loaders busy?
    E2  Communication     - broker sensitivity to latency and message loss
    E3  Manual traffic    - what happens with unconnected, manually driven vehicles?
    E4  Sensitivity       - which parameters matter most (one-at-a-time, broker)?
    E5  Priority rules    - FIFO, loaded-first or brand priority: who pays?
    E6  Broker outage     - 10 min broker failure: fall back to local rules or stop?
    E7  Shared crusher    - both brands dump at one crusher: fairness as it saturates
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aozsim import MineModel, SimConfig, run  # noqa: E402

RESULTS = ROOT / "results"
BASE = SimConfig()


def _run(job):
    tag, cfg = job
    out = run(cfg)
    out["tag"] = tag
    return out


BIN = 120.0  # s, time-series resolution for E6


def _run_timeseries(job):
    """Summary plus binned time series (deliveries and mean truck delay per bin)."""
    tag, cfg = job
    m = MineModel(cfg).run()
    summary = m.summary() | {"tag": tag}
    n_bins = int(cfg.total_time // BIN)
    loads = [0] * n_bins
    delays = [[] for _ in range(n_bins)]
    for t, _brand in m.delivery_log:
        loads[min(int(t // BIN), n_bins - 1)] += 1
    for t, d, kind in m.crossing_log:
        if kind != "manual":
            delays[min(int(t // BIN), n_bins - 1)].append(d)
    series = [{"tag": tag, "seed": cfg.seed, "t_bin": i * BIN, "loads": loads[i],
               "delay_mean_s": sum(delays[i]) / len(delays[i]) if delays[i] else None}
              for i in range(n_bins)]
    return summary, series


def e1(seeds):
    for n in range(1, 7):
        for s in ("local", "central", "broker"):
            for seed in seeds:
                yield s, BASE.with_(strategy=s, trucks_per_brand=n, seed=seed)


def e2(seeds):
    no_manual = BASE.with_(manual_rate_per_hour=0)
    for lat in (0, 1, 2, 3, 5, 8):
        for loss in (0.0, 0.1, 0.3):
            for seed in seeds:
                yield "broker", no_manual.with_(strategy="broker", comm_latency=lat, comm_loss=loss, seed=seed)
    for s in ("local", "central"):  # reference levels
        for seed in seeds:
            yield s, no_manual.with_(strategy=s, seed=seed)


def e3(seeds):
    for rate in (0, 6, 12, 24, 48):
        for seed in seeds:
            base = BASE.with_(manual_rate_per_hour=rate, seed=seed)
            yield "local", base.with_(strategy="local")
            yield "central", base.with_(strategy="central")
            yield "broker (ideal link)", base.with_(strategy="broker", comm_latency=0, comm_loss=0)
            yield "broker", base.with_(strategy="broker")


SENSITIVITY = {
    # parameter: (low, high) around the baseline, broker strategy
    "comm_latency": (0.0, 3.0),
    "comm_loss": (0.0, 0.2),
    "trucks_per_brand": (3, 5),
    "manual_rate_per_hour": (0.0, 24.0),
    "grant_lookahead": (3.0, 30.0),
    "zone_width": (10.0, 30.0),
    "human_decision_delay": (1.5, 6.0),
}


def e4(seeds):
    base = BASE.with_(strategy="broker")
    for seed in seeds:
        yield "baseline", base.with_(seed=seed)
    for param, (lo, hi) in SENSITIVITY.items():
        for level, value in (("low", lo), ("high", hi)):
            for seed in seeds:
                yield f"{param}|{level}|{value}", base.with_(seed=seed, **{param: value})


def e5(seeds):
    for rule in ("fifo", "loaded_first", "brand_priority"):
        for s in ("local", "central", "broker"):
            for seed in seeds:
                yield f"{s}|{rule}", BASE.with_(strategy=s, priority_rule=rule, seed=seed)


OUTAGE_START = 2700.0  # s: 30 min after warm-up


def e6(seeds):
    base = BASE.with_(strategy="broker")
    for seed in seeds:
        yield "no outage", base.with_(seed=seed)
        yield "outage, fallback to local rules", base.with_(seed=seed, outage_start=OUTAGE_START, fallback="local")
        yield "outage, fail-safe stop", base.with_(seed=seed, outage_start=OUTAGE_START, fallback="stop")


def e7(seeds):
    base = BASE.with_(strategy="broker", shared_crusher=True, trucks_per_brand=5)
    for dump in (40, 45, 50, 55, 60):
        for rule in ("fifo", "brand_priority"):
            for seed in seeds:
                yield rule, base.with_(dump_time=dump, priority_rule=rule, seed=seed)


EXPERIMENTS = {"E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6, "E7": e7}
TIMESERIES = {"E6"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--only", choices=list(EXPERIMENTS), nargs="*")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    seeds = list(range(1, args.seeds + 1))
    for name in args.only or EXPERIMENTS:
        jobs = list(EXPERIMENTS[name](seeds))
        t0 = time.time()
        with Pool(args.workers) as pool:
            if name in TIMESERIES:
                out = pool.map(_run_timeseries, jobs, chunksize=2)
                rows = [summary for summary, _ in out]
                pd.DataFrame([r for _, series in out for r in series]).to_csv(
                    RESULTS / f"{name}_timeseries.csv", index=False)
            else:
                rows = pool.map(_run, jobs, chunksize=4)
        df = pd.DataFrame(rows)
        violations = int(df["safety_violations"].sum())
        df.to_csv(RESULTS / f"{name}.csv", index=False)
        print(f"{name}: {len(jobs)} runs in {time.time() - t0:.0f} s, safety violations = {violations}")


if __name__ == "__main__":
    main()
