"""The web capability card names exactly the doors the web turn dispatches.

`SHARED_DOORS` (what "what can you do" lists on the web) was a hand-written
copy of `_WEB_SEAM` (what the web turn dispatches) and `WEB_ROUTED_PERMISSION`
(the gate). The seam and the gate are pinned to each other; the card's list
was pinned only to its own literal. It is now read off the gate.
"""
from bot.nlp.chat_turn import SHARED_DOORS
from bot.skills.command_catalog import all_entries
from bot.skills.skill_permissions import WEB_ROUTED_PERMISSION
from bot.web import user_gateway as ug


def test_the_card_names_every_dispatched_door_and_nothing_else():
    assert set(SHARED_DOORS) == set(ug._WEB_SEAM)
    assert set(SHARED_DOORS) == set(WEB_ROUTED_PERMISSION) - {"status"}
    assert len(SHARED_DOORS) == len(set(SHARED_DOORS))


def test_every_door_on_the_card_has_a_catalogue_sentence():
    entries = all_entries()
    for name in SHARED_DOORS:
        row = entries.get(name)
        assert isinstance(row, tuple) and len(row) > 2 and row[2].strip(), name


def test_a_sixteenth_door_reaches_the_card(monkeypatch):
    import importlib

    import bot.nlp.chat_turn as ct
    monkeypatch.setitem(WEB_ROUTED_PERMISSION, "etf_flows", "etf_flows")
    try:
        fresh = importlib.reload(ct)
        assert "etf_flows" in fresh.SHARED_DOORS
        assert "status" not in fresh.SHARED_DOORS
    finally:
        monkeypatch.undo()
        importlib.reload(ct)
    assert "etf_flows" not in ct.SHARED_DOORS


# ── the words on the card are the web's, and each one opens its door ──────


def test_every_door_has_web_words():
    from bot.nlp.chat_turn import WEB_DOOR_WORDS
    assert set(WEB_DOOR_WORDS) == set(SHARED_DOORS)


def test_each_example_reaches_its_own_door():
    from bot.nlp.chat_turn import WEB_DOOR_WORDS
    from bot.nlp.intent_router import IntentRouter
    router = IntentRouter()
    for door, (_what, example) in WEB_DOOR_WORDS.items():
        found = router.classify_rules(example)
        assert found.skill == door and found.confidence == 1.0, (door, example, found.skill)


def test_the_card_names_no_slash_command_and_not_the_operators_scan():
    from types import SimpleNamespace

    from bot.nlp.chat_turn import shared_door_sentences
    users = SimpleNamespace(permission_denial=lambda uid, name: None)
    lines = shared_door_sentences(users, "web:1")
    assert len(lines) == len(SHARED_DOORS)
    assert not [ln for ln in lines if " /" in ln or ln.startswith("/")]
    assert not [ln for ln in lines if "cross-source best-rate scan" in ln]
    assert ("your linked on-chain wallet, mirrored read-only — say \u201cmy wallet on base\u201d"
            in lines)


def test_a_refused_door_is_left_off():
    from types import SimpleNamespace

    from bot.nlp.chat_turn import shared_door_sentences
    users = SimpleNamespace(
        permission_denial=lambda uid, name: "admin only" if name == "idleyield" else None)
    lines = shared_door_sentences(users, "web:1")
    assert len(lines) == len(SHARED_DOORS) - 1
    assert not [ln for ln in lines if "idle funds" in ln]
