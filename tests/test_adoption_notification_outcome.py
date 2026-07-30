""""SL/TP may not be set — check and add manually" told the operator to redo
work adoption already does.

By the time the adoption notification fires, every adopted position has been
through the full protection ladder (exchange-set levels → plan-channel read →
donor inheritance → 3%/6% safety stops placed with retry), or carries the
RC-AUD-022 `unprotected` marker because the stop genuinely could not land.
The old footer was a blanket maybe: it under-claimed the protected cases and
— worse — said nothing louder for the one case that needs a human NOW.

The card now states each position's actual outcome. Pinned by rendering the
card body against stub positions.
"""
from __future__ import annotations

import re
from tests.source_scan import code_only

SRC = open("bot/skills/telegram_handler.py", encoding="utf-8").read()
CODE = code_only(SRC)
# The card body moved to a PURE renderer so it could be exercised at all —
# living inline in the handler is why #999's per-position detail shipped and
# never rendered once. These source scans follow it there.
#
# Note what happened when it moved: the SOURCE-SCANNING assertions below all
# broke on a behaviour-preserving refactor, while the behavioural scenarios
# in test_surface_scenarios.py passed untouched. That is the argument for
# the scenario suite in one observation.
CARD_SRC = open("bot/formatters/rich_cards.py", encoding="utf-8").read()
CARD = code_only(CARD_SRC)


def test_the_blanket_maybe_is_gone():
    assert "may not be set" not in CODE and "may not be set" not in CARD, (
        "the footer must state outcomes, not a maybe that sends the operator "
        "to redo the ladder adoption already ran"
    )


def test_the_unprotected_case_is_loud_and_directive():
    # RC-AUD-022's marker is the one state where a human must act NOW.
    assert "UNPROTECTED" in CARD
    assert "Set one NOW" in CARD


def test_protected_positions_show_their_levels():
    block = CARD.split("Adopted Exchange Positions", 1)[1]
    assert "stop_loss" in block and "take_profit" in block


def test_the_lookup_is_scoped_to_adopted_open_positions():
    # The card must not attribute a NON-adopted position's levels to an
    # adopted symbol that shares its name.
    block = CARD.split("Adopted Exchange Positions", 1)[1].split("lines.extend", 1)[0]
    assert '"adopted"' in block
    assert '"open"' in block


def test_a_failed_lookup_degrades_to_the_bare_symbol():
    # The card is a notification; a lookup error must never suppress it.
    block = CARD_SRC.split("Adopted Exchange Positions", 1)[1].split("lines.extend", 1)[0]
    assert re.search(r"except Exception:\s*\n\s*detail = \"\"", block), (
        "the per-symbol detail must fail to empty, not raise"
    )


# ── the callback receives SYMBOLS, not prose ─────────────────────────────

from bot.core.engine import adopted_symbols_from_messages


def test_the_symbol_is_extracted_from_the_adoption_message():
    # Observed live 2026-07-30: the card printed "• Adopted orphan: UNI" with
    # no levels, because the per-position lookup was handed the whole message
    # as if it were a symbol. The parameter was even named adopted_symbols.
    assert adopted_symbols_from_messages(["Adopted orphan: UNI/USDT"]) == ["UNI/USDT"]


def test_the_longer_prefix_wins():
    # "Adopted orphan limit order: X" also starts with "Adopted orphan", so a
    # shortest-first strip would yield "limit order: X" as the "symbol".
    assert adopted_symbols_from_messages(
        ["Adopted orphan limit order: SOL/USDT"]) == ["SOL/USDT"]


def test_non_adoption_messages_are_filtered_out():
    msgs = ["Ghost position cleared: BTC/USDT", "Adopted orphan: UNI/USDT"]
    assert adopted_symbols_from_messages(msgs) == ["UNI/USDT"]


def test_an_unknown_adoption_shape_is_passed_through_not_dropped():
    # Losing an adoption notice is worse than rendering one without levels.
    out = adopted_symbols_from_messages(["Adopted something new: XYZ"])
    assert out == ["Adopted something new: XYZ"]


def test_the_engine_passes_symbols_to_the_callback():
    import inspect
    from bot.core.engine import RuneClawEngine
    src = code_only(inspect.getsource(RuneClawEngine))
    assert "adopted_symbols_from_messages(sync_msgs)" in src, (
        "the callback renders a per-position SL/TP outcome and needs "
        "something it can match against the executor's positions"
    )
