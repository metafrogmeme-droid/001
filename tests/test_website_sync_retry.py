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
        # Pinned as "opts in", not as a literal count. This asserted
        # `retries=2` and so blocked raising the count on the cold-start
        # paths — where the third backoff gap is unreachable at 2, which is
        # exactly how a 43-second profile spent 13 seconds. What matters here
        # is that a replaceable payload retries at all; how far it retries
        # belongs with the profile that sizes it.
        import re
        src = self._src()
        for path in ('_post("/api/bot/sync", {', '"/api/bot/sync/scan"',
                     '"/api/bot/sync/signals"', '"/api/bot/sync/tiers"'):
            i = src.index(path)
            m = re.search(r"retries=(\d+)", src[i:i + 400])
            assert m and int(m.group(1)) >= 2, f"{path} should retry"


class TestColdStartOutlivesTheOutageItRetriesThrough:
    """`Synced` sat at 0 for a day and a half against a healthy website.

    The secret was right, the URL was right, `/api/bot/sync/signals` answered
    200 to a hand-rolled curl, and the retry was already opted in. What was
    wrong was its SIZE.

    The site is an ephemeral instance torn down after a short idle — its own
    `/api/version` reported `uptime_s` in the low hundreds with nobody
    restarting it — and a cold start answers the request that triggered it with
    a fast 503, then serves normally about thirty seconds later.
    `_RETRY_BACKOFF` is (0.5, 2.0, 5.0) and `retries=2` uses only the first two
    gaps, so all three attempts landed inside the first 2.5 SECONDS of a
    thirty-second warm-up. Every cycle woke the instance, gave up long before
    it was ready, and left it to go cold again — the only traffic that would
    have kept it warm being the traffic that had just given up.

    A retry that fires three times in two and a half seconds looks, in a log
    and in a diff, exactly like a retry that works. The budget has to be scaled
    to the outage it exists to survive.
    """

    def test_the_budget_outlasts_a_cold_start(self):
        """The two halves multiplied together, not each pinned to a literal.

        `retries=N` sleeps only N times — there is no gap after the final
        attempt — so the last entry of a backoff tuple is unreachable unless
        the retry count reaches it. The first version of this fix set a
        (3, 10, 30) profile and left `retries=2` at the call sites, spending
        13 of its 43 seconds against a thirty-second cold start: the defect
        being fixed, one size down, in the fix for it. It was caught only
        because this test reads the retry count from the CALL SITE instead of
        assuming it.
        """
        import re
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/website_sync.py", encoding="utf-8").read())
        i = src.index('"/api/bot/sync/signals"')
        m = re.search(r"retries=(\d+)", src[i:i + 400])
        assert m, "the signals push no longer declares its retry count here"
        used = ws._COLD_START_BACKOFF[:int(m.group(1))]
        assert sum(used) >= 30, (
            f"retries={m.group(1)} waits {sum(used)}s in total, which does not "
            "span the ~30s cold start it exists for — either the profile "
            "shrank or the retry count stopped reaching the end of it")
        assert sum(used) > sum(ws._RETRY_BACKOFF), (
            "the cold-start profile is no longer more patient than the "
            "trading-path one, so it is not a profile at all")

    def test_a_cold_start_is_survived_rather_than_reported(self, monkeypatch):
        # The behaviour, driven: two fast 503s (the instance warming) and then
        # the real answer. The old profile made exactly these three attempts
        # too — the fix is WHEN the third one happens, which is why the test
        # above pins the interval and this one pins the outcome.
        spy = _Spy(_http_error(503), _http_error(503), _Resp())
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws.sync_signals([{"signal_key": "k", "symbol": "BTC/USDT"}]) is True
        assert spy.calls == 3

    def test_it_still_gives_up_rather_than_hammering_forever(self, monkeypatch):
        # A patient retry is not an infinite one. A website that is genuinely
        # down must not accumulate threads that never finish.
        import re
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/website_sync.py", encoding="utf-8").read())
        i = src.index('"/api/bot/sync/signals"')
        attempts = int(re.search(r"retries=(\d+)", src[i:i + 400]).group(1)) + 1
        spy = _Spy(*[_http_error(503)] * (attempts + 3))
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws.sync_signals([{"signal_key": "k"}]) is False
        assert spy.calls == attempts, "the patient retry became an unbounded one"

    def test_a_4xx_is_still_not_retried_at_all(self, monkeypatch):
        # THE CONTROL that matters most. The previous incident on this exact
        # path was a 403 from a mismatched BOT_SYNC_SECRET, 9345 times. Waiting
        # 43 seconds to re-send a request that can never be accepted would turn
        # a loud misconfiguration into a slow one.
        spy = _Spy(_http_error(403))
        monkeypatch.setattr(ws.urllib.request, "urlopen", spy)
        assert ws.sync_signals([{"signal_key": "k"}]) is False
        assert spy.calls == 1

    @pytest.mark.parametrize("path", ['"/api/bot/sync/scan"',
                                      '"/api/bot/sync/signals"',
                                      '"/api/bot/sync/tiers"'])
    def test_every_off_thread_wholesale_push_is_patient(self, path):
        # The wiring, from the caller. Each of these reaches the network only
        # through an `*_in_background` helper on its own daemon thread, so the
        # "never delay an order" reasoning behind the short profile does not
        # apply to any of them — and the arena feed and the engine chip, the
        # two surfaces the operator actually noticed were stale, are the first
        # two in this list.
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/website_sync.py", encoding="utf-8").read())
        i = src.index(path)
        assert "_COLD_START_BACKOFF" in src[i:i + 400], (
            f"{path} is back on the trading-path backoff, which cannot outlast "
            "a cold start")

    def test_the_hot_and_non_replayable_paths_keep_the_short_profile(self):
        # And the ones that must NOT change: a trade event is append-only and
        # must never retry at all, and /sync is a person waiting on a Telegram
        # reply, where 43 seconds of patience is a hang.
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/website_sync.py", encoding="utf-8").read())
        for path in ('"/api/bot/sync/trade-event"', '_post("/api/bot/sync", {',
                     '"/api/bot/sync/telegram-unlink"'):
            i = src.index(path)
            assert "_COLD_START_BACKOFF" not in src[i:i + 400], (
                f"{path} was made patient — it is either non-idempotent or has "
                "a human waiting on it")

    def test_giving_up_says_how_long_it_waited(self):
        # "gave up after 3 attempts" is the same sentence whether the budget
        # was 2.5 seconds or 90, and that difference WAS the bug. A log line
        # that cannot distinguish the two costs another day and a half.
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/website_sync.py", encoding="utf-8").read())
        assert src.count("gave up after %d attempts over %.1fs") == 2, (
            "the give-up lines no longer report elapsed time")
        assert "_began = _t.monotonic()" in src
