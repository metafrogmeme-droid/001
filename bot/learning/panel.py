"""The /learn panel, from what each module actually records.

The panel used to give every module the decision-log length and the current
time, then turn that length into a score out of 10. A log of rejected ideas
read as a learning system at 100%. This module states each record it can
read, and it states a score only when a learner is READY and the closes have
a profit factor.

Nothing here writes a proposal, flips a flag, or changes an order.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from typing import Any, Optional

from bot.compat import UTC
from bot.utils.win_rate import profit_factor, win_stats

from .trade_outcomes import (
    ingest_closes,
    load_outcomes,
    outcome_stats,
    strategy_name,
)

logger = logging.getLogger("runeclaw.learning.panel")

#: Shown when a module has no outcomes of its own. Not a percentage.
INSUFFICIENT = "insufficient data"

_PATTERN = (
    "Groups of closed trades by the condition recorded on each row "
    "(regime, macro state, or low volatility). Not a candle-pattern model."
)
_REGIME = (
    "Win rate of closes that recorded a market regime. "
    "It does not change how a regime is detected."
)
_WEIGHTS = (
    "Voter-weight multipliers fitted from closed trades. "
    "Whether they change an order is a separate flag."
)
_FEEDBACK = (
    "Closed trades recorded for scoring. "
    "Human comments are counted on their own line and are not these closes."
)
_VOLATILITY = "Closes whose row recorded a volatility state."
_CORRELATION = (
    "Prices kept in this process for cross-asset context. "
    "Not a learned model, and this panel does not apply it to an order."
)
_DRAWDOWN = (
    "No recovery model is fitted. "
    "The figure, when shown, is the shared paper book's max drawdown."
)
_TIMING = (
    "Hold-time and hour-of-day counts kept in this process. "
    "They are not saved across restarts and this panel does not apply them."
)


def evidence_score(
    *,
    scored_closes: int,
    losses: Optional[int],
    profit_factor_value: Optional[float],
    ready_lower_bound: Optional[float],
) -> dict:
    """A score out of 10, or none.

    A number is stated only when three things are true together: at least one
    close was scored, a learner is READY, and that learner's out-of-sample
    lower bound is a rate between 0 and 1. The score is that lower bound on a
    0–10 scale. A profit factor below 1 caps it at 4, because a validated
    weight that still loses money is not a high score.

    No scored close → ``insufficient data``. Closes without a READY learner,
    or a READY learner whose profit factor cannot be stated, → ``not
    validated``. Neither case carries a grade. ``0.0`` as a profit factor is
    a real result and is not used here as a stand-in for "could not compute":
    the caller passes None for that.
    """
    closes = scored_closes if isinstance(scored_closes, int) and scored_closes > 0 else 0
    if closes == 0:
        return {
            "grade": None,
            "label": INSUFFICIENT,
            "reason": "No scored close is on record, so no score is stated.",
        }
    bound = ready_lower_bound
    if not isinstance(bound, (int, float)) or isinstance(bound, bool):
        return {
            "grade": None,
            "label": "not validated",
            "reason": "No learner is READY, so a score out of 10 is not stated.",
        }
    if not 0.0 <= float(bound) <= 1.0:
        return {
            "grade": None,
            "label": "not validated",
            "reason": "The ready learner's lower bound is not a rate, so no score is stated.",
        }
    grade = round(float(bound) * 10.0, 1)
    if losses == 0 and profit_factor_value is None:
        return {
            "grade": grade,
            "label": "validated",
            "reason": (
                f"Lower bound {float(bound):.0%} on a ready learner. "
                "Profit factor is not computed because no scored close lost."
            ),
        }
    if not isinstance(profit_factor_value, (int, float)) or isinstance(profit_factor_value, bool):
        return {
            "grade": None,
            "label": "not validated",
            "reason": (
                "A learner is READY but the profit factor could not be stated "
                "as one number, so no score is stated."
            ),
        }
    pf = float(profit_factor_value)
    reason = f"Lower bound {float(bound):.0%} on a ready learner, profit factor {pf:.2f}."
    if pf < 1.0:
        grade = min(grade, 4.0)
        reason += " The score is capped at 4 because profit factor is below 1."
    return {"grade": grade, "label": "validated", "reason": reason}


def book_line(label: str, trades, *, unread: bool) -> str:
    """One book's closes, win rate and profit factor.

    ``unread`` is a failed read, which is not an empty book. An empty book
    says "no closed trades". A profit factor that cannot be computed (nothing
    scored, or nothing lost) is said, not printed as 0.
    """
    if unread:
        return f"{label}: not read"
    stats = win_stats(trades or ())
    if stats["total"] == 0:
        return f"{label}: no closed trades"
    rate = stats["rate"]
    rate_txt = "win rate not computed" if rate is None else f"win {rate:.0%}"
    pf = profit_factor(trades or ())
    pf_txt = "profit factor not computed" if pf is None else f"profit factor {pf:.2f}"
    extra = ""
    if stats["unscored"]:
        extra = f" ({stats['unscored']} unpriced, left out of the rate)"
    return f"{label}: {stats['total']} closes{extra}, {rate_txt}, {pf_txt}"


def book_stats(trades, *, unread: bool) -> dict:
    """The counts ``evidence_score`` needs from one book. Unread → all None."""
    if unread:
        return {"unread": True, "scored": None, "losses": None, "profit_factor": None}
    stats = win_stats(trades or ())
    return {
        "unread": False,
        "scored": stats["scored"],
        "losses": stats["losses"],
        "profit_factor": profit_factor(trades or ()),
    }


def fmt_ts(ts: Any) -> str:
    """A real timestamp, ``never`` when there is none, ``not read`` when it will not parse."""
    if ts is None or ts == "":
        return "never"
    try:
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            dt = datetime.fromtimestamp(ts, tz=UTC)
        elif isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%d %b %H:%M")
    except (TypeError, ValueError, OSError):
        return "not read"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _module(name: str, measures: str, status: str, detail: str) -> dict:
    return {"name": name, "measures": measures, "status": status, "detail": detail}


def _count_detail(status: str, obs: Optional[int], noun: str, updated: str, extra: str = "") -> str:
    if obs is None:
        obs_txt = "not read"
    else:
        obs_txt = f"{obs} {noun}"
    line = f"{status} · {obs_txt} · updated {updated}"
    if extra:
        line = f"{line} · {extra}"
    return line


def _ready_bound(component: Optional[dict]) -> Optional[float]:
    """The voter-weight lower bound when that learner is READY, else None."""
    if not component or component.get("state") != "READY":
        return None
    bound = component.get("oos_hold_rate_lower")
    if isinstance(bound, bool) or not isinstance(bound, (int, float)):
        return None
    return float(bound)


def _grade_from(paper: dict, exchange: dict, ledger: dict, ready_bound: Optional[float]) -> dict:
    """Outcome stats for the grade: one readable book, else the ledger.

    Two books that both have scored closes are not averaged. Their profit
    factors are different quantities, so the grade is withheld.
    """
    readable = []
    for book in (paper, exchange):
        scored = book.get("scored")
        if book.get("unread") or not isinstance(scored, int) or scored <= 0:
            continue
        readable.append(book)
    if len(readable) == 1:
        src = readable[0]
        return evidence_score(
            scored_closes=src["scored"], losses=src.get("losses"),
            profit_factor_value=src.get("profit_factor"),
            ready_lower_bound=ready_bound)
    if len(readable) > 1:
        return evidence_score(
            scored_closes=sum(b["scored"] for b in readable), losses=None,
            profit_factor_value=None, ready_lower_bound=ready_bound)
    if ledger.get("unreadable"):
        return {
            "grade": None,
            "label": "not read",
            "reason": "The learning record could not be read, so no score is stated.",
        }
    scored = ledger.get("scored")
    if isinstance(scored, bool) or not isinstance(scored, int):
        scored = 0
    return evidence_score(
        scored_closes=scored,
        losses=ledger.get("losses"),
        profit_factor_value=ledger.get("profit_factor"),
        ready_lower_bound=ready_bound)


def _decision_rows(store) -> tuple[Optional[list], bool]:
    try:
        return store.get_decisions(limit=1000, strict=True), False
    except Exception as exc:  # noqa: BLE001 — a failed read is not an empty log
        logger.error("Decision log could not be read for the panel (%s)", type(exc).__name__)
        return None, True


def _pattern_module(store, rows: Optional[list], bad: bool) -> dict:
    if bad or rows is None:
        return _module("PatternMemory", _PATTERN, "not read",
                       _count_detail("not read", None, "scored closes", "not read"))
    try:
        from .patterns import PatternLearner
        # Bound to a name the reachability sweep can type. A call straight
        # off the constructor is an opaque receiver, and `outcome_groups`
        # is also a method on the orchestrator, so an untyped receiver
        # makes that shared name ambiguous.
        pattern_learner = PatternLearner(store)
        groups = pattern_learner.outcome_groups(rows)
    except Exception as exc:  # noqa: BLE001
        logger.error("Pattern groups could not be read (%s)", type(exc).__name__)
        return _module("PatternMemory", _PATTERN, "not read",
                       _count_detail("not read", None, "scored closes", "not read"))
    completed = [d for d in rows if d.pnl_result is not None]
    last = max((d.timestamp_utc for d in completed), default=None)
    closes = groups.closes
    status = INSUFFICIENT if closes == 0 else "recorded"
    extra = ""
    if closes:
        shaped = [{"pnl": d.pnl_result} for d in completed]
        rate = win_stats(shaped)["rate"]
        extra = "win rate not computed" if rate is None else f"win {rate:.0%}"
        if groups.groups:
            extra += f" · {len(groups.groups)} group(s)"
    return _module("PatternMemory", _PATTERN, status,
                   _count_detail(status, closes, "scored closes", fmt_ts(last), extra))


def _regime_module(rows: Optional[list], bad: bool, expectancy: Optional[dict]) -> dict:
    flag = ""
    if expectancy:
        state = expectancy.get("state") or "not read"
        applied = "on" if expectancy.get("applied") else "off"
        flag = f"setup expectancy {state}, flag {applied}"
    if bad or rows is None:
        return _module("RegimeAdaptation", _REGIME, "not read",
                       _count_detail("not read", None, "closes with a regime", "not read", flag))
    recorded = [d for d in rows if isinstance(d.market_regime, str) and d.market_regime.strip()
                and d.pnl_result is not None]
    last = max((d.timestamp_utc for d in recorded), default=None)
    n = len(recorded)
    status = INSUFFICIENT if n == 0 else "recorded"
    extra = flag
    if n:
        rate = win_stats([{"pnl": d.pnl_result} for d in recorded])["rate"]
        rate_txt = "win rate not computed" if rate is None else f"win {rate:.0%}"
        extra = f"{rate_txt} · {flag}" if flag else rate_txt
    return _module("RegimeAdaptation", _REGIME, status,
                   _count_detail(status, n, "closes with a regime", fmt_ts(last), extra))


def _weights_module(component: Optional[dict], fitted_at: Any) -> dict:
    if component is None:
        return _module("IndicatorWeightEvolution", _WEIGHTS, "not read",
                       _count_detail("not read", None, "samples", "not read"))
    samples = component.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int):
        samples = None
    state = component.get("state") or "not read"
    applied = component.get("applied")
    flag = "flag not read" if applied is None else ("flag on" if applied else "flag off")
    lower = component.get("oos_hold_rate_lower")
    extra = flag
    if isinstance(lower, (int, float)) and not isinstance(lower, bool):
        extra = f"95% lower bound {float(lower):.0%} · {flag}"
    if samples == 0:
        status = INSUFFICIENT
    elif samples is None:
        status = "not read"
    else:
        status = state
    return _module(
        "IndicatorWeightEvolution", _WEIGHTS, status,
        _count_detail(status, samples, "samples", fmt_ts(fitted_at), extra))


def _feedback_module(stats: dict) -> dict:
    if stats.get("unreadable"):
        return _module("FeedbackLoop", _FEEDBACK, "not read",
                       _count_detail("not read", None, "closes", "not read"))
    n = stats.get("closes")
    if not isinstance(n, int) or isinstance(n, bool):
        n = None
    status = INSUFFICIENT if n == 0 else ("not read" if n is None else "recorded")
    extra = ""
    if n:
        rate = stats.get("rate")
        pf = stats.get("profit_factor")
        rate_txt = "win rate not computed" if rate is None else f"win {rate:.0%}"
        pf_txt = "profit factor not computed" if pf is None else f"profit factor {pf:.2f}"
        extra = f"{rate_txt} · {pf_txt}"
    return _module("FeedbackLoop", _FEEDBACK, status,
                   _count_detail(status, n, "closes", fmt_ts(stats.get("last")), extra))


def _volatility_module(rows: Optional[list], bad: bool) -> dict:
    if bad or rows is None:
        return _module("VolatilityProfiler", _VOLATILITY, "not read",
                       _count_detail("not read", None, "rows with a volatility state", "not read"))
    recorded = [d for d in rows if isinstance(d.volatility_state, str) and d.volatility_state.strip()]
    last = max((d.timestamp_utc for d in recorded), default=None)
    n = len(recorded)
    status = INSUFFICIENT if n == 0 else "recorded"
    return _module("VolatilityProfiler", _VOLATILITY, status,
                   _count_detail(status, n, "rows with a volatility state", fmt_ts(last)))


def _correlation_module(engine) -> dict:
    tracker = getattr(engine, "cross_asset", None)
    history = getattr(tracker, "_price_history", None) if tracker is not None else None
    if not isinstance(history, dict):
        return _module("CorrelationTracker", _CORRELATION, "not read",
                       _count_detail("not read", None, "symbols", "not read"))
    last = None
    for rows in history.values():
        if not isinstance(rows, list):
            continue
        for item in rows:
            if isinstance(item, (tuple, list)) and item and isinstance(item[0], (int, float)):
                if last is None or item[0] > last:
                    last = item[0]
    n = len(history)
    status = INSUFFICIENT if n == 0 else "recorded"
    return _module("CorrelationTracker", _CORRELATION, status,
                   _count_detail(status, n, "symbols", fmt_ts(last)))


def _drawdown_module(drawdown_pct: Any) -> dict:
    if isinstance(drawdown_pct, bool) or not isinstance(drawdown_pct, (int, float)):
        figure = "paper book drawdown not read"
    else:
        figure = f"paper book max drawdown {float(drawdown_pct):.2f}%"
    return _module("DrawdownRecovery", _DRAWDOWN, INSUFFICIENT,
                   f"{INSUFFICIENT} · no recovery model · {figure}")


def _timing_module(engine) -> dict:
    hold = getattr(engine, "hold_analytics", None)
    records = getattr(hold, "_records", None) if hold is not None else None
    if isinstance(records, dict):
        hold_n = sum(len(v) for v in records.values() if isinstance(v, list))
    elif hold is None:
        hold_n = None
    else:
        hold_n = None
    tod = getattr(engine, "time_of_day", None)
    stats = getattr(tod, "_stats", None) if tod is not None else None
    if isinstance(stats, dict):
        hour_n = 0
        for by_hour in stats.values():
            if isinstance(by_hour, dict):
                for cell in by_hour.values():
                    if isinstance(cell, dict) and isinstance(cell.get("total"), int):
                        hour_n += cell["total"]
    elif tod is None:
        hour_n = None
    else:
        hour_n = None
    if hold_n is None and hour_n is None:
        return _module("TimingOptimizer", _TIMING, "not read",
                       "not read · hold records not read · hour records not read")
    h = 0 if hold_n is None else hold_n
    t = 0 if hour_n is None else hour_n
    status = INSUFFICIENT if h == 0 and t == 0 else "recorded"
    return _module(
        "TimingOptimizer", _TIMING, status,
        f"{status} · {h} hold records · {t} hour records · not timestamped")


def _voter_fitted_at() -> Any:
    """The fit time stored on the voter-weight file, or None when there is no file.

    An unreadable file returns the string ``"not read"`` so the panel does not
    print ``never`` over a file it failed to open.
    """
    try:
        from pathlib import Path

        from bot.learning import voter_weights as vw
        path = Path(vw._FILE)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Voter-weight fit time could not be read (%s)", type(exc).__name__)
        return "not read"
    stamp = data.get("fitted_at") if isinstance(data, dict) else None
    return stamp if isinstance(stamp, str) else None


def _file_json(store, key: str):
    path = store._files[key]
    try:
        if not path.exists():
            return [], False
        return json.loads(path.read_text(encoding="utf-8")), False
    except Exception as exc:  # noqa: BLE001
        logger.error("%s could not be read for the panel (%s)", key, type(exc).__name__)
        return None, True


def _strategies_scored(store) -> Optional[int]:
    data, bad = _file_json(store, "scorecard")
    if bad:
        return None
    if not isinstance(data, dict):
        return None if data else 0
    n = 0
    for raw in data.values():
        if isinstance(raw, dict):
            trades = raw.get("total_trades")
            if isinstance(trades, int) and not isinstance(trades, bool) and trades > 0:
                n += 1
    return n


def _human_feedback(store) -> Optional[int]:
    path = store._files["feedback"]
    try:
        if not path.exists():
            return 0
    except OSError:
        return None
    try:
        from .models import HumanFeedback
        return len(store._read_jsonl("feedback", HumanFeedback, strict=True))
    except Exception:  # noqa: BLE001
        return None


def _proposals(store) -> tuple[Optional[int], Optional[int]]:
    data, bad = _file_json(store, "backlog")
    if bad:
        return None, None
    if not isinstance(data, list):
        return (None, None) if data else (0, 0)
    pending = blocked = 0
    for row in data:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "pending":
            pending += 1
        elif row.get("status") == "rejected":
            blocked += 1
    return pending, blocked


def _calibration_line(component: Optional[dict]) -> str:
    if not component or not component.get("state"):
        return "Confidence calibration: not read"
    samples = component.get("samples")
    n = "samples not read" if not isinstance(samples, int) or isinstance(samples, bool) else f"{samples} samples"
    flag = "on" if component.get("applied") else "off"
    return f"Confidence calibration: {component['state']}, {n}, flag {flag}"


def _strategy_lines(rows: Optional[list]) -> list[str]:
    if not rows:
        return []
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(strategy_name(r.strategy), []).append({"pnl": r.pnl_result})
    lines = []
    for name in sorted(groups):
        stats = win_stats(groups[name])
        pf = profit_factor(groups[name])
        rate = "win rate not computed" if stats["rate"] is None else f"win {stats['rate']:.0%}"
        pf_txt = "profit factor not computed" if pf is None else f"profit factor {pf:.2f}"
        lines.append(f"  {_esc(name)}: {stats['scored']} scored, {rate}, {pf_txt}")
    return lines


def _direction(trade) -> str:
    raw = getattr(trade, "direction", "")
    if hasattr(raw, "value"):
        raw = raw.value
    return raw if isinstance(raw, str) else ""


def _symbol(trade) -> str:
    for attr in ("asset", "symbol"):
        raw = getattr(trade, attr, None)
        if isinstance(raw, str) and raw:
            return raw
    return ""


def close_row(trade, *, mode: str, source: str) -> Optional[dict]:
    """One close as the outcome ledger wants it, or None when it must not be stored.

    A practice fill is skipped. A close with no readable P&L is skipped.
    """
    from bot.core.practice_fill import close_trains_engine
    if not close_trains_engine(trade):
        return None
    from bot.utils.win_rate import trade_pnl
    pnl = trade_pnl(trade)
    if pnl is None:
        return None
    strategy = getattr(trade, "strategy_type", "")
    trade_id = getattr(trade, "trade_id", "")
    closed = getattr(trade, "closed_at", None)
    return {
        "symbol": _symbol(trade),
        "direction": _direction(trade),
        "pnl": pnl,
        "strategy": strategy if isinstance(strategy, str) else "",
        "trade_id": trade_id if isinstance(trade_id, str) else "",
        "mode": mode,
        "source": source,
        "market_regime": "",
        "closed_at": closed if isinstance(closed, datetime) else None,
    }


def ingest_engine_books(engine) -> int:
    """Copy book closes that are not yet in the scoring record.

    Does not write decision memory, so a paper close still does not train the
    confidence nudge. Returns how many new rows were stored.
    """
    # Not named ``learning``: that word is also ``self.learning`` on the
    # engine, and an assignment this sweep cannot type poisons every call
    # on that attribute, including the close-path outcome writers.
    learning_orch = getattr(engine, "learning", None)
    store = getattr(learning_orch, "store", None)
    evaluator = getattr(learning_orch, "strategy", None)
    if store is None or evaluator is None:
        return 0
    rows: list[dict] = []
    portfolio = getattr(engine, "portfolio", None)
    try:
        history = list(getattr(portfolio, "trade_history", []) or [])
    except Exception as exc:  # noqa: BLE001
        logger.error("Paper book could not be copied for scoring (%s)", type(exc).__name__)
        history = []
    for trade in history:
        row = close_row(trade, mode="paper", source="paper_outcome")
        if row is not None:
            rows.append(row)
    executor = getattr(engine, "live_executor", None)
    if executor is not None and not getattr(executor, "closed_trades_read_failed", False):
        try:
            from bot.core.engine import decision_mode
            mode = decision_mode(executor, live=True)
        except Exception:  # noqa: BLE001
            mode = "live"
        source = "demo_outcome" if mode == "demo" else "live_outcome"
        try:
            closed = list(getattr(executor, "closed_positions", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.error("Exchange book could not be copied for scoring (%s)", type(exc).__name__)
            closed = []
        for trade in closed:
            row = close_row(trade, mode=mode, source=source)
            if row is not None:
                rows.append(row)
    if not rows:
        return 0
    return ingest_closes(store, evaluator, rows)


def _paper_drawdown(engine) -> Any:
    portfolio = getattr(engine, "portfolio", None)
    snap = getattr(portfolio, "snapshot", None)
    if not callable(snap):
        return None
    try:
        state = snap()
    except Exception as exc:  # noqa: BLE001
        logger.error("Paper book drawdown could not be read (%s)", type(exc).__name__)
        return None
    return getattr(state, "max_drawdown_pct", None)


def _books(engine) -> tuple[str, str, dict, dict]:
    portfolio = getattr(engine, "portfolio", None)
    try:
        history = list(getattr(portfolio, "trade_history", []) or [])
        paper_unread = False
    except Exception:  # noqa: BLE001
        history = []
        paper_unread = True
    paper_text = book_line("Paper book", history, unread=paper_unread)
    paper = book_stats(history, unread=paper_unread)

    executor = getattr(engine, "live_executor", None)
    if executor is None:
        return paper_text, "Exchange book: not read", paper, {"unread": True, "scored": None,
                                                              "losses": None, "profit_factor": None}
    if getattr(executor, "closed_trades_read_failed", False):
        label = "Exchange book"
        try:
            from bot.core.engine import decision_mode
            mode = decision_mode(executor, live=True)
            label = "Testnet book" if mode == "demo" else "Live book" if mode == "live" else label
        except Exception:  # noqa: BLE001
            pass
        empty = {"unread": True, "scored": None, "losses": None, "profit_factor": None}
        return paper_text, book_line(label, [], unread=True), paper, empty
    try:
        closed = list(getattr(executor, "closed_positions", []) or [])
        unread = False
    except Exception:  # noqa: BLE001
        closed = []
        unread = True
    try:
        from bot.core.engine import decision_mode
        mode = decision_mode(executor, live=True)
    except Exception:  # noqa: BLE001
        mode = ""
    label = "Testnet book" if mode == "demo" else "Live book" if mode == "live" else "Exchange book"
    return paper_text, book_line(label, closed, unread=unread), paper, book_stats(closed, unread=unread)


def collect_facts(engine, *, readiness: Optional[dict] = None, ingest: bool = True) -> dict:
    """Everything the panel prints. ``ingest`` copies book closes into the scoring record first."""
    if ingest:
        try:
            ingest_engine_books(engine)
        except Exception as exc:  # noqa: BLE001 — the panel still renders when the copy fails
            logger.error("Scoring record was not updated (%s)", type(exc).__name__)
    learning_orch = getattr(engine, "learning", None)
    store = getattr(learning_orch, "store", None)
    if readiness is None and store is not None:
        try:
            from .readiness import assess_readiness
            readiness = assess_readiness(store)
        except Exception as exc:  # noqa: BLE001
            logger.error("Readiness could not be assessed (%s)", type(exc).__name__)
            readiness = None
    components = (readiness or {}).get("components") or {}
    decision_rows, decisions_bad = None, True
    if store is None:
        ledger_rows, ledger = None, outcome_stats(None)
        strategies = humans = None
        pending = blocked = None
    else:
        ledger_rows, _bad = load_outcomes(store)
        ledger = outcome_stats(ledger_rows)
        strategies = _strategies_scored(store)
        humans = _human_feedback(store)
        pending, blocked = _proposals(store)
        decision_rows, decisions_bad = _decision_rows(store)
    voter = components.get("voter_weights")
    paper_text, exchange_text, paper, exchange = _books(engine)
    try:
        from bot.config import CONFIG
        voter_on = bool(CONFIG.analyzer.voter_weight_learning_enabled)
        expectancy_on = bool(CONFIG.analyzer.setup_expectancy_enabled)
    except Exception:  # noqa: BLE001
        voter_on = False
        expectancy_on = False
    return {
        "score": _grade_from(paper, exchange, ledger, _ready_bound(voter)),
        "books": [paper_text, exchange_text],
        "modules": [
            _pattern_module(store, decision_rows, decisions_bad) if store is not None else _module(
                "PatternMemory", _PATTERN, "not read", "not read"),
            _regime_module(decision_rows, decisions_bad, components.get("setup_expectancy"))
            if store is not None else _module(
                "RegimeAdaptation", _REGIME, "not read", "not read"),
            _weights_module(voter, _voter_fitted_at() if voter is not None else None),
            _feedback_module(ledger),
            _volatility_module(decision_rows, decisions_bad) if store is not None else _module(
                "VolatilityProfiler", _VOLATILITY, "not read", "not read"),
            _correlation_module(engine),
            _drawdown_module(_paper_drawdown(engine)),
            _timing_module(engine),
        ],
        "strategy_lines": _strategy_lines(ledger_rows),
        "trade_outcomes": None if ledger.get("unreadable") else ledger.get("closes"),
        "strategies_scored": strategies,
        "human_feedback": humans,
        "pending": pending,
        "blocked": blocked,
        "voter_on": voter_on,
        "expectancy_on": expectancy_on,
        "calibration_line": _calibration_line(components.get("calibration")),
    }


def _code_count(value: Optional[int]) -> str:
    if value is None:
        return "not read"
    return f"<code>{value}</code>"


def render_learning_panel(facts: dict) -> str:
    """The Telegram text. A grade is printed only when ``facts["score"]`` has one."""
    score = facts.get("score") or {}
    lines = ["\U0001f9e0 <b>AI LEARNING SYSTEM</b>", ""]
    grade = score.get("grade")
    if isinstance(grade, (int, float)) and not isinstance(grade, bool):
        lines.append(f"Evidence score: <code>{float(grade):.1f}/10</code>")
    else:
        lines.append("No score.")
    reason = score.get("reason") or ""
    if reason:
        lines.append(_esc(reason))
    lines.append("")
    lines.append("<b>Books</b>")
    for line in facts.get("books") or ["Paper book: not read", "Exchange book: not read"]:
        lines.append(f"- {_esc(line)}")
    lines.append(
        "Book lines are the shared paper book and the operator exchange book. "
        "The learning record also includes any other non-practice close."
    )
    lines.append("")
    lines.append("<b>Modules</b>")
    for mod in facts.get("modules") or []:
        lines.append(f"<b>{_esc(mod.get('name', ''))}</b> — {_esc(mod.get('measures', ''))}")
        lines.append(f"  {_esc(mod.get('detail', ''))}")
    lines.append("")
    if facts.get("strategy_lines"):
        lines.append("<b>Strategies</b>")
        lines.extend(facts["strategy_lines"])
        lines.append("")
    lines.append("<b>Record</b>")
    lines.append(f"- Trade outcomes recorded: {_code_count(facts.get('trade_outcomes'))}")
    lines.append(f"- Strategies scored: {_code_count(facts.get('strategies_scored'))}")
    lines.append(f"- Human feedback: {_code_count(facts.get('human_feedback'))}")
    cal = facts.get("calibration_line")
    if cal:
        lines.append(f"- {_esc(cal)}")
    lines.append("")
    lines.append("<b>Proposals</b>")
    lines.append(f"- Pending: {_code_count(facts.get('pending'))}")
    lines.append(f"- Blocked: {_code_count(facts.get('blocked'))}")
    lines.append("")
    voter = "on" if facts.get("voter_on") else "off"
    expectancy = "on" if facts.get("expectancy_on") else "off"
    lines.append(
        "<i>Safety sandbox active. "
        f"VOTER_WEIGHT_LEARNING_ENABLED is {voter}. "
        f"SETUP_EXPECTANCY_ENABLED is {expectancy}. "
        "These records do not override the risk engine. "
        "A proposal is not applied without approval.</i>"
    )
    return "\n".join(lines)
