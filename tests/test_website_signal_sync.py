"""
Website signal-stream sync (Stage 1a of the web signals/wallet feature).

The bot pushes every generated signal — taken or not — to the website's global
signal stream (POST /api/bot/sync/signals, UPSERT by signal_key). These cover the
pure payload shaping (scan entry_cards -> signal rows; TradeIdea -> signal row)
and that sync_signals posts the right envelope. No network: _post is stubbed.
"""

from types import SimpleNamespace

import bot.utils.website_sync as ws
from bot.skills.scan_skill import _scan_signal_rows


def _scan_payload():
    return {
        "regime": {"label": "TREND_UP"},
        "timestamp": "2026-06-30 19:30 UTC",
        "entry_cards": [
            {"symbol": "BTC", "direction": "LONG", "score": 0.72,
             "entry": "65000", "stop_loss": "64000", "tp1": "67000",
             "rr": "2.0", "trigger": "RSI 41, Vol 1.8x", "thesis": "long bias"},
            {"symbol": "ETH", "direction": "SHORT", "score": 0.61,
             "entry": "3200", "stop_loss": "3260", "tp1": "3080",
             "rr": "2.0", "trigger": "double top", "thesis": "short bias"},
        ],
    }


class TestScanSignalRows:
    def test_maps_each_card(self):
        rows = _scan_signal_rows(_scan_payload())
        assert len(rows) == 2
        btc = rows[0]
        assert btc["symbol"] == "BTC" and btc["direction"] == "LONG"
        assert btc["confidence"] == 0.72 and btc["score"] == 0.72
        assert btc["entry_price"] == 65000 and btc["stop_loss"] == 64000
        assert btc["take_profit"] == 67000 and btc["rr"] == 2.0
        assert btc["regime"] == "TREND_UP"
        assert btc["status"] == "NEW" and btc["pnl"] is None

    def test_signal_key_is_stable_per_symbol_dir_scan(self):
        rows1 = _scan_signal_rows(_scan_payload())
        rows2 = _scan_signal_rows(_scan_payload())
        # Same scan timestamp -> identical keys (re-push UPSERTs, no duplicate).
        assert rows1[0]["signal_key"] == rows2[0]["signal_key"]
        assert rows1[0]["signal_key"] != rows1[1]["signal_key"]  # per symbol/dir

    def test_skips_cards_missing_symbol_or_direction(self):
        p = _scan_payload()
        p["entry_cards"].append({"direction": "LONG", "entry": "1"})  # no symbol
        p["entry_cards"].append({"symbol": "SOL", "entry": "1"})       # no direction
        assert len(_scan_signal_rows(p)) == 2

    def test_empty_payload_is_empty(self):
        assert _scan_signal_rows({}) == []


class TestBuildSignalPayload:
    def test_from_trade_idea_like(self):
        idea = SimpleNamespace(
            asset="BTC/USDT", direction=SimpleNamespace(value="LONG"),
            confidence=0.8, entry_price=65000.0, stop_loss=64000.0,
            take_profit=67000.0, risk_reward_ratio=2.0, reasoning="setup")
        # direction str() of SimpleNamespace isn't "LONG"; pass the enum-ish value
        idea.direction = "LONG"
        row = ws.build_signal_payload("k1", idea, score=0.8, regime="RANGE")
        assert row["signal_key"] == "k1"
        assert row["symbol"] == "BTC/USDT" and row["direction"] == "LONG"
        assert row["entry_price"] == 65000.0 and row["take_profit"] == 67000.0
        assert row["rr"] == 2.0 and row["regime"] == "RANGE"
        assert row["status"] == "NEW"

    def test_uses_the_same_pre_calibration_reading_as_signal_cards(self):
        idea = SimpleNamespace(
            asset="SUI/USDT", direction="LONG", confidence=0.31,
            blended_confidence_raw=0.70, entry_price=1.171,
            stop_loss=1.1535, take_profit=1.2552, reasoning="setup")
        row = ws.build_signal_payload("k-confidence", idea, score=0.70)

        assert row["confidence"] == 0.70
        assert row["score"] == 0.70

    def test_setup_dimensions_are_copied_or_left_absent(self):
        # Both arms. A recorded word is the word. A missing attribute, a
        # blank, and a number are not a setup — stringifying 1 would name a
        # group the idea never stored.
        idea = SimpleNamespace(
            asset="BTC/USDT", direction="LONG", confidence=0.8,
            entry_price=1.0, stop_loss=0.9, take_profit=1.2, reasoning="x",
            signal_type="vwap_reversion", timeframe="4h", source="rules",
        )
        row = ws.build_signal_payload("k-dims", idea, regime="TREND")
        assert row["signal_type"] == "vwap_reversion"
        assert row["timeframe"] == "4h"
        assert row["source"] == "rules"
        assert row["regime"] == "TREND"

        bare = SimpleNamespace(
            asset="ETH/USDT", direction="SHORT", confidence=0.5,
            entry_price=1.0, stop_loss=1.1, take_profit=0.8, reasoning="x",
        )
        missing = ws.build_signal_payload("k-bare", bare)
        assert missing["signal_type"] is None
        assert missing["timeframe"] is None
        assert missing["source"] is None

        weird = SimpleNamespace(
            asset="SOL/USDT", direction="LONG", confidence=0.5,
            entry_price=1.0, stop_loss=0.9, take_profit=1.2, reasoning="x",
            signal_type=1, timeframe="  ", source="",
        )
        blank = ws.build_signal_payload("k-blank", weird)
        assert blank["signal_type"] is None
        assert blank["timeframe"] is None
        assert blank["source"] is None

    def test_a_producer_default_is_not_a_recorded_source_or_regime(self):
        # #511: the analyzer built its TradeIdea with no ``source``, so it
        # carried the field default "unknown", and the risk engine's regime
        # before any measurement is "UNKNOWN". Both were published as
        # recorded dimensions, and every engine signal's setup cell was
        # filed under source "unknown". The real model, both arms.
        from bot.utils.models import (
            ANALYZER_SOURCE,
            REGIME_UNMEASURED,
            SOURCE_UNSTATED,
            Direction,
            TradeIdea,
        )
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                         stop_loss=95.0, take_profit=110.0, confidence=0.7, reasoning="r",
                         signal_type="vwap_reversion", timeframe="1h")
        assert idea.source == SOURCE_UNSTATED
        row = ws.build_signal_payload("k-default", idea, regime=REGIME_UNMEASURED)
        assert row["source"] is None
        assert row["regime"] == ""
        named = idea.model_copy(update={"source": ANALYZER_SOURCE})
        row2 = ws.build_signal_payload("k-named", named, regime="RANGE")
        assert row2["source"] == ANALYZER_SOURCE
        assert row2["regime"] == "RANGE"
        # The word is checked as written: another case is another word.
        row3 = ws.build_signal_payload("k-case", idea.model_copy(update={"source": "Unknown"}),
                                       regime="unknown")
        assert row3["source"] == "Unknown" and row3["regime"] == "unknown"

    def test_the_analyzer_names_its_own_producer(self):
        # Its TradeIdea( is inside a coroutine behind a thesis model; the
        # keyword is the shape a drive cannot reach cheaply. Raw source, AST.
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "bot/core/analyzer.py").read_text()
        calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "TradeIdea"]
        assert calls, "the analyzer builds a TradeIdea"
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            assert "source" in kw, f"TradeIdea( at line {call.lineno} names no producer"
            assert getattr(kw["source"], "id", None) == "ANALYZER_SOURCE"

    def test_the_website_reads_the_same_two_absence_words(self):
        # signal_analytics.js keeps a copy for rows stored before the bot
        # stopped publishing them. One reading: the copy equals the bot's.
        import re
        from pathlib import Path

        from bot.utils.models import REGIME_UNMEASURED, SOURCE_UNSTATED
        js = (Path(__file__).resolve().parent.parent / "app/lib/signal_analytics.js").read_text()
        got = dict(re.findall(r"const (SOURCE_UNSTATED|REGIME_UNMEASURED) = '([^']*)';", js))
        assert got == {"SOURCE_UNSTATED": SOURCE_UNSTATED, "REGIME_UNMEASURED": REGIME_UNMEASURED}

    def test_rr_computed_when_absent(self):
        idea = {"asset": "ETH/USDT", "direction": "LONG", "confidence": 0.5,
                "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
        row = ws.build_signal_payload("k2", idea)
        # reward 20 / risk 10 = 2.0
        assert row["rr"] == 2.0


class TestSyncSignalsPost:
    def test_posts_envelope(self, monkeypatch):
        captured = {}

        def _fake_post(path, data, **kw):
            captured["path"] = path
            captured["data"] = data
            return {"ok": True, "upserted": len(data.get("signals", []))}

        monkeypatch.setattr(ws, "_post", _fake_post)
        rows = _scan_signal_rows(_scan_payload())
        assert ws.sync_signals(rows) is True
        assert captured["path"] == "/api/bot/sync/signals"
        assert captured["data"]["signals"] == rows

    def test_empty_is_noop_true(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(ws, "_post", lambda p, d, **kw: called.__setitem__("n", called["n"] + 1))
        assert ws.sync_signals([]) is True
        assert called["n"] == 0
