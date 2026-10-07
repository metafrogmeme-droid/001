"""Daily Volatility Rotation: one name, daily bars, no invented fill.

Direction is long only when the close is above the trend average and the
close-to-close momentum ratio clears the preset threshold. An unreadable
momentum, average, or ATR is not an entry. The percent exits stay recorded
and are not applied, so the reader does not open a book.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

import pytest

from bot.compat import UTC
from bot.core import strategy_catalog as sc
from bot.core.strategy_gate import check_confirm, describe_gates, resolve_key
from bot.core.vol_rotation import (
    bars_per_target,
    momentum_ratio,
    rotate,
    symbol_read,
)
from bot.skills.skill_registry import RunStrategySkill
from bot.utils.trailing import make_trailing_state, update_trailing_stop

_HOUR_MS = 3_600_000
_START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _preset():
    return RunStrategySkill.PRESETS["daily vol rotation"]


def _hours(closes, start=_START):
    rows = []
    for i, close in enumerate(closes):
        price = float(close)
        rows.append([start + i * _HOUR_MS, price, price + 1.0, price - 1.0, price, 1.0])
    return rows


def _days(values):
    closes = []
    for value in values:
        closes.extend([float(value)] * 24)
    return _hours(closes)


def _fifty_one(early, late):
    """51 daily closes: 31 at ``early``, then 20 at ``late``."""
    return [early] * 31 + [late] * 20


def test_the_preset_is_the_stated_parameters():
    cfg = _preset()
    assert cfg["label"] == "Daily Volatility Rotation"
    assert cfg["symbols"] == [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"]
    assert cfg["momentum_period"] == 20
    assert cfg["atr_period"] == 20
    assert cfg["trend_period"] == 50
    assert cfg["momentum_threshold"] == 0.5
    assert cfg["trailing_stop_pct"] == 0.08
    assert cfg["take_profit_pct"] == 0.05
    assert cfg["hard_stop_loss_pct"] == 0.04
    assert cfg["leverage"] == 5
    assert cfg["utilization"] == 0.95
    assert cfg["bar_timeframe"] == "1d"
    assert cfg["bar_source_timeframe"] == "1h"
    assert cfg["direction"] == "long_only"
    assert bars_per_target("1h", "1d") == 24


def test_the_slug_and_alias_resolve_to_the_preset():
    presets, aliases = RunStrategySkill.PRESETS, RunStrategySkill.ALIASES
    assert resolve_key("daily-vol-rotation", presets, aliases) == "daily vol rotation"
    assert resolve_key("volrotation", presets, aliases) == "daily vol rotation"
    assert resolve_key("daily vol rotation", presets, aliases) == "daily vol rotation"
    assert aliases["momentum"] == "momentum hunter"


def test_momentum_is_the_close_ratio_and_unreadable_is_not_zero():
    assert momentum_ratio([100.0] * 20 + [150.0], 20) == pytest.approx(0.5)
    assert momentum_ratio([100.0] * 20 + [149.0], 20) == pytest.approx(0.49)
    assert momentum_ratio([100.0] * 10, 20) is None
    assert momentum_ratio([0.0] + [1.0] * 20, 20) is None
    assert momentum_ratio([100.0] * 20 + [float("nan")], 20) is None
    assert momentum_ratio([1.0, 2.0, 3.0], True) is None


def test_a_half_ratio_qualifies_and_either_miss_does_not():
    cfg = _preset()
    closes = _fifty_one(100.0, 150.0)
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    hit = symbol_read(closes, highs, lows, cfg)
    assert hit["momentum"] == pytest.approx(0.5)
    assert hit["above_average"] is True
    assert hit["atr"] is not None and hit["atr"] > 0
    assert hit["qualifies"] is True
    assert hit["side"] == "LONG"
    assert hit["hard_stop"] is None
    assert hit["take_profit"] is None
    assert hit["trail"] is None

    short = symbol_read(_fifty_one(100.0, 149.0), highs, lows, cfg)
    assert short["momentum"] == pytest.approx(0.49)
    assert short["qualifies"] is False
    assert short["side"] is None

    below = [1000.0] * 51
    below[30] = 10.0
    below[50] = 15.0
    missed = symbol_read(below, [c + 1.0 for c in below], [c - 1.0 for c in below], cfg)
    assert missed["momentum"] == pytest.approx(0.5)
    assert missed["above_average"] is False
    assert missed["qualifies"] is False
    assert missed["side"] is None


def test_an_unreadable_atr_is_not_a_stop_of_zero():
    cfg = _preset()
    closes = _fifty_one(100.0, 150.0)
    highs = [c + 1.0 for c in closes]
    highs[-1] = float("nan")
    lows = [c - 1.0 for c in closes]
    unread = symbol_read(closes, highs, lows, cfg)
    assert unread["atr"] is None
    assert unread["qualifies"] is False
    assert unread["hard_stop"] is None
    assert unread["take_profit"] is None
    assert unread["trail"] is None

    flat = [100.0] * 51
    quiet = symbol_read(flat, list(flat), list(flat), cfg)
    assert quiet["atr"] == 0.0
    assert quiet["above_average"] is False
    assert quiet["qualifies"] is False
    assert quiet["hard_stop"] is None


def test_a_partial_day_is_dropped_and_hourly_bars_stay_hourly():
    cfg = _preset()
    closed = _days(_fifty_one(100.0, 150.0))
    partial = closed + _hours([999.0] * 23, start=_START + len(closed) * _HOUR_MS)
    book = rotate({"BTCUSDT": partial}, cfg)
    read = book["reads"]["BTCUSDT"]
    assert read["daily_bars"] == 51
    assert read["momentum"] == pytest.approx(0.5)
    assert read["qualifies"] is True
    assert book["chosen"] == "BTCUSDT"
    assert book["opens"] is False

    finished = closed + _hours([999.0] * 24, start=_START + len(closed) * _HOUR_MS)
    moved = rotate({"BTCUSDT": finished}, cfg)
    assert moved["reads"]["BTCUSDT"]["daily_bars"] == 52
    assert moved["reads"]["BTCUSDT"]["momentum"] != pytest.approx(0.5)

    hourly = _hours([100.0] * 20 + [150.0])
    assert momentum_ratio([row[4] for row in hourly], 20) == pytest.approx(0.5)
    raw = rotate({"BTCUSDT": hourly}, cfg)
    assert raw["reads"]["BTCUSDT"]["daily_bars"] == 0
    assert raw["reads"]["BTCUSDT"]["momentum"] is None
    assert raw["reads"]["BTCUSDT"]["qualifies"] is False
    assert raw["chosen"] is None


def test_the_highest_momentum_is_the_one_name_and_a_tie_is_not():
    cfg = _preset()
    btc = _days(_fifty_one(100.0, 150.0))
    eth = _days(_fifty_one(100.0, 200.0))
    book = rotate({"BTCUSDT": btc, "ETHUSDT": eth, "DOGEUSDT": eth}, cfg)
    assert set(book["qualifiers"]) == {"BTCUSDT", "ETHUSDT"}
    assert "DOGEUSDT" not in book["reads"]
    assert book["chosen"] == "ETHUSDT"
    assert book["selection"] == "highest_momentum"
    assert book["opens"] is False
    assert book["exits_applied"] is False
    assert book["sizing_applied"] is False
    assert book["reads"]["ETHUSDT"]["hard_stop"] is None

    tied = rotate({"BTCUSDT": btc, "ETHUSDT": btc}, cfg)
    assert set(tied["qualifiers"]) == {"BTCUSDT", "ETHUSDT"}
    assert tied["chosen"] is None
    assert tied["opens"] is False


def test_the_house_trail_is_not_the_percent_trail():
    import inspect
    assert "trailing_stop_pct" not in inspect.signature(update_trailing_stop).parameters
    state = make_trailing_state(100.0, "LONG", 4.0, 1.0)
    sl, active = update_trailing_stop(state, 105.0, 96.0, "LONG")
    assert active is True
    assert sl != pytest.approx(105.0 * (1.0 - 0.08))
    quiet = make_trailing_state(100.0, "LONG", 4.0, 1.0)
    held, waiting = update_trailing_stop(quiet, 103.0, 96.0, "LONG")
    assert waiting is False
    assert held == 96.0


def test_run_states_the_rule_and_does_not_place_or_close():
    class _Boom:
        def __getattr__(self, _name):
            raise AssertionError("the rotation must not scan")

    text = asyncio.run(RunStrategySkill().execute(_Boom(), strategy="volrotation"))
    assert "No order is placed" in text
    assert "recorded and not applied" in text
    assert "24 closed 1h bars" in text
    assert "1h bars stay 1h bars" in text
    assert "50%" in text
    assert "0.5%" not in text
    assert "do not size a fill" in text
    assert "0.95" in text
    assert "5\u00d7" in text
    assert "$" not in text


def test_the_public_card_has_no_track_record_and_no_dollars():
    card = sc.get_agent("daily-vol-rotation")
    assert card is not None
    assert card["name"] == "Daily Volatility Rotation"
    assert card["run"] == "volrotation"
    assert "recorded and not applied" in card["how"]
    assert "24 closed 1h bars" in card["how"]
    assert "BTCUSDT" in card["how"] and "AVAXUSDT" in card["how"]
    assert "0.5%" not in card["how"]
    assert card["scorecard"]["omitted"]
    assert "recorded and not applied" in card["scorecard"]["omitted"]
    assert "metrics" not in card["scorecard"]
    blob = repr(card)
    assert "$" not in blob
    path = os.path.join(_REPO, "benchmark", "scorecards", "daily-vol-rotation.json")
    assert not os.path.exists(path)
    dip = sc.get_agent("dip-sniper")
    assert "recorded and not applied" not in dip["how"]
    assert dip["scorecard"]["metrics"]["total_trades"] is not None


def test_confirm_keeps_the_universe_and_does_not_pretend_to_apply_exits():
    cfg = _preset()
    refused = check_confirm("daily vol rotation", cfg, "DOGE/USDT:USDT", 0.9)
    assert refused["ok"] is False
    assert "symbols" in refused["enforced"]
    allowed = check_confirm("daily vol rotation", cfg, "AVAX/USDT:USDT", None)
    assert allowed["ok"] is True
    assert "momentum_period" in allowed["scan_only"]
    assert "trailing_stop_pct" not in allowed["scan_only"]
    confirm, scan = describe_gates(cfg)
    assert confirm == ["symbols"]
    assert "momentum_period" in scan and "bar_timeframe" in scan
    assert "trailing_stop_pct" not in scan
    assert "leverage" not in scan and "utilization" not in scan
    dip_confirm, _dip_scan = describe_gates(RunStrategySkill.PRESETS["dip sniper"])
    assert dip_confirm == ["confidence>=70%"]


def test_the_generator_does_not_publish_a_majors_card():
    from scripts.gen_agent_scorecards import _gate_args, _unmodeled, generate, publishes_scorecard
    cfg = _preset()
    assert publishes_scorecard(cfg) is False
    assert publishes_scorecard(RunStrategySkill.PRESETS["full scan"]) is True
    assert publishes_scorecard(RunStrategySkill.PRESETS["eth ma trend"]) is True
    args = _gate_args(cfg)
    assert "--direction" in args and "long_only" in args
    assert "--leverage" not in args
    assert "--ma-utilization" not in args
    assert "0.08" not in args and "0.05" not in args and "0.04" not in args
    assert _gate_args(RunStrategySkill.PRESETS["full scan"]) == []
    assert set(_unmodeled(cfg)) == {
        "trailing_stop_pct", "take_profit_pct", "hard_stop_loss_pct"}
    assert _unmodeled(RunStrategySkill.PRESETS["eth ma trend"]) == []
    dataset = os.path.join(_REPO, "benchmark", "majors_1h")
    with pytest.raises(SystemExit) as raised:
        generate(dataset, "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT", 10,
                 preset="daily-vol-rotation")
    assert "not applied" in str(raised.value)
    assert "not this daily book" in str(raised.value)
