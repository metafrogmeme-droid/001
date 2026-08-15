"""The /users table, as something that can be called and asserted on.

It was eight lines inline in a 12,000-line handler, and it shortened the
operator's own identifiers without saying so::

    tid = u["telegram_id"][-8:]   # Last 8 digits

WHY A DISPLAY-ONLY TRUNCATION IS NOT DISPLAY-ONLY

`/users` is the command an operator reads before running `/approve <id>`,
`/revoke <id>` or the admit button — all three of which take the id it just
printed. `6307156912` rendered as `07156912` is not a key in the store, so the
next command answers "not found".

And the shortening is INVISIBLE. Telegram ids of 8 and 9 digits exist, so a row
reading `71461243` might be a complete id or the tail of a longer one, and the
card offers no way to tell. On the roster this was written against, five rows
gave themselves away only by starting with `0` — which no real Telegram id
does. The rest were ambiguous by construction.

That is the same shape as a partial sum printed as a total: a value rendered in
the format of a complete one. So nothing here is shortened silently. When a
value genuinely will not fit, it is shortened with a leading `…` and reads as
shortened.

WHAT ELSE WAS IN THOSE EIGHT LINES

Asking which other claim the card makes found two more of the same:

  * `u.get("tier", "basic")` — an absent tier rendered as the real tier
    `basic`. A record with no tier is not a basic-tier record.
  * `"✓" if u.get("authorized") else "✗"` — absent and explicitly False both
    rendered ✗. They are different states and `register()` writes the key for
    everyone, so a record without it is odd enough to be worth seeing.

`role` was already right (`u.get("role", "?")`), which is the shape the other
two should have had.
"""
from __future__ import annotations

import unicodedata
from typing import Callable, Optional, Sequence

#: Widths at which a value stops being shown in full. Generous rather than
#: tight: the block scrolls horizontally in Telegram, so an over-wide table
#: costs a swipe, while an over-eager clip costs the operator the id they came
#: for. `web:<n>` ids run to 24 characters at the limit of the gateway's
#: pattern; a Telegram id is 10 and heading for more.
MAX_ID = 20
MAX_NAME = 14

#: Trailing joiners and modifiers. Slicing a string of code points can cut an
#: emoji sequence mid-cluster and leave one of these dangling, which renders as
#: a stray box next to a name — the roster is full of emoji names.
_DANGLING = "‍️︎"

ELLIPSIS = "…"


def cells(value: str) -> int:
    """Width in monospace CELLS, which is not `len()`.

    Half this roster has emoji in the name and `len()` counts code points: a
    heart is one to Python and two to every monospace renderer, so padding by
    `len()` pushed those rows a column right and the table has always been
    ragged for exactly the users who decorate their names.

    Combining marks and joiners occupy no cell of their own — a variation
    selector counted as 1 is the same error in the other direction.
    """
    n = 0
    for ch in value:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _pad(value: str, width: int) -> str:
    """Left-align to `width` cells. `f"{v:<{w}}"` cannot do this — format specs
    pad by len(), which is the bug this exists to avoid."""
    return value + " " * max(0, width - cells(value))


def _clip(value: str, width: int) -> str:
    """Shorten to `width` and SAY SO, keeping the tail.

    The tail rather than the head because a shortened id is most recognisable
    by its last digits — but the marker is what makes this honest, not the
    choice of end. Without it the result is a different id, silently.
    """
    if len(value) <= width:
        return value
    kept = value[-(width - 1):].lstrip(_DANGLING)
    return ELLIPSIS + kept


def _name(value: Optional[str]) -> str:
    if not value:
        return "?"
    return _clip(value, MAX_NAME).rstrip(_DANGLING)


def render_table(users: Sequence[dict],
                 can_trade_live: Callable[[str], bool],
                 limit: int = 15) -> list:
    """The `<pre>` block of /users, as a list of lines.

    `can_trade_live` is passed in rather than reached for so this stays pure
    and a test can plant the answer. `limit` bounds the rows; the CALLER says
    how many were omitted — `users_more` already reads "Showing last 15 of N",
    which is why the cap is not this module's problem to announce.
    """
    shown = list(users)[-limit:] if limit else list(users)

    rows = []
    for u in shown:
        raw_id = str(u.get("telegram_id") or "?")
        # `is None`, not falsiness, and not `is True` either. Absence is the
        # thing being separated out; once the key is present, `1` from a
        # hand-edited or older file means authorized as surely as `true` does,
        # and an identity check would report that user as unknown.
        authorized = u.get("authorized")
        rows.append({
            "id": _clip(raw_id, MAX_ID),
            "name": _name(u.get("name")),
            # Absent is not a value. `role` was always written this way; the
            # other two have been brought into line with it.
            "role": u.get("role") or "?",
            "tier": u.get("tier") or "?",
            "auth": "?" if authorized is None else ("✓" if authorized else "✗"),
            "mode": "LIVE" if can_trade_live(raw_id) else "paper",
        })

    # Widths from the data, so a full id is never the reason to shorten one —
    # and measured in cells, so an emoji name does not shift its own row.
    def w(key: str, header: str) -> int:
        return max([cells(r[key]) for r in rows] + [cells(header)]) + 1

    iw, nw = w("id", "ID"), w("name", "NAME")
    rw, tw = w("role", "ROLE"), w("tier", "TIER")

    dash = "─"
    lines = ["<pre>",
             " " + _pad("ID", iw) + _pad("NAME", nw) + _pad("ROLE", rw + 1)
             + _pad("TIER", tw) + "MODE",
             " " + dash * (iw + nw + rw + tw + 5)]
    for r in rows:
        lines.append(" " + _pad(r["id"], iw) + _pad(r["name"], nw) + r["auth"]
                     + _pad(r["role"], rw) + _pad(r["tier"], tw) + r["mode"])
    lines.append("</pre>")
    return lines
