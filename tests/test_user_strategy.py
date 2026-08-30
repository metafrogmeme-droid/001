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


def test_resolve_key_accepts_slug_alias_and_key():
    from bot.core.strategy_gate import resolve_key
    P, A = RunStrategySkill.PRESETS, RunStrategySkill.ALIASES
    assert resolve_key("dip sniper", P, A) == "dip sniper"   # key
    assert resolve_key("dip-sniper", P, A) == "dip sniper"   # slug
    assert resolve_key("dip", P, A) == "dip sniper"          # alias
    assert resolve_key("MOMENTUM", P, A) == "momentum hunter"
    assert resolve_key("moon wizard", P, A) is None
    assert resolve_key("", P, A) is None


def test_describe_gates_matches_the_veto_split():
    from bot.core.strategy_gate import describe_gates
    confirm, scan = describe_gates(RunStrategySkill.PRESETS["dip sniper"])
    assert confirm == ["confidence>=70%"]
    assert "rsi_threshold" in scan and "regime" in scan
    confirm2, scan2 = describe_gates(RunStrategySkill.PRESETS["safe scalper"])
    assert "symbols:top3_volume" in scan2
    assert confirm2 == ["confidence>=75%"]


def test_web_gateway_mirrors_mystrategy():
    """The web surface is the SAME product: same store, same permission,
    same honesty split, registered on the user gateway."""
    src = Path("bot/web/user_gateway.py").read_text(encoding="utf-8")
    assert 'app.router.add_get("/user/strategy", handle_user_strategy_get)' in src
    assert 'app.router.add_post("/user/strategy", handle_user_strategy_set)' in src
    # SET requires the same trader-role permission Telegram's /mystrategy has
    set_body = src.split("async def handle_user_strategy_set")[1].split("async def ")[0]
    assert '_guard_user(tg_handler, tg_id, command="mystrategy")' in set_body
    assert "resolve_key" in set_body, "accepts key, slug or alias — one product"
    get_body = src.split("async def handle_user_strategy_get")[1].split("async def ")[0]
    assert "describe_gates" in get_body, "the honesty split ships with the catalogue"
    assert "tighten-only veto" in get_body


def test_community_snapshot_roundtrip_and_validation(tmp_path, monkeypatch):
    """A community strategy is armed as a validated SNAPSHOT — the caller's
    projection is never trusted blindly."""
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    e = user_strategy_store.set_custom(
        42, "long-majors-ab12", "Long Majors",
        {"confidence_threshold": 0.7, "symbols": ["BTC", "ETH"],
         "direction": "long_only", "regime_filter": "trend_up",
         "max_position_pct": 5,            # not signal-checkable -> dropped
         "confidence_threshold_evil": 9})  # unknown -> dropped
    assert e["kind"] == "community" and e["slug"] == "long-majors-ab12"
    assert set(e["gates"]) == {"confidence_threshold", "symbols", "direction", "regime_filter"}
    assert e["armed_at"], "the snapshot records WHEN it was taken"
    # get() is engine-preset shaped; get_entry() understands both
    assert user_strategy_store.get(42) is None
    assert user_strategy_store.get_entry(42)["slug"] == "long-majors-ab12"
    # a bogus slug is refused, never stored
    assert user_strategy_store.set_custom(43, "NOT A SLUG", "x", {}) is None
    assert user_strategy_store.clear(42) is True


def test_custom_gate_enforces_and_states_scan_only():
    entry = {"kind": "community", "slug": "s", "label": "Long Majors",
             "gates": {"confidence_threshold": 0.7, "symbols": ["BTC", "ETH"],
                       "direction": "long_only", "regime_filter": "trend_up"}}
    ok = strategy_gate.check_custom(entry, "BTCUSDT", 0.8, "LONG")
    assert ok["ok"] is True
    assert "regime" in ok["scan_only"], "regime is scan-only — never claimed at confirm"
    assert "confidence" in ok["enforced"] and "direction" in ok["enforced"]
    bad_sym = strategy_gate.check_custom(entry, "DOGE/USDT:USDT", 0.9, "LONG")
    assert bad_sym["ok"] is False and "DOGE" in bad_sym["reason"]
    bad_dir = strategy_gate.check_custom(entry, "BTCUSDT", 0.9, "SHORT")
    assert bad_dir["ok"] is False and "long-only" in bad_dir["reason"]
    bad_conf = strategy_gate.check_custom(entry, "BTCUSDT", 0.61, "LONG")
    assert bad_conf["ok"] is False and "61%" in bad_conf["reason"] and "70%" in bad_conf["reason"]
    # an unreadable snapshot fails CLOSED, like every armed control here
    dead = strategy_gate.check_custom(None, "BTCUSDT", 0.9, "LONG")
    assert dead["ok"] is False and "fail closed" in dead["reason"]


def test_engine_routes_the_veto_by_stored_shape():
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    assert "user_strategy_store.get_entry(user_id)" in src
    assert "strategy_gate.check_custom(" in src
    assert "isinstance(_sel, dict)" in src


def test_web_arming_says_it_is_a_snapshot_not_a_link():
    gw = Path("bot/web/user_gateway.py").read_text(encoding="utf-8")
    assert "set_custom(" in gw
    assert "snapshot_note" in gw
    assert "does not change your bot" in gw
    js = Path("app/routes/botstrategy.js").read_text(encoding="utf-8")
    assert "rulesToGates" in js, "the SAME projection the picks feed uses"
    assert "no_enforceable_rules" in js, \
        "a config with nothing signal-checkable is refused, not falsely armed"


def test_command_is_catalogued_bilingually():
    from bot.skills.command_catalog import DESC_ZH, all_entries
    assert "mystrategy" in set(all_entries())
    assert "mystrategy" in DESC_ZH
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    assert '("mystrategy", self._cmd_mystrategy)' in src
    assert "tighten-only" in src.lower() or "TIGHTEN-ONLY" in src
