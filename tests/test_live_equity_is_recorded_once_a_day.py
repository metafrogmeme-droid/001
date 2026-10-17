"""Live equity is recorded once a UTC day, from a reading the venue answered.

The 17 Oct edge audit could give live a profit factor and a win rate but no
Sharpe, because nothing kept a daily equity series: the risk engine's
snapshot is in memory and the website's is per process. One row a day, in
the state directory, from a fresh reading. Three facts kept apart: a day
already recorded stays as it is, an unreadable reading records nothing
(never a zero), and a row says when it was read and from what.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bot.core import equity_record as ER

D0 = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTheRow:
    def test_a_fresh_reading_writes_one_row_with_its_facts(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        out = ER.record_daily_equity({"total": 9698.084, "free": 8800.0}, mode="live",
                                     source="bitget", when=D0, path=p)
        assert out == ER.RECORDED
        rows = _rows(p)
        assert rows == [{"day": "2026-10-17", "read_at": "2026-10-17T09:30:00+00:00",
                         "mode": "live", "source": "bitget",
                         "equity_usd": 9698.08, "free_usd": 8800.0}]

    def test_the_same_day_is_not_recorded_twice(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        ER.record_daily_equity({"total": 100.0}, mode="live", source="v", when=D0, path=p)
        out = ER.record_daily_equity({"total": 101.0}, mode="live", source="v",
                                     when=D0 + timedelta(hours=5), path=p)
        assert out == ER.ALREADY
        assert [r["equity_usd"] for r in _rows(p)] == [100.0]

    def test_the_next_day_is(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        ER.record_daily_equity({"total": 100.0}, mode="live", source="v", when=D0, path=p)
        out = ER.record_daily_equity({"total": 99.0}, mode="live", source="v",
                                     when=D0 + timedelta(days=1), path=p)
        assert out == ER.RECORDED
        assert [r["day"] for r in _rows(p)] == ["2026-10-17", "2026-10-18"]

    @pytest.mark.parametrize("reading", [
        None, {}, {"free": 5.0}, {"total": None}, {"total": "9698"}, {"total": float("nan")},
        {"error": "AuthenticationError", "total": 0},   # the failed read get_live_equity lets through
        {"total": True},
    ])
    def test_an_unreadable_reading_records_nothing(self, tmp_path, reading):
        p = tmp_path / "eq.jsonl"
        assert ER.record_daily_equity(reading, mode="live", source="v", when=D0, path=p) == ER.UNREADABLE
        assert not p.exists()

    def test_a_zero_balance_is_a_measurement(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        assert ER.record_daily_equity({"total": 0.0}, mode="live", source="v",
                                      when=D0, path=p) == ER.RECORDED
        assert _rows(p)[0]["equity_usd"] == 0.0

    def test_a_missing_free_figure_is_absent_not_zero(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        ER.record_daily_equity({"total": 100.0}, mode="live", source="v", when=D0, path=p)
        assert _rows(p)[0]["free_usd"] is None


class TestReadingBack:
    def test_the_series_comes_back_oldest_first_and_skips_what_cannot_be_read(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        for i in range(3):
            ER.record_daily_equity({"total": 100.0 + i}, mode="live", source="v",
                                   when=D0 + timedelta(days=i), path=p)
        with open(p, "a") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"day": "2026-10-20", "equity_usd": "unread"}) + "\n")
        rows = ER.read_daily_equity(p)
        assert [r["equity_usd"] for r in rows] == [100.0, 101.0, 102.0]

    def test_a_missing_file_is_an_empty_series(self, tmp_path):
        assert ER.read_daily_equity(tmp_path / "none.jsonl") == []

    def test_the_last_day_is_read_from_the_last_readable_row(self, tmp_path):
        p = tmp_path / "eq.jsonl"
        assert ER.last_recorded_day(p) is None
        ER.record_daily_equity({"total": 1.0}, mode="live", source="v", when=D0, path=p)
        assert ER.last_recorded_day(p) == "2026-10-17"

    def test_the_path_is_anchored_under_the_state_directory(self, monkeypatch):
        monkeypatch.delenv("EQUITY_LOG_PATH", raising=False)
        p = ER.equity_log_path()
        assert p.is_absolute() and p.name == "equity_daily.jsonl"


class TestTheEngineHook:
    """The engine's method, bound onto a stand-in that answers only what the
    method asks: the fresh-cache reading. A stale cache is None there."""

    def _engine(self, reading):
        from types import SimpleNamespace

        from bot.core.engine import RuneClawEngine
        stub = SimpleNamespace(live_balance_cached=lambda max_age_s=900.0: reading)
        return lambda: RuneClawEngine._record_live_equity_day(stub)

    def test_a_fresh_reading_is_recorded_under_the_venue_s_name(self, tmp_path, monkeypatch):
        p = tmp_path / "eq.jsonl"
        monkeypatch.setenv("EQUITY_LOG_PATH", str(p))
        from bot.config import CONFIG
        assert self._engine({"total": 1234.5, "free": 1000.0})() == ER.RECORDED
        row = _rows(p)[0]
        assert row["mode"] == "live" and row["source"] == CONFIG.exchange.venue
        assert row["equity_usd"] == 1234.5

    def test_a_stale_or_absent_cache_records_nothing(self, tmp_path, monkeypatch):
        p = tmp_path / "eq.jsonl"
        monkeypatch.setenv("EQUITY_LOG_PATH", str(p))
        assert self._engine(None)() == ER.UNREADABLE
        assert not p.exists()

    def test_the_tick_records_after_the_refresh_and_fails_open(self):
        """The tick is a long coroutine behind the venue; this is a scan of
        the one block: the record call sits in the live-mode branch after the
        refresh, inside its own try, and the except names only the class."""
        import ast
        import inspect
        import textwrap

        from bot.core.engine import RuneClawEngine
        src = textwrap.dedent(inspect.getsource(RuneClawEngine._tick))
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "_record_live_equity_day"]
        assert len(calls) == 1
        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
                 and any(c is calls[0] for s in n.body for c in ast.walk(s))]
        assert len(tries) == 1
        handler_src = ast.get_source_segment(src, tries[0].handlers[0])
        assert "type(exc).__name__" in handler_src and "str(exc)" not in handler_src
