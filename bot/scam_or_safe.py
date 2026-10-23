"""Scam or Safe? Daily, as the anonymous Daily Duel bot says it.

The seal is the website gate (``sos-offer`` / ``sos-seal`` on
``app/lib/daily_duel_gate.js``), which calls ``sealDuelPick``. This module
picks no puzzle and hashes nothing. It only decides whether the flag is on
and how a reply is worded.

``SCAM_OR_SAFE_V0`` unset is off. The bot then does not register /scam, and
/duel is the duel it already was.

A field that still contains a full account or a URL is withheld. The catalog
audit is what keeps those out; this is the last check before a person sees
the text.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

_ON = frozenset({"1", "true", "on", "yes"})
_FULL_ADDR = re.compile(r"0x[a-fA-F0-9]{40}")
_URL = re.compile(r"https?://|www\.|javascript:", re.I)
_CB = re.compile(r"^sos:(ask|seal|back)(?::(safe|risky))?(?::(sos-[a-z]{2}))?$")

NOT_OPEN = "Scam or Safe is not open. Nothing was sealed."
UNREAD = "Today's request could not be read, so nothing was sealed."
AI_LABEL = "AI-written explanation"
AI_NOTE = "Education and practice, not advice."

SCAM_WELCOME = (
    "Scam or Safe is open. Tap /scam for today's decoded request. "
    "Call it safe or risky. Your call is sealed before the explanation, and nothing is signed."
)
SCAM_HELP = (
    "/scam — one decoded request each UTC day. Call it safe or risky. "
    "The call is sealed before the explanation. Nothing on that screen can be signed."
)


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    e = env if env is not None else os.environ
    return str(e.get("SCAM_OR_SAFE_V0", "")).strip().lower() in _ON


def parse_callback(data: str) -> Optional[tuple[str, Optional[str], Optional[str]]]:
    m = _CB.fullmatch(str(data or ""))
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def show_field(value: object) -> str:
    text = str(value if value is not None else "")
    if _FULL_ADDR.search(text) or _URL.search(text):
        return "[withheld]"
    return text


def _fields(puzzle: object) -> list[str]:
    if not isinstance(puzzle, Mapping):
        return []
    fields = puzzle.get("fields")
    if not isinstance(fields, list):
        return []
    lines = []
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        lines.append(f"{field.get('k')}: {show_field(field.get('v'))}")
    return lines


def offer_text(offer: object) -> str:
    if not isinstance(offer, Mapping) or not offer.get("puzzle"):
        return UNREAD
    puzzle = offer["puzzle"]
    chain = puzzle.get("chain_label") if isinstance(puzzle, Mapping) else None
    lines = [
        f"Today ({offer.get('day')}, UTC): one decoded request on {chain}.",
        *_fields(puzzle),
        "Call it safe or risky. You confirm before it is sealed. "
        "Nothing here can be signed, and a masked account cannot be copied out.",
        f"Closes at {offer.get('cutoff_at')} (the UTC day ends then).",
    ]
    return "\n".join(lines)


def confirm_text(pick: str, offer: Mapping[str, object]) -> str:
    puzzle = offer.get("puzzle") if isinstance(offer, Mapping) else None
    pid = puzzle.get("id") if isinstance(puzzle, Mapping) else None
    return "\n".join([
        f"Seal {pick} on {pid} for {offer.get('day')}?",
        "The explanation is shown after the seal, and the call cannot be changed.",
        "Nothing is signed.",
    ])


def _right_line(right: object) -> str:
    if not isinstance(right, Mapping):
        return "The share of right calls could not be read."
    pct = right.get("pct")
    total = right.get("total")
    if type(pct) not in (int, float) or type(total) is not int or isinstance(pct, bool):
        return "The share of right calls could not be read."
    line = f"{pct}% of {total} players called it right."
    unread = right.get("unreadable")
    if type(unread) is int and unread > 0:
        line += f" {unread} sealed calls could not be read and are not in that percent."
    return line


def _streak_line(streak: object) -> str:
    if not isinstance(streak, Mapping) or type(streak.get("current")) is not int:
        return "Your streak could not be read."
    current = streak["current"]
    unit = "day" if current == 1 else "days"
    best = streak.get("best")
    line = f"Streak: {current} {unit}"
    if type(best) is int:
        line += f" (best {best})"
    return line + ". Consecutive UTC days you called it right."


def _rank_line(rank: object) -> str:
    if not isinstance(rank, Mapping):
        return "Your place on the streak board could not be read."
    place = rank.get("place")
    players = rank.get("players")
    if type(place) is not int or type(players) is not int:
        return "Your place on the streak board could not be read."
    return f"Streak board: place {place} of {players}."


def reveal_text(body: Mapping[str, object]) -> str:
    pick = body.get("pick")
    verdict = body.get("verdict")
    lines = ["Sealed. This cannot be changed."]
    if pick in ("safe", "risky"):
        lines.append(f"You called this request {pick}.")
    if verdict in ("safe", "risky"):
        lines.append(f"Verdict: {verdict}.")
    else:
        lines.append("The verdict could not be read, so it is not shown.")
    correct = body.get("correct")
    if correct is True:
        lines.append("That matches the verdict.")
    elif correct is False:
        lines.append("That does not match the verdict.")
    else:
        lines.append("Whether it matched could not be read.")
    label = body.get("ai_label")
    if label:
        lines.append(str(label))
        lines.append(str(body.get("explanation") or ""))
        if body.get("ai_note"):
            lines.append(str(body["ai_note"]))
    else:
        lines.append("The explanation is withheld until it carries its AI label.")
    lines.append(_right_line(body.get("right")))
    lines.append(_streak_line(body.get("streak")))
    lines.append(_rank_line(body.get("rank")))
    seal = body.get("seal")
    if seal:
        lines.append(f"Seal: {seal}")
    return "\n".join(lines)


def refused_text(body: object) -> str:
    if not isinstance(body, Mapping):
        return "That call was not sealed. Nothing was recorded."
    err = body.get("error")
    if err == "off":
        return NOT_OPEN
    if err == "closed":
        return f"That day has closed (cutoff {body.get('cutoff_at')}). Nothing was sealed."
    if err == "moved":
        return "The day's request changed before the seal. Nothing was sealed. /scam shows today's."
    if err == "unreadable":
        return UNREAD
    return "That call was not sealed. Nothing was recorded."
