"""The operator's backfill closes the website's stuck calls, and writes only when told.

`scripts/signal_outcomes_backfill.py` reads the sealed calls the website still
holds as NEW/OPEN, asks the bot's own `signal_outcomes.resolve` what each did
on public hourly candles, and prints counts. Only `--apply` posts, only final
answers, in batches the website accepts, each with the answering bar's close as
its `resolved_at`. These drive it with a stand-in website and venue: nothing
here reaches a network, and no real secret is used.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from bot.core import signal_outcomes as so

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "signal_outcomes_backfill", ROOT / "scripts" / "signal_outcomes_backfill.py")
bf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bf          # a dataclass resolves its module by name
_spec.loader.exec_module(bf)

H = so.BAR_MS
T0 = so.parse_time_ms("2026-10-01T00:00:00+00:00")
CREATED = T0 + 17 * 60_000                       # 00:17: the first bar read opens at 01:00
NOW = T0 + 400 * H
WINDOW = 4 * 3600


def _iso(ms):
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _call(key, symbol="BTC/USDT", created=CREATED, **kw):
    row = {"id": 0, "signal_key": key, "symbol": symbol, "direction": "LONG",
           "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
           "status": "NEW", "created_at": _iso(created), "sealed_at": _iso(created + 5_000)}
    row.update(kw)
    return row


def _bar(i, o, h, lo, c, base=T0):
    return [base + i * H, o, h, lo, c, 1.0]


#: Reaches the 100 entry on the 01:00 bar and the 110 target on the 02:00 bar.
WINS = [_bar(0, 102, 103, 101, 102), _bar(1, 102, 102, 99.5, 100), _bar(2, 100, 111, 99.8, 110)]
#: Never trades down to the entry: EXPIRED once the bars cover 04:17.
NEVER = [_bar(i, 103, 104, 102, 103) for i in range(0, 10)]


def _pages(rows, size=2):
    """A stand-in for GET /signals/pending: keyset pages of ``size``."""
    for i, r in enumerate(rows, start=1):
        r["id"] = i
    asked = []

    def fetch(after):
        asked.append(after)
        page = [r for r in rows if r["id"] > after][:size]
        return {"ok": True, "signals": page,
                "next_after_id": page[-1]["id"] if len(page) == size else None}
    return fetch, asked


def _bars_by_market(table):
    calls = []

    def fetch(market, since):
        calls.append((market, since))
        got = table[market]
        if isinstance(got, BaseException):
            raise got
        return got
    return fetch, calls


def _run(rows, table, *, apply, post_ok=True, now=NOW):
    posted = []
    fetch_page, _ = _pages(rows)
    fetch_bars, asked = _bars_by_market(table)

    def post(batch):
        posted.append(batch)
        return post_ok
    s = bf.run(fetch_page, fetch_bars, post, now_ms=now, window_s=WINDOW, apply=apply)
    return s, posted, asked


def test_a_dry_run_reads_and_answers_but_sends_nothing():
    rows = [_call("w1"), _call("w2"), _call("x1", symbol="ETH/USDT")]
    s, posted, asked = _run(rows, {"BTC/USDT": WINS, "ETH/USDT": NEVER}, apply=False)
    assert posted == [], "a dry run posted"
    assert s.read == 3 and s.markets == 2 and s.markets_read == 2
    assert s.answered == {so.TARGET: 2, so.EXPIRED: 1}
    assert s.to_post == 3 and s.posted == 0
    # One venue read per market, from an hour before its oldest call.
    assert sorted(asked) == [("BTC/USDT", CREATED - H), ("ETH/USDT", CREATED - H)]


def test_apply_posts_final_answers_with_the_answering_bars_close():
    rows = [_call("w1"), _call("x1", symbol="ETH/USDT")]
    s, posted, _ = _run(rows, {"BTC/USDT": WINS, "ETH/USDT": NEVER}, apply=True)
    sent = {r["signal_key"]: r for batch in posted for r in batch}
    assert sent["w1"]["status"] == so.TARGET and sent["w1"]["pnl"] == 2.0
    assert sent["w1"]["resolved_at"] == _iso(T0 + 3 * H), "the close of the 02:00 bar"
    assert sent["x1"]["status"] == so.EXPIRED and sent["x1"]["pnl"] is None
    assert sent["x1"]["resolved_at"] == _iso(CREATED + WINDOW * 1000), "04:17, the window's end"
    # The call's own fields travel with it, so the upsert can match it.
    assert {sent["w1"][k] for k in ("symbol", "direction")} == {"BTC/USDT", "LONG"}
    assert s.landed == 2 and s.sealed_after_outcome == 0


def test_batches_are_at_most_five_hundred_and_a_refused_batch_is_not_counted_landed():
    rows = [_call(f"w{i}") for i in range(1200)]
    s, posted, _ = _run(rows, {"BTC/USDT": WINS}, apply=True)
    assert [len(b) for b in posted] == [500, 500, 200]
    assert s.landed == 1200
    s, posted, _ = _run([_call(f"w{i}") for i in range(10)], {"BTC/USDT": WINS},
                        apply=True, post_ok=False)
    assert s.posted == 10 and s.landed == 0


def test_an_unlisted_market_and_history_that_does_not_reach_back_are_unscored():
    late = [_bar(i, 101, 102, 99, 101) for i in range(5, 40)]       # starts 04:00, call at 00:17
    rows = [_call("u1", symbol="GONE/USDT"), _call("h1", symbol="OLD/USDT"),
            _call("n1", symbol="B T C")]
    s, posted, _ = _run(rows, {"GONE/USDT": bf.MarketUnlisted("GONE/USDT"),
                               "OLD/USDT": late}, apply=True)
    sent = {r["signal_key"]: r for batch in posted for r in batch}
    assert {k: v["status"] for k, v in sent.items()} == {
        "u1": so.UNSCORED, "h1": so.UNSCORED, "n1": so.UNSCORED}
    assert all(v["resolved_at"] == "" and v["pnl"] is None for v in sent.values())
    assert s.markets_unlisted == 1


def test_candles_that_did_not_read_and_calls_still_pending_are_not_sent():
    young = _call("y1", symbol="ETH/USDT", created=NOW - 2 * H)
    rows = [_call("e1", symbol="DOWN/USDT"), young,
            _call("r1", symbol="BAD/USDT")]
    quiet = [_bar(i, 103, 104, 102, 103, base=NOW - 3 * H) for i in range(3)]
    s, posted, _ = _run(rows, {"DOWN/USDT": TimeoutError("venue down"),
                               "ETH/USDT": quiet,
                               "BAD/USDT": [_bar(1, 102, 102, 99.5, 100), [T0 + 2 * H]]},
                        apply=True)
    assert posted == []
    assert (s.markets_unread, s.unread_calls, s.still_pending, s.retry) == (1, 1, 1, 1)
    assert s.to_post == 0


def test_a_pending_read_that_failed_is_nothing_known_and_nothing_sent():
    posted = []
    s = bf.run(lambda after: None, lambda m, since: [], posted.append,
               now_ms=NOW, window_s=WINDOW, apply=True)
    assert s is None and posted == []
    assert "could not be read" in bf.render(None, apply=True)


def test_every_page_of_pending_calls_is_read():
    rows = [_call(f"w{i}") for i in range(7)]
    fetch_page, asked = _pages(rows, size=3)
    s = bf.run(fetch_page, lambda m, since: WINS, lambda b: True,
               now_ms=NOW, window_s=WINDOW, apply=False)
    assert s.read == 7 and asked == [0, 3, 6]


def test_the_summary_carries_counts_only():
    rows = [_call("SECRETKEY1", symbol="ZZTOP/USDT")]
    s, _, _ = _run(rows, {"ZZTOP/USDT": WINS}, apply=False)
    text = bf.render(s, apply=False)
    for leak in ("SECRETKEY1", "ZZTOP", "100.0", "110"):
        assert leak not in text, leak
    assert "would post: 1" in text and "dry run" in text


@pytest.mark.parametrize("seed", range(4))
def test_the_bars_handed_to_resolve_answer_what_the_whole_series_answers(seed):
    rng = random.Random(seed)
    price = 100.0
    bars = []
    for i in range(-50, 700):
        o = price
        price += rng.uniform(-1.5, 1.5)
        bars.append([T0 + i * H, o, max(o, price) + rng.uniform(0, 1),
                     min(o, price) - rng.uniform(0, 1), price, 1.0])
    keys = [b[0] for b in bars]
    span = (WINDOW + so.HORIZON_S + so.TAIL_GRACE_S) * 1000
    for _ in range(40):
        created = T0 + rng.randrange(0, 400 * H)
        mid = bars[(created - T0) // H + 50][1]
        row = _call("k", created=created, entry_price=mid - 0.5, stop_loss=mid - 4,
                    take_profit=mid + 4)
        whole = so.resolve(row, bars, NOW + 400 * H, WINDOW)
        sliced = so.resolve(row, bf._window(bars, keys, created, span), NOW + 400 * H, WINDOW)
        assert sliced == whole


# ── main ────────────────────────────────────────────────────────────────────

def test_apply_is_refused_on_the_server_before_anything_is_read(monkeypatch, capsys):
    from bot.utils import website_sync as ws
    monkeypatch.setenv("RUNECLAW_SERVER", "1")
    monkeypatch.setattr(ws, "fetch_pending_signals",
                        lambda *a, **k: pytest.fail("read on a refused run"))
    assert bf.main(["--apply"]) == bf.EXIT_REFUSED
    assert "operator-only" in capsys.readouterr().out


def test_main_dry_runs_by_default_and_writes_only_with_apply(monkeypatch, capsys):
    from bot.utils import website_sync as ws
    monkeypatch.delenv("RUNECLAW_SERVER", raising=False)
    rows = [_call("w1")]
    fetch_page, _ = _pages(rows)
    monkeypatch.setattr(ws, "fetch_pending_signals", lambda after, limit, older: fetch_page(after))
    monkeypatch.setattr(bf, "_venue_bars", lambda now_ms: (lambda m, since: WINS))
    sent = []
    monkeypatch.setattr(ws, "sync_signals", lambda batch: sent.append(batch) or True)
    assert bf.main([]) == bf.EXIT_OK
    assert sent == [] and "would post: 1" in capsys.readouterr().out
    assert bf.main(["--apply"]) == bf.EXIT_OK
    assert [r["signal_key"] for b in sent for r in b] == ["w1"]
    monkeypatch.setattr(ws, "fetch_pending_signals", lambda *a: None)
    assert bf.main([]) == bf.EXIT_UNREAD


# ── the read it pages ───────────────────────────────────────────────────────

class _Site(BaseHTTPRequestHandler):
    answers: list = []
    seen: list = []

    def do_GET(self):  # noqa: N802 -- http.server's spelling
        type(self).seen.append((self.path, self.headers.get("X-Bot-Secret")))
        code, body = type(self).answers.pop(0)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


def test_the_pending_read_sends_the_bot_secret_and_never_reads_a_failure_as_empty(monkeypatch):
    from bot.utils import website_sync as ws
    srv = HTTPServer(("127.0.0.1", 0), _Site)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setattr(ws, "WEBSITE_URL", f"http://127.0.0.1:{srv.server_port}")
        monkeypatch.setenv("BOT_SYNC_SECRET", "test-only-not-a-secret")
        _Site.seen = []
        _Site.answers = [
            (200, json.dumps({"ok": True, "signals": [{"signal_key": "a"}], "next_after_id": 9})),
            (503, json.dumps({"error": "x"})),
            (200, json.dumps({"ok": True})),
            (200, "not json"),
        ]
        got = ws.fetch_pending_signals(4, 2, 600, timeout=5)
        assert got["next_after_id"] == 9 and got["signals"] == [{"signal_key": "a"}]
        path, secret = _Site.seen[0]
        assert path == "/api/bot/sync/signals/pending?after_id=4&limit=2&older_than_s=600"
        assert secret == "test-only-not-a-secret"
        assert ws.fetch_pending_signals(timeout=5) is None          # 503
        assert ws.fetch_pending_signals(timeout=5) is None          # no list of signals
        assert ws.fetch_pending_signals(timeout=5) is None          # not JSON
    finally:
        srv.shutdown()
