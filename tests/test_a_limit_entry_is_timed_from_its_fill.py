"""A limit entry is timed from its fill, not from when the order was placed.

The live card, 2026-09-26 08:04 UTC, printed ``Hold: 55m`` for a DOT/USDT
limit entry that was closed seconds after it filled. The hold was measured
from ``opened_at``, which is when the ORDER was placed, and a limit order can
rest for up to ``LIMIT_ORDER_EXPIRE_SEC`` (4h by default) before it fills.

The fill paths had always stamped ``filled_at``, and only the 90-second grace
gate read it. So everything that counts time on a position counted the rest
too: the executor's time stop, the engine's five time exits, every hold and
age on a card, the journal's hold, the post-mortem and the website's trade
record. A scalp limit that rested two hours was time-stopped at the first
monitor pass after it filled. And ``filled_at`` was never saved, so a restart
put every limit entry back on the placement clock.

``position_telemetry.entered_at`` is the one reading, and a ratchet over every
``opened_at`` read in ``bot/`` keeps a new hold from reading placement.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, closed_trade_row
from bot.core.position_telemetry import entered_at
from bot.core.trade_postmortem import _hold_hours
from tests.test_a_position_card_states_its_time_exits import (
    _line,
    _lp,
    _smart_exit_closes,
    _time_stop_closes,
)
from tests.test_an_abort_card_says_why_and_what_it_cost import GUESSED, _closed
from tests.test_journal_records_live_closes import _engine_stub, _journal, _Pos

UTC = timezone.utc
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _filled(pos, ago):
    setattr(pos, "filled_at", datetime.now(UTC) - ago)
    return pos


class TestTheReading:
    def test_a_fill_on_record_is_the_entry(self):
        p = _filled(_lp(opened_at=datetime.now(UTC) - timedelta(hours=3)),
                    timedelta(minutes=5))
        assert entered_at(p) == p.filled_at

    def test_a_market_entry_has_no_fill_stamp_and_reads_placement(self):
        p = _lp()
        assert getattr(p, "filled_at", None) is None
        assert entered_at(p) == p.opened_at

    def test_neither_is_none(self):
        assert entered_at(object()) is None


class TestTheExitsCountFromTheFill:
    def test_the_executor_time_stop(self):
        """Swing, placed 50h ago: under entry and above the stop, so the 48h
        time stop fires on the placement clock and not five minutes after the
        fill."""
        placed = datetime.now(UTC) - timedelta(hours=50)
        closed = _time_stop_closes(_lp(opened_at=placed), 1.99)
        assert closed and closed[0].startswith("TIME_STOP"), "the control must fire"
        fresh = _filled(_lp(opened_at=placed), timedelta(minutes=5))
        assert _time_stop_closes(fresh, 1.99) == []

    def test_a_fill_that_is_old_enough_still_fires(self):
        placed = datetime.now(UTC) - timedelta(hours=52)
        old = _filled(_lp(opened_at=placed), timedelta(hours=50))
        closed = _time_stop_closes(old, 1.99)
        assert closed and closed[0].startswith("TIME_STOP")

    def test_the_engine_time_exits(self):
        """+2R at 16.1h: the momentum hard limit closes the trade on the
        placement clock, and not ten minutes after it filled."""
        assert _smart_exit_closes(_lp(), 2.12), "the control must fire"
        assert _smart_exit_closes(_filled(_lp(), timedelta(minutes=10)), 2.12) == []

    def test_the_card_counts_down_from_the_fill(self):
        from tests.test_a_position_card_states_its_time_exits import NOW
        p = _lp(opened_at=NOW - timedelta(hours=5))
        setattr(p, "filled_at", NOW - timedelta(hours=1))
        line = _line(p, 2.03)
        assert "after 8h if under 1R (in 7h)" in line, line


class TestTheFillSurvivesARestart:
    def test_an_open_position(self, tmp_path):
        p = _filled(_lp(), timedelta(minutes=5))
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._positions[p.trade_id] = p
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path))._positions[p.trade_id]
        assert back.filled_at == p.filled_at
        assert entered_at(back) == p.filled_at

    def test_a_closed_position(self):
        p = _filled(_lp(status="closed", close_price=2.1, pnl_usd=1.0),
                    timedelta(minutes=5))
        p.closed_at = datetime.now(UTC)
        back = LiveExecutor._closed_row_to_position(closed_trade_row(p))
        assert back.filled_at == p.filled_at

    def test_a_market_entry_writes_none(self):
        assert closed_trade_row(_lp(status="closed"))["filled_at"] is None

    @pytest.mark.parametrize("raw,expect", [
        ("2026-09-26T07:10:00", datetime(2026, 9, 26, 7, 10, tzinfo=UTC)),
        ("2026-09-26T07:10:00+00:00", datetime(2026, 9, 26, 7, 10, tzinfo=UTC)),
        ("not a time", None),
        (12345, None),
        (None, None),
    ])
    def test_the_saved_value_is_read_or_ignored(self, raw, expect):
        """A naive time is the executor's own UTC; anything else is no fill on
        record, so the reading falls back to placement rather than raising."""
        row = closed_trade_row(_lp(status="closed"))
        row["filled_at"] = raw
        back = LiveExecutor._closed_row_to_position(row)
        assert getattr(back, "filled_at", None) == expect


class TestTheHoldsCountFromTheFill:
    def test_the_close_card(self, tmp_path):
        ex, pos = _closed(tmp_path, dict(GUESSED, pnl=-0.2182))
        _filled(pos, timedelta(minutes=10))
        msg = asyncio.run(ex._handle_already_closed_position(
            pos, bot_reason="sl_placement_failed", bot_closed=True))
        assert "Hold: 10m" in msg, msg
        assert ex._last_close_data["hold_time"] == "10m"

    @pytest.mark.parametrize("path", ["close", "reconcile"])
    def test_the_other_close_cards(self, path):
        from tests.test_an_abort_card_says_why_and_what_it_cost import _card
        msg, _pos = _card(path)
        assert "Hold: 10m" in msg, msg

    def test_the_journal(self, tmp_path):
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        RuneClawEngine._on_live_position_closed(
            eng, _Pos(filled_at=datetime.now(UTC) - timedelta(minutes=30)))
        assert 0.45 < j._entries[-1].holding_hours < 0.55

    def test_the_post_mortem(self):
        closed = datetime.now(UTC)
        p = _Pos(closed_at=closed, filled_at=closed - timedelta(hours=1))
        assert _hold_hours(p) == pytest.approx(1.0)

    def test_the_website_record(self, monkeypatch):
        import bot.utils.website_sync as ws
        from tests.test_live_website_sync import _live_pos, _stub_engine_for_sync
        seen = {}
        monkeypatch.setattr(ws, "sync_in_background",
                            lambda eq, positions, closed: seen.update(p=positions, c=closed))
        fill = datetime(2026, 6, 30, 12, 40, tzinfo=UTC)
        pos, done = _live_pos("TI-1", "HYPE/USDT:USDT"), _live_pos(
            "TI-2", "ENA/USDT:USDT", status="closed", pnl=0.1, close_price=63.6)
        pos.filled_at = done.filled_at = fill
        RuneClawEngine._sync_live_state_to_website(_stub_engine_for_sync([pos], [done]))
        assert seen["p"][0]["opened_at"] == fill
        assert seen["c"][0]["opened_at"] == fill


# ── the ratchet ──────────────────────────────────────────────────────────────

BASELINE = ROOT / "tests" / "opened_at_reads_baseline.txt"


def opened_at_reads(tree: ast.AST) -> dict[str, int]:
    """``qualname -> number of opened_at reads`` in one module."""
    out: dict[str, int] = {}
    stack: list[str] = []

    class V(ast.NodeVisitor):
        def _scoped(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

        def _count(self):
            key = ".".join(stack) or "<module>"
            out[key] = out.get(key, 0) + 1

        def visit_Attribute(self, node):
            if node.attr == "opened_at" and isinstance(node.ctx, ast.Load):
                self._count()
            self.generic_visit(node)

        def visit_Call(self, node):
            f = node.func
            if (isinstance(f, ast.Name) and f.id in ("getattr", "hasattr")
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "opened_at"):
                self._count()
            self.generic_visit(node)

    V().visit(tree)
    return out


def tree_reads(root: pathlib.Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted((root / "bot").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        for name, n in opened_at_reads(ast.parse(path.read_text())).items():
            found[f"{rel}::{name}"] = n
    found.pop("bot/core/position_telemetry.py::entered_at", None)
    return found


def baseline(text: str) -> dict[str, tuple[int, str]]:
    rows: dict[str, tuple[int, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, reason = line.partition("|")
        key, count = head.split()
        rows[key] = (int(count), reason.strip())
    return rows


def violations(found: dict[str, int], rows: dict[str, tuple[int, str]]) -> list[str]:
    out = []
    for key, n in sorted(found.items()):
        if key not in rows:
            out.append(f"{key}: reads opened_at {n}x and is not listed — a hold, "
                       f"an age or a time exit reads entered_at(pos)")
        elif rows[key][0] != n:
            out.append(f"{key}: reads opened_at {n}x, the baseline says {rows[key][0]}")
    for key, (_n, reason) in sorted(rows.items()):
        if key not in found:
            out.append(f"{key}: listed and reads no opened_at; delete the row")
        if not reason:
            out.append(f"{key}: listed with no reason")
    return out


class TestTheRatchet:
    def test_the_tree_matches_the_baseline(self):
        assert violations(tree_reads(ROOT), baseline(BASELINE.read_text())) == []

    def test_a_new_hold_off_placement_fails(self):
        tree = ast.parse("def hold(pos, now):\n    return now - pos.opened_at\n")
        assert opened_at_reads(tree) == {"hold": 1}
        assert violations({"m.py::hold": 1}, {})

    def test_getattr_is_a_read_too(self):
        tree = ast.parse("def hold(p):\n    return getattr(p, 'opened_at', None)\n")
        assert opened_at_reads(tree) == {"hold": 1}

    def test_an_assignment_is_not_a_read(self):
        tree = ast.parse("def stamp(p, t):\n    p.opened_at = t\n")
        assert opened_at_reads(tree) == {}

    def test_a_second_read_in_a_listed_function_fails(self):
        assert violations({"m.py::f": 2}, {"m.py::f": (1, "a reason")})

    def test_a_stale_row_fails(self):
        assert violations({}, {"m.py::f": (1, "a reason")})

    def test_a_row_with_no_reason_fails(self):
        assert violations({"m.py::f": 1}, baseline("m.py::f 1 |"))

    def test_the_reading_itself_is_the_one_exemption(self):
        assert "bot/core/position_telemetry.py::entered_at" not in tree_reads(ROOT)
