"""
Pull pending live-control changes from the website and apply them (Stage 3a).

Users set their own live-trading controls on the website (live on/off, per-trade
margin cap, pause-to-paper). The web queues a `pending_controls` row; this PULLS
it over the shared-secret channel and applies it via the bot's UserStore (the
source of truth), then ACKs the APPLIED state back so the web UI mirrors it.

Safety: enabling live only flips the user-store ``can_trade_live`` flag — the bot's
``_can_trade_live`` gate STILL also requires the operator's env allowlist, so this
can never grant live access the operator hasn't pre-approved. The ack reports
``allowlisted`` separately so the UI can show "on, pending operator approval".

PAUSE-TO-PAPER IS GATED THE SAME WAY, AND WAS NOT
--------------------------------------------------
``paused`` sets ``sim_opt_in``, which ``engine.confirm_trade`` honours only when
``CONFIG.paper_sim_opt_in_enabled`` is true — and that env flag defaults to
**False**. So on a default deployment this stored the preference, acked
``paused: True``, and the website showed the user as paused while every confirmed
trade still went to the exchange. Believing your trades are simulated when they
are real is the worst direction for this to fail in.

Telegram never had the bug: ``/paper`` refuses up front and says the flag is off.
The web path just never asked. It now applies the same precondition and reports
``paper_mode_available`` alongside ``paused`` — the same shape as
``live_enabled``/``allowlisted`` above, for the same reason — plus
``paused_effective``, which is the only field that answers "will my next trade be
simulated?".

Turning pause OFF is always applied, whatever the flag says. Only the direction
that claims protection needs the feature to exist; refusing to clear a stale
opt-in would strand a user in a paper mode they asked to leave.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from bot.utils.credential_pull import _request, SYNC_SECRET  # reuse the channel

log = logging.getLogger(__name__)


def _coerce_bool(v):
    return None if v is None else bool(int(v)) if not isinstance(v, bool) else v


def _paper_mode_available() -> bool:
    """Will the engine actually honour ``sim_opt_in``?

    Imported lazily and fail-CLOSED: if the config cannot be read we report the
    feature as unavailable, so the ack never claims a protection whose status is
    unknown. `bot.utils` is imported early enough that a module-level
    `from bot.config import CONFIG` risks a cycle, hence the local import.
    """
    try:
        from bot.config import CONFIG
        return bool(CONFIG.paper_sim_opt_in_enabled)
    except Exception as exc:                                  # pragma: no cover
        log.warning("control pull: cannot read paper-mode config (%s) — "
                    "reporting paper mode UNAVAILABLE", exc)
        return False


def process_pending_controls(rows, store,
                             allowlist_check: Optional[Callable[[str], bool]] = None,
                             on_change: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Apply each pending control row to ``store``; return acks with applied state.

    NULL columns mean "leave unchanged". ``allowlist_check(telegram_id) -> bool``
    reports operator pre-approval (does NOT change behaviour here — the bot's gate
    enforces it — it's surfaced to the UI). ``on_change(telegram_id)`` fires after
    a successful apply so the caller can refresh per-user state.
    """
    acks: list[dict] = []
    paper_available = _paper_mode_available()
    for r in rows:
        uid = r.get("user_id")
        tg = str(r.get("telegram_id") or "")
        if uid is None or not tg:
            continue
        try:
            live = _coerce_bool(r.get("live_enabled"))
            paused = _coerce_bool(r.get("paused"))
            margin = r.get("max_margin")
            if live is not None:
                store.set_live_trading(tg, live)
            paused_rejected = None
            if paused is not None:
                # Storing a True the engine will ignore is what made the website
                # show "paused" over a live account. Refuse it instead, and say
                # so. Clearing the opt-in always applies.
                if paused and not paper_available:
                    paused_rejected = "paper_mode_disabled"
                    log.warning(
                        "control pull: user %s asked to pause to paper, but "
                        "PAPER_SIM_OPT_IN_ENABLED is off — NOT applied; their "
                        "confirmed trades still execute live", tg)
                else:
                    store.set_sim_opt_in(tg, paused)
            if margin is not None:
                m = float(margin)
                store.set_max_margin(tg, m if m > 0 else None)  # 0 clears the cap
            applied_margin = store.max_margin(tg)
            stored_pause = bool(store.sim_opt_in(tg))
            ack = {
                "user_id": uid,
                "live_enabled": bool(store.can_trade_live(tg)),
                "max_margin": applied_margin,
                "paused": stored_pause,
                # A stored opt-in from when the feature WAS enabled still reads
                # back True after the operator turns it off, so the stored flag
                # alone cannot answer the only question the user is asking.
                "paper_mode_available": paper_available,
                "paused_effective": stored_pause and paper_available,
                "allowlisted": bool(allowlist_check(tg)) if allowlist_check else False,
                "ok": True,
            }
            if paused_rejected:
                ack["paused_rejected"] = paused_rejected
            acks.append(ack)
            if on_change:
                on_change(tg)
        except Exception as exc:
            log.warning("control pull: failed row user=%s: %s", uid, exc)
            acks.append({"user_id": uid, "ok": False, "error": "processing error"})
    return acks


def pull_and_apply_controls(store=None, allowlist_check=None, on_change=None) -> int:
    """Fetch pending control changes, apply, ack. Returns #acked. No-op when the
    sync secret is unset (default deployment unaffected)."""
    if not SYNC_SECRET or store is None:
        return 0
    resp = _request("/api/bot/sync/controls/pending")
    rows = (resp or {}).get("pending", []) if resp else []
    if not rows:
        return 0
    acks = process_pending_controls(rows, store, allowlist_check=allowlist_check, on_change=on_change)
    if acks:
        _request("/api/bot/sync/controls/ack", {"acks": acks})
    return len(acks)


VALID_STANCE_MODES = frozenset({"defensive", "balanced", "aggressive", "manual"})


def pull_and_apply_stance(store=None) -> bool:
    """Fetch a web-queued strategy-stance change and apply it — ADMIN ONLY.

    Stance (RUNTIME.strategy_mode) is a GLOBAL operator setting, so this is
    deliberately stricter than the per-user controls above: the request is
    applied only when the requesting telegram user's tier in the bot's own
    UserStore (the tier authority) is 'admin'. Anything else is acked away
    (dropped) so a non-admin request can't sit in the queue forever. Returns
    True when a stance was actually applied.
    """
    if not SYNC_SECRET or store is None:
        return False
    resp = _request("/api/bot/sync/stance/pending")
    row = (resp or {}).get("pending") if resp else None
    if not row:
        return False
    mode = str(row.get("mode") or "").lower()
    tg = str(row.get("telegram_id") or "")
    applied = False
    try:
        if mode in VALID_STANCE_MODES and tg and store.get_tier(tg) == "admin":
            from bot.config import RUNTIME
            RUNTIME.strategy_mode = mode
            applied = True
            log.info("Web stance change applied: %s (by tg=%s)", mode, tg)
            try:
                from bot.core.agent_feed import FEED
                FEED.emit("stance", f"Stance changed to {mode.capitalize()}",
                          data={"mode": mode, "via": "web"})
            except Exception:
                pass
        else:
            log.warning("Web stance change REJECTED: mode=%r tg=%r tier=%r",
                        mode, tg, store.get_tier(tg) if tg else None)
    except Exception as exc:
        log.warning("stance pull: apply failed: %s", exc)
    # Always ack so the row clears — rejected requests must not retry forever.
    _request("/api/bot/sync/stance/ack", {"applied": applied, "mode": mode})
    return applied


def fetch_flatten_pending() -> list[dict]:
    """Fetch queued emergency-stop flatten requests. Empty when unconfigured."""
    if not SYNC_SECRET:
        return []
    resp = _request("/api/bot/sync/flatten/pending")
    return (resp or {}).get("pending", []) if resp else []


def ack_flatten(acks: list[dict]) -> None:
    """Clear completed flatten requests on the website (only ok=True rows clear)."""
    if acks and SYNC_SECRET:
        _request("/api/bot/sync/flatten/ack", {"acks": acks})
