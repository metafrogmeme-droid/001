"""The nightly audit's model is told what each flag is set to now.

On 2026-10-13 the audit card read::

    OF_MAX_SPREAD_BPS=120 (in force: 50)
      Raised from 70 to 120 bps to respect elevated spread during this volatility.

The card's `in force: 50` was right: it is the order-flow default. The
rationale's "70" was the model's own, because its prompt carried only the
environment's overrides ("unset = default"), so a flag left at its code
default was a flag whose setting the model was never given. It now gets every
allowlisted flag's value in force (`self_audit.values_in_force`), the same
reading the card prints, with `null` for one that could not be read.

Driven through `SelfAudit.run` with the model's call captured.
"""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import bot.llm.provider as prov
from bot.core import self_audit as sa_mod
from bot.core.self_audit import (
    ALLOWED_FLAGS,
    FLAG_MEANINGS,
    SelfAudit,
    audit_user_prompt,
    values_in_force,
)


def _engine():
    analyzer = SimpleNamespace(
        _resolve_llm_config=lambda: SimpleNamespace(is_configured=lambda: True),
        _build_client_for_config=lambda cfg: object())
    return SimpleNamespace(analyzer=analyzer,
                           live_executor=SimpleNamespace(_closed_trades=[]), risk=None)


def _asked(tmp_path, monkeypatch) -> tuple[str, str]:
    seen = {}

    async def complete(client, cfg, system_prompt, user_prompt):
        seen["system"], seen["user"] = system_prompt, user_prompt
        return "[]"

    monkeypatch.setattr(prov, "llm_complete", complete)
    audit = SelfAudit(state_file=str(tmp_path / "sa.json"))
    asyncio.run(audit.run(_engine()))
    assert seen, "the model was never asked"
    return seen["system"], seen["user"]


def _block(prompt: str) -> dict:
    m = re.search(r"VALUES IN FORCE[^\n]*\n(\{.*?\})\n", prompt, re.S)
    assert m, prompt[:400]
    return json.loads(m.group(1))


def test_a_flag_at_its_code_default_is_given_its_value(tmp_path, monkeypatch):
    for flag in ALLOWED_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    system, user = _asked(tmp_path, monkeypatch)
    given = _block(user)
    assert set(given) == set(ALLOWED_FLAGS), "a flag the model was not told about"
    # The two from the card: neither is set in the environment.
    assert given["OF_MAX_SPREAD_BPS"] == "50"
    assert given["VOLATILITY_GUARD_ATR_PCT"] == "7"
    assert "CURRENT ENV OVERRIDES" not in user
    assert "`VALUES IN FORCE`" in system and "names that one" in system


def test_an_override_is_given_as_the_value_in_force(tmp_path, monkeypatch):
    monkeypatch.setenv("OF_MAX_SPREAD_BPS", "70")
    _, user = _asked(tmp_path, monkeypatch)
    assert _block(user)["OF_MAX_SPREAD_BPS"] == "70"


def test_the_prompt_and_the_card_read_one_value(monkeypatch):
    """The card prints `in force:` from `validate_proposals`; the prompt from
    `values_in_force`. Both are `_current_text(effective_value(...))`."""
    monkeypatch.delenv("OF_MAX_SPREAD_BPS", raising=False)
    [card] = sa_mod.validate_proposals(
        [{"flag": "OF_MAX_SPREAD_BPS", "value": 120, "rationale": "r"}], current_env={})
    assert card["current"] == values_in_force({})["OF_MAX_SPREAD_BPS"] == "50"


def test_a_value_that_cannot_be_read_is_null_not_a_default(monkeypatch):
    monkeypatch.setitem(sa_mod.FLAG_CONFIG_PATHS, "VOLATILITY_GUARD_ATR_PCT", "risk.no_such_knob")
    monkeypatch.delenv("VOLATILITY_GUARD_ATR_PCT", raising=False)
    given = _block(audit_user_prompt({}, {}, env={}))
    assert given["VOLATILITY_GUARD_ATR_PCT"] is None
    assert given["OF_MAX_SPREAD_BPS"] == "50", "one unread value blanks no other"


# ── what each value IS ───────────────────────────────────────────────────────
#
# On 2026-10-14 the card read
#
#     VOLATILITY_GUARD_ATR_PCT=15 (in force: 7)
#       ATR at 7.0% — a low read — … warrants raising the guard to 15.0%.
#
# The 7.0 was the guard's own threshold, which the evidence never pairs with
# any ATR reading (it carries none), and raising that threshold admits MORE
# volatile entries: the model was given a name, bounds and a value, and
# nothing saying what the value is or which way loosens it.

def _bounds(prompt: str) -> dict:
    m = re.search(r"ALLOWLISTED FLAGS AND BOUNDS:\n(\{.*?\n\})\n\nVALUES IN FORCE", prompt, re.S)
    assert m, prompt[:400]
    return json.loads(m.group(1))


def test_the_model_is_told_what_each_flag_means(tmp_path, monkeypatch):
    _, user = _asked(tmp_path, monkeypatch)
    bounds = _bounds(user)
    assert set(bounds) == set(ALLOWED_FLAGS)
    for flag, b in bounds.items():
        assert b["means"] == FLAG_MEANINGS[flag], flag
    guard = bounds["VOLATILITY_GUARD_ATR_PCT"]
    assert (guard["min"], guard["max"]) == (3, 15)
    assert "not a reading of current volatility" in guard["means"]
    assert "admits more volatile entries (loosens)" in guard["means"]


def test_every_allowlisted_flag_has_a_meaning_and_no_meaning_is_stale():
    assert set(FLAG_MEANINGS) == set(ALLOWED_FLAGS), (
        set(FLAG_MEANINGS) ^ set(ALLOWED_FLAGS))
    for flag, spec in ALLOWED_FLAGS.items():
        text = FLAG_MEANINGS[flag]
        if spec["type"] == "bool":
            assert text.startswith("on "), (flag, "say what ON does")
        else:
            assert "raising it" in text, (flag, "say which way raising moves")


def test_a_setting_is_never_presented_as_a_market_reading(tmp_path, monkeypatch):
    system, _ = _asked(tmp_path, monkeypatch)
    assert "a SETTING, never a market reading" in system
    assert "carries no ATR, volatility, spread, depth or funding reading" in system
    assert "says it loosens it" in system
