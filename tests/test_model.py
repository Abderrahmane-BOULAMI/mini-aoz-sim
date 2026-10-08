"""Verification tests: kinematics, validation against theory, safety, reproducibility."""

import random

import pytest

from aozsim import MineModel, SimConfig, run
from aozsim.communication import Channel
from aozsim.kinematics import free_flow_leg_time, next_speed

STRATEGIES = ["local", "central", "broker"]


def analytic_cycle(cfg: SimConfig) -> float:
    t = cfg.trucks
    return (cfg.load_time + cfg.dump_time
            + free_flow_leg_time(cfg.haul_road_length, t.v_max_loaded, t.accel, t.decel)
            + free_flow_leg_time(cfg.haul_road_length, t.v_max_empty, t.accel, t.decel))


# --- Kinematics ---------------------------------------------------------------
def test_speed_update_never_overshoots_constraint():
    rng = random.Random(0)
    for _ in range(10_000):
        v, d, b, dt = rng.uniform(0, 15), rng.uniform(0, 200), rng.uniform(0.5, 3), rng.choice([0.1, 0.5, 1.0])
        v_new = next_speed(v, 15.0, 1.0, b, d, dt)
        # after moving, the vehicle can still stop before the constraint
        assert v_new * dt + v_new**2 / (2 * b) <= d + 1e-9


# --- Validation against an analytical result -----------------------------------
@pytest.mark.parametrize("dt, tol", [(1.0, 0.015), (0.1, 0.002)])
def test_single_truck_cycle_matches_theory(dt, tol):
    """One truck, no conflicts: simulated cycle time == analytical free-flow cycle."""
    cfg = SimConfig(trucks_per_brand=1, manual_rate_per_hour=0, strategy="central", dt=dt, service_cv=0)
    expected = analytic_cycle(cfg)
    simulated = run(cfg)["cycle_time_mean_s"]
    assert simulated == pytest.approx(expected, rel=tol)


# --- Safety invariant ---------------------------------------------------------------
STRESS = [
    dict(trucks_per_brand=8, manual_rate_per_hour=48),
    dict(trucks_per_brand=6, manual_rate_per_hour=24, comm_latency=8, comm_loss=0.4),
    dict(trucks_per_brand=3, manual_rate_per_hour=60, comm_latency=0, comm_loss=0.0),
]


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("stress", STRESS)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_never_two_vehicles_in_the_zone(strategy, stress, seed):
    r = run(SimConfig(strategy=strategy, seed=seed, horizon=3600, **stress))
    assert r["safety_violations"] == 0
    assert r["throughput_per_h"] > 0  # liveness: no deadlock


# --- Reproducibility and experimental design ----------------------------------------
def test_same_seed_same_result():
    cfg = SimConfig(strategy="broker", seed=42, horizon=1800)
    assert run(cfg) == run(cfg)


def test_strategies_see_identical_manual_traffic():
    """Common random numbers: manual arrivals do not depend on the strategy."""
    arrivals = {s: list(MineModel(SimConfig(strategy=s, seed=5))._manual_arrivals) for s in STRATEGIES}
    assert arrivals["local"] == arrivals["central"] == arrivals["broker"]


def test_broker_without_latency_matches_central_without_manual_traffic():
    """With perfect communication and nobody unconnected, broker == central."""
    base = SimConfig(seed=3, manual_rate_per_hour=0, comm_latency=0, comm_loss=0, horizon=3600)
    a, b = run(base.with_(strategy="central")), run(base.with_(strategy="broker"))
    assert a["truck_delay_mean_s"] == pytest.approx(b["truck_delay_mean_s"])
    assert a["throughput_per_h"] == b["throughput_per_h"]


# --- Communication channel ---------------------------------------------------------
def test_channel_latency_and_loss():
    ch = Channel(latency=3.0, loss=0.2, rng=random.Random(1))
    for i in range(10_000):
        ch.send(i, due=0.0)
    assert ch.deliver(2.9) == []
    delivered = ch.deliver(3.0)
    assert len(delivered) + ch.lost == 10_000
    assert ch.lost / 10_000 == pytest.approx(0.2, abs=0.02)
    assert delivered == sorted(delivered)  # FIFO


# --- Priority policy, shared crusher, broker outage ----------------------------------
def test_priority_rank():
    from aozsim.policy import priority_rank
    kw = dict(favoured="A")
    assert priority_rank("fifo", brand="B", loaded=False, is_manual=False, **kw) == 0
    assert priority_rank("loaded_first", brand="B", loaded=True, is_manual=False, **kw) == 0
    assert priority_rank("loaded_first", brand="A", loaded=False, is_manual=False, **kw) == 1
    assert priority_rank("brand_priority", brand="A", loaded=False, is_manual=False, **kw) == 0
    assert priority_rank("brand_priority", brand="B", loaded=True, is_manual=False, **kw) == 1
    assert priority_rank("brand_priority", brand=None, loaded=False, is_manual=True, **kw) == 0
    with pytest.raises(ValueError):
        priority_rank("random", brand="A", loaded=False, is_manual=False, **kw)


def test_brand_priority_shifts_waiting_to_the_other_brand():
    """At a saturated shared crusher, the non-favoured brand waits much longer."""
    cfg = SimConfig(shared_crusher=True, dump_time=60, trucks_per_brand=5, horizon=3600)
    fifo = run(cfg.with_(priority_rule="fifo"))
    prio = run(cfg.with_(priority_rule="brand_priority"))
    assert prio["crusher_wait_B_s"] > 2 * prio["crusher_wait_A_s"]
    assert abs(fifo["crusher_wait_B_s"] - fifo["crusher_wait_A_s"]) < 0.5 * fifo["crusher_wait_A_s"]


def test_shared_crusher_keeps_every_truck_in_the_system():
    m = MineModel(SimConfig(shared_crusher=True, trucks_per_brand=3, horizon=1800)).run()
    trucks = [a for a in m.agents if a.__class__.__name__ == "HaulTruck"]
    assert len(trucks) == 6
    assert m.deliveries["A"] > 0 and m.deliveries["B"] > 0


@pytest.mark.parametrize("fallback", ["local", "stop"])
def test_broker_outage_is_safe(fallback):
    r = run(SimConfig(outage_start=1500, outage_duration=600, fallback=fallback,
                      manual_rate_per_hour=24, horizon=3600))
    assert r["safety_violations"] == 0
    # trucks are uncoordinated during the outage, the 40 s recovery and the heartbeat timeout
    assert r["fallback_share"] == pytest.approx((600 + 40 + 5) / 3600, abs=0.01)


def test_fail_safe_stop_costs_production_but_local_fallback_does_not():
    base = SimConfig(horizon=3600, seed=2)
    normal = run(base)["throughput_per_h"]
    local = run(base.with_(outage_start=1500, fallback="local"))["throughput_per_h"]
    stop = run(base.with_(outage_start=1500, fallback="stop"))["throughput_per_h"]
    assert stop < local - 3
    assert local >= normal - 1
