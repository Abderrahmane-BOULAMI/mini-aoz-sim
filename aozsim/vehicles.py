"""Vehicle agents: autonomous haul trucks (two brands) and manual light vehicles."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import mesa

from .communication import Grant, StatusReport
from .config import VehicleSpec
from .kinematics import STOP_TOLERANCE, braking_distance, next_speed
from .policy import priority_rank

if TYPE_CHECKING:
    from .geometry import Lane
    from .model import MineModel

MEASURE_UPSTREAM = 120.0  # m before the zone where intersection delay starts to count
REPORT_DOWNSTREAM = 40.0  # m after clearing the zone during which vehicles keep reporting


class Vehicle(mesa.Agent):
    """A vehicle moving along one lane at a time and crossing the zone once per lane."""

    model: "MineModel"
    stops_at_lane_end = True

    def __init__(self, model: "MineModel", spec: VehicleSpec) -> None:
        super().__init__(model)
        self.spec = spec
        self.lane: Lane | None = None
        self.pos = 0.0
        self.v = 0.0
        self.leg = 0
        self.loaded = False
        self._reset_leg()

    # -- lifecycle -----------------------------------------------------------
    def _reset_leg(self) -> None:
        self.entered = False
        self.in_zone = False
        self.cleared = False
        self.waiting_since: float | None = None
        self.t_meas_start: float | None = None
        self.s_meas_start = 0.0
        self.grant: Grant | None = None
        self.intervened = False

    def place_on(self, lane: "Lane", speed: float) -> None:
        lane.vehicles.append(self)
        self.lane, self.pos, self.v = lane, 0.0, speed
        self.leg += 1
        self._reset_leg()

    def leave_lane(self) -> None:
        if self.lane is not None:
            self.lane.vehicles.remove(self)
        self.lane = None

    # -- properties used by other agents ----------------------------------
    @property
    def v_max(self) -> float:
        return self.spec.v_max_loaded if self.loaded else self.spec.v_max_empty

    @property
    def is_entering(self) -> bool:
        """Physically committed: too close to the zone to stop before it (visible to perception)."""
        if self.lane is None or self.entered or self.v <= 0.0:
            return False
        return self.lane.zone_start - self.pos < braking_distance(self.v, self.spec.decel) + STOP_TOLERANCE

    @property
    def brand(self) -> str | None:
        return None

    def rank(self) -> int:
        cfg = self.model.cfg
        return priority_rank(cfg.priority_rule, brand=self.brand, loaded=self.loaded,
                             is_manual=isinstance(self, ManualVehicle), favoured=cfg.priority_brand)

    def priority_key(self) -> tuple:
        """Site policy rank, then first at the line; loaded trucks win exact ties."""
        return (self.rank(), self.waiting_since, 0 if self.loaded else 1, self.unique_id)

    def status_report(self, t: float) -> StatusReport | None:
        lane = self.lane
        if lane is None:
            return None
        dist = lane.stop_line - self.pos
        if dist > self.model.cfg.control_horizon:
            return None
        if self.cleared and self.pos - self.spec.length > lane.zone_end + REPORT_DOWNSTREAM:
            return None
        status = "cleared" if self.cleared else "in_zone" if self.in_zone else "approaching"
        return StatusReport(self.unique_id, self.leg, t, max(dist, 0.0), self.v, status,
                            self.loaded, is_manual=isinstance(self, ManualVehicle), brand=self.brand)

    def receive_grant(self, grant: Grant) -> None:
        if grant.leg == self.leg and not self.entered:
            self.grant = grant

    def zone_blocked(self) -> bool:
        """Perception check: zone occupied, or another vehicle visibly entering it."""
        m = self.model
        if not m.intersection.is_free_for(self):
            return True
        return any(o is not self and o.is_entering for o in m.vehicles_on_lanes())

    # -- behaviour hooks -----------------------------------------------------
    def authorized_to_enter(self, t: float) -> bool:
        raise NotImplementedError

    def on_lane_end(self, t: float) -> None:
        raise NotImplementedError

    def after_move(self, t: float) -> None:
        """Hook called after the position update (e.g. to report status)."""

    # -- main update -----------------------------------------------------------
    def step(self) -> None:
        lane = self.lane
        if lane is None:
            return
        m, cfg, t = self.model, self.model.cfg, self.model.t

        if self.t_meas_start is None and self.pos >= max(0.0, lane.zone_start - MEASURE_UPSTREAM):
            self.t_meas_start, self.s_meas_start = t, self.pos

        # 1) Hard constraints ahead of the front bumper.
        free = lane.length - self.pos if self.stops_at_lane_end else math.inf
        leader = lane.leader_of(self)
        if leader is not None:
            free = min(free, leader.pos - leader.spec.length - cfg.standstill_gap - self.pos)
        if not self.entered and not self.authorized_to_enter(t):
            free = min(free, lane.stop_line - self.pos)

        # 2) Speed and position update.
        v_new = next_speed(self.v, self.v_max, self.spec.accel, self.spec.decel, free, cfg.dt)
        new_pos = self.pos + v_new * cfg.dt

        # 3) Last-resort physical check: never enter an occupied zone.
        if (not self.entered and new_pos > lane.zone_start
                and not m.intersection.is_free_for(self)):
            new_pos, v_new = min(new_pos, lane.zone_start), 0.0
            m.hard_blocks += 1
        self.pos, self.v = new_pos, v_new

        # 4) Bookkeeping: waiting at the line, zone entry and exit.
        if (not self.entered and self.v == 0.0
                and lane.stop_line - self.pos < STOP_TOLERANCE and self.waiting_since is None):
            self.waiting_since = t
        occupying = lane.occupies_zone(self.pos, self.spec.length)
        if occupying and not self.in_zone:
            self.entered = self.in_zone = True
            m.intersection.enter(self)
        elif self.in_zone and not occupying:
            self.in_zone, self.cleared = False, True
            m.intersection.exit(self)
            self._record_crossing(t + cfg.dt)

        self.after_move(t)
        if self.lane is not None and (
                (self.stops_at_lane_end and lane.length - self.pos < STOP_TOLERANCE and self.v == 0.0)
                or self.pos >= lane.length):
            self.on_lane_end(t)

    def _record_crossing(self, t_exit: float) -> None:
        if self.t_meas_start is None:
            return
        distance = self.lane.zone_end + self.spec.length - self.s_meas_start
        delay = (t_exit - self.t_meas_start) - distance / self.v_max
        self.model.record_crossing(self, max(delay, 0.0), t_exit)


class HaulTruck(Vehicle):
    """Autonomous haul truck cycling loader -> crusher -> loader for one brand."""

    def __init__(self, model: "MineModel", brand: str) -> None:
        self._brand = brand
        super().__init__(model, model.cfg.trucks)
        self.state = "init"
        self.queued_at = 0.0

    @property
    def brand(self) -> str:
        return self._brand

    def authorized_to_enter(self, t: float) -> bool:
        go = self.model.strategy.may_enter(self, t)
        if go and self.zone_blocked():
            # Independent safety layer overrides the coordinator.
            go = False
            dist = self.lane.stop_line - self.pos
            must_act = dist <= braking_distance(self.v, self.spec.decel) + self.v * self.model.cfg.dt + 2.0
            if must_act and self.v > 0.5 and not self.intervened:
                self.intervened = True
                required = self.v ** 2 / (2 * max(dist, 1e-3))
                self.model.record_intervention(self, severe=required > self.spec.decel_emergency)
        return go

    def crossing_time_bound(self) -> float:
        """Upper bound on the time needed to clear the zone, starting from rest."""
        dist = max(self.lane.zone_end + self.spec.length - self.pos, 0.0)
        a, v = self.spec.accel, self.v_max
        if v * v / (2 * a) >= dist:
            t_needed = math.sqrt(2 * dist / a)
        else:
            t_needed = v / a + (dist - v * v / (2 * a)) / v
        return t_needed + 2 * self.model.cfg.dt

    def after_move(self, t: float) -> None:
        self.model.strategy.report(self, t)

    def on_lane_end(self, t: float) -> None:
        self.leave_lane()
        self.model.arrive_at_service(self, t)


class ManualVehicle(Vehicle):
    """Manually driven light vehicle crossing the zone on the service road.

    The driver follows the site rule (stop at the line, look, go when clear) and
    is never connected to any coordinator.
    """

    stops_at_lane_end = False

    def __init__(self, model: "MineModel") -> None:
        super().__init__(model, model.cfg.light_vehicle)
        self.going = False

    def _reset_leg(self) -> None:
        super()._reset_leg()
        self.going = False

    def authorized_to_enter(self, t: float) -> bool:
        m, cfg = self.model, self.model.cfg
        if not self.going:
            if self.waiting_since is None or t - self.waiting_since < cfg.human_decision_delay:
                return False
            if not m.intersection.is_free or self._truck_coming():
                return False
            self.going = True
        return m.intersection.is_free_for(self)

    def _truck_coming(self) -> bool:
        reach = self.model.cfg.human_commit_distance
        for o in self.model.vehicles_on_lanes():
            if isinstance(o, HaulTruck) and not o.entered and o.v > 0.5:
                if o.lane.zone_start - o.pos <= reach:
                    return True
        return False

    @property
    def is_entering(self) -> bool:
        return self.going and not self.entered

    def on_lane_end(self, t: float) -> None:
        self.leave_lane()
        self.model.despawn(self)
