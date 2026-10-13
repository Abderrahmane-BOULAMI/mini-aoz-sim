"""Unreliable message channel with constant latency and random loss."""

from __future__ import annotations

import heapq
import itertools
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class StatusReport:
    """Limited intention information a vehicle shares with a coordinator."""

    vid: int
    leg: int            # identifies one crossing of the zone by one vehicle
    t_meas: float       # s, time the state was measured
    dist_to_stop: float  # m, distance from front bumper to the stop line
    speed: float        # m/s
    status: str         # "approaching" | "in_zone" | "cleared"
    loaded: bool
    is_manual: bool = False
    brand: str | None = None


@dataclass(frozen=True)
class Grant:
    """Permission to enter the zone for one crossing, valid until ``expiry``."""

    vid: int
    leg: int
    expiry: float       # s, absolute time (clocks assumed synchronised, e.g. GNSS)


@dataclass(frozen=True)
class Heartbeat:
    """Periodic broadcast proving the broker is alive. ``mode`` is "active" or "recovering"."""

    t_sent: float
    mode: str


class Channel:
    """FIFO channel: every message is delayed by ``latency`` or lost with ``loss``."""

    def __init__(self, latency: float, loss: float, rng: random.Random) -> None:
        if latency < 0 or not 0 <= loss < 1:
            raise ValueError("latency must be >= 0 and loss in [0, 1)")
        self.latency = latency
        self.loss = loss
        self.rng = rng
        self._queue: list = []
        self._seq = itertools.count()
        self.sent = 0
        self.lost = 0

    def send(self, msg, due: float) -> None:
        self.sent += 1
        if self.loss and self.rng.random() < self.loss:
            self.lost += 1
            return
        heapq.heappush(self._queue, (due + self.latency, next(self._seq), msg))

    def deliver(self, now: float) -> list:
        out = []
        while self._queue and self._queue[0][0] <= now + 1e-9:
            out.append(heapq.heappop(self._queue)[2])
        return out
