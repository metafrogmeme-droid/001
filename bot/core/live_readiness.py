"""What must be true before a real order can reach the venue.

`/golive CONFIRM` used to arm live trading and verify NOTHING. It set
``RUNTIME.live_mode``, granted ``Permission.LIVE_TRADE``, and replied:

    🟢 LIVE TRADING ENABLED
    Real orders will execute on Bitget (USDT-M futures).

On a default install every one of those words is false, and three independent
mechanisms each make it false on their own:

  1. ``SIMULATION_MODE`` defaults to **true**, and
     ``_live_execution_vetoed_by_simulation()`` returns ``bool(CONFIG
     .simulation_mode)`` — explicitly "regardless of any runtime flag (e.g.
     RUNTIME.live_mode) that might otherwise arm live mode". Every order is
     rejected with "Trade REJECTED: SIMULATION_MODE=true".
  2. ``CONFIG.is_live()`` returns False when ``TELEGRAM_CHAT_ID`` is empty. It
     logs an ERROR — to a log file, not to the operator who just read a green
     banner.
  3. No exchange credential is checked at all. The boot credential preflight
     (``bot/main.py``) RETURNS EARLY when ``simulation_mode`` is set, so on a
     sim-booted bot the keys are never probed.

And the third one compounds: ``live_auth_healthy()`` defaults **True** for an
account it has never seen — correct as a detection default ("fail-open on
detection, fail-closed only on a confirmed failure"), and wrong as a readiness
answer. Never probed is not healthy. It is unknown, and this module keeps the
two apart rather than letting an unprobed account read as verified.

A green all-clear over a state that contradicts it, on the control that decides
whether real money moves. CLAUDE.md's rule, at the most expensive surface in
the product.

The core is pure and takes every input explicitly — `from_engine()` is the only
part that touches CONFIG or the engine, so the decision table can be driven
through every combination without constructing either.
"""
from __future__ import annotations

from typing import Any, Optional

# Live execution is impossible while any of these hold. Not warnings.
BLOCKER = "blocker"
# Live execution may work; something that should have been verified was not.
UNVERIFIED = "unverified"


def _item(code: str, detail: str, fix: str) -> dict[str, str]:
    return {"code": code, "detail": detail, "fix": fix}


def assess(
    *,
    simulation_mode: bool,
    chat_id: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
    auth_probed: bool,
    auth_healthy: bool,
    venue: str = "Bitget",
) -> dict[str, Any]:
    """Blockers and unverified preconditions for real order execution.

    Pure. ``can_execute`` is True only when nothing blocks — it is NOT a claim
    that a trade will succeed, only that no known gate refuses it up front.
    Unverified items never clear a blocker and never become one; they are the
    third outcome, and collapsing them into either is the defect this module
    was written for.
    """
    blockers: list[dict[str, str]] = []
    unverified: list[dict[str, str]] = []

    if simulation_mode:
        blockers.append(_item(
            "simulation_mode",
            "SIMULATION_MODE=true — the engine hard-vetoes every live order, "
            "independently of any runtime arm flag.",
            "Set SIMULATION_MODE=false in .env and restart. /golive alone "
            "cannot override it; that is the point of the switch.",
        ))

    if not str(chat_id or "").strip():
        blockers.append(_item(
            "no_chat_allowlist",
            "TELEGRAM_CHAT_ID is empty — CONFIG.is_live() refuses to arm live "
            "mode without a chat allow-list, and says so only in the log.",
            "Set TELEGRAM_CHAT_ID to the admin chat and restart.",
        ))

    missing = [n for n, v in (("BITGET_API_KEY", api_key),
                              ("BITGET_API_SECRET", api_secret),
                              ("BITGET_PASSPHRASE", passphrase))
               if not str(v or "").strip()]
    if missing:
        blockers.append(_item(
            "no_credentials",
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
            f"empty — the bot cannot authenticate to {venue}, so it can "
            "neither open a position nor place the stop that protects one.",
            "Set them in .env, or re-enter them with /setexchange (the vault "
            "recovery path for a redeploy that wiped .env).",
        ))

    if not auth_probed:
        # Deliberately NOT a blocker: the credentials may be perfectly good.
        # Deliberately NOT silence either: the boot preflight skips entirely in
        # simulation mode, so on a sim-booted bot nothing has ever asked the
        # venue whether these keys work.
        unverified.append(_item(
            "auth_never_probed",
            f"No authenticated call has been made to {venue} on this account, "
            "so the credentials are UNTESTED — the boot preflight skips when "
            "SIMULATION_MODE is set. `live_auth_healthy()` reads True here "
            "because unknown accounts default to allow, not because anything "
            "succeeded.",
            "Run /livebalance once armed — it makes an authenticated call and "
            "reports the venue's own answer.",
        ))
    elif not auth_healthy:
        blockers.append(_item(
            "auth_failing",
            f"The last authenticated call to {venue} failed, so new live "
            "entries are halted (open positions are still monitored).",
            "Check the startup credential diagnosis in the admin chat — it "
            "names the venue error code (40006 key, 40099 environment, 40012 "
            "passphrase).",
        ))

    return {
        "can_execute": not blockers,
        "blockers": blockers,
        "unverified": unverified,
    }


def from_engine(engine: Optional[Any] = None, config: Optional[Any] = None) -> dict[str, Any]:
    """`assess()` against the live CONFIG and engine. The only coupled part.

    Every read is defensive: a readiness check that raises tells the operator
    nothing, and this one is consulted precisely when something is wrong.
    An input that cannot be read is reported as its unsafe value, because an
    unreadable precondition has not been met — it has merely not been checked.
    """
    if config is None:
        from bot.config import CONFIG as config  # noqa: N813

    def _get(obj: Any, path: str, default: Any) -> Any:
        cur = obj
        for part in path.split("."):
            try:
                cur = getattr(cur, part)
            except Exception:
                return default
            if cur is None:
                return default
        return cur

    # simulation_mode defaults TRUE on an unreadable config: the unsafe answer
    # is "we are live", so an unknown must resolve to the blocking side.
    simulation_mode = bool(_get(config, "simulation_mode", True))

    auth_probed = False
    auth_healthy = True
    if engine is not None:
        try:
            auth_probed = bool(engine.live_auth_probed())
        except Exception:
            auth_probed = False
        try:
            auth_healthy = bool(engine.live_auth_healthy())
        except Exception:
            auth_healthy = True

    return assess(
        simulation_mode=simulation_mode,
        chat_id=str(_get(config, "telegram.chat_id", "") or ""),
        api_key=str(_get(config, "exchange.api_key", "") or ""),
        api_secret=str(_get(config, "exchange.api_secret", "") or ""),
        passphrase=str(_get(config, "exchange.passphrase", "") or ""),
        auth_probed=auth_probed,
        auth_healthy=auth_healthy,
    )


def mode_label(config: Optional[Any] = None) -> str:
    """LIVE / PAPER / IDLE — the trading mode, with the third value it needs.

    Three status surfaces derived this from ``simulation_mode`` ALONE::

        mode = "SIM" if CONFIG.simulation_mode else "LIVE"
        mode = "LIVE" if not CONFIG.simulation_mode else "PAPER"
        mode_str = "PAPER" if CONFIG.simulation_mode else "LIVE"

    But ``CONFIG.is_live()`` needs more than that flag being off: it also needs
    ``live_trading_enabled`` (or a runtime arm) AND a non-empty chat allow-list.
    So a bot with ``SIMULATION_MODE=false`` and ``LIVE_TRADING_ENABLED=false``
    — sim switched off, live not yet armed, which is exactly the staging
    posture the runbook describes — trades nothing at all while all three of
    those cards announce **LIVE**. One of them is fed to the LLM as engine
    state, so the assistant repeats it to the user in prose.

    IDLE is that state named: not simulating, not live, placing nothing.
    `_status_line` at telegram_handler.py:1238 already had all three values;
    the other sites are why this is a shared helper rather than a fourth copy.
    """
    if config is None:
        from bot.config import CONFIG as config  # noqa: N813
    try:
        if config.is_live():
            return "LIVE"
    except Exception:
        # Unreadable is not live. Claiming LIVE off a failed read is the
        # expensive direction of this exact mistake.
        return "UNKNOWN"
    try:
        return "PAPER" if config.simulation_mode else "IDLE"
    except Exception:
        return "UNKNOWN"


def render(report: dict[str, Any]) -> str:
    """The readiness report as Telegram HTML.

    Says what IS true. The banner it replaced said what the operator wanted to
    be true.
    """
    blockers = report.get("blockers") or []
    unverified = report.get("unverified") or []
    lines: list[str] = []

    if blockers:
        lines.append("🔴 <b>LIVE TRADING NOT ARMED</b>")
        lines.append("")
        lines.append(f"<b>No real order can execute</b> — "
                     f"{len(blockers)} blocker(s):")
        for b in blockers:
            lines.append("")
            lines.append(f"• <b>{b['code']}</b> — {b['detail']}")
            lines.append(f"  <i>Fix:</i> {b['fix']}")
    else:
        lines.append("🟢 <b>LIVE TRADING ARMED</b>")
        lines.append("")
        lines.append("No gate refuses a live order. That is not a promise a "
                     "trade will fill — it is the absence of a known blocker.")

    if unverified:
        lines.append("")
        lines.append(f"⚪ <b>Unverified ({len(unverified)})</b> — not a pass "
                     "and not a failure:")
        for u in unverified:
            lines.append("")
            lines.append(f"• <b>{u['code']}</b> — {u['detail']}")
            lines.append(f"  <i>Resolve:</i> {u['fix']}")

    return "\n".join(lines)
