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
PICKS = ("long", "short", "pass")


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


def agent_line(round_: dict) -> str:
    """What the agent said — or an honest statement that it has not been shown.

    "Hidden" and "passed" are different sentences. The API omits the field
    rather than nulling it precisely so these two can be told apart; collapsing
    them here would throw that away.
    """
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
    word = _PICK_WORD.get(str(call.get("pick")), str(call.get("pick") or "?").upper())
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
        lines.append(f"_Each call runs {int(horizon)}h from the moment you make it._")
    lines.append("")

    if not rounds:
        lines.append("No rounds are open right now.")
    for r in rounds:
        lines.append(f"*{r.get('symbol', '?')}*")
        lines.append("  " + agent_line(r))
        mine = call_line(r)
        if mine:
            lines.append("  " + mine)
        elif r.get("callable"):
            lines.append("  _Not called yet — tap below._")
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

    word = _PICK_WORD.get(str(result.get("pick")), str(result.get("pick") or "?").upper())
    lines = [f"✅ Call recorded: *{word}* on *{result.get('symbol', '?')}*"]
    entry = _num(result.get("entry_price"))
    if entry is not None:
        lines.append(f"Entry: {entry:g} (snapped for you, just now)")
    agent = result.get("agent_direction")
    if "agent_direction" in result:
        lines.append(
            "The Claw passed on this one."
            if agent is None
            else f"The Claw called {_PICK_WORD.get(str(agent), str(agent))}."
        )
    if result.get("resolves_at"):
        lines.append(f"Settles: {result['resolves_at']}")
    return "\n".join(lines)
