"""What /venues tells a user, with the state planted underneath it.

`tests/test_surface_scenarios.py` holds the pattern this follows: MUST_SAY,
MUST_NOT_SAY, and a planted red herring — a true-but-misleading signal the card
has to not be fooled by.

The red herring here is the SELECTION ITSELF. Two venues chosen and saved is a
true fact, and rendering it as two ticked venues is a plausible card. It is
also a lie whenever the flag is off, because every order still goes to one
venue — and a user reading that card sizes their risk against a
diversification they do not have. That is the single most expensive thing this
card can get wrong, so it is the first thing tested.
"""
from __future__ import annotations

from bot.formatters.venue_card import venue_card


# ── the flag, which outranks the selection ───────────────────────────────

def test_a_selection_with_the_flag_off_says_it_is_not_in_force():
    out = venue_card(connected=["bitget", "bybit"], selected=["bitget", "bybit"],
                     dropped=[], mode="off")
    assert "OFF" in out
    assert "not</b> spread" in out or "not spread" in out, (
        "two selected venues rendered without saying the book is not actually "
        "spread across them — the user sizes risk against diversification "
        "they do not have")


def test_shadow_says_nothing_is_spread_yet():
    out = venue_card(connected=["bitget", "bybit"], selected=["bitget", "bybit"],
                     dropped=[], mode="shadow")
    assert "SHADOW" in out
    assert "Nothing is spread yet" in out


def test_enforce_says_orders_are_routed_and_that_one_trade_goes_to_one_venue():
    out = venue_card(connected=["bitget", "bybit"], selected=["bitget", "bybit"],
                     dropped=[], mode="enforce")
    assert "routed across" in out
    assert "ONE of them" in out, (
        "the card does not say a trade goes to one venue — a reader could take "
        "'routed across two venues' as the trade being placed on both")
    assert "never to all of them" in out


def test_enforce_that_is_not_available_is_not_rendered_as_live():
    """The `ENFORCE_IMPLEMENTED` case, on the surface. An operator who set
    enforce on a build without routing must not read a green card."""
    out = venue_card(connected=["bitget"], selected=["bitget"], dropped=[],
                     mode="enforce", enforce_available=False)
    assert "not available" in out
    assert "✅" not in out, "an unavailable enforce rendered as in force"


def test_no_selection_says_single_venue_rather_than_looking_broken():
    out = venue_card(connected=["bitget"], selected=[], dropped=[], mode="enforce")
    assert "have not chosen" in out
    assert "single connected venue" in out
    assert "⚠️" not in out, "the default state was rendered as a warning"


# ── a venue that stopped being connected ─────────────────────────────────

def test_a_dropped_venue_is_shown_as_a_problem_not_omitted():
    """A shorter list reads as "I deselected that". The difference between
    "you turned this off" and "your keys stopped working" is the whole reason
    the raw selection and the live one are separate reads."""
    out = venue_card(connected=["bitget"], selected=["bitget", "bybit"],
                     dropped=["bybit"], mode="enforce")
    assert "Selected but not connected" in out
    assert "bybit" in out
    assert "nothing is routed there" in out


def test_a_dropped_venue_is_not_counted_as_trading():
    out = venue_card(connected=["bitget"], selected=["bitget", "bybit"],
                     dropped=["bybit"], mode="enforce")
    assert "<b>1</b> selected" in out, (
        "a disconnected venue was counted in the number of venues trading")


# ── the venue list ───────────────────────────────────────────────────────

def test_connected_but_unselected_venues_are_visible_so_they_can_be_added():
    out = venue_card(connected=["bitget", "bybit", "okx"], selected=["bitget"],
                     dropped=[], mode="enforce")
    assert "okx" in out and "not selected" in out


def test_open_positions_are_shown_where_they_block_a_deselect():
    """Saying it here saves the user discovering it from a refusal."""
    out = venue_card(connected=["bitget", "bybit"], selected=["bitget", "bybit"],
                     dropped=[], mode="enforce", positions={"bybit": 3})
    assert "3 open" in out


def test_nothing_connected_says_so_plainly():
    out = venue_card(connected=[], selected=[], dropped=[], mode="off")
    assert "No venues connected" in out
    assert "/connect" in out


def test_the_card_escapes_what_it_prints():
    out = venue_card(connected=["<b>x</b>"], selected=[], dropped=[], mode="off")
    assert "&lt;b&gt;" in out


# ── it is reachable ──────────────────────────────────────────────────────

def test_the_handler_registers_and_uses_it():
    """#999: a card that is built and never reached renders zero times in
    production while every test here passes."""
    import inspect

    from bot.skills import telegram_handler
    src = inspect.getsource(telegram_handler)
    assert '("venues", self._cmd_venues)' in src, (
        "/venues is not registered, so the selection cannot be made by a user "
        "and the store can only be populated by editing JSON by hand")
    assert "venue_card(" in src, "the command does not render through the card"
