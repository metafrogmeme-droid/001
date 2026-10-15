"""Every operator setting has one write path: bounded, saved, recorded.

The runtime settings were attributes on `RUNTIME`, and each door that wrote
one did its own share of the job. /leverage set wrote the value and nothing
else, so a restart put the configured leverage back without a word;
/autoconfirm wrote and logged, and a restart undid it too; /drawdownlimit
saved through the risk state; /mode and the stance buttons wrote and nothing
else. Nobody could say who changed a setting, or when.

`bot/core/settings_registry.py` is now the one path: the Telegram doors, the
website stance queue and the new Admin page all call `apply`/`reset`. Driven
here:
- a change lands in force, is saved, and leaves one audit line naming who,
  through which door, before and after, and which way it moved the guard;
- a saved change survives a restart, and a reset forgets it;
- an unreadable saved file is never written over (it would erase the rest);
- each door's bounds hold as before (refuse, or clamp and say so);
- the live drawdown cap is saved with the risk state, and a save that did
  not land is reported as not saved;
- every Telegram door goes through the registry, and no module writes a
  setting on `RUNTIME` by hand (a two-way baseline of named exceptions);
- the gateway's admin endpoints are admin-only and refuse live trading and the
  venue by name;
- the stance is said to be the label it is.
"""
from __future__ import annotations

import ast
import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import CONFIG, RUNTIME, RuntimeState
from bot.core import settings_registry as reg

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = "7"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """The registry's two files in a temp dir, and RUNTIME handed back."""
    monkeypatch.setattr(reg, "OVERRIDES_PATH", tmp_path / "settings_overrides.json")
    monkeypatch.setattr(reg, "AUDIT_PATH", tmp_path / "settings_audit.jsonl")
    saved = {k: v for k, v in vars(RUNTIME).items() if k != "_lock"}
    yield tmp_path
    for k, v in saved.items():
        setattr(RUNTIME, k, v)


def _audit_rows():
    try:
        text = Path(reg.AUDIT_PATH).read_text()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _saved():
    return json.loads(Path(reg.OVERRIDES_PATH).read_text())["values"]


def _restart():
    """What a restart does to RUNTIME: every value back to its fresh state."""
    fresh = RuntimeState()
    for k, v in vars(fresh).items():
        if k != "_lock":
            setattr(RUNTIME, k, v)


# ── the one write path ────────────────────────────────────────────────────

class TestApply:
    def test_a_change_is_in_force_saved_and_recorded(self, files):
        res = reg.apply("leverage_override", 15, actor="telegram:7", via="telegram")
        assert res["ok"] is True and res["persisted"] is True
        assert RUNTIME.leverage_override == 15
        assert _saved() == {"leverage_override": 15}
        rows = _audit_rows()
        assert len(rows) == 1
        row = rows[0]
        assert (row["key"], row["before"], row["after"]) == ("leverage_override", None, 15)
        assert (row["actor"], row["via"]) == ("telegram:7", "telegram")
        assert row["effect"] == "loosens"
        assert row["persisted"] is True

    def test_a_saved_change_survives_a_restart(self, files):
        reg.apply("auto_confirm_threshold", 0.9, actor="telegram:7", via="telegram")
        reg.apply("leverage_override", 4, actor="telegram:7", via="telegram")
        reg.apply("asset_universe", "metals", actor="telegram:7", via="telegram")
        reg.apply("strategy_mode", "defensive", actor="telegram:7", via="telegram")
        reg.apply("paper_auto_accept", "off", actor="telegram:7", via="website")
        _restart()
        assert RUNTIME.leverage_override is None, "the restart stand-in did not reset"
        out = reg.restore_overrides()
        assert out["error"] is None
        assert sorted(out["applied"]) == sorted(
            ["auto_confirm_threshold", "leverage_override", "asset_universe",
             "strategy_mode", "paper_auto_accept"])
        assert RUNTIME.auto_confirm_threshold == 0.9
        assert RUNTIME.leverage_override == 4
        assert RUNTIME.asset_universe == "metals"
        assert RUNTIME.strategy_mode == "defensive"
        assert RUNTIME.paper_auto_accept_in_force(True) is False

    def test_a_reset_forgets_the_saved_value(self, files):
        reg.apply("leverage_override", 12, actor="telegram:7", via="telegram")
        res = reg.reset("leverage_override", actor="telegram:7", via="telegram")
        assert res["ok"] and res["reset"] is True
        assert RUNTIME.leverage_override is None
        assert "leverage_override" not in _saved()
        _restart()
        reg.restore_overrides()
        assert RUNTIME.leverage_override is None
        assert _audit_rows()[-1]["reset"] is True

    def test_nothing_unnamed_writes(self, files):
        with pytest.raises(ValueError):
            reg.apply("leverage_override", 5, actor="", via="telegram")
        assert RUNTIME.leverage_override is None and _audit_rows() == []

    def test_an_unknown_setting_changes_nothing(self, files):
        res = reg.apply("live_mode", True, actor="telegram:7", via="website")
        assert res["ok"] is False and res["error"] == "unknown_setting"
        assert _audit_rows() == []


class TestEffect:
    @pytest.mark.parametrize("key,before,after,want", [
        ("leverage_override", 10, 15, "loosens"),
        ("leverage_override", 15, 10, "tightens"),
        ("auto_confirm_threshold", 0.8, 0.9, "tightens"),
        ("auto_confirm_threshold", 0.9, 0.8, "loosens"),
        ("live_drawdown_override_pct", 10.0, 20.0, "loosens"),
        ("paper_auto_accept", False, True, "loosens"),
        ("paper_auto_accept", True, False, "tightens"),
        ("strategy_mode", "balanced", "aggressive", "neither"),
        ("asset_universe", "all", "metals", "neither"),
        ("leverage_override", 10, 10, None),
    ])
    def test_the_direction_is_computed_from_the_setting(self, key, before, after, want):
        assert reg.effect(key, before, after) == want

    def test_no_override_is_compared_as_the_configured_value(self):
        from bot.core.leverage import operator_standard
        cfg = operator_standard(CONFIG.exchange, None).leverage
        assert reg.effect("leverage_override", None, cfg + 1) == "loosens"
        assert reg.effect("leverage_override", None, max(1, cfg - 1)) == "tightens"
        dd = float(CONFIG.risk.live_max_drawdown_pct)
        assert reg.effect("live_drawdown_override_pct", None, dd + 5) == "loosens"
        assert reg.effect("live_drawdown_override_pct", None, max(1.0, dd - 1)) == "tightens"


class TestBounds:
    def test_auto_confirm_out_of_range_is_refused_and_nothing_moves(self, files):
        before = RUNTIME.auto_confirm_threshold
        for bad in (0.3, 1.5, "nan", "inf", "abc"):
            res = reg.apply("auto_confirm_threshold", bad, actor="t:1", via="telegram")
            assert res["ok"] is False, bad
        assert RUNTIME.auto_confirm_threshold == before
        assert _audit_rows() == [], "a refused value left an audit line"

    def test_leverage_and_drawdown_clamp_and_say_so(self, files):
        res = reg.apply("leverage_override", 50, actor="t:1", via="telegram")
        assert res["after"] == RuntimeState.LEVERAGE_OVERRIDE_MAX and res["clamped"] is True
        res = reg.apply("live_drawdown_override_pct", 999, actor="t:1", via="telegram")
        assert res["after"] == RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MAX and res["clamped"] is True
        res = reg.apply("leverage_override", 3, actor="t:1", via="telegram")
        assert res["clamped"] is False

    def test_a_choice_outside_the_list_is_refused(self, files):
        assert reg.apply("asset_universe", "moon", actor="t:1", via="x")["ok"] is False
        assert reg.apply("strategy_mode", "yolo", actor="t:1", via="x")["ok"] is False


class TestUnreadableSavedFile:
    def test_it_is_not_written_over_and_the_change_says_not_saved(self, files):
        p = Path(reg.OVERRIDES_PATH)
        p.write_text("{not json")
        res = reg.apply("leverage_override", 6, actor="t:1", via="telegram")
        assert res["persisted"] is False
        assert "unreadable" in res["persist_note"]
        assert p.read_text() == "{not json", "an unreadable file was written over"
        assert "Not saved" in reg.saved_sentence(res)
        assert RUNTIME.leverage_override == 6, "the value in force still changed"

    def test_restore_applies_nothing_and_names_the_error(self, files):
        Path(reg.OVERRIDES_PATH).write_text("[]")
        out = reg.restore_overrides()
        assert out["applied"] == [] and out["error"] == "BadShape"

    def test_the_snapshot_says_unreadable_not_none_saved(self, files):
        Path(reg.OVERRIDES_PATH).write_text("{not json")
        snap = reg.snapshot()
        assert snap["saved_readable"] is False and snap["saved_error"]
        assert {r["source"] for r in snap["settings"] if r["store"] == "overrides"} == {"unknown"}

    def test_an_unreadable_record_is_not_an_empty_one(self, files):
        assert reg.snapshot()["changes"] == [] and reg.snapshot()["changes_error"] is None
        Path(reg.AUDIT_PATH).mkdir()
        snap = reg.snapshot()
        assert snap["changes"] is None and snap["changes_error"]


# ── the drawdown cap lives in the risk state ─────────────────────────────

def _risk(path):
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=str(path))


class TestDrawdownCap:
    def test_saved_with_the_risk_state_and_restored_by_it(self, files):
        sf = files / "risk_state.json"
        eng = SimpleNamespace(risk=_risk(sf))
        res = reg.apply("live_drawdown_override_pct", 12, actor="t:1",
                        via="telegram", engine=eng)
        assert res["persisted"] is True
        RUNTIME.clear_live_drawdown_override()
        _risk(sf)._load_state()
        assert RUNTIME.live_drawdown_override_pct == 12.0
        assert not Path(reg.OVERRIDES_PATH).exists(), "a second store for one value"

    def test_no_engine_or_a_reader_is_not_confirmed(self, files):
        res = reg.apply("live_drawdown_override_pct", 9, actor="t:1", via="telegram")
        assert res["persisted"] is None and "Not confirmed" in reg.saved_sentence(res)
        reader = _risk(files / "r.json")
        reader._reader = True
        res = reg.apply("live_drawdown_override_pct", 9, actor="t:1", via="telegram",
                        engine=SimpleNamespace(risk=reader))
        assert res["persisted"] is None

    def test_a_save_that_did_not_land_is_not_saved(self, files):
        blocker = files / "a_file"
        blocker.write_text("x")
        eng = SimpleNamespace(risk=_risk(blocker / "risk_state.json"))
        res = reg.apply("live_drawdown_override_pct", 9, actor="t:1",
                        via="telegram", engine=eng)
        assert res["persisted"] is False and "Not saved" in reg.saved_sentence(res)


# ── the doors ─────────────────────────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ops_host(sent, engine=None):
    async def _send(update, text, **kw):
        sent.append(text)
    return SimpleNamespace(_is_admin=lambda u: True, _is_operator=lambda u: True,
                           _get_tg_id=lambda u: OPERATOR, _send=_send, _reply=_send,
                           _guard=AsyncMock(return_value=True),
                           engine=engine if engine is not None else SimpleNamespace(
                               _user_executors={}, risk=None))


def _wrapped(fn):
    return getattr(fn, "__wrapped__", fn)


class TestTelegramDoors:
    def test_autoconfirm_goes_through_the_registry(self, files):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []
        host = _ops_host(sent)
        _run(_wrapped(EngineOpsCommands._cmd_autoconfirm)(
            host, SimpleNamespace(), SimpleNamespace(args=["0.9"])))
        _run(_wrapped(EngineOpsCommands._cmd_autoconfirm)(
            host, SimpleNamespace(), SimpleNamespace(args=["off"])))
        rows = _audit_rows()
        assert [(r["key"], r["after"], r["via"], r["actor"]) for r in rows] == [
            ("auto_confirm_threshold", 0.9, "telegram", f"telegram:{OPERATOR}"),
            ("auto_confirm_threshold", 1.0, "telegram", f"telegram:{OPERATOR}")]
        assert _saved() == {"auto_confirm_threshold": 1.0}
        assert all("Saved: it survives a restart." in s for s in sent), sent

    def test_autoconfirm_refuses_nan_as_it_refuses_out_of_range(self, files):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []
        before = RUNTIME.auto_confirm_threshold
        _run(_wrapped(EngineOpsCommands._cmd_autoconfirm)(
            _ops_host(sent), SimpleNamespace(), SimpleNamespace(args=["nan"])))
        assert RUNTIME.auto_confirm_threshold == before and _audit_rows() == []

    def test_leverage_set_and_reset_go_through_the_registry(self, files):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []
        host = _ops_host(sent)
        _run(_wrapped(EngineOpsCommands._cmd_leverage)(
            host, SimpleNamespace(), SimpleNamespace(args=["set", "4"])))
        assert RUNTIME.leverage_override == 4 and _saved() == {"leverage_override": 4}
        _run(_wrapped(EngineOpsCommands._cmd_leverage)(
            host, SimpleNamespace(), SimpleNamespace(args=["reset"])))
        assert RUNTIME.leverage_override is None and _saved() == {}
        assert [(r["key"], r["reset"]) for r in _audit_rows()] == [
            ("leverage_override", False), ("leverage_override", True)]
        assert all("Saved" in s for s in sent), sent

    def test_drawdownlimit_saves_with_the_risk_state_and_says_so(self, files):
        from bot.skills.engine_ops_commands import EngineOpsCommands
        sent = []
        eng = SimpleNamespace(risk=_risk(files / "risk_state.json"), _user_executors={})
        host = _ops_host(sent, engine=eng)
        _run(EngineOpsCommands._cmd_drawdownlimit(
            host, SimpleNamespace(), SimpleNamespace(args=["12"])))
        assert RUNTIME.live_drawdown_override_pct == 12.0
        row = _audit_rows()[-1]
        assert (row["key"], row["after"], row["persisted"]) == (
            "live_drawdown_override_pct", 12.0, True)
        assert "Saved: it survives a restart." in sent[-1]
        _run(EngineOpsCommands._cmd_drawdownlimit(
            host, SimpleNamespace(), SimpleNamespace(args=["off"])))
        assert RUNTIME.live_drawdown_override_pct is None
        assert _audit_rows()[-1]["reset"] is True

    def test_mode_goes_through_the_registry(self, files):
        from bot.skills.scan_commands import ScanCommands
        sent = []
        host = _ops_host(sent)
        update = SimpleNamespace(message=SimpleNamespace(text="/mode metals"))
        try:
            _run(ScanCommands._cmd_mode(host, update, SimpleNamespace(args=["metals"])))
        except Exception:
            pass    # the card after the write reads more than this stand-in has
        assert RUNTIME.asset_universe == "metals"
        assert _audit_rows()[-1]["key"] == "asset_universe"
        assert _saved() == {"asset_universe": "metals"}

    def test_the_website_stance_queue_goes_through_the_registry(self, files, monkeypatch):
        from bot.utils import control_pull as cp
        acks = []

        def _request(path, body=None):
            if path.endswith("/pending"):
                return {"pending": {"mode": "defensive", "telegram_id": OPERATOR}}
            acks.append(body)
            return {}
        monkeypatch.setattr(cp, "SYNC_SECRET", "s" * 40)
        monkeypatch.setattr(cp, "_request", _request)
        store = SimpleNamespace(get_tier=lambda tg: "admin")
        assert cp.pull_and_apply_stance(store) is True
        assert RUNTIME.strategy_mode == "defensive"
        row = _audit_rows()[-1]
        assert (row["key"], row["via"], row["actor"]) == (
            "strategy_mode", "website-queue", f"telegram:{OPERATOR}")
        assert acks == [{"applied": True, "mode": "defensive"}]


# ── nothing writes a setting by hand ─────────────────────────────────────

_SETTING_ATTRS = {"auto_confirm_threshold", "leverage_override",
                  "live_drawdown_override_pct", "paper_auto_accept_override",
                  "asset_universe", "strategy_mode"}
_RECEIVERS = {"RUNTIME", "_RT", "RT", "_rt", "rt", "runtime"}

#: Writes on RUNTIME that are not an operator's change, with the reason.
#: Two-way: a new site fails, and a listed site that is gone fails.
_ALLOWED = {
    ("bot/core/settings_registry.py", "_write"):
        "the one write path",
    ("bot/core/engine.py", "_adapt_auto_confirm_threshold"):
        "the adaptive threshold: the engine's own move, tighten-only on live, "
        "audited where it happens; not an operator setting to save",
    ("bot/risk/risk_engine.py", "_restore_dd_override"):
        "the boot restore of the drawdown cap from the risk state, where it is saved",
}


def _writes_in(path: Path):
    tree = ast.parse(path.read_text())
    out = []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            f = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            targets = []
            if isinstance(child, ast.Assign):
                targets = child.targets
            elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                targets = [child.target]
            for t in targets:
                if (isinstance(t, ast.Attribute) and t.attr in _SETTING_ATTRS
                        and isinstance(t.value, ast.Name) and t.value.id in _RECEIVERS):
                    out.append((f, t.attr, child.lineno))
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "clear_live_drawdown_override"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in _RECEIVERS):
                out.append((f, "clear_live_drawdown_override", child.lineno))
            visit(child, f)
    visit(tree, None)
    return out


def test_no_module_writes_an_operator_setting_by_hand():
    found = {}
    for path in sorted((ROOT / "bot").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for func, attr, line in _writes_in(path):
            found.setdefault((rel, func), []).append(f"{attr}@{line}")
    unexpected = {k: v for k, v in found.items() if k not in _ALLOWED}
    assert not unexpected, (
        "a setting is written on RUNTIME outside the settings registry, so the "
        f"change is neither saved nor recorded: {unexpected}. Call "
        "settings_registry.apply/reset, or add the site to _ALLOWED with a reason.")
    stale = [k for k in _ALLOWED if k not in found]
    assert not stale, f"_ALLOWED lists a site that no longer writes: {stale}"


def test_the_scan_catches_a_planted_write(tmp_path):
    """The rule above is only as good as its scan: a door that writes by hand
    must be seen."""
    p = tmp_path / "door.py"
    p.write_text("from bot.config import RUNTIME as _RT\n"
                 "def door():\n    _RT.leverage_override = 9\n"
                 "async def other():\n    RUNTIME.clear_live_drawdown_override()\n")
    assert [(f, a) for f, a, _ in _writes_in(p)] == [
        ("door", "leverage_override"), ("other", "clear_live_drawdown_override")]


# ── the gateway's admin endpoints ────────────────────────────────────────

class _Req:
    def __init__(self, app, body):
        self.app = app
        self._body = body

    async def json(self):
        return self._body


def _gw_app(tmp_path):
    from bot.utils.user_store import UserStore
    users = UserStore(tmp_path / "users.json")
    users.seed_admin(OPERATOR)
    users.register("55", name="Bo")
    return {"engine": SimpleNamespace(risk=_risk(tmp_path / "risk.json"),
                                      live_executor=None),
            "tg_handler": SimpleNamespace(users=users)}


class TestGateway:
    def test_a_non_admin_reads_and_changes_nothing(self, files):
        from bot.web import user_gateway as ug
        assert not ug.CONFIG.telegram.admin_ids, "the fixture needs no env admin list"
        app = _gw_app(files)
        r = _run(ug.handle_admin_settings(_Req(app, {"telegram_id": "55"})))
        assert r.status == 403
        r = _run(ug.handle_admin_settings_set(_Req(app, {
            "telegram_id": "55", "key": "leverage_override", "value": 20})))
        assert r.status == 403 and RUNTIME.leverage_override is None
        assert _audit_rows() == []

    def test_the_operator_reads_every_setting_and_changes_one(self, files):
        from bot.web import user_gateway as ug
        app = _gw_app(files)
        r = _run(ug.handle_admin_settings(_Req(app, {"telegram_id": OPERATOR})))
        assert r.status == 200
        snap = json.loads(r.body)
        assert [s["key"] for s in snap["settings"]] == list(reg.SETTINGS)
        assert {lk["key"] for lk in snap["locked"]} == {"live_mode", "venue"}
        r = _run(ug.handle_admin_settings_set(_Req(app, {
            "telegram_id": OPERATOR, "key": "leverage_override", "value": 8})))
        body = json.loads(r.body)
        assert r.status == 200 and body["ok"] and body["after"] == 8
        assert body["saved_sentence"] == "Saved: it survives a restart."
        row = _audit_rows()[-1]
        assert (row["via"], row["actor"]) == ("website", f"telegram:{OPERATOR}")
        r = _run(ug.handle_admin_settings_set(_Req(app, {
            "telegram_id": OPERATOR, "key": "leverage_override", "reset": True})))
        assert json.loads(r.body)["reset"] is True and RUNTIME.leverage_override is None

    @pytest.mark.parametrize("key", ["live_mode", "venue"])
    def test_live_trading_and_the_venue_are_refused_by_name(self, files, key):
        from bot.web import user_gateway as ug
        before = RUNTIME.live_mode
        r = _run(ug.handle_admin_settings_set(_Req(_gw_app(files), {
            "telegram_id": OPERATOR, "key": key, "value": True})))
        assert r.status == 403 and json.loads(r.body)["error"] == "not_web_editable"
        assert RUNTIME.live_mode == before and _audit_rows() == []

    def test_a_bad_value_is_a_400_with_a_sentence(self, files):
        from bot.web import user_gateway as ug
        r = _run(ug.handle_admin_settings_set(_Req(_gw_app(files), {
            "telegram_id": OPERATOR, "key": "auto_confirm_threshold", "value": 0.1})))
        body = json.loads(r.body)
        assert r.status == 400 and body["error"] == "bad_value" and "between" in body["detail"]
        r = _run(ug.handle_admin_settings_set(_Req(_gw_app(files), {
            "telegram_id": OPERATOR, "key": "leverage_override"})))
        assert r.status == 400 and json.loads(r.body)["error"] == "value_required"


# ── paper auto-accept is a setting now ──────────────────────────────────

class TestPaperAutoAccept:
    @pytest.mark.parametrize("configured,override,admitted", [
        (True, None, True), (False, None, False),
        (True, False, False), (False, True, True),
    ])
    def test_the_override_decides_and_none_means_the_config(
            self, files, configured, override, admitted):
        from unittest.mock import patch

        from bot.skills.telegram_handler import TelegramHandler
        from bot.utils.user_store import UserStore
        h = TelegramHandler.__new__(TelegramHandler)
        h.users = UserStore(files / "users.json")
        h.users.seed_admin(OPERATOR)
        RUNTIME.paper_auto_accept_override = override
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = OPERATOR
            mc.telegram.admin_ids = OPERATOR
            mc.paper_auto_accept = configured
            assert h._admit_for_paper("999", "Ann") is admitted
        assert h.users.is_admitted("999") is admitted


# ── the stance is the label it is ───────────────────────────────────────

_OLD_CLAIMS = ("sizing bias", "confirm every trade", "fewer signals",
               "Full human control", "stricter setup selection", "higher exposure")


def test_every_stance_surface_says_it_is_a_label():
    from bot.warroom.warroom_bot import render_strategy_mode
    text = render_strategy_mode("aggressive")["text"]
    assert reg.STANCE_NOTE in text
    assert not any(c.lower() in text.lower() for c in _OLD_CLAIMS), text
    from bot.skills.agent_commands import AgentCommands
    blurbs = " ".join(AgentCommands._STANCE_BLURB.values())
    assert not any(c.lower() in blurbs.lower() for c in _OLD_CLAIMS), blurbs
    # The dashboard's own table, sliced by its literal (the comment above it
    # quotes the old claims, so the whole file cannot be searched for them).
    js = (ROOT / "app/public/js/dashboard.js").read_text()
    start = js.index("const STANCE = {")
    table = js[start:js.index("}[stance];", start)]
    assert "larger sizing bias" not in table and "proposes only" not in table, table
    assert "a label; sizing and confirmation follow the risk settings" in js
    assert reg.SETTINGS["strategy_mode"].means == reg.STANCE_NOTE


def test_the_proposal_and_agent_card_carry_the_note():
    from bot.skills.agent_commands import AgentCommands
    sent = []

    async def _send(update, text, **kw):
        sent.append(text)
    host = SimpleNamespace(_send=_send, _STANCE_BLURB=AgentCommands._STANCE_BLURB,
                           _guard=AsyncMock(return_value=True),
                           engine=SimpleNamespace(live_executor=None))
    RUNTIME.strategy_mode = "balanced"
    try:
        _run(AgentCommands._propose_stance(host, MagicMock(), "defensive"))
        _run(AgentCommands._cmd_agent(host, MagicMock(), MagicMock()))
    finally:
        RUNTIME.strategy_mode = "balanced"
    assert len(sent) == 2 and all(reg.STANCE_NOTE in s for s in sent), sent


def test_no_number_in_the_registry_is_a_nan_bound():
    for s in reg.SETTINGS.values():
        for b in (s.lo, s.hi):
            assert b is None or math.isfinite(b)
