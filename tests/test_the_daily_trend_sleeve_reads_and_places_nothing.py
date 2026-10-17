"""The daily trend sleeve: the one pre-registered rule that passed, as a preset
the live bot does not run.

On Kraken's spot history (2014–2021 development, 2021–2026 holdout) the
long-only trend rule cleared every pre-registered criterion; the engineering
log has the table. This preset carries that rule's numbers, `/dailytrend`
reads it on the last closed bars, and nothing is placed. These tests drive the
reading (a zero score is a measurement, an unreadable close is not), the
sizing arithmetic, the board, the door, `/run`, and the public card.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from bot.core import daily_trend as dt
from bot.core import strategy_catalog as sc
from bot.core import strategy_scan as ss
from bot.skills.skill_registry import RunStrategySkill

_HOUR = 3_600_000
_START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def _preset():
    return RunStrategySkill.PRESETS["daily trend"]


def _hours_from_days(values, start=_START):
    rows = []
    i = 0
    for v in values:
        for _ in range(24):
            rows.append([start + i * _HOUR, float(v), float(v) + 1.0, float(v) - 1.0, float(v), 1.0])
            i += 1
    return rows


def _ramp(n, start, step):
    return [start + i * step for i in range(n)]


class TestTheNumbers:
    def test_the_preset_is_the_pre_registered_rule(self):
        cfg = _preset()
        p = dt.params_from_preset(cfg)
        assert p == dt.SleeveParams((20, 60, 120), 30, 0.20, 0.10, 1.0)
        assert p.days_needed == 121
        assert len(cfg["symbols"]) == 16 and "BTCUSDT" in cfg["symbols"]
        assert not any(s.startswith(x) for s in cfg["symbols"] for x in ("AVAX", "BNB", "NEAR", "SOL"))
        assert "leverage" not in cfg

    @pytest.mark.parametrize("bad", [
        {"lookbacks": [60, 20, 120]}, {"lookbacks": []}, {"lookbacks": [20, 20, 120]},
        {"vol_window": 1}, {"target_vol": 0}, {"target_vol": 1.5}, {"band": 1.0},
        {"cap": 0}, {"extra": 1}, {"lookbacks": [20, True, 120]},
    ])
    def test_a_number_that_does_not_read_refuses_the_preset(self, bad):
        assert dt.params_from_preset({"daily_trend": bad}) is None

    def test_the_slug_and_alias_resolve_to_the_preset(self):
        assert RunStrategySkill._resolve("dailytrend") == "daily trend"
        assert RunStrategySkill._resolve("daily-trend") == "daily trend"
        assert ss.scan_command("daily trend") == "dailytrend"


class TestTheReading:
    def test_the_score_is_the_mean_sign_floored_at_zero(self):
        up = _ramp(130, 100.0, 1.0)
        assert dt.trend_score(up, (20, 60, 120)) == 1.0
        down = _ramp(130, 300.0, -1.0)
        assert dt.trend_score(down, (20, 60, 120)) == 0.0, "long only: a down trend is zero, not negative"
        mixed = [100.0] * 70 + _ramp(60, 100.0, -0.5)   # up over 120 is flat (0), down over 20 and 60
        assert dt.trend_score(mixed, (20, 60, 120)) == 0.0
        partly = _ramp(100, 100.0, 1.0) + _ramp(30, 200.0, -0.5)  # down 20, up 60 and 120
        assert dt.trend_score(partly, (20, 60, 120)) == pytest.approx(1 / 3)

    def test_an_unreadable_close_is_not_a_zero_score(self):
        seq = _ramp(130, 100.0, 1.0)
        assert dt.trend_score(seq[:100], (20, 60, 120)) is None
        seq[-61] = None
        assert dt.trend_score(seq, (20, 60, 120)) is None
        assert dt.trend_score([], (20,)) is None

    def test_the_vol_is_annualised_and_a_flat_series_is_unread_not_zero(self):
        seq = [100.0, 101.0] * 20
        v = dt.realised_vol(seq, 30)
        # The sample standard deviation of the last 30 daily returns, times
        # the square root of the 365 days crypto trades, by hand.
        rets = [b / a - 1.0 for a, b in zip(seq[-31:], seq[-30:])]
        mean = sum(rets) / 30
        sd = (sum((r - mean) ** 2 for r in rets) / 29) ** 0.5
        assert v == pytest.approx(sd * 365 ** 0.5)
        assert v == pytest.approx(0.19, abs=0.01)
        assert dt.realised_vol([100.0] * 40, 30) is None
        assert dt.realised_vol(_ramp(10, 100.0, 1.0), 30) is None

    def test_the_weight_is_score_times_the_split_over_the_vol_and_capped(self):
        p = dt.SleeveParams()
        assert dt.target_weight(1.0, 0.50, 4, p) == pytest.approx(0.20 / 4 / 0.50)
        assert dt.target_weight(1 / 3, 0.50, 4, p) == pytest.approx(0.20 / 4 / 0.50 / 3)
        assert dt.target_weight(1.0, 0.01, 1, p) == 1.0, "capped at one times notional"
        assert dt.target_weight(0.0, 0.5, 4, p) == 0.0, "a zero score is a zero weight"
        assert dt.target_weight(None, 0.5, 4, p) is None
        assert dt.target_weight(1.0, None, 4, p) is None
        assert dt.target_weight(1.0, 0.5, 0, p) is None


class TestTheBook:
    def _book(self, series):
        return dt.read_book(series, _preset())

    def test_the_split_counts_only_the_markets_that_read_today(self):
        up = _hours_from_days(_ramp(130, 100.0, 1.0))
        short = _hours_from_days(_ramp(50, 100.0, 1.0))
        book = self._book({"BTC": up, "ETH": up, "XRP": short})
        assert book["n_book"] == 2
        assert book["reads"]["XRP"].weight is None
        assert book["reads"]["XRP"].reason == "unread: 50 closed daily bars, 121 needed"
        assert book["holds"] == ["BTC", "ETH"] or book["holds"] == ["ETH", "BTC"]
        assert book["reads"]["BTC"].weight == pytest.approx(
            dt.target_weight(1.0, dt.realised_vol([r[4] for r in up][23::24], 30), 2, book["params"]))
        assert book["gross"] == pytest.approx(2 * book["reads"]["BTC"].weight)

    def test_a_down_market_is_read_with_a_zero_score_and_not_held(self):
        up = _hours_from_days(_ramp(130, 100.0, 1.0))
        down = _hours_from_days(_ramp(130, 300.0, -1.0))
        book = self._book({"BTC": up, "ETH": down})
        assert book["reads"]["ETH"].score == 0.0 and book["reads"]["ETH"].weight == 0.0
        assert book["holds"] == ["BTC"]
        assert book["n_book"] == 2, "a zero score is still a market that read today"

    def test_hourly_bars_are_grouped_into_days_and_a_partial_day_is_dropped(self):
        rows = _hours_from_days(_ramp(130, 100.0, 1.0))
        rows += rows[-1:]  # one hour of a new, unfinished day
        book = self._book({"BTC": rows})
        assert book["reads"]["BTC"].daily_bars == 130

    def test_nothing_is_opened_sized_or_exited(self):
        book = self._book({"BTC": _hours_from_days(_ramp(130, 100.0, 1.0))})
        assert book["opens"] is False and book["sizing_applied"] is False and book["exits_applied"] is False

    def test_a_preset_whose_numbers_do_not_read_reads_nothing(self):
        cfg = dict(_preset())
        cfg["daily_trend"] = {"vol_window": 0}
        book = dt.read_book({"BTC": _hours_from_days(_ramp(130, 100.0, 1.0))}, cfg)
        assert book["params"] is None and book["reads"] == {} and book["gross"] is None


class TestTheDoorAndTheCard:
    def test_the_board_names_the_book_and_carries_no_dollar(self):
        up = _hours_from_days(_ramp(130, 100.0, 1.0))
        down = _hours_from_days(_ramp(130, 300.0, -1.0))
        rows = {"BTC": up, "ETH": down}
        board = ss.board_daily_trend(_preset(), rows)
        by = {r["symbol"]: r for r in board["rows"]}
        assert by["BTC"]["weight_pct"] > 0 and by["ETH"]["weight_pct"] == 0.0
        assert "It would hold 1 of 2" in board["verdict"] and "BTC" in board["verdict"]
        text = json.dumps(board)
        assert "$" not in text
        card = ss.render_text({**board, "label": "x", "bar_close": "x", "rule": ss.DAILY_TREND, "unread": []})
        assert "BTC: trend score 1.00" in card and "Nothing is placed or queued" in card

    def test_an_empty_book_says_so(self):
        down = _hours_from_days(_ramp(130, 300.0, -1.0))
        board = ss.board_daily_trend(_preset(), {"BTC": down})
        assert board["verdict"] == "No market has a positive trend score, so it would hold nothing."

    def test_bars_needed_is_the_longest_lookback_in_hours_with_a_day_to_spare(self):
        assert ss.bars_needed(_preset()) == (120 + 3) * 24
        assert ss.rule_of(_preset()) == ss.DAILY_TREND

    def test_run_states_the_rule_and_does_not_place(self):
        class _Boom:
            def __getattr__(self, _name):
                raise AssertionError("the sleeve must not scan")

        text = asyncio.run(RunStrategySkill().execute(_Boom(), strategy="dailytrend"))
        assert "No order is placed" in text
        assert "20, 60 and 120 days" in text and "long only" in text
        assert "/dailytrend" in text
        assert "recorded and not applied" in text
        assert "$" not in text

    def test_the_public_card_has_no_track_record_and_names_the_study(self):
        card = sc.get_agent("daily-trend")
        assert card is not None
        text = json.dumps(card)
        assert "$" not in text
        assert card["name"] == "Daily Trend Sleeve" and card["run"] == "dailytrend"
        assert card["scorecard"]["omitted"].startswith("No track record published")
        assert "engineering log" in card["scorecard"]["omitted"]
        assert "metrics" not in card["scorecard"]
        assert "/dailytrend" in card["how"] and "last closed daily bars" in card["how"]
        assert "BTCUSDT" not in card["how"] or "16 USDT perpetuals" in card["how"]
        assert sc.live_runs(_preset()) is False

    def test_the_scorecard_generator_does_not_publish_a_card(self):
        assert sc.publishes_scorecard(_preset()) is False


# ── the paper record: one board a day ───────────────────────────────────────

from datetime import timedelta  # noqa: E402

from bot.core import equity_record as ER  # noqa: E402

D0 = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)


def _board(rows, unread=()):
    return {"rows": rows, "unread": [{"symbol": s, "why": "TimeoutError"} for s in unread],
            "bar_close": "17 Oct 00:00 UTC"}


def _rows_file(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTheDailyRecord:
    def test_a_readable_board_writes_one_row_with_the_book(self, tmp_path):
        p = tmp_path / "book.jsonl"
        board = _board([{"symbol": "BTC", "score": 1.0, "vol_pct": 40.0, "weight_pct": 12.5, "reason": "x"},
                        {"symbol": "ETH", "score": 0.0, "vol_pct": 50.0, "weight_pct": 0.0, "reason": "y"},
                        {"symbol": "XRP", "score": None, "vol_pct": None, "weight_pct": None, "reason": "unread"}],
                       unread=("ADA",))
        assert dt.record_book(board, when=D0, path=p) == ER.RECORDED
        row = _rows_file(p)[0]
        assert row["day"] == "2026-10-17" and row["bar_close"] == "17 Oct 00:00 UTC"
        assert row["n_book"] == 2, "the markets that read, zero score included"
        assert row["holds"] == [{"symbol": "BTC", "score": 1.0, "weight_pct": 12.5}]
        assert row["gross_pct"] == 12.5 and row["unread"] == ["ADA"]

    def test_the_same_day_is_not_recorded_twice(self, tmp_path):
        p = tmp_path / "book.jsonl"
        board = _board([{"symbol": "BTC", "score": 1.0, "vol_pct": 40.0, "weight_pct": 12.5, "reason": "x"}])
        dt.record_book(board, when=D0, path=p)
        assert dt.record_book(board, when=D0 + timedelta(hours=6), path=p) == ER.ALREADY
        assert dt.record_book(board, when=D0 + timedelta(days=1), path=p) == ER.RECORDED
        assert len(_rows_file(p)) == 2

    @pytest.mark.parametrize("board", [None, {}, {"rows": "x"}, _board([]),
                                       _board([{"symbol": "BTC", "score": None, "weight_pct": None}])])
    def test_a_board_nothing_read_on_records_nothing(self, tmp_path, board):
        p = tmp_path / "book.jsonl"
        assert dt.record_book(board, when=D0, path=p) == ER.UNREADABLE
        assert not p.exists()

    def test_the_engine_scans_once_a_day_and_records_what_it_read(self, tmp_path, monkeypatch):
        p = tmp_path / "book.jsonl"
        monkeypatch.setenv("DAILY_TREND_BOOK_PATH", str(p))
        from types import SimpleNamespace

        from bot.core.engine import RuneClawEngine
        asked = []

        async def scan(engine, key, **kw):
            asked.append(key)
            return _board([{"symbol": "BTC", "score": 1.0, "vol_pct": 40.0, "weight_pct": 12.5, "reason": "x"}])

        monkeypatch.setattr("bot.core.strategy_scan.scan", scan)
        stub = SimpleNamespace(scanner=SimpleNamespace(_futures_exchange=object()))
        method = RuneClawEngine._record_daily_trend_book
        assert asyncio.run(method(stub)) == ER.RECORDED
        assert asked == ["daily trend"]
        assert asyncio.run(method(stub)) == ER.ALREADY
        assert asked == ["daily trend"], "a day already recorded is not scanned again"

    def test_a_tick_whose_scan_opened_no_venue_asks_nothing(self, tmp_path, monkeypatch):
        """The reading rides the scan phase's connection and opens none of its
        own: with none open it records nothing, scans nothing, and says so."""
        p = tmp_path / "book.jsonl"
        monkeypatch.setenv("DAILY_TREND_BOOK_PATH", str(p))
        from types import SimpleNamespace

        from bot.core.engine import RuneClawEngine
        asked = []

        async def scan(engine, key, **kw):
            asked.append(key)
            return _board([{"symbol": "BTC", "score": 1.0, "vol_pct": 40.0, "weight_pct": 12.5, "reason": "x"}])

        monkeypatch.setattr("bot.core.strategy_scan.scan", scan)
        method = RuneClawEngine._record_daily_trend_book
        for stub in (SimpleNamespace(), SimpleNamespace(scanner=SimpleNamespace(_futures_exchange=None))):
            assert asyncio.run(method(stub)) == dt.NOT_ASKED
        assert asked == [] and not p.exists()

    def test_the_tick_records_in_every_mode_inside_its_own_try(self):
        import ast
        import inspect
        import textwrap

        from bot.core.engine import RuneClawEngine
        from tests.source_scan import segment_reader
        src = textwrap.dedent(inspect.getsource(RuneClawEngine._tick))
        seg = segment_reader(src)
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "_record_daily_trend_book"]
        assert len(calls) == 1
        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
                 and any(c is calls[0] for s in n.body for c in ast.walk(s))]
        assert len(tries) == 1
        # Not inside the live-mode branch: the sleeve is a reading in paper too.
        ifs = [n for n in ast.walk(tree) if isinstance(n, ast.If)
               and any(c is calls[0] for s in n.body for c in ast.walk(s))]
        assert not any("is_live" in seg(i.test) for i in ifs)
        handler_src = seg(tries[0].handlers[0])
        assert "type(exc).__name__" in handler_src and "str(exc)" not in handler_src
