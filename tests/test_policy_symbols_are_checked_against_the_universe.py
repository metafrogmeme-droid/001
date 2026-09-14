"""compile_policy validated no symbol against the engine's tradable universe.

Numeric rules are clamped so "the policy can only tighten"; list rules were
mapped through `_base_symbol` and kept, with no check that any entry names
something the engine trades. So "only trade btc please" bound an allow-list
of ['BTC', 'PLEASE'] and "never trade pepe" spelled wrongly bound a block-list
that blocked nothing — silently, on the card the operator approves.

The universe rides in `engine_caps["tradable_symbols"]` beside the numeric
caps, and lists are CHECKED against it and never trimmed: trimming an
allow-list loosens it, which is the one thing this compiler must never do.
An engine whose universe cannot be read checks nothing and says so.
"""
from __future__ import annotations

import inspect

from bot.core import engine as engine_mod
from bot.guardian import intent_policy as ip
from tests.source_scan import code_only

UNI = frozenset({"BTC", "ETH", "SOL"})


def _compile(rules, caps):
    return ip.compile_policy({"mode": "shadow", "rules": rules}, caps)


def test_an_entry_the_engine_does_not_trade_is_named_and_kept():
    pol = _compile([{"type": "allowed_symbols", "value": ["btc", "please"]}], {"tradable_symbols": UNI})
    assert pol["rules"][0]["value"] == ["BTC", "PLEASE"], "the list is never trimmed"
    assert any("PLEASE" in w and "check the spelling" in w for w in pol["warnings"]), pol["warnings"]
    assert not any("allow no trades" in w for w in pol["warnings"]), "BTC is tradable, so the rule allows something"


def test_a_block_list_that_binds_nothing_says_so():
    pol = _compile([{"type": "blocked_symbols", "value": ["pepe_wrongly"]}], {"tradable_symbols": UNI})
    assert pol["rules"][0]["type"] == "blocked_symbols"
    assert any("blocks nothing" in w for w in pol["warnings"]), pol["warnings"]


def test_an_allow_list_that_allows_nothing_says_so_and_stays():
    # Dropping it would let the bot trade everything under a sentence that
    # said "only". Kept, it allows nothing — and the card says exactly that.
    pol = _compile([{"type": "allowed_symbols", "value": ["please", "thanks"]}], {"tradable_symbols": UNI})
    assert pol["rules"][0]["value"] == ["PLEASE", "THANKS"]
    assert any("allow no trades" in w for w in pol["warnings"]), pol["warnings"]


def test_a_universe_that_could_not_be_read_checks_nothing_and_says_so():
    pol = _compile([{"type": "blocked_symbols", "value": ["pepe"]}], {"tradable_symbols": None})
    assert pol["rules"][0]["value"] == ["PEPE"]
    assert any("could not read the tradable universe" in w for w in pol["warnings"]), pol["warnings"]
    assert not any("blocks nothing" in w for w in pol["warnings"]), "no verdict from no reading"


def test_a_caller_that_supplies_no_universe_gets_the_old_silence():
    pol = _compile([{"type": "blocked_symbols", "value": ["pepe"]}], {"max_open_positions": 5})
    assert pol["warnings"] == []


def test_the_real_sentence_from_the_task():
    parsed = ip.compile_nl("only trade btc please")
    pol = ip.compile_policy({"mode": "shadow", "rules": parsed["rules"]}, {"tradable_symbols": UNI})
    allow = next(r for r in pol["rules"] if r["type"] == "allowed_symbols")
    assert "PLEASE" in allow["value"]
    assert any("PLEASE" in w for w in pol["warnings"])
    text = ip.human_readable(pol)
    assert "PLEASE" in text and "check the spelling" in text, text
    assert "engine caps" in text, "the notes header still names the caps section the older test pins"


def test_the_engine_supplies_its_universe_and_never_an_empty_one():
    u = engine_mod._tradable_base_symbols()
    assert u is not None and isinstance(u, frozenset)
    assert {"BTC", "ETH", "SOL"} <= u and "" not in u
    assert len(u) >= 100, len(u)
    src = code_only(inspect.getsource(engine_mod.RuneClawEngine._intent_engine_caps))
    assert '"tradable_symbols": _tradable_base_symbols()' in src, "the caps no longer carry the universe"


def test_an_unreadable_universe_is_none_not_empty(monkeypatch):
    import bot.skills.skill_registry as sr
    monkeypatch.delattr(sr, "DEEPSCAN_UNIVERSE")
    assert engine_mod._tradable_base_symbols() is None
