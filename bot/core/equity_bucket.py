"""Bitget tokenized equities, isolated from the crypto book.

The R-prefix family (RAAPL, RTSLA, RNVDA, RFSLY, and the same shape of
replica) stays in the scanner. This module is the cap on how much of the
book they may be, and the wall between their closes and the crypto
learners. It places nothing.

The flag is ``EQUITY_BUCKET_ENABLED``. It defaults off. It is ignored on
live mainnet, including a process armed live after boot. On a venue
testnet (``BITGET_SANDBOX`` or ``HYPERLIQUID_TESTNET``) or a paper process
it applies only when the flag is set. That is the same deployment split
as ``bot.core.testnet_book``, with the opposite default: there the profile
is on for a venue testnet unless reverted; here nothing changes until the
flag is set.

Detection
---------
Bitget's instrument row is the preferred reading. ``symbolType`` of
``stock``, ``isRwa`` of ``YES``, or ``isReality`` of ``yes`` marks a
tokenized equity. ``symbolType`` of ``crypto`` is crypto, and so is an
``isRwa`` / ``isReality`` of ``no`` when the row does not also say stock.
A row that carries none of those fields is not a reading.

The name still has to be the R-prefix family: base coin ``R`` plus a
ticker, ``^R[A-Z][A-Z0-9]{1,8}$``. A stock perp whose base is ``TSLA``
or ``AAPLON`` is not this family. A base on the crypto refusal list
(RUNE, RENDER, RSR, RVN, and the other crypto bases recorded below) is
never an equity, including when a row is mis-tagged ``stock``.

When the row is absent, the fallback is that same shape AND the ticker
after the leading ``R`` is on the documented underlying list (the replica
names in ``US_STOCK_SYMBOLS``, plus FSLY). A leading ``R`` alone is not
enough, which is why RUNE, RENDER, RSR and RVN stay crypto with no
metadata.

Ceilings, when the bucket is on
-------------------------------
* At most ``EQUITY_MAX_OPEN_POSITIONS`` equity positions (default 2).
* Equity open risk at most ``EQUITY_MAX_RISK_SHARE`` of total open risk
  (default 20%). With no other open risk the share of one equity would
  be the whole book, so the ceiling admits none until another book has
  risk open. The performance budget can only lower this share.
* Per-trade equity risk at most ``EQUITY_RISK_MULTIPLE`` times the crypto
  size (default 0.5). Nothing here raises a size.

Crypto keeps the position slots. In one confirm batch, crypto ideas are
ordered first. An equity is refused when crypto already holds every slot
(``crypto_open >= max_open_positions``) or the book is already full.
The existing max-open check is unchanged and still counts every position.

Learning
--------
Equity closes do not enter confidence calibration, voter-weight learning,
or setup expectancy, and they do not move the streak breaker, the
live-performance window, or a crypto symbol cooldown. They do move the
account daily-loss accumulator (live) and the portfolio's own daily P&L
(paper). The daily loss limit still counts them.

Adaptive adjustments are deterministic and only tighten. Each one is a
reason code on the risk check.
"""
from __future__ import annotations

import math
import os
import re
import threading
from datetime import datetime
from typing import Any, Mapping, NamedTuple, Optional, Sequence

from bot.core.testnet_book import (
    LIVE,
    _runtime_is_live_mainnet,
    deployment_kind,
)

ASSET_EQUITY = "equity"
ASSET_CRYPTO = "crypto"

# Crypto bases a leading R must never reclassify, even if a venue row says
# stock. The four the scanner actually confuses with this family are RUNE,
# RENDER, RSR and RVN. The rest are crypto bases already in this repo's
# lists, or the usual short R-tickers a mis-tagged row would otherwise pull in.
CRYPTO_BASES = frozenset({
    "RUNE", "RENDER", "RSR", "RVN",
    "RAVE", "RAY", "RNDR", "ROSE", "RON", "RPL", "REEF", "REN",
    "RLC", "RIF", "RAD", "RARE", "REQ", "ROOK", "RDNT", "RBN",
    "RGT", "RONIN", "ROUTE",
})

# Ticker AFTER the leading R. The replica names in bot.config.US_STOCK_SYMBOLS
# plus FSLY (RFSLY). Used only when Bitget did not send a symbol row.
_FALLBACK_UNDERLYINGS = frozenset({
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD",
    "SPY", "QQQ", "COIN", "HOOD", "ARM", "MRVL", "DELL", "INTC",
    "NOK", "ANET", "FSLY",
})

_R_PREFIX = re.compile(r"^R[A-Z][A-Z0-9]{1,8}$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_QUOTES = ("USDT", "USDC", "USD")

# Defaults. Env may tighten or, where noted, retune. Nothing may exceed 1
# as a size multiple: the bucket never sizes an equity above the crypto path.
DEFAULT_MAX_OPEN = 2
DEFAULT_RISK_SHARE = 0.20
DEFAULT_RISK_MULTIPLE = 0.5
DEFAULT_DEPTH_FRACTION = 0.10
DEFAULT_SESSION_CLOSED_MULT = 0.25
DEFAULT_CLUSTER_SHRINK = 0.5
DEFAULT_CLUSTER_FLOOR = 0.25
DEFAULT_VOL_TARGET = 0.03
DEFAULT_VOL_UNKNOWN_MULT = 0.5
DEFAULT_ASSUMED_CORR_MULT = 0.5
DEFAULT_CORR_THRESHOLD = 0.50
DEFAULT_CORR_MIN_POINTS = 5
DEFAULT_BUDGET_FLOOR = 0.05
DEFAULT_BUDGET_WINDOW = 20
DEFAULT_BUDGET_MIN_CLOSES = 8
DEFAULT_BUDGET_DD = 0.10
DEFAULT_BUDGET_STEP = 0.02

_CLOSES_LOCK = threading.Lock()
_CLOSES: list[float] = []
_CLOSES_CAP = 500


class OpenLeg(NamedTuple):
    """One open position the equity decision can see."""

    symbol: str
    direction: str
    risk_usd: Optional[float]
    notional_usd: Optional[float]


class EquityEffect(NamedTuple):
    """What the bucket does to one idea. ``None`` from ``apply_to_risk`` means
    the bucket is off or the idea is not this family, and the caller changes
    nothing."""

    position_usd: float
    multiplier: float
    failed: tuple[str, ...]
    passed_line: str
    size_note: str
    audit: dict[str, Any]


def _mapping(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _raw(env: Mapping[str, str], name: str) -> Optional[str]:
    if name not in env:
        return None
    text = str(env.get(name, "")).strip()
    return text if text else None


def _on(env: Mapping[str, str], name: str) -> bool:
    raw = _raw(env, name)
    if raw is None:
        return False
    return raw.lower() in _TRUE


def _float_env(env: Mapping[str, str], name: str, default: float,
               *, lo: float, hi: float) -> float:
    """A tuned float, or ``default`` when the variable is absent or not a
    number. An unreadable value is not zero and not a loosened cap."""
    raw = _raw(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    if value < lo or value > hi:
        return default
    return value


def _int_env(env: Mapping[str, str], name: str, default: int, *, lo: int, hi: int) -> int:
    raw = _raw(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < lo or value > hi:
        return default
    return value


def bucket_active(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the equity bucket is in force.

    A passed mapping is decided on its own. The process itself also refuses
    the bucket when it is live on mainnet, the same runtime arm
    ``testnet_book.profile_active`` refuses.
    """
    if env is None and _runtime_is_live_mainnet():
        return False
    e = _mapping(env)
    if deployment_kind(e) == LIVE:
        return False
    return _on(e, "EQUITY_BUCKET_ENABLED")


def base_coin(symbol: str) -> str:
    """The base of a spot or perp symbol. ``""`` when there is nothing to read."""
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    core = text.split(":")[0]
    if "/" in core:
        return core.split("/", 1)[0]
    for quote in _QUOTES:
        if core.endswith(quote) and len(core) > len(quote):
            return core[: -len(quote)]
    return core


def _norm_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _meta_rows(metadata: Optional[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if not isinstance(metadata, Mapping) or not metadata:
        return []
    rows: list[Mapping[str, Any]] = [metadata]
    info = metadata.get("info")
    if isinstance(info, Mapping):
        rows.append(info)
    return rows


def metadata_says_equity(metadata: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """What the Bitget row says, or None when it does not say.

    ``True`` is stock / RWA / reality. ``False`` is crypto, or an explicit
    not-RWA with no stock tag. ``None`` means those fields are absent, so
    the documented name rule is the reading.
    """
    rows = _meta_rows(metadata)
    if not rows:
        return None
    saw = False
    positive = False
    negative = False
    for row in rows:
        kind = _norm_token(row.get("symbolType", row.get("symbol_type")))
        if kind:
            saw = True
            if kind == "crypto":
                return False
            if kind == "stock":
                positive = True
        rwa = _norm_token(row.get("isRwa", row.get("is_rwa")))
        if rwa:
            saw = True
            if rwa in ("yes", "true", "1"):
                positive = True
            elif rwa in ("no", "false", "0"):
                negative = True
        reality = _norm_token(row.get("isReality", row.get("is_reality")))
        if reality:
            saw = True
            if reality in ("yes", "true", "1"):
                positive = True
            elif reality in ("no", "false", "0"):
                negative = True
    if not saw:
        return None
    if positive:
        return True
    if negative:
        return False
    return None


def _family_shape(base: str) -> bool:
    return bool(base) and _R_PREFIX.match(base) is not None


def is_tokenized_equity(symbol: str, metadata: Optional[Mapping[str, Any]] = None) -> bool:
    """Whether ``symbol`` is an R-prefix Bitget tokenized equity.

    See the module docstring for the metadata-then-rule order. Crypto bases
    on ``CRYPTO_BASES`` are never equities.
    """
    base = base_coin(symbol)
    if not base or base in CRYPTO_BASES:
        return False
    tagged = metadata_says_equity(metadata)
    if tagged is True:
        return _family_shape(base)
    if tagged is False:
        return False
    if not _family_shape(base):
        return False
    return base[1:] in _FALLBACK_UNDERLYINGS


def crypto_learning_isolated(symbol: str, metadata: Optional[Mapping[str, Any]] = None,
                             env: Optional[Mapping[str, str]] = None) -> bool:
    """An equity close is kept out of the crypto learners while the bucket is on."""
    return bucket_active(env) and is_tokenized_equity(symbol, metadata)


def without_equity_bucket(decisions: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """Drop equity rows, and any row sharing their trade id, from a learner sample.

    When the bucket is off this is the same object the caller passed. A row
    with no symbol and no id linked to an equity row stays: absence is not
    an equity classification.
    """
    if not bucket_active(env):
        return decisions
    rows = list(decisions or [])
    equity_ids: set[str] = set()
    for row in rows:
        sym = str(getattr(row, "symbol", "") or "")
        if sym and is_tokenized_equity(sym):
            tid = str(getattr(row, "paper_trade_id", "") or "")
            if tid:
                equity_ids.add(tid)
    kept = []
    for row in rows:
        sym = str(getattr(row, "symbol", "") or "")
        if sym and is_tokenized_equity(sym):
            continue
        tid = str(getattr(row, "paper_trade_id", "") or "")
        if tid and tid in equity_ids:
            continue
        kept.append(row)
    return kept


def trade_fields(symbol: str, metadata: Optional[Mapping[str, Any]] = None,
                 env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """``asset_class=equity`` while the bucket is on and the symbol is one.
    Empty otherwise, so a crypto log line is unchanged."""
    if crypto_learning_isolated(symbol, metadata, env):
        return {"asset_class": ASSET_EQUITY}
    return {}


def slot_order(pairs: Sequence[Any], env: Optional[Mapping[str, str]] = None) -> list[Any]:
    """Crypto ideas first, then equities. Stable. A no-op when the bucket is off.

    ``pairs`` is ``(id, idea)``. The idea's asset is ``asset`` or ``symbol``.
    """
    rows = list(pairs)
    if not bucket_active(env):
        return rows

    def rank(pair: Any) -> int:
        idea = pair[1] if isinstance(pair, tuple) and len(pair) > 1 else pair
        asset = str(getattr(idea, "asset", "") or getattr(idea, "symbol", "") or "")
        return 1 if is_tokenized_equity(asset) else 0

    return sorted(rows, key=rank)


def _reset_bucket_state() -> None:
    """Forget this process's equity closes. The suite calls this between cases."""
    with _CLOSES_LOCK:
        _CLOSES.clear()


def record_bucket_close(pnl: float, symbol: str,
                        env: Optional[Mapping[str, str]] = None) -> None:
    """Append one equity close to the bucket's own record. Crypto closes are ignored."""
    if not crypto_learning_isolated(symbol, env=env):
        return
    try:
        value = float(pnl)
    except (TypeError, ValueError):
        return
    if not math.isfinite(value):
        return
    with _CLOSES_LOCK:
        _CLOSES.append(value)
        if len(_CLOSES) > _CLOSES_CAP:
            del _CLOSES[:-_CLOSES_CAP]


def bucket_closes() -> tuple[float, ...]:
    with _CLOSES_LOCK:
        return tuple(_CLOSES)


def _direction(value: Any) -> str:
    raw = getattr(value, "value", value)
    word = str(raw or "").strip().upper()
    return word if word in ("LONG", "SHORT") else ""


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def legs_from_held(rows: Optional[Sequence[Any]]) -> tuple[list[OpenLeg], bool]:
    """Legs from ``HeldRow``-like records. The bool is True when a row's
    symbol or notional could not be read (a missing notional is not zero)."""
    unread = False
    legs: list[OpenLeg] = []
    for row in list(rows or []):
        symbol = str(getattr(row, "symbol", "") or "")
        if not symbol:
            unread = True
            continue
        notional = _finite(getattr(row, "notional_usd", None))
        if notional is None or notional < 0:
            unread = True
            legs.append(OpenLeg(symbol, _direction(getattr(row, "direction", "")), None, None))
            continue
        # The held row has no stop. Notional is the conservative risk:
        # the whole position, not a zero loss.
        legs.append(OpenLeg(symbol, _direction(getattr(row, "direction", "")), notional, notional))
    return legs, unread


def legs_from_portfolio(portfolio: Any) -> tuple[list[OpenLeg], bool]:
    """Legs from a paper book. Risk is the loss at the stop when the stop
    is on record; otherwise the notional. A missing notional is unread."""
    unread = False
    legs: list[OpenLeg] = []
    positions = list(getattr(portfolio, "open_positions", []) or [])
    for pos in positions:
        symbol = str(getattr(pos, "asset", "") or getattr(pos, "symbol", "") or "")
        if not symbol:
            unread = True
            continue
        entry = _finite(getattr(pos, "entry_price", None))
        stop = _finite(getattr(pos, "stop_loss", None))
        qty = _finite(getattr(pos, "quantity", None))
        notional: Optional[float] = None
        if entry is not None and qty is not None and entry > 0 and qty > 0:
            notional = entry * qty
        if notional is None:
            unread = True
            risk: Optional[float] = None
        elif stop is not None and stop > 0 and entry is not None and entry > 0 and stop != entry:
            risk = abs(entry - stop) / entry * notional
        else:
            risk = notional
        legs.append(OpenLeg(symbol, _direction(getattr(pos, "direction", "")), risk, notional))
    return legs, unread


def _pearson(xs: Sequence[float], ys: Sequence[float], min_points: int) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < min_points:
        return None
    a = [float(v) for v in list(xs)[-n:]]
    b = [float(v) for v in list(ys)[-n:]]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    da = [v - mean_a for v in a]
    db = [v - mean_b for v in b]
    cov = sum(x * y for x, y in zip(da, db))
    var_a = sum(x * x for x in da)
    var_b = sum(y * y for y in db)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / math.sqrt(var_a * var_b)


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    gains = sum(p for p in pnls if p > 0)
    losses = sum(-p for p in pnls if p < 0)
    if gains == 0 and losses == 0:
        return None
    if losses == 0:
        return math.inf
    return gains / losses


def _max_drawdown(pnls: Sequence[float]) -> float:
    """Peak-to-trough on the cumulative sum, once a positive peak exists.

    A window that never rises has no high-water mark. Its drawdown is 0;
    a losing window is caught by the profit factor, not by calling a
    decline from zero a 100% drawdown.
    """
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += float(pnl)
        if equity > peak:
            peak = equity
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def budget_share(closes: Sequence[float], *, ceiling: float, floor: float,
                 window: int, min_closes: int, dd_limit: float, step: float
                 ) -> tuple[float, tuple[str, ...]]:
    """The equity book's risk share from its own closes.

    Fewer than ``min_closes`` stays at ``floor``. A trailing window whose
    profit factor is below 1, or whose drawdown exceeds ``dd_limit``,
    returns the share to ``floor``. A healthy window steps the share up by
    ``step`` and no further, and never above ``ceiling``. ``ceiling`` is
    the hard share the caller already resolved; this function does not
    raise it.
    """
    if len(closes) < min_closes:
        return min(floor, ceiling), ("EQUITY_BUDGET_FLOOR",)
    share = min(floor, ceiling)
    shrunk = False
    stepped = False
    span = max(1, window)
    for i in range(min_closes, len(closes) + 1):
        sample = list(closes[max(0, i - span):i])
        pf = _profit_factor(sample)
        dd = _max_drawdown(sample)
        unhealthy = (pf is not None and pf < 1.0) or dd > dd_limit
        if unhealthy:
            share = min(floor, ceiling)
            shrunk = True
            continue
        nxt = min(ceiling, share + step)
        if nxt > share + 1e-12:
            stepped = True
        share = nxt
    if shrunk and share <= floor + 1e-12:
        return share, ("EQUITY_BUDGET_SHRINK",)
    if stepped and share + 1e-12 < ceiling:
        return share, ("EQUITY_BUDGET_RECOVER",)
    if share + 1e-12 < ceiling:
        return share, ("EQUITY_BUDGET_FLOOR",)
    return share, ()


def _session_state(now: Optional[datetime], override: Optional[str]) -> str:
    if override is not None:
        return override
    try:
        from bot.core.order_rules import reference_session_state
        return reference_session_state("Stock", now)
    except Exception:
        return "unknown"


def top_of_book_usd(signal: Any, direction: str) -> Optional[float]:
    """Executable top-of-book depth in USD for the side this entry takes.

    ``None`` when the book was not read. ``0`` is a measured empty side,
    not an unread one. A long buys the ask; a short hits the bid.
    """
    if signal is None or direction not in ("LONG", "SHORT"):
        return None
    comps = getattr(signal, "components_ok", None)
    if not isinstance(comps, (list, tuple)) or "book" not in comps:
        return None
    field = "ask_depth_top_usd" if direction == "LONG" else "bid_depth_top_usd"
    depth = _finite(getattr(signal, field, None))
    if depth is None or depth < 0:
        return None
    return depth


def _fit_share(new_risk: float, equity_open: float, crypto_risk: float, share: float) -> float:
    """Fraction of ``new_risk`` that keeps equity risk within ``share`` of the book.

    ``0`` when the open equity book is already at the share, or when there
    is no other risk to be a share of.
    """
    if share <= 0:
        return 0.0
    if share >= 1:
        return 1.0
    if new_risk <= 0:
        return 1.0
    room = (share * (crypto_risk + equity_open) - equity_open) / (1.0 - share)
    if room <= 0:
        return 0.0
    return min(1.0, room / new_risk)


class _Ceilings(NamedTuple):
    max_open: int
    risk_share: float
    risk_multiple: float
    depth_fraction: float
    session_closed: float
    cluster_shrink: float
    cluster_floor: float
    vol_target: float
    assumed_corr: float
    corr_threshold: float
    budget_floor: float
    budget_window: int
    budget_min_closes: int
    budget_dd: float
    budget_step: float


def _ceilings(env: Mapping[str, str]) -> _Ceilings:
    return _Ceilings(
        max_open=_int_env(env, "EQUITY_MAX_OPEN_POSITIONS", DEFAULT_MAX_OPEN, lo=0, hi=100),
        risk_share=_float_env(env, "EQUITY_MAX_RISK_SHARE", DEFAULT_RISK_SHARE, lo=0.0, hi=1.0),
        risk_multiple=_float_env(env, "EQUITY_RISK_MULTIPLE", DEFAULT_RISK_MULTIPLE, lo=0.0, hi=1.0),
        depth_fraction=_float_env(env, "EQUITY_DEPTH_FRACTION", DEFAULT_DEPTH_FRACTION, lo=0.0, hi=1.0),
        session_closed=_float_env(
            env, "EQUITY_SESSION_CLOSED_MULT", DEFAULT_SESSION_CLOSED_MULT, lo=0.0, hi=1.0),
        cluster_shrink=_float_env(env, "EQUITY_CLUSTER_SHRINK", DEFAULT_CLUSTER_SHRINK, lo=0.0, hi=1.0),
        cluster_floor=_float_env(env, "EQUITY_CLUSTER_FLOOR", DEFAULT_CLUSTER_FLOOR, lo=0.0, hi=1.0),
        vol_target=_float_env(env, "EQUITY_VOL_TARGET", DEFAULT_VOL_TARGET, lo=1e-6, hi=1.0),
        assumed_corr=_float_env(
            env, "EQUITY_ASSUMED_CORR_MULT", DEFAULT_ASSUMED_CORR_MULT, lo=0.0, hi=1.0),
        corr_threshold=_float_env(
            env, "EQUITY_CORR_THRESHOLD", DEFAULT_CORR_THRESHOLD, lo=0.0, hi=1.0),
        budget_floor=_float_env(env, "EQUITY_BUDGET_FLOOR", DEFAULT_BUDGET_FLOOR, lo=0.0, hi=1.0),
        budget_window=_int_env(env, "EQUITY_BUDGET_WINDOW", DEFAULT_BUDGET_WINDOW, lo=1, hi=500),
        budget_min_closes=_int_env(
            env, "EQUITY_BUDGET_MIN_CLOSES", DEFAULT_BUDGET_MIN_CLOSES, lo=1, hi=500),
        budget_dd=_float_env(env, "EQUITY_BUDGET_DD", DEFAULT_BUDGET_DD, lo=0.0, hi=1.0),
        budget_step=_float_env(env, "EQUITY_BUDGET_STEP", DEFAULT_BUDGET_STEP, lo=0.0, hi=1.0),
    )


def decide(
    *,
    symbol: str,
    direction: str,
    position_usd: float,
    leverage: int,
    stop_distance_pct: float,
    legs: Sequence[OpenLeg],
    book_unread: bool,
    max_open_positions: int,
    atr: Optional[float] = None,
    entry_price: Optional[float] = None,
    depth_usd: Optional[float] = None,
    depth_known: bool = False,
    returns: Optional[Mapping[str, Sequence[float]]] = None,
    closes: Optional[Sequence[float]] = None,
    now: Optional[datetime] = None,
    session_state: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    active: Optional[bool] = None,
) -> Optional[EquityEffect]:
    """The bucket's decision for one entry. ``None`` when it does not apply.

    ``depth_known`` False is an unread book (fail closed). ``depth_usd`` of
    0 with ``depth_known`` True is a measured empty book.
    """
    e = _mapping(env)
    if active is None:
        active = bucket_active(e)
    if not active or not is_tokenized_equity(symbol, metadata):
        return None

    caps = _ceilings(e)
    side = _direction(direction)
    reasons: list[str] = []
    failed: list[str] = []
    mult = caps.risk_multiple
    reasons.append("EQUITY_RISK_MULTIPLE")

    if book_unread:
        failed.append("EQUITY_BOOK_UNREAD: open book could not be classified")
        return _effect(position_usd, 1.0, failed, reasons, symbol, caps)

    equity_legs = [leg for leg in legs if is_tokenized_equity(leg.symbol)]
    crypto_legs = [leg for leg in legs if not is_tokenized_equity(leg.symbol)]
    if any(leg.risk_usd is None for leg in legs):
        failed.append("EQUITY_RISK_UNREAD: an open position has no risk on record")

    equity_n = len(equity_legs)
    crypto_n = len(crypto_legs)
    if equity_n >= caps.max_open:
        failed.append(
            f"EQUITY_MAX_OPEN: {equity_n} >= {caps.max_open}")
    # Spare capacity is a slot crypto is not already sitting in, inside the
    # same max-open cap. Equities do not raise that cap.
    if crypto_n >= max_open_positions or (crypto_n + equity_n) >= max_open_positions:
        failed.append(
            f"EQUITY_SLOT: crypto {crypto_n} of {max_open_positions}, "
            f"equity {equity_n}, no spare slot")

    same = [leg for leg in equity_legs if leg.direction and leg.direction == side]
    cluster_corr = 1.0
    if same:
        cluster_corr *= caps.cluster_shrink ** len(same)
        reasons.append("EQUITY_CLUSTER_SAME_DIRECTION")

    measured_shrink: Optional[float] = None
    assumed = False
    if legs:
        if returns:
            for leg in legs:
                if leg.symbol == symbol:
                    continue
                left = returns.get(symbol)
                right = returns.get(leg.symbol)
                corr = None
                if left is not None and right is not None:
                    corr = _pearson(left, right, DEFAULT_CORR_MIN_POINTS)
                if corr is None:
                    assumed = True
                    continue
                if abs(corr) >= caps.corr_threshold:
                    cut = max(caps.cluster_floor, 1.0 - abs(corr))
                    measured_shrink = cut if measured_shrink is None else min(measured_shrink, cut)
        else:
            assumed = True
    if measured_shrink is not None:
        cluster_corr *= measured_shrink
        reasons.append("EQUITY_CORR_MEASURED")
    elif assumed:
        cluster_corr *= caps.assumed_corr
        reasons.append("EQUITY_CORR_ASSUMED")
    if cluster_corr < 1.0:
        cluster_corr = max(caps.cluster_floor, cluster_corr)
    mult *= cluster_corr

    realized = None
    price = _finite(entry_price)
    atr_v = _finite(atr)
    if price is not None and price > 0 and atr_v is not None and atr_v > 0:
        realized = atr_v / price
    if realized is not None and realized > 0:
        mult *= min(1.0, caps.vol_target / realized)
        reasons.append("EQUITY_VOL_SCALE")
    else:
        mult *= DEFAULT_VOL_UNKNOWN_MULT
        reasons.append("EQUITY_VOL_UNKNOWN")

    session = _session_state(now, session_state)
    if session == "closed":
        if caps.session_closed <= 0:
            failed.append("EQUITY_SESSION_CLOSED: US reference session is closed")
        else:
            mult *= caps.session_closed
            reasons.append("EQUITY_SESSION_THIN")
    elif session == "unknown":
        reasons.append("EQUITY_SESSION_UNREAD")
    elif session == "open":
        reasons.append("EQUITY_SESSION_OPEN")

    mult = min(caps.risk_multiple, max(0.0, mult))
    sized = max(0.0, float(position_usd)) * mult

    if not depth_known or depth_usd is None:
        failed.append("EQUITY_DEPTH_UNKNOWN: top-of-book depth was not read")
    else:
        lev = max(1, int(leverage))
        notional = sized * lev
        cap_notional = float(depth_usd) * caps.depth_fraction
        if notional > cap_notional + 1e-9:
            if cap_notional <= 0 or lev <= 0:
                sized = 0.0
            else:
                sized = cap_notional / lev
            reasons.append("EQUITY_DEPTH_CAP")
            if sized <= 0:
                failed.append("EQUITY_DEPTH_CAP: measured top-of-book cannot hold a size")

    history = bucket_closes() if closes is None else list(closes)
    share, budget_reasons = budget_share(
        history,
        ceiling=caps.risk_share,
        floor=min(caps.budget_floor, caps.risk_share),
        window=caps.budget_window,
        min_closes=caps.budget_min_closes,
        dd_limit=caps.budget_dd,
        step=caps.budget_step,
    )
    reasons.extend(budget_reasons)

    if not any(code.startswith("EQUITY_RISK_UNREAD") for code in failed):
        equity_open = 0.0
        for leg in equity_legs:
            if leg.risk_usd is not None:
                equity_open += leg.risk_usd
        crypto_risk = 0.0
        for leg in crypto_legs:
            if leg.risk_usd is not None:
                crypto_risk += leg.risk_usd
        stop = _finite(stop_distance_pct)
        lev = max(1, int(leverage))
        if stop is None or stop <= 0:
            failed.append("EQUITY_RISK_UNREAD: stop distance was not read")
        else:
            new_risk = sized * lev * stop
            fit = _fit_share(new_risk, equity_open, crypto_risk, share)
            if fit < 1.0 - 1e-12:
                sized *= fit
                reasons.append("EQUITY_RISK_SHARE")
                if sized <= 1e-9:
                    failed.append(
                        f"EQUITY_RISK_SHARE: equity risk would exceed {share:.0%} of open risk")

    final_mult = (sized / float(position_usd)) if float(position_usd) > 0 else mult
    return _effect(sized, final_mult, failed, reasons, symbol, caps, share=share)


def _effect(position_usd: float, multiplier: float, failed: Sequence[str],
            reasons: Sequence[str], symbol: str, caps: _Ceilings,
            share: Optional[float] = None) -> EquityEffect:
    codes = tuple(dict.fromkeys(reasons))
    note = "equity bucket x{:.2f} ({})".format(multiplier, ",".join(codes))
    passed = ""
    if not failed:
        passed = "EQUITY_BUCKET: x{:.2f} [{}]".format(multiplier, ",".join(codes))
    audit = {
        "asset_class": ASSET_EQUITY,
        "symbol": symbol,
        "reasons": list(codes),
        "multiplier": round(multiplier, 6),
        "position_usd": round(position_usd, 4),
        "max_open": caps.max_open,
        "risk_share": caps.risk_share,
        "risk_multiple": caps.risk_multiple,
    }
    if share is not None:
        audit["budget_share"] = round(share, 6)
    return EquityEffect(
        position_usd=position_usd,
        multiplier=multiplier,
        failed=tuple(failed),
        passed_line=passed,
        size_note=note,
        audit=audit,
    )


def apply_to_risk(
    *,
    symbol: str,
    direction: Any,
    position_usd: float,
    leverage: int,
    stop_distance_pct: float,
    atr: Optional[float],
    entry_price: float,
    held_rows: Optional[Sequence[Any]],
    portfolio: Any,
    book_unread: bool,
    max_open_positions: int,
    depth_signal: Any,
    now: Optional[datetime] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    returns: Optional[Mapping[str, Sequence[float]]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[EquityEffect]:
    """The risk-engine hook. Never raises. ``None`` when the bucket does not apply.

    A fault after the symbol is known to be an equity refuses the trade.
    A fault before that returns ``None`` so a crypto idea is left alone.
    """
    try:
        if not bucket_active(env) or not is_tokenized_equity(symbol, metadata):
            return None
    except Exception:
        return None
    try:
        if held_rows is not None:
            legs, unread = legs_from_held(held_rows)
        elif portfolio is not None:
            legs, unread = legs_from_portfolio(portfolio)
        else:
            legs, unread = [], True
        side = _direction(direction)
        depth = top_of_book_usd(depth_signal, side)
        return decide(
            symbol=symbol,
            direction=side,
            position_usd=position_usd,
            leverage=leverage,
            stop_distance_pct=stop_distance_pct,
            legs=legs,
            book_unread=book_unread or unread,
            max_open_positions=max_open_positions,
            atr=atr,
            entry_price=entry_price,
            depth_usd=depth,
            depth_known=depth is not None,
            returns=returns,
            now=now,
            metadata=metadata,
            env=env,
            active=True,
        )
    except Exception as exc:
        caps = _ceilings(_mapping(env))
        failed = (f"EQUITY_BUCKET: evaluation error ({type(exc).__name__})",)
        return _effect(position_usd, 1.0, failed, ["EQUITY_BUCKET"], symbol, caps)
