"""Longitudinal kinematics (point-mass, one dimension).

The speed update guarantees that a vehicle can always stop before its next
hard constraint (stop line, vehicle ahead, end of lane) using its comfortable
deceleration ``b``. With a time step ``dt`` the largest safe speed ``v`` for
the next step satisfies::

    v * dt + v**2 / (2 b) <= d      (d = free distance to the constraint)

whose positive root is ``v = -b dt + sqrt(b^2 dt^2 + 2 b d)``. This is the
same idea as Gipps' car-following model, without reaction time.
"""

from __future__ import annotations

import math

STOP_TOLERANCE = 0.1  # m, closer than this to a constraint counts as "stopped at it"


def safe_speed(free_distance: float, decel: float, dt: float) -> float:
    """Largest speed that still allows stopping within ``free_distance``."""
    if free_distance == math.inf:
        return math.inf
    d = max(free_distance, 0.0)
    return -decel * dt + math.sqrt((decel * dt) ** 2 + 2.0 * decel * d)


def next_speed(v: float, v_max: float, accel: float, decel: float,
               free_distance: float, dt: float) -> float:
    """Speed for the next step, limited by v_max, acceleration and safety."""
    if free_distance < STOP_TOLERANCE:
        return 0.0
    return max(0.0, min(v_max, v + accel * dt, safe_speed(free_distance, decel, dt)))


def braking_distance(v: float, decel: float) -> float:
    return v * v / (2.0 * decel)


def free_flow_leg_time(length: float, v_max: float, accel: float, decel: float) -> float:
    """Analytical time to drive a leg starting and ending at rest (continuous time).

    Used to validate the discrete model: accelerate at ``accel`` to ``v_max``,
    cruise, brake at ``decel`` to a stop.
    """
    d_acc = v_max**2 / (2 * accel)
    d_dec = v_max**2 / (2 * decel)
    if d_acc + d_dec > length:  # never reaches v_max (triangular profile)
        v_peak = math.sqrt(2 * length * accel * decel / (accel + decel))
        return v_peak / accel + v_peak / decel
    return length / v_max + v_max / (2 * accel) + v_max / (2 * decel)
