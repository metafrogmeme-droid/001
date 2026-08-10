"""The Paper Arena, rendered for Telegram.

The Arena is the zero-friction on-ramp: every registered member gets a virtual
account with the same starting stake, no exchange keys, no setup. It has a
season, a standings board, a live tape and a Hall of Champions — eighteen
endpoints on the web, and not one door from the chat where the members are.

This is the door. Everything it renders is public-surface data: opt-in handles,
percent returns against a FIXED starting stake (so the number is size-agnostic
by construction), and counts. `assert_no_money` runs over the finished card —
the same guard the share button and the verified board use, over the same
audited pattern.

FOUR PAIRS THIS CARD REFUSES TO CONFLATE. Each is a real reading on one side
and an absence on the other, and each would render identically under the shapes
CLAUDE.md lists:

  unreadable board          vs   a board with nobody on it
  no season configured      vs   the site could not be reached
  a season not yet started  vs   a season running with no closes yet
  a close of exactly 0.0%   vs   a close whose return could not be computed

The first is why `arena_pull` returns None rather than `[]`, and why a dead
tape omits the tape section instead of printing "0 closes" over a floor this
process never reached.

VIRTUAL IS A CLAIM TOO, and the one worth most. "+12.4%" on a board headed
"trading arena" is exactly the number a reader compares against real money.
The card says *virtual funds* only when the payload asserts ``virtual: true``,
never by default — see `arena_pull.is_virtual`. If the source stops saying it,
this stops promising it.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from bot.formatters.share_invite import MoneyLeak, assert_no_money
from bot.utils.arena_pull import is_virtual

#: Chat-window budgets. Both are STATED when they bite, never silently applied.
STANDINGS_ROWS = 8
TAPE_ROWS = 8

#: The close reasons `app/lib/arena.js` and `app/routes/arena.js` actually
#: write. An unrecognised one renders as a dash: inventing a label for a reason
#: this bot has never heard of is how a vocabulary drifts into a lie.
REASONS = {"tp": "TP", "sl": "SL", "liquidated": "LIQ", "manual": "manual"}

UNREACHABLE = (
    "🏟️ <b>PAPER ARENA</b>\n\n"
    "<i>The arena could not be read just now — this is a failure to reach it, "
    "not an empty floor. Nothing here is a measurement. Try again shortly.</i>")


def _pct(value: Any) -> Optional[float]:
    """A finite percentage, or None. Never a substituted zero."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _pct_text(value: Any) -> str:
    """`+2.10%` / `-0.80%`, or an em-dash for anything unreadable.

    Not `0.00%`. A close that returned exactly break-even and a close whose
    return could not be computed are different facts, and `float(x or 0)` on
    this line would publish the second as the first.
    """
    pct = _pct(value)
    return "—" if pct is None else f"{pct:+.2f}%"


def _mark(value: Any) -> str:
    """The row's glyph. Unknown gets a neutral one — colour is a claim.

    `>= 0` would mark an unreadable close as a win, which is the `(x or 0) >= 0`
    shape this repo has been bitten by. Break-even is genuinely flat and is
    marked as such.
    """
    pct = _pct(value)
    if pct is None:
        return "·"
    return "▲" if pct > 0 else ("▼" if pct < 0 else "=")


def _countdown(target: Any, now: Optional[datetime] = None) -> str:
    """`3d 4h` until ``target``, or "" when it cannot be read or has passed.

    Empty rather than a guess: a countdown is the most-read thing on a season
    card, and one computed from an unparseable timestamp would be confidently
    wrong about the only fact a member acts on.

    The CALLER picks the target, because the two seasons states count toward
    different instants. Always counting to ``ends_at`` put "51d 12h left"
    beside an *upcoming* season — which reads as fifty-one days left to
    compete, in a competition that has not opened.
    """
    raw = str(target or "").strip()
    if not raw:
        return ""
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    secs = int((when - (now or datetime.now(timezone.utc))).total_seconds())
    if secs <= 0:
        return ""
    days, rem = divmod(secs, 86400)
    hours = rem // 3600
    return f"{days}d {hours}h" if days else f"{hours}h"


def _standings(rows: Sequence[Mapping[str, Any]], me: str) -> list[str]:
    out = [f"  {'#':<3} {'HANDLE':<14} {'RETURN':>8}  ✓"]
    for row in rows[:STANDINGS_ROWS]:
        handle = str(row.get("handle") or "anon")[:14]
        mine = "◀" if me and handle.lower() == me else " "
        sealed, closes = row.get("sealed"), row.get("closes")
        # "3/8" is how much of this record carries an open-time receipt. Absent
        # counts print as a dash rather than 0/0, which would read as "nothing
        # of this is provable" when it means "we were not told".
        proof = (f"{sealed}/{closes}" if isinstance(sealed, int)
                 and isinstance(closes, int) else "—")
        out.append(f"  {str(row.get('rank') or '?'):<3} {handle:<14} "
                   f"{_pct_text(row.get('return_pct')):>8}  {proof:>5}{mine}")
    return out


def _symbol(raw: Any) -> str:
    """`NATGASUSDT` → `NATGAS`. The base asset, never a truncated ticker.

    The tape returns symbols WITHOUT a slash (`ZESTUSDT`), unlike the ccxt-style
    `BTC/USDT:USDT` every fixture used. Stripping only the punctuated forms left
    the quote currency attached, and the column cut then produced `NATGASUSD`
    and `FARTCOINU` — which do not read as an abbreviation, they read as a
    different instrument. Only live data showed it.
    """
    text = str(raw or "").upper().replace("/", "").replace(":", "")
    for quote in ("USDT", "USDC", "USD"):
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)][:9]
    return text[:9] or "?"


def _tape_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    for row in rows[:TAPE_ROWS]:
        handle = str(row.get("handle") or "anon")[:12]
        direction = str(row.get("direction") or "").upper()[:5]
        reason = REASONS.get(str(row.get("reason") or "").lower(), "—")
        seal = "🔏" if row.get("key") else " "
        out.append(f"  {_mark(row.get('pct'))} {handle:<12} {_symbol(row.get('symbol')):<9} "
                   f"{direction:<5} {_pct_text(row.get('pct')):>8}  {reason:<6} {seal}")
    return out


def render_arena(season: Optional[Mapping[str, Any]],
                 tape: Optional[Mapping[str, Any]],
                 viewer_handle: Optional[str] = None,
                 now: Optional[datetime] = None) -> str:
    """The arena card. ``None`` for either half means THAT half was unreadable.

    Both unreadable is the only case that refuses to render a card at all —
    with one board alive there is still something true to show, and blanking it
    because the other timed out is the failure mode the omit strategy exists to
    prevent.
    """
    if season is None and tape is None:
        return UNREACHABLE

    me = str(viewer_handle or "").strip().lower()
    virtual = is_virtual(season) or is_virtual(tape)
    head = "🏟️ <b>PAPER ARENA</b>"
    out = [head + (" — virtual funds, percent only" if virtual else "")]
    if not virtual:
        # The source stopped asserting it, so this stops promising it — and
        # says which, rather than quietly dropping four words nobody misses.
        out.append("<i>⚠ The source did not confirm these are practice funds. "
                   "Treat the figures below as unlabelled.</i>")

    # ── Season ──────────────────────────────────────────────────────────
    if season is None:
        out.append("\n<i>Season standings could not be read. The tape below "
                   "loaded fine.</i>")
    else:
        info = season.get("season")
        if not isinstance(info, Mapping):
            out.append("\n<i>No season is running right now — the all-time "
                       "board keeps counting between seasons.</i>")
        else:
            status = str(info.get("status") or "unknown")
            name = html.escape(str(info.get("name") or "Season")[:40])
            # An unstarted season counts toward its OPENING; a running one
            # toward its close. Same widget, two different instants.
            if status == "upcoming":
                left = _countdown(info.get("starts_at"), now)
                clock = f"starts in {left}" if left else ""
            else:
                left = _countdown(info.get("ends_at"), now)
                clock = f"{left} left" if left else ""
            line = f"\n<b>{name}</b> · {html.escape(status)}"
            out.append(line + (f" · {clock}" if clock else ""))
            if status == "upcoming":
                # No standings EXIST yet — the endpoint omits them entirely for
                # an unstarted season. Printing an empty board here would say
                # "nobody has scored", which is a different claim.
                out.append("<i>Not started — standings open when it does.</i>")
            else:
                rows = season.get("rows")
                if not isinstance(rows, list):
                    out.append("<i>Standings unavailable for this season.</i>")
                elif not rows:
                    out.append("<i>No closes inside the window yet — the board "
                               "fills as trades close.</i>")
                else:
                    out.append("<pre>" + html.escape(
                        "\n".join(_standings(rows, me))) + "</pre>")
                    if len(rows) > STANDINGS_ROWS:
                        out.append(f"<i>{len(rows) - STANDINGS_ROWS} more "
                                   "ranked below.</i>")
                    if me and not any(str(r.get("handle") or "").lower() == me
                                      for r in rows):
                        out.append("<i>You have no closes in this window yet.</i>")

    # ── Tape ────────────────────────────────────────────────────────────
    if tape is None:
        out.append("\n<i>The live tape could not be read. The standings above "
                   "loaded fine.</i>")
    else:
        rows = tape.get("rows")
        if not isinstance(rows, list):
            out.append("\n<i>The live tape was unreadable.</i>")
        elif not rows:
            out.append("\n<i>No closes on the tape yet from opted-in traders. "
                       "Only members who chose a public handle appear here.</i>")
        else:
            out.append("\n<b>Live tape</b> — last closes")
            out.append("<pre>" + html.escape("\n".join(_tape_lines(rows))) + "</pre>")
        pulse = []
        for key, label in (("traders", "traders"), ("trades_24h", "closes in 24h")):
            val = tape.get(key)
            # `is not None` and a real int: 0 traders is a reading, a missing
            # count is not, and `.get(key, 0)` would print the second as the
            # first.
            if isinstance(val, int) and not isinstance(val, bool):
                pulse.append(f"{val} {label}")
        if pulse:
            out.append("<i>" + " · ".join(pulse) + "</i>")

    out.append("<i>Ranked on percent return against the same starting stake "
               "for everyone. Percent and counts only — never account size.</i>")
    try:
        return assert_no_money("\n".join(out))
    except MoneyLeak:
        # The arena is a public surface and this text is one tap from a group
        # chat. Refusing to render is the smaller harm; unlike the leaderboard
        # there is no per-row structure to withhold, because the leak could be
        # in a season name the operator typed.
        return ("🏟️ <b>PAPER ARENA</b>\n\n<i>This card was withheld: it carried "
                "a figure that must not appear on a public surface. The "
                "operator has been notified.</i>")
