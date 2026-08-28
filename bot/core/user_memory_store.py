"""What the agent has actually done for THIS person — the recall half of memory.

`user_profile_store` holds what a user DECLARED: a risk preference they picked
and a watchlist they typed. This holds what was OBSERVED: the assets they have
actually had the agent look at. They are different claims and the renderer
keeps them apart, because "they say they watch SOL" and "they have asked about
SOL eleven times" are not interchangeable and only one of them is evidence.

The roadmap row this closes reads "remembers risk appetite, watchlist, past
decisions". The first two shipped; the third was the gap — every conversation
started from zero, so the agent could be told about a position it had itself
recommended an hour earlier and have no idea.

RECORDED FROM RESOLVED INTENTS, NOT FROM PROSE
----------------------------------------------
`observe()` is called with the skill the router DISPATCHED and the kwargs it
resolved, so the symbol is one the bot itself decided on. Scraping tickers out
of the user's sentence would be a guess, and a guess written into a store that
feeds a system prompt is a guess the model will then state as fact.

Nothing free-form is stored. A symbol survives only if it matches SYMBOL_RE
after normalization, and the skill name only if it is a bare identifier. That
is the same rule `user_profile_store` spells out, for the same reason: this
content lands in an LLM SYSTEM PROMPT, which is the one place user-controlled
text must never arrive.

ONE CALLER PER SURFACE, AND THAT IS THE DANGER
----------------------------------------------
Telegram dispatches free-text intents in `telegram_handler`, and the web does
it again in `user_gateway`. Two dispatch sites for one behaviour is exactly how
the auth classifier came to be fixed on the operator path and left broken on
the user path one function below it. `observe()` is called from both, and
`tests/test_user_memory_store.py` asserts both call sites exist — a memory that
only remembers the surface somebody thought of is worse than none, because it
makes the agent inconsistent about the same person depending on the door.

UNREADABLE IS NOT "NEW HERE"
----------------------------
Every read failure resolves to None, and None renders as "" — the caller
appends nothing and the model is told nothing about this person's history.
It must never render as "they have not asked about anything before", which is
a claim about the user manufactured from a failed file read.

BOUNDED, BECAUSE THIS IS PROMPT BUDGET
--------------------------------------
`TOPICS_MAX` symbols per user, evicted least-recently-seen first, and the
rendered line names at most `NOTE_TOPICS`. An unbounded memory is a prompt-size
problem before it is anything else, and a store that grows per message is a
disk problem after that.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import env_state_path

log = logging.getLogger(__name__)
_LOCK = threading.Lock()

#: Symbols kept per user. LRU by last-seen; see `_trim`.
TOPICS_MAX = 12
#: Symbols named in the prompt line. Fewer than we store on purpose — the store
#: is evidence, the note is a summary, and a twelve-ticker sentence is noise.
NOTE_TOPICS = 5
#: Same shape as user_profile_store.SYMBOL_RE. A bare ticker, nothing that
#: could carry an instruction into a system prompt.
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")
#: A skill name is an identifier we dispatched on, never user text.
SKILL_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: kwargs keys that carry the asset the router resolved.
_SYMBOL_KEYS = ("symbol", "asset", "pair", "ticker")


def _path():
    return env_state_path("RUNECLAW_USER_MEMORY_FILE", "data/user_memory.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def base_symbol(raw) -> str:
    """`BTC/USDT:USDT` -> `BTC`. Returns "" for anything unrecognisable.

    "" is not a symbol and callers must not store it. Deliberately strict:
    the quote-currency split is the only transformation, so a value the router
    handed us in an unexpected shape is DROPPED rather than mangled into
    something that looks like a ticker.
    """
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    for sep in (":", "/", "-", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    return s if SYMBOL_RE.match(s) else ""


def normalize(mem) -> Optional[dict]:
    """The one definition of a valid memory record. None if nothing survives.

    None is the honest answer for "no usable memory" — it is not an empty
    memory, and callers must not render it as one.
    """
    if not isinstance(mem, dict):
        return None
    topics: dict = {}
    raw_topics = mem.get("topics")
    if isinstance(raw_topics, dict):
        for key, val in raw_topics.items():
            sym = base_symbol(key)
            if not sym or not isinstance(val, dict):
                continue
            try:
                n = int(val.get("n") or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            last = str(val.get("last") or "")[:32]
            try:
                seq = int(val.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            topics[sym] = {"n": n, "last": last, "seq": max(seq, 0)}
    out: dict = {}
    if topics:
        out["topics"] = _trim(topics)
    skill = str(mem.get("last_skill") or "")
    if SKILL_RE.match(skill):
        out["last_skill"] = skill
    return out or None


def _trim(topics: dict) -> dict:
    """Keep TOPICS_MAX, evicting least-recently-seen.

    By recency, not by count: a user who asked about BTC twenty times last year
    and has since moved on should stop being described by BTC, and evicting the
    rarely-asked would pin the note to whatever they were interested in FIRST.

    Ordered on `seq`, a per-user counter, rather than on the `last` timestamp.
    `last` is ISO seconds — human-readable, and far too coarse to order by: a
    burst inside one second ties every entry, the tie then falls through to the
    count, and eviction silently becomes the by-count rule this docstring says
    it is not. The first version did exactly that and its own test caught it.
    """
    if len(topics) <= TOPICS_MAX:
        return topics
    ranked = sorted(topics.items(),
                    key=lambda kv: (kv[1].get("seq", 0), kv[1].get("last") or ""),
                    reverse=True)
    return dict(ranked[:TOPICS_MAX])


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("user_memory read failed: %s", exc)
        return {}


def get(user_id) -> Optional[dict]:
    """A user's memory, or None.

    None covers no file, no entry and unreadable file alike, because the
    caller's action is identical for all three: add no history context.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return None
    return normalize(_load().get(uid))


def observe(user_id, skill, kwargs=None) -> Optional[dict]:
    """Record one dispatched intent. Returns the stored record, or None.

    NEVER RAISES and never blocks the dispatch it is observing. Instrumentation
    on the chat path must not be the reason chat fails — the same rule every
    other diagnostic in this repo is written under.
    """
    uid = str(user_id or "").strip()
    name = str(skill or "").strip().lower()
    if not uid or not SKILL_RE.match(name):
        return None
    sym = ""
    if isinstance(kwargs, dict):
        for key in _SYMBOL_KEYS:
            if key in kwargs:
                sym = base_symbol(kwargs.get(key))
                if sym:
                    break
    try:
        with _LOCK:
            d = _load()
            rec = normalize(d.get(uid)) or {}
            topics = dict(rec.get("topics") or {})
            if sym:
                prev = topics.get(sym) or {}
                nxt = max((int(v.get("seq") or 0) for v in topics.values()),
                          default=0) + 1
                topics[sym] = {"n": int(prev.get("n") or 0) + 1,
                               "last": _now_iso(), "seq": nxt}
                rec["topics"] = _trim(topics)
            rec["last_skill"] = name
            clean = normalize(rec)
            if clean is None:
                return None
            d[uid] = clean
            atomic_write_json(_path(), d, indent=None)
        return clean
    except Exception as exc:
        log.warning("user_memory write failed: %s", exc)
        return None


def clear(user_id) -> bool:
    """Forget a user's observed history. Never raises."""
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
            log.warning("user_memory clear failed: %s", exc)
            return False
    return True


def note_for(user_id) -> str:
    """The history line for a user, or "" when there is nothing to say."""
    return render_note(get(user_id))


def render_note(mem) -> str:
    """Format an ALREADY-NORMALIZED memory (or None) as a prompt line.

    Split from `note_for` so any surface holding a record renders it through
    identical code — one formatter, so two surfaces cannot describe the same
    person differently.

    WHAT THIS SENTENCE IS ALLOWED TO CLAIM. Only what the counts support:
    which assets were asked about, in what order of frequency, and over how
    many recorded questions. Not "prefers", not "always", not "only" — the
    store is a bounded, evicting window, so "only" would become false the
    moment a thirteenth symbol pushed a twelfth out. The observation count is
    included because a ranking over three questions and a ranking over three
    hundred are different evidence and the model should be able to tell.
    """
    m = normalize(mem)
    if not m:
        return ""
    topics = m.get("topics") or {}
    if not topics:
        return ""
    ranked = sorted(topics.items(),
                    key=lambda kv: (-kv[1].get("n", 0), kv[0]))
    named = [sym for sym, _ in ranked[:NOTE_TOPICS]]
    total = sum(int(v.get("n") or 0) for v in topics.values())
    return ("Assets they have recently asked this agent about, most-asked "
            f"first: {', '.join(named)} (from {total} recorded question"
            f"{'' if total == 1 else 's'}).")
