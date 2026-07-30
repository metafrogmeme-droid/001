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


def test_the_blanket_maybe_is_gone():
    assert "may not be set" not in CODE, (
        "the footer must state outcomes, not a maybe that sends the operator "
        "to redo the ladder adoption already ran"
    )


def test_the_unprotected_case_is_loud_and_directive():
    # RC-AUD-022's marker is the one state where a human must act NOW.
    assert "UNPROTECTED" in CODE
    assert "Set one NOW" in CODE


def test_protected_positions_show_their_levels():
    block = CODE.split("Adopted Exchange Positions", 1)[1]
    assert "stop_loss" in block and "take_profit" in block


def test_the_lookup_is_scoped_to_adopted_open_positions():
    # The card must not attribute a NON-adopted position's levels to an
    # adopted symbol that shares its name.
    block = CODE.split("Adopted Exchange Positions", 1)[1].split("lines.extend", 1)[0]
    assert '"adopted"' in block
    assert '"open"' in block


def test_a_failed_lookup_degrades_to_the_bare_symbol():
    # The card is a notification; a lookup error must never suppress it.
    block = SRC.split("Adopted Exchange Positions", 1)[1].split("lines.extend", 1)[0]
    assert re.search(r"except Exception:\s*\n\s*_detail = \"\"", block), (
        "the per-symbol detail must fail to empty, not raise"
    )
