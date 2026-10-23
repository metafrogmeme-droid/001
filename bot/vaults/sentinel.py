"""Fork-only cap decrease.

The broadcaster is injected. This module holds no key. A mainnet chain id
returns before the broadcaster is asked. The flag must be on. A current
cap that was not read is not treated as zero, and a proposed cap that is
not strictly lower is not sent.
"""

from __future__ import annotations

from typing import Callable

from bot.rxb.chains import signing_allowed
from bot.rxb.flag import flag_on
from bot.vaults.cap_coordinator import coordinate_caps

Broadcaster = Callable[[list[dict]], None]


def plan_cap_down(
    *,
    chain_id: int,
    vault: str,
    id_data: str,
    proposals: list[dict],
    current_absolute: int | None,
    current_relative: int | None,
    broadcaster: Broadcaster | None,
    audit_path: str | None,
    env: dict | None = None,
) -> dict:
    """Coordinate, then send only a strict decrease. Never an increase."""
    if not flag_on(env):
        return {"sent": False, "reason": "flag_off", "plan": []}
    if not signing_allowed(chain_id):
        return {"sent": False, "reason": "mainnet_refusal", "plan": []}
    if isinstance(current_absolute, bool) or (
        current_absolute is not None and (not isinstance(current_absolute, int) or current_absolute < 0)
    ):
        return {"sent": False, "reason": "current_unreadable", "plan": []}
    if isinstance(current_relative, bool) or (
        current_relative is not None and (not isinstance(current_relative, int) or current_relative < 0)
    ):
        return {"sent": False, "reason": "current_unreadable", "plan": []}

    decision = coordinate_caps(proposals, audit_path)
    rows = [d for d in decision["decisions"] if d["vault"] == vault and d["id_data"] == id_data]
    if not rows:
        return {"sent": False, "reason": "no_opinion", "plan": []}
    chosen = rows[0]
    if chosen["readable"] is not True:
        return {"sent": False, "reason": "unreadable", "plan": []}

    plan: list[dict] = []
    if chosen["absolute"] is not None:
        if current_absolute is None:
            return {"sent": False, "reason": "current_unreadable", "plan": []}
        if chosen["absolute"] < current_absolute:
            plan.append({"kind": "absolute", "cap": chosen["absolute"]})
    if chosen["relative"] is not None:
        if current_relative is None:
            return {"sent": False, "reason": "current_unreadable", "plan": []}
        if chosen["relative"] < current_relative:
            plan.append({"kind": "relative", "cap": chosen["relative"]})
    if not plan:
        return {"sent": False, "reason": "no_decrease", "plan": []}
    if broadcaster is None:
        return {"sent": False, "reason": "no_broadcaster", "plan": plan}
    broadcaster(plan)
    return {"sent": True, "reason": None, "plan": plan}
