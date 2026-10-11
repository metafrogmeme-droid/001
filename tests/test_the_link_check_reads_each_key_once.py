"""The link check reads each key's account once, reads a load refusal again,
and names each side's reason with Bitget's code.

10 October, with the link check that says why deployed, the operator's
`/duplicates` card read:

    Book 6307156912 was not compared: the account ID behind either set of
    keys could not be read (ExchangeError / DDoSProtection), so it is not
    known whether they open the operator's account.

Three things in it the operator could not act on:

* "either set of keys" with two reasons: which was whose? The linked key's
  read was refused (ExchangeError); the operator's, made a moment after it,
  met a rate limit (DDoSProtection).
* ExchangeError is ccxt's class for every Bitget code it does not map. The
  code is what tells a permission from an IP list, and it was dropped.
* A rate limit is a refusal for load, not for the key: one read a moment
  later settles it. The check gave up instead.

And every re-check asked Bitget for both account IDs again, back to back,
though a key opens one account for its life: the second read is the one the
rate limit met. Now an account ID is read once per key per process (a digest
of the key holds it, never the key), the operator's first; a failed read is
never kept; a load refusal is read once more after a pause.

Driven through the real reading, the real engine method and the real card;
only Bitget's account-info reply is replaced.
"""
from __future__ import annotations

import asyncio
import html
import time
from unittest.mock import patch

import ccxt
import pytest

from bot.core import duplicate_closes as dc
from bot.core import exchange_credentials as ec
from tests.test_a_close_booked_twice_is_struck_once import OP, books  # noqa: F401
from tests.test_one_account_one_executor import (  # noqa: F401
    OPERATOR,
    OTHER,
    ROTATED,
    UID_OF,
    _cfg,
    _engine,
    _store,
    operator_keys,
)

OPK, ROTK, OTHK = OPERATOR["api_key"], ROTATED["api_key"], OTHER["api_key"]


def _bitget(script=None):
    """Bitget's stand-in. ``script[api_key]`` lists the replies that key's
    reads get in turn: an account UID (digits) or why none was read. A key
    past its script reads the account `UID_OF` says it opens."""
    script = {k: list(v) for k, v in (script or {}).items()}
    calls: list = []

    async def probe(api_key, api_secret, passphrase, sandbox=False):
        calls.append(api_key)
        queue = script.get(api_key)
        reply = queue.pop(0) if queue else UID_OF.get(api_key)
        if reply and reply.isdigit():
            return {"account_uid": reply, "account_uid_error": None}
        return {"account_uid": None, "account_uid_error": reply}

    probe.calls = calls
    return probe


def _read(fields, probe, known=None):
    return asyncio.run(ec.bitget_link_reading(fields, probe=probe, known=known))


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(ec, "UID_READ_RETRY_DELAY_S", 0.0)


# ── the order and the retry ─────────────────────────────────────────────────

def test_the_operators_account_is_read_first(operator_keys, no_pause):  # noqa: F811
    probe = _bitget()
    assert _read(OTHER, probe).verdict == ec.LINK_OTHER_ACCOUNT
    assert probe.calls == [OPK, OTHK]


@pytest.mark.parametrize("load", [
    "DDoSProtection", "RateLimitExceeded", "RequestTimeout", "NetworkError",
    "ExchangeNotAvailable", "TimeoutError", "DDoSProtection, Bitget code 429",
])
def test_a_refusal_for_load_is_read_once_more(operator_keys, no_pause, load):  # noqa: F811
    """The 10 October pair: the operator's read met the rate limit."""
    probe = _bitget({OPK: [load]})
    r = _read(ROTATED, probe)
    assert (r.verdict, r.same) == (ec.LINK_SAME_ACCOUNT, True)
    assert probe.calls == [OPK, OPK, ROTK]


def test_the_second_read_waits(operator_keys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ec, "UID_READ_RETRY_DELAY_S", 0.2)
    started = time.monotonic()
    assert _read(ROTATED, _bitget({ROTK: ["DDoSProtection"]})).same is True
    assert time.monotonic() - started >= 0.2, "read again into the same rate limit"


@pytest.mark.parametrize("refusal", [
    "PermissionDenied, Bitget code 40014", "AuthenticationError, Bitget code 40012",
    "ExchangeError, Bitget code 40725", "ExchangeError", "no account ID in Bitget's reply",
])
def test_a_refusal_for_the_key_is_not_read_again(operator_keys, no_pause, refusal):  # noqa: F811
    """A key without the permission is refused the same way a moment later;
    a class ccxt could not name is not assumed to be load."""
    probe = _bitget({OTHK: [refusal]})
    r = _read(OTHER, probe)
    assert probe.calls == [OPK, OTHK]
    assert (r.verdict, r.their_cause, r.our_cause) == (ec.LINK_UNREAD, refusal, "")


def test_a_load_refusal_twice_is_unread_after_two_reads(operator_keys, no_pause):  # noqa: F811
    probe = _bitget({OPK: ["DDoSProtection", "RateLimitExceeded"]})
    r = _read(OTHER, probe)
    assert probe.calls == [OPK, OPK, OTHK], "read more than twice, or not twice"
    assert (r.same, r.our_cause, r.their_cause) == (None, "RateLimitExceeded", "")


def test_a_probe_that_raises_names_its_class_and_bitgets_code(operator_keys, no_pause):  # noqa: F811
    async def probe(api_key, api_secret, passphrase, sandbox=False):
        probe.calls.append(api_key)
        if api_key == OTHK:
            raise ccxt.PermissionDenied('bitget {"code":"40014","msg":"secret detail"}')
        if len(probe.calls) == 1:
            raise TimeoutError("the venue said something long")
        return {"account_uid": UID_OF[api_key]}

    probe.calls = []
    r = _read(OTHER, probe)
    assert probe.calls == [OPK, OPK, OTHK], "a timeout is load: read once more"
    assert (r.their_cause, r.our_cause) == ("PermissionDenied, Bitget code 40014", "")
    assert "secret detail" not in repr(r)


# ── read once per key ───────────────────────────────────────────────────────

def test_an_account_read_is_kept_per_key_and_a_failure_is_not(operator_keys, no_pause):  # noqa: F811
    known: dict = {}
    probe = _bitget({OTHK: ["PermissionDenied, Bitget code 40014"]})
    assert _read(OTHER, probe, known).their_cause == "PermissionDenied, Bitget code 40014"
    assert probe.calls == [OPK, OTHK]
    # The operator's account is not asked again; the failed read is.
    assert _read(OTHER, probe, known).verdict == ec.LINK_OTHER_ACCOUNT
    assert probe.calls == [OPK, OTHK, OTHK]
    # Both read: a re-check asks Bitget nothing.
    assert _read(OTHER, probe, known).verdict == ec.LINK_OTHER_ACCOUNT
    assert probe.calls == [OPK, OTHK, OTHK]
    assert sorted(known.values()) == ["7001", "8002"]
    held = repr(known)
    for fields in (OPERATOR, OTHER):
        for secret in fields.values():
            assert secret not in held, "the memory holds a key, not a digest of it"
    # The other arm: with no memory, every reading asks again.
    probe = _bitget()
    _read(OTHER, probe)
    _read(OTHER, probe)
    assert probe.calls == [OPK, OTHK, OPK, OTHK]


def test_the_engine_reads_each_key_once_across_re_checks(tmp_path, operator_keys, no_pause):  # noqa: F811
    s = _store(tmp_path)
    s.set_venue("1001", "bitget", ROTATED)
    eng = _engine()
    probe = _bitget()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        _cfg(cfg)
        assert asyncio.run(eng.check_operator_account_links(probe=probe)) == {"1001"}
        assert asyncio.run(eng.check_operator_account_links(probe=probe)) == {"1001"}
        assert probe.calls == [OPK, ROTK], "the re-check asked Bitget again"
        assert eng._operator_link_readings["1001"].verdict == ec.LINK_SAME_ACCOUNT
        # New keys on the link are read; the operator's account is not again.
        s.set_venue("1001", "bitget", OTHER)
        assert asyncio.run(eng.check_operator_account_links(probe=probe)) == set()
        assert probe.calls == [OPK, ROTK, OTHK]


# ── the card ────────────────────────────────────────────────────────────────

def test_a_reading_keeps_each_sides_reason():
    both = ec.LinkReading(ec.LINK_UNREAD, their_cause="ExchangeError", our_cause="DDoSProtection")
    assert both.cause == "ExchangeError / DDoSProtection"
    assert ec.LinkReading(ec.LINK_UNREAD, their_cause="A", our_cause="A").cause == "A"
    assert ec.LinkReading(ec.LINK_UNREAD, our_cause="DDoSProtection").cause == "DDoSProtection"
    assert ec.LinkReading(ec.LINK_SAME_ACCOUNT).cause == ""


def test_the_card_names_each_sides_reason(books):  # noqa: F811
    books.store.set_venue(OP, "bitget", dict(OTHER))

    def card(**causes):
        books._operator_link_readings = {OP: ec.LinkReading(ec.LINK_UNREAD, **causes)}
        r = dc.find_duplicate_closes(books)
        assert r.not_compared == [OP]
        return dc.duplicates_card(r)[0]

    remedy = "/disconnect removes the link"
    tail = ", so it is not known whether its keys open the operator's account."
    both = card(their_cause="ExchangeError, Bitget code 40725", our_cause="DDoSProtection")
    assert html.escape(
        f"Book {OP} was not compared: neither account ID could be read (its keys: "
        "ExchangeError, Bitget code 40725; the operator's own keys: DDoSProtection)"
        + tail) in both, both
    assert "either set of keys" not in both
    ours = card(our_cause="DDoSProtection")
    assert html.escape(f"Book {OP} was not compared: the account ID behind the operator's "
                       "own keys could not be read (DDoSProtection)" + tail) in ours, ours
    theirs = card(their_cause="PermissionDenied, Bitget code 40014")
    assert html.escape(f"Book {OP} was not compared: the account ID behind its keys could "
                       "not be read (PermissionDenied, Bitget code 40014)" + tail) in theirs
    for text in (both, ours, theirs):
        assert remedy in text, "could not tell is not a verdict: the remedy belongs"
