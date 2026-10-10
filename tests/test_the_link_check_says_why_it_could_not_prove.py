"""The link check says why it could not prove a link, and never forgets a proof.

10 October, after the `/duplicates` deploy, the operator's card read:

    Book 6307156912 holds keys not found to open the operator's account, so
    it may be another account and was not compared.

The operator's own book. The sentence fits three different facts and the
operator's next step differs for each: the keys open another account (leave
the book), the check never read them (it ran only at boot and every six
hours, and only with per-user live on), or Bitget would not hand over the
account ID (a futures-only key is refused the spot account read, and the
probe threw the reason away). Now:

* the operator's own API key stored again is the operator's account, without
  asking Bitget anything: a key opens one account;
* every other reading is kept with its verdict, or with whose account ID did
  not read and why (an exception class, never a message);
* `/duplicates` re-reads the operator ids' links before it places their
  books, and the card names the reason for each book it did not compare.

And one defect found on the way: each re-check REPLACED the marks with what
that pass could prove, so a proven link that failed one read (a Bitget
outage at the six-hour re-check) lost its mark, and the next ask built the
second executor on the operator's account again: the 8 October failure, from
a timeout. An unread UID now keeps the mark it had.

Driven through the real ExchangeCredentialStore, the real engine method, the
real `/duplicates` command; only Bitget's account-info reply is replaced.
"""
from __future__ import annotations

import asyncio
import html
from unittest.mock import patch

from bot.core import duplicate_closes as dc
from bot.core import exchange_credentials as ec
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor
from tests.test_a_close_booked_twice_is_struck_once import MANUAL, OP, _handler, _update, books  # noqa: F401
from tests.test_one_account_one_executor import (  # noqa: F401
    OPERATOR,
    OTHER,
    ROTATED,
    UID_OF,
    _cfg,
    _engine,
    _probe,
    _store,
    operator_keys,
)


def _refusing(reads=None, error="PermissionDenied"):
    """Bitget's stand-in that reads the UIDs in ``reads`` and refuses the
    rest the way it refuses a key without spot-account read permission."""
    reads = reads or {}
    calls: list = []

    async def probe(api_key, api_secret, passphrase, sandbox=False):
        calls.append(api_key)
        uid = reads.get(api_key)
        return {"withdraw": "unknown", "ip_allowlist": None, "account_uid": uid,
                "account_uid_error": None if uid else error}

    probe.calls = calls
    return probe


# ── the reading ─────────────────────────────────────────────────────────────

def test_the_operators_own_key_stored_again_is_the_operators_account(operator_keys):  # noqa: F811
    """No UID can be read here (the 10 October case), and none is needed."""
    probe = _refusing()
    r = asyncio.run(ec.bitget_link_reading(dict(OPERATOR), probe=probe))
    assert (r.verdict, r.same) == (ec.LINK_SAME_KEY, True)
    assert probe.calls == [], "Bitget was asked about a key the bot already holds"
    assert asyncio.run(ec.same_bitget_account_as_operator(dict(OPERATOR), probe=probe)) is True


def test_each_reading_names_its_verdict(operator_keys):  # noqa: F811
    probe = _probe()
    same = asyncio.run(ec.bitget_link_reading(ROTATED, probe=probe))
    other = asyncio.run(ec.bitget_link_reading(OTHER, probe=probe))
    assert (same.verdict, same.same) == (ec.LINK_SAME_ACCOUNT, True)
    assert (other.verdict, other.same) == (ec.LINK_OTHER_ACCOUNT, False)


def test_an_unread_uid_says_whose_and_why(operator_keys):  # noqa: F811
    theirs = asyncio.run(ec.bitget_link_reading(
        OTHER, probe=_refusing({OPERATOR["api_key"]: "7001"})))
    assert (theirs.verdict, theirs.same) == (ec.LINK_UNREAD, None)
    assert (theirs.unread, theirs.cause) == ("theirs", "PermissionDenied")
    ours = asyncio.run(ec.bitget_link_reading(
        OTHER, probe=_refusing({OTHER["api_key"]: "8002"}, error="AuthenticationError")))
    assert (ours.unread, ours.cause) == ("operator", "AuthenticationError")
    both = asyncio.run(ec.bitget_link_reading(OTHER, probe=_refusing()))
    assert (both.unread, both.cause) == ("both", "PermissionDenied")

    async def _raises(*a, **k):
        raise TimeoutError("the venue said something long")

    boom = asyncio.run(ec.bitget_link_reading(OTHER, probe=_raises))
    assert (boom.unread, boom.cause) == ("both", "TimeoutError")


def test_the_probe_keeps_why_it_read_no_uid(monkeypatch):
    """The class, never the message: the reason reaches a card."""
    import ccxt
    import ccxt.async_support as ccxt_async

    real = ccxt_async.bitget
    reply: dict = {}

    class _Bitget(real):
        def __init__(self, config=None):
            super().__init__(config)

            async def info(params=None):
                if reply.get("raise"):
                    raise ccxt.PermissionDenied('bitget {"code":"40014","msg":"secret detail"}')
                return {"code": "00000", "data": reply["data"]}

            self.privateSpotGetV2SpotAccountInfo = info

    monkeypatch.setattr(ccxt_async, "bitget", _Bitget)
    reply["raise"] = True
    out = asyncio.run(ec.probe_bitget_key_scope(**OPERATOR))
    assert (out["account_uid"], out["account_uid_error"]) == (None, "PermissionDenied")
    reply.update({"raise": False, "data": {"authorities": []}})
    out = asyncio.run(ec.probe_bitget_key_scope(**OPERATOR))
    assert (out["account_uid"], out["account_uid_error"]) == (None, "no account ID in Bitget's reply")
    reply["data"] = {"userId": "7001", "authorities": []}
    out = asyncio.run(ec.probe_bitget_key_scope(**OPERATOR))
    assert (out["account_uid"], out["account_uid_error"]) == ("7001", None)


# ── the marks ───────────────────────────────────────────────────────────────

def test_a_proven_link_keeps_its_mark_through_a_failed_read(tmp_path, operator_keys):  # noqa: F811
    s = _store(tmp_path)
    s.set_venue("1001", "bitget", ROTATED)
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        _cfg(cfg)
        assert asyncio.run(eng.check_operator_account_links(probe=_probe())) == {"1001"}
        # The six-hour re-check meets an outage: no UID reads.
        assert asyncio.run(eng.check_operator_account_links(probe=_refusing())) == {"1001"}
        assert eng._executor_for("1001") is eng.live_executor, \
            "a failed read rebuilt the second executor on the operator's account"
        assert eng._operator_link_readings["1001"].cause == "PermissionDenied"
        # A verdict does move it: the keys now open another account ...
        s.set_venue("1001", "bitget", OTHER)
        assert asyncio.run(eng.check_operator_account_links(probe=_probe())) == set()
        own = eng._executor_for("1001")
        assert isinstance(own, LiveExecutor) and own is not eng.live_executor
        # ... and so does a link removed.
        s.set_venue("1001", "bitget", ROTATED)
        asyncio.run(eng.check_operator_account_links(probe=_probe()))
        assert eng._operator_account_users == {"1001"}
        s.delete("1001")
        asyncio.run(eng.check_operator_account_links(probe=_refusing()))
        assert eng._operator_account_users == set()


def test_a_re_read_of_the_operators_links_leaves_every_other_mark(tmp_path, operator_keys):  # noqa: F811
    s = _store(tmp_path)
    s.set_venue("1001", "bitget", ROTATED)    # the operator
    s.set_venue("2002", "bitget", ROTATED)    # somebody else holding those keys
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=s), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: None):
        _cfg(cfg)
        asyncio.run(eng.check_operator_account_links(probe=_probe()))
        assert eng._operator_account_users == {"1001", "2002"}
        s.set_venue("2002", "bitget", OTHER)   # changed, but not re-read below
        probe = _probe()
        asyncio.run(eng.check_operator_account_links(probe=probe, operators_only=True))
        assert OTHER["api_key"] not in probe.calls, "a non-operator's link was re-read"
        assert eng._operator_account_users == {"1001", "2002"}
        assert eng._operator_link_readings["2002"].verdict == ec.LINK_SAME_ACCOUNT
        assert eng._executor_for("2002") is None


# ── /duplicates ─────────────────────────────────────────────────────────────

def _real_check(engine):
    engine.check_operator_account_links = (
        RuneClawEngine.check_operator_account_links.__get__(engine))
    return engine


def test_duplicates_reads_the_link_now_and_searches_the_operators_book(books, operator_keys):  # noqa: F811
    """The 10 October card: the operator's own key, never checked. One
    `/duplicates` now proves it, compares the book, and offers the strike."""
    books.store.set_venue(OP, "bitget", dict(OPERATOR))
    _real_check(books)
    assert dc.find_duplicate_closes(books).not_compared == [OP]   # the card before
    h = _handler(books, admin=True)
    asyncio.run(h._cmd_duplicates(_update(), None))
    (text, kb), = h.sent
    assert books._operator_account_users == {OP}
    assert MANUAL in text and "its keys open the operator's account" in text
    (row,), = kb.to_dict()["inline_keyboard"]
    assert row["callback_data"].startswith("dupstrike:")
    assert f"Book {OP} was not compared:" not in text, text


def test_the_card_says_why_a_book_was_not_compared(books):  # noqa: F811
    books.store.set_venue(OP, "bitget", dict(OTHER))

    def card(reading):
        if reading is None:
            books.__dict__.pop("_operator_link_readings", None)
        else:
            books._operator_link_readings = {OP: reading}
        r = dc.find_duplicate_closes(books)
        assert r.strays == [] and r.not_compared == [OP]
        return dc.duplicates_card(r)[0]

    remedy = "/disconnect removes the link"
    other = card(ec.LinkReading(ec.LINK_OTHER_ACCOUNT))
    assert html.escape(f"Book {OP} was not compared: its Bitget keys open a different "
                       "account") in other
    assert remedy not in other, "a different account was told to unlink"
    unread = card(ec.LinkReading(ec.LINK_UNREAD, unread="theirs", cause="PermissionDenied"))
    assert "the account ID behind its keys could not be read (PermissionDenied)" in unread
    assert remedy in unread
    never = card(None)
    assert "have not been checked against the operator" in never and remedy in never


def test_a_link_check_that_could_not_run_is_said(books):  # noqa: F811
    async def _down(**kw):
        raise RuntimeError("store locked")

    books.check_operator_account_links = _down
    h = _handler(books, admin=True)
    asyncio.run(h._cmd_duplicates(_update(), None))
    (text, _kb), = h.sent
    assert "The link check could not run now (RuntimeError)" in text
    assert "store locked" not in text
    # The search still ran on what the last check placed.
    assert MANUAL in text
