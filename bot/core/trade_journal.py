"""
RUNECLAW Trade Journal — structured trade documentation and AI review.

For each closed trade, generates a structured journal entry with:
  - Trade parameters (entry, exit, SL, TP, size, leverage)
  - What signals were used and their accuracy
  - Market conditions at entry (regime, session, volatility)
  - Outcome analysis (PnL, R-multiple, holding time)
  - Lessons learned (auto-generated from pattern matching)

Weekly AI-generated performance review summarizing patterns.
Persisted to disk for historical analysis.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from bot.utils.paths import state_path
from bot.utils.win_rate import pnl_stats, trade_pnl, win_stats

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """Structured trade journal entry."""
    trade_id: str
    symbol: str
    direction: str
    strategy_type: str

    # Prices
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float

    # Results
    pnl: float
    pnl_pct: float
    #: actual PnL / initial risk -- ``None`` when the risk could not be read.
    #: OPTIONAL, and the reason is the whole point: R is a RATIO AGAINST THE
    #: STOP, so a close whose stop nobody recorded has no R at all. It is not
    #: 0R (a trade that came back to its stop distance) and it is certainly not
    #: pnl/entry_price, which is what the old arithmetic produced. See
    #: `r_multiple_for`.
    r_multiple: Optional[float]
    holding_hours: float

    # Context at entry
    regime: str = ""
    session: str = ""
    volatility: str = ""
    confidence: float = 0.0
    signals_used: list = field(default_factory=list)
    #: Base-currency size the R was scored against; ``None`` for an entry
    #: journaled before sizes were recorded, whose stored R is then NOT loaded.
    quantity: Optional[float] = None

    # Analysis
    exit_reason: str = ""  # "sl_hit", "tp_hit", "trailing", "manual", "partial_tp"
    # WHERE THIS TRADE HAPPENED. The live close path records here, not into the
    # paper portfolio's TradeExecution, so attributing only that one would leave
    # every REAL trade venue-blind — the half that matters. Defaults to the
    # venue every existing entry is on; it is a back-fill of a fact, not a guess.
    venue: str = "bitget"
    lessons: list = field(default_factory=list)
    tags: list = field(default_factory=list)  # "winner", "loser", "breakeven", "runner", etc.

    timestamp: float = 0.0
    #: WHOSE trade. The journal is one store fed by every account's close
    #: callback, and `find_trade` is by an id that is NOT unique across
    #: accounts (`TI-adopted-{SYM}-{second}`), so an entry that knows its
    #: owner is handed to that owner only. "" on every entry written before
    #: this field existed — those are unattributed, not the caller's.
    user_id: str = ""


def r_multiple_for(entry_price: float, stop_loss: float, pnl: float,
                   quantity: Optional[float]) -> Optional[float]:
    """R for this close, or ``None`` when the risk it is a ratio OF is unknown.

    THE OLD ARITHMETIC FABRICATED ONE. It was::

        initial_risk = abs(entry_price - stop_loss)
        r_multiple = pnl / (initial_risk * ±1) if initial_risk > 0 else 0

    and its two callers both hand it ``float(getattr(pos, "stop_loss", 0) or 0)``
    — the absent-field-is-zero shape, on the one field the whole calculation is
    a ratio against. So a close with no recorded stop (an ORPHAN: a position
    the bot did not open, which is exactly the kind whose stop it cannot read)
    arrived here as ``stop_loss = 0.0``, and then::

        initial_risk = abs(entry - 0) = entry
        r_multiple   = pnl / entry_price

    which is not an R-multiple at all. It is P&L over the entry price — a
    different quantity entirely, in the right units, printed on the weekly
    review as ``Avg R-Multiple: +0.02R`` and ``Best: SOL $+41.00 (0.0R)``.
    Not a rounding error: a number with no meaning wearing the name of one
    that has.

    The ``else 0`` branch was the quieter half of the same bug. Both prices
    unreadable gives ``abs(0 - 0) == 0``, and 0R is a REAL outcome — a trade
    that ended exactly at its risk distance — so an unmeasurable close entered
    the record indistinguishable from a measured break-even. Same defect as
    the ghost close booked at its entry price, one module over.

    ``None`` for both cases. A stop of zero is not a stop.

    AND THE ARITHMETIC THAT SURVIVED THAT FIX WAS STILL NOT AN R. It divided
    by ``|entry - stop|`` — the risk per UNIT — with no quantity, so it equalled
    R only for a trade of exactly one coin: a 0.1 ETH trade risking $10 that
    made +$11.40 read +0.11R, and a 5,000 DOGE trade over-read by 5,000x. And it
    negated the divisor for a SHORT, so a losing short printed a POSITIVE R and
    a winning short a negative one — on the weekly review's Avg R, Best and
    Worst, and in ``_generate_lessons`` ("Full stop hit" fires on ``r < -0.8``)
    and ``_generate_tags`` ("runner" on ``r >= 3.0``). The post-mortem leaf had
    already computed its own R over dollar risk and declined to print the
    journal's, which is how this was found.

    R is net P&L over the DOLLAR risk the stop defined — ``|entry - stop| *
    quantity`` — with the sign of the P&L, for either direction. ``None`` when
    the quantity is not on record (an entry journaled before sizes were), so
    that the review counts it as unscored rather than averaging in a number
    that is not an R. ``trade_postmortem.realized_r`` is this function.
    """
    try:
        entry = float(entry_price)
        stop = float(stop_loss)
    except (TypeError, ValueError):
        return None
    # `<= 0`, not `is None`: these arrive already coerced by the callers, so
    # zero IS the absent value here and there is no earlier seam to read.
    # A real stop is a positive price on every venue this trades.
    if entry <= 0 or stop <= 0:
        return None
    try:
        qty = float(quantity) if quantity is not None else None
    except (TypeError, ValueError):
        return None
    if qty is None or qty <= 0:
        return None
    risk_usd = abs(entry - stop) * qty
    if risk_usd <= 0:
        return None
    try:
        return float(pnl) / risk_usd
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def r_unknown_reason(entry) -> Optional[str]:
    """Why an entry carries no R — ``"no_stop"``, ``"no_quantity"`` — or
    ``None`` when it carries one (or the reason is not one of those two). The
    card prints the reason, because "R unknown" alone reads as a defect and
    the two causes have different remedies: a stop nobody recorded cannot be
    recovered; a quantity missing from an entry journaled before sizes were
    recorded is a fact about the record's age."""
    if getattr(entry, "r_multiple", None) is not None:
        return None
    # Read as three values, never coerced: an absent price is not a price of
    # zero, and a zero on record is exactly the absent-stop shape this reads.
    if not (_positive(getattr(entry, "stop_loss", None))
            and _positive(getattr(entry, "entry_price", None))):
        return "no_stop"
    if not _positive(getattr(entry, "quantity", None)):
        return "no_quantity"
    return None


def _positive(v) -> bool:
    """True only for a value that reads as a number greater than zero."""
    if v is None:
        return False
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def average_r(entries) -> dict:
    """Mean R over the entries that HAVE one, with the coverage beside it.

    Three fields, not one, for the reason `win_stats` carries `scored` and
    `unscored`: "0.42R over 20 trades" and "0.42R over the 6 of 20 we could
    price" are different readings and only the coverage tells them apart.
    ``avg`` is ``None`` when nothing could be priced — never 0.0, which is a
    real R a real trade can post.
    """
    rs = []
    total = 0
    for e in entries or ():
        total += 1
        r = getattr(e, "r_multiple", None)
        if r is not None:
            try:
                rs.append(float(r))
            except (TypeError, ValueError):
                pass
    return {
        "avg": (sum(rs) / len(rs)) if rs else None,
        "scored": len(rs),
        "total": total,
    }


#: How many entries survive a restart: ``_save`` writes the newest this many.
#: A reader printing a record over the journal says so once it is this full.
KEEPS = 500


class TradeJournal:
    """Manages trade journal entries with persistence."""

    def __init__(self, journal_file: str = "data/trade_journal.json") -> None:
        self._entries: list[JournalEntry] = []
        self._journal_file = str(state_path(journal_file))
        self._max_entries = 1000
        #: True when the file was there and could not be read. An absent file
        #: is a fresh journal; a file that raised left the list empty or
        #: partial, and a reader must not print that as "no trades".
        self.read_failed = False
        self._load()

    def closed_entries(self) -> list[JournalEntry]:
        """A copy of the entries, oldest first, for a reader that aggregates."""
        return list(self._entries)

    def record_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        strategy_type: str,
        entry_price: float,
        exit_price: float,
        stop_loss: float,
        take_profit: float,
        pnl: float,
        confidence: float = 0.0,
        signals_used: Optional[list] = None,
        regime: str = "",
        session: str = "",
        volatility: str = "",
        holding_hours: float = 0.0,
        exit_reason: str = "",
        venue: str = "bitget",
        user_id: str = "",
        quantity: Optional[float] = None,
    ) -> JournalEntry:
        """Record a completed trade in the journal."""
        # R, or None when the risk it is a ratio of could not be read —
        # the stop OR the size; both are the denominator.
        r_multiple = r_multiple_for(entry_price, stop_loss, pnl, quantity)

        # Calculate PnL %
        pnl_pct = (pnl / (entry_price * 1)) * 100 if entry_price > 0 else 0  # simplified

        # Auto-generate lessons
        lessons = self._generate_lessons(
            pnl=pnl, r_multiple=r_multiple, exit_reason=exit_reason,
            confidence=confidence, holding_hours=holding_hours,
            direction=direction, regime=regime,
        )

        # Auto-tag
        tags = self._generate_tags(pnl, r_multiple, exit_reason, holding_hours)

        entry = JournalEntry(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            strategy_type=strategy_type,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            r_multiple=None if r_multiple is None else round(r_multiple, 2),
            holding_hours=round(holding_hours, 2),
            regime=regime,
            session=session,
            volatility=volatility,
            confidence=confidence,
            signals_used=signals_used or [],
            exit_reason=exit_reason,
            venue=venue,
            lessons=lessons,
            tags=tags,
            timestamp=time.time(),
            user_id=str(user_id or ""),
            quantity=quantity,
        )

        self._entries.append(entry)

        # Cap entries
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        self._save()
        return entry
    def find_trade(self, trade_id: str, *, user_id: Optional[str] = None):
        """The entry recorded for ``trade_id``, or None.

        By id, newest first, and by OWNER where the entry recorded one: the
        caller must already own the id — it comes off the caller's own book
        (`engine.viewer_executor` / their paper portfolio) — before it is
        looked up here, and nothing searches this store by symbol or by
        anything a user types. Ids are not unique across accounts (an
        adopted position's id is symbol + a one-second timestamp), so an
        entry carrying another account's ``user_id`` is never returned; an
        entry that recorded no owner (every row written before the column
        existed) is returned and left to the caller's own consistency check.
        """
        tid = str(trade_id or "")
        if not tid:
            return None
        mine = str(user_id or "")
        for e in reversed(self._entries):
            if e.trade_id != tid:
                continue
            owner = str(getattr(e, "user_id", "") or "")
            if owner and owner != mine:
                continue
            return e
        return None

    def get_weekly_review(self, lookback_days: int = 7) -> dict:
        """Generate a weekly performance review summary."""
        cutoff = time.time() - (lookback_days * 86400)
        recent = [e for e in self._entries if e.timestamp >= cutoff]

        if not recent:
            return {"period": f"Last {lookback_days} days", "trades": 0, "summary": "No trades in period"}

        # THE ONE READING every other record surface in this tree asks.
        # This was the outlier: `wins = [e for e in recent if e.pnl > 0]` and
        # `losses = [... < 0]` beside `len(recent)`, so the card printed
        # `Trades: 5 (2W / 1L)` and left two rows with no word at all — a
        # MEASURED BREAK-EVEN is neither, and the reader's repair is
        # `5 - 2 = 3 losses`, which is the shape `win_stats`'s own header is
        # about. It carries `losses` and `flat` now and the four counts close.
        _ws = win_stats(recent)
        _ps = pnl_stats(recent)
        total_pnl = _ps["total"]

        # Best and worst over the rows that could be PRICED. `max(recent,
        # key=...)` over a P&L that is NaN keeps whichever it happened to
        # meet first -- every comparison against NaN is False -- so the card
        # printed `Best: BTC/USDT $+nan` under a trophy, and WHICH row won
        # was decided by list order rather than by any measurement. Found by
        # rendering the card for a window nothing could price, which is a row
        # `_load` can produce: `json` parses a bare `NaN` token by default.
        _scored = [e for e in recent if trade_pnl(e) is not None]
        best = max(_scored, key=lambda e: e.pnl) if _scored else None
        worst = min(_scored, key=lambda e: e.pnl) if _scored else None

        def _group(key_of) -> dict:
            """Per-group record, classified by the SAME reader as the total.

            Each bucket closes the way `win_stats` does, and `pnl` is the sum
            over the rows that could be priced with `scored` beside it — a
            group total over a set holding an unreadable row, printed as a
            whole, is the partial-total shape from CLAUDE.md's table.
            """
            out: dict = defaultdict(
                lambda: {"trades": 0, "pnl": 0.0, "wins": 0,
                         "losses": 0, "flat": 0, "scored": 0, "unscored": 0})
            for e in recent:
                g = out[key_of(e) or "unknown"]
                g["trades"] += 1
                p = trade_pnl(e)
                if p is None:
                    g["unscored"] += 1
                    continue
                g["scored"] += 1
                g["pnl"] += p
                if p > 0:
                    g["wins"] += 1
                elif p < 0:
                    g["losses"] += 1
                else:
                    g["flat"] += 1
            return dict(out)

        regime_stats = _group(lambda e: e.regime)
        strat_stats = _group(lambda e: e.strategy_type)

        # Common lessons
        all_lessons = []
        for e in recent:
            all_lessons.extend(e.lessons)
        lesson_counts = defaultdict(int)
        for lesson in all_lessons:
            lesson_counts[lesson] += 1
        top_lessons = sorted(lesson_counts.items(), key=lambda x: -x[1])[:5]

        # Average holding time
        avg_hold = sum(e.holding_hours for e in recent) / len(recent)

        # Average R over the entries that HAVE one. `sum(e.r_multiple ...)` /
        # len(recent) counted an unpriceable R as 0R in both halves of the
        # fraction, which is the partial-total shape from CLAUDE.md's table
        # printed as a whole.
        _r = average_r(recent)

        return {
            "period": f"Last {lookback_days} days",
            "trades": len(recent),
            "wins": _ws["wins"],
            "losses": _ws["losses"],
            # A close the record priced at exactly 0.00. It is not a win and
            # it is not a loss, and until it had a name the card's `W / L`
            # simply did not add up to the `Trades` above it.
            "flat": _ws["flat"],
            "scored": _ws["scored"],
            "unscored": _ws["unscored"],
            # The rate is over what could be SCORED, and it is None rather
            # than 0.0 when nothing could be — "0% of this window won" and
            # "nothing in this window could be priced" are different claims.
            "win_rate": (None if _ws["rate"] is None
                         else round(_ws["rate"] * 100, 1)),
            # None when nothing could be priced, for the same reason: a total
            # over zero measurements is not a measurement.
            "total_pnl": (None if total_pnl is None else round(total_pnl, 2)),
            "pnl_scored": _ps["scored"],
            "pnl_unscored": _ps["unscored"],
            # None, not 0.0, when nothing in the window could be priced in R.
            # The two counts travel with it so a reader can tell "0.42R over
            # 20" from "0.42R over the 6 of 20 that had a stop on record".
            "avg_r_multiple": None if _r["avg"] is None else round(_r["avg"], 2),
            "r_scored": _r["scored"],
            "r_unscored": _r["total"] - _r["scored"],
            "avg_holding_hours": round(avg_hold, 1),
            # None -- not a row with a junk figure -- when nothing in the
            # window could be priced. There is no best trade among closes
            # nobody could score.
            "best_trade": (None if best is None else
                           {"symbol": best.symbol, "pnl": best.pnl,
                            "r": best.r_multiple,
                            "r_reason": r_unknown_reason(best)}),
            "worst_trade": (None if worst is None else
                            {"symbol": worst.symbol, "pnl": worst.pnl,
                             "r": worst.r_multiple,
                             "r_reason": r_unknown_reason(worst)}),
            "by_regime": dict(regime_stats),
            "by_strategy": dict(strat_stats),
            "top_lessons": top_lessons,
        }
    def _generate_lessons(self, **kwargs) -> list[str]:
        """Auto-generate lessons from trade outcome patterns."""
        lessons = []
        pnl = kwargs.get("pnl", 0)
        # `is None` rather than a default: every rule below is a THRESHOLD on
        # R, and a close with no R clears none of them. Defaulting to 0 made
        # "the stop was too tight" and "nobody recorded a stop" produce the
        # same (empty) set of lessons for opposite reasons.
        #
        # Tested inline at each site rather than hoisted to an `r_known` flag:
        # a separate boolean does not NARROW the Optional for the analyser, so
        # the hoisted version cost three `operator` errors on the type gate
        # while reading identically.
        r_mult = kwargs.get("r_multiple")
        exit_reason = kwargs.get("exit_reason", "")
        confidence = kwargs.get("confidence", 0)
        holding = kwargs.get("holding_hours", 0)
        direction = kwargs.get("direction", "")
        regime = kwargs.get("regime", "")

        # Exit analysis
        if r_mult is not None and exit_reason == "sl_hit" and r_mult < -0.8:
            lessons.append("Full stop hit — consider if SL was too tight")
        if r_mult is not None and exit_reason == "tp_hit" and r_mult > 2.5:
            lessons.append("TP hit at good R — setup quality was high")
        if r_mult is not None and exit_reason == "trailing" and r_mult > 1.0:
            lessons.append("Trailing stop locked profit — good trade management")

        # Confidence analysis
        if pnl < 0 and confidence < 0.60:
            lessons.append("Low confidence trade lost — stick to high-conf setups")
        if pnl > 0 and confidence >= 0.80:
            lessons.append("High confidence = high win rate confirmed")

        # Holding time
        if holding < 0.5 and abs(pnl) > 0:
            lessons.append("Very short hold — possible overreaction or noise stop")
        if holding > 48 and pnl < 0:
            lessons.append("Long holding loser — consider time-based exits")

        # Regime alignment
        if regime in ("RANGE", "CHOP") and direction == "LONG" and pnl < 0:
            lessons.append("Long in choppy market lost — reduce directional bias in ranges")
        if "TREND" in regime and pnl > 0:
            lessons.append("Profitable trend trade — regime alignment works")

        return lessons
    def _generate_tags(self, pnl: float, r_mult: Optional[float],
                       exit_reason: str, holding: float) -> list[str]:
        """Auto-tag the trade for filtering."""
        tags = []
        # `r_mult is None` is a close with no stop on record. The P&L tags
        # still apply — a win is a win — but "runner" and "full_stop" are
        # claims about the SIZE of the move in risk units, and there are no
        # risk units here. An unknown R used to arrive as 0.0 and quietly
        # failed every one of these thresholds, so the tags were absent for a
        # reason no reader could see.
        if pnl > 0:
            tags.append("winner")
            if r_mult is not None and r_mult >= 3.0:
                tags.append("runner")
            elif r_mult is not None and r_mult >= 2.0:
                tags.append("solid_win")
        elif pnl < 0:
            tags.append("loser")
            if r_mult is not None and r_mult <= -1.0:
                tags.append("full_stop")
        else:
            tags.append("breakeven")

        if exit_reason == "trailing":
            tags.append("trailed")
        if exit_reason == "partial_tp":
            tags.append("partial")
        if holding < 1:
            tags.append("quick")
        if holding > 24:
            tags.append("swing")

        return tags
    def _save(self) -> None:
        """Persist journal to disk."""
        try:
            os.makedirs(os.path.dirname(self._journal_file) or ".", exist_ok=True)
            data = []
            for e in self._entries[-KEEPS:]:
                data.append({
                    "trade_id": e.trade_id, "symbol": e.symbol,
                    "direction": e.direction, "strategy_type": e.strategy_type,
                    "entry": e.entry_price, "exit": e.exit_price,
                    "sl": e.stop_loss, "tp": e.take_profit,
                    "pnl": e.pnl, "pnl_pct": e.pnl_pct,
                    "r_mult": e.r_multiple, "hold_hrs": e.holding_hours,
                    "regime": e.regime, "session": e.session,
                    "vol": e.volatility, "conf": e.confidence,
                    "signals": e.signals_used, "exit_reason": e.exit_reason,
                    "lessons": e.lessons, "tags": e.tags, "ts": e.timestamp,
                    "venue": e.venue, "uid": e.user_id,
                    "qty": e.quantity,
                })
            with open(self._journal_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.debug("Journal save failed: %s", exc)
    def _load(self) -> None:
        """Load journal from disk."""
        try:
            if not os.path.exists(self._journal_file):
                return
            with open(self._journal_file) as f:
                data = json.load(f)
            for d in data:
                self._entries.append(JournalEntry(
                    trade_id=d["trade_id"], symbol=d["symbol"],
                    direction=d["direction"], strategy_type=d.get("strategy_type", "swing"),
                    entry_price=d["entry"], exit_price=d["exit"],
                    stop_loss=d["sl"], take_profit=d["tp"],
                    pnl=d["pnl"], pnl_pct=d.get("pnl_pct", 0),
                    # `.get("r_mult")` with NO default. `_save` writes JSON
                    # null for a close that had no stop on record, and an
                    # entry written before this field became Optional simply
                    # lacks the key — both are "no R", and defaulting them to
                    # 0 puts a measured break-even into the average on the way
                    # back off disk. The same shape the writer just lost.
                    # An entry recorded before sizes were journaled carries an
                    # R computed over the PRICE distance (and negated for a
                    # short): known-wrong, not unknown-but-plausible. It loads
                    # as None, and the review counts it as unscored.
                    r_multiple=(d.get("r_mult") if d.get("qty") is not None else None),
                    holding_hours=d.get("hold_hrs", 0),
                    regime=d.get("regime", ""), session=d.get("session", ""),
                    volatility=d.get("vol", ""), confidence=d.get("conf", 0),
                    signals_used=d.get("signals", []), exit_reason=d.get("exit_reason", ""),
                    lessons=d.get("lessons", []), tags=d.get("tags", []),
                    timestamp=d.get("ts", 0),
                    # Absent on every entry written before venues existed, and
                    # those really are Bitget — a back-fill of a fact.
                    venue=d.get("venue", "bitget"),
                    user_id=str(d.get("uid", "") or ""),
                    quantity=d.get("qty"),
                ))
            logger.info("Loaded %d journal entries", len(self._entries))
        except Exception as exc:
            self.read_failed = True
            logger.warning("Journal load failed: %s", exc)
