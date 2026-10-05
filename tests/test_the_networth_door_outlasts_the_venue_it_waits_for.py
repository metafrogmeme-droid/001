"""The net-worth door's budget outlasts the chain it waits for, and a wait
that runs out is said as a wait.

PR 494 moved Telegram's /networth off the in-process `networth_reading`
(bounded by its own 25 s venue budget, rendered as its own line) onto the
website's card over the sync channel. That channel's `_request` gave every
request 15 s, while the card behind it calls back into the bot with a 30 s
budget around the same 25 s venue read. So a linked trader whose venue took
16-25 s -- the exact case the 25 s budget exists for -- was told "The web app
isn't reachable (or your account isn't linked) ... /link your account": a
wrong cause, and a door painted on a wall for a caller who is linked. The
website finished rendering after the bot had hung up (BrokenPipe in the
review's reproduction). Idle yield has the same shape.

The channel takes a budget per request now; the net-worth and idle-yield
cards get one that outlasts the chain (`WEB_CARD_TIMEOUT_S`); and a fetch
that runs out of its budget comes back as its own fact (`timed_out`), which
the seam renders as a wait, never as a dead channel and never as /link.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bot.core.networth_reading import BALANCE_TIMEOUT_S
from bot.skills.market_commands import MarketCommands
from bot.skills.telegram_handler import TelegramHandler
from bot.utils import credential_pull, web_data_pull


class _SlowCard(BaseHTTPRequestHandler):
    delay = 0.0
    hits: list = []

    def do_GET(self):  # noqa: N802
        _SlowCard.hits.append(self.path)
        import time
        time.sleep(_SlowCard.delay)
        body = json.dumps({"reply_html": "• <b>BITGET</b>: <b>$2,500.5</b>", "intent": "networth"}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def website(monkeypatch):
    """A stub website answering the net-worth card after `delay` seconds."""
    srv = HTTPServer(("127.0.0.1", 0), _SlowCard)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _SlowCard.hits = []
    monkeypatch.setattr(credential_pull, "WEBSITE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(credential_pull, "SYNC_SECRET", "s" * 48)
    monkeypatch.setattr(web_data_pull, "SYNC_SECRET", "s" * 48)
    yield srv
    srv.shutdown()
    srv.server_close()


# ── the budget ────────────────────────────────────────────────────────────

def test_the_card_budgets_outlast_the_reads_behind_them():
    """The website waits 30 s on the bot for these cards, and the bot's venue
    read inside that is 25 s. A budget under either hangs up on an answer
    that was coming."""
    for name in ("networth", "idleyield"):
        budget = web_data_pull.web_card_timeout_s(name)
        assert budget > 30.0, name
        assert budget > BALANCE_TIMEOUT_S, name
    # Every other card keeps the channel's default.
    assert web_data_pull.web_card_timeout_s("wallet") == credential_pull.REQUEST_TIMEOUT_S


def test_the_transport_honours_the_budget_it_is_handed(website):
    _SlowCard.delay = 1.0
    failure: dict = {}
    got = credential_pull._request("/api/bot/sync/card/networth?telegram_id=7",
                                   timeout=0.3, failure=failure)
    assert got is None
    assert failure == {"kind": "timeout"}
    # And answers within it.
    _SlowCard.delay = 0.0
    failure = {}
    got = credential_pull._request("/api/bot/sync/card/networth?telegram_id=7",
                                   timeout=5.0, failure=failure)
    assert isinstance(got, dict) and got.get("intent") == "networth"
    assert failure == {}


def test_a_refused_connection_is_an_error_not_a_timeout(monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(credential_pull, "WEBSITE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(credential_pull, "SYNC_SECRET", "s" * 48)
    failure: dict = {}
    assert credential_pull._request("/api/bot/sync/card/networth", timeout=2.0,
                                    failure=failure) is None
    assert failure == {"kind": "error"}


def test_is_timeout_reads_bare_and_wrapped_timeouts():
    assert credential_pull._is_timeout(socket.timeout("timed out"))
    assert credential_pull._is_timeout(TimeoutError())
    assert credential_pull._is_timeout(urllib.error.URLError(socket.timeout("timed out")))
    assert not credential_pull._is_timeout(urllib.error.URLError(ConnectionRefusedError()))
    assert not credential_pull._is_timeout(ValueError("x"))


# ── the fetch, and the seam's third sentence ──────────────────────────────

def test_a_fetch_that_runs_out_comes_back_as_a_wait(website, monkeypatch):
    monkeypatch.setitem(web_data_pull.WEB_CARD_TIMEOUT_S, "networth", 0.3)
    _SlowCard.delay = 1.0
    payload = web_data_pull.fetch_networth("7")
    assert payload == {"reply_html": None, "timed_out": 0.3}
    assert web_data_pull.web_card_timed_out(payload) == 0.3
    assert web_data_pull.web_card_text(payload) is None
    assert web_data_pull.web_card_unlinked(payload) is False
    # The other arm: an answer within the budget is the card.
    monkeypatch.setitem(web_data_pull.WEB_CARD_TIMEOUT_S, "networth", 5.0)
    _SlowCard.delay = 0.0
    payload = web_data_pull.fetch_networth("7")
    assert web_data_pull.web_card_timed_out(payload) is None
    assert "BITGET" in web_data_pull.web_card_text(payload)


def _seam() -> MarketCommands:
    h = TelegramHandler.__new__(TelegramHandler)
    return h


def test_the_seam_names_the_wait_and_not_a_dead_channel_or_a_link(website, monkeypatch):
    monkeypatch.setitem(web_data_pull.WEB_CARD_TIMEOUT_S, "networth", 0.3)
    _SlowCard.delay = 1.0
    h = _seam()
    for surface in ("telegram", "web"):
        out = asyncio.run(h._web_card_text("networth", surface=surface, telegram_id="7",
                                           keep_markup=(surface == "web")))
        assert "within 0.3 s" in out
        assert "slow right now" in out
        assert "Nothing was read" in out
        assert "/link" not in out
        assert "isn't reachable" not in out
    # Both arms: a channel that is DOWN still reads as the link hint, and
    # a card that arrives in time is the card.
    monkeypatch.setitem(web_data_pull.WEB_CARD_TIMEOUT_S, "networth", 5.0)
    _SlowCard.delay = 0.0
    assert "BITGET" in asyncio.run(h._web_card_text("networth", surface="telegram", telegram_id="7"))
    website.shutdown()
    website.server_close()
    out = asyncio.run(h._web_card_text("networth", surface="telegram", telegram_id="7"))
    assert out == TelegramHandler._WEB_LINK_HINT


def test_the_timeout_sentence_rounds_the_budget_it_names():
    assert "within 45 s" in TelegramHandler._timeout_hint("telegram", 45.0)
    assert "within 45 s" in TelegramHandler._timeout_hint("web", 45.0)
    assert "within its budget" in TelegramHandler._timeout_hint("telegram", 0.0)
    for s in (TelegramHandler._timeout_hint("telegram", 45.0), TelegramHandler._timeout_hint("web", 45.0)):
        assert "/link" not in s and "Nothing was read" in s
