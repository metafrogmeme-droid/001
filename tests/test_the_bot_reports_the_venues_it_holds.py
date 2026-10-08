"""The bot tells the website which venues it holds keys for.

The website's keys card was a copy of its own last ack (8 October: Bitget
"connected" while the bot held no key file; "not connected" after the key was
re-linked in Telegram). `report_held_venues` sends a complete report on the
credential pull: every user the store holds, per venue held or unreadable,
never a key. The website side is app/test/the_keys_card_shows_what_the_bot_holds.test.js.

Driven on the real ExchangeCredentialStore, with only the website channel
(`_request`) recorded.
"""
from __future__ import annotations

import json
import logging

import pytest
from cryptography.fernet import Fernet

from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.utils import credential_pull as cp

BITGET = {"api_key": "BGKEY" + "K" * 10, "api_secret": "BGSEC" + "S" * 10, "passphrase": "pp"}
EU = {"api_key": "EUKEY" + "K" * 10, "api_secret": "EUSEC" + "S" * 10}
STATE = "/api/bot/sync/credentials/state"


def _store(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                   key_file=str(tmp_path / ".key"))


@pytest.fixture
def site(monkeypatch):
    """A paired website that answers ok, recording what it was sent."""
    sent: list = []
    monkeypatch.setattr(cp, "SYNC_SECRET", "s" * 48)
    monkeypatch.setattr(cp, "_state_report", cp._fresh_state_report())

    def _request(path, data=None, **k):
        sent.append((path, json.loads(json.dumps(data)) if data is not None else None))
        return {"ok": True} if path == STATE else {}

    monkeypatch.setattr(cp, "_request", _request)
    return sent


def _reports(sent):
    return [d for p, d in sent if p == STATE]


# ── what it says ────────────────────────────────────────────────────────────

def test_the_report_names_venues_and_states_and_no_key(tmp_path):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    s.set_venue("7", "bybiteu", EU)
    s.set_venue("8", "bitget", BITGET)
    # 8's Bitget record stops decrypting (a changed encryption key does this).
    s._enc["8"]["venues"]["bitget"]["api_key"] = "not-a-token"
    users = cp.held_venues(s)
    assert users == [
        {"telegram_id": "7", "venues": {"bitget": "held", "bybiteu": "held"}},
        {"telegram_id": "8", "venues": {"bitget": "unreadable"}},
    ]
    text = json.dumps(users)
    for secret in (*BITGET.values(), *EU.values()):
        assert secret not in text


def test_an_empty_store_is_a_report_that_nobody_holds_anything(tmp_path):
    assert cp.held_venues(_store(tmp_path)) == []


def test_an_unreadable_store_file_is_no_report_at_all(tmp_path, site, caplog):
    (tmp_path / "creds.enc").write_text("{not json")
    s = _store(tmp_path)
    assert s.load_failed is True
    assert cp.held_venues(s) is None
    with caplog.at_level(logging.WARNING):
        assert cp.report_held_venues(s, force=True) is False
        assert cp.report_held_venues(s, force=True) is False
    assert _reports(site) == [], "an unreadable store told the website nobody holds a key"
    assert sum("no held-venues report" in r.getMessage() for r in caplog.records) == 1


def test_the_timer_read_does_not_log_each_unreadable_record(tmp_path, caplog):
    s = _store(tmp_path)
    s.set_venue("8", "bitget", BITGET)
    s._enc["8"]["venues"]["bitget"]["api_key"] = "not-a-token"
    with caplog.at_level(logging.ERROR):
        cp.held_venues(s)
    assert not [r for r in caplog.records if "Failed to decrypt" in r.getMessage()]
    # The other arm: the store's own readers still say so.
    with caplog.at_level(logging.ERROR):
        s.venue_states("8")
    assert [r for r in caplog.records if "Failed to decrypt" in r.getMessage()]


# ── when it is sent ─────────────────────────────────────────────────────────

def test_it_is_sent_on_change_and_on_the_resend_clock_only(tmp_path, site):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    assert cp.report_held_venues(s, now=1000.0) is True
    assert _reports(site) == [{"complete": True, "users": [
        {"telegram_id": "7", "venues": {"bitget": "held"}}]}]
    # Inside the check interval the store is not read: a change made there
    # waits for the interval (the store is read every two minutes, not every
    # pull).
    s.set_venue("7", "okx", {"api_key": "OK" * 8, "api_secret": "OS" * 8, "passphrase": "pp"})
    assert cp.report_held_venues(s, now=1000.0 + cp.STATE_CHECK_EVERY_S - 1) is False
    assert len(_reports(site)) == 1
    s.delete_venue("7", "okx")
    # Checked again, unchanged: nothing sent.
    assert cp.report_held_venues(s, now=1000.0 + cp.STATE_CHECK_EVERY_S) is False
    # A change (Bybit EU linked in Telegram) goes out at the next check.
    s.set_venue("7", "bybiteu", EU)
    assert cp.report_held_venues(s, now=1000.0 + 2 * cp.STATE_CHECK_EVERY_S) is True
    assert _reports(site)[-1]["users"][0]["venues"] == {"bitget": "held", "bybiteu": "held"}
    # Unchanged, but past the resend clock: sent again.
    assert cp.report_held_venues(s, now=1000.0 + 2 * cp.STATE_CHECK_EVERY_S
                                 + cp.STATE_RESEND_EVERY_S) is True
    assert len(_reports(site)) == 3


def test_a_report_the_website_did_not_accept_is_sent_again(tmp_path, site, monkeypatch):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    monkeypatch.setattr(cp, "_request", lambda p, d=None, **k: (site.append((p, d)), None)[1])
    assert cp.report_held_venues(s, now=10.0) is False          # site down
    monkeypatch.setattr(cp, "_request", lambda p, d=None, **k: (site.append((p, d)), {"ok": True})[1])
    assert cp.report_held_venues(s, now=10.0 + cp.STATE_CHECK_EVERY_S) is True
    assert len(_reports(site)) == 2


def test_an_unpaired_bot_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "SYNC_SECRET", "")
    monkeypatch.setattr(cp, "_request", lambda *a, **k: pytest.fail("no website is paired"))
    assert cp.report_held_venues(_store(tmp_path), force=True) is False


# ── where it runs: after the acks, on every pull ────────────────────────────

def test_the_pull_reports_after_its_acks_and_with_no_rows_too(tmp_path, site, monkeypatch):
    s = _store(tmp_path)
    s.set_venue("7", "bitget", BITGET)
    monkeypatch.setattr(cp, "publish_sealing_key", lambda *a, **k: True)
    # No pending rows: still reported (Telegram /connect never makes a row).
    cp.pull_and_apply(store=s)
    assert [p for p, _ in site][-1] == STATE
    # And not on every pull: the next one, nothing pending, sends nothing.
    cp.pull_and_apply(store=s)
    assert len(_reports(site)) == 1, "every pull forced a report past both clocks"
    # Rows acked: the report follows the ack, at once (force), clocks aside.
    site.clear()

    def _rows(path, data=None, **k):
        site.append((path, data))
        if path.endswith("/credentials/pending"):
            return {"pending": [{"user_id": 1}]}
        return {"ok": True} if path == STATE else {}

    monkeypatch.setattr(cp, "_request", _rows)
    monkeypatch.setattr(cp, "process_pending",
                        lambda rows, store, **k: [{"user_id": 1, "ok": True, "action": "connect"}])
    cp.pull_and_apply(store=s)
    paths = [p for p, _ in site]
    assert paths.index("/api/bot/sync/credentials/ack") < paths.index(STATE)


def test_a_failing_report_never_breaks_the_pull(tmp_path, site, monkeypatch):
    monkeypatch.setattr(cp, "publish_sealing_key", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("report exploded")

    monkeypatch.setattr(cp, "report_held_venues", _boom)
    assert cp.pull_and_apply(store=_store(tmp_path)) == 0
