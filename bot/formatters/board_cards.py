"""The public leaderboard, rendered for Telegram.

`bot/proofofpnl/leaderboard.py` builds a cryptographically re-verified,
size-agnostic board — every row's `publish_hash` is re-derived before it can
rank, and dollar magnitudes are excluded at the source rather than filtered
here. The web has three endpoints onto it. The bot had none, so the one surface
whose entire pitch is "re-verifiable, not self-reported" was invisible from the
place the members actually live.

This is the door, and it renders three things the board's own credibility
depends on:

**The denominator.** A rank without a total is half a fact. The web version
learned this the hard way — its own comment records that anyone below the
top-50 cutoff "used to be invisible and unmotivating", so it added `my_rank`
and `ranked_total`. Same rule here: `#7` alone is a boast, `#7 of 23` is a
measurement.

**Infinity is not a score.** `profit_factor` is `'inf'` for a member who has
not yet lost a round-trip. Printing `inf`, or worse sorting it to the top and
formatting it as a number, reads as extraordinary skill when it usually means
three trades and no drawdown yet. It renders as `∞` and says why.

**Verification is the product.** `trust_tier` and `reconciliation` are what
separate this board from a self-reported one; a card that drops them keeps the
ranking and throws away the reason to believe it.

WHERE THE DOLLAR GUARD GOES, and why it is not one call at the end.

CLAUDE.md: no dollar amounts on public, community or leaderboard payloads. The
first draft ran `assert_no_money` once over the finished card — which is the
**guard** strategy from the CLAUDE.md table, and the table says guard is for *a
single-source panel*. A leaderboard is the other row: a composite view, where
one dead source must not blank the rest. One member whose row carried a dollar
figure would have raised and taken the board down for all twenty-three.

So the guard runs **per row** and a failing row is withheld and *counted on the
card* — omission is only honest when it is stated, and this file already argues
that a truncated list which looks complete is its own lie. The final
`assert_no_money` stays as a backstop over the chrome the renderer itself
writes, which is where a plausible future leak actually lives: a promo footer
naming a prize.

Handles are echoed, never repaired. Stripping `$` out of `night$jar` yields
`nightjar`, which can collide with a real member — silently manufacturing an
impersonation on a board whose entire point is verified identity. Withholding
the row is the smaller harm.

Reachability, before anyone "fixes" the upstream: `app/routes/leaderboard.js`
enforces a 3–20 word-character `HANDLE_RE` on opt-in, and the operator handle
comes from an env var, so a money-bearing handle is not reachable today. It is
guarded anyway because that rule is written in JavaScript, this consumer is
Python, and `LeaderboardRegistry.put` between them accepts any non-empty
string. An invariant that crosses a language boundary with nothing pinning it
is the drift this repo spends most of its guard tests preventing.
"""
from __future__ import annotations

import html
from typing import Any, Mapping, Optional, Sequence

from bot.formatters.share_invite import MoneyLeak, assert_no_money

#: Rows past this are noise in a chat window. The board itself is capped
#: upstream; this is the display cut, and it is STATED on the card rather than
#: silently applied — a truncated list that looks complete is its own lie.
DISPLAY_ROWS = 10

#: Chrome the renderer writes itself. Named so the closing `assert_no_money`
#: has something real to protect: the plausible future leak on this surface is
#: not a member's row, it is a promo footer naming a prize.
TITLE = "🏆 <b>LEADERBOARD</b> — verified record, no dollar figures"
INF_NOTE = ("<i>∞ = no losing round-trip yet. Usually a short record, "
            "not a perfect one.</i>")
FOOTER = ("<i>Ranked on profit factor from sealed, re-verifiable records. "
          "Ratios and counts only — never account size.</i>")
EMPTY = ("🏆 <b>LEADERBOARD</b>\n\n"
         "<i>No verified entries yet. The board ranks opted-in members whose "
         "sealed record re-verifies — it is empty because nobody qualifies "
         "yet, not because it failed to load.</i>")


def _pf(value: Any) -> str:
    """Profit factor for display. `inf` becomes a symbol, never a number.

    Anything unparseable collapses to the same em-dash as absent, rather than
    being echoed back onto a public card. "We could not read this" is the whole
    of what an unparseable ratio tells a reader; the raw bytes tell them nothing
    more and put upstream data on a public surface to say it.
    """
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return "—"
    if text in ("inf", "infinity", "∞"):
        return "∞"
    try:
        return f"{float(text):.2f}"
    except (TypeError, ValueError):
        return "—"


def _tier_mark(row: Mapping[str, Any]) -> str:
    """A compact credibility mark. Absent verification shows as absent."""
    tier = str(row.get("trust_tier") or "").strip().lower()
    if tier.startswith("gold"):
        return "🥇"
    if tier.startswith("silver"):
        return "🥈"
    if tier.startswith("bronze"):
        return "🥉"
    return "·"


def _row_line(row: Mapping[str, Any], me: str) -> Optional[str]:
    """One board line, or None if it cannot be published as-is.

    None rather than a repaired line: see the module docstring on why a mangled
    handle is worse than a missing row.
    """
    handle = str(row.get("handle") or "anon")[:14]
    mark = "◀" if me and handle.lower() == me else " "
    line = (f"  {str(row.get('rank') or '?'):<3} {handle:<14} "
            f"{_pf(row.get('profit_factor')):>6}"
            f"  {str(row.get('round_trips', '—')):>4}  {_tier_mark(row)}{mark}")
    try:
        return assert_no_money(line)
    except MoneyLeak:
        return None


def _has_unbounded_pf(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(_pf(r.get("profit_factor")) == "∞" for r in rows)


def render_leaderboard(rows: Optional[Sequence[Mapping[str, Any]]],
                       viewer_handle: Optional[str] = None,
                       ranked_total: Optional[int] = None,
                       limit: int = DISPLAY_ROWS,
                       viewer_opted_in: Optional[bool] = None) -> str:
    """The board, or an honest account of why there isn't one.

    ``ranked_total`` is the number of members who RANKED, which is not
    ``len(rows)`` once the display cut applies — passing it is what makes
    "#7 of 23" possible. None renders as an unknown denominator rather than
    silently reusing the row count, which would tell every reader they were
    last.

    ``viewer_opted_in`` is TRI-STATE and tested with ``is None``, because a
    missing handle has two very different causes. The first draft rendered
    "Not on the board — opt in from the dashboard" whenever no handle was
    passed, which turned an unread input into a confident negative about the
    reader's own account — and the caller could not read it at all, so every
    member would have been told that. ``False`` is a measured "you have not
    opted in"; ``None`` means nobody looked, and the standing line is omitted
    rather than guessed.
    """
    ordered = list(rows or [])
    if not ordered:
        return EMPTY

    total = ranked_total if isinstance(ranked_total, int) else None
    me = str(viewer_handle or "").strip().lower()

    body = [f"  {'#':<3} {'HANDLE':<14} {'PF':>6}  {'RT':>4}  ✓"]
    printed: list[Mapping[str, Any]] = []
    withheld = 0
    for row in ordered[:limit]:
        line = _row_line(row, me)
        if line is None:
            withheld += 1
            continue
        body.append(line)
        printed.append(row)

    out = [TITLE]
    if total is not None:
        out.append(f"<i>{len(printed)} of {total} ranked</i>")
    else:
        out.append(f"<i>{len(printed)} shown — total ranked unknown</i>")
    out += ["", "<pre>" + html.escape("\n".join(body)) + "</pre>"]

    # The caller's own standing, including when they fell outside the display
    # cut. Without this the board silently stops mentioning you the moment you
    # drop to 11th, which is exactly when you most want to know.
    if me:
        mine = next((r for r in ordered
                     if str(r.get("handle") or "").lower() == me), None)
        if mine is None:
            out.append("<i>You are opted in but not ranked yet — a verified "
                       "round-trip is needed to appear.</i>")
        elif not any(mine is p for p in printed):
            denom = f" of {total}" if total is not None else ""
            mine_line = (f"<i>You: #{mine.get('rank')}{denom} — "
                         f"PF {_pf(mine.get('profit_factor'))}, "
                         f"{mine.get('round_trips', '—')} round-trips.</i>")
            try:
                out.append(assert_no_money(mine_line))
            except MoneyLeak:
                out.append("<i>Your row could not be displayed. Contact the "
                           "operator — your rank is unaffected.</i>")
    elif viewer_opted_in is False:
        out.append("<i>Not on the board — opt in from the dashboard with an "
                   "anonymous handle.</i>")

    remaining = len(ordered) - len(printed) - withheld
    if remaining > 0:
        out.append(f"<i>{remaining} more ranked below.</i>")
    if withheld:
        # Stated, never silent: a board short by one row with no explanation is
        # indistinguishable from a board that simply has one fewer member.
        out.append(f"<i>{withheld} row(s) withheld — a value on them could not "
                   "be published. Ranks above are unchanged.</i>")
    if _has_unbounded_pf(printed):
        out.append(INF_NOTE)
    out.append(FOOTER)
    return assert_no_money("\n".join(out))
