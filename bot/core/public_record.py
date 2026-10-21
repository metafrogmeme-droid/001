"""The weekly public record: what the bot has recorded, in percent, ratio and
count only.

Three sections, each read on its own so one unreadable source leaves the
others standing (the composite-view rule: omit and say so, never blank the
page):

- **sleeve** — the daily trend sleeve's paper book
  (`bot/core/daily_trend.record_book`). Its pre-registration
  (`docs/research/daily_trend_sleeve_prereg.md`) forbids measuring it before
  2027-10-17, "and not by anyone who has looked at the rows before then: the
  first reading of the record is the measurement". So this publishes that
  the record is being kept (how many days, the first and last day, the gaps)
  and nothing that is in a row: no weights, no holdings, no gross. A weekly
  public row would be a look at the rows, and a public series of weights
  would let anyone compute the returns early.
- **equity** — the live book's daily equity (`bot/core/equity_record`),
  turned into day-over-day returns. A Sharpe ratio is printed only once
  `MIN_DAILY_RETURNS` returns exist; before that the section says how many it
  has. Equity is never published, only returns, drawdown and counts. The log
  does not separate deposits and withdrawals from trading, and the section
  says so beside every figure.
- **parity** — live exits against the backtest, the dollar-free projection the
  dashboard's reports panel already serves (`web_reports.parity_public`).

`scripts/weekly_record.py` writes this payload to `benchmark/record/` once a
week for the operator to commit, so each week's record is frozen in git.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

FORMAT = "runeclaw.public_record.v1"

#: Daily returns needed before a Sharpe ratio is printed. Thirty is a month of
#: days: fewer, and the ratio's standard error is larger than the ratio.
MIN_DAILY_RETURNS = 30

#: The sleeve's sealed measurement date (docs/research/daily_trend_sleeve_prereg.md,
#: "Not before 2027-10-17"). A test holds this to the document's own words.
SLEEVE_MEASURED_ON = "2027-10-17"
SLEEVE_PREREG = "docs/research/daily_trend_sleeve_prereg.md"

ABSENT = "absent"
READ = "read"
UNREADABLE = "unreadable"


class RecordUnreadable(Exception):
    """A source file exists and could not be read as a record."""

    # A body besides the docstring, so `code_only` (which blanks docstrings)
    # leaves this file parseable for the source scans that walk bot/.
    pass


def _finite(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _jsonl_rows(path: Path) -> tuple[list[dict], int]:
    """The readable rows of a JSONL file and the count of lines that were not
    one. Raises RecordUnreadable when the file exists but cannot be opened, or
    holds lines and not one readable row (a file of junk is not an empty
    record)."""
    rows: list[dict] = []
    skipped = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(row, dict) and isinstance(row.get("day"), str):
                    rows.append(row)
                else:
                    skipped += 1
    except OSError as exc:
        raise RecordUnreadable(type(exc).__name__) from exc
    if skipped and not rows:
        raise RecordUnreadable("no readable row")
    return rows, skipped


def _day(s: object) -> Optional[date]:
    try:
        return date.fromisoformat(s) if isinstance(s, str) else None
    except (TypeError, ValueError):
        return None


def _gaps(days: list[date]) -> int:
    """Calendar days between the first and last that have no row."""
    if len(days) < 2:
        return 0
    span = (days[-1] - days[0]).days + 1
    return max(0, span - len(set(days)))


# ── sleeve ───────────────────────────────────────────────────────────────

def sleeve_section(path: Optional[Path] = None) -> dict:
    """That the sleeve's record is being kept, in counts. Reads each row's
    ``day`` and nothing else in it (the module docstring says why)."""
    from bot.core.daily_trend import book_log_path
    p = path or book_log_path()
    base = {"measured": False, "measured_on": SLEEVE_MEASURED_ON,
            "prereg": SLEEVE_PREREG, "places_orders": False}
    if not p.exists():
        return {**base, "state": ABSENT, "days_recorded": 0}
    rows, skipped = _jsonl_rows(p)
    days = sorted(d for d in (_day(r.get("day")) for r in rows) if d is not None)
    if not days:
        return {**base, "state": ABSENT, "days_recorded": 0, "skipped_rows": skipped}
    return {
        **base,
        "state": READ,
        "days_recorded": len(set(days)),
        "first_day": days[0].isoformat(),
        "last_day": days[-1].isoformat(),
        "gap_days": _gaps(days),
        "skipped_rows": skipped,
    }


# ── equity ───────────────────────────────────────────────────────────────

def _sharpe(returns: list[float]) -> Optional[float]:
    if len(returns) < MIN_DAILY_RETURNS:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return round(mean / sd * math.sqrt(365), 2)


def equity_section(path: Optional[Path] = None) -> dict:
    from bot.core.equity_record import equity_log_path
    p = path or equity_log_path()
    base = {"book": "live", "min_daily_returns": MIN_DAILY_RETURNS,
            "flows_separated": False}
    if not p.exists():
        return {**base, "state": ABSENT, "days_recorded": 0, "daily_returns": 0,
                "sharpe": None}
    rows, skipped = _jsonl_rows(p)
    series: dict[date, float] = {}
    for r in rows:
        d = _day(r.get("day"))
        eq = _finite(r.get("equity_usd"))
        if d is None or eq is None or eq <= 0:
            skipped += 1
            continue
        series[d] = eq                        # one row a day; a repeat keeps the last
    days = sorted(series)
    if not days:
        return {**base, "state": ABSENT, "days_recorded": 0, "daily_returns": 0,
                "sharpe": None, "skipped_rows": skipped}
    returns = [series[b] / series[a] - 1.0 for a, b in zip(days, days[1:])]
    peak = series[days[0]]
    max_dd = 0.0
    for d in days:
        peak = max(peak, series[d])
        max_dd = min(max_dd, series[d] / peak - 1.0)
    return {
        **base,
        "state": READ,
        "days_recorded": len(days),
        "first_day": days[0].isoformat(),
        "last_day": days[-1].isoformat(),
        "gap_days": _gaps(days),
        "skipped_rows": skipped,
        "daily_returns": len(returns),
        "return_pct": round((series[days[-1]] / series[days[0]] - 1.0) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": _sharpe(returns),
    }


# ── parity ───────────────────────────────────────────────────────────────

def parity_section(path: Optional[Path]) -> dict:
    """Live exits against the backtest, dollar-free. ``path`` is the live
    executor's closed-trades file; None means no live book is configured."""
    if path is None:
        return {"state": ABSENT}
    p = Path(path)
    if not p.exists():
        return {"state": ABSENT}
    # load_closed_trades answers [] for a file it cannot parse, which would
    # read here as "no live trades yet". Parse first, so a broken file is
    # unreadable and only a readable empty list is absent.
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordUnreadable(type(exc).__name__) from exc
    if not isinstance(data, (list, dict)):
        raise RecordUnreadable("not a trade list")
    from bot.backtest.parity import load_closed_trades
    from bot.core.web_reports import parity_public
    trades = load_closed_trades(p)
    if not trades:
        return {"state": ABSENT, "trades": 0}
    out = parity_public(trades)
    if not out.get("trades"):
        # Rows on file, none of them a scored strategy exit (non-fills, or
        # closes with no PnL record): parity_summary's win rate is then a
        # stated 0.0 over nothing, which is not a measurement. Absent, with
        # the counts that say why.
        return {"state": ABSENT, "trades": 0,
                "excluded_non_fills": out.get("excluded_non_fills"),
                "unscored_pnl": out.get("unscored_pnl")}
    return {"state": READ, **out}


# ── the record ───────────────────────────────────────────────────────────

def _guarded(builder: Callable[[], dict]) -> dict:
    try:
        return builder()
    except Exception as exc:  # noqa: BLE001 — one section must not blank the rest
        return {"state": UNREADABLE, "exception": type(exc).__name__}


def iso_week(when: datetime) -> str:
    y, w, _ = when.isocalendar()
    return f"{y}-W{w:02d}"


def build_public_record(*, closed_trades_path: Optional[Path],
                        sleeve_path: Optional[Path] = None,
                        equity_path: Optional[Path] = None,
                        now: Optional[datetime] = None) -> dict:
    when = now or datetime.now(UTC)
    return {
        "format": FORMAT,
        "as_of": when.isoformat(timespec="seconds"),
        "week": iso_week(when),
        "week_starts": (when.date() - timedelta(days=when.weekday())).isoformat(),
        "sleeve": _guarded(lambda: sleeve_section(sleeve_path)),
        "equity": _guarded(lambda: equity_section(equity_path)),
        "parity": _guarded(lambda: parity_section(closed_trades_path)),
    }


__all__ = ["FORMAT", "MIN_DAILY_RETURNS", "SLEEVE_MEASURED_ON", "SLEEVE_PREREG",
           "ABSENT", "READ", "UNREADABLE", "RecordUnreadable", "sleeve_section",
           "equity_section", "parity_section", "build_public_record", "iso_week"]
