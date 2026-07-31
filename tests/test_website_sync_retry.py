"""A 503 did not defer the trade sync — it deleted it.

`_post` had no retry. One transient 5xx and the payload was gone: the scan
and portfolio pushes heal themselves on the next cycle, but a trade event is
append-only and there is no next cycle for it. The website's trade history
simply lost a trade.

The obvious fix is the dangerous one. /api/bot/sync/trade-event inserts
unconditionally:

    INSERT INTO trades (...) VALUES (...)          -- on 'open'
    DELETE ... WHERE status='OPEN' LIMIT 1         -- on 'close'
    INSERT INTO trades (... status='CLOSED' ...)

so a retry of a delivery whose RESPONSE was merely lost appends a second
trade — a phantom position on `open`, and on `close` it consumes another
open row and manufactures a closed trade to sit beside it, corrupting the
P&L history with a trade that never happened.

    LOSING AN EVENT IS BAD. INVENTING ONE IS WORSE.

So retry is opt-in, defaults to off, and is only turned on where a second
delivery is harmless: endpoints that replace state wholesale, or a trade
event carrying an id the server dedupes on. And a 4xx is never retried — it
will not succeed the second time, and hammering it hides the real fault
(the live 2026-07-31 incident was an unset BOT_SYNC_SECRET).
"""
from __future__ import annotations

import urllib.error

import pytest

from bot.utils import website_sync as ws


class _Resp:
    def __init__(self, body=b'{"ok": true}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ws._t, "sleep", lambda *_: None)


def _http_error(code):
    return urllib.error.HTTPError("u", code, "boom", {}, None)


class _Spy:
    """Answers with a queue of outcomes and counts the attempts."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.payloads = []

    def __call__(self, req, timeout=None):
        self.calls += 1
        self.payloads.append(req.data)
        out = self.outcomes.pop(0) if self.outcomes else _Resp()
        if isinstance(out, Exception):
            raise out
        return out


class TestRetryIsOptIn:
    def test_the_default_makes_exactly_one_attempt(self, monkeypatch):
        # The default protects the non-idempotent endpoints. A caller that
        # has not made its payload replayable must not get a retry by
        # accident.
        spy = _Spy(_http_error(503))
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}) is None
        assert spy.calls == 1

    def test_opting_in_retries_up_to_the_limit(self, monkeypatch):
        spy = _Spy(_http_error(503), _http_error(503), _http_error(503))
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}, retries=2) is None
        assert spy.calls == 3

    def test_a_transient_failure_then_success_returns_the_body(self, monkeypatch):
        spy = _Spy(_http_error(503), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}, retries=2) == {"ok": True}
        assert spy.calls == 2

    def test_a_dropped_connection_is_retried(self, monkeypatch):
        spy = _Spy(OSError("connection reset"), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}, retries=2) == {"ok": True}
        assert spy.calls == 2


class TestA4xxIsNeverRetried:
    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_it_gives_up_immediately(self, monkeypatch, code):
        # Retrying a request the server will never accept is noise that hides
        # the real fault — the live 503 turned out to be an unset secret.
        spy = _Spy(_http_error(code), _Resp(), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}, retries=2) is None
        assert spy.calls == 1

    @pytest.mark.parametrize("code", [500, 502, 503, 504, 408, 429])
    def test_server_side_and_throttle_codes_are_retried(self, monkeypatch, code):
        spy = _Spy(_http_error(code), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws._post("/x", {}, retries=2) == {"ok": True}
        assert spy.calls == 2


class TestTheTradeEventIsReplayable:
    def test_every_attempt_carries_the_same_event_id(self, monkeypatch):
        # This is the whole safety argument. If a retry minted a NEW id the
        # server could not recognise it, and the retry would append a second
        # trade — the exact corruption the dedupe exists to prevent.
        import json
        spy = _Spy(_http_error(503), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        trade = {"asset": "BTC/USDT", "direction": "LONG", "entry_price": 1.0,
                 "quantity": 1.0, "commission": 0.0, "stop_loss": 0.9,
                 "take_profit": 1.1}
        ws.sync_trade_event(1, "open", trade, 100.0)
        ids = [json.loads(p.decode())["event_id"] for p in spy.payloads]
        assert len(ids) == 2
        assert ids[0] == ids[1], "a retry must replay the id, not mint one"

    def test_separate_events_get_separate_ids(self, monkeypatch):
        # A hash of the trade's fields would be stable across retries AND
        # collide across two genuinely identical trades, silently dropping
        # the second. A per-call uuid is unique where it must be.
        import json
        spy = _Spy(_Resp(), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        trade = {"asset": "BTC/USDT", "direction": "LONG", "entry_price": 1.0,
                 "quantity": 1.0, "commission": 0.0}
        ws.sync_trade_event(1, "open", trade, 100.0)
        ws.sync_trade_event(1, "open", trade, 100.0)
        ids = [json.loads(p.decode())["event_id"] for p in spy.payloads]
        assert ids[0] != ids[1]


class TestOnlyReplayableCallersOptIn:
    """A retry on an endpoint that cannot recognise a replay is the defect."""

    def _src(self):
        from tests.source_scan import code_only
        return code_only(
            open("bot/utils/website_sync.py", encoding="utf-8").read())

    def test_the_trade_event_opts_in_and_sends_an_id(self):
        src = self._src()
        i = src.index("/api/bot/sync/trade-event")
        window = src[i:i + 400]
        assert "retries=2" in window
        assert "event_id" in src[i - 400:i + 400], (
            "retry on this endpoint is only sound because of the id"
        )

    def test_the_replace_all_syncs_opt_in(self):
        src = self._src()
        for path in ('_post("/api/bot/sync", {', '"/api/bot/sync/scan"',
                     '"/api/bot/sync/signals"', '"/api/bot/sync/tiers"'):
            i = src.index(path)
            assert "retries=2" in src[i:i + 400], f"{path} should retry"
