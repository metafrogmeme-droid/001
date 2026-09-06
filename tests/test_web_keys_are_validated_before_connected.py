"""Web-submitted exchange keys were imported without ever being checked.

``engine._maybe_pull_web_credentials`` called ``pull_and_apply(on_change=...)``
with **no validator**, so ``process_pending`` skipped its verdict branch
entirely: every submitted key was written into the Fernet store and acked
``ok: true``. The website flipped ``exchange_status.connected`` and the
dashboard printed **"✓ connected"** over credentials that could be typo'd,
revoked, read-only or IP-restricted. Telegram's ``/connect`` has validated
against the venue since it was written and prints the venue's own error — two
doors to the same store, disagreeing about whether bad keys are acceptable.

The other half was on the website: ``POST /credentials/ack`` read
``if (!a || a.user_id == null || !a.ok) continue;``, discarding every failure.
So even once the bot COULD reject a key, the reason went nowhere, the pending
row was never deleted, and ``/status`` returned ``pending: 'connect'``
forever — the card stuck on "applying…" with no timeout while the bot
re-pulled and re-failed the same row every 30 seconds.

THE TRAP IN THE FIX, which is why ``default_validator`` exists at all rather
than the async probe being passed directly:

``validate_venue_credentials`` is ``async def``. ``process_pending`` is
synchronous and runs inside ``asyncio.to_thread``. Handing it the coroutine
FUNCTION makes ``verdict`` a coroutine OBJECT — truthy, so ``verdict is
False`` and ``verdict is None`` are both False and **every key is imported as
valid**, with a "never awaited" warning as the only trace. That is
``bot/core/basis.py``'s recorded failure (a sync call to an async factory,
swallowed by a broad except, wired and dead) on the path that decides whose
keys trade. ``test_the_validator_is_not_a_coroutine_function`` is the pin.
"""
from __future__ import annotations

import inspect
import json
import threading

import pytest

import bot.utils.credential_pull as cp


class _Store:
    def __init__(self):
        self.creds = {}

    def set(self, tg, k, s, p):
        self.creds[str(tg)] = (k, s, p)

    def set_venue(self, tg, venue, fields):
        self.creds[f"{tg}:{venue}"] = fields


def _row(payload: str, uid=1, tg="999"):
    return {"user_id": uid, "telegram_id": tg, "action": "connect",
            "encrypted_payload": payload}


@pytest.fixture()
def sealed(tmp_path, monkeypatch):
    """A real sealed row the puller can open, so the validator is reached."""
    import bot.utils.creds_sealing as cs
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_CREDS_KEY", raising=False)
    cs._cache.clear()
    from tests.test_creds_sealing import _seal
    yield json.dumps(_seal({"venue": "bitget", "api_key": "AK",
                            "api_secret": "SEC", "passphrase": "PP"},
                           cs.public_key_pem()))
    cs._cache.clear()


# ── the trap ───────────────────────────────────────────────────────────────

def test_the_validator_is_not_a_coroutine_function():
    """If this ever becomes async, process_pending's verdict is a truthy
    coroutine object and EVERY key is accepted. The failure is silent: the
    keys import, the ack says ok, and a never-awaited warning is the only
    trace. Pinned as the property, not as a spelling."""
    assert not inspect.iscoroutinefunction(cp.default_validator)


def test_passing_the_async_probe_directly_would_accept_everything(sealed):
    """The mutation, executed, so the pin above is not a claim about a claim.

    A coroutine object is truthy, so neither `is False` nor `is None` fires
    and the row imports. Demonstrated here rather than argued."""
    async def _async_reject(_creds):
        return False, "these keys are bad"

    store = _Store()
    acks = cp.process_pending([_row(sealed)], store, validator=_async_reject)
    assert acks and acks[0]["ok"] is True, (
        "if this ever stops being True the demonstration is stale")
    assert store.creds, "the rejected key was imported — the trap, reproduced"
    # And the honest wrapper refuses the same keys.
    store2 = _Store()
    acks2 = cp.process_pending([_row(sealed)], store2,
                               validator=lambda c: (False, "these keys are bad"))
    assert acks2[0]["ok"] is False and not store2.creds


# ── the three outcomes reach the website ───────────────────────────────────

def test_a_rejected_key_is_acked_with_the_venue_s_reason(sealed):
    """The reason is what a user can act on. "IP not whitelisted" and "the
    exchange rejected these keys" are different instructions."""
    store = _Store()
    acks = cp.process_pending([_row(sealed)], store,
                              validator=lambda c: (False, "IP not whitelisted"))
    assert acks == [{"user_id": 1, "action": "connect", "ok": False,
                     "error": "IP not whitelisted"}]
    assert not store.creds, "a rejected key must not be stored"


def test_a_transient_failure_leaves_the_row_queued(sealed):
    """None is "could not verify right now". Acking it would clear a
    submission whose keys may be perfect and tell the user they failed."""
    store = _Store()
    acks = cp.process_pending([_row(sealed)], store,
                              validator=lambda c: (None, "could not reach the exchange"))
    assert acks == []
    assert not store.creds


def test_a_valid_key_still_imports_and_acks(sealed):
    store = _Store()
    acks = cp.process_pending([_row(sealed)], store, validator=lambda c: (True, ""))
    assert acks == [{"user_id": 1, "action": "connect", "ok": True}]
    assert store.creds["999"] == ("AK", "SEC", "PP")


def test_a_bare_bool_verdict_is_still_honoured(sealed):
    """The older contract. A validator that answers True/False/None without a
    reason must keep working — the tuple is an extension, not a replacement."""
    store = _Store()
    assert cp.process_pending([_row(sealed)], store, validator=lambda c: False)[0]["ok"] is False
    assert cp.process_pending([_row(sealed)], _Store(), validator=lambda c: None) == []
    assert cp.process_pending([_row(sealed)], _Store(), validator=lambda c: True)[0]["ok"] is True


# ── default_validator itself ───────────────────────────────────────────────

def test_it_runs_from_a_worker_thread_which_is_where_it_lives(monkeypatch):
    """`asyncio.run` is correct only off the loop. The engine offloads via
    asyncio.to_thread, so this is the real calling context."""
    import bot.core.exchange_credentials as ec

    async def _probe(venue, fields, sandbox=False):
        return True, ""

    monkeypatch.setattr(ec, "validate_venue_credentials", _probe)
    out: list = []
    t = threading.Thread(target=lambda: out.append(cp.default_validator(
        {"venue": "bitget", "api_key": "a", "api_secret": "b", "passphrase": "c"})))
    t.start()
    t.join(30)
    assert out and out[0][0] is True


def test_an_unknown_venue_is_a_verdict_not_a_retry():
    """It will never become valid, so the row must clear rather than retry
    forever — the same reasoning as an undecryptable payload."""
    verdict, reason = cp.default_validator({"venue": "ftx", "api_key": "x"})
    assert verdict is False
    assert "ftx" in reason


def test_a_probe_that_raises_is_transient_not_a_rejection(monkeypatch):
    """A venue outage must not destroy a submission. None retries."""
    import bot.core.exchange_credentials as ec

    async def _boom(venue, fields, sandbox=False):
        raise TimeoutError("venue unreachable")

    monkeypatch.setattr(ec, "validate_venue_credentials", _boom)
    verdict, _ = cp.default_validator(
        {"venue": "bitget", "api_key": "a", "api_secret": "b", "passphrase": "c"})
    assert verdict is None


def test_the_engine_passes_a_validator_at_all():
    """WIRING, and it is the whole defect: every branch above was already
    implemented and correct, and unreachable because the one call site
    omitted the argument. Behaviour is covered by the tests above; this pins
    that something reaches them."""
    import re
    from pathlib import Path

    from tests.source_scan import code_only
    src = code_only(Path("bot/core/engine.py").read_text(encoding="utf-8"))
    # ANCHORED TO THE CALL. The first draft took `src.index("pull_and_apply")`
    # and read a window around it — which lands on the `from ... import` line
    # several statements earlier, so it asserted about the import and failed
    # against a correctly-wired call. Match the invocation itself.
    call = re.search(r"to_thread\(\s*\n?\s*pull_and_apply.*?\)", src, re.S)
    assert call, "the credential pull is no longer offloaded — this scan has drifted"
    assert "validator=" in call.group(0), (
        "the credential pull runs with no validator — submitted keys are "
        "imported unchecked and the dashboard prints them as connected:\n"
        + call.group(0))
