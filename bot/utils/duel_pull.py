"""
The Daily Duel over the shared-secret channel.

The duel is scored in the web app (app/lib/duel.js and duel_service.js) and
this only reads and posts. There is no second implementation of the rules in
Python on purpose: a copy would agree with the original exactly until one of
them changed, and the two surfaces would then disagree about a player's record
with nothing to say which was right.

Unlike the other pullers here, this one returns the HTTP STATUS alongside the
body. The duel's refusals are ordinary outcomes a player must be told apart —
a round already called (409) is not the same as a dead price feed (503), and
neither is the same as a channel that is simply unconfigured. Collapsing them
to ``None`` would make every one of them read as "something went wrong".
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from bot.utils.credential_pull import SYNC_SECRET, WEBSITE_URL

log = logging.getLogger(__name__)

TIMEOUT_SEC = 15


def is_configured() -> bool:
    return bool(SYNC_SECRET and WEBSITE_URL)


def _call(path: str, data: dict | None = None) -> tuple[int | None, dict | None]:
    """(status, body). status None = the request never completed."""
    url = f"{WEBSITE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "RUNECLAW-Bot/1.0",
        "X-Bot-Secret": SYNC_SECRET,
    }
    body = None
    method = "GET"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # A 4xx here carries the reason the player needs, so the body is read
        # rather than discarded with the status.
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None
    except Exception as exc:
        log.error("duel channel error on %s: %s", path, exc)
        return None, None


def fetch_card(telegram_id: str) -> tuple[int | None, dict | None]:
    """Today's card plus the caller's record."""
    if not is_configured() or not telegram_id:
        return None, None
    tg = urllib.parse.quote(str(telegram_id)[:32])
    return _call(f"/api/bot/sync/duel?telegram_id={tg}")


def place_pick(telegram_id: str, round_id: int, pick: str) -> tuple[int | None, dict | None]:
    """Record one call. The web app decides whether it may be recorded."""
    if not is_configured() or not telegram_id:
        return None, None
    return _call(
        "/api/bot/sync/duel/pick",
        {"telegram_id": str(telegram_id)[:32], "round_id": int(round_id), "pick": str(pick)},
    )
