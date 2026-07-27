"""Per-user strategy selection — "your bot, your strategy". The contract:

- a selection must name a REAL preset (alias-resolved by the command);
  anything else is refused, never stored;
- the gate is tighten-only: it can refuse a confirm, never create one;
- only confirm-time facts enforce (symbols list, confidence floor) — the
  scan-time gates (RSI/regime/volume, top3_volume universe) are STATED as
  scan-only, never silently claimed;
- every refusal names its rule with the numbers that tripped it;
- a stored selection whose preset cannot be read fails CLOSED (a missing
  preferences file, by contrast, just means "no selection");
- clearing always works — revocable is the whole point;
- the engine applies the veto to explicit user confirms only, never the
  operator auto-loop.
"""

import json
import re
from pathlib import Path

from bot.core import strategy_gate, user_strategy_store
from bot.skills.skill_registry import RunStrategySkill


VALID = RunStrategySkill.PRESETS.keys()


def test_store_roundtrip_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    assert user_strategy_store.get(777) is None
    assert user_strategy_store.set_pref(777, "dip sniper", VALID) == "dip sniper"
    assert user_strategy_store.get(777) == "dip sniper"
    # a selection that names no real preset is refused, never stored
    assert user_strategy_store.set_pref(777, "moon wizard", VALID) is None
    assert user_strategy_store.get(777) == "dip sniper"
    assert user_strategy_store.clear(777) is True
    assert user_strategy_store.get(777) is None
    assert user_strategy_store.clear(777) is False


def test_store_corrupt_file_reads_as_no_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    (tmp_path / "user_strategy.json").write_text("{not json", encoding="utf-8")
    assert user_strategy_store.get(1) is None   # fail-safe READ: no selection


def test_gate_no_selection_is_open():
    v = strategy_gate.check_confirm("", None, "BTC/USDT:USDT", 0.5)
    assert v["ok"] is True


def test_gate_confidence_floor_refuses_with_numbers():
    preset = RunStrategySkill.PRESETS["dip sniper"]     # conf >= 0.70
    v = strategy_gate.check_confirm("dip sniper", preset, "BTC/USDT:USDT", 0.61)
    assert v["ok"] is False
    assert "61%" in v["reason"] and "70%" in v["reason"]
    ok = strategy_gate.check_confirm("dip sniper", preset, "BTC/USDT:USDT", 0.83)
    assert ok["ok"] is True
    assert "confidence" in ok["enforced"]


def test_gate_states_scan_only_gates_instead_of_claiming_them():
    v = strategy_gate.check_confirm(
        "dip sniper", RunStrategySkill.PRESETS["dip sniper"], "BTC/USDT:USDT", 0.9)
    assert "rsi_threshold" in v["scan_only"] and "regime" in v["scan_only"]
    v2 = strategy_gate.check_confirm(
        "safe scalper", RunStrategySkill.PRESETS["safe scalper"], "ETHUSDT", 0.9)
    assert "symbols:top3_volume" in v2["scan_only"]


def test_gate_symbols_list_enforces_on_base_ticker():
    preset = {"label": "Majors Only", "symbols": ["BTC/USDT:USDT", "ETHUSDT"],
              "confidence_threshold": None}
    ok = strategy_gate.check_confirm("majors", preset, "BTCUSDT", 0.5)
    assert ok["ok"] is True
    bad = strategy_gate.check_confirm("majors", preset, "DOGE/USDT:USDT", 0.5)
    assert bad["ok"] is False
    assert "DOGE" in bad["reason"]


def test_gate_unreadable_selection_fails_closed():
    v = strategy_gate.check_confirm("ghost preset", None, "BTCUSDT", 0.9)
    assert v["ok"] is False
    assert "fail closed" in v["reason"]
    assert "/mystrategy off" in v["reason"]
    # unreadable confidence is refused too — never assumed to pass
    bad = strategy_gate.check_confirm(
        "dip sniper", RunStrategySkill.PRESETS["dip sniper"], "BTCUSDT", None)
    assert bad["ok"] is False


def test_engine_applies_veto_to_user_confirms_only():
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    block = re.search(
        r'if user_id and user_id != "auto":[\s\S]{0,2400}user_strategy_gate', src)
    assert block, "the veto must gate explicit user confirms and exempt the auto loop"
    assert "fails CLOSED" in src or "fail closed" in block.group(0)
    assert 'action="user_strategy_gate", result="REFUSED"' in src, \
        "every refusal is audited"


def test_command_is_catalogued_bilingually():
    from bot.skills.command_catalog import DESC_ZH, all_entries
    assert "mystrategy" in set(all_entries())
    assert "mystrategy" in DESC_ZH
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    assert '("mystrategy", self._cmd_mystrategy)' in src
    assert "tighten-only" in src.lower() or "TIGHTEN-ONLY" in src
