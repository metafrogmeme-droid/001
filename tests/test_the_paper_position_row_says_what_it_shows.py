"""The status card's paper position row: a stop that differs from its entry
prints differently, the size figure is named for what it is, and a level of
zero is no level on record.

Driven through the real `paper_position_row`, which the status card calls
for every paper position (bot/skills/skill_registry.py).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

from bot.skills.skill_registry import PlaybookSkill, paper_position_row
from bot.utils.models import Direction
from tests.source_scan import code_only


def _pos(entry=0.0712, sl=0.0734, tp=0.0651, qty=96_000.0, side=Direction.SHORT):
    return NS(asset="XPL/USDT", direction=side, entry_price=entry, quantity=qty,
              stop_loss=sl, take_profit=tp)


def _line(row: str, label: str) -> str:
    return next(line for line in row.splitlines() if line.strip().startswith(f"- {label}:"))


def test_a_sub_dollar_stop_does_not_print_as_its_entry():
    row = paper_position_row(_pos())
    assert _line(row, "Entry") != _line(row, "SL").replace("SL", "Entry")
    assert "$0.07120" in _line(row, "Entry") and "$0.07340" in _line(row, "SL")


def test_the_size_figure_is_named_as_the_notional_at_entry():
    row = paper_position_row(_pos())
    assert "$6,835.20" in _line(row, "Notional at entry")
    assert not any(line.strip().startswith("- Size:") for line in row.splitlines())


def test_a_level_of_zero_is_none_on_record_not_zero_dollars():
    row = paper_position_row(_pos(sl=0.0, tp=None))
    assert "none on record" in _line(row, "SL") and "none on record" in _line(row, "TP")
    assert "$0.00" not in row


def test_an_unread_entry_gives_no_notional():
    row = paper_position_row(_pos(entry=0.0))
    assert "none on record" in _line(row, "Entry")
    assert "--" in _line(row, "Notional at entry")


def test_a_long_row_carries_the_long_marker():
    assert "▲" in paper_position_row(_pos(side=Direction.LONG))
    assert "▼" in paper_position_row(_pos(side=Direction.SHORT))


def test_the_status_card_renders_paper_rows_through_it():
    src = code_only(inspect.getsource(PlaybookSkill.execute))
    assert "paper_position_row(pos)" in src
