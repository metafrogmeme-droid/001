"""Replaying recorded ideas through the AudaceFi publisher into a real loopback gateway.

`tests/fixtures/audacefi/recorded_ideas.jsonl` holds TradeIdeas the analyzer
produced from frozen SOL bars (`benchmark/kraken_ethsol_1h`, LLM off, clock
pinned) -- one LONG, one SHORT -- and hand-varied copies the publisher must
refuse: a BTC idea, a typed ticket, a stale bar, a re-offer.

The gateway is a real `http.server` bound to 127.0.0.1 on an ephemeral port,
so the bytes asserted are the bytes that crossed a socket. Every test points
the outbox at ``tmp_path``.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from bot.integrations.audacefi import publisher as pub
from bot.integrations.audacefi.outbox import AudaceOutbox
from bot.integrations.audacefi.signal_map import audacefi_signal_id
from bot.utils.models import TradeIdea

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "audacefi" / "recorded_ideas.jsonl"
NOW_MS = 1_780_000_000_000


def _recorded() -> list[tuple[str, TradeIdea]]:
    out = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        out.append((row["label"], TradeIdea.model_validate(row["idea"])))
    return out


def _by_label() -> dict[str, TradeIdea]:
    return dict(_recorded())


class _Gateway:
    """A loopback HTTP server that records each POST and what the outbox held
    at the moment the request arrived."""

    def __init__(self, outbox_path: Path, status: int = 200):
        self.requests: list[dict] = []
        self.status = status
        gw = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 -- the stdlib's name
                body = self.rfile.read(int(self.headers["Content-Length"]))
                held = outbox_path.read_text(encoding="utf-8") if outbox_path.exists() else ""
                gw.requests.append({"path": self.path, "body": body,
                                    "mode": self.headers.get("X-Audacefi-Mode"),
                                    "ctype": self.headers.get("Content-Type"),
                                    "outbox_at_request": held})
                self.send_response(gw.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("AUDACEFI_DRY_RUN_ENABLED", "true")
    for k in ("AUDACEFI_SIGNAL_TTL_S", "AUDACEFI_LONG_TARGET_BPS", "BAR_FRESHNESS_MAX_PERIODS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def gateway(tmp_path):
    gw = _Gateway(tmp_path / "outbox.jsonl")
    yield gw
    gw.close()


def _replay(tmp_path, port, *, now_ms=NOW_MS, outbox=None):
    box = outbox or AudaceOutbox(tmp_path / "outbox.jsonl")
    ideas = [i for _l, i in _recorded()]
    return box, pub.schedule_audacefi_dry_run(ideas, port=port, outbox=box, now_ms=now_ms,
                                              send_in_thread=False)


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


# ── off means off ────────────────────────────────────────────────────────────

def test_flag_off_touches_no_file_and_no_socket(monkeypatch, tmp_path):
    monkeypatch.delenv("AUDACEFI_DRY_RUN_ENABLED", raising=False)

    def _no(*_a, **_k):
        raise AssertionError("must not be reached with the flag off")
    monkeypatch.setattr(pub, "AudaceOutbox", _no)
    monkeypatch.setattr(pub, "post_to_loopback_gateway", _no)
    monkeypatch.setattr(pub, "_OUTBOX", None)
    assert pub.schedule_audacefi_dry_run([i for _l, i in _recorded()]) == []
    assert pub._OUTBOX is None


# ── the recorded ideas, replayed ─────────────────────────────────────────────

def test_only_the_sol_calls_are_sent_and_each_refusal_says_why(flag_on, tmp_path, gateway):
    _box, outcomes = _replay(tmp_path, gateway.port)
    ideas = _by_label()
    got = dict(outcomes)
    long_id = audacefi_signal_id(ideas["sol_long"].id, "long")
    flat_id = audacefi_signal_id(ideas["sol_short"].id, "flat")
    assert got[ideas["sol_long"].id] == long_id
    assert got[ideas["sol_short"].id] == flat_id
    assert got[ideas["btc_long"].id] == "not SOL ('BTC/USDT')"
    assert got[ideas["sol_manual"].id].startswith("not an analyzer idea")
    assert got[ideas["sol_stale"].id].startswith("bar freshness: newest closed 1h bar is 10800s old")
    assert got[ideas["sol_reoffer"].id] == "a re-offer of a call already published"
    assert [json.loads(r["body"])["id"] for r in gateway.requests] == [long_id, flat_id]


def test_the_bytes_on_the_wire_are_exactly_the_signal(flag_on, tmp_path, gateway):
    _replay(tmp_path, gateway.port)
    ideas = _by_label()
    expected = [
        {"expiresAtMs": NOW_MS + 60_000, "id": audacefi_signal_id(ideas["sol_long"].id, "long"),
         "sequence": 1, "side": "long", "symbol": "SOL", "targetBps": 10_000},
        {"expiresAtMs": NOW_MS + 60_000, "id": audacefi_signal_id(ideas["sol_short"].id, "flat"),
         "sequence": 2, "side": "flat", "symbol": "SOL", "targetBps": 0},
    ]
    assert [r["body"] for r in gateway.requests] == [
        json.dumps(e, separators=(",", ":"), sort_keys=True).encode() for e in expected]
    for r in gateway.requests:
        assert r["path"] == "/signals"
        assert r["mode"] == "dry-run"
        assert r["ctype"] == "application/json"
        assert len(r["body"]) <= 512


def test_every_signal_was_on_disk_before_it_was_sent(flag_on, tmp_path, gateway):
    _replay(tmp_path, gateway.port)
    assert gateway.requests
    for r in gateway.requests:
        sid = json.loads(r["body"])["id"]
        saved = [json.loads(x) for x in r["outbox_at_request"].splitlines() if x]
        assert {"id": sid, "state": "saved"}.items() <= next(
            row for row in saved if row["id"] == sid).items()
        assert not any(row["id"] == sid and row["state"] == "sent" for row in saved)


def test_a_replay_sends_nothing_new_and_keeps_its_ids_and_sequences(flag_on, tmp_path, gateway):
    box, first = _replay(tmp_path, gateway.port)
    sent = list(gateway.requests)
    _box, again = _replay(tmp_path, gateway.port, now_ms=NOW_MS + 5_000, outbox=box)
    assert again == first
    assert gateway.requests == sent                      # same process: nothing resent
    # ...and after a restart, from the file alone.
    restarted = AudaceOutbox(tmp_path / "outbox.jsonl")
    _box, after = _replay(tmp_path, gateway.port, now_ms=NOW_MS + 9_000, outbox=restarted)
    assert after == first
    assert gateway.requests == sent
    # Both saved before either was sent: the saves happen in the caller.
    assert [r["state"] for r in _rows(tmp_path / "outbox.jsonl")] == ["saved", "saved", "sent", "sent"]
    assert [r["sequence"] for r in _rows(tmp_path / "outbox.jsonl") if r["state"] == "saved"] == [1, 2]


def test_the_sequence_continues_after_a_restart(flag_on, tmp_path, gateway):
    box, _ = _replay(tmp_path, gateway.port)
    later = _by_label()["sol_long"].model_copy(update={"id": "TI-later001"})
    restarted = AudaceOutbox(tmp_path / "outbox.jsonl")
    pub.schedule_audacefi_dry_run([later], port=gateway.port, outbox=restarted,
                                  now_ms=NOW_MS + 3_600_000, send_in_thread=False)
    assert json.loads(gateway.requests[-1]["body"])["sequence"] == 3


# ── when the gateway is not there or says no ─────────────────────────────────

def test_a_refusing_gateway_leaves_saved_then_send_failed_and_a_replay_retries(flag_on, tmp_path):
    gw = _Gateway(tmp_path / "outbox.jsonl", status=503)
    try:
        box, _ = _replay(tmp_path, gw.port)
        rows = _rows(tmp_path / "outbox.jsonl")
        assert [r["state"] for r in rows] == ["saved", "saved", "send_failed", "send_failed"]
        assert {r.get("status") for r in rows if r["state"] == "send_failed"} == {503}
        gw.status = 200
        _replay(tmp_path, gw.port, now_ms=NOW_MS + 1_000, outbox=box)
        assert [r["state"] for r in _rows(tmp_path / "outbox.jsonl")][-2:] == ["sent", "sent"]
        assert len(gw.requests) == 4
    finally:
        gw.close()


def test_no_gateway_listening_is_recorded_by_class(flag_on, tmp_path):
    with socket.socket() as s:                      # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _replay(tmp_path, port)
    failed = [r for r in _rows(tmp_path / "outbox.jsonl") if r["state"] == "send_failed"]
    assert len(failed) == 2
    assert {r["error"] for r in failed} == {"ConnectionRefusedError"}


def test_a_signal_saved_but_never_sent_is_not_sent_after_it_expired(flag_on, tmp_path, gateway):
    # A process that saved and stopped before sending: the restart finds it.
    box = AudaceOutbox(tmp_path / "outbox.jsonl")
    from bot.integrations.audacefi.signal_map import audacefi_draft_for
    draft, _ = audacefi_draft_for(_by_label()["sol_long"], NOW_MS - 120_000)
    box.save_audacefi_draft(draft)
    restarted = AudaceOutbox(tmp_path / "outbox.jsonl")
    pub.schedule_audacefi_dry_run([_by_label()["sol_long"]], port=gateway.port,
                                  outbox=restarted, now_ms=NOW_MS, send_in_thread=False)
    assert gateway.requests == []
    assert [r["state"] for r in _rows(tmp_path / "outbox.jsonl")] == ["saved", "expired_unsent"]


# ── an outbox that cannot be read is not an empty one ────────────────────────

def test_a_torn_last_line_is_the_one_crash_it_forgives(flag_on, tmp_path, gateway):
    box, _ = _replay(tmp_path, gateway.port)
    with open(tmp_path / "outbox.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"id": "torn", "sta')                  # cut off mid-append
    restarted = AudaceOutbox(tmp_path / "outbox.jsonl")
    later = _by_label()["sol_long"].model_copy(update={"id": "TI-later002"})
    pub.schedule_audacefi_dry_run([later], port=gateway.port, outbox=restarted,
                                  now_ms=NOW_MS, send_in_thread=False)
    assert json.loads(gateway.requests[-1]["body"])["sequence"] == 3


def test_a_corrupt_line_refuses_publishing_rather_than_restarting_at_zero(
        flag_on, tmp_path, gateway, monkeypatch):
    _replay(tmp_path, gateway.port)
    path = tmp_path / "outbox.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(pub, "_OUTBOX", None)
    monkeypatch.setattr(pub, "AudaceOutbox", lambda: AudaceOutbox(path))
    before = len(gateway.requests)
    later = _by_label()["sol_long"].model_copy(update={"id": "TI-later003"})
    out = pub.schedule_audacefi_dry_run([later], port=gateway.port, now_ms=NOW_MS,
                                        send_in_thread=False)
    assert out == [("TI-later003", "outbox unreadable")]
    assert len(gateway.requests) == before


# ── the default path sends on a thread and never blocks its caller ───────────

def test_the_engine_path_sends_on_a_daemon_thread(flag_on, tmp_path, gateway):
    box = AudaceOutbox(tmp_path / "outbox.jsonl")
    pub.schedule_audacefi_dry_run([_by_label()["sol_long"]], port=gateway.port, outbox=box,
                                  now_ms=NOW_MS)
    for t in [t for t in threading.enumerate() if t.name.startswith("audacefi-")]:
        assert t.daemon
        t.join(timeout=10)
    assert [r["state"] for r in _rows(tmp_path / "outbox.jsonl")] == ["saved", "sent"]


def test_a_replay_while_a_send_is_in_flight_does_not_send_twice(flag_on, tmp_path):
    release = threading.Event()
    gw = _Gateway(tmp_path / "outbox.jsonl")
    handler_cls = gw.server.RequestHandlerClass
    real_post = handler_cls.do_POST

    def slow_post(handler):
        release.wait(timeout=10)
        real_post(handler)
    handler_cls.do_POST = slow_post
    try:
        box = AudaceOutbox(tmp_path / "outbox.jsonl")
        idea = _by_label()["sol_long"]
        pub.schedule_audacefi_dry_run([idea], port=gw.port, outbox=box, now_ms=NOW_MS)
        # The first send is held at the gateway; the same idea arrives again.
        pub.schedule_audacefi_dry_run([idea], port=gw.port, outbox=box, now_ms=NOW_MS + 10)
        release.set()
        for t in [t for t in threading.enumerate() if t.name.startswith("audacefi-")]:
            t.join(timeout=10)
        assert len(gw.requests) == 1
        assert [r["state"] for r in _rows(tmp_path / "outbox.jsonl")] == ["saved", "sent"]
    finally:
        release.set()
        gw.close()


# ── the engine hands over its fresh calls, and only those ────────────────────

def test_the_engine_hands_its_fresh_calls_to_the_publisher(monkeypatch):
    from types import SimpleNamespace

    import bot.core.agent_feed as feed
    import bot.core.signal_outcomes as so
    from bot.core.engine import RuneClawEngine

    ideas = _by_label()
    batch = [ideas["sol_long"], ideas["sol_short"], ideas["btc_long"]]
    # The ledger says the SHORT re-offers a call already pending.
    monkeypatch.setattr(so, "publish_signals",
                        lambda rows, producer="": {ideas["sol_short"].id: "TI-earlier"})
    monkeypatch.setattr(feed.FEED, "emit", lambda *_a, **_k: None)
    handed: list = []
    monkeypatch.setattr(pub, "schedule_audacefi_dry_run",
                        lambda calls, **_k: handed.append([c.id for c in calls]) or [])
    host = SimpleNamespace(_outcome_regime=lambda *_a, **_k: "UNKNOWN")
    RuneClawEngine._publish_engine_ideas(host, batch)
    assert handed == [[ideas["sol_long"].id, ideas["btc_long"].id]]
    assert ideas["sol_short"].repeat_of == "TI-earlier"


def test_a_publisher_that_raises_does_not_break_the_engines_publish(monkeypatch, caplog):
    from types import SimpleNamespace

    import bot.core.agent_feed as feed
    import bot.core.signal_outcomes as so
    from bot.core.engine import RuneClawEngine

    monkeypatch.setattr(so, "publish_signals", lambda rows, producer="": {})
    emitted: list = []
    monkeypatch.setattr(feed.FEED, "emit", lambda *a, **k: emitted.append(a))

    def _boom(*_a, **_k):
        raise RuntimeError("apiKey=SECRET")
    monkeypatch.setattr(pub, "schedule_audacefi_dry_run", _boom)
    host = SimpleNamespace(_outcome_regime=lambda *_a, **_k: "UNKNOWN")
    RuneClawEngine._publish_engine_ideas(host, [_by_label()["sol_long"]])
    assert emitted, "the thesis feed still ran"
    assert "SECRET" not in caplog.text
