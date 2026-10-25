"""Limit Entry Scanner: one reading of its rules.

The RUNECLAW Limit Entry Scanner playbook (``playbooks/runeclaw-limit-scanner``)
ported onto closed 1h bars, so the frozen backtest and the live scan read the
same rules. One pass:

1. **The leader's regime.** For crypto the leader is BTC, never traded. Up on
   the day and above its 24h average price are two votes for long; down and
   below are two for short. Taker flow, when it was read, is a third vote;
   unread, it votes for neither side. Two votes are a full-size side, one is
   half size, and none, or a leader that could not be read, opens nothing.
2. **The score.** Every other symbol is scored for that side, out of 100:
   momentum 25 (24h change relative to the leader, ranked across the symbols
   that read one), VWAP position 20, 24h range position 20, order-book
   balance 20, and quote volume 15 (a percentile across the class, so one
   busy name does not take the whole score). A dimension that could not be
   read adds nothing and is named. When the order book is that dimension,
   the other 80 points are scaled onto 100, so the same 70 is the same
   fraction of what was read. The playbook gave an unread dimension
   mid-scale points (12.5 momentum, 10 VWAP, 8 order book, 7.5 volume), so a
   symbol could clear the bar on points nobody measured.
3. **The skips.** Extended beyond the VWAP cap on the entry side, quote
   volume below the floor, an opposing order-book wall, or funding at or
   beyond the skip threshold on the side that pays it.
4. **One order.** The best symbol at or above the minimum score gets one
   resting limit: the 24h VWAP less half an ATR for a long (plus, for a
   short), never through the market. The stop sits at the 24h low (high),
   widened to the symbol's tier floor; the target is the first take-profit.
   A limit already further than the chase cap from the market is not placed.

Everything is a fraction of price or a count, never a dollar figure, except
the quote-volume floor, which is a market fact.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from bot.core.limit_entry import _calculate_vwap
from bot.core.position_telemetry import atr_reading, price_on_record
from bot.core.strategy_gate import _base
from bot.utils.candles import volume_on_record

LONG = "LONG"
SHORT = "SHORT"

#: The playbook's weights, the points each dimension is worth.
WEIGHTS: dict[str, float] = {
    "momentum": 25.0, "vwap": 20.0, "range": 20.0, "orderbook": 20.0, "volume": 15.0,
}

#: A preset is this scanner when it names it.
SCANNER_NAME = "limit_entry"

#: The ``source`` its ideas carry. The backtest gives a position with it the
#: scanner's own exits (`BacktestEngine._check_scanner_exit`).
SOURCE = "limit_scanner"

#: The bars a "day" is: the playbook read Bitget's 24h ticker.
DAY_BARS = 24


def preset_is_limit_scanner(cfg: Any) -> bool:
    """True when this preset is the Limit Entry Scanner."""
    return isinstance(cfg, dict) and cfg.get("scanner") == SCANNER_NAME


@dataclass(frozen=True)
class ScannerParams:
    """The scanner's numbers. Percent figures are percent (5.0 is 5%)."""

    leader: str = "BTC"
    min_score: float = 70.0
    max_vwap_ext_pct: float = 5.0
    atr_period: int = 14
    atr_limit_mult: float = 0.5
    limit_chase_pct: float = 3.0
    min_quote_volume: float = 10_000_000.0
    funding_skip_bps: float = 30.0
    bidask_full_ratio: float = 2.0
    bidask_partial_ratio: float = 1.2
    bidask_wall_ratio: float = 10.0
    tp1_pct: float = 5.0
    stop_floor_pct: Mapping[str, float] = field(default_factory=lambda: {
        "BTC": 1.5, "ETH": 1.5, "SOL": 1.2, "BNB": 1.2})
    alt_stop_floor_pct: float = 2.5
    allow_short: bool = True
    # The exits, each a percent move from the fill unless named otherwise.
    scale_out_trigger_pct: float = 3.5
    scale_out_fraction: float = 0.35
    tp1_fraction: float = 0.5
    tp2_pct: float = 20.0
    trail_atr_mult: float = 2.0
    step_locks: tuple[tuple[float, float], ...] = ((2.0, 1.5), (4.0, 3.0), (6.0, 4.5))
    pullback_time_stop_hours: float = 4.0
    time_stop_hours: float = 12.0
    # Market classes, each behind its own leader with its own volume floor.
    # None is one class: ``leader`` over every other symbol.
    classes: tuple["ScanClass", ...] = ()
    # The playbook's portfolio layer: positions open or resting at once, and
    # alts of one class at once (`portfolio_cap`). None is no cap of the
    # scanner's own; the house risk gate's apply either way.
    max_concurrent: Optional[int] = None
    max_correlated_alts: Optional[int] = None
    # Quote volume is a percentile across the class. ``minmax`` is the old
    # rank, kept so a measurement can show what it did. It is not the default.
    volume_rank: str = "percentile"
    # An unread order book is not given points. The other four weights are
    # scaled onto 100 so the same 70 is the same fraction of what was read.
    # Off, the raw sum is the bar (the old card: 70 of 80, which only the
    # volume leader could clear).
    scale_unread_book: bool = True
    # Adverse-selection studies. Each is off unless a preset turns it on.
    imbalance_filter: bool = False
    neutral_on_tie: bool = False
    vol_entry_offset: float = 0.0
    reclaim_fill: bool = False
    cancel_adverse_bp: float = 0.0
    # Tokenized equities stay out unless a preset opts in. Their book on
    # Bitget's v2 feed is a failed read for the R-prefix names, and a missing
    # book is a fifth of the score. Opting in still skips a name whose book
    # was not read.
    include_equities: bool = False

    def stop_floor(self, symbol: str) -> float:
        """The tier floor for ``symbol``'s stop distance, as a fraction."""
        return float(self.stop_floor_pct.get(_base(symbol), self.alt_stop_floor_pct)) / 100.0


def _positive(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and 0 < f < float("inf") else None


#: The numbers a preset may set, each a positive number.
_NUMBER_KEYS = ("min_score", "max_vwap_ext_pct", "atr_limit_mult", "limit_chase_pct",
                "min_quote_volume", "funding_skip_bps", "bidask_full_ratio",
                "bidask_partial_ratio", "bidask_wall_ratio", "tp1_pct",
                "alt_stop_floor_pct", "scale_out_trigger_pct", "scale_out_fraction",
                "tp1_fraction", "tp2_pct", "trail_atr_mult",
                "pullback_time_stop_hours", "time_stop_hours")

#: Shares of a position: positive, and at most the whole of it.
_FRACTION_KEYS = ("scale_out_fraction", "tp1_fraction")

#: Counts of positions: a positive int each.
_COUNT_KEYS = ("max_concurrent", "max_correlated_alts")

#: Every preset key the scanner reads: the one list for `params_from_preset`,
#: the runner's ``--scanner-config`` and the card's gate block.
SCANNER_KEYS: tuple[str, ...] = _NUMBER_KEYS + _COUNT_KEYS + (
    "atr_period", "leader", "allow_short", "stop_floor_pct", "step_locks", "classes",
    "volume_rank", "scale_unread_book", "imbalance_filter", "neutral_on_tie",
    "vol_entry_offset", "reclaim_fill", "cancel_adverse_bp", "include_equities")


@dataclass(frozen=True)
class ScanClass:
    """One market class: its leader (never traded), its members, and the
    quote-volume floor a member needs to be placed."""

    name: str
    leader: str
    members: frozenset[str]
    min_quote_volume: float


def _read_classes(raw: Any) -> Optional[tuple[ScanClass, ...]]:
    """The preset's classes, or None when any of them does not read.

    A class names a leader, its members and its own floor. A member belongs
    to one class, and no class trades its own leader.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    out: list[ScanClass] = []
    seen_names: set[str] = set()
    seen: set[str] = set()
    for c in raw:
        if not isinstance(c, dict):
            return None
        name = c.get("name")
        leader = _base(c.get("leader") or "")
        members = c.get("members")
        floor = _positive(c.get("min_quote_volume"))
        if not isinstance(name, str) or not name.strip() or name in seen_names:
            return None
        if not leader or floor is None or not isinstance(members, (list, tuple)) or not members:
            return None
        bases = {_base(m) for m in members}
        if "" in bases or leader in bases or bases & seen or leader in seen:
            return None
        seen_names.add(name)
        seen |= bases | {leader}
        out.append(ScanClass(name, leader, frozenset(bases), floor))
    return tuple(out)


def scanner_config(cfg: dict) -> dict:
    """The scanner keys a preset sets, as the runner and the card carry them."""
    return {k: cfg[k] for k in SCANNER_KEYS if isinstance(cfg, dict) and k in cfg}


def _canonical(v: Any) -> Any:
    """A whole float as an int: JSON from a browser has one number type, so
    the card's 5.0 comes back from the Lab's page as 5."""
    if isinstance(v, dict):
        return {k: _canonical(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canonical(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def scanner_config_arg(config: dict) -> str:
    """``--scanner-config``'s text: one serialisation for the generator and
    the Lab, so a card and its reproduction pass the runner the same bytes."""
    return json.dumps(_canonical(config), sort_keys=True, separators=(",", ":"))


def params_from_preset(cfg: dict) -> Optional[ScannerParams]:
    """The scanner's numbers from a preset, or None when one is unreadable.

    A preset that names a number the scanner cannot read is not run on a
    default in its place: the card would describe a rule nobody applies.
    """
    if not preset_is_limit_scanner(cfg):
        return None
    base = ScannerParams()
    kw: dict[str, Any] = {}
    for key in _NUMBER_KEYS:
        if key not in cfg:
            continue
        val = _positive(cfg[key])
        if val is None:
            return None
        kw[key] = val
    for key in ("atr_period",) + _COUNT_KEYS:
        if key not in cfg:
            continue
        p = cfg[key]
        if isinstance(p, bool) or not isinstance(p, int) or p < 1:
            return None
        kw[key] = p
    if "classes" in cfg:
        # A class carries its own leader and floor; the single-class keys
        # beside it would be a second answer to the same question.
        if "leader" in cfg or "min_quote_volume" in cfg:
            return None
        classes = _read_classes(cfg["classes"])
        if classes is None:
            return None
        kw["classes"] = classes
    if "leader" in cfg:
        leader = _base(cfg["leader"])
        if not leader:
            return None
        kw["leader"] = leader
    if "allow_short" in cfg:
        if not isinstance(cfg["allow_short"], bool):
            return None
        kw["allow_short"] = cfg["allow_short"]
    for key in ("scale_unread_book", "imbalance_filter", "neutral_on_tie", "reclaim_fill",
                "include_equities"):
        if key not in cfg:
            continue
        if not isinstance(cfg[key], bool):
            return None
        kw[key] = cfg[key]
    if "volume_rank" in cfg:
        if cfg["volume_rank"] not in ("percentile", "minmax"):
            return None
        kw["volume_rank"] = cfg["volume_rank"]
    for key in ("vol_entry_offset", "cancel_adverse_bp"):
        if key not in cfg:
            continue
        raw = cfg[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        val = float(raw)
        if not (val == val and 0 <= val < float("inf")):
            return None
        kw[key] = val
    if "stop_floor_pct" in cfg:
        floors = cfg["stop_floor_pct"]
        if not isinstance(floors, dict):
            return None
        read = {}
        for sym, pct in floors.items():
            val = _positive(pct)
            if val is None or not _base(sym):
                return None
            read[_base(sym)] = val
        kw["stop_floor_pct"] = read
    for key in _FRACTION_KEYS:
        if kw.get(key, getattr(base, key)) > 1.0:
            return None
    if "step_locks" in cfg:
        steps = cfg["step_locks"]
        if not isinstance(steps, (list, tuple)):
            return None
        read_steps = []
        for step in steps:
            if not isinstance(step, (list, tuple)) or len(step) != 2:
                return None
            trigger, lock = _positive(step[0]), _positive(step[1])
            if trigger is None or lock is None or lock >= trigger:
                return None
            read_steps.append((trigger, lock))
        kw["step_locks"] = tuple(sorted(read_steps))
    if kw.get("min_score", base.min_score) > sum(WEIGHTS.values()):
        return None
    if kw.get("tp1_pct", base.tp1_pct) >= kw.get("tp2_pct", base.tp2_pct):
        return None
    return ScannerParams(**kw)


#: What the frozen bars cannot carry, said on the card beside its figures.
BACKTEST_READS = (
    "The frozen 1h bars carry no order book, funding or taker flow. An unread "
    "order book is not given points: the other four dimensions, 80 in all, are "
    "scaled onto 100 so the same 70 is the same fraction of what was read as a "
    "live pass that saw the book. Quote volume is a percentile across the class, "
    "so one busy market does not take the whole volume score. The backtest skips "
    "nothing for a wall or for funding, and reads each leader's side from its day "
    "and VWAP alone. Its limit rests from the bar after the one it was decided at.")


def _pct(v: float) -> str:
    return f"{v:g}%"


def how_line(cfg: dict) -> str:
    """What the rule does, read off the preset's own numbers. Percent, ratio
    and count only; the volume floor is a market fact in USDT."""
    p = params_from_preset(cfg)
    if p is None:
        return "Its numbers could not be read, so no pass runs it."
    syms = cfg.get("symbols")
    leaders = {c.leader for c in p.classes} or {p.leader}
    n = len([s for s in syms if _base(s) not in leaders]) if isinstance(syms, (list, tuple)) else 0
    floors: dict[float, list[str]] = {}
    for sym, pct in sorted(p.stop_floor_pct.items()):
        if sym not in leaders:
            floors.setdefault(pct, []).append(sym)
    floor_text = ", ".join(f"{_pct(pct)} for {' and '.join(names)}"
                           for pct, names in sorted(floors.items(), reverse=True))
    sides = "long or short" if p.allow_short else "long only"
    weights = ", ".join(f"{name} {w:g}" for name, w in (
        ("momentum against the leader", WEIGHTS["momentum"]),
        ("VWAP position", WEIGHTS["vwap"]), ("24-hour range position", WEIGHTS["range"]),
        ("order-book balance", WEIGHTS["orderbook"]), ("quote volume", WEIGHTS["volume"])))
    def _vol(v: float) -> str:
        return f"{v / 1e6:g}M" if v >= 1e5 else f"{v:g}"

    steps = ", ".join(f"+{_pct(lock)} at +{_pct(trigger)}" for trigger, lock in p.step_locks)
    if p.classes:
        groups = "; ".join(
            f"{len(c.members)} {c.name} markets behind {c.leader}, with a floor of "
            f"{_vol(c.min_quote_volume)} USDT of 24-hour quote volume"
            for c in p.classes)
        gate = (
            f"The rule covers {groups}; {sides}. Each class is scored and placed on its "
            f"own, behind its leader, which is a gate and never traded: up on the day and "
            f"above its 24-hour VWAP opens the long side for its class; down and below "
            f"opens the short side; one of the two is half size, and a mixed or unreadable "
            f"leader opens nothing. Each member is scored out of 100 for that side: "
            f"{weights}.")
        floor_words = "under its class's volume floor"
        best = "each class's best market"
        thin = [c.name for c in p.classes if c.min_quote_volume < 1_000_000]
        thin_text = (f" The {' and '.join(thin)} floor admits thin markets, whose real "
                     f"slippage is more than the backtest's flat 0.05%." if thin else "")
    else:
        gate = (
            f"The rule covers {n} USDT perpetuals, {sides}, with {p.leader} as the regime "
            f"gate, never traded. {p.leader} up on the day and above its 24-hour VWAP "
            f"opens the long side; down and below opens the short side; one of the two "
            f"is half size, and a mixed or unreadable {p.leader} opens nothing. Each other "
            f"market is scored out of 100 for that side: {weights}.")
        floor_words = f"under {_vol(p.min_quote_volume)} USDT of 24-hour quote volume"
        best = "the best market"
        thin_text = ""
    return (
        gate + f" A dimension that could not be read "
        f"adds nothing. At {p.min_score:g} or more, {best} gets one limit a pass "
        f"at its 24-hour VWAP less {p.atr_limit_mult:g} ATR({p.atr_period}) for a long "
        f"(plus, for a short), never through the market and never more than "
        f"{_pct(p.limit_chase_pct)} from it. The stop is the 24-hour low (high), at least "
        f"{floor_text} and {_pct(p.alt_stop_floor_pct)} for the rest. A market more "
        f"than {_pct(p.max_vwap_ext_pct)} past its "
        f"VWAP on the entry side, {floor_words}, against an "
        f"order-book wall, or paying funding of {p.funding_skip_bps:g} bps or more is "
        f"skipped.{thin_text} Its exits are its own: {p.scale_out_fraction * 100:g}% of the position "
        f"closes at +{_pct(p.scale_out_trigger_pct)}, {p.tp1_fraction * 100:g}% of what is "
        f"left at +{_pct(p.tp1_pct)} and the rest at +{_pct(p.tp2_pct)}; the stop steps up "
        f"to {steps}; after the first close the rest trails {p.trail_atr_mult:g} ATR "
        f"behind its best price; and a pullback entry is closed at "
        f"{p.pullback_time_stop_hours:g} hours. The playbook's source was not provided, "
        f"so the share the +{_pct(p.tp1_pct)} target closes is a reading of its "
        f"description (its v0.1 share). Its breakout entries are not ported, so its "
        f"{p.time_stop_hours:g}-hour general stop closes nothing a pullback entry has "
        f"not already closed." + _portfolio_words(p, leaders)
    )


def _portfolio_words(p: ScannerParams, leaders: set) -> str:
    """The portfolio sentence: the playbook's caps, and the house's limits."""
    caps = []
    if p.max_concurrent is not None:
        caps.append(f"{p.max_concurrent} markets")
    if p.max_correlated_alts is not None:
        majors = sorted(set(p.stop_floor_pct) - set(leaders))
        caps.append(f"{p.max_correlated_alts} of one class's alts (every market but "
                    f"{', '.join(majors)} and the leaders)")
    held = (f" It holds at most {' and '.join(caps)} at once, open or resting, so a "
            f"pass places nothing while either is full." if caps else "")
    return (held + " Every entry is sized and may be refused by the house risk gate; "
            "the playbook's own per-trade loss cap and daily circuit are looser than "
            "the house's and are not applied.")


# ── one symbol's day, from closed 1h bars ────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    """One symbol's 24h reading at a closed bar. None is unread, never zero.

    ``bid_volume``/``ask_volume`` (resting book), ``funding_bps`` (this
    settlement's rate, in basis points) and ``taker_ratio`` (buy over sell
    taker volume) are read live; the frozen bars carry none of them.
    """

    symbol: str
    last: Optional[float] = None
    vwap: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    change_pct: Optional[float] = None
    quote_volume: Optional[float] = None
    atr: Optional[float] = None
    bid_volume: Optional[float] = None
    ask_volume: Optional[float] = None
    funding_bps: Optional[float] = None
    taker_ratio: Optional[float] = None
    # Best prices. None is unread. A limit at or through the opposite price
    # is a taker; the pullback refuses it when these were read.
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None


#: Closed 1h bars one snapshot is read from: the day (25), the ATR span and
#: room to spare. The backtest's pass and the live scan read the same window.
SNAPSHOT_BARS = 64


def snapshot_from_bars(symbol: str, rows: Sequence[Sequence[Any]],
                       params: ScannerParams, **live: Optional[float]) -> Snapshot:
    """The 24h reading over the last closed 1h ``rows`` ([ts, o, h, l, c, v]).

    The day is the last 24 bars. Its change is measured against the close
    24 bars before the last, so it needs 25 bars; an ATR needs its period
    plus one. A field that cannot be measured is None. ``live`` passes the
    book, funding and taker readings when the caller has them.
    """
    rows = list(rows or [])
    day = rows[-DAY_BARS:]
    last = price_on_record(rows[-1][4]) if rows and len(rows[-1]) > 4 else None
    vwap = _calculate_vwap(day) if len(day) == DAY_BARS else None
    highs = [price_on_record(r[2]) if len(r) > 2 else None for r in day]
    lows = [price_on_record(r[3]) if len(r) > 3 else None for r in day]
    full_day = len(day) == DAY_BARS
    high = max(highs) if full_day and None not in highs else None  # type: ignore[type-var]
    low = min(lows) if full_day and None not in lows else None  # type: ignore[type-var]
    change = None
    if len(rows) > DAY_BARS and last is not None:
        ref_row = rows[-DAY_BARS - 1]
        ref = price_on_record(ref_row[4]) if len(ref_row) > 4 else None
        if ref is not None:
            change = (last / ref - 1.0) * 100.0
    quote = None
    if full_day:
        total = 0.0
        for r in day:
            c = price_on_record(r[4]) if len(r) > 4 else None
            v = volume_on_record(r[5]) if len(r) > 5 else None
            if c is None or v is None:
                total = -1.0
                break
            total += c * v
        quote = total if total >= 0 else None
    span = rows[-(params.atr_period + 1) * 3:]
    hs = [price_on_record(r[2]) if len(r) > 2 else None for r in span]
    ls = [price_on_record(r[3]) if len(r) > 3 else None for r in span]
    cs = [price_on_record(r[4]) if len(r) > 4 else None for r in span]
    atr = None
    if None not in hs and None not in ls and None not in cs:
        atr = atr_reading(hs, ls, cs, params.atr_period)
    return Snapshot(symbol=symbol, last=last, vwap=vwap, high=high, low=low,
                    change_pct=change, quote_volume=quote, atr=atr,
                    bid_volume=live.get("bid_volume"), ask_volume=live.get("ask_volume"),
                    funding_bps=live.get("funding_bps"), taker_ratio=live.get("taker_ratio"),
                    bid_price=live.get("bid_price"), ask_price=live.get("ask_price"))


# ── the leader's regime ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Regime:
    """The side the leader allows, and at what size.

    ``side`` None is "opens nothing": a mixed or flat leader (``read`` True)
    or a leader that could not be read (``read`` False) -- two facts, two
    sentences.
    """

    side: Optional[str]
    size_factor: float
    votes: int
    read: bool
    reason: str


def leader_regime(leader: Snapshot, params: ScannerParams) -> Regime:
    """The playbook's gate: day change and VWAP position, taker flow a third vote."""
    if leader.change_pct is None or leader.last is None or leader.vwap is None:
        return Regime(None, 0.0, 0, False, "the leader's day could not be read")
    up, down = leader.change_pct > 0, leader.change_pct < 0
    above, below = leader.last > leader.vwap, leader.last < leader.vwap
    taker = leader.taker_ratio
    buy = taker is not None and taker > 1.0
    sell = taker is not None and taker < 1.0
    long_votes = int(up) + int(above) + int(buy)
    short_votes = int(down) + int(below) + int(sell)
    if long_votes >= 2:
        return Regime(LONG, 1.0, long_votes, True, "the leader is constructive")
    if params.allow_short and short_votes >= 2:
        return Regime(SHORT, 1.0, short_votes, True, "the leader is weak")
    # A one-vote tie used to break toward long. That bias is off unless asked.
    if params.neutral_on_tie and long_votes == short_votes:
        return Regime(None, 0.0, long_votes, True, "the leader is mixed")
    if long_votes == 1 and long_votes >= short_votes:
        return Regime(LONG, 0.5, long_votes, True, "the leader is partly constructive")
    if params.allow_short and short_votes == 1:
        return Regime(SHORT, 0.5, short_votes, True, "the leader is partly weak")
    return Regime(None, 0.0, max(long_votes, short_votes), True, "the leader is mixed")


# ── the score ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Scored:
    """One symbol's score for a side. ``dims[name]`` None is unread."""

    symbol: str
    side: str
    points: float
    dims: Mapping[str, Optional[float]]
    skip: str


def _is_tokenized_equity(symbol: str) -> bool:
    """True for a Bitget stock perp (``AAPLON/USDT``, ``RAAPL/USDT``).

    The list is ``US_STOCK_SYMBOLS``. A leading R is not enough: RENDER is
    not an equity.
    """
    from bot.core.stock_trading import is_stock_symbol
    raw = str(symbol or "").upper().replace(" ", "")
    base = _base(raw)
    candidates = {raw, f"{base}/USDT"}
    if raw.endswith("USDT") and "/" not in raw:
        candidates.add(raw[:-4] + "/USDT")
    return any(is_stock_symbol(c) for c in candidates)


def _percentile(value: Optional[float], values: Sequence[float]) -> Optional[float]:
    """Where ``value`` sits in ``values``, 0 at the minimum and 1 at the maximum.

    The count of readings strictly below, over one less than the count. A
    single reading, or every reading equal, ranks nothing: there is no
    spread to place it on. One very large reading then leaves the next
    name in the middle of the class, not at zero.
    """
    if value is None:
        return None
    xs = [v for v in values if v is not None]
    if len(xs) < 2:
        return None
    if max(xs) <= min(xs):
        return None
    below = sum(1 for v in xs if v < value)
    return below / (len(xs) - 1)


def bar_score(points: float, dims: Mapping[str, Optional[float]], *,
              scale_unread_book: bool = True) -> float:
    """The score the minimum is compared with.

    ``points`` is the raw sum. Dimensions that were not read are absent
    from that sum. When the order book is the one that was not read, and
    scaling is on, those points are a fraction of 80 and the bar is 70 of
    100, so the fraction is rewritten onto 100. Other unread dimensions
    stay in the 80 as zeros: one readable dimension does not become 100.
    """
    if not scale_unread_book or dims.get("orderbook") is not None:
        return points
    denom = sum(WEIGHTS.values()) - WEIGHTS["orderbook"]
    if denom <= 0:
        return points
    return points / denom * 100.0


def _rank(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> Optional[float]:
    """Where ``value`` sits between the universe's lowest and highest, 0..1.

    None when it is unread or the universe has no spread to rank on: a
    single reading, or every reading equal, ranks nothing.
    """
    if value is None or lo is None or hi is None or hi <= lo:
        return None
    return (value - lo) / (hi - lo)


def score_universe(snaps: Sequence[Snapshot], leader: Snapshot, side: str,
                   params: ScannerParams) -> list[Scored]:
    """Score every symbol for ``side``, best first; skipped ones last."""
    is_long = side == LONG
    rels = {}
    if leader.change_pct is not None:
        rels = {s.symbol: s.change_pct - leader.change_pct
                for s in snaps if s.change_pct is not None}
    rel_lo = min(rels.values()) if rels else None
    rel_hi = max(rels.values()) if rels else None
    vols = [s.quote_volume for s in snaps if s.quote_volume is not None]
    vol_lo = min(vols) if vols else None
    vol_hi = max(vols) if vols else None
    cap = params.max_vwap_ext_pct / 100.0
    out: list[Scored] = []
    for s in snaps:
        dims: dict[str, Optional[float]] = dict.fromkeys(WEIGHTS)
        skip = ""
        r = _rank(rels.get(s.symbol), rel_lo, rel_hi)
        if r is not None:
            dims["momentum"] = WEIGHTS["momentum"] * (r if is_long else 1.0 - r)
        if s.last is not None and s.vwap is not None:
            above, below = s.last > s.vwap * 1.001, s.last < s.vwap * 0.999
            favour, against = (above, below) if is_long else (below, above)
            dims["vwap"] = WEIGHTS["vwap"] * (1.0 if favour else 0.0 if against else 0.5)
            ext = (s.last - s.vwap) / s.vwap
            if (ext > cap) if is_long else (ext < -cap):
                skip = "extended beyond the VWAP cap"
        if s.last is not None and s.high is not None and s.low is not None and s.high > s.low:
            pos = (s.last - s.low) / (s.high - s.low)
            if is_long:
                frac = 1.0 if pos > 0.66 else 0.5 if pos >= 0.33 else 0.0
            else:
                frac = 1.0 if pos < 0.34 else 0.5 if pos <= 0.67 else 0.0
            dims["range"] = WEIGHTS["range"] * frac
        bid, ask = s.bid_volume, s.ask_volume
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            favour_ratio = bid / ask if is_long else ask / bid
            if favour_ratio >= params.bidask_full_ratio:
                frac = 1.0
            elif favour_ratio >= params.bidask_partial_ratio:
                frac = 0.6
            elif favour_ratio >= 0.8:
                frac = 0.4
            else:
                frac = 0.2
            dims["orderbook"] = WEIGHTS["orderbook"] * frac
            if 1.0 / favour_ratio >= params.bidask_wall_ratio:
                skip = skip or "an opposing order-book wall"
        if params.volume_rank == "minmax":
            rv = _rank(s.quote_volume, vol_lo, vol_hi)
        else:
            rv = _percentile(s.quote_volume, vols)
        if rv is not None:
            dims["volume"] = WEIGHTS["volume"] * rv
        if _is_tokenized_equity(s.symbol):
            if not params.include_equities:
                skip = skip or "tokenized equity is excluded"
            elif dims.get("orderbook") is None:
                # The v2 book fails for these names. Leaving the dimension
                # absent would scale the other 80 onto 100 and hide that a
                # fifth of the score was not read. Zero keeps the weight.
                dims["orderbook"] = 0.0
                skip = skip or "the order book was not read"
        if s.quote_volume is not None and s.quote_volume < params.min_quote_volume:
            skip = skip or "quote volume below the floor"
        if params.imbalance_filter:
            taker = s.taker_ratio
            opposes = taker is None or (taker <= 1.0 if is_long else taker >= 1.0)
            if opposes:
                skip = skip or "taker flow opposes the side or was not read"
        if s.funding_bps is not None:
            pays = s.funding_bps >= params.funding_skip_bps if is_long \
                else s.funding_bps <= -params.funding_skip_bps
            if pays:
                skip = skip or "funding at or beyond the skip threshold"
        points = sum(v for v in dims.values() if v is not None)
        out.append(Scored(s.symbol, side, points, dims, skip))
    out.sort(key=lambda x: (not x.skip, x.points), reverse=True)
    return out


# ── the one order ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EntryPlan:
    """The resting limit a pass places. Prices, and fractions of price."""

    symbol: str
    side: str
    limit_price: float
    stop: float
    take_profit: float
    stop_frac: float
    size_factor: float
    points: float


def plan_pullback(snap: Snapshot, side: str, size_factor: float,
                  params: ScannerParams) -> tuple[Optional[EntryPlan], str]:
    """The pullback limit for ``snap``, or (None, why not)."""
    if snap.vwap is None or snap.atr is None or snap.last is None:
        return None, "its VWAP, ATR or last price could not be read"
    if snap.low is None or snap.high is None:
        return None, "its 24h range could not be read"
    offset = (params.atr_limit_mult + params.vol_entry_offset) * snap.atr
    if side == LONG:
        px = snap.vwap - offset
        # A buy at or above the last trade is a market buy. The 3% distance
        # cap is how far a resting limit may sit, not permission to cross.
        if px >= snap.last:
            return None, "the limit would cross the market"
        if snap.ask_price is not None and px >= snap.ask_price:
            return None, "the limit would cross the best ask"
        structure = (px - snap.low) / px if px > snap.low else 0.0
    else:
        px = snap.vwap + offset
        if px <= snap.last:
            return None, "the limit would cross the market"
        if snap.bid_price is not None and px <= snap.bid_price:
            return None, "the limit would cross the best bid"
        structure = (snap.high - px) / px if snap.high > px else 0.0
    if not px > 0:
        return None, "the limit price is not a price"
    gap = (snap.last - px) / px if side == LONG else (px - snap.last) / px
    if gap > params.limit_chase_pct / 100.0:
        return None, "the limit is further from the market than the chase cap"
    frac = max(structure, params.stop_floor(snap.symbol))
    tp = params.tp1_pct / 100.0
    if side == LONG:
        stop, target = px * (1.0 - frac), px * (1.0 + tp)
    else:
        stop, target = px * (1.0 + frac), px * (1.0 - tp)
    if not (stop > 0 and target > 0):
        return None, "the stop or target is not a price"
    return EntryPlan(snap.symbol, side, px, stop, target, frac, size_factor, 0.0), ""


@dataclass(frozen=True)
class Pass:
    """One scan pass: the regime, the board, and the order it places, if any."""

    regime: Regime
    board: tuple[Scored, ...]
    plan: Optional[EntryPlan]
    reason: str


def scan_pass(snaps: Sequence[Snapshot], params: ScannerParams,
              held: frozenset[str] = frozenset()) -> Pass:
    """The whole pass over ``snaps`` (the leader among them, or not).

    ``held`` names the symbols that already hold a position or a resting
    order from this scanner; they are scored for the board and not placed.
    """
    lead = next((s for s in snaps if _base(s.symbol) == params.leader), None)
    if lead is None:
        reg = Regime(None, 0.0, 0, False, "the leader's day could not be read")
        return Pass(reg, (), None, reg.reason)
    reg = leader_regime(lead, params)
    others = [s for s in snaps if _base(s.symbol) != params.leader]
    if reg.side is None:
        return Pass(reg, tuple(score_universe(others, lead, LONG, params)), None, reg.reason)
    board = score_universe(others, lead, reg.side, params)
    for row in board:
        scored = bar_score(row.points, row.dims, scale_unread_book=params.scale_unread_book)
        if row.skip or scored < params.min_score:
            break
        if _base(row.symbol) in held:
            continue
        snap = next(s for s in others if s.symbol == row.symbol)
        plan, why = plan_pullback(snap, reg.side, reg.size_factor, params)
        if plan is None:
            return Pass(reg, tuple(board), None, f"{row.symbol}: {why}")
        plan = EntryPlan(plan.symbol, plan.side, plan.limit_price, plan.stop,
                         plan.take_profit, plan.stop_frac, plan.size_factor, row.points)
        return Pass(reg, tuple(board), plan, "")
    return Pass(reg, tuple(board), None, "no symbol at or above the minimum score")


# ── the exits ───────────────────────────────────────────────────────────────
#
# The playbook's management, read off its description (its source was not
# provided): 35% of the position closes at +3.5% (the scale-out); half of what
# is left closes at +5% (TP1; the share is the v0.1 playbook's own TP1 share,
# a reading); the rest closes at +20% (TP2). The stop steps up as the move
# grows: at +2% it locks +1.5% (the break-even lock), at +4% +3%, at +6% +4.5%.
# After the scale-out, what is left trails two ATR behind its best price. A
# pullback entry is closed at 4 hours, as the v0.1 playbook's time stop closed
# every position at its age; the 12-hour general stop is for entries without a
# stop of their own (the playbook's breakouts, which are not ported).

@dataclass
class ExitState:
    """One position's exit book. Prices are prices; ``remaining`` is a share
    of the original quantity (1.0 is all of it)."""

    side: str
    entry: float
    stop: float
    atr: Optional[float]
    opened_ms: int
    remaining: float = 1.0
    best: float = 0.0
    scaled: bool = False
    tp1_hit: bool = False
    locked: bool = False
    trailing: bool = False

    def __post_init__(self) -> None:
        if not self.best:
            self.best = self.entry


@dataclass(frozen=True)
class ExitFill:
    """A close the bar made: ``share`` of the ORIGINAL quantity at ``price``."""

    share: float
    price: float
    reason: str


def _move(state: ExitState, price: float) -> float:
    """``price``'s move from the fill in the position's favour, a fraction."""
    signed = (price - state.entry) / state.entry
    return signed if state.side == LONG else -signed


def _at(state: ExitState, pct: float) -> float:
    """The price ``pct`` percent from the fill in the position's favour."""
    f = pct / 100.0
    return state.entry * (1.0 + f) if state.side == LONG else state.entry * (1.0 - f)


def step_exit(state: ExitState, params: ScannerParams, *, open_: float, high: float,
              low: float, close: float, close_ms: int) -> list[ExitFill]:
    """One closed bar against ``state``; returns the closes it made.

    The stop is read first against the adverse extreme (a gap fills at the
    open, worse than the stop), then the targets against the favourable one,
    then the stop is raised for the NEXT bar from this bar's best, and the
    time stops read the close. ``state`` is updated in place.
    """
    fills: list[ExitFill] = []
    is_long = state.side == LONG
    adverse, favourable = (low, high) if is_long else (high, low)
    if state.remaining <= 0:
        return fills
    hit = adverse <= state.stop if is_long else adverse >= state.stop
    if hit:
        price = min(state.stop, open_) if is_long else max(state.stop, open_)
        reason = "TRAILING_SL" if state.trailing else "LOCK_SL" if state.locked else "SL"
        fills.append(ExitFill(state.remaining, price, reason))
        state.remaining = 0.0
        return fills
    reach = _move(state, favourable) * 100.0
    if not state.scaled and reach >= params.scale_out_trigger_pct:
        share = min(params.scale_out_fraction, state.remaining)
        fills.append(ExitFill(share, _at(state, params.scale_out_trigger_pct), "SCALE_OUT"))
        state.remaining -= share
        state.scaled = True
    if state.remaining > 0 and not state.tp1_hit and reach >= params.tp1_pct:
        share = state.remaining * params.tp1_fraction
        fills.append(ExitFill(share, _at(state, params.tp1_pct), "TP1"))
        state.remaining -= share
        state.tp1_hit = True
    if state.remaining > 0 and reach >= params.tp2_pct:
        fills.append(ExitFill(state.remaining, _at(state, params.tp2_pct), "TP2"))
        state.remaining = 0.0
        return fills
    # Raise the stop for the next bar: the step locks, then the trail.
    if _move(state, favourable) > _move(state, state.best):
        state.best = favourable
    best_pct = _move(state, state.best) * 100.0
    for trigger, lock in params.step_locks:
        if best_pct >= trigger:
            level = _at(state, lock)
            if (level > state.stop) if is_long else (level < state.stop):
                state.stop = level
                state.locked = True
    if state.scaled and state.atr is not None and state.atr > 0:
        dist = params.trail_atr_mult * state.atr
        level = state.best - dist if is_long else state.best + dist
        if (level > state.stop) if is_long else (level < state.stop):
            state.stop = level
            state.trailing = True
    hours = (close_ms - state.opened_ms) / 3_600_000.0
    if hours >= params.time_stop_hours:
        fills.append(ExitFill(state.remaining, close, "TIME_STOP"))
        state.remaining = 0.0
    elif hours >= params.pullback_time_stop_hours:
        fills.append(ExitFill(state.remaining, close, "PULLBACK_TIME_STOP"))
        state.remaining = 0.0
    return fills


def class_of(params: ScannerParams, symbol: str) -> Optional[str]:
    """The name of the class ``symbol`` trades in: ``"all"`` for a preset
    with one class, None for a symbol no class names."""
    if not params.classes:
        return "all"
    base = _base(symbol)
    return next((c.name for c in params.classes
                 if base in c.members or base == c.leader), None)


def is_alt(params: ScannerParams, symbol: str) -> bool:
    """An alt is a market with no tier floor of its own (`stop_floor`'s
    reading): the playbook's tiers are BTC and ETH, SOL and BNB, and alts."""
    return _base(symbol) not in params.stop_floor_pct


def portfolio_cap(params: ScannerParams, symbol: str, held: Sequence[str]) -> Optional[str]:
    """Why ``symbol`` may not join ``held``, the scanner's open positions and
    resting limits; None when it may.

    The playbook's portfolio layer: at most ``max_concurrent`` at once, and
    at most ``max_correlated_alts`` alts of one class, since a class's alts
    move with its leader and with each other. A cap the preset does not set
    is not applied here; the house risk gate's caps apply either way.
    """
    if params.max_concurrent is not None and len(held) >= params.max_concurrent:
        return f"{params.max_concurrent} markets already held"
    if params.max_correlated_alts is not None and is_alt(params, symbol):
        cls = class_of(params, symbol)
        alts = [h for h in held if is_alt(params, h) and class_of(params, h) == cls]
        if len(alts) >= params.max_correlated_alts:
            return f"{params.max_correlated_alts} of its class's alts already held"
    return None


def class_params(params: ScannerParams, cls: ScanClass) -> ScannerParams:
    """``params`` for one class: its leader and its volume floor."""
    return dataclasses.replace(params, leader=cls.leader,
                               min_quote_volume=cls.min_quote_volume, classes=())


def scan_classes(snaps: Sequence[Snapshot], params: ScannerParams,
                 held: frozenset[str] = frozenset()) -> list[tuple[str, Pass]]:
    """One pass per class, each over its own leader and members; at most
    one order per class. Without classes, one pass over everything."""
    if not params.classes:
        return [("all", scan_pass(snaps, params, held))]
    out = []
    for cls in params.classes:
        mine = [s for s in snaps
                if _base(s.symbol) == cls.leader or _base(s.symbol) in cls.members]
        out.append((cls.name, scan_pass(mine, class_params(params, cls), held)))
    return out
