"""Coordination strategies for access to the shared conflict zone.

All strategies answer one question for an autonomous truck: *may I enter the
zone now?* They differ only in the information they rely on:

* ``LocalPriority``  - on-board perception only, no communication. Every
  vehicle stops at the line, then the site priority policy decides who goes
  (by default: first to arrive). Works across brands only if every brand
  implements the same rule.
* ``TokenCoordinator`` - a coordinator grants time-limited access leases
  (one holder at a time) based on vehicles' reported intentions.
    - ``central``: one omniscient site controller, no latency, no loss, and it
      also tracks manual vehicles. Idealised single-supplier benchmark.
    - ``broker``: a supplier-neutral broker that only receives the limited
      intention messages each brand's fleet system publishes, through an
      unreliable channel (latency + loss). Manual vehicles are invisible to it.
      The broker can fail (``outage_start``); trucks detect the missing
      heartbeat and fall back to local rules or stop (``fallback``).

Safety does not depend on the coordinator: every vehicle keeps an independent
perception-based safety layer (see ``HaulTruck.authorized_to_enter``), and
the lease protocol itself never lets two grants overlap in time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .communication import Channel, Grant, Heartbeat, StatusReport
from .policy import priority_rank

if TYPE_CHECKING:
    from .model import MineModel
    from .vehicles import HaulTruck


def local_rule_allows(model: "MineModel", truck: "HaulTruck", t: float) -> bool:
    """Stop, look, then go if the zone is free and nobody waiting has priority."""
    since = truck.waiting_since
    if since is None or t - since < model.cfg.auto_decision_delay:
        return False
    if not model.intersection.is_free:
        return False
    mine = truck.priority_key()
    return all(other.priority_key() >= mine
               for other in model.vehicles_waiting_at_line()
               if other is not truck)


class CoordinationStrategy(ABC):
    name: str = "abstract"

    def __init__(self, model: "MineModel") -> None:
        self.model = model
        self.cfg = model.cfg

    def update(self, t: float) -> None:
        """Called once per step, before vehicles move."""

    def deliver_downlink(self, t: float) -> None:
        """Deliver messages addressed to vehicles (if any)."""

    def report(self, truck: "HaulTruck", t: float) -> None:
        """Called by a truck after it moved, to share its state."""

    @abstractmethod
    def may_enter(self, truck: "HaulTruck", t: float) -> bool:
        """True if the coordination logic allows ``truck`` to enter now."""

    def coordinated(self, t: float) -> bool:
        """True if trucks currently follow a live coordinator (False = fallback mode)."""
        return False

    def stats(self) -> dict:
        return {}


class LocalPriority(CoordinationStrategy):
    """No communication at all."""

    name = "local"

    def may_enter(self, truck: "HaulTruck", t: float) -> bool:
        return local_rule_allows(self.model, truck, t)


class TokenCoordinator(CoordinationStrategy):
    """Lease-based exclusive access granted from reported intentions."""

    def __init__(self, model: "MineModel", name: str, latency: float, loss: float,
                 sees_manual: bool, can_fail: bool = False) -> None:
        super().__init__(model)
        self.name = name
        self.sees_manual = sees_manual
        self.uplink = Channel(latency, loss, model.comm_rng)
        self.downlink = Channel(latency, loss, model.comm_rng)
        self.info: dict[int, StatusReport] = {}
        self.holder: Grant | None = None
        self.holder_is_manual = False
        self.can_fail = can_fail and self.cfg.outage_start is not None
        self.was_down = False
        self.recovering_until = -1.0
        self.last_heartbeat: Heartbeat | None = None
        self.last_heartbeat_rx = -1e9
        self.grants_issued = 0
        self.lease_expiries = 0
        self.fallback_time = 0.0   # s (measured period) during which trucks were not coordinated

    # -- coordinator side --------------------------------------------------
    def is_down(self, t: float) -> bool:
        if not self.can_fail:
            return False
        start = self.cfg.outage_start
        return start <= t < start + self.cfg.outage_duration

    def update(self, t: float) -> None:
        if self.model.measuring and not self.coordinated(t):
            self.fallback_time += self.cfg.dt
        if self.is_down(t):
            self.uplink.deliver(t)  # a crashed broker loses whatever arrives
            self.was_down = True
            return
        if self.was_down:
            # Cold restart: state is lost, and leases granted before the crash may
            # still be valid, so stay passive for one lease duration.
            self.was_down = False
            self.info.clear()
            self.holder = None
            self.recovering_until = t + self.cfg.lease_duration

        for r in self.uplink.deliver(t):
            prev = self.info.get(r.vid)
            if prev is None or r.t_meas >= prev.t_meas:
                self.info[r.vid] = r
        if self.sees_manual:  # site tracking of light vehicles (central only)
            for lv in self.model.manual_vehicles():
                r = lv.status_report(t)
                if r is not None:
                    self.info[r.vid] = r

        mode = "recovering" if t < self.recovering_until else "active"
        self.downlink.send(Heartbeat(t, mode), due=t)
        if mode != "active":
            return
        self._release_if_done(t)
        if self.holder is None:
            self._grant_next(t)
        if self.holder is not None and not self.holder_is_manual:
            # Grants are re-sent every cycle, so a lost message only delays.
            self.downlink.send(self.holder, due=t)

    def _release_if_done(self, t: float) -> None:
        if self.holder is None:
            return
        r = self.info.get(self.holder.vid)
        if self.holder_is_manual:
            vehicle = self.model.vehicle_by_id.get(self.holder.vid)
            if vehicle is None or (r is not None and r.status == "cleared"):
                self.holder = None
            return
        if r is not None and r.leg == self.holder.leg and r.status == "cleared":
            self.holder = None
        elif t >= self.holder.expiry:
            # No clearance confirmation received in time. The lease protocol
            # guarantees the vehicle is out of the zone, so reuse is safe.
            self.lease_expiries += 1
            self.holder = None

    def _grant_next(self, t: float) -> None:
        fresh = [r for r in self.info.values() if t - r.t_meas <= self.cfg.stale_after]
        if any(r.status == "in_zone" for r in fresh):
            return  # someone (e.g. a truck in fallback mode) is reported inside the zone
        cfg = self.cfg
        best, best_key = None, None
        for r in fresh:
            if r.status != "approaching":
                continue
            travelled = r.speed * (t - r.t_meas)
            eta = max(0.0, r.dist_to_stop - travelled) / max(r.speed, 1.0)
            if eta > cfg.grant_lookahead:
                continue
            rank = priority_rank(cfg.priority_rule, brand=r.brand, loaded=r.loaded,
                                 is_manual=r.is_manual, favoured=cfg.priority_brand)
            key = (rank, eta, 0 if r.loaded else 1, r.vid)
            if best_key is None or key < best_key:
                best, best_key = r, key
        if best is None:
            return
        self.holder = Grant(best.vid, best.leg, t + cfg.lease_duration)
        self.holder_is_manual = best.is_manual
        self.grants_issued += 1

    def deliver_downlink(self, t: float) -> None:
        for msg in self.downlink.deliver(t):
            if isinstance(msg, Heartbeat):
                self.last_heartbeat, self.last_heartbeat_rx = msg, t
                continue
            vehicle = self.model.vehicle_by_id.get(msg.vid)
            if vehicle is not None:
                vehicle.receive_grant(msg)

    # -- vehicle side ------------------------------------------------------
    def coordinated(self, t: float) -> bool:
        """Trucks trust the broker only if a recent heartbeat says it is active.

        The heartbeat is one radio broadcast, so all trucks switch mode together.
        """
        hb = self.last_heartbeat
        return (hb is not None and hb.mode == "active"
                and t - self.last_heartbeat_rx <= self.cfg.heartbeat_timeout)

    def report(self, truck: "HaulTruck", t: float) -> None:
        r = truck.status_report(t)
        if r is not None:
            # Reports reach the coordinator at the next control cycle at the earliest.
            self.uplink.send(r, due=t + self.cfg.dt)

    def _lease_valid(self, truck: "HaulTruck", t: float) -> bool:
        g = truck.grant
        return (g is not None and g.leg == truck.leg
                and t + truck.crossing_time_bound() <= g.expiry)

    def may_enter(self, truck: "HaulTruck", t: float) -> bool:
        if self._lease_valid(truck, t):
            return True   # a valid lease stays valid even if the broker is gone
        if self.coordinated(t):
            return False
        if self.cfg.fallback == "local":
            return local_rule_allows(self.model, truck, t)
        return False      # fallback == "stop": fail-safe, wait for the broker

    def stats(self) -> dict:
        sent = self.uplink.sent + self.downlink.sent
        lost = self.uplink.lost + self.downlink.lost
        return {"messages_sent": sent, "messages_lost": lost,
                "grants_issued": self.grants_issued, "lease_expiries": self.lease_expiries,
                "fallback_share": self.fallback_time / self.cfg.horizon}


def make_strategy(model: "MineModel") -> CoordinationStrategy:
    cfg = model.cfg
    if cfg.strategy == "local":
        return LocalPriority(model)
    if cfg.strategy == "central":
        return TokenCoordinator(model, "central", latency=0.0, loss=0.0, sees_manual=True)
    if cfg.strategy == "broker":
        return TokenCoordinator(model, "broker", cfg.comm_latency, cfg.comm_loss,
                                sees_manual=False, can_fail=True)
    raise ValueError(f"unknown strategy {cfg.strategy!r}")
