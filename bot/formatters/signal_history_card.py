"""The signal history card (``/signals``): what the published calls did.

It read ``SignalTracker``, an in-memory store nothing recorded into (its
``record_signal`` and ``record_outcome`` had no caller outside tests), so the
command answered "No signals recorded yet" whatever the bot had published.
The published calls and what became of them are in the outcome ledger
(``bot.core.signal_outcomes``), which the engine walks on hourly candles; this
card renders that ledger's summary and nothing else, so it cannot disagree with
the website's stats panel about what a call did.

Three states at the top, because they are three facts: the ledger could not be
read (never "no signals"), it holds no call, or it holds some. A call is not a
position: nobody's money is in it, so its result is in R, gross of fees, and
the card says so. No verdict is drawn: the counts and the mean R with its
denominator are printed, and a reader sees how few there are.
"""
from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Callable, Optional

TITLE = "<b>📊 SIGNAL HISTORY</b>"
RULE = "━━━━━━━━━━━━━━━━━━━━━"

UNREAD = ("The published-signal ledger could not be read, so this is not an "
          "empty record: nothing was counted.")
EMPTY = "No published signal is on record since the ledger began."
BASIS = ("Each call is walked on hourly candles from when it was published: its "
         "target scores its own reward÷risk in R, its stop scores −1R, gross of "
         "fees (a call has no size). OTH ended with no R: not filled, an "
         "ambiguous bar, no exit within 7 days, or unscorable. OPN is pending.")

_HEAD = f" {'PAIR':<9}{'CALL':>5}{'T':>4}{'S':>4}{'OTH':>4}{'OPN':>4}{'HIT':>5}{'AVG R':>7}"


def _short(sym: str) -> str:
    s = str(sym or "?").split(":")[0].replace("/USDT", "").replace("/", "")
    return (s or "?")[:9]


def hit_text(target: int, stop: int) -> str:
    """Target as a share of the calls that reached a level, or a dash."""
    n = target + stop
    return "—" if n <= 0 else f"{round(100 * target / n)}%"


def avg_r_text(r_sum: float, r_n: int) -> str:
    """The mean R over the calls that carry one, or a dash over none."""
    return "—" if r_n <= 0 else f"{r_sum / r_n:+.2f}"


def _row(label: str, p: dict) -> str:
    return (f" {label:<9}{p['calls']:>5}{p['target']:>4}{p['stop']:>4}"
            f"{p['other']:>4}{p['open']:>4}"
            f"{hit_text(p['target'], p['stop']):>5}{avg_r_text(p['r_sum'], p['r_n']):>7}")


def _span(summary: dict) -> str:
    total = summary["total"]["calls"]
    ms = summary.get("oldest_ms")
    oldest = (datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")
              if isinstance(ms, (int, float)) else "not on record")
    keep_days = int(summary["keep_resolved_s"]) // 86400
    return (f"Span: the {total} call(s) the ledger holds, oldest published {oldest}. "
            f"A resolved call is kept {keep_days} days after it syncs and the ledger "
            f"holds at most {summary['max_rows']} rows, so this is the recent "
            f"record, not necessarily the whole history.")


def _default_grouping() -> tuple[Callable, Callable, Callable]:
    from bot.core.market_scanner import category_for_symbol, category_icon, group_by_category
    return group_by_category, category_icon, category_for_symbol


def render(summary: Optional[dict], grouping: Optional[tuple] = None) -> str:
    """The card for ``summary`` (``signal_outcomes.ledger_summary()``)."""
    if summary is None:
        return "\n".join([TITLE, RULE, "", f"<i>{UNREAD}</i>"])
    pairs = summary.get("pairs") or {}
    skipped = int(summary["skipped"])
    skip_line = (f"{skipped} ledger row(s) could not be read and are not counted."
                 if skipped else "")
    if not pairs:
        out = [TITLE, RULE, "", f"<i>{EMPTY}</i>"]
        if skip_line:
            out.append(f"<i>{skip_line}</i>")
        return "\n".join(out)

    group_by_category, category_icon, category_for_symbol = grouping or _default_grouping()
    entries = sorted(pairs.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))
    grouped = group_by_category(entries, lambda e: category_for_symbol(e[0]))
    lines = [TITLE, RULE, ""]
    for cat, cat_entries in grouped.items():
        lines.append(f"{category_icon(cat)} <b>{html.escape(str(cat))}</b>")
        lines.append("<pre>")
        lines.append(_HEAD)
        for sym, p in cat_entries:
            lines.append(html.escape(_row(_short(sym), p)))
        lines.append("</pre>")

    t = summary["total"]
    lines.append(html.escape(
        f"All: {t['calls']} call(s) · {t['target']} target / {t['stop']} stop "
        f"(hit {hit_text(t['target'], t['stop'])} of the {t['target'] + t['stop']} "
        f"that reached a level) · avg R {avg_r_text(t['r_sum'], t['r_n'])} over "
        f"{t['r_n']} · {t['other']} ended with no R · {t['open']} pending"))
    if t.get("unknown"):
        lines.append(html.escape(
            f"{t['unknown']} call(s) carry a word this build does not know; they "
            f"are counted in CALL and in no other column."))
    if skip_line:
        lines.append(skip_line)
    lines.append("")
    lines.append(f"<i>{html.escape(BASIS)}</i>")
    lines.append(f"<i>{html.escape(_span(summary))}</i>")
    return "\n".join(lines)
