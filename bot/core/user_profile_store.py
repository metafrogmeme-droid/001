"""Per-user agent profile — risk appetite and watchlist, readable by the BOT.

WHY THIS EXISTS: THE AGENT FORGOT YOU DEPENDING ON WHICH DOOR YOU CAME THROUGH.

The web already has this. `user_profiles` (risk_pref, watchlist) is written by
`PUT /api/profile`, and `app/routes/chat.js` attaches it to the chat request, so
`user_gateway.handle_chat` can hand `profile_note` to the LLM and the agent
tailors its answer.

Every Telegram path takes the default `profile_note: str = ""`. Traced
2026-08-21: the ONLY callers passing it are three lines in `user_gateway.py`.
So a user who sets "conservative" and a watchlist on the web gets an agent that
knows them there, and the same person messaging the same agent on Telegram is a
stranger. Not a bug in either surface — the profile simply never existed
anywhere the bot could read it, because it lived in a web request body.

This is the bot-side copy the Telegram path can read. The web remains the SOURCE
OF TRUTH and pushes changes down; this store is a cache, and it says so where it
matters (see `is_readable`).

ONE VALIDATOR, NOT TWO. `normalize()` is the single definition of what a valid
profile is, and `user_gateway.build_profile_note` calls it rather than keeping
its own copy of the whitelist. Two surfaces validating the same payload with two
copies of the rule is how they drift, and this content reaches an LLM SYSTEM
PROMPT — the place where free-form text must never arrive. risk_pref is one of
three known words; watchlist entries are bare uppercase tickers; everything else
is dropped rather than escaped.

FAIL-SAFE, AND HONEST ABOUT IT. A read error returns None, and None means "no
context to add" — the caller then omits the profile line entirely. It must never
render as "this user has no watchlist", because an unreadable file and a user
who saved nothing are different facts and only one of them is a statement about
the user. Omission is not a claim; a confident negative is.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import env_state_path

log = logging.getLogger(__name__)
_LOCK = threading.Lock()

#: The three words the agent understands. Anything else is dropped.
RISK_PREFS = frozenset({"conservative", "balanced", "aggressive"})
#: Cap on stored symbols. The note lands in a system prompt; an unbounded list
#: is a prompt-size problem before it is anything else.
WATCHLIST_MAX = 20
#: A bare ticker. Deliberately strict — no separators, no punctuation, nothing
#: that could carry an instruction into the prompt.
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def _path():
    # `env_state_path`, not a bare relative join: durable state must not depend
    # on the process's cwd. The older user_*_store modules predate that helper
    # and are left alone; new state uses it.
    return env_state_path("RUNECLAW_USER_PROFILE_FILE", "data/user_profile.json")


def normalize(profile) -> Optional[dict]:
    """The one definition of a valid profile. Returns None if nothing survives.

    None is the honest answer for "no usable profile": it is not an empty
    profile, and callers must not render it as one.
    """
    if not isinstance(profile, dict):
        return None
    out = {}
    risk = str(profile.get("risk_pref") or "").strip().lower()
    if risk in RISK_PREFS:
        out["risk_pref"] = risk
    wl = profile.get("watchlist")
    if isinstance(wl, (list, tuple)):
        syms = []
        for raw in list(wl)[:WATCHLIST_MAX]:
            s = str(raw or "").strip().upper()
            if SYMBOL_RE.match(s) and s not in syms:
                syms.append(s)
        if syms:
            out["watchlist"] = syms
    return out or None


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("user_profile read failed: %s", exc)
        return {}


def get(user_id) -> Optional[dict]:
    """A user's stored profile, or None.

    None covers three different situations on purpose — no file, no entry,
    unreadable file — because the caller's action is the same for all three:
    add no profile context. What it must NOT do is invent an empty profile and
    tell the model the user has no preferences.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return None
    return normalize(_load().get(uid))


def set_profile(user_id, profile) -> Optional[dict]:
    """Persist a profile. Returns what was stored, or None. Never raises."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    clean = normalize(profile)
    with _LOCK:
        d = _load()
        if clean is None:
            # An empty profile is a DELETE, not a stored blank. Keeping `{}`
            # would make "saved nothing" indistinguishable from "never saved",
            # and the next reader would have to guess which.
            if uid not in d:
                return None
            del d[uid]
        else:
            d[uid] = clean
        try:
            atomic_write_json(_path(), d, indent=None)
        except Exception as exc:
            log.warning("user_profile write failed: %s", exc)
            return None
    return clean


def clear(user_id) -> bool:
    """Forget a user's profile. Never raises."""
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _LOCK:
        d = _load()
        if uid not in d:
            return False
        del d[uid]
        try:
            atomic_write_json(_path(), d, indent=None)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("user_profile clear failed: %s", exc)
            return False
    return True


def note_for(user_id) -> str:
    """The context line for a user, or "" when there is nothing to say.

    "" is not a claim. The caller appends nothing, and the model is told
    nothing about this user's preferences — which is exactly right when we do
    not know them.
    """
    return render_note(get(user_id))


def render_note(profile) -> str:
    """Format an ALREADY-NORMALIZED profile (or None) as a prompt line.

    Split from `note_for` so the web path — which receives a profile in the
    request body rather than from this store — renders through the identical
    code. One formatter, so the two surfaces cannot describe the same user
    differently.
    """
    p = normalize(profile)
    if not p:
        return ""
    parts = []
    if p.get("risk_pref"):
        parts.append(f"Their self-declared risk preference is {p['risk_pref']}.")
    if p.get("watchlist"):
        parts.append("They are watching: " + ", ".join(p["watchlist"]) + ".")
    return " ".join(parts)
