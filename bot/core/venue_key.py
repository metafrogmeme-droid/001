"""Where a (user, venue) pair's risk and portfolio state lives on disk.

Phase 2 of multi-venue (``docs/MULTI_VENUE_RISK_SPLIT.md``): the account
breakers split per (user, venue), the same way they already split per user.
This module owns exactly one decision — the KEY — because getting it wrong
corrupts state silently rather than failing, and every other part of the phase
depends on it.

WHY NOT ``risk_state_{user}_{venue}.json``, WHICH IS WHAT THE SCOPE SKETCHED.

Because the filename is not just a name here: ``MultiUserPortfolio``
reconstructs the user id FROM it at startup (``_load_existing`` reads
``portfolio_{raw}.json`` and runs ``_sanitize(raw)``), so any separator has to
survive that round trip AND stay unambiguous. Neither holds:

  * ``_sanitize`` keeps ``[a-zA-Z0-9_-]`` and strips everything else, so a dot
    separator is DELETED — ``portfolio_alice.bybit.json`` reloads as the user
    ``alicebybit``. A phantom account, created on restart, holding somebody's
    real balance. Measured, not assumed: ``_sanitize('alice.bybit')`` returns
    ``'alicebybit'``.
  * The separators that DO survive are the ones user ids may already contain.
    ``{user}_{venue}`` cannot tell the user ``alice_bybit`` apart from the user
    ``alice`` on Bybit, and a collision here means two accounts sharing one
    circuit breaker.

So the venue is a DIRECTORY, not a filename fragment: ``data/venue/{venue}/``.
A path component cannot be confused with part of a name, the existing
non-recursive ``data/portfolio_*.json`` glob cannot see the new files, and the
single-venue layout on disk today is left exactly where it is.

The venue is validated against the credential store's own registry rather than
a copy — an unrecognised string must never become a directory, both because it
would be attacker-influenced path input and because a typo would silently open
a second, empty book instead of failing.
"""
from __future__ import annotations

import re

from bot.utils.paths import state_path

#: The single-venue default. A caller passing this, or passing nothing, gets
#: the ORIGINAL paths — Phase 2 must be byte-identical to Phase 1 on the
#: default path, and that is easiest to guarantee by making it the same code.
DEFAULT_VENUE = "bitget"

#: Subdirectory holding every venue-scoped state file, relative to the repo
#: root. Never join to this directly — call ``venue_root()``, which anchors it.
_VENUE_SUBDIR = "data/venue"


def venue_root() -> str:
    """Absolute path of the venue state directory.

    ANCHORED, because the caller that needs it is a glob at startup and a
    relative one scans whatever directory the process happened to be launched
    from. That does not fail — it finds nothing, and finding nothing here means
    every split book silently resets to the default paper balance with no open
    positions. ``tests/test_durable_paths_are_not_cwd_dependent.py`` caught the
    first version of this module doing exactly that.
    """
    return str(state_path(_VENUE_SUBDIR))


_SAFE = re.compile(r"^[a-z0-9]+$")


def known_venues() -> frozenset:
    """The venues the credential store accepts, read from IT, not copied.

    A second list of venue names is the kind that drifts silently and then
    disagrees about what a venue is — the same reason ``_is_admin_id`` is not
    duplicated into the proactive monitor.
    """
    try:
        from bot.core.exchange_credentials import _VENUE_FIELDS
        return frozenset(_VENUE_FIELDS)
    except Exception:
        # Fail CLOSED to the default alone. An import failure must not widen
        # what counts as a venue; the caller then behaves as single-venue,
        # which is the safe direction.
        return frozenset({DEFAULT_VENUE})


def normalize_venue(venue) -> str:
    """A venue string reduced to its canonical form, or ``''`` if unusable.

    ``''`` means "no venue was named" and is the DEFAULT-PATH signal, distinct
    from a named venue. It is deliberately NOT ``'bitget'``: a caller that
    passes nothing and a caller that explicitly says bitget both end up on the
    same state, but only the second one has made a claim, and collapsing them
    here would hide an unrecognised venue string as if it were the default.
    An unknown venue returns ``''`` too — see ``venue_key``, which refuses.
    """
    v = str(venue or "").strip().lower()
    if not v or not _SAFE.match(v):
        return ""
    return v if v in known_venues() else ""


def is_split(venue) -> bool:
    """Does this venue get its own state, separate from the default path?

    False for the empty string, for an unknown venue, and for the default
    venue — all three run the original single-venue paths.
    """
    v = normalize_venue(venue)
    return bool(v) and v != DEFAULT_VENUE


def executor_state_dir(venue):
    """Where a per-user LIVE EXECUTOR for ``venue`` keeps its book.

    ``None`` for the default venue (and for ``''``): the original ``data/``
    paths, so a single-venue deploy is byte-identical. ``data/venue/{v}`` for a
    split venue. Raises ``ValueError`` for a venue the credential store does
    not know, for the reason ``venue_state_path`` gives.

    The executor's positions and closed-trade files were the one piece of
    per-user state this layout never reached: they are named
    ``live_positions_{user}.json`` with no venue in them, so a user's bitget
    executor and bybit executor wrote ONE file. Driven: a bitget position was
    loaded into the bybit executor's book when the user connected bybit, and
    the bybit executor's first save of an empty book erased it.

    Rooted where the EXECUTOR roots its files (``RUNECLAW_STATE_DIR``, default
    ``data``), not at ``venue_root()``: a user's default-venue book and their
    split-venue book have to live under one root, or a deployment that moves
    the state directory splits one person's positions across two trees.
    """
    import os

    s = str(venue or "").strip()
    if not s:
        return None
    v = normalize_venue(s)
    if not v:
        raise ValueError(f"unknown venue: {venue!r}")
    if v == DEFAULT_VENUE:
        return None
    base = os.environ.get("RUNECLAW_STATE_DIR") or "data"
    return os.path.join(base, "venue", v)


def venue_state_path(kind: str, user_id: str, venue: str) -> str:
    """``data/venue/{venue}/{kind}_{user}.json`` for a split venue.

    Raises ``ValueError`` when the venue is not one the credential store knows.
    That is deliberate: this value becomes a filesystem path, and a caller that
    reached here with an unrecognised venue has a bug that must surface now,
    not as an empty book six hours into a trading day.
    """
    v = normalize_venue(venue)
    if not v:
        raise ValueError(f"unknown venue: {venue!r}")
    if not _SAFE.match(str(kind) or ""):
        raise ValueError(f"unsafe state kind: {kind!r}")
    user = re.sub(r"[^a-zA-Z0-9_-]", "", str(user_id))
    if not user:
        raise ValueError("empty user id after sanitization")
    return str(state_path(f"{_VENUE_SUBDIR}/{v}/{kind}_{user}.json"))
