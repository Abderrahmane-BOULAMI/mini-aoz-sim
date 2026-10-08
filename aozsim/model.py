"""MineModel: one Autonomous Operation Zone with two truck brands and manual traffic."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from statistics import mean

import mesa
import numpy as np

from .config import SimConfig
from .coordination import make_strategy
from .geometry import build_lanes
from .intersection import Intersection
from .vehicles import HaulTruck, ManualVehicle, Vehicle

BRANDS = ("A", "B")


@dataclass(eq=False)
class ServicePoint:
    """Loader or crusher: one vehicle served at a time.

    The queue is FIFO, except under the ``brand_priority`` policy where trucks of
    the favoured brand are served first (relevant for a crusher shared by brands).
    """

    name: str
    service_time: float
    exit_lanes: dict  # brand -> lane name taken after service
    queue: deque = field(default_factory=deque)
    current: HaulTruck | None = None
    busy_until: float = 0.0
    busy_time: float = 0.0  # s, measured period only
    rng: random.Random | None = None
    cv: float = 0.0

    def draw_service_time(self) -> float:
        """Normal service time truncated at 50 % of the mean (cv = 0: deterministic)."""
        if not self.cv or self.rng is None:
            return self.service_time
        return max(0.5 * self.service_time, self.rng.gauss(self.service_time, self.cv * self.service_time))

    def pop_next(self, rule: str, favoured: str) -> HaulTruck:
        if rule == "brand_priority":
            for truck in self.queue:
                if truck.brand == favoured:
                    self.queue.remove(truck)
                    return truck
        return self.queue.popleft()


class MineModel(mesa.Model):
    def __init__(self, cfg: SimConfig | None = None) -> None:
        cfg = cfg or SimConfig()
        super().__init__(rng=cfg.seed)
        self.cfg = cfg
        self.t = 0.0
        # Independent random streams so that strategies are compared on the
        # same manual-traffic arrivals (common random numbers).
        self.arrival_rng = random.Random(cfg.seed * 7919 + 1)
        self.comm_rng = random.Random(cfg.seed * 7919 + 2)

        self.lanes = build_lanes(cfg)
        self.intersection = Intersection()
        self.vehicle_by_id: dict[int, Vehicle] = {}
        self.departures: dict[str, deque] = {name: deque() for name in self.lanes}
        self.service: dict[str, ServicePoint] = {}

        def add_service(name: str, mean_t: float, exits: dict, stream: int) -> None:
            rng = random.Random(cfg.seed * 7919 + 10 + stream)  # one stream per service point
            self.service[name] = ServicePoint(name, mean_t, exits, rng=rng, cv=cfg.service_cv)

        for i, b in enumerate(BRANDS):
            add_service(f"loader_{b}", cfg.load_time, {b: f"{b}_haul"}, 2 * i)
            if not cfg.shared_crusher:
                add_service(f"crusher_{b}", cfg.dump_time, {b: f"{b}_return"}, 2 * i + 1)
        if cfg.shared_crusher:
            add_service("crusher_shared", cfg.dump_time, {b: f"{b}_return" for b in BRANDS}, 9)
        self.strategy = make_strategy(self)

        # Metrics
        self.deliveries = {b: 0 for b in BRANDS}
        self.truck_delays: list[float] = []
        self.truck_delays_by_brand: dict[str, list[float]] = {b: [] for b in BRANDS}
        self.queue_waits: dict[str, list[float]] = {}   # "loader_A" / "crusher_B" -> waits (s)
        self.crossing_log: list[tuple] = []   # (t_exit, delay, "A" | "B" | "manual"), whole run
        self.delivery_log: list[tuple] = []   # (t, brand), whole run
        self.manual_delays: list[float] = []
        self.interventions = 0
        self.severe_interventions = 0
        self.hard_blocks = 0
        self.manual_crossings = 0
        self.cycle_times: list[float] = []
        self.trace: list[tuple] = []  # (t, vehicle id, lane, distance to zone entry, kind)
        self._last_loader_departure: dict[int, float] = {}

        for b in BRANDS:
            for _ in range(cfg.trucks_per_brand):
                truck = HaulTruck(self, b)
                self.vehicle_by_id[truck.unique_id] = truck
                self._enqueue(self.service[f"loader_{b}"], truck)

        self._manual_arrivals = deque(self._poisson_arrivals())
        self._manual_pending = 0

    # -- helpers used by agents ------------------------------------------------
    @property
    def measuring(self) -> bool:
        return self.t >= self.cfg.warmup

    def vehicles_on_lanes(self):
        for lane in self.lanes.values():
            yield from lane.vehicles

    def manual_vehicles(self):
        return list(self.lanes["M"].vehicles)

    def vehicles_waiting_at_line(self):
        return [v for v in self.vehicles_on_lanes() if v.waiting_since is not None and not v.entered]

    def record_crossing(self, vehicle: Vehicle, delay: float, t_exit: float) -> None:
        manual = isinstance(vehicle, ManualVehicle)
        self.crossing_log.append((t_exit, delay, "manual" if manual else vehicle.brand))
        if t_exit < self.cfg.warmup:
            return
        if manual:
            self.manual_delays.append(delay)
            self.manual_crossings += 1
        else:
            self.truck_delays.append(delay)
            self.truck_delays_by_brand[vehicle.brand].append(delay)

    def record_intervention(self, vehicle: Vehicle, severe: bool) -> None:
        if self.measuring:
            self.interventions += 1
            self.severe_interventions += int(severe)

    def arrive_at_service(self, truck: HaulTruck, t: float) -> None:
        if not truck.loaded:
            name = f"loader_{truck.brand}"
        else:
            name = "crusher_shared" if self.cfg.shared_crusher else f"crusher_{truck.brand}"
        self._enqueue(self.service[name], truck)

    def despawn(self, vehicle: Vehicle) -> None:
        self.vehicle_by_id.pop(vehicle.unique_id, None)
        vehicle.remove()

    # -- internals -------------------------------------------------------------
    def _enqueue(self, sp: ServicePoint, truck: HaulTruck) -> None:
        truck.state = f"queue_{sp.name}"
        truck.queued_at = self.t
        sp.queue.append(truck)

    def _poisson_arrivals(self) -> list[float]:
        rate = self.cfg.manual_rate_per_hour / 3600.0
        times, t = [], 0.0
        if rate <= 0:
            return times
        while True:
            t += self.arrival_rng.expovariate(rate)
            if t >= self.cfg.total_time:
                return times
            times.append(t)

    def _step_service_points(self) -> None:
        dt = self.cfg.dt
        for sp in self.service.values():
            if sp.current is not None and self.t >= sp.busy_until:
                truck = sp.current
                sp.current = None
                if sp.name.startswith("crusher"):
                    truck.loaded = False
                    self.delivery_log.append((self.t, truck.brand))
                    if self.measuring:
                        self.deliveries[truck.brand] += 1
                else:
                    truck.loaded = True
                truck.state = "ready"
                self.departures[sp.exit_lanes[truck.brand]].append(truck)
            if sp.current is None and sp.queue:
                truck = sp.pop_next(self.cfg.priority_rule, self.cfg.priority_brand)
                if self.measuring:
                    kind = "loader" if sp.name.startswith("loader") else "crusher"
                    self.queue_waits.setdefault(f"{kind}_{truck.brand}", []).append(self.t - truck.queued_at)
                sp.current = truck
                truck.state = sp.name
                sp.busy_until = self.t + sp.draw_service_time()
            if sp.current is not None and self.measuring:
                sp.busy_time += dt

    def _insert_departures(self) -> None:
        for name, q in self.departures.items():
            lane = self.lanes[name]
            if q and lane.entry_is_free(q[0].spec.length, self.cfg.standstill_gap):
                truck = q.popleft()
                if name.endswith("_haul"):  # leaving the loader: one full cycle done
                    prev = self._last_loader_departure.get(truck.unique_id)
                    if prev is not None and self.measuring:
                        self.cycle_times.append(self.t - prev)
                    self._last_loader_departure[truck.unique_id] = self.t
                truck.place_on(lane, speed=0.0)
                truck.state = "driving"

    def _spawn_manual(self) -> None:
        while self._manual_arrivals and self._manual_arrivals[0] <= self.t:
            self._manual_arrivals.popleft()
            self._manual_pending += 1
        lane = self.lanes["M"]
        spec = self.cfg.light_vehicle
        if self._manual_pending and lane.entry_is_free(spec.length, self.cfg.standstill_gap):
            self._manual_pending -= 1
            lv = ManualVehicle(self)
            self.vehicle_by_id[lv.unique_id] = lv
            lv.place_on(lane, speed=spec.v_max_empty)

    def step(self) -> None:
        self._step_service_points()
        self._insert_departures()
        self._spawn_manual()
        self.strategy.update(self.t)
        self.strategy.deliver_downlink(self.t)
        # Random activation order each step (Mesa 3 AgentSet API).
        self.agents.select(lambda a: a.lane is not None).shuffle_do("step")
        self.intersection.end_of_step(self.cfg.dt, self.measuring)
        if self.cfg.trace:
            for v in self.vehicles_on_lanes():
                kind = v.brand if isinstance(v, HaulTruck) else "manual"
                self.trace.append((self.t, v.unique_id, v.lane.name, v.pos - v.lane.zone_start, kind))
        self.t += self.cfg.dt

    def run(self) -> "MineModel":
        n_steps = int(round(self.cfg.total_time / self.cfg.dt))
        for _ in range(n_steps):
            self.step()
        return self

    # -- results -------------------------------------------------------------
    def summary(self) -> dict:
        cfg = self.cfg
        hours = cfg.horizon / 3600.0
        loaders = [self.service[f"loader_{b}"].busy_time / cfg.horizon for b in BRANDS]
        per_brand = [self.deliveries[b] / hours for b in BRANDS]
        td = self.truck_delays
        out = {
            "strategy": cfg.strategy,
            "seed": cfg.seed,
            "trucks_per_brand": cfg.trucks_per_brand,
            "comm_latency": cfg.comm_latency if cfg.strategy == "broker" else 0.0,
            "comm_loss": cfg.comm_loss if cfg.strategy == "broker" else 0.0,
            "manual_rate": cfg.manual_rate_per_hour,
            "dump_time": cfg.dump_time,
            "priority_rule": cfg.priority_rule,
            "shared_crusher": cfg.shared_crusher,
            "outage": cfg.outage_start is not None and cfg.strategy == "broker",
            "fallback": cfg.fallback,
            "throughput_per_h": sum(per_brand),
            "throughput_A_per_h": per_brand[0],
            "throughput_B_per_h": per_brand[1],
            "brand_fairness": (min(per_brand) / max(per_brand)) if max(per_brand) > 0 else 1.0,
            "loader_utilisation": mean(loaders),
            "crusher_utilisation": mean(sp.busy_time / cfg.horizon for name, sp in self.service.items()
                                        if name.startswith("crusher")),
            "cycle_time_mean_s": mean(self.cycle_times) if self.cycle_times else 0.0,
            "truck_delay_mean_s": mean(td) if td else 0.0,
            "truck_delay_p95_s": float(np.percentile(td, 95)) if td else 0.0,
            **{f"truck_delay_{b}_s": _mean(self.truck_delays_by_brand[b]) for b in BRANDS},
            **{f"crusher_wait_{b}_s": _mean(self.queue_waits.get(f"crusher_{b}", [])) for b in BRANDS},
            "manual_delay_mean_s": mean(self.manual_delays) if self.manual_delays else 0.0,
            "manual_crossings": self.manual_crossings,
            "interventions_per_h": self.interventions / hours,
            "severe_interventions_per_h": self.severe_interventions / hours,
            "hard_blocks": self.hard_blocks,
            "zone_utilisation": self.intersection.busy_time / cfg.horizon,
            "safety_violations": self.intersection.conflict_steps,
        }
        out.update(self.strategy.stats())
        return out


def _mean(xs: list[float]) -> float:
    return mean(xs) if xs else 0.0


def run(cfg: SimConfig) -> dict:
    """Run one simulation and return its summary metrics."""
    return MineModel(cfg).run().summary()
