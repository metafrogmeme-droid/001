"""/pro_scan grades each idea off its own asset, and publishes what it measured.

Driven on 2026-09-26:

* One asset read "⛔ NO-TRADE ZONE, Setup Quality 0/10" and its idea read
  "🎯 Execution Ready, Quality 9/10", because the idea's grade stamped
  midrange=False, volume confirmed and structure clear, and the asset's grade
  stamped a confidence of 0.5 and an R:R of 1.0.
* Fourteen rising closes (or a flat series) divided the RSI by zero and the
  whole scan failed.
* The website push carried `rsi: 50.0` for every symbol although the card had
  just measured it (BTC's card said RSI 0), `atr: 0`, and 24h dollar volume in
  millions under the name `vol_ratio` (850.0); the payload derived BTC's regime
  from the stamped RSI and built entry cards from a 2%-of-price "ATR"
  (trigger "RSI 50.0, Vol 850.0x").
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.utils.website_sync as website_sync
from bot.compat import UTC
from bot.skills import skill_registry as sr
from bot.skills.scan_skill import _build_scan_payload
from bot.utils.models import Direction, MarketSignal, TradeIdea

M15 = 15 * 60 * 1000


def _rows(kind: str, n: int = 100, null_close_at=None, volume=1000.0,
          null_volume_at=None) -> list:
    last_closed = (int(time.time() * 1000) // M15) * M15 - M15
    t0 = last_closed - (n - 1) * M15
    out, p = [], 100.0
    for i in range(n):
        if kind == "range":        # chop in a band, last close mid-range
            o, c = 100.0, 100.0 + (0.5 if i % 2 else -0.5)
            if i == n - 1:
                c = 100.02
        elif kind == "rising":     # every close up: no losing bar at all
            o, c = p, p * 1.003
            p = c
        elif kind == "falling":
            o, c = p, p * 0.997
            p = c
        else:                      # flat: no move at all
            o = c = 100.0
        row = [t0 + i * M15, o, max(o, c) + 0.1, min(o, c) - 0.1, c, volume]
        if i == null_close_at:
            row[4] = None
        if i == null_volume_at:
            row[5] = None
        out.append(row)
    return out


def _run(monkeypatch, kinds: dict, idea_for: dict, *, extra_signals=()):
    """The real ProScanSkill.execute over planted candles; returns the card
    (tags stripped) and the payload it pushed."""
    pushed: list = []
    monkeypatch.setattr(website_sync, "sync_scan_in_background", pushed.append)

    class _Ex:
        async def fetch_ohlcv(self, sym, tf, limit=100):
            spec = kinds[sym]
            if isinstance(spec, dict):
                return _rows(**spec)
            return _rows(*spec) if isinstance(spec, tuple) else _rows(spec)

    sigs = [MarketSignal(symbol=s, price=100.02, change_pct_24h=5.0, volume_usd_24h=8.5e8,
                         momentum_score=0.7, timestamp=datetime.now(UTC))
            for s in kinds] + list(extra_signals)

    class _Scanner:
        async def scan(self):
            return list(sigs)

        async def _get_exchange(self):
            return _Ex()

        async def _get_futures_exchange(self):
            return _Ex()

    async def _an(sig, timeframe="1h", **kw):
        return idea_for.get(sig.symbol)

    pf = SimpleNamespace(snapshot=lambda: SimpleNamespace(
        equity_usd=10000.0, open_positions=0, daily_pnl=0.0, max_drawdown_pct=0.0),
        _history=[], open_positions=[])
    eng = SimpleNamespace(scanner=_Scanner(), portfolio=pf, _analyze_signal=_an,
                          _pending_ideas={})
    out = asyncio.run(sr.ProScanSkill().execute(eng, mode="intraday"))
    return re.sub(r"<[^>]+>", "", out), (pushed[-1] if pushed else None)


def _idea(sym="SOL/USDT", conf=0.80):
    return TradeIdea(asset=sym, direction=Direction.LONG, entry_price=100.0, stop_loss=99.0,
                     take_profit=103.0, confidence=conf,
                     reasoning="[LLM|RANGE|x|intraday|C=0.70] thesis")


# ── F4: one asset, one grade ─────────────────────────────────────────────


def test_a_midrange_assets_idea_is_not_execution_ready(monkeypatch):
    card, _p = _run(monkeypatch, {"SOL/USDT": "range"}, {"SOL/USDT": _idea()})
    assert "NO-TRADE ZONE" in card
    assert "Execution Ready" not in card, (
        "the idea was graded with midrange stamped False, volume and structure "
        "stamped True")
    assert "No-Trade Zone" in card


def test_the_assets_grade_and_its_ideas_grade_are_one_reading(monkeypatch):
    card, _p = _run(monkeypatch, {"SOL/USDT": "range"}, {"SOL/USDT": _idea()})
    asset = re.search(r"Setup Quality: \S+ (\d+)/10 — (.+)", card)
    idea = re.search(r"Quality: \S+ (\d+)/10 — (.+)", card.split("Claw Verdict")[1])
    assert asset and idea
    assert asset.groups() == idea.groups()
    # conf 0.80 (+2 +1), R:R 3.0 (+2), no structure, no volume, neutral RSI
    assert asset.group(1) == "5"


def test_an_asset_with_no_idea_is_not_graded_on_placeholders(monkeypatch):
    card, _p = _run(monkeypatch, {"SOL/USDT": "range"}, {})
    assert "Setup Quality: — no setup from the analyzer to grade" in card
    assert "/10" not in card.split("Claw Verdict")[0]


def test_the_one_glance_verdict_reads_the_best_ideas_asset(monkeypatch):
    card, _p = _run(monkeypatch, {"SOL/USDT": "range"}, {"SOL/USDT": _idea()})
    assert "One-Glance Verdict: No-Trade Zone" in card


# ── F4: the RSI never divides by zero ───────────────────────────────────


def test_fourteen_rising_closes_read_rsi_100_and_the_scan_completes(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": "rising"}, {})
    assert re.search(r"RSI\s+100 \(overbought\)", card)
    assert pushed["symbols"]["SOLUSDT"]["rsi"] == 100.0


def test_a_flat_series_reads_rsi_50_and_the_scan_completes(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": "flat"}, {})
    assert re.search(r"RSI\s+50 \(neutral\)", card)
    assert pushed["symbols"]["SOLUSDT"]["rsi"] == 50.0


def test_a_null_close_is_an_unreadable_series_not_a_failed_scan(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": ("rising", 100, 60),
                                      "ETH/USDT": "falling"}, {})
    assert "SOL/USDT — candles unreadable" in card
    assert pushed["symbols"]["SOLUSDT"]["rsi"] is None
    assert pushed["symbols"]["ETHUSDT"]["rsi"] == 0.0


def test_a_null_volume_in_the_window_is_an_unreadable_series(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": {"kind": "rising", "null_volume_at": 95},
                                      "ETH/USDT": "falling"}, {})
    assert "SOL/USDT — candles unreadable" in card
    assert pushed["symbols"]["SOLUSDT"]["vol_ratio"] is None


def test_a_null_volume_outside_the_window_is_not(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": {"kind": "rising", "null_volume_at": 10}}, {})
    assert "candles unreadable" not in card
    assert pushed["symbols"]["SOLUSDT"]["vol_ratio"] == pytest.approx(1.0)


def test_a_window_that_traded_nothing_has_no_volume_ratio(monkeypatch):
    card, pushed = _run(monkeypatch, {"SOL/USDT": {"kind": "rising", "volume": 0.0}}, {})
    assert "No volume in the window" in card
    assert "Neutral volume" not in card, "`else 1` read no volume as average volume"
    assert pushed["symbols"]["SOLUSDT"]["vol_ratio"] is None


# ── F3: the push carries what the loop measured ──────────────────────────


def test_the_push_carries_the_measured_rsi_and_volume_ratio(monkeypatch):
    card, pushed = _run(monkeypatch, {"BTC/USDT": "falling"}, {})
    btc = pushed["symbols"]["BTCUSDT"]
    assert btc["rsi"] == 0.0, "the card measured RSI 0; the push said 50.0"
    assert btc["vol_ratio"] == pytest.approx(1.0), (
        "the ratio of the last bar's volume to the window's, not 24h dollars "
        "in millions (850.0)")
    assert btc["atr"] is None, "this loop measures no ATR"


def test_a_signal_the_loop_never_read_is_published_unread(monkeypatch):
    extra = MarketSignal(symbol="XRP/USDT", price=0.5, change_pct_24h=1.0,
                         volume_usd_24h=2e8, momentum_score=0.1, timestamp=datetime.now(UTC))

    # XRP is scanned but outside the card's top (the planted exchange cannot
    # serve it either), so nobody measured anything about it.
    card, pushed = _run(monkeypatch, {"BTC/USDT": "falling"}, {}, extra_signals=[extra])
    xrp = pushed["symbols"]["XRPUSDT"]
    assert xrp["rsi"] is None and xrp["vol_ratio"] is None and xrp["book_ratio"] is None


def test_the_regime_is_derived_from_the_measured_rsi(monkeypatch):
    _card, pushed = _run(monkeypatch, {"BTC/USDT": "falling"}, {})
    # the planted signal's 24h change is +5%, so the row's side is LONG and a
    # measured RSI of 0 does not make BEARISH; what matters is that the score
    # is the measured RSI's, not the stamp's 0.0.
    assert pushed["regime"]["score"] == pytest.approx((0.0 - 50) / 50)
    assert pushed["regime"]["gate"] == 100.02


def test_no_entry_card_is_built_off_a_volatility_nobody_measured(monkeypatch):
    _card, pushed = _run(monkeypatch, {"BTC/USDT": "falling"}, {})
    assert pushed["entry_cards"] == [], (
        "a 2%-of-price 'ATR' stood in for one nobody measured")


def test_the_ideas_own_card_carries_its_assets_measured_volume(monkeypatch):
    _card, pushed = _run(monkeypatch, {"SOL/USDT": "range"}, {"SOL/USDT": _idea()})
    (card,) = pushed["entry_cards"]
    assert card["symbol"] == "SOL" and card["book_ratio"] == pytest.approx(1.0)


# ── F3: the payload builder, on rows nobody measured ─────────────────────


def _payload(rows):
    return _build_scan_payload(rows, None)


def _r(sym, **kw):
    r = {"sym": sym, "price": 60000.0, "dir": "LONG", "score": 0.7, "rsi": 70.0,
         "atr": 900.0, "vol_ratio": 1.4, "patterns": []}
    r.update(kw)
    return r


def test_a_btc_row_with_no_rsi_leaves_the_regime_unread():
    p = _payload([_r("BTC/USDT", rsi=None)])
    assert p["regime"]["gate"] == 0, "gate 0 is what every reader reads as not read"
    assert "BTC RSI: unread" in p["key_call"]


def test_a_measured_btc_rsi_still_derives_the_regime():
    p = _payload([_r("BTC/USDT", rsi=70.0)])
    assert p["regime"]["label"] == "BULLISH" and p["regime"]["gate"] == 60000.0


def test_an_unmeasured_volume_ratio_is_null_not_average():
    p = _payload([_r("ETH/USDT", vol_ratio=None)])
    row = p["symbols"]["ETHUSDT"]
    assert row["vol_ratio"] is None and row["book_ratio"] is None
    (card,) = p["entry_cards"]
    assert card["book_ratio"] is None and "Vol unread" in card["thesis"]


def test_a_measured_zero_volume_ratio_stays_zero():
    p = _payload([_r("ETH/USDT", vol_ratio=0.0)])
    assert p["symbols"]["ETHUSDT"]["vol_ratio"] == 0.0


@pytest.mark.parametrize("atr", [None, 0, 0.0, float("nan")])
def test_no_entry_card_without_a_measured_atr(atr):
    p = _payload([_r("ETH/USDT", atr=atr)])
    assert p["entry_cards"] == []


@pytest.mark.parametrize("atr", [float("nan"), float("inf"), "0.5", True])
def test_an_unreadable_atr_is_published_null_and_the_payload_stays_json(atr):
    """The symbols row carries the ATR as a reading or null. A NaN written
    through reaches the wire as a bare ``NaN``, which is not JSON, so a strict
    parser would refuse the whole scan over one row's volatility."""
    p = _payload([_r("ETH/USDT", atr=atr)])
    assert p["symbols"]["ETHUSDT"]["atr"] is None
    json.dumps(p, allow_nan=False)


def test_a_measured_atr_still_builds_the_card_the_telegram_scan_prints():
    p = _payload([_r("ETH/USDT", atr=100.0, price=2000.0)])
    (card,) = p["entry_cards"]
    assert float(card["entry"]) == pytest.approx(2000.0 - 30.0)
    assert float(card["stop_loss"]) == pytest.approx(2000.0 - 250.0)
