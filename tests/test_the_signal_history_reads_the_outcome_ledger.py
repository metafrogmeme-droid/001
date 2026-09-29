"""`/signals` and the bot's `/api/signals` read the published-signal ledger.

Both read `SignalTracker`, an in-memory store nothing recorded into: its
`record_signal` and `record_outcome` had no caller outside tests. So `/signals`
answered "No signals recorded yet" whatever the bot had published, and the
dashboard server's `/api/signals` read `engine.signal_tracker`, which the engine
does not have, and answered an empty list forever. The ledger the engine
resolves on hourly candles holds every published call and what it did; both
readers ask one summary of it, and the tracker is gone.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import bot.core.signal_outcomes as so
from bot.formatters import signal_history_card as card

REPO = Path(__file__).resolve().parents[1]
NOW_MS = int(time.time() * 1000)


def _row(key, symbol, direction="LONG", created_ms=None):
    at = datetime.fromtimestamp((created_ms or NOW_MS - 3_600_000) / 1000, UTC).isoformat()
    return {"signal_key": key, "symbol": symbol, "direction": direction,
            "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
            "status": "NEW", "created_at": at}


def _ledger(tmp_path, monkeypatch):
    path = tmp_path / "signal_outcomes.json"
    monkeypatch.setattr(so, "ledger_path", lambda: path)
    so.record_published([
        _row("a1", "BTC/USDT", created_ms=NOW_MS - 5 * 3_600_000),
        _row("a2", "BTC/USDT"), _row("a3", "BTC/USDT"),
        _row("b1", "ETH/USDT", "SHORT"), _row("b2", "ETH/USDT", "SHORT"),
        _row("c1", "SOL/USDT"),
    ])
    so.apply("a1", so.Resolution(so.TARGET, "t", r=2.0, resolved_ms=NOW_MS), NOW_MS)
    so.apply("a2", so.Resolution(so.STOP, "s", r=-1.0, resolved_ms=NOW_MS), NOW_MS)
    so.apply("b1", so.Resolution(so.EXPIRED, "x", resolved_ms=NOW_MS), NOW_MS)
    so.apply("b2", so.Resolution(so.TARGET, "t", r=1.5, resolved_ms=NOW_MS), NOW_MS)
    return path


# ── the summary ───────────────────────────────────────────────────────────────

def test_the_summary_counts_each_word_and_the_counts_close(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    s = so.ledger_summary()
    btc, eth, sol = (s["pairs"][k] for k in ("BTC/USDT", "ETH/USDT", "SOL/USDT"))
    assert (btc["calls"], btc["target"], btc["stop"], btc["other"], btc["open"]) == (3, 1, 1, 0, 1)
    assert (eth["calls"], eth["target"], eth["stop"], eth["other"], eth["open"]) == (2, 1, 0, 1, 0)
    assert (sol["calls"], sol["open"]) == (1, 1)
    for p in s["pairs"].values():
        assert p["target"] + p["stop"] + p["other"] + p["open"] + p["unknown"] == p["calls"]
    assert (btc["r_sum"], btc["r_n"]) == (1.0, 2)
    assert (s["total"]["calls"], s["total"]["r_n"]) == (6, 3)
    assert s["total"]["r_sum"] == pytest.approx(2.5)
    assert s["oldest_ms"] == pytest.approx(NOW_MS - 5 * 3_600_000, abs=1000)
    assert (s["keep_resolved_s"], s["max_rows"]) == (so.KEEP_RESOLVED_S, so.MAX_ROWS)


def test_an_unreadable_ledger_is_none_and_a_fresh_one_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "signal_outcomes.json"
    monkeypatch.setattr(so, "ledger_path", lambda: path)
    fresh = so.ledger_summary()
    assert fresh is not None and fresh["pairs"] == {} and fresh["total"]["calls"] == 0
    path.write_text("{not json", encoding="utf-8")
    assert so.ledger_summary() is None


def test_an_r_that_is_not_a_number_or_not_scored_is_not_averaged(tmp_path):
    path = tmp_path / "l.json"
    sigs = {
        "k1": {"row": {"symbol": "X/USDT"}, "status": "TARGET", "r": True},
        "k2": {"row": {"symbol": "X/USDT"}, "status": "TARGET", "r": "nan"},
        "k3": {"row": {"symbol": "X/USDT"}, "status": "EXPIRED", "r": 5.0},
        "k4": {"row": {"symbol": "X/USDT"}, "status": "STOP", "r": -1.0},
        "k5": {"row": {"symbol": "X/USDT"}, "status": "CLOSED", "r": 9.0},
        "k6": "not a record",
    }
    path.write_text(json.dumps({"signals": sigs}), encoding="utf-8")
    s = so.ledger_summary(path)
    x = s["pairs"]["X/USDT"]
    assert (x["r_sum"], x["r_n"]) == (-1.0, 1)
    assert (x["calls"], x["target"], x["stop"], x["other"], x["unknown"]) == (5, 2, 1, 1, 1)
    assert s["skipped"] == 1


# ── the card ──────────────────────────────────────────────────────────────────

def _grouping():
    return (lambda entries, key: {"Crypto": list(entries)}, lambda c: "🪙", lambda s: "Crypto")


def test_the_card_prints_the_record_and_says_what_it_is(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    out = card.render(so.ledger_summary(), _grouping())
    assert "No signals recorded yet" not in out
    btc = next(line for line in out.splitlines() if line.startswith(" BTC "))
    assert btc.split() == ["BTC", "3", "1", "1", "0", "1", "50%", "+0.50"]
    sol = next(line for line in out.splitlines() if line.startswith(" SOL "))
    assert sol.split() == ["SOL", "1", "0", "0", "0", "1", "—", "—"]
    assert "All: 6 call(s) · 2 target / 1 stop (hit 67% of the 3 that reached a level)" in out
    assert "avg R +0.83 over 3" in out
    assert "gross of" in out and "−1R" in out
    assert "the 6 call(s) the ledger holds" in out and "kept 14 days" in out
    assert "not necessarily the whole history" in out
    assert "does not know" not in out and "could not be read" not in out


def test_an_unreadable_ledger_is_said_and_never_shown_as_empty():
    out = card.render(None)
    assert "could not be read" in out and "not an empty record" in out
    assert "No published signal" not in out and "<pre>" not in out


def test_an_empty_ledger_says_so():
    s = {"pairs": {}, "total": {k: 0 for k in so.SUMMARY_COUNTS} | {"r_sum": 0.0, "r_n": 0},
         "oldest_ms": None, "skipped": 0, "keep_resolved_s": 1, "max_rows": 1}
    out = card.render(s)
    assert card.EMPTY in out and "<pre>" not in out and "could not be read" not in out
    s["skipped"] = 2
    assert "2 ledger row(s) could not be read" in card.render(s)


def test_a_symbol_is_escaped_and_an_unknown_word_is_named(tmp_path):
    path = tmp_path / "l.json"
    sigs = {"k1": {"row": {"symbol": "<b>X</b>"}, "status": "CLOSED"}}
    path.write_text(json.dumps({"signals": sigs}), encoding="utf-8")
    out = card.render(so.ledger_summary(path), _grouping())
    assert "<b>X" not in out.replace("<b>📊", "").replace("<b>Crypto", "")
    assert "1 call(s) carry a word this build does not know" in out
    assert "oldest published not on record" in out


# ── the two readers ───────────────────────────────────────────────────────────

class _Host:
    def __init__(self):
        self.sent = []

    async def _guard(self, update, command, ctx=None):
        return True

    async def _send(self, update, text, **kw):
        self.sent.append(text)


def test_slash_signals_sends_the_ledger_card(tmp_path, monkeypatch):
    from bot.skills.portfolio_commands import PortfolioCommands
    _ledger(tmp_path, monkeypatch)
    host = _Host()
    asyncio.run(PortfolioCommands._cmd_signals(host, object(), object()))
    assert len(host.sent) == 1
    assert "All: 6 call(s)" in host.sent[0] and "No signals recorded yet" not in host.sent[0]


def test_slash_signals_says_an_unreadable_ledger(tmp_path, monkeypatch):
    from bot.skills.portfolio_commands import PortfolioCommands
    path = tmp_path / "signal_outcomes.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(so, "ledger_path", lambda: path)
    host = _Host()
    asyncio.run(PortfolioCommands._cmd_signals(host, object(), object()))
    assert card.UNREAD in host.sent[0]


def _dash_signals(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    import bot.web.dashboard_server as ds
    app = web.Application()
    app["engine"] = NS(user_portfolios=NS(all_portfolios=lambda: {}))
    resp = asyncio.run(ds.handle_signals(make_mocked_request("GET", "/api/signals", app=app)))
    return json.loads(resp.body)


def test_the_dashboard_api_reads_the_ledger(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    body = _dash_signals(monkeypatch)
    assert body["signals_read"] is True
    by = {r["symbol"]: r for r in body["signals"]}
    assert set(by) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    assert (by["BTC/USDT"]["calls"], by["BTC/USDT"]["target"]) == (3, 1)


def test_the_dashboard_api_says_an_unreadable_ledger(tmp_path, monkeypatch):
    path = tmp_path / "signal_outcomes.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(so, "ledger_path", lambda: path)
    body = _dash_signals(monkeypatch)
    assert body["signals_read"] is False and body["signals"] == []


def test_the_tracker_nothing_recorded_into_is_gone():
    assert not (REPO / "bot/core/signal_tracker.py").exists()
    for f in (REPO / "bot").rglob("*.py"):
        src = f.read_text(encoding="utf-8", errors="replace")
        assert "import SignalTracker" not in src and "signal_tracker." not in src, f
