"""Cap decreases. A fork may hand the plan to an injected broadcaster.

This module holds no key and does not sign. On a mainnet chain id the same
decrease is returned as an unsigned proposal and the broadcaster is not
called. The flag must be on, and the pause switch stops both paths. A
current cap that was not read is not treated as zero, and a proposed cap
that is not strictly lower is not sent.
"""

from __future__ import annotations

from typing import Callable

from bot.rxb.chains import MAINNET_CHAIN_IDS, signing_allowed
from bot.rxb.flag import flag_on
from bot.rxb.mainnet import pause_on
from bot.vaults.cap_coordinator import coordinate_caps
from bot.vaults.cap_id import decrease_calldata, delay_refusal

Broadcaster = Callable[[list[dict]], None]


def _stop(reason: str, plan: list | None = None) -> dict:
    return {"sent": False, "signed": False, "reason": reason, "plan": list(plan or [])}


def _unsigned(plan: list[dict]) -> dict:
    rows = []
    for row in plan:
        item = dict(row)
        item["calldata"] = decrease_calldata(str(row.get("kind")), row.get("id_data"), row.get("cap"))
        rows.append(item)
    return {
        "sent": False,
        "signed": False,
        "broadcast": False,
        "activated": False,
        "reason": "unsigned_proposal",
        "plan": rows,
    }


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
    increase_delay: object = None,
) -> dict:
    """Coordinate, then send only a strict decrease. Never an increase.

    ``increase_delay`` is the vault's cap-increase timelock in seconds.
    Vault V2 defaults it to 0. Under 72 hours, or unread, this refuses.
    """
    if not flag_on(env):
        return _stop("flag_off")
    if pause_on(env):
        return _stop("paused")
    refusal = delay_refusal(increase_delay)
    if refusal is not None:
        return _stop(refusal)
    if isinstance(current_absolute, bool) or (
        current_absolute is not None and (not isinstance(current_absolute, int) or current_absolute < 0)
    ):
        return _stop("current_unreadable")
    if isinstance(current_relative, bool) or (
        current_relative is not None and (not isinstance(current_relative, int) or current_relative < 0)
    ):
        return _stop("current_unreadable")

    decision = coordinate_caps(proposals, audit_path)
    rows = [d for d in decision["decisions"] if d["vault"] == vault and d["id_data"] == id_data]
    if not rows:
        return _stop("no_opinion")
    chosen = rows[0]
    if chosen["readable"] is not True:
        return _stop("unreadable")

    plan: list[dict] = []
    if chosen["absolute"] is not None:
        if current_absolute is None:
            return _stop("current_unreadable")
        if chosen["absolute"] < current_absolute:
            plan.append({
                "kind": "absolute",
                "cap": chosen["absolute"],
                "id_data": id_data,
                "cap_id": chosen.get("cap_id"),
            })
    if chosen["relative"] is not None:
        if current_relative is None:
            return _stop("current_unreadable")
        if chosen["relative"] < current_relative:
            plan.append({
                "kind": "relative",
                "cap": chosen["relative"],
                "id_data": id_data,
                "cap_id": chosen.get("cap_id"),
            })
    if not plan:
        return _stop("no_decrease")
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return _stop("chain_refusal")
    if chain_id in MAINNET_CHAIN_IDS:
        return _unsigned(plan)
    if not signing_allowed(chain_id):
        return _stop("chain_refusal")
    if broadcaster is None:
        return _stop("no_broadcaster", plan)
    broadcaster(plan)
    return {"sent": True, "signed": False, "reason": None, "plan": plan}
