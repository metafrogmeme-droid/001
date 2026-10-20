"""
The Daily Duel card, as a PURE renderer.

This is a seam, deliberately. CLAUDE.md records what happens without one: the
Telegram adoption card was built inline in the handler, #999 added a per-position
SL/TP outcome, source-scanned it, shipped it — and it rendered zero times in
production because the callback received prose where the lookup expected symbols.
The code was present. It was never reached, and no scan can tell those apart.

So the card is built here, from a plain dict, and the handler only sends what
this returns.

THE RULE THIS FILE KEEPS. A duel call has four states and only two are results:

    pending      the horizon has not arrived. Nothing is known yet.
    unresolved   it has, and no price could be read. Still nothing.
    correct/wrong/push   an actual outcome.

Rendering either absence as a result is how a call that is merely waiting
starts reading as a loss. Accuracy is shown as a dash — never 0% — until
something has actually settled, because a player who has not been measured has
not failed.

No dollar amount appears anywhere: a duel has no stake to report.

THE CATEGORIES (2026-10-19). A round carries its `kind` and its `options`;
the card prints the question each kind asks and the keyboard offers the
round's own words, so the bot never offers a tap the server would refuse.
"""

from __future__ import annotations

from typing import Any

# Muted glyphs for the two unknowns; a result gets a glyph that claims something.
_STATE_GLYPH = {
    "correct": "✅",
    "wrong": "❌",
    "push": "➖",
    "unresolved": "⚪",
    "pending": "⏳",
}

_PICK_WORD = {"long": "LONG", "short": "SHORT", "pass": "PASS"}


CALLBACK_PREFIX = "duel"
# THE CATEGORIES (2026-10-19). The picks each round takes, as app/lib/duel.js
# KINDS defines them; tests/test_duel_telegram_surface.py holds the two to one
# answer by reading that file. The card itself says which words a round takes
# (`options`), and the keyboard is built from those; this table is the
# fallback for a card that predates the field, and the set the callback parser
# admits. A round of a kind not listed here offers no button.
KIND_PICKS = {
    "direction": ("long", "short", "pass"),
    "backfade": ("back", "fade", "pass"),
    "range": ("break", "hold", "pass"),
    "mover": ("first", "second", "pass"),
    "sports": ("home", "draw", "away", "pass"),
}
PICKS = tuple(dict.fromkeys(p for picks in KIND_PICKS.values() for p in picks))
_KIND_NAME = {
    "direction": "Direction",
    "backfade": "Back or fade",
    "range": "Range",
    "mover": "Bigger mover",
    "sports": "Football",
}


def kind_of(round_: dict | None) -> str | None:
    """A round's category. Absent means a classic (direction) round, the rows
    that predate the categories; a kind this code does not know is None, and
    a None kind gets no question, no button and no guess at what it meant."""
    kind = (round_ or {}).get("kind")
    if kind is None or kind == "":
        return "direction"
    kind = str(kind).strip().lower()
    return kind if kind in KIND_PICKS else None


def _base(symbol: Any) -> str:
    """'ARBUSDT' -> 'ARB'; the symbol itself when no quote suffix is found."""
    sym = str(symbol or "")
    for quote in ("USDT", "USDC", "USD"):
        if sym.upper().endswith(quote) and len(sym) > len(quote):
            return sym[: -len(quote)]
    return sym


def _band(round_: dict | None) -> str:
    """'±2%' for a range round's own band; a dash when it carries none."""
    thr = _num((round_ or {}).get("threshold_pct"))
    return f"±{thr:g}%" if thr is not None and thr > 0 else "—"


def _team(round_: dict | None, side: str) -> str:
    """A football round's team, short name first; a dash when unread."""
    match = (round_ or {}).get("match") or {}
    return str(match.get(f"{side}_short") or match.get(side) or "—")


def _utc(raw: object, fmt: str) -> str:
    """An ISO instant in the given strftime shape, UTC; a dash when unreadable."""
    if not raw:
        return "—"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(fmt)


def _kickoff_utc(round_: dict | None) -> str:
    """'HH:MM UTC' from the match's kick-off, or a dash when unreadable."""
    return _utc(((round_ or {}).get("match") or {}).get("kickoff"), "%H:%M UTC")


def round_title(round_: dict | None) -> str:
    """What a round is about: its symbol, a mover's pair, or a match's teams."""
    r = round_ or {}
    kind = kind_of(r)
    if kind == "mover":
        return f"{_base(r.get('symbol'))} vs {_base(r.get('symbol_b'))}"
    if kind == "sports":
        return f"{_team(r, 'home')} vs {_team(r, 'away')}"
    return str(r.get("symbol") or "?")


def question_line(round_: dict | None, horizon_hours: Any = None) -> str | None:
    """The question a round asks, in words, or None for a kind this code does
    not know. Every category has one, so a player meeting a mover for the
    first time is told what the first button means before it is offered."""
    r = round_ or {}
    h = _num(horizon_hours)
    hh = f"{int(h)}h" if h is not None else "the horizon"
    kind = kind_of(r)
    if kind == "direction":
        return f"Higher or lower in {hh}? The Claw's own call is shown once you commit."
    if kind == "backfade":
        direction = r.get("agent_direction")
        word = _PICK_WORD.get(str(direction), "—") if direction is not None else "—"
        return f"The Claw called {word} on {_base(r.get('symbol'))}. Back it, or fade it?"
    if kind == "range":
        return f"Does {_base(r.get('symbol'))} move more than {_band(r)} either way in {hh}?"
    if kind == "mover":
        return (f"{_base(r.get('symbol'))} or {_base(r.get('symbol_b'))} — "
                f"which moves more in {hh}, either way?")
    if kind == "sports":
        comp = str(((r.get("match") or {}).get("competition")) or "—")
        return (f"{_team(r, 'home')} vs {_team(r, 'away')} — who wins? "
                f"{comp}, kick-off {_kickoff_utc(r)}")
    return None


def pick_word(pick: Any, round_: dict | None = None) -> str:
    """The word a call is reported in. A mover's two sides are its symbols."""
    r = round_ or {}
    p = str(pick or "").lower()
    if p in _PICK_WORD:
        return _PICK_WORD[p]
    if p == "back":
        return "BACK the Claw"
    if p == "fade":
        return "FADE the Claw"
    if p == "break":
        return f"BREAKS {_band(r)}"
    if p == "hold":
        return f"HOLDS {_band(r)}"
    if p == "first":
        return f"{_base(r.get('symbol'))} moves more"
    if p == "second":
        return f"{_base(r.get('symbol_b'))} moves more"
    if p == "home":
        return _team(r, "home")
    if p == "away":
        return _team(r, "away")
    if p == "draw":
        return "DRAW"
    return str(pick or "?").upper()


def round_options(round_: dict | None) -> tuple[str, ...]:
    """The picks a round takes: the card's own list when it carries one, else
    the table's for its kind. A word the table does not know is dropped, so a
    newer server cannot put a button here whose tap the parser would refuse."""
    r = round_ or {}
    kind = kind_of(r)
    if kind is None:
        return ()
    raw = r.get("options")
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(p).lower() for p in raw if str(p).lower() in PICKS)
    return KIND_PICKS[kind]


def button_rows(rounds: Any) -> list[list[tuple[str, str]]]:
    """One row of (label, callback_data) per uncalled, callable round.

    The first button names the market so a row reads on its own; the labels
    are short because Telegram fits three to a row on a phone. The callback
    data is `duel:<round_id>:<pick>` — ids and enum tokens, never prose (#999).
    """
    rows: list[list[tuple[str, str]]] = []
    for r in rounds or []:
        if not isinstance(r, dict) or r.get("my_call") or not r.get("callable"):
            continue
        rid = r.get("id")
        if rid is None:
            continue
        options = round_options(r)
        if not options:
            continue
        kind = kind_of(r)
        row: list[tuple[str, str]] = []
        for i, pick in enumerate(options):
            if kind in ("mover", "sports"):
                label = "PASS" if pick == "pass" else pick_word(pick, r)
            elif pick == "pass":
                label = "PASS"
            else:
                word = pick_word(pick, r)
                if pick in ("back", "fade"):
                    word = word.split(" ")[0]          # BACK / FADE
                label = f"{_base(r.get('symbol'))} {word}" if i == 0 else word
            row.append((label, pick_callback_data(rid, pick)))
        rows.append(row)
    return rows


def pick_callback_data(round_id: Any, pick: str) -> str:
    """The token a duel button carries: ``duel:<round_id>:<pick>``.

    Encoding and decoding live together, and both live here rather than in the
    handler, so a test can prove that what the button emits is what the callback
    reads. #999 shipped a card whose button carried prose where the lookup
    expected a symbol: the code was present, correct-looking, and reached zero
    times in production. A round-trip assertion is the only thing that catches
    that, and it needs both halves in one place.
    """
    return f"{CALLBACK_PREFIX}:{int(round_id)}:{str(pick).lower()}"


def parse_callback_data(data: str) -> tuple[int, str] | None:
    """(round_id, pick), or None if this is not a well-formed duel token.

    Refuses rather than guesses: an unparseable token must not become a call
    on some default round in some default direction.
    """
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    if not parts[1].isdigit():
        return None
    pick = parts[2].lower()
    if pick not in PICKS:
        return None
    return int(parts[1]), pick


def _num(value: Any) -> float | None:
    """A finite number, or None. Absence never becomes zero here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def accuracy_line(accuracy: dict | None) -> str:
    """The record headline.

    `pct is None` means nothing has settled. It renders as a dash and says so,
    because a 0% here would report a measured failure where there is only an
    absence — and it would sit next to real percentages and read like one.
    """
    acc = accuracy or {}
    pct = _num(acc.get("pct"))
    # Counts we could not read are OMITTED from the sentence rather than
    # printed as zero. `or 0` would be fine for the two that only gate an
    # optional clause, but the denominator is a claim of its own: "of 0 settled
    # calls" beside a real percentage is a statement, and a wrong one.
    scored = _num(acc.get("scored"))
    unresolved = _num(acc.get("unresolved"))
    if pct is None:
        nothing_settled = unresolved and not scored
        return "Accuracy: — (no calls resolved yet)" + (
            " · nothing has settled yet" if nothing_settled else "")
    line = f"Accuracy: {pct:.0f}%"
    if scored is not None:
        line += f" of {int(scored)} settled call{'s' if int(scored) != 1 else ''}"
    if unresolved:
        line += f" · {int(unresolved)} unresolved"
    return line


def marks_line(marks: dict | None) -> str:
    """Marks, with what they could not include reported beside them.

    A partial total printed as a whole one is the shape this repo has been
    bitten by most; "7 marks" alone is a sum over a set that quietly excluded
    every call still running.
    """
    m = marks or {}
    total = _num(m.get("marks"))
    pending = int(m.get("pending") or 0)
    beat = int(m.get("beat_agent") or 0)
    head = "Marks: —" if total is None else f"Marks: {int(total)}"
    bits = []
    if beat:
        bits.append(f"beat the Claw {beat}×")
    if pending:
        bits.append(f"{pending} still running")
    return head + (" (" + ", ".join(bits) + ")" if bits else "")


def agent_line(round_: dict) -> str | None:
    """What the agent said — or an honest statement that it has not been shown.

    "Hidden" and "passed" are different sentences. The API omits the field
    rather than nulling it precisely so these two can be told apart; collapsing
    them here would throw that away. Only a classic round has this line: on a
    back/fade round the stance is the question, and a range or a mover round
    never had one, so saying "passed" there would describe a choice that was
    never on offer.
    """
    if kind_of(round_) != "direction":
        return None
    if "agent_direction" not in round_:
        return "The Claw: hidden until you call"
    direction = round_.get("agent_direction")
    if direction is None:
        return "The Claw: passed on this one"
    return f"The Claw: {_PICK_WORD.get(str(direction), str(direction))}"


def call_line(round_: dict) -> str | None:
    """How the caller's own call on this round is doing, or None if uncalled."""
    call = round_.get("my_call")
    if not call:
        return None
    result = round_.get("my_result") or {}
    state = result.get("state")
    if call.get("pending"):
        state = "pending"
    elif call.get("unresolved") or state == "unresolved":
        state = "unresolved"
    glyph = _STATE_GLYPH.get(state, "⏳")
    word = pick_word(call.get("pick"), round_)
    text = {
        "correct": "correct",
        "wrong": "wrong",
        "push": "no result",
        "unresolved": "unresolved — no price",
        "pending": "running",
    }.get(state, "running")
    if state == "correct" and result.get("beat_agent"):
        text = "correct — you beat the Claw"
    move = _num(call.get("move_pct"))
    # A move is only printed when a price actually backed it.
    tail = f" ({move:+.2f}%)" if move is not None and state in ("correct", "wrong", "push") else ""
    score = call.get("score") or {}
    home, away = _num(score.get("home")), _num(score.get("away"))
    if home is not None and away is not None and state in ("correct", "wrong", "push"):
        tail = f" (final score {int(home)}–{int(away)})"
    return f"{glyph} You called {word} — {text}{tail}"


def render_card(data: dict | None) -> str:
    """The whole /duel message.

    A failed read is stated, never rendered as an empty card: "no rounds today"
    is a confident claim about the game, and an unreadable feed does not
    support it.
    """
    if not data:
        return (
            "⚔️ *Daily Duel*\n\n"
            "The card could not be read just now — your record is unchanged.\n"
            "Try again in a moment."
        )
    rounds = data.get("rounds") or []
    lines = ["⚔️ *Daily Duel* — " + str(data.get("day") or "today")]
    horizon = _num(data.get("horizon_hours"))
    if horizon is not None:
        lines.append(f"_A market call runs {int(horizon)}h from the moment you make it; "
                     "a match settles on its final score._")
    lines.append("")

    if not rounds:
        lines.append("No rounds are on today's card.")
    for r in rounds:
        kind = kind_of(r)
        name = _KIND_NAME.get(kind or "", "Unknown round type")
        lines.append(f"*{round_title(r)}* · {name}")
        question = question_line(r, horizon)
        if question:
            lines.append("  " + question)
        else:
            lines.append("  This round asks a question this bot cannot show. Update the bot.")
        agent = agent_line(r)
        if agent:
            lines.append("  " + agent)
        mine = call_line(r)
        if mine:
            lines.append("  " + mine)
        elif r.get("callable") and question:
            lines.append("  _Not called yet — tap below._")
        elif r.get("callable"):
            pass
        else:
            lines.append("  _This round has closed._")
        lines.append("")

    lines.append(accuracy_line(data.get("accuracy")))
    lines.append(marks_line(data.get("marks")))
    streak = (data.get("streak") or {}).get("current")
    if streak is not None:
        lines.append(f"Streak: 🔥 {int(streak)} day{'s' if int(streak) != 1 else ''}")
    return "\n".join(lines).rstrip()


def render_pick_result(result: dict | None, status: int | None = None) -> str:
    """The reply after a call is recorded — or refused.

    Every refusal is a different sentence because they mean different things to
    a player, and none of them may read as success.
    """
    if status == 409:
        return (result or {}).get("error") or "You have already called this round."
    if status == 503:
        return (
            (result or {}).get("error")
            or "The price feed is unreadable right now — your call was not recorded."
        )
    if not result or not result.get("ok"):
        return (result or {}).get("error") or "Your call could not be recorded — try again."

    word = pick_word(result.get("pick"), result)
    lines = [f"✅ Call recorded: *{word}* on *{round_title(result)}*"]
    entry = _num(result.get("entry_price"))
    if entry is not None:
        lines.append(f"Entry: {entry:g} (snapped for you, just now)")
    if kind_of(result) == "sports":
        lines.append(f"Kick-off {_kickoff_utc(result)} — settled on the final score.")
    entry_b = _num(result.get("entry_price_b"))
    if entry_b is not None:
        lines.append(f"{_base(result.get('symbol_b'))} entry: {entry_b:g} (snapped in the same read)")
    agent = result.get("agent_direction")
    # The stance is reported where it was something to reveal: on a classic
    # round. On a back/fade round it was the question, already on the card.
    if "agent_direction" in result and kind_of(result) == "direction":
        lines.append(
            "The Claw passed on this one."
            if agent is None
            else f"The Claw called {_PICK_WORD.get(str(agent), str(agent))}."
        )
    if result.get("resolves_at"):
        # A moment a person can read, not the wire's ISO string.
        when = _utc(result["resolves_at"], "%Y-%m-%d %H:%M UTC")
        lines.append(f"Settles: {when}")
    return "\n".join(lines)
