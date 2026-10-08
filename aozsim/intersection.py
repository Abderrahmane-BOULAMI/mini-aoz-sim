"""The shared conflict zone where roads A, B and the service road cross."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .vehicles import Vehicle


class Intersection:
    """Exclusive conflict zone. Tracks occupancy and checks the safety invariant."""

    def __init__(self) -> None:
        self.occupants: set["Vehicle"] = set()
        self.conflict_steps = 0      # steps with >1 occupant: must stay 0
        self.busy_time = 0.0         # s occupied during the measured period
        self.entries = 0

    def is_free_for(self, vehicle: "Vehicle") -> bool:
        return not (self.occupants - {vehicle})

    @property
    def is_free(self) -> bool:
        return not self.occupants

    def enter(self, vehicle: "Vehicle") -> None:
        self.occupants.add(vehicle)
        self.entries += 1

    def exit(self, vehicle: "Vehicle") -> None:
        self.occupants.discard(vehicle)

    def end_of_step(self, dt: float, measuring: bool) -> None:
        if len(self.occupants) > 1:
            self.conflict_steps += 1
        if measuring and self.occupants:
            self.busy_time += dt
