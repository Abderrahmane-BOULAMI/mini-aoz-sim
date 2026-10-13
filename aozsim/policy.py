"""Site priority policy shared by every coordination strategy and by queues.

A lower rank goes first. Inside the same rank, the earliest vehicle goes first
(arrival at the line for local rules, estimated time of arrival for coordinators).
"""

from __future__ import annotations

RULES = ("fifo", "loaded_first", "brand_priority")


def priority_rank(rule: str, *, brand: str | None, loaded: bool, is_manual: bool,
                  favoured: str) -> int:
    if rule == "fifo":
        return 0
    if rule == "loaded_first":
        return 0 if (loaded or is_manual) else 1   # manned vehicles are never made to wait
    if rule == "brand_priority":
        return 0 if (is_manual or brand == favoured) else 1
    raise ValueError(f"unknown priority rule {rule!r}, expected one of {RULES}")
