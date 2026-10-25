"""Testnet book chosen by internal review 2026-10-24.

The house scan (the hourly book) is what a live mainnet process runs, at the
leverage, streak reset and post-loss wait ``bot.config`` already declares.
A venue testnet, or a paper process that opts in, names B1v as its primary
book and tightens the hourly book around it. Live mainnet never takes those
overrides, including when the opt-in is set and including when live was armed
at runtime rather than in the environment.

B1v is the daily long-only trend rule (H1b): lookbacks 20, 60 and 120 days,
a 10% rebalance band, a 20% vol target, a 60-day vol lookback, and a leverage
cap of 2x. The sleeve still places nothing; the engine's daily record is
written with that spec when B1v is primary. The hourly book keeps its order
path, sized at 2x, with the streak breaker clearing itself after 24 hours so
a testnet bot does not latch, and with the post-loss wait split by symbol.

The cooldown trial is not a result. Half of symbols, chosen by a stable hash
of the base asset, wait 14400 seconds. The other half keep the configured
wait (120 seconds when nobody has changed it). The account-wide scan pause
stays on the configured wait: a four-hour pause after one symbol's loss
would stop the control half as well, and the trial would measure nothing.
The entry gate is the arm. An unreadable symbol is not assigned.

Revert the whole profile with ``TESTNET_BOOK_PROFILE=0``. On a paper process
that is not a venue testnet the profile stays off until
``TESTNET_BOOK_PROFILE=1``. ``PRIMARY_BOOK=hourly`` on a profile that is on
keeps the pre-registered 30-day sleeve as the recorded book and leaves the
hourly overrides in place.
"""
from __future__ import annotations

import hashlib
import os
from typing import Mapping, NamedTuple, Optional

from bot.utils.logger import audit, scan_log, trade_log

REVIEW = "internal-review-2026-10-24"
HOURLY_BOOK = "hourly"
B1V_BOOK = "b1v"
LIVE = "live"
TESTNET = "testnet"
PAPER = "paper"

TESTNET_SIZING_LEVERAGE = 2
TESTNET_STREAK_HOURS = 24.0
TREATMENT_COOLDOWN_SEC = 14400

ARM_TREATMENT = "cooldown_14400"
ARM_CONTROL = "control"
ARM_OFF = "off"
ARM_UNREAD = "unread"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class B1vSpec(NamedTuple):
    """The B1v book. ``weight_cap`` is the sleeve's per-name notional cap.
    ``leverage_cap`` is the leverage the book is sized at."""

    lookbacks: tuple[int, ...]
    vol_window: int
    target_vol: float
    band: float
    weight_cap: float
    leverage_cap: int


class CooldownDecision(NamedTuple):
    """The wait one entry is held to, and the arm that chose it.

    ``arm`` is ``off`` when the trial is not running, and ``unread`` when
    the symbol did not read. Neither of those is an assignment.
    """

    seconds: int
    arm: str
    symbol: str
    configured: int


def b1v_parameters() -> B1vSpec:
    """The B1v spec from internal review 2026-10-24. One reading."""
    return B1vSpec(
        lookbacks=(20, 60, 120),
        vol_window=60,
        target_vol=0.20,
        band=0.10,
        weight_cap=1.0,
        leverage_cap=TESTNET_SIZING_LEVERAGE,
    )


def _mapping(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _raw(env: Mapping[str, str], name: str) -> Optional[str]:
    if name not in env:
        return None
    text = str(env.get(name, "")).strip()
    return text if text else None


def _on(env: Mapping[str, str], name: str, *, absent: bool) -> bool:
    raw = _raw(env, name)
    if raw is None:
        return absent
    return raw.lower() in _TRUE


def deployment_kind(env: Optional[Mapping[str, str]] = None) -> str:
    """``live``, ``testnet`` or ``paper``, from the environment only.

    A passed mapping is the whole environment. ``{}`` is nothing set, not
    this process. Live mainnet is live flags on, simulation off, and neither
    venue testnet flag. A live flag on a venue testnet is testnet. Anything
    else is paper, which is also the state when the variables are absent:
    simulation ships on, live ships off.
    """
    e = _mapping(env)
    simulation = _on(e, "SIMULATION_MODE", absent=True)
    live_enabled = _on(e, "LIVE_TRADING_ENABLED", absent=False)
    sandbox = (_on(e, "BITGET_SANDBOX", absent=False)
               or _on(e, "HYPERLIQUID_TESTNET", absent=False))
    if live_enabled and not simulation and not sandbox:
        return LIVE
    if sandbox:
        return TESTNET
    return PAPER


def _runtime_is_live_mainnet() -> bool:
    """Whether this process is live on mainnet right now.

    ``/golive`` arms live without rewriting the environment. The frozen
    fields would still say paper, and a paper opt-in would then size a live
    mainnet order. A venue testnet that is live is not mainnet.
    """
    try:
        from bot.config import CONFIG
    except Exception:
        return False
    try:
        live = bool(CONFIG.is_live())
    except Exception:
        return False
    if not live:
        return False
    ex = getattr(CONFIG, "exchange", None)
    sandbox = bool(getattr(ex, "sandbox", False)) or bool(
        getattr(ex, "hyperliquid_testnet", False))
    return not sandbox


def _profile_from_env(env: Mapping[str, str]) -> bool:
    kind = deployment_kind(env)
    if kind == LIVE:
        return False
    choice = _raw(env, "TESTNET_BOOK_PROFILE")
    if choice is not None:
        return choice.lower() in _TRUE and choice.lower() not in _FALSE
    return kind == TESTNET


def profile_active(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the testnet book is in force.

    A passed mapping is decided on its own, so a test can describe a
    deployment without arming this process. The process itself also refuses
    the profile when it is live on mainnet.
    """
    if env is None and _runtime_is_live_mainnet():
        return False
    return _profile_from_env(_mapping(env))


def primary_book(env: Optional[Mapping[str, str]] = None) -> str:
    """The book this deployment names as primary.

    Off the profile this is the hourly house book, whatever ``PRIMARY_BOOK``
    says: live mainnet does not grow a second answer. On the profile the
    primary is B1v, unless ``PRIMARY_BOOK`` is exactly ``hourly`` or ``b1v``.
    Any other spelling is not a selection, and the testnet book stands.
    """
    if not profile_active(env):
        return HOURLY_BOOK
    raw = _raw(_mapping(env), "PRIMARY_BOOK")
    if raw is None:
        return B1V_BOOK
    name = raw.lower()
    if name in (HOURLY_BOOK, B1V_BOOK):
        return name
    return B1V_BOOK


def effective_sizing_leverage(configured: int,
                              env: Optional[Mapping[str, str]] = None) -> int:
    """``SIZING_LEVERAGE`` the order is sized at.

    The profile sizes at 2. Off the profile the configured value is returned
    unchanged, including a value an operator set above or below 5.
    """
    if not profile_active(env):
        return configured
    return TESTNET_SIZING_LEVERAGE


def effective_streak_hours(configured: float,
                           env: Optional[Mapping[str, str]] = None) -> float:
    """Hours after which a streak trip clears itself.

    The profile uses 24. Off the profile the configured value is returned
    unchanged, and that value ships at 0, so a live streak stays latched
    until a person clears it.
    """
    if not profile_active(env):
        return configured
    return TESTNET_STREAK_HOURS


def _symbol_key(symbol: object) -> Optional[str]:
    """The base asset a cooldown arm is hashed on, or None when it does not read.

    ``BTC/USDT``, ``BTC/USDT:USDT`` and ``BTCUSDT`` are one symbol. A blank,
    a non-string, or a string with no asset is unread, not a bucket of its own.
    """
    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper()
    if not text:
        return None
    head = text.split(":", 1)[0].replace("-", "").replace("_", "").replace("/", "")
    if not head:
        return None
    if head.endswith("USDT") and len(head) > 4:
        head = head[:-4]
    return head or None


def cooldown_arm_for(symbol: object,
                     env: Optional[Mapping[str, str]] = None) -> str:
    """The cooldown arm for one symbol.

    Stable: the same asset is always the same arm. Even and odd are the two
    halves of the hash, not of a particular universe. Off the profile the
    arm is ``off``. An unreadable symbol is ``unread`` and is not hashed.
    """
    if not profile_active(env):
        return ARM_OFF
    key = _symbol_key(symbol)
    if key is None:
        return ARM_UNREAD
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    if digest[0] % 2 == 0:
        return ARM_TREATMENT
    return ARM_CONTROL


def cooldown_for(symbol: object, configured: int,
                 env: Optional[Mapping[str, str]] = None) -> CooldownDecision:
    """The post-loss wait for an entry on ``symbol``.

    Treatment waits 14400 seconds. Control waits ``configured``, which is
    the live value of ``COOLDOWN_AFTER_LOSS_SEC``. Off the profile, and for
    a symbol that does not read, the wait is ``configured`` and the arm says
    so.
    """
    shown = "" if not isinstance(symbol, str) else symbol.strip()
    arm = cooldown_arm_for(symbol, env)
    if arm == ARM_TREATMENT:
        return CooldownDecision(TREATMENT_COOLDOWN_SEC, arm, shown, configured)
    return CooldownDecision(configured, arm, shown, configured)


def log_cooldown_decision(decision: CooldownDecision, *, where: str,
                          env: Optional[Mapping[str, str]] = None) -> None:
    """Write one assigned arm to the trade log. An arm that was not assigned
    is not a row: ``off`` and ``unread`` are the absence of a trial, and a
    row for them would read as a measurement."""
    if decision.arm in (ARM_OFF, ARM_UNREAD):
        return
    try:
        audit(
            trade_log,
            f"Cooldown arm {decision.arm} for {decision.symbol or 'unread'} "
            f"at {decision.seconds}s ({where})",
            action="cooldown_ab",
            result=decision.arm,
            data={
                "symbol": decision.symbol,
                "cooldown_arm": decision.arm,
                "cooldown_sec": decision.seconds,
                "configured_sec": decision.configured,
                "primary_book": primary_book(env),
                "where": where,
                "review": REVIEW,
            },
        )
    except Exception:
        return


def overlay_daily_trend(cfg: dict, env: Optional[Mapping[str, str]] = None) -> dict:
    """The daily-trend preset the recorded book is read with.

    B1v replaces the vol window, the lookbacks, the target and the band.
    The per-name notional cap stays the sleeve's: this overlay does not
    teach the sleeve to place an order. The caller's preset is not mutated.
    Off B1v the same object comes back.
    """
    if primary_book(env) != B1V_BOOK or not isinstance(cfg, dict):
        return cfg
    raw = cfg.get("daily_trend")
    if not isinstance(raw, dict):
        return cfg
    spec = b1v_parameters()
    sleeve = dict(raw)
    sleeve["lookbacks"] = list(spec.lookbacks)
    sleeve["vol_window"] = spec.vol_window
    sleeve["target_vol"] = spec.target_vol
    sleeve["band"] = spec.band
    if "cap" not in sleeve:
        sleeve["cap"] = spec.weight_cap
    merged = dict(cfg)
    merged["daily_trend"] = sleeve
    return merged


def note_primary_book(env: Optional[Mapping[str, str]] = None) -> str:
    """The primary book, and a scan-log row when the profile is on.

    A scan on a process the profile does not cover writes nothing: the
    hourly book is the book that process already had, and a row would say
    a selection happened.
    """
    book = primary_book(env)
    if not profile_active(env):
        return book
    spec = b1v_parameters()
    try:
        audit(
            scan_log,
            f"Primary book {book}",
            action="primary_book",
            result=book,
            data={
                "primary_book": book,
                "sizing_leverage": TESTNET_SIZING_LEVERAGE,
                "streak_breaker_autoreset_hours": TESTNET_STREAK_HOURS,
                "b1v_vol_window": spec.vol_window,
                "b1v_leverage_cap": spec.leverage_cap,
                "review": REVIEW,
            },
        )
    except Exception:
        return book
    return book


def cooldown_card_line(configured: int,
                       env: Optional[Mapping[str, str]] = None) -> str:
    """The rulebook's cooldown line. Off the profile it is the configured
    wait alone, the sentence the card already printed."""
    if not profile_active(env):
        return f"- Cooldown: <code>{configured}s</code>"
    return (
        "- Cooldown: <code>A/B</code> "
        f"control <code>{configured}s</code>, "
        f"trial <code>{TREATMENT_COOLDOWN_SEC}s</code> by symbol"
    )
