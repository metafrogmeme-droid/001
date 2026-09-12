"""The chat prompt printed an adopted position's placeholders as measurements.

`adopt_exchange_positions` records 0.0 for an entry or margin the venue did not
state, 0 for an unstated leverage and 0 for a stop found nowhere on its ladder,
and names the unread fields in `adoption_unread` so "a reader can tell a
recorded 0 from a hole". The /positions card was taught to read that. The
chat prompt's ACTIVE POSITIONS row — the model's evidence about the user's own
money, on Telegram AND on the web, which calls the same `_llm_chat` — was not.
Driven against the same under-reported venue row the adoption suite uses, it
handed the model

    entry $0.0000, size $0.00, lev 0x, SL $0.0000, TP $0.0000

and, given a mark, `unrealized +N% ($+0.00)` from a quantity it never checked.
Each of those is a number the model repeats in a sentence that sounds
considered: "your stop is at $0" is `_cmd_open_positions`'s SL-None-for-every-
orphan incident, arriving through the chat.

TWO BASES, NEITHER LABELLED. The row's "unrealized +5.00%" was the raw price
move; /open_positions prints the return on margin (+50.00% at 10x). Same
position, two surfaces, two numbers — the -2.56%/-0.13% incident
`leveraged_return.py` opens with. Both print now under their own names, from
the helpers the card uses.

AND THE MARKERS LIVED FOR ONE PROCESS. `_save_positions` wrote none of
`origin`, `sl_tp_source`, `adoption_unread`, `unprotected`, and
`_load_positions` built every record as an "executed" position — so one
restart turned the adopted position above into a bot-opened one with an
entry of $0.0000, on the card and in the prompt alike.

THE RED HERRINGS, planted below: the quantity the venue DID state must stay
on the row; a 3%/6% safety default is a real stop and must not read as
UNPROTECTED; and a fully recorded bot position must print exactly the numbers
it always did.
"""
from __future__ import annotations

import inspect
import re
from types import SimpleNamespace as NS

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition, restore_provenance
from bot.skills import telegram_handler as th
from bot.skills.telegram_handler import _closed_trade_line, _live_position_row, _live_positions_block
from bot.utils.leveraged_return import _leveraged_pnl_usd, _leveraged_return_pct
from tests.source_scan import code_only
from tests.test_adoption_records_what_the_venue_stated import UNDER_REPORTED, _executor

PCT = re.compile(r"[+-]\d+\.\d+%")
DOLLAR_ZERO = re.compile(r"\$[+-]?0\.0+\b")


def _pos(**kw):
    """A fully recorded bot-opened position: margin x lev == entry x qty."""
    base = dict(direction="LONG", symbol="BTC/USDT", entry_price=60000.0,
                quantity=0.01, cost_usd=60.0, leverage=10,
                stop_loss=58000.0, take_profit=64000.0, status="open")
    base.update(kw)
    return NS(**base)


# ── THE FINDING, driven through the real adoption path ─────────────────────

@pytest.mark.asyncio
async def test_unread_fields_are_stated_not_printed_as_zero(monkeypatch, tmp_path):
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    out = _live_positions_block(ex, {"NFLX/USDT:USDT": 3.1})
    for must in ("entry NOT ON RECORD", "margin NOT ON RECORD", "lev NOT ON RECORD",
                 "SL NONE ON RECORD", "TP NONE ON RECORD", "ADOPTED",
                 "did not state entry_price, margin, leverage", "UNPROTECTED"):
        assert must in out, must
    for never in ("$0.0000", "size $", "lev 0x", "lev 1x"):
        assert never not in out, never
    assert not DOLLAR_ZERO.search(out), out


@pytest.mark.asyncio
async def test_a_mark_without_an_entry_yields_no_figure_at_all(monkeypatch, tmp_path):
    """The mark is real and is printed; nothing is computed FROM it."""
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    out = _live_positions_block(ex, {"NFLX/USDT:USDT": 3.1})
    assert "MARK $3.1000" in out
    assert "CANNOT BE COMPUTED" in out
    assert "do not estimate" in out
    assert not PCT.search(out), "a percentage was manufactured with no entry"
    assert "unrealized $" not in out


@pytest.mark.asyncio
async def test_the_quantity_the_venue_did_state_is_kept(monkeypatch, tmp_path):
    """RED HERRING. Contracts is the one field adoption requires; a fix that
    swept every field of an under-reported row into 'not on record' would be
    the same defect pointing the other way."""
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    out = _live_positions_block(ex, {})
    assert "qty 3" in out
    assert "qty NOT ON RECORD" not in out


# ── The markers survive a restart ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_provenance_survives_a_restart(monkeypatch, tmp_path):
    ex = _executor(monkeypatch, UNDER_REPORTED, tmp_path)
    await ex.adopt_exchange_positions()
    ex._save_positions()

    fresh = LiveExecutor(state_dir=str(tmp_path))
    pos = next(iter(fresh._positions.values()))
    assert pos.origin == "adopted"
    assert set(getattr(pos, "adoption_unread", ())) >= {"entry_price", "margin", "leverage"}
    assert getattr(pos, "unprotected", False) is True

    out = _live_positions_block(fresh, {"NFLX/USDT:USDT": 3.1})
    assert "ADOPTED" in out and "did not state" in out and "UNPROTECTED" in out
    assert "$0.0000" not in out


def test_sl_tp_source_round_trips(tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    pos = LivePosition(trade_id="t1", symbol="UNI/USDT", direction="LONG",
                       entry_price=6.51, quantity=100.0, cost_usd=65.1,
                       stop_loss=6.31, take_profit=6.9, leverage=10, origin="adopted")
    setattr(pos, "sl_tp_source", "default")
    ex._positions["t1"] = pos
    ex._save_positions()
    fresh = LiveExecutor(state_dir=str(tmp_path))
    assert getattr(fresh._positions["t1"], "sl_tp_source", None) == "default"
    assert fresh._positions["t1"].origin == "adopted"


def test_a_bot_opened_position_restores_with_no_markers(tmp_path):
    """Every reader goes through getattr(pos, name, default); a restored record
    must look like a fresh one, so nothing is set that was not recorded."""
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._positions["t1"] = LivePosition(
        trade_id="t1", symbol="BTC/USDT", direction="LONG", entry_price=60000.0,
        quantity=0.01, cost_usd=60.0, stop_loss=58000.0, take_profit=64000.0, leverage=10)
    ex._save_positions()
    pos = LiveExecutor(state_dir=str(tmp_path))._positions["t1"]
    assert pos.origin == "executed"
    for marker in ("sl_tp_source", "adoption_unread", "unprotected"):
        assert not hasattr(pos, marker), marker


def test_a_record_written_before_these_keys_restores_as_it_always_did():
    pos = LivePosition(trade_id="t", symbol="X/USDT", direction="LONG", entry_price=1.0,
                       quantity=1.0, cost_usd=1.0, stop_loss=0.9, take_profit=1.1)
    restore_provenance(pos, {"trade_id": "t", "symbol": "X/USDT"})
    for marker in ("sl_tp_source", "adoption_unread", "unprotected"):
        assert not hasattr(pos, marker), marker


def test_a_false_unprotected_is_not_restored_as_a_marker():
    pos = LivePosition(trade_id="t", symbol="X/USDT", direction="LONG", entry_price=1.0,
                       quantity=1.0, cost_usd=1.0, stop_loss=0.9, take_profit=1.1)
    restore_provenance(pos, {"unprotected": False, "adoption_unread": [], "sl_tp_source": ""})
    for marker in ("sl_tp_source", "adoption_unread", "unprotected"):
        assert not hasattr(pos, marker), marker


# ── Both bases, both labelled, from the card's own helpers ─────────────────

class TestBothBasesAreLabelled:
    def test_price_move_and_return_on_margin_both_print(self):
        out = _live_position_row(_pos(), 63000.0)
        assert "price move +5.00% from entry" in out
        assert "return on margin +50.00% at 10x" in out
        assert "unrealized $+30.00" in out

    def test_a_short_inverts_both_and_the_dollar(self):
        out = _live_position_row(_pos(direction="SHORT"), 63000.0)
        assert "price move -5.00%" in out
        assert "return on margin -50.00%" in out
        assert "unrealized $-30.00" in out

    def test_the_dollar_agrees_with_the_card_helper(self):
        """Quantity route here, margin route on the card; on a consistent
        record they are one number, and this is where that is pinned."""
        p = _pos()
        out = _live_position_row(p, 63000.0)
        card = _leveraged_pnl_usd(p.entry_price, 63000.0, "LONG", p.cost_usd, p.leverage)
        assert f"unrealized ${card:+,.2f}" in out

    def test_the_margin_return_is_the_card_helper(self):
        p = _pos(leverage=20)
        out = _live_position_row(p, 61000.0)
        card = _leveraged_return_pct(p.entry_price, 61000.0, "LONG", 20)
        assert f"return on margin {card:+.2f}% at 20x" in out

    def test_a_fully_recorded_row_never_says_not_on_record(self):
        """RED HERRING at the happy path: the old numbers, exactly."""
        out = _live_position_row(_pos(), 63000.0)
        for never in ("NOT ON RECORD", "NONE ON RECORD", "ADOPTED", "UNPROTECTED",
                      "did not state", "CANNOT BE COMPUTED", "NOT COMPUTABLE"):
            assert never not in out, never
        for must in ("entry $60,000.0000", "qty 0.01", "margin $60.00", "lev 10x",
                     "notional $600.00", "SL $58,000.0000", "TP $64,000.0000",
                     "MARK $63,000.0000"):
            assert must in out, must

    def test_leverage_not_on_record_withholds_the_margin_return_only(self):
        # No margin, no leverage: the price move and the dollar are still
        # readings — entry, quantity and mark are all on record.
        out = _live_position_row(_pos(leverage=0, cost_usd=0.0), 63000.0)
        assert "lev NOT ON RECORD" in out
        assert "return on margin NOT COMPUTABLE" in out
        assert "price move +5.00% from entry" in out
        assert "unrealized $+30.00" in out
        assert "at 0x" not in out and "at 1x" not in out

    def test_leverage_is_derived_from_margin_and_notional_when_unstored(self):
        """`position_leverage`'s rule, and the reason the row uses it rather
        than reading `pos.leverage` by hand."""
        out = _live_position_row(_pos(leverage=0), 63000.0)
        assert "lev 10x" in out
        assert "return on margin +50.00% at 10x" in out

    def test_a_measured_break_even_still_prints_zero(self):
        """0.0 is a real outcome; the rule is about absence, not about zero."""
        out = _live_position_row(_pos(), 60000.0)
        assert "price move +0.00% from entry" in out
        assert "unrealized $+0.00" in out


# ── Absent is words, never a number ────────────────────────────────────────

class TestAbsentFieldsAreWords:
    @pytest.mark.parametrize("absent", [0, 0.0, -1, None, "n/a", float("nan"), float("inf")])
    def test_an_absent_entry(self, absent):
        out = _live_position_row(_pos(entry_price=absent), 63000.0)
        assert "entry NOT ON RECORD" in out
        assert "CANNOT BE COMPUTED" in out
        assert not PCT.search(out)
        assert "entry $" not in out

    @pytest.mark.parametrize("absent", [0, 0.0, None, "n/a", float("nan")])
    def test_an_absent_margin(self, absent):
        out = _live_position_row(_pos(cost_usd=absent, leverage=0), 63000.0)
        assert "margin NOT ON RECORD" in out
        assert "margin $" not in out

    @pytest.mark.parametrize("absent", [0, 0.0, None, "n/a", float("nan")])
    def test_an_absent_quantity_withholds_the_dollar_not_the_percent(self, absent):
        out = _live_position_row(_pos(quantity=absent), 63000.0)
        assert "qty NOT ON RECORD" in out
        assert "unrealized $ NOT COMPUTABLE" in out
        assert "price move +5.00%" in out
        assert not DOLLAR_ZERO.search(out), out

    @pytest.mark.parametrize("absent", [0, 0.0, None, "n/a"])
    def test_no_stop_is_not_a_stop_at_zero(self, absent):
        out = _live_position_row(_pos(stop_loss=absent), 63000.0)
        assert "SL NONE ON RECORD" in out
        assert "do not describe one" in out
        assert "SL $" not in out

    @pytest.mark.parametrize("absent", [0, 0.0, None])
    def test_no_target_is_not_a_target_at_zero(self, absent):
        out = _live_position_row(_pos(take_profit=absent), 63000.0)
        assert "TP NONE ON RECORD" in out
        assert "TP $" not in out

    def test_a_missing_mark_is_still_stated_not_omitted(self):
        out = _live_position_row(_pos(), None)
        assert "MARK UNAVAILABLE" in out and "do NOT know" in out
        assert "price move" not in out and "unrealized" not in out

    def test_a_stated_zero_margin_carries_no_venue_excuse(self):
        """RED HERRING at the row level. A margin the venue STATED as 0.0 is
        not in `adoption_unread`; it is unusable as a basis, so it reads as
        not on record, but the row must not claim the venue never said."""
        out = _live_position_row(_pos(cost_usd=0.0, origin="adopted"), 63000.0)
        assert "margin NOT ON RECORD" in out
        assert "did not state" not in out


# ── Stop provenance ────────────────────────────────────────────────────────

class TestStopProvenance:
    def test_default_levels_say_so_and_are_not_unprotected(self):
        """RED HERRING. A 3%/6% safety default is a REAL stop on the venue."""
        out = _live_position_row(_pos(origin="adopted", sl_tp_source="default"), 63000.0)
        assert "SAFETY DEFAULTS" in out and "not strategy levels" in out
        assert "UNPROTECTED" not in out
        assert "SL $58,000.0000" in out

    def test_inherited_and_exchange_levels_are_named(self):
        assert "inherited from the bot's own strategy record" in _live_position_row(
            _pos(origin="adopted", sl_tp_source="inherited"), 63000.0)
        assert "the exchange's own levels" in _live_position_row(
            _pos(origin="adopted", sl_tp_source="exchange"), 63000.0)

    def test_the_unprotected_marker_is_loud(self):
        out = _live_position_row(_pos(origin="adopted", stop_loss=0, unprotected=True), 63000.0)
        assert "UNPROTECTED" in out and "place one now" in out
        assert "SL NONE ON RECORD" in out

    def test_a_reclaimed_order_says_what_it_is(self):
        assert "re-tracked after a restart" in _live_position_row(_pos(origin="reclaimed"), 63000.0)

    def test_an_adopted_row_with_everything_recorded_names_no_unread_field(self):
        out = _live_position_row(_pos(origin="adopted"), 63000.0)
        assert "ADOPTED from the exchange" in out
        assert "did not state" not in out


# ── Wiring: one seam, and the other surface is the same surface ───────────

def test_the_block_builds_every_row_through_the_seam():
    """A row built inline again would be a row nothing can drive."""
    body = code_only(inspect.getsource(_live_positions_block))
    assert "_live_position_row(" in body
    assert "cost_usd:,.2f" not in body and "lev {p.leverage}" not in body


def test_the_web_chat_has_no_positions_block_of_its_own():
    """The web's chat routes call `tg_handler._llm_chat`, so the prompt the
    model reads there is built by the same code: one fix, both surfaces. This
    pins that no second builder has appeared."""
    import bot.web.user_gateway as ug
    assert "ACTIVE POSITIONS" not in code_only(inspect.getsource(ug))
    assert "tg_handler._llm_chat(" in inspect.getsource(ug)


def test_the_prompt_builder_still_calls_the_block():
    src = code_only(inspect.getsource(th.TelegramHandler._build_chat_system_prompt))
    assert "_live_positions_block(" in src


# ── The sibling row: RECENT CLOSED TRADES ──────────────────────────────────

def _closed(**kw):
    base = dict(trade_id="t", symbol="BTC/USDT", direction="LONG", entry_price=60000.0,
                quantity=0.01, cost_usd=60.0, stop_loss=58000.0, take_profit=64000.0,
                leverage=10, status="closed", close_price=63000.0, pnl_usd=29.5,
                close_reason="TP")
    base.update(kw)
    return LivePosition(**base)


class TestAnUnpricedCloseIsNotAnExitAtEntry:
    def test_the_finding(self):
        """`close_position` books close_price=None / pnl_usd=None when the exit
        could not be read; the row said `exit $60,000.0000` — the ENTRY."""
        out = _closed_trade_line(_closed(close_price=None, pnl_usd=None, close_reason="SL"))
        assert "exit NOT ON RECORD" in out and "could not be read" in out
        assert "PnL not recorded" in out and "do not call it flat" in out
        assert "exit $" not in out
        assert not DOLLAR_ZERO.search(out), out

    def test_a_restored_null_reads_the_same(self):
        """`_load_closed_trades` turns a persisted null close price into 0.0."""
        out = _closed_trade_line(_closed(close_price=0.0, pnl_usd=None))
        assert "exit NOT ON RECORD" in out
        assert "exit $" not in out

    def test_a_priced_close_prints_its_numbers(self):
        out = _closed_trade_line(_closed())
        assert "entry $60,000.0000" in out
        assert "exit $63,000.0000" in out
        assert "PnL $+29.50" in out
        assert "closed via TP" in out
        assert "NOT ON RECORD" not in out and "not recorded" not in out

    def test_a_measured_break_even_close_still_prints_zero(self):
        """RED HERRING. 0.0 is a measured outcome; only None is an absence."""
        out = _closed_trade_line(_closed(pnl_usd=0.0))
        assert "PnL $+0.00" in out
        assert "not recorded" not in out

    def test_an_adopted_close_with_no_entry_is_named_not_zeroed(self):
        out = _closed_trade_line(_closed(entry_price=0.0, origin="adopted"))
        assert "entry NOT ON RECORD" in out
        assert "adopted from the exchange" in out
        assert "$0.0000" not in out

    @pytest.mark.parametrize("reason,shown", [
        ("SL", True), ("TP", True), ("manual", True), ("time_stop", True),
        ("", False), (None, False),
        ("Exception: ccxt.NetworkError bitget GET /api/v2 timed out", False),
        ("a" * 40, False),
    ])
    def test_the_close_reason_is_named_only_when_it_is_a_token(self, reason, shown):
        out = _closed_trade_line(_closed(close_reason=reason))
        assert ("closed via" in out) is shown, out
        if not shown and reason:
            assert reason not in out

    def test_the_builder_uses_the_seam_and_the_fallback_is_gone(self):
        src = code_only(inspect.getsource(th.TelegramHandler._build_chat_system_prompt))
        assert "_closed_trade_line(" in src
        assert "close_price or" not in src
