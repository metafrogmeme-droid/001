"""One reading of "the last bar is still forming" in the bot process.

Two findings from the sixty-PR review:

- #506 wrote ``chart_renderer.closed_overlay_frame`` as "the same comparison
  as ``drop_forming_candle``" but as a second function: the analysis honours
  DROP_UNCLOSED_CANDLE_ENABLED and the chart's overlays did not, so with the
  flag off the analysis read the forming bar and the picture attached to the
  same message did not. Both now ask ``bar_is_forming``.
- ``timeframe_to_ms`` and the chart's ``_period_ms`` folded case, so ccxt's
  month (``1M``) read as one minute and a monthly bar counted as closed a
  minute after it opened. A month has no fixed length; ``period_end_ms``
  reads it on the calendar, the same reading as the website's
  ``chartread.periodEnd``.
"""
from datetime import datetime, timezone

import pytest

from bot.config import CONFIG
from bot.utils import candles
from bot.utils.candles import (
    bar_is_forming,
    drop_forming_candle,
    period_end_ms,
    timeframe_to_ms,
)
from tests.dep_policy import require

_H = 3_600_000


def _ms(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp() * 1000.0


@pytest.fixture
def set_flag():
    """Flip the real (frozen) CONFIG flag, restored at teardown."""
    old = CONFIG.analyzer.drop_unclosed_candle_enabled

    def _set(value):
        object.__setattr__(CONFIG.analyzer, "drop_unclosed_candle_enabled", value)

    yield _set
    _set(old)


@pytest.fixture
def flag_on(set_flag):
    set_flag(True)


def test_a_month_is_not_a_minute_and_closes_on_the_calendar():
    assert timeframe_to_ms("1M") == 0, "a month has no fixed length"
    assert timeframe_to_ms("1m") == 60_000
    sep = _ms(2026, 9, 1)
    assert period_end_ms(sep, "1M") == _ms(2026, 10, 1)
    assert period_end_ms(sep, "3M") == _ms(2026, 12, 1)
    assert period_end_ms(_ms(2026, 12, 1), "1M") == _ms(2027, 1, 1)
    # Bitget's plain 1M opens at 00:00 UTC+8, 16:00 UTC the day before.
    assert period_end_ms(_ms(2026, 9, 30, 16), "1M") == _ms(2026, 10, 31, 16)
    # A zone west of UTC carries across a 31-day month the same way.
    assert period_end_ms(_ms(2026, 10, 1, 5), "1M") == _ms(2026, 11, 1, 5)
    # Fixed periods add their length; nothing is guessed for the rest.
    assert period_end_ms(sep, "1h") == sep + _H
    assert period_end_ms(sep, "1mo") == 0.0
    assert period_end_ms(sep, "") == 0.0
    assert period_end_ms(None, "1h") == 0.0
    assert period_end_ms(float("nan"), "1h") == 0.0


def test_a_forming_monthly_bar_is_dropped_and_a_closed_one_kept(flag_on, monkeypatch):
    rows = [[_ms(2026, m, 1), 1.0, 2.0, 0.5, 1.5, 1.0] for m in (6, 7, 8, 9)]
    monkeypatch.setattr(candles.time, "time", lambda: _ms(2026, 9, 20) / 1000.0)
    assert drop_forming_candle(rows, "1M") == rows[:-1], "three weeks into September"
    monkeypatch.setattr(candles.time, "time", lambda: _ms(2026, 10, 1, 0, 0, 1) / 1000.0)
    assert drop_forming_candle(rows, "1M") == rows


def test_the_flag_decides_for_every_consumer(set_flag):
    open_ms = _ms(2026, 10, 6, 13)
    live = open_ms + 1000
    set_flag(True)
    assert bar_is_forming(open_ms, "1h", live) is True
    assert bar_is_forming(open_ms, "1h", open_ms + _H) is False
    assert bar_is_forming(open_ms, "zzz", live) is False, "an unknown period is never forming"
    set_flag(False)
    assert bar_is_forming(open_ms, "1h", live) is False


def _frame(open_last):
    require("pandas", "bot/skills/chart_renderer.py renders with it")
    from bot.skills import chart_renderer as cr
    rows = [[open_last - (39 - i) * _H, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1.0]
            for i in range(40)]
    df = cr.compute_chart_indicators(rows)
    if df is None:
        pytest.skip("chart stack unavailable")
    return cr, df


def test_the_chart_overlays_follow_the_flag_the_analysis_follows(set_flag, monkeypatch):
    open_ms = _ms(2026, 10, 6, 13)
    cr, df = _frame(open_ms)
    live = open_ms + 1000
    rows = [[open_ms - (39 - i) * _H, 1, 1, 1, 1, 1] for i in range(40)]
    monkeypatch.setattr(candles.time, "time", lambda: live / 1000.0)

    set_flag(True)
    assert len(cr.closed_overlay_frame(df, "1h", live)) == len(df) - 1
    assert len(drop_forming_candle(rows, "1h")) == len(rows) - 1

    # Off: the analysis keeps the forming bar, and so does the picture of it.
    set_flag(False)
    assert len(drop_forming_candle(rows, "1h")) == len(rows)
    assert len(cr.closed_overlay_frame(df, "1h", live)) == len(df)


def test_the_chart_reads_a_month_as_a_month(flag_on):
    open_ms = _ms(2026, 9, 1)
    cr, df = _frame(open_ms)
    assert len(cr.closed_overlay_frame(df, "1M", _ms(2026, 9, 20))) == len(df) - 1
    assert len(cr.closed_overlay_frame(df, "1M", _ms(2026, 10, 1))) == len(df)
    # Bitget's fifteen-minute spelling still folds to the engine's.
    assert len(cr.closed_overlay_frame(df, "15min", open_ms + 1000)) == len(df) - 1
