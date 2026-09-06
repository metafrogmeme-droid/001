"""Reviewing an adopted position required a code read to interpret its stops.

#999's card says "Use Positions to review" — and the /positions card then
showed `SL 6.32 / TP 7.72` with nothing distinguishing a strategy stop from
the 3%/6% safety default the adoption ladder places when nothing better is
found. Those two call for different actions (nothing vs review-and-tighten),
and the difference was only knowable by reading live_executor.py.

Adoption now records WHICH ladder rung supplied the levels — a runtime
marker like `unprotected` — and the card renders it:

  exchange  ⚑ informational: the exchange's own levels
  inherited ⚑ informational: the strategy record's levels
  default   ⚠️ actionable: 3%/6% SAFETY DEFAULTS, review and tighten

"default" is the conservative reading: even a partial default (real SL,
defaulted TP) marks the pair default, because the card's job is to say
"review these levels".
"""
from __future__ import annotations

import re

from bot.formatters.rich_cards import render_open_positions
from bot.utils.i18n import t

from tests.source_scan import code_only


def _row(**over):
    base = {
        "pair": "UNIUSDT", "direction": "LONG", "entry": 6.51, "current": 6.60,
        "pnl_pct": 1.4, "pnl_usd": 1.2, "sl": 6.32, "tp": 7.72,
        "size_usd": 100.0, "hold_hours": 2.0,
    }
    base.update(over)
    return base


def _card(**over) -> str:
    return render_open_positions([_row(**over)])


class TestTheCardNamesTheSource:
    def test_default_levels_carry_the_warning(self):
        card = _card(origin="adopted", sl_tp_source="default")
        assert "SAFETY DEFAULTS" in card
        assert "⚠️" in card

    def test_exchange_levels_are_informational(self):
        card = _card(origin="adopted", sl_tp_source="exchange")
        assert "exchange's own levels" in card
        assert "SAFETY DEFAULTS" not in card

    def test_inherited_levels_name_the_strategy_record(self):
        card = _card(origin="adopted", sl_tp_source="inherited")
        assert "inherited from the strategy record" in card

    def test_unknown_source_still_marks_adoption(self):
        # Positions adopted before this field existed keep an honest generic
        # marker — never a guessed source.
        card = _card(origin="adopted", sl_tp_source="")
        assert "Adopted from exchange" in card
        assert "SAFETY DEFAULTS" not in card

    def test_non_adopted_rows_carry_no_adoption_line(self):
        card = _card()
        assert "Adopted" not in card


class TestTheLadderRecordsProvenance:
    """The marker must be written where the levels are chosen, or the card
    renders a guess. Source-pinned per rung."""

    SRC = code_only(open("bot/core/live_executor.py", encoding="utf-8").read())

    def test_every_rung_stamps_its_source(self):
        for marker in ('"exchange"', '"inherited"', '"default"'):
            assert f'setattr(lp, "sl_tp_source", {marker})' in self.SRC, (
                f"the {marker} rung must stamp its own provenance")

    def test_partial_defaults_read_as_default(self):
        # The stamp sits inside the `if need_sl or need_tp:` block, so a
        # pair with one real level and one defaulted level reads "default" —
        # the conservative direction for a card whose job is "review".
        block = self.SRC.split("if need_sl or need_tp:", 1)[1][:400]
        assert 'setattr(lp, "sl_tp_source", "default")' in block

    def test_the_positions_payload_carries_both_fields(self):
        # Every file the handler class is made of: /open_positions lives in
        # the trading mixin since the handler split.
        from tests.source_scan import handler_sources
        src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
        assert '"origin": getattr(pos, "origin", "")' in src
        assert '"sl_tp_source": getattr(pos, "sl_tp_source", "")' in src


def test_i18n_default_warning_says_the_numbers():
    # The warning must carry the 3%/6% so the operator can sanity-check the
    # rendered SL/TP against it without opening the code.
    assert re.search(r"3%/6%", t("adopted_levels_default", "en"))
