"""The Paper Arena's public boards, read from the website.

Unlike the verifiable leaderboard — which lives on this process's own disk and
needs no network at all — the Arena lives entirely in the web app's database
(`arena_trades`, `arena_seasons`, `arena_accounts`). There is no second copy
here and there must not be one: a Python reimplementation of the season rules
would agree with `app/lib/arena_seasons.js` exactly until one of them changed,
and the two surfaces would then disagree about who won with nothing to say
which was right. Same reasoning `duel_pull` records for the duel.

So this is a reader, and the only thing it has to get right is the difference
between **nothing to report** and **could not be read**.

``None`` means unreadable, always. It is never an empty board, an empty tape or
a zero count — a dead website rendering as "0 traders, no closes" would be a
confident report about a floor this process never reached. Every caller must
treat ``None`` as "say so", and the renderer in formatters/arena_cards does.

The two boards are fetched INDEPENDENTLY, and the card omits whichever failed
rather than dropping both: CLAUDE.md's table puts a composite view on the
*omit* row, and a season standing that loads fine should not be thrown away
because the tape query timed out.

NO SECRET IS SENT. These endpoints are public by design (`/api/arena/season`,
`/api/arena/tape` carry opt-in handles and percent returns only — never a
dollar figure, not even a virtual one), and the other pullers' ``X-Bot-Secret``
would put the shared sync credential on a path that does not require it and
does not check it.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

from bot.utils.site_url import site_url

log = logging.getLogger(__name__)

#: Deliberately shorter than the sync pullers' 15s. This one runs inside a
#: /arena keystroke, and a member staring at a silent chat learns less from a
#: card that arrives in 15 seconds than from one that admits at 8 that the
#: site is unreachable.
TIMEOUT_SEC = 8


def _get_json(path: str) -> Optional[dict]:
    """The endpoint's JSON body, or None if it could not be read.

    None on transport failure, on any non-2xx, and on a body that is not a JSON
    object — all four are "this process did not learn anything", and a caller
    that cannot tell them apart still must not print a number.
    """
    url = f"{site_url()}{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "RUNECLAW-Bot/1.0",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            if not 200 <= int(getattr(resp, "status", 0) or 0) < 300:
                return None
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        log.warning("arena read HTTP %s on %s", exc.code, path)
        return None
    except Exception as exc:
        log.warning("arena read failed on %s: %s", path, exc)
        return None
    return body if isinstance(body, dict) else None


def fetch_season() -> Optional[dict]:
    """The current season and its standings, or None if unreadable.

    A readable answer of ``{"season": None}`` is NOT None: it means the site
    was reached and reported that no season is running, which is a fact worth
    telling a member. Collapsing the two would let an outage read as "no season
    on right now".
    """
    return _get_json("/api/arena/season")


def fetch_tape() -> Optional[dict]:
    """The live tape and floor pulse, or None if unreadable."""
    return _get_json("/api/arena/tape")


def fetch_arena() -> "tuple[Optional[dict], Optional[dict]]":
    """``(season, tape)`` — each independently None when that read failed."""
    return fetch_season(), fetch_tape()


def is_virtual(payload: Any) -> bool:
    """Whether the payload ASSERTS that these funds are virtual.

    The card may only promise "virtual funds" when the source said so. Both
    arena endpoints set ``virtual: true`` deliberately, and defaulting to True
    here would mean a changed or spoofed payload could get a real-money board
    labelled as practice — the one mislabel on this surface that could cost
    somebody money.
    """
    return isinstance(payload, dict) and payload.get("virtual") is True
