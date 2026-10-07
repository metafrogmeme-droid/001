"""A second order is not placed beside one still resting on the symbol, and a
live account is not offered an add its executor refuses.

The engine's same-symbol guard read `live_executor.open_positions`, which
lists open AND `pending_fill` rows, and measured a same-direction add's "1R in
profit" from the row's entry price -- for a resting limit, a price nothing has
filled at. Driven through the guard: a BTC long limit resting at 95 (stop 93),
the market at 97, read "1.00R profit" and a pyramid add was approved. The flag
skips confirm's duplicate check by design, and the executor's own same-symbol
guard counted `open` rows only, so a second order went out beside the resting
one. When both fill, the book holds two records on one symbol and direction,
and the duplicate merge folds them into one every five minutes, leaving the
other quantity on the venue with nothing tracking it.

Two more fell out of the same guard. In live mode it fell back to the shared
PAPER book whenever the live book held nothing on the symbol; nothing in this
build writes that book, so what it holds there is a paper position restored
from before the account went live, and driven, a stale paper SHORT refused a
live LONG as a "flip". And a same-direction add on an OPEN live position was
approved and offered although the executor holds one position per symbol and
refuses a second whatever the engine decided: a Take-it that is always
refused, retried by auto-confirm every tick until the idea lapsed.

The paper book keeps its add rules; they are pinned here as they were.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import engine as eng_mod
from bot.core import live_executor as le
from bot.core.engine import RuneClawEngine, _direction_word
from bot.core.live_executor import LiveExecutor, LivePosition, normalize_symbol
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only
from tests.test_a_resting_limit_is_sized_at_its_own_price import _Recording
from tests.test_the_placed_order_is_the_checked_order import _market

KEY = normalize_symbol("BTC/USDT")


def _idea(direction=Direction.LONG, *, asset="BTC/USDT", entry=97.0, conf=0.80):
    long = direction == Direction.LONG
    return TradeIdea(asset=asset, direction=direction, entry_price=entry,
                     stop_loss=entry - 3 if long else entry + 3,
                     take_profit=entry + 6 if long else entry - 6,
                     confidence=conf, blended_confidence_raw=conf,
                     reasoning="fixture", source="unknown")


def _row(status="pending_fill", direction="LONG", symbol="BTC/USDT", tid="R1",
         entry=95.0, stop=93.0):
    return LivePosition(trade_id=tid, symbol=symbol, direction=direction,
                        entry_price=entry, quantity=0.001, stop_loss=stop,
                        take_profit=entry + 5, cost_usd=20.0, leverage=5,
                        status=status)


def _paper(direction=Direction.LONG, *, entry=90.0, stop=87.0, asset="BTC/USDT"):
    return SimpleNamespace(asset=asset, direction=direction, entry_price=entry,
                           stop_loss=stop, trade_id="P1")


def _verdict(*, live, live_rows=(), paper_rows=(), idea=None, executor=True):
    host = RuneClawEngine.__new__(RuneClawEngine)
    host.live_executor = (SimpleNamespace(open_positions=list(live_rows))
                          if executor else None)
    host.portfolio = SimpleNamespace(open_positions=list(paper_rows))
    audits: list[dict] = []
    with patch.object(type(CONFIG), "is_live", lambda self: live), \
            patch.object(eng_mod, "audit",
                         lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        out = host._same_symbol_verdict(idea or _idea(), KEY)
    return out, audits


def _actions(audits):
    return [a.get("action") for a in audits]


# ── live mode: the guard reads the live book and nothing else ──────────────

class TestLiveMode:

    def test_a_resting_order_is_not_a_position_in_profit(self):
        # The driven case: 97 is 1R above a resting 95 with its stop at 93.
        out, audits = _verdict(live=True, live_rows=[_row()])
        assert out == "skip"
        assert _actions(audits) == ["resting_order_on_symbol"]
        assert "still resting" in audits[0]["message"]
        assert "R1" in audits[0]["message"]

    def test_a_resting_order_skips_the_opposite_side_too(self):
        out, audits = _verdict(live=True, live_rows=[_row()],
                               idea=_idea(Direction.SHORT))
        assert out == "skip"
        assert _actions(audits) == ["resting_order_on_symbol"]

    def test_a_resting_order_is_read_before_the_count(self):
        # A resting row beside an open one: the resting order is the reason,
        # because it is the order the second one would stand beside.
        out, audits = _verdict(live=True, live_rows=[_row(status="open", tid="O1"),
                                                     _row(tid="R2")])
        assert out == "skip"
        assert _actions(audits) == ["resting_order_on_symbol"]
        assert "R2" in audits[0]["message"]

    def test_a_held_symbol_is_offered_no_add(self):
        out, audits = _verdict(live=True, live_rows=[_row(status="open", tid="O1")])
        assert out == "skip"
        assert _actions(audits) == ["pyramid_live_one_per_symbol"]
        assert "O1" in audits[0]["message"]
        assert "held LONG" in audits[0]["message"]

    def test_a_held_symbol_is_offered_no_add_however_far_in_profit(self):
        # Ten R above entry, measured confidence: still no add, because the
        # executor holds one position per symbol and would refuse it.
        out, _ = _verdict(live=True, live_rows=[_row(status="open", entry=60.0, stop=57.0)],
                          idea=_idea(conf=0.95))
        assert out == "skip"

    def test_the_opposite_side_of_a_held_symbol_is_a_blocked_flip_in_words(self):
        out, audits = _verdict(live=True, live_rows=[_row(status="open", direction="SHORT")])
        assert out == "skip"
        assert _actions(audits) == ["flip_blocked"]
        assert "SHORT -> LONG" in audits[0]["message"]
        assert "Direction." not in audits[0]["message"]
        assert audits[0]["data"]["existing"] == "SHORT"

    def test_a_held_symbol_in_lower_case_is_the_same_side(self):
        out, audits = _verdict(live=True, live_rows=[_row(status="open", direction="long")])
        assert _actions(audits) == ["pyramid_live_one_per_symbol"]

    def test_two_rows_on_the_symbol_are_the_maximum(self):
        out, audits = _verdict(live=True, live_rows=[_row(status="open", tid="A"),
                                                     _row(status="open", tid="B")])
        assert out == "skip"
        assert _actions(audits) == ["pyramid_maxed"]

    def test_the_paper_book_is_not_read_in_live_mode(self):
        # The driven case: a stale paper SHORT refused a live LONG as a flip.
        out, audits = _verdict(live=True, paper_rows=[_paper(Direction.SHORT)])
        assert (out, audits) == ("clear", [])

    def test_a_paper_position_the_same_way_is_not_an_add_in_live_mode(self):
        out, audits = _verdict(live=True, paper_rows=[_paper(entry=60.0, stop=57.0)],
                               idea=_idea(conf=0.95))
        assert (out, audits) == ("clear", [])

    def test_another_symbol_is_clear(self):
        out, audits = _verdict(live=True, live_rows=[_row(symbol="ETH/USDT")])
        assert (out, audits) == ("clear", [])

    def test_the_same_market_in_another_spelling_is_the_symbol(self):
        out, _ = _verdict(live=True, live_rows=[_row(symbol="BTC/USDT:USDT")])
        assert out == "skip"

    def test_no_live_executor_is_clear_not_a_raise(self):
        out, audits = _verdict(live=True, executor=False,
                               paper_rows=[_paper(Direction.SHORT)])
        assert (out, audits) == ("clear", [])


# ── paper mode: the add rules, as they were ─────────────────────────────────

class TestPaperMode:

    def test_a_same_way_position_one_r_in_profit_is_an_add(self):
        out, audits = _verdict(live=False, paper_rows=[_paper(entry=90.0, stop=87.0)],
                               idea=_idea(entry=94.0))
        assert out == "pyramid"
        assert _actions(audits) == ["pyramid_approved"]

    def test_under_one_r_is_no_add(self):
        out, audits = _verdict(live=False, paper_rows=[_paper(entry=90.0, stop=87.0)],
                               idea=_idea(entry=91.0))
        assert out == "skip"
        assert _actions(audits) == ["pyramid_insufficient_profit"]

    def test_an_unmeasured_confidence_is_no_add(self):
        idea = _idea(entry=94.0)
        idea.blended_confidence_raw = None
        idea.source = "manual"
        out, audits = _verdict(live=False, paper_rows=[_paper(entry=90.0, stop=87.0)],
                               idea=idea)
        assert out == "skip"
        assert _actions(audits) == ["pyramid_low_conf"]

    def test_the_opposite_side_is_a_blocked_flip_in_words(self):
        out, audits = _verdict(live=False, paper_rows=[_paper(Direction.SHORT)])
        assert out == "skip"
        assert _actions(audits) == ["flip_blocked"]
        assert "SHORT -> LONG" in audits[0]["message"]
        assert "Direction." not in audits[0]["message"]

    def test_the_live_book_is_not_read_in_paper_mode(self):
        out, audits = _verdict(live=False, live_rows=[_row(status="open")])
        assert (out, audits) == ("clear", [])


def test_the_direction_word_reads_every_spelling():
    assert _direction_word(Direction.SHORT) == "SHORT"
    assert _direction_word("long") == "LONG"
    assert _direction_word(None) == ""


# ── the executor: its own guard counts a resting row ───────────────────────

def _preflight(tmp_path, rows, symbol="BTC/USDT"):
    ex = LiveExecutor(state_dir=str(tmp_path), user_id="")
    ex._positions = {r.trade_id: r for r in rows}
    return ex._preflight_check(10.0, symbol=symbol)


class TestTheExecutorsGuard:

    def test_a_resting_row_refuses_a_second_order(self, tmp_path):
        err = _preflight(tmp_path, [_row()])
        assert err and "still resting" in err and "R1" in err

    def test_a_resting_row_in_another_spelling_refuses(self, tmp_path):
        err = _preflight(tmp_path, [_row(symbol="BTC/USDT:USDT")])
        assert err and "still resting" in err

    def test_an_open_row_still_refuses_in_its_own_words(self, tmp_path):
        err = _preflight(tmp_path, [_row(status="open")])
        assert err and err.startswith("Already have an open LONG position")

    def test_another_symbol_resting_does_not_refuse(self, tmp_path):
        assert _preflight(tmp_path, [_row(symbol="ETH/USDT")]) is None

    @pytest.mark.parametrize("status", ["closed", "closing"])
    def test_a_row_that_is_not_open_or_resting_does_not_refuse(self, tmp_path, status):
        assert _preflight(tmp_path, [_row(status=status)]) is None


@pytest.fixture
def _five_x():
    RUNTIME.leverage_override = 5
    yield
    RUNTIME.leverage_override = None


def test_execute_places_nothing_beside_a_resting_order(tmp_path, _five_x):
    """End to end through the real `execute`: a pyramid-flagged idea skips the
    engine's confirm check, and this is the line that still refuses it."""
    venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), 4000.0)
    ex = LiveExecutor(state_dir=tmp_path)
    ex._exchange = venue
    ex._user_leverage_pref = None
    ex._positions = {"R1": _row(symbol="ETH/USDT", entry=3900.0, stop=3800.0)}
    idea = TradeIdea(id="TI-ADD-1", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=4000.0, stop_loss=3920.0, take_profit=4240.0,
                     confidence=0.8, reasoning="fixture", source="unknown")
    with patch.object(le, "audit", lambda *a, **k: None), \
            patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
            patch.object(type(CONFIG), "is_live", return_value=True):
        result = asyncio.run(ex.execute(idea, size_usd=20.0, order_type="market"))
    assert result.startswith("BLOCKED: An order on ETH/USDT is still resting")
    assert venue.sent == []


# ── wiring: the analysis asks the seam, and maps its words ─────────────────

def _analyze_src():
    return textwrap.dedent(inspect.getsource(RuneClawEngine._analyze_signal))


def test_the_analysis_asks_the_seam_once_and_maps_its_three_words():
    """A scan, and stated as one: `_analyze_signal` is several hundred lines
    behind a scanner, an analyzer and an exchange. The claim is the wiring:
    one call, a skip returns no idea, and only a pyramid sets the add flag."""
    tree = ast.parse(_analyze_src())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_same_symbol_verdict"]
    assert len(calls) == 1

    def _branch(word):
        found = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "_same"
                 and isinstance(n.test.ops[0], ast.Eq)
                 and isinstance(n.test.comparators[0], ast.Constant)
                 and n.test.comparators[0].value == word]
        assert len(found) == 1, word
        return found[0].body

    skip = _branch("skip")
    assert len(skip) == 1 and isinstance(skip[0], ast.Return)
    assert isinstance(skip[0].value, ast.Constant) and skip[0].value.value is None
    pyramid = _branch("pyramid")
    assert len(pyramid) == 1
    assert ast.unparse(pyramid[0]) == "self._pending_pyramid[idea.id] = True"
    # Only that branch sets the add flag, and the inline guard is gone (the
    # live book is still read here, for the slot count, which is another
    # question).
    src = code_only(_analyze_src())
    assert src.count("self._pending_pyramid[idea.id]") == 1
    assert "existing_positions" not in src


# ── confirm's duplicate check reads the book the confirm places on ─────────

class TestConfirmReadsTheAccountItPlacesOn:
    """`confirm_trade` suppressed a confirm when the OPERATOR's book held the
    symbol, whoever was confirming. Driven under per-user live: a linked
    trader whose own account was flat was told "already have an open/pending
    order" because the operator held BTC, and the trader's idea was dropped.
    The check reads `_executor_for(user_id)`, the resolution the placement
    takes."""

    @staticmethod
    def _engine(own_rows=(), operator_rows=(), resolver="own"):
        from tests.test_duplicate_entry_guard import _FakeEngine, _FakeExec
        eng = _FakeEngine({"T7": SimpleNamespace(
            asset="BTC/USDT", direction=SimpleNamespace(value="LONG"))})
        eng.live_executor._pos.extend(operator_rows)
        own = _FakeExec()
        own._pos.extend(own_rows)
        if resolver == "own":
            eng._executor_for = lambda uid="", venue="": own if uid == "u7" else eng.live_executor
        elif resolver == "raises":
            def _boom(uid="", venue=""):
                raise RuntimeError("store down")
            eng._executor_for = _boom
        elif resolver == "none":
            eng._executor_for = lambda uid="", venue="": None
        return eng

    @staticmethod
    def _confirm(eng, uid="u7"):
        # The wrapper asks `confirm_is_practice` first, which reads the
        # practice opt-in flag; off, as it ships.
        with patch.object(eng_mod, "CONFIG", SimpleNamespace(
                is_live=lambda: True, paper_sim_opt_in_enabled=False)):
            return asyncio.run(eng.confirm_trade("T7", user_id=uid))

    _HELD = SimpleNamespace(symbol="BTC/USDT", status="open")
    _RESTING = SimpleNamespace(symbol="BTC/USDT:USDT", status="pending_fill")

    def test_the_operators_position_does_not_refuse_a_traders_confirm(self):
        eng = self._engine(operator_rows=[self._HELD])
        out = self._confirm(eng)
        assert "duplicate suppressed" not in out
        assert eng.inner_calls == ["T7"]
        assert "T7" in eng._pending_ideas

    def test_the_traders_own_resting_order_refuses_it(self):
        eng = self._engine(own_rows=[self._RESTING])
        out = self._confirm(eng)
        assert "duplicate suppressed" in out
        assert eng.inner_calls == []
        assert "T7" not in eng._pending_ideas

    def test_the_operators_own_confirm_still_reads_the_operators_book(self):
        eng = self._engine(operator_rows=[self._HELD])
        out = self._confirm(eng, uid="auto")
        assert "duplicate suppressed" in out

    @pytest.mark.parametrize("resolver", ["raises", "none"])
    def test_a_resolution_that_fails_keeps_the_operators_book(self, resolver):
        eng = self._engine(operator_rows=[self._HELD], resolver=resolver)
        out = self._confirm(eng)
        assert "duplicate suppressed" in out
        assert eng.inner_calls == []
