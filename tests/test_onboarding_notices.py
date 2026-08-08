"""What the bot tells someone who is not (yet) a user.

Every message on the registration path asserted something the code beside it
had just contradicted. Collected on 2026-08-08 while working out why a bot the
operator expected 40+ people on reported one:

  * ``_handle_message`` called ``users.register(...)`` and replied, on the very
    next line, "I don't recognize you yet ... Use /start to register, then wait
    for approval." It had just created the record; /start was not needed; and
    on a bot with no allowlist there is no approval step to wait for. Someone
    who believed the message waited for a reply that could not arrive.
  * ``/start`` is UNGUARDED, so a stranger got the full status card — equity,
    positions, win rate — and the next command they tried answered "This bot is
    locked to its configured operator." A welcome that promises what the next
    tap refuses reads as a broken bot.
  * ``/approve`` told the admin "USER APPROVED" and the user "Access Granted"
    while ``_is_allowlisted`` still refused them, because it read env vars and
    the store recorded a flag nothing consulted.
  * A 24h-stale session was reported as "Your role (trader) cannot use /trade".
    The role can. The session had expired, and /start — the entire fix — was
    unguessable from the words.

The pattern under all four: a confident sentence about a state the system was
not in. So these tests plant the state and assert both halves — MUST say, and
MUST NOT say — because a message can be improved into a different lie.
"""
from __future__ import annotations

import pytest

from bot.formatters.onboarding import (ACCESS_STATES, access_denied_notice,
                                       admin_access_request,
                                       permission_denied_notice,
                                       welcome_notice)


class TestNothingClaimsAnApprovalStepThatDoesNotExist:
    def test_an_open_bot_never_says_wait(self):
        card = welcome_notice("Ann", "4242", access="open")
        low = card.lower()
        for forbidden in ("wait for approval", "pending approval",
                          "don't recognize", "do not recognize"):
            assert forbidden not in low, (
                f"open bot, no approval step, yet the welcome says {forbidden!r}")
        assert "registered" in low
        assert "4242" in card, "they need their id to be approved or supported"

    def test_a_granted_caller_gets_the_same_ready_card(self):
        """Someone on the env allowlist is IN. Telling the operator to wait for
        approval — which the old _guard copy did — is the same defect pointed
        at the one person who definitely has access."""
        card = welcome_notice("Ops", "111", access="granted")
        low = card.lower()
        assert "wait for approval" not in low and "pending" not in low
        assert "registered" in low

    def test_registration_is_never_described_as_unrecognised(self):
        for state in ACCESS_STATES:
            card = welcome_notice("Ann", "1", access=state,
                                  operator_notified=True)
            assert "recognize" not in card.lower()
            assert "recognise" not in card.lower()


class TestAnApprovalStepThatDoesExistIsNamed:
    def test_needs_approval_says_so_and_gives_the_id(self):
        card = welcome_notice("Ann", "4242", access="needs_approval",
                              operator_notified=True)
        assert "4242" in card
        low = card.lower()
        assert "operator" in low
        # Must NOT promise the commands work — that was /start's old card.
        assert "nothing to wait for" not in low

    def test_notified_and_not_notified_say_different_things(self):
        told = welcome_notice("Ann", "42", access="needs_approval",
                              operator_notified=True)
        untold = welcome_notice("Ann", "42", access="needs_approval",
                                operator_notified=False)
        assert told != untold, (
            "'I've told the operator' printed after a FAILED send is a "
            "measurement of something that never happened, and it leaves the "
            "person waiting instead of asking")
        assert "/approve 42" in untold, (
            "when nobody was told, point them at a human with the exact command")
        assert "/approve" not in told, (
            "when the operator has it, do not also send them chasing")

    def test_denied_command_says_locked_not_broken(self):
        card = access_denied_notice("42", operator_notified=False)
        low = card.lower()
        assert "42" in card
        assert "not broken" in low, (
            "an unexplained refusal right after a warm welcome is "
            "indistinguishable from a bug, and that reading is why people left")
        assert "/approve 42" in card


class TestAnUnknownStateIsNeverGuessed:
    """`access` has no default on purpose. Guessing 'open' promises access that
    may not exist; guessing 'needs_approval' invents a step that may not."""

    @pytest.mark.parametrize("bogus", ["", "unknown", "pending", None, "OPEN"])
    def test_it_raises_rather_than_picking_a_friendly_default(self, bogus):
        with pytest.raises(ValueError):
            welcome_notice("Ann", "1", access=bogus)


class TestTheRefusalNamesTheRealCause:
    def test_a_stale_session_is_not_reported_as_a_role_problem(self):
        card = permission_denied_notice("trade", "trader", "stale_session")
        low = card.lower()
        assert "/start" in card, "the remedy must be in the message"
        assert "24" in card
        assert "cannot use" not in low, (
            "the role CAN use it; saying otherwise sends the user to an admin "
            "for a problem /start fixes in one tap")

    def test_a_role_problem_is_not_reported_as_a_timeout(self):
        card = permission_denied_notice("halt", "viewer", "role")
        assert "viewer" in card
        assert "24" not in card, (
            "no amount of waiting or /start grants a role you do not have")

    def test_the_two_reasons_produce_different_cards(self):
        assert (permission_denied_notice("trade", "trader", "stale_session")
                != permission_denied_notice("trade", "trader", "role"))

    def test_an_unrecognised_reason_falls_back_to_the_conservative_half(self):
        """Role wording never promises a timer will fix things; the session
        wording does. An unknown reason must not offer the false hope."""
        card = permission_denied_notice("trade", "trader", "something_new")
        assert card == permission_denied_notice("trade", "trader", "role")


class TestTheOperatorsSide:
    def test_the_request_says_what_approving_actually_grants(self):
        card = admin_access_request("Ann", "4242")
        assert "4242" in card
        low = card.lower()
        assert "paper" in low and "live" in low, (
            "the operator is approving from a chat button; the card is the only "
            "place they can read that admission does NOT grant live trading")
        assert "/approve 4242" in card


class TestBothLanguages:
    """The bot ships a complete zh translation. A card that only exists in
    English silently downgrades every Chinese user at the moment they are
    deciding whether this thing works."""

    @pytest.mark.parametrize("render", [
        lambda lang: welcome_notice("Ann", "1", access="open", lang=lang),
        lambda lang: welcome_notice("Ann", "1", access="needs_approval",
                                    operator_notified=True, lang=lang),
        lambda lang: welcome_notice("Ann", "1", access="needs_approval",
                                    operator_notified=False, lang=lang),
        lambda lang: access_denied_notice("1", lang=lang),
        lambda lang: permission_denied_notice("trade", "trader",
                                              "stale_session", lang=lang),
        lambda lang: permission_denied_notice("trade", "viewer", "role",
                                              lang=lang),
        lambda lang: admin_access_request("Ann", "1", lang=lang),
    ])
    def test_zh_differs_from_en_and_is_fully_substituted(self, render):
        en, zh = render("en"), render("zh")
        assert en and zh
        assert zh != en, "missing zh translation falls back to English silently"
        for card in (en, zh):
            assert "{" not in card, (
                "t() swallows a KeyError and returns the template, so an "
                "unfilled placeholder ships as literal braces")
