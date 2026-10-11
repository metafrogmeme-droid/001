"""The public chat describes the product that ships, and says where it trades
the way the FAQ does.

The public prompt opened "You are RUNECLAW, an autonomous AI crypto-trading
agent" and closed on "autonomous trading on their own keys". A live order no
person confirmed needs an eligibility record for the running strategy
(`bot/core/live_eligibility.py`) and none ships, so a visitor was told about
a product nobody can use. The prompt now states the mechanism, which holds in
every deployment state, and tells the model it cannot see that state.

The prompt named four venues and the FAQ's exchanges answer two, each typed
by hand. Both read `venues.venue_lineup` now: the venues a per-user executor
may be built for, and the ones linked for balances only.
"""
from __future__ import annotations

import ast
import inspect

from bot.core import faq_kb, venues
from bot.core.venues import (
    PER_USER_EXECUTION_VENUES,
    per_user_execution_refusal,
    valid_venue_ids,
    venue_lineup,
    venue_lineup_sentence,
)
from bot.skills import telegram_handler as th

PROMPT = th.TelegramHandler._PUBLIC_CHAT_SYSTEM_PROMPT
EXCHANGES_Q = "Which exchanges are supported?"


def test_the_lineup_is_the_registrys_own_split():
    trade, balances = venue_lineup()
    names = {vid: venues.get_venue(vid).display_name for vid in valid_venue_ids()}
    assert set(trade) == {names[v] for v in PER_USER_EXECUTION_VENUES}
    assert set(trade) | set(balances) == set(names.values())
    assert not set(trade) & set(balances)


def test_the_sentence_agrees_with_the_refusal_a_confirm_gets():
    """A venue the sentence says takes orders is one a per-user executor may
    be built for, and a balances-only one is refused."""
    trade, _ = venue_lineup()
    for vid in valid_venue_ids():
        name = venues.get_venue(vid).display_name
        refused = per_user_execution_refusal(vid) is not None
        assert refused == (name not in trade), vid


def test_the_prompt_and_the_faq_say_the_same_thing():
    faq = faq_kb.faq_answer(EXCHANGES_Q)
    sentence = venue_lineup_sentence()
    assert sentence in PROMPT
    assert sentence in faq
    trade, balances = venue_lineup()
    assert len(trade) >= 4 and len(balances) >= 1, "the fixture can tell two lists apart"
    places, linked = sentence.split(";", 1)
    assert all(name in places for name in trade), places
    assert all(name in linked for name in balances), linked
    assert "balances only" in linked and not any(name in places for name in balances)


def test_the_faq_answer_is_read_off_the_registry_when_asked(monkeypatch):
    """Driven: a venue added to the executor allow-list appears in the
    answer, which a hand-typed list would not do."""
    monkeypatch.setattr(venues, "PER_USER_EXECUTION_VENUES",
                        PER_USER_EXECUTION_VENUES | {"okx"})
    faq = faq_kb.faq_answer(EXCHANGES_Q)
    trade_part = faq.split(";", 1)[0]
    assert "OKX" in trade_part


def test_the_prompt_takes_its_venues_from_the_one_reading():
    """The class attribute is built at import, which a test cannot rebuild
    without reloading the handler; the expression that builds it can be
    read."""
    tree = ast.parse(inspect.getsource(th.TelegramHandler))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(tg, ast.Name) and tg.id == "_PUBLIC_CHAT_SYSTEM_PROMPT"
                        for tg in node.targets)):
            found = [c for c in ast.walk(node.value)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                     and c.func.id == "venue_lineup_sentence"]
    assert found, "the public prompt's venues are not the registry's reading"


def test_the_prompt_says_a_person_taps_and_the_model_cannot_see_more():
    assert "an order is placed when the account's owner taps Confirm" in PROMPT
    assert "eligibility record" in PROMPT
    assert "never describe RUNECLAW as trading real money on its own" in PROMPT
    assert "autonomous" not in PROMPT.lower()


def test_no_built_in_answer_claims_autonomy_either():
    """The other surface the visitor reads with no model at all."""
    for item in faq_kb._FAQ:
        answer = item["answer"]
        text = answer() if callable(answer) else answer
        assert "autonom" not in text.lower(), item["id"]
