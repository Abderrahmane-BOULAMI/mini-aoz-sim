"""Simulation parameters.

Every number used by the model lives here, with its unit. Values are
illustrative assumptions for a small Autonomous Operation Zone (AOZ), not
measured data from a real mine. See README section "Assumptions".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class VehicleSpec:
    """Kinematic and geometric parameters of a vehicle class."""

    length: float          # m, bumper to bumper
    v_max_loaded: float    # m/s
    v_max_empty: float     # m/s
    accel: float           # m/s^2, nominal acceleration
    decel: float           # m/s^2, comfortable (planned) braking
    decel_emergency: float  # m/s^2, physical braking limit


HAUL_TRUCK = VehicleSpec(
    length=12.0,
    v_max_loaded=5.0,    # 18 km/h on a loaded haul segment
    v_max_empty=10.0,    # 36 km/h empty
    accel=0.5,
    decel=1.0,
    decel_emergency=2.5,
)

LIGHT_VEHICLE = VehicleSpec(
    length=5.0,
    v_max_loaded=12.0,
    v_max_empty=12.0,
    accel=1.5,
    decel=2.5,
    decel_emergency=5.0,
)


@dataclass(frozen=True)
class SimConfig:
    """Full configuration of one simulation run."""

    # --- Experiment -------------------------------------------------------
    seed: int = 1
    dt: float = 1.0                # s per step
    warmup: float = 900.0          # s, discarded from statistics
    horizon: float = 7200.0        # s, measured period after warm-up
    trace: bool = False            # record every vehicle position (for plots)

    # --- Layout -----------------------------------------------------------
    haul_road_length: float = 500.0   # m, loader -> crusher (one leg)
    zone_width: float = 20.0          # m, exclusive intersection conflict zone
    stop_line_offset: float = 2.0     # m, stop line upstream of the zone
    service_road_length: float = 200.0  # m, light-vehicle crossing road

    # --- Fleet and operations ---------------------------------------------
    trucks_per_brand: int = 4
    load_time: float = 120.0       # s, loader service time
    dump_time: float = 60.0        # s, crusher service time
    service_cv: float = 0.15       # coefficient of variation of load/dump times (0 = deterministic)
    shared_crusher: bool = False   # both brands dump at one crusher (shared queue)
    standstill_gap: float = 8.0    # m, min gap to the vehicle ahead
    manual_rate_per_hour: float = 6.0  # light-vehicle crossings / h (Poisson)

    # --- Coordination -----------------------------------------------------
    strategy: str = "broker"       # "local" | "central" | "broker"
    comm_latency: float = 1.0      # s, one-way, broker only
    comm_loss: float = 0.05        # probability a message is lost, broker only
    control_horizon: float = 150.0  # m before the stop line where vehicles report
    lease_duration: float = 40.0   # s, validity of an access grant
    grant_lookahead: float = 10.0  # s, only vehicles this close (ETA) can receive a grant
    stale_after: float = 10.0      # s, broker ignores older vehicle reports
    auto_decision_delay: float = 2.0    # s, autonomous stop-and-check (local rule)
    human_decision_delay: float = 3.0   # s, manual driver stop-and-look
    human_commit_distance: float = 30.0  # m, driver waits if a truck is this close and moving

    # --- Site priority policy (intersection and shared crusher queue) -----
    priority_rule: str = "fifo"    # "fifo" | "loaded_first" | "brand_priority"
    priority_brand: str = "A"      # favoured brand when priority_rule == "brand_priority"

    # --- Broker outage and fallback -----------------------------------------
    outage_start: float | None = None  # s, absolute time the broker goes down (None = never)
    outage_duration: float = 600.0     # s
    fallback: str = "local"            # what trucks do without a live broker: "local" | "stop"
    heartbeat_timeout: float = 5.0     # s without broker heartbeat before trucks fall back

    trucks: VehicleSpec = field(default=HAUL_TRUCK)
    light_vehicle: VehicleSpec = field(default=LIGHT_VEHICLE)

    def with_(self, **changes) -> "SimConfig":
        """Return a modified copy (configs are immutable)."""
        return replace(self, **changes)

    @property
    def total_time(self) -> float:
        return self.warmup + self.horizon
