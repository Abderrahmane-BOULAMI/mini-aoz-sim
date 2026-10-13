"""Road network: one-directional lanes that all cross one exclusive conflict zone.

Layout (top view)::

    Loader A ===== A_haul  ====>  [ ZONE ]  ====>  Crusher A
    Loader A <==== A_return <====  [ ZONE ]  <====  Crusher A
                                     ||
    Loader B ===== B_haul  ====>  [ ZONE ]  ====>  Crusher B
    Loader B <==== B_return <====  [ ZONE ]  <====  Crusher B
                                     ||
                    light-vehicle service road (M) crosses the zone

Roads A and B cross at the zone, and a service road used by manually driven
light vehicles crosses it too. The zone is modelled as a single exclusive
resource: at most one vehicle may occupy it at any time (conservative AOZ
rule, see README).

A position ``s`` is the distance (m) of the vehicle's *front* bumper from the
start of its lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vehicles import Vehicle


@dataclass(eq=False)
class Lane:
    name: str
    length: float
    zone_start: float
    zone_end: float
    stop_line: float
    vehicles: list["Vehicle"] = field(default_factory=list)

    def leader_of(self, vehicle: "Vehicle") -> "Vehicle | None":
        """Closest vehicle strictly ahead of ``vehicle`` on this lane."""
        ahead = [v for v in self.vehicles if v is not vehicle and v.pos > vehicle.pos]
        return min(ahead, key=lambda v: v.pos) if ahead else None

    def entry_is_free(self, length: float, gap: float) -> bool:
        """True if a vehicle can be inserted at s=0 without violating the gap."""
        if not self.vehicles:
            return True
        last = min(self.vehicles, key=lambda v: v.pos)
        return last.pos - last.spec.length >= gap

    def occupies_zone(self, front: float, length: float) -> bool:
        return front > self.zone_start and front - length < self.zone_end


def build_lanes(cfg) -> dict[str, Lane]:
    """Create the five lanes, each crossing the zone in its middle."""

    def lane(name: str, length: float) -> Lane:
        zs = length / 2 - cfg.zone_width / 2
        return Lane(name, length, zs, zs + cfg.zone_width, zs - cfg.stop_line_offset)

    L = cfg.haul_road_length
    return {
        "A_haul": lane("A_haul", L),
        "A_return": lane("A_return", L),
        "B_haul": lane("B_haul", L),
        "B_return": lane("B_return", L),
        "M": lane("M", cfg.service_road_length),
    }
