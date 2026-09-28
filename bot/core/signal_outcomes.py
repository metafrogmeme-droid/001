"""What became of each signal the bot published.

Both producers of the public signal stream (the engine's own ideas and the
scan cards) push a row with ``status: NEW`` and no outcome, and nothing ever
pushed a second one. So every signal stayed NEW for good, ``/api/signals/stats``
could never count a resolved signal, and the panel promising that "outcomes
appear once signals hit target or stop" described a path that did not exist.

This module is that path. Every published row is recorded here, and the engine
walks hourly candles from the moment each signal was published and says what
it did. A signal is a CALL, not a position: nobody's money is in it, so what it
realized is stated in R, the multiple of its own risk distance, and never in
dollars (the stream is public).

The words, and what each one claims:

* ``NEW`` -- published; price has not reached the entry yet.
* ``OPEN`` -- price reached the entry; neither level yet.
* ``TARGET`` -- the target was reached after the entry: R = reward / risk.
* ``STOP`` -- the stop was reached after the entry: R = -1.
* ``AMBIGUOUS`` -- one bar spans both levels (or the entry and the level on the
  far side of it), and OHLC cannot say which came first, so it carries no R in
  either direction. It is counted, never folded into a win or a loss.
* ``EXPIRED`` -- the entry window closed before price reached the entry. The
  call was never taken, which is not a loss.
* ``NO_EXIT`` -- the entry was reached and neither level within ``HORIZON_S``.
* ``UNSCORED`` -- the candles could not answer: they do not reach back to the
  signal, or its levels do not describe a trade. Not a verdict.

THE ENTRY WINDOW IS THE BOT'S RESTING-LIMIT CLOCK, not the row's
``expires_at``. That field is the PENDING_IDEA_TTL (five minutes by default):
how long a follower may still act on the call, which is what the copy readers
select on. It is finer than an hourly candle, so a walk that honoured it would
call nearly every signal EXPIRED before its first bar closed. What the call
itself did is asked over the time the bot would rest a limit order at its entry
(``LIMIT_ORDER_EXPIRE_SEC``, four hours by default), which the engine hands in.

ONE CALL PER MARKET AND DIRECTION, PER PRODUCER, WHILE IT IS PENDING. The
engine's pending idea lives ``PENDING_IDEA_TTL`` (five minutes); when it lapses
untaken, the next scan reads the same closed hourly candles and the same cached
thesis and emits the same setup under a new id. Each of those used to be a new
call: a new ledger row scored on its own, a new website row, a new copy push.
One market move was counted as many calls as the setup survived five-minute
windows. A row whose producer already has a PENDING call on the same market in
the same direction is a RE-OFFER of that call: it is not recorded and not sent,
and ``publish_signals`` says which call it re-offers. The opposite direction is
a new call, and so is the same direction once the earlier call has resolved.
The re-offer's own levels are not the call's: the call is scored on what it
said when it was made, which is what a call is.

The R is GROSS: a signal has no size, so no fee can be charged to it, and the
panel says so. And the walk is over HOURLY bars, so a bar that spans both
levels is more common than it would be at a finer grain; that is the price of
reading a week in one fetch, and it is why AMBIGUOUS is its own word.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, NamedTuple, Optional, Sequence

from bot.utils.json_store import StoreUnreadable, read_json_store, update_json_store
from bot.utils.paths import state_path

logger = logging.getLogger(__name__)

NEW, OPEN = "NEW", "OPEN"
TARGET, STOP, AMBIGUOUS = "TARGET", "STOP", "AMBIGUOUS"
EXPIRED, NO_EXIT, UNSCORED = "EXPIRED", "NO_EXIT", "UNSCORED"

#: Still being walked: the bot asks the candles about these again.
PENDING = (NEW, OPEN)
#: Final. A row in one of these is not asked about again.
TERMINAL = (TARGET, STOP, AMBIGUOUS, EXPIRED, NO_EXIT, UNSCORED)
#: The words that carry an R.
SCORED = (TARGET, STOP)

TIMEFRAME = "1h"
BAR_MS = 3_600_000
#: How long a signal may sit in the market with neither level reached before
#: it is NO_EXIT. A week: longer than any strategy's own time exit.
HORIZON_S = 7 * 24 * 3600
#: The window to reach the entry when the caller hands none: the default of
#: LIMIT_ORDER_EXPIRE_SEC, the time the bot rests a limit order at its entry.
DEFAULT_ENTRY_WINDOW_S = 4 * 3600
#: Candles asked for per signal: 200 hourly bars is eight days and a bit, which
#: covers the entry window plus the horizon for a signal read on time.
FETCH_LIMIT = 200
#: How long past the end of a window (the entry window, or the week after the
#: entry) the walk waits for the candles to reach it before it gives up. A walk
#: reads CLOSED bars only -- `_cached_ohlcv` drops the forming one at the fetch
#: and caches for up to ten minutes -- so at any moment the bars end up to about
#: an hour before now. A verdict at the end of a window taken off the clock
#: alone was taken over an hour nobody read: a call that reached its entry in
#: the window's last hour was recorded EXPIRED, which is final. Three days,
#: because a market closed for a weekend produces no bar until it reopens, and
#: the bar it reopens with answers the question.
TAIL_GRACE_S = 3 * 24 * 3600
#: A resolved row is kept this long after it was synced, then pruned.
KEEP_RESOLVED_S = 14 * 24 * 3600
#: The ledger never holds more than this many rows (oldest resolved go first).
MAX_ROWS = 2000

def ledger_path():
    return state_path("data/learning/signal_outcomes.json")


def _f(value: object) -> Optional[float]:
    """A finite float, or None. A bool is not a number here."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def parse_time_ms(value: object) -> Optional[int]:
    """Epoch milliseconds from an ISO stamp or the scan's ``%Y-%m-%d %H:%M UTC``.

    None for anything else: a signal whose publication time cannot be read
    cannot be walked from it.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    for fmt in ("%Y-%m-%d %H:%M UTC",):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp() * 1000)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def market_for(symbol: object) -> Optional[str]:
    """The market to ask for candles, from the symbol a row was published under.

    The engine publishes ``BTC/USDT``; the scan cards publish the base alone
    (``BTC``). A bare ``BTCUSDT`` is read as the USDT pair too.
    """
    s = str(symbol or "").strip().upper()
    if not s:
        return None
    if "/" in s:
        return s
    if s.endswith("USDT") and len(s) > 4:
        return f"{s[:-4]}/USDT"
    if s.isalnum():
        return f"{s}/USDT"
    return None


@dataclass(frozen=True)
class Resolution:
    """What one signal did, as far as the bars say."""

    status: str
    why: str
    r: Optional[float] = None
    resolved_ms: Optional[int] = None
    triggered_ms: Optional[int] = None
    #: The bars could not answer THIS time (a candle that did not read, none
    #: closed yet). Nothing about the signal is recorded from such a walk.
    retry: bool = False


def _levels(row: dict):
    """(long, entry, stop, target) or None when they do not describe a trade."""
    d = str(row.get("direction", "")).split(".")[-1].upper()
    if d not in ("LONG", "SHORT"):
        return None
    e, s, t = (_f(row.get(k)) for k in ("entry_price", "stop_loss", "take_profit"))
    if e is None or s is None or t is None or e <= 0 or s <= 0 or t <= 0:
        return None
    long = d == "LONG"
    if long and not (s < e < t):
        return None
    if not long and not (t < e < s):
        return None
    return long, e, s, t


def entry_window_s(value: object) -> int:
    """The entry window in seconds: ``value`` when it is a positive number,
    otherwise the default. A setting that does not read is not a window of 0."""
    v = _f(value)
    return int(v) if v is not None and v >= 1 else DEFAULT_ENTRY_WINDOW_S


def resolve(row: dict, bars: Sequence[Sequence[Any]], now_ms: int,
            window_s: object = None) -> Resolution:
    """Walk ``bars`` (closed OHLCV rows, oldest first) and say what ``row`` did.

    Only bars that OPENED at or after the signal's publication are read: the
    bar that was forming when the signal went out holds prices from before it,
    and one of those reaching the entry is not the market answering the call.

    HOW THE ENTRY IS REACHED depends on where it sits. An entry on the far side
    of the market from the first bar's open (below it for a long) is a
    pullback, reached when a bar trades down to it; one on the near side is a
    break, reached when a bar trades up to it. A single rule for both reads a
    gap past a pullback entry as never filled, or a gap past a break as filled.

    ON THE BAR THAT REACHES THE ENTRY, a level on the far side of the entry
    from where price came from (the stop, for a pullback long) is reached after
    it, and counts. A level on the side price came FROM may have been reached
    before the entry was, so that bar is AMBIGUOUS rather than a win.
    """
    lv = _levels(row)
    if lv is None:
        return Resolution(UNSCORED, "the levels do not describe a trade")
    long, e, s, t = lv
    created = parse_time_ms(row.get("created_at"))
    if created is None:
        return Resolution(UNSCORED, "the publication time could not be read")
    expires = created + entry_window_s(window_s) * 1000

    walk = []
    for b in bars:
        if not b or len(b) < 5:
            return Resolution(NEW, "a candle could not be read; asked again later", retry=True)
        ts = _f(b[0])
        if ts is None:
            return Resolution(NEW, "a candle could not be read; asked again later", retry=True)
        if ts >= created:
            walk.append(b)
    if not walk:
        if now_ms >= expires + TAIL_GRACE_S * 1000:
            # Nothing after publication came back at all, long past the window.
            return Resolution(UNSCORED, "no candle after the signal was published came back")
        return Resolution(NEW, "no candle has closed since the signal was published", retry=True)
    first_ts = _f(walk[0][0])
    if first_ts is not None and first_ts > created + BAR_MS:
        # The series starts later than the signal: bars in between are missing,
        # and the entry may have been reached in them.
        return Resolution(UNSCORED, "the candles do not reach back to the signal")

    first_open = _f(walk[0][1])
    if first_open is None:
        return Resolution(NEW, "a candle could not be read; asked again later", retry=True)
    # Pullback: the entry sits on the far side of the market (below for a long).
    pullback = e <= first_open if long else e >= first_open
    reward = (t - e) if long else (e - t)
    risk = (e - s) if long else (s - e)
    r_target = round(reward / risk, 2)

    trig_ts: Optional[int] = None
    for b in walk:
        ts = int(float(b[0]))
        hi, lo = _f(b[2]), _f(b[3])
        if hi is None or lo is None:
            return Resolution(NEW if trig_ts is None else OPEN,
                              "a candle could not be read; asked again later",
                              triggered_ms=trig_ts, retry=True)
        hit_stop = (lo <= s) if long else (hi >= s)
        hit_target = (hi >= t) if long else (lo <= t)
        if trig_ts is None:
            if ts >= expires:
                return Resolution(EXPIRED, "the window closed before price reached the entry",
                                  resolved_ms=expires)
            reached = ((lo <= e) if long else (hi >= e)) if pullback else \
                ((hi >= e) if long else (lo <= e))
            if not reached:
                continue
            trig_ts = ts
            # The level beyond the entry (in the direction price was moving)
            # is reached after it; the level behind it may have come first.
            ahead_hit = hit_stop if pullback else hit_target
            behind_hit = hit_target if pullback else hit_stop
            if behind_hit:
                return Resolution(AMBIGUOUS, "the bar that reached the entry also spans the "
                                  "other level; OHLC cannot say which came first",
                                  resolved_ms=ts + BAR_MS, triggered_ms=ts)
            if ahead_hit:
                status = STOP if pullback else TARGET
                return Resolution(status, "reached on the bar that reached the entry",
                                  r=-1.0 if status == STOP else r_target,
                                  resolved_ms=ts + BAR_MS, triggered_ms=ts)
            continue
        if ts - trig_ts >= HORIZON_S * 1000:
            return Resolution(NO_EXIT, "neither level was reached within a week of the entry",
                              resolved_ms=trig_ts + HORIZON_S * 1000, triggered_ms=trig_ts)
        if hit_stop and hit_target:
            return Resolution(AMBIGUOUS, "one bar spans the stop and the target; OHLC "
                              "cannot say which came first",
                              resolved_ms=ts + BAR_MS, triggered_ms=trig_ts)
        if hit_target:
            return Resolution(TARGET, "the target was reached", r=r_target,
                              resolved_ms=ts + BAR_MS, triggered_ms=trig_ts)
        if hit_stop:
            return Resolution(STOP, "the stop was reached", r=-1.0,
                              resolved_ms=ts + BAR_MS, triggered_ms=trig_ts)

    # A verdict at the END of a window needs the bars to reach that end. The
    # clock passing it is not enough: the last hour of it may be the forming
    # candle, which no walk reads (TAIL_GRACE_S).
    covered = int(float(walk[-1][0])) + BAR_MS
    if trig_ts is None:
        if now_ms < expires:
            return Resolution(NEW, "price has not reached the entry")
        if covered >= expires:
            return Resolution(EXPIRED, "the window closed before price reached the entry",
                              resolved_ms=expires)
        if now_ms >= expires + TAIL_GRACE_S * 1000:
            return Resolution(UNSCORED, "the candles stop before the entry window closed, "
                              "so its end was never read")
        return Resolution(NEW, "the entry window has closed and its last hour has not "
                          "been read yet")
    week_end = trig_ts + HORIZON_S * 1000
    if now_ms < week_end:
        return Resolution(OPEN, "the entry was reached; neither level yet", triggered_ms=trig_ts)
    if covered >= week_end:
        return Resolution(NO_EXIT, "neither level was reached within a week of the entry",
                          resolved_ms=week_end, triggered_ms=trig_ts)
    if now_ms >= week_end + TAIL_GRACE_S * 1000:
        return Resolution(UNSCORED, "the candles stop before the week after the entry "
                          "ended, so its end was never read", triggered_ms=trig_ts)
    return Resolution(OPEN, "the week after the entry has ended and its last hour has not "
                      "been read yet", triggered_ms=trig_ts)


# ---------------------------------------------------------------- the ledger

def _check(data: Any) -> str:
    if not isinstance(data.get("signals", {}), dict):
        return "signals is not a map"
    return ""


#: The engine's own ideas: the one producer that re-emits the same setup on
#: its own clock, so the one whose rows are held to one live call per market
#: and direction. The scan cards carry no producer and are recorded as sent,
#: because a scan card's verify link points at the key it was published under.
ENGINE = "engine"


def call_market(row: dict) -> Optional[tuple[str, str]]:
    """(market, direction) a row makes a call on, or None when it names neither.

    The market is read the way every other reader here reads one (``BTC``,
    ``BTC/USDT`` and ``BTC/USDT:USDT`` are one market), so two spellings of it
    are never two calls.
    """
    from bot.core.symbol_form import normalize_symbol

    m = market_for(row.get("symbol"))
    d = str(row.get("direction", "")).split(".")[-1].upper()
    if m is None or d not in ("LONG", "SHORT"):
        return None
    return normalize_symbol(m), d


class Recorded(NamedTuple):
    """What ``record_published`` did: how many rows became calls, and
    ``{row key: the key of the call it re-offers}`` for the rows that did not."""

    added: int
    reoffers: dict


def record_published(rows: Sequence[dict], path=None, *,
                     producer: str = "") -> Optional[Recorded]:
    """Record every published row that is a new call, so the engine can walk it.

    The row is kept WHOLE: a resolution is re-sent as the same row with its
    outcome fields set, because the website INSERTs a row it has never seen and
    seals it from the decision facts, so a row re-sent with only its key and
    outcome would be sealed with zeros. A key already recorded is left alone.

    With a ``producer``, a row on a market and direction that producer already
    has a PENDING call on (in the ledger, or earlier in this batch) is a
    re-offer of that call and is not recorded. None when the ledger could not
    be read or written: then nothing is known about which rows re-offer what,
    and nothing was recorded.
    """
    p = path or ledger_path()
    fresh = [dict(r) for r in rows or ()
             if isinstance(r, dict) and r.get("signal_key") and r.get("symbol")
             and r.get("direction")]
    if not fresh:
        return Recorded(0, {})
    reoffers: dict[str, str] = {}
    added = 0

    def change(data):
        nonlocal added
        sigs = data.setdefault("signals", {})
        live: dict[tuple[str, str], str] = {}
        if producer:
            for k, v in sigs.items():
                if (isinstance(v, dict) and v.get("producer") == producer
                        and v.get("status") in PENDING and isinstance(v.get("row"), dict)):
                    cm = call_market(v["row"])
                    if cm is not None:
                        live.setdefault(cm, k)
        for r in fresh:
            k = str(r["signal_key"])
            if k in sigs:
                continue
            cm = call_market(r) if producer else None
            call = live.get(cm) if cm is not None else None
            if call is not None:
                reoffers[k] = call
                continue
            entry = {"row": r, "status": str(r.get("status") or NEW),
                     "r": None, "resolved_ms": None, "why": "",
                     "checked_ms": None, "synced": True}
            if producer:
                entry["producer"] = producer
            sigs[k] = entry
            if cm is not None:
                live[cm] = k
            added += 1
        _prune(sigs)
        return added > 0

    try:
        update_json_store(p, change, check=_check, separators=(",", ":"))
    except StoreUnreadable as exc:
        logger.error("signal outcome ledger unreadable (%s); %d published signal(s) "
                     "not recorded, and the file is left as it is", exc.detail, len(fresh))
        return None
    except OSError as exc:
        logger.warning("signal outcome ledger could not be written (%s)", type(exc).__name__)
        return None
    return Recorded(added, dict(reoffers))


def _prune(sigs: dict, now_ms: Optional[int] = None) -> None:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    for k in [k for k, v in sigs.items()
              if v.get("status") in TERMINAL and v.get("synced")
              and (v.get("resolved_ms") or now) < now - KEEP_RESOLVED_S * 1000]:
        del sigs[k]
    if len(sigs) > MAX_ROWS:
        order = sorted(sigs, key=lambda k: (sigs[k].get("status") not in TERMINAL,
                                            sigs[k].get("resolved_ms") or 0))
        for k in order[:len(sigs) - MAX_ROWS]:
            del sigs[k]


def rows_due(path=None, *, limit: int = 12) -> Optional[list[tuple[str, dict]]]:
    """The rows to walk now: pending ones, then resolved ones not yet synced.

    Oldest check first, so a burst of new signals cannot starve an old one.
    None when the ledger could not be read (never an empty list).
    """
    got = read_json_store(path or ledger_path(), check=_check)
    if got.state == "unreadable":
        return None
    sigs = (got.data or {}).get("signals", {})
    items = [(k, v) for k, v in sigs.items()
             if isinstance(v, dict) and (v.get("status") in PENDING or not v.get("synced"))]
    items.sort(key=lambda kv: kv[1].get("checked_ms") or 0)
    return items[:limit]


def outcome_row(entry: dict) -> dict:
    """The published row with its outcome fields set, ready to re-send."""
    row = dict(entry.get("row") or {})
    row["status"] = entry.get("status") or NEW
    row["pnl"] = entry.get("r") if entry.get("status") in SCORED else None
    ms = entry.get("resolved_ms")
    row["resolved_at"] = (datetime.fromtimestamp(ms / 1000, UTC).isoformat()
                          if isinstance(ms, (int, float)) and entry.get("status") in TERMINAL
                          else "")
    return row


def apply(key: str, res: Resolution, now_ms: int, path=None) -> Optional[dict]:
    """Record a walk's answer. Returns the ledger entry when it CHANGED the
    signal's word (so it must be re-sent), None otherwise."""
    changed: dict = {}

    def change(data):
        sigs = data.setdefault("signals", {})
        v = sigs.get(key)
        if not isinstance(v, dict):
            return False
        v["checked_ms"] = now_ms
        if v.get("status") in TERMINAL or res.retry:
            return True
        # A signal that reached its entry does not un-reach it: a shorter or
        # later fetch that no longer holds the bar is not evidence against it.
        if v.get("status") == OPEN and res.status == NEW:
            return True
        if res.status != v.get("status"):
            v["status"] = res.status
            v["r"] = res.r
            v["resolved_ms"] = res.resolved_ms
            v["why"] = res.why
            v["synced"] = False
            changed.update(v)
        return True

    try:
        update_json_store(path or ledger_path(), change, check=_check, separators=(",", ":"))
    except (StoreUnreadable, OSError) as exc:
        logger.warning("signal outcome not recorded for %s (%s)", key, type(exc).__name__)
        return None
    return changed or None


def mark_synced(key: str, path=None) -> bool:
    def change(data):
        v = data.setdefault("signals", {}).get(key)
        if not isinstance(v, dict) or v.get("synced"):
            return False
        v["synced"] = True
        return True
    try:
        _, written = update_json_store(path or ledger_path(), change, check=_check, separators=(",", ":"))
    except (StoreUnreadable, OSError) as exc:
        logger.warning("signal outcome sync mark not recorded for %s (%s)",
                       key, type(exc).__name__)
        return False
    return bool(written)


#: The per-pair counts a summary carries, in the order a card prints them.
SUMMARY_COUNTS = ("calls", "target", "stop", "other", "open", "unknown")


def ledger_summary(path=None) -> Optional[dict]:
    """What the ledger holds, per pair, for the cards that report the record.

    None when the ledger could not be read, never an empty summary: "no signal
    on record" is a claim about the stream, and a failed read is not one.

    Each pair counts its calls by word, and the words close
    (``target + stop + other + open + unknown == calls``): ``other`` is a word
    that ends a call and carries no R (not filled, ambiguous, no exit,
    unscored), ``open`` is still pending, and ``unknown`` is a word this build
    does not know, counted rather than dropped so a total never reads as whole
    when it is not. The R is summed over TARGET and STOP rows that carry a
    finite one, with its own count, because a mean needs its denominator.

    A row that is not a record is ``skipped`` and counted; the span the record
    covers travels with it (the oldest publication time on record, the keep
    period and the row cap), because the ledger prunes and a card that did not
    say so would read as the whole history.
    """
    got = read_json_store(path or ledger_path(), check=_check)
    if got.state == "unreadable":
        return None
    sigs = (got.data or {}).get("signals", {}) or {}
    pairs: dict[str, dict] = {}
    oldest: Optional[int] = None
    skipped = 0
    for v in sigs.values():
        if not isinstance(v, dict):
            skipped += 1
            continue
        raw = v.get("row")
        row = raw if isinstance(raw, dict) else {}
        sym = str(row.get("symbol") or "").strip() or "?"
        p = pairs.setdefault(sym, {**{k: 0 for k in SUMMARY_COUNTS}, "r_sum": 0.0, "r_n": 0})
        p["calls"] += 1
        st = v.get("status")
        if st == TARGET:
            p["target"] += 1
        elif st == STOP:
            p["stop"] += 1
        elif st in TERMINAL:
            p["other"] += 1
        elif st in PENDING:
            p["open"] += 1
        else:
            p["unknown"] += 1
        if st in SCORED:
            r = _f(v.get("r"))
            if r is not None:
                p["r_sum"] += r
                p["r_n"] += 1
        ms = parse_time_ms(row.get("created_at"))
        if ms is not None and (oldest is None or ms < oldest):
            oldest = ms
    total: dict = {k: sum(p[k] for p in pairs.values()) for k in SUMMARY_COUNTS}
    total["r_sum"] = sum(p["r_sum"] for p in pairs.values())
    total["r_n"] = sum(p["r_n"] for p in pairs.values())
    return {"pairs": pairs, "total": total, "oldest_ms": oldest, "skipped": skipped,
            "keep_resolved_s": KEEP_RESOLVED_S, "max_rows": MAX_ROWS}


def publish_signals(rows: Sequence[dict], sync_fn: Optional[Callable[[list], None]] = None,
                    *, producer: str = "") -> dict[str, str]:
    """Record the rows, then push the ones that are calls. The one door both
    producers use.

    Returns ``{row key: the key of the call it re-offers}`` for every row that
    was a re-offer (see ``record_published``); those are not pushed. When the
    ledger could not say, every row is pushed as it always was and the answer
    is ``{}``: a re-offer counted twice is the recoverable mistake, and a call
    withheld on a guess is not.
    """
    rows = [r for r in rows or () if isinstance(r, dict)]
    if not rows:
        return {}
    got: Optional[Recorded] = None
    try:
        got = record_published(rows, producer=producer)
    except Exception as exc:  # noqa: BLE001 -- recording must never cost the push
        logger.warning("signal outcome ledger: record failed (%s)", type(exc).__name__)
    reoffers: dict[str, str] = got.reoffers if got is not None else {}
    calls = [r for r in rows if str(r.get("signal_key") or "") not in reoffers]
    if not calls:
        return reoffers
    send = sync_fn
    if send is None:
        from bot.utils.website_sync import sync_signals_in_background
        send = sync_signals_in_background
    send(calls)
    return reoffers
