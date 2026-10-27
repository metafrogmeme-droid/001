"""The telemetry stream: what it publishes, to whom, and what it refuses.

`bot/web/telemetry_stream.py` carries orderbook metrics and risk alerts to
token holders over a WebSocket. Four properties are claimed in its docstring and
each is driven here with both arms (the thing is done, and the thing it is
guarded against is refused):

  1. DOLLAR-FREE and SECRET-FREE on every frame (`encode_frame`).
  2. UNREADABLE IS NEVER ZERO: a book nobody answered is `read: false`, not a
     0 bps spread; an unreadable risk log is announced, not silent.
  3. AUTH is constant-time and total: nothing is subscribed, queued or polled
     before the token matches.
  4. A SLOW CLIENT IS DROPPED, ALONE: its own bounded queue fills, it is closed
     (1013), and every other connection carries on.

The risk-alert half is driven from lines the REAL risk engine and the REAL
logger wrote, not from a fixture's idea of them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from types import SimpleNamespace

import pytest

from bot.web import telemetry_stream as ts

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

TOKEN = "dash-token-0123456789"


def run(coro):
    return asyncio.run(coro)


def sym_ok(s: str) -> bool:
    import re
    return bool(re.match(r"^[A-Z0-9]{1,15}(/[A-Z0-9]{1,15})?$", s))


# ── 1. dollar-free, secret-free, bounded ─────────────────────────────────────


def test_amount_named_keys_are_dropped_and_ratios_survive():
    msg = {"type": "orderbook", "symbol": "BTC/USDT", "spread_bps": 1.2,
           "book_imbalance": 0.31, "bid_depth_usd": 120000.0, "mid_price": 63000.5,
           "nested": {"pnl": 4.0, "equity": 9.0, "ratio": 0.5}, "fee_paid": 1.0}
    out = ts.dollar_free(msg)
    assert out["spread_bps"] == 1.2 and out["book_imbalance"] == 0.31
    assert out["symbol"] == "BTC/USDT"
    assert out["nested"] == {"ratio": 0.5}
    for gone in ("bid_depth_usd", "mid_price", "fee_paid"):
        assert gone not in out


def test_a_string_carrying_an_amount_is_dropped_but_a_symbol_is_not():
    out = ts.dollar_free({"a": "equity $4,012 below floor", "b": "lost 4,012 USD today",
                          "c": "BTC/USDT", "d": "Daily loss"})
    assert "a" not in out and "b" not in out
    assert out["c"] == "BTC/USDT" and out["d"] == "Daily loss"


def test_a_non_finite_number_is_dropped_not_zeroed():
    out = ts.dollar_free({"a": float("nan"), "b": float("inf"), "c": 0.0, "d": [1.0, float("nan")]})
    assert "a" not in out and "b" not in out
    assert out["c"] == 0.0           # a real zero stays a zero
    assert out["d"] == [1.0]


def test_encode_frame_scrubs_a_credential_and_a_chat_id_in_a_string_field():
    token = "123456789:AAHfjdkslfjdkslfjdkslfjdkslfjdksl"
    frame = ts.encode_frame({"type": "risk", "note": f"send failed chat_id=987654321 token {token}"})
    assert frame is not None
    assert "987654321" not in frame and token not in frame
    assert json.loads(frame)["type"] == "risk"          # still valid JSON


def test_encode_frame_refuses_what_it_cannot_make_safe():
    assert ts.encode_frame({"big": "x" * (ts.MAX_FRAME_BYTES + 1)}) is None
    assert ts.encode_frame({"obj": object()}) is not None      # an unserialisable value is dropped...
    assert "obj" not in json.loads(ts.encode_frame({"obj": object(), "ok": 1}))
    assert json.loads(ts.encode_frame({"ok": 1})) == {"ok": 1}


def test_a_refused_frame_is_counted_and_reaches_nobody():
    hub = ts.TelemetryHub()
    sub = hub.attach({"risk"}, set())
    assert hub.publish("risk", {"big": "x" * (ts.MAX_FRAME_BYTES + 1)}) == 0
    assert hub.refused_frames == 1 and sub.queue.empty()


# ── 2. auth ──────────────────────────────────────────────────────────────────


def test_token_matches_both_arms():
    assert ts.token_matches(TOKEN, TOKEN) is True
    assert ts.token_matches(TOKEN + "x", TOKEN) is False
    assert ts.token_matches("", TOKEN) is False
    assert ts.token_matches(None, TOKEN) is False
    assert ts.token_matches(123, TOKEN) is False
    assert ts.token_matches(TOKEN, "") is False          # no token configured: nothing matches


def test_a_non_ascii_candidate_is_a_mismatch_not_a_crash():
    """`hmac.compare_digest(str, str)` raises TypeError on non-ASCII, which
    would turn a probe into a stack trace. Compared as bytes."""
    assert ts.token_matches("tökén-中文", TOKEN) is False
    assert ts.token_matches("tökén", "tökén") is True


def test_the_comparison_is_hmac_compare_digest(monkeypatch):
    seen = []
    real = ts.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ts.hmac, "compare_digest", spy)
    assert ts.token_matches(TOKEN, TOKEN)
    assert seen == [(TOKEN.encode(), TOKEN.encode())]


def test_bearer_parsing():
    assert ts.bearer_from_header("Bearer abc") == "abc"
    assert ts.bearer_from_header("bearer abc") == "abc"
    for bad in (None, "", "Bearer", "Bearer ", "Basic abc", "abc"):
        assert ts.bearer_from_header(bad) is None


# ── 3. the hub ───────────────────────────────────────────────────────────────


def test_publish_reaches_only_its_topic_and_its_symbol():
    async def go():
        hub = ts.TelemetryHub()
        risk = hub.attach({"risk"}, set())
        btc = hub.attach({"orderbook"}, {"BTC/USDT"})
        eth = hub.attach({"orderbook"}, {"ETH/USDT"})
        assert hub.publish("orderbook", {"type": "orderbook", "symbol": "BTC/USDT"}, symbol="BTC/USDT") == 1
        assert hub.publish("risk", {"type": "risk"}) == 1
        assert risk.queue.qsize() == 1 and btc.queue.qsize() == 1 and eth.queue.qsize() == 0
        assert hub.watched_symbols() == ["BTC/USDT", "ETH/USDT"]
    run(go())


def test_a_full_queue_drops_that_client_and_only_that_client():
    async def go():
        hub = ts.TelemetryHub(queue_size=2)
        slow = hub.attach({"risk"}, set())
        fast = hub.attach({"risk"}, set())
        got = []
        for i in range(6):
            hub.publish("risk", {"type": "risk", "n": i})
            while not fast.queue.empty():
                got.append(json.loads(fast.queue.get_nowait())["n"])
        assert got == [0, 1, 2, 3, 4, 5]                  # the good client lost nothing
        assert slow.closed_reason == "slow_client"
        assert slow.queue.qsize() == 1 and slow.queue.get_nowait() is None   # backlog gone, close queued
        assert hub.subscribers == 1 and hub.dropped_slow == 1
    run(go())


def test_the_hub_refuses_a_subscriber_past_capacity():
    hub = ts.TelemetryHub(max_subscribers=1)
    assert hub.attach({"risk"}, set()) is not None
    assert hub.attach({"risk"}, set()) is None


# ── 4. orderbook metrics: unreadable is never zero ───────────────────────────


def _signal(**kw):
    from bot.core.order_flow import OrderFlowSignal
    return OrderFlowSignal(symbol="BTC/USDT", **kw)


def test_a_read_book_publishes_ratios_and_never_an_amount():
    sig = _signal(components_ok=["book", "trades"], spread_bps=1.5, book_imbalance=0.2,
                  bid_depth_usd=300000.0, ask_depth_usd=200000.0,
                  bid_depth_top_usd=150000.0, ask_depth_top_usd=100000.0, mid_price=63000.0,
                  aggressor_ratio=0.6, cvd_trend="rising", whale_buy_usd=50000.0,
                  whale_bias="accumulation", confidence=0.8, cvd_window_usd=1234.0)
    frame = ts.orderbook_metrics(sig, "BTC/USDT")
    assert frame["read"] is True
    assert frame["spread_bps"] == 1.5 and frame["book_imbalance"] == 0.2
    assert frame["depth_ratio_top"] == 1.5
    assert frame["aggressor_ratio"] == 0.6 and frame["whale_bias"] == "accumulation"
    wire = ts.encode_frame(frame)
    assert wire is not None
    for amount in ("300000", "63000", "50000", "1234", "usd", "mid_price"):
        assert amount not in wire.lower() or amount == "usd" and "USDT" in wire
    assert "depth_ratio_top" in json.loads(wire)


def test_a_book_the_venue_never_answered_is_unread_not_a_zero_spread():
    sig = _signal(components_ok=["trades"])             # defaults: spread 0.0, imbalance 0.0
    frame = ts.orderbook_metrics(sig, "BTC/USDT")
    assert frame["read"] is False
    for key in ("spread_bps", "book_imbalance", "depth_ratio_top", "aggressor_ratio"):
        assert key not in frame


def test_an_imbalance_needs_depth_on_both_sides():
    sig = _signal(components_ok=["book"], spread_bps=2.0, bid_depth_usd=1000.0,
                  ask_depth_usd=0.0, bid_depth_top_usd=1000.0, ask_depth_top_usd=0.0)
    frame = ts.orderbook_metrics(sig, "BTC/USDT")
    assert frame["read"] is True and frame["spread_bps"] == 2.0
    assert "book_imbalance" not in frame and "depth_ratio_top" not in frame


def test_no_whales_means_no_whale_bias_not_neutral():
    sig = _signal(components_ok=["book", "trades"], spread_bps=1.0, bid_depth_usd=5.0,
                  ask_depth_usd=5.0, aggressor_ratio=0.5)
    frame = ts.orderbook_metrics(sig, "BTC/USDT")
    assert "whale_bias" not in frame


# ── 5. risk alerts, from lines the real engine wrote ─────────────────────────


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        from bot.utils.logger import _JSONFormatter
        self.lines.append(_JSONFormatter().format(record))


@pytest.fixture
def real_risk_lines(tmp_path):
    """The lines `RiskEngine.evaluate` really writes, collected off the risk
    channel, from a red-team run that rejects most of what it sends."""
    from bot.core.red_team import RedTeamEngine
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    from bot.utils.logger import risk_log

    handler = _Collect()
    risk_log.addHandler(handler)
    try:
        portfolio = PortfolioTracker()
        risk = RiskEngine(portfolio, state_file=str(tmp_path / "state.json"))
        RedTeamEngine(risk, portfolio).run_stress_test()
    finally:
        risk_log.removeHandler(handler)
    assert handler.lines, "the risk engine wrote nothing, so this fixture measures nothing"
    return handler.lines


def test_real_rejections_become_alerts_with_gate_names_and_no_amounts(real_risk_lines):
    alerts = [a for a in map(ts.risk_alert_from_line, real_risk_lines) if a]
    rejections = [a for a in alerts if a["kind"] == "rejection"]
    assert rejections, "no REJECTED line was recognised"
    assert any(a["gates"] for a in rejections)
    assert any("asset" in a for a in rejections)
    for a in rejections:
        wire = ts.encode_frame(a)
        assert wire is not None
        assert "$" not in wire
    # And the other arm: an APPROVED evaluation is not an alert.
    approved = [ln for ln in real_risk_lines if '"result": "APPROVED"' in ln and '"risk_check"' in ln]
    for ln in approved:
        assert ts.risk_alert_from_line(ln) is None


def test_the_reason_text_with_an_amount_is_never_forwarded():
    line = json.dumps({
        "ts": "2026-10-27T10:00:00.000000Z", "level": "INFO", "channel": "runeclaw.risk",
        "message": "Risk REJECTED for BTC/USDT [3P/1F]", "action": "risk_check",
        "result": "REJECTED",
        "data": {"checks_failed": ["Daily loss: equity $4,012 below the 5% floor"],
                 "reason": "Daily loss: equity $4,012 below the 5% floor"}})
    alert = ts.risk_alert_from_line(line)
    assert alert == {"kind": "rejection", "gates": ["Daily loss"], "asset": "BTC/USDT",
                     "type": "risk", "level": "INFO", "source_ts": "2026-10-27T10:00:00.000000Z"}
    assert "4,012" not in ts.encode_frame(alert)


def test_warnings_and_non_alerts():
    warn = json.dumps({"level": "WARNING", "action": "warning_rate_breaker", "result": "TRIPPED",
                       "message": "x $5", "data": {"count": 3}})
    alert = ts.risk_alert_from_line(warn)
    assert alert["kind"] == "warning" and alert["action"] == "warning_rate_breaker"
    assert "message" not in alert and "data" not in alert
    info = json.dumps({"level": "INFO", "action": "dd_recovery", "result": "ACTIVATED"})
    assert ts.risk_alert_from_line(info) is None
    for junk in ("", "not json", "[1,2]", "null", '{"level":"WARNING"}'):
        assert ts.risk_alert_from_line(junk) is None


# ── 6. the tailer ────────────────────────────────────────────────────────────


def _rej(n):
    return json.dumps({"level": "INFO", "action": "risk_check", "result": "REJECTED",
                       "message": f"Risk REJECTED for ASSET{n}/USDT [1P/1F]",
                       "data": {"checks_failed": [f"Gate{n}: detail"]}}) + "\n"


def test_the_tailer_starts_at_the_end_and_reads_what_is_appended(tmp_path):
    log = tmp_path / "risk.jsonl"
    log.write_text(_rej(0) + _rej(1))
    tail = ts.RiskLogTailer(log)
    assert tail.poll() == [] and tail.state == "ok"        # history is not replayed
    with open(log, "a") as fh:
        fh.write(_rej(2))
    alerts = tail.poll()
    assert [a["asset"] for a in alerts] == ["ASSET2/USDT"]
    assert tail.poll() == []                                # and not again


def test_a_half_written_line_waits_for_its_newline(tmp_path):
    log = tmp_path / "risk.jsonl"
    log.write_text("")
    tail = ts.RiskLogTailer(log)
    tail.poll()
    whole = _rej(7)
    with open(log, "a") as fh:
        fh.write(whole[:30])
    assert tail.poll() == []
    with open(log, "a") as fh:
        fh.write(whole[30:])
    assert [a["asset"] for a in tail.poll()] == ["ASSET7/USDT"]


def test_a_rotated_log_is_read_from_its_top(tmp_path):
    log = tmp_path / "risk.jsonl"
    log.write_text(_rej(0) * 5)
    tail = ts.RiskLogTailer(log)
    tail.poll()
    os.replace(log, tmp_path / "risk.jsonl.1")
    log.write_text(_rej(9))                                 # shorter, new inode
    assert [a["asset"] for a in tail.poll()] == ["ASSET9/USDT"]


def test_an_absent_log_is_absent_and_not_a_fault(tmp_path):
    tail = ts.RiskLogTailer(tmp_path / "nope.jsonl")
    assert tail.poll() == [] and tail.state == "absent"
    (tmp_path / "nope.jsonl").write_text("")
    tail.poll()
    assert tail.state == "ok"


def test_a_log_that_cannot_be_read_is_unreadable_not_quiet(tmp_path, monkeypatch):
    log = tmp_path / "risk.jsonl"
    log.write_text("")
    tail = ts.RiskLogTailer(log)
    tail.poll()

    def deny(*_a, **_k):
        raise PermissionError("no")

    monkeypatch.setattr(ts.os, "stat", deny)
    assert tail.poll() == [] and tail.state == "unreadable"


def test_a_burst_is_capped_and_says_so(tmp_path):
    log = tmp_path / "risk.jsonl"
    log.write_text("")
    tail = ts.RiskLogTailer(log, max_alerts=3)
    tail.poll()
    with open(log, "a") as fh:
        fh.write("".join(_rej(i) for i in range(10)))
    alerts = tail.poll()
    assert len(alerts) == 4
    assert alerts[-1] == {"type": "risk", "kind": "overflow", "skipped": 7}


def test_a_line_longer_than_the_read_is_skipped_not_waited_on_forever(tmp_path):
    log = tmp_path / "risk.jsonl"
    log.write_text("")
    tail = ts.RiskLogTailer(log, max_read_bytes=300)
    tail.poll()
    with open(log, "a") as fh:
        fh.write("x" * 600 + "\n" + _rej(1))
    seen = []
    for _ in range(12):
        seen += tail.poll()
    assert [a["asset"] for a in seen if "asset" in a] == ["ASSET1/USDT"]


# ── 7. the feeds ─────────────────────────────────────────────────────────────


def _feeds(hub, fetch, tailer=None, **kw):
    return ts.TelemetryFeeds(hub, fetch, tailer or SimpleNamespace(poll=lambda: [], state="ok"), **kw)


def test_the_orderbook_feed_publishes_a_frame_an_unread_frame_and_survives_a_raise():
    async def go():
        hub = ts.TelemetryHub()
        sub = hub.attach({"orderbook"}, {"BTC/USDT", "ETH/USDT", "SOL/USDT"})
        good = _signal(components_ok=["book"], spread_bps=1.0, bid_depth_usd=2.0, ask_depth_usd=2.0)

        async def fetch(symbol):
            if symbol == "BTC/USDT":
                return good
            if symbol == "ETH/USDT":
                return None
            raise RuntimeError("api_key=SECRETVALUE1234 leaked in a message")

        await _feeds(hub, fetch).poll_orderbooks_once()
        frames = {}
        while not sub.queue.empty():
            f = json.loads(sub.queue.get_nowait())
            frames[f["symbol"]] = f
        assert frames["BTC/USDT"]["read"] is True and frames["BTC/USDT"]["spread_bps"] == 1.0
        assert frames["ETH/USDT"] == {"read": False, "symbol": "ETH/USDT", "type": "orderbook",
                                      "ts": frames["ETH/USDT"]["ts"]}
        assert frames["SOL/USDT"]["read"] is False
        assert "SECRETVALUE" not in json.dumps(frames)
    run(go())


def test_a_symbol_past_the_process_cap_is_told_it_is_not_polled_not_left_silent():
    """Sixteen symbols, a cap of eight: the alphabetically late ones used to get
    an ack and then nothing, which reads as a quiet book. Each now gets one
    `read: false` frame that says it was not polled."""
    async def go():
        hub = ts.TelemetryHub()
        names = [f"S{i:02d}/USDT" for i in range(16)]
        subs = [hub.attach({"orderbook"}, set(names[i:i + 1])) for i in range(16)]
        polled: list[str] = []
        good = _signal(components_ok=["book"], spread_bps=1.0, bid_depth_usd=2.0, ask_depth_usd=2.0)

        async def fetch(symbol):
            polled.append(symbol)
            return good

        await _feeds(hub, fetch).poll_orderbooks_once()
        assert polled == names[:ts.MAX_POLLED_SYMBOLS]            # only the cap is read
        def drain(queue):
            out = []
            while not queue.empty():
                out.append(json.loads(queue.get_nowait()))
            return out

        by_symbol = {n: drain(sub.queue) for n, sub in zip(names, subs)}
        for n in names[:ts.MAX_POLLED_SYMBOLS]:
            (frame,) = by_symbol[n]
            assert frame["read"] is True and "polled" not in frame
        for n in names[ts.MAX_POLLED_SYMBOLS:]:
            (frame,) = by_symbol[n]
            assert frame["read"] is False and frame["polled"] is False
            assert frame["reason"] == "over_capacity" and "spread_bps" not in frame
    run(go())


def test_a_hung_venue_read_is_unread_after_its_timeout():
    async def go():
        hub = ts.TelemetryHub()
        sub = hub.attach({"orderbook"}, {"BTC/USDT"})

        async def hang(_symbol):
            await asyncio.sleep(30)

        await _feeds(hub, hang, call_timeout_s=0.05).poll_orderbooks_once()
        assert json.loads(sub.queue.get_nowait())["read"] is False
    run(go())


def test_the_risk_feed_announces_an_unreadable_source_once_and_its_recovery_once():
    hub = ts.TelemetryHub()
    sub = hub.attach({"risk"}, set())
    tailer = SimpleNamespace(state="ok", alerts=[], poll=None)
    tailer.poll = lambda: list(tailer.alerts)
    feeds = _feeds(hub, None, tailer)

    def drain():
        out = []
        while not sub.queue.empty():
            out.append(json.loads(sub.queue.get_nowait()))
        return out

    feeds.poll_risk_once()
    assert drain() == []
    tailer.state = "unreadable"
    feeds.poll_risk_once()
    feeds.poll_risk_once()
    assert [m["kind"] for m in drain()] == ["source_unreadable"]
    tailer.state = "ok"
    tailer.alerts = [{"type": "risk", "kind": "rejection", "gates": ["G"]}]
    feeds.poll_risk_once()
    assert [m["kind"] for m in drain()] == ["source_recovered", "rejection"]


def test_a_feed_runs_only_while_someone_listens_and_ends_after():
    async def go():
        hub = ts.TelemetryHub()
        calls = []

        async def fetch(symbol):
            calls.append(symbol)
            return None

        feeds = _feeds(hub, fetch, orderbook_interval_s=0.01, risk_interval_s=0.01)
        feeds.ensure_running()
        assert feeds.running() == {}                        # no subscriber, no task, no venue call
        sub = hub.attach({"orderbook"}, {"BTC/USDT"})
        feeds.ensure_running()
        await asyncio.sleep(0.1)
        assert calls and feeds.running()["orderbook"] is True
        hub.detach(sub)
        await asyncio.sleep(0.1)
        assert feeds.running()["orderbook"] is False        # it ended by itself
        n = len(calls)
        await asyncio.sleep(0.05)
        assert len(calls) == n
        await feeds.stop_all()
    run(go())


# ── 8. one connection ────────────────────────────────────────────────────────


class _Gone(Exception):
    pass


class FakeSocket:
    def __init__(self, headers=None, query=None, inbound=(), send_delay=0.0):
        self.headers = headers or {}
        self.query_params = query or {}
        self.accepted = False
        self.closed = None
        self.sent: list[dict] = []
        self.send_delay = send_delay
        self._in: asyncio.Queue = asyncio.Queue()
        for m in inbound:
            self._in.put_nowait(m)

    def say(self, obj):
        self._in.put_nowait(obj if isinstance(obj, str) else json.dumps(obj))

    def hang_up(self):
        self._in.put_nowait(_Gone())

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        item = await self._in.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, data):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(json.loads(data))

    async def close(self, code=1000, reason=None):
        self.closed = (code, reason)


class _NoFeeds:
    """Session tests ask about the socket, not about a venue: the feeds are
    driven on their own above."""

    def __init__(self):
        self.started = 0

    def ensure_running(self):
        self.started += 1


async def _session(ws, hub=None, feeds=None, token=TOKEN, **kw):
    hub = hub if hub is not None else ts.TelemetryHub()
    feeds = feeds if feeds is not None else _NoFeeds()
    await ts.serve_session(ws, hub=hub, feeds=feeds, expected_token=token, symbol_ok=sym_ok, **kw)
    return hub


def _types(ws):
    return [m["type"] for m in ws.sent]


def test_no_token_configured_closes_the_socket_and_subscribes_nothing():
    async def go():
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        hub = await _session(ws, token="")
        assert ws.closed[0] == ts.CLOSE_POLICY and ws.sent == [] and hub.subscribers == 0
    run(go())


def test_a_wrong_token_is_closed_before_anything_is_subscribed():
    async def go():
        for headers, first in (({"authorization": "Bearer nope"}, None),
                               ({}, {"op": "auth", "token": "nope"})):
            ws = FakeSocket(headers=headers)
            if first:
                ws.say(first)
            hub = await _session(ws)
            assert ws.closed == (ts.CLOSE_POLICY, "unauthorized")
            assert ws.sent == [] and hub.subscribers == 0
    run(go())


def test_the_header_and_the_first_message_each_authenticate():
    async def go():
        for kwargs, first in (
            ({"headers": {"authorization": f"Bearer {TOKEN}"}}, None),
            ({}, {"op": "auth", "token": TOKEN}),
        ):
            ws = FakeSocket(**kwargs)
            if first:
                ws.say(first)
            ws.say({"op": "ping"})
            ws.hang_up()
            await _session(ws)
            assert _types(ws)[:2] == ["hello", "pong"], (kwargs, ws.sent)
    run(go())


def test_a_token_in_the_url_is_refused_even_when_it_is_the_right_one():
    """uvicorn's access log and every proxy record the query string of a
    WebSocket path, and the dashboard token gates other endpoints too. The
    connection is closed before anything is subscribed, right token or not, and
    the right token in the URL does not authenticate."""
    async def go():
        for query in ({"token": TOKEN}, {"token": "nope"}, {"token": TOKEN, "topics": "risk"}):
            ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"}, query=query)
            ws.say({"op": "auth", "token": TOKEN})
            ws.hang_up()                      # a session that was let in ends here
            hub = await _session(ws)
            assert ws.closed == (ts.CLOSE_POLICY, "token in url refused"), query
            assert ws.sent == [] and hub.subscribers == 0
        # The other arm: the same socket with no token in the URL, and topics
        # in the query string, still connects.
        ok = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"}, query={"topics": "risk"})
        ok.hang_up()
        await _session(ok)
        assert _types(ok)[:1] == ["hello"]
    run(go())


def test_a_first_message_that_is_not_auth_or_never_comes_is_unauthenticated():
    async def go():
        ws = FakeSocket()
        ws.say({"op": "subscribe", "topics": ["risk"]})
        await _session(ws)
        assert ws.closed[0] == ts.CLOSE_POLICY and ws.sent == []
        silent = FakeSocket()
        await _session(silent, auth_timeout_s=0.05)
        assert silent.closed[0] == ts.CLOSE_POLICY and silent.sent == []
    run(go())


def test_subscribe_validates_topics_and_symbols_and_the_session_survives_a_bad_one():
    async def go():
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        ws.say({"op": "subscribe", "topics": ["wallet"]})
        ws.say({"op": "subscribe", "symbols": ["BTC/USDT/../x"]})
        ws.say({"op": "subscribe", "symbols": ["A", "B", "C", "D"]})
        ws.say({"op": "frobnicate"})
        ws.say({"op": "subscribe", "topics": ["orderbook"], "symbols": ["BTC/USDT"]})
        ws.hang_up()
        await _session(ws)
        assert [m.get("code") for m in ws.sent if m["type"] == "error"] == [
            "unknown_topic", "bad_symbol", "too_many_symbols", "bad_op"]
        assert ws.sent[-1] == {"type": "subscribed", "topics": ["orderbook"], "symbols": ["BTC/USDT"]}
    run(go())


def test_the_query_string_can_subscribe_and_a_bad_one_is_refused():
    async def go():
        ok = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"},
                        query={"topics": "orderbook,risk", "symbols": "BTC/USDT,ETH/USDT"})
        ok.hang_up()
        await _session(ok)
        assert ok.sent[0]["topics"] == ["orderbook", "risk"]
        assert ok.sent[0]["symbols"] == ["BTC/USDT", "ETH/USDT"]
        bad = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"}, query={"symbols": "not a symbol"})
        hub = await _session(bad)
        assert bad.closed[0] == ts.CLOSE_POLICY and hub.subscribers == 0
    run(go())


def test_a_client_that_talks_too_much_is_closed():
    async def go():
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        for _ in range(ts.CLIENT_MESSAGES_PER_WINDOW + 5):
            ws.say({"op": "ping"})
        hub = await _session(ws)
        assert ws.closed == (ts.CLOSE_POLICY, "client_rate") and hub.subscribers == 0
    run(go())


def test_an_oversized_client_message_is_closed():
    async def go():
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        ws.say("x" * (ts.MAX_CLIENT_MESSAGE_CHARS + 1))
        hub = await _session(ws)
        assert ws.closed == (ts.CLOSE_POLICY, "message_too_large") and hub.subscribers == 0
    run(go())


def test_a_connection_past_capacity_is_told_to_try_later():
    async def go():
        hub = ts.TelemetryHub(max_subscribers=1)
        hub.attach({"risk"}, set())
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        await _session(ws, hub=hub)
        assert ws.closed == (ts.CLOSE_TRY_LATER, "at capacity")
    run(go())


def test_a_slow_socket_is_dropped_and_a_good_one_keeps_receiving():
    async def go():
        hub = ts.TelemetryHub(queue_size=3)
        slow = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"}, send_delay=5.0)
        good = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"})
        t_slow = asyncio.ensure_future(_session(slow, hub=hub, send_timeout_s=0.05))
        t_good = asyncio.ensure_future(_session(good, hub=hub))
        await asyncio.sleep(0.05)
        assert hub.subscribers == 2
        for i in range(10):
            hub.publish("risk", {"type": "risk", "kind": "rejection", "n": i})
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)
        assert slow.closed is not None and slow.closed[0] == ts.CLOSE_TRY_LATER
        assert hub.dropped_slow >= 1
        assert [m["n"] for m in good.sent if m["type"] == "risk"] == list(range(10))
        assert good.closed is None                          # still connected
        good.hang_up()
        await asyncio.gather(t_slow, t_good)
        assert hub.subscribers == 0
    run(go())


def test_a_frame_published_while_connected_arrives_and_nothing_after_hangup():
    async def go():
        hub = ts.TelemetryHub()
        feeds = _NoFeeds()
        ws = FakeSocket(headers={"authorization": f"Bearer {TOKEN}"},
                        query={"topics": "orderbook", "symbols": "BTC/USDT"})
        task = asyncio.ensure_future(_session(ws, hub=hub, feeds=feeds))
        await asyncio.sleep(0.05)
        sig = _signal(components_ok=["book"], spread_bps=3.0, bid_depth_usd=1.0, ask_depth_usd=1.0)
        hub.publish("orderbook", ts.orderbook_metrics(sig, "BTC/USDT"), symbol="BTC/USDT")
        hub.publish("orderbook", ts.orderbook_metrics(sig, "ETH/USDT"), symbol="ETH/USDT")
        await asyncio.sleep(0.05)
        ws.hang_up()
        await task
        books = [m for m in ws.sent if m["type"] == "orderbook"]
        assert [b["symbol"] for b in books] == ["BTC/USDT"] and books[0]["spread_bps"] == 3.0
        assert hub.subscribers == 0
        assert hub.publish("orderbook", {"type": "orderbook"}, symbol="BTC/USDT") == 0
    run(go())
