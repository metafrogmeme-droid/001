"""Admin preview on the anonymous Daily Duel bot.

The bot had no admin check of its own. It reuses TelegramHandler._is_admin_id's
rule: user-store role ``admin``, or ``ADMIN_TELEGRAM_IDS``. No new allowlist.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

from bot import admin_preview
from bot.config import CONFIG
from bot.scam_or_safe import NOT_OPEN, SCAM_HELP
from tests.test_scam_or_safe_daily import _bot, _run, _texts, _update
from tests.test_the_daily_duel_bot import TG
from tests.test_the_daily_duel_bot import _update as duel_update

ROOT = pathlib.Path(__file__).resolve().parents[1]
OTHER = "777001"


def _as_admin(monkeypatch, tg=TG):
    new_tg = dataclasses.replace(CONFIG.telegram, admin_ids=str(tg))
    monkeypatch.setattr("bot.config.CONFIG", dataclasses.replace(CONFIG, telegram=new_tg))


def test_the_helper_names_no_enabled_and_an_unknown_flag_is_off():
    tree = ast.parse((ROOT / "bot" / "admin_preview.py").read_text(encoding="utf-8"))
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "enabled" not in names
    assert admin_preview.flag_on_for(TG, "NOT_A_FLAG", env={}) is False
    assert admin_preview.is_preview(TG, "NOT_A_FLAG", env={}) is False
    assert admin_preview.public_on("NOT_A_FLAG", env={}) is False


def test_public_on_admits_everyone_and_off_admits_only_the_existing_admin_rule():
    env = {"SCAM_OR_SAFE_V0": "1", "DUEL_CONFIDENCE_ENABLED": "on"}
    assert admin_preview.flag_on_for(OTHER, "SCAM_OR_SAFE_V0", env=env, store={}) is True
    assert admin_preview.is_preview(TG, "DUEL_CONFIDENCE_ENABLED", env=env, store={}) is False
    off = {"SCAM_OR_SAFE_V0": "0", "DUEL_CONFIDENCE_ENABLED": ""}
    assert admin_preview.flag_on_for(OTHER, "SCAM_OR_SAFE_V0", env=off, store={}) is False
    assert admin_preview.flag_on_for(
        TG, "SCAM_OR_SAFE_V0", env={**off, "ADMIN_TELEGRAM_IDS": TG}, store={}) is True
    assert admin_preview.flag_on_for(
        OTHER, "DUEL_CONFIDENCE_ENABLED", env={**off, "ADMIN_TELEGRAM_IDS": TG}, store={}) is False
    assert admin_preview.flag_on_for(
        TG, "DUEL_CONFIDENCE_ENABLED", env=off, store={TG: {"role": "admin"}}) is True
    assert admin_preview.is_preview(
        TG, "SCAM_OR_SAFE_V0", env=off, store={TG: {"role": "trader"}}) is False
    # A passed mapping is the only id list. The process environment is not a back door.
    assert admin_preview.telegram_admin(TG, store={}, env={}) is False


def test_a_missing_user_file_is_not_an_admin(tmp_path, monkeypatch):
    import bot.utils.paths as paths
    monkeypatch.setattr(paths, "state_path", lambda relative: tmp_path / "no-such-users.json")
    assert admin_preview.telegram_admin(TG, store=None, env={}) is False
    broken = tmp_path / "users.json"
    broken.write_text("{", encoding="utf-8")
    monkeypatch.setattr(paths, "state_path", lambda relative: broken)
    assert admin_preview.telegram_admin(TG, store=None, env={}) is False
    broken.write_text(json.dumps({TG: {"role": "admin"}}), encoding="utf-8")
    assert admin_preview.telegram_admin(TG, store=None, env={}) is True
    assert not (tmp_path / "users.json.corrupt").exists()


def test_flag_off_non_admin_is_today_and_an_admin_can_open_scam(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAM_OR_SAFE_V0", raising=False)
    monkeypatch.delenv("DUEL_CONFIDENCE_ENABLED", raising=False)
    bot, gate, _ = _bot(tmp_path)
    stranger = _update(user_id=OTHER)
    _run(bot.cmd_scam(stranger, None))
    assert _texts(stranger) == [NOT_OPEN]
    assert not hasattr(gate, "offered")
    help_view = _update(user_id=OTHER)
    _run(bot.cmd_help(help_view, None))
    assert SCAM_HELP not in _texts(help_view)[0]
    assert admin_preview.BANNER not in _texts(help_view)[0]

    _as_admin(monkeypatch)
    admin = _update(user_id=TG)
    _run(bot.cmd_scam(admin, None))
    assert gate.caller_is_admin is True
    assert _texts(admin)[0].startswith(admin_preview.BANNER)
    assert "Safe" in _texts(admin)[0] or "safe" in _texts(admin)[0].lower()
    help_admin = _update(user_id=TG)
    _run(bot.cmd_help(help_admin, None))
    assert SCAM_HELP in _texts(help_admin)[0]
    assert _texts(help_admin)[0].startswith(admin_preview.BANNER)


def test_flag_on_scam_is_not_a_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")
    monkeypatch.setenv("DUEL_CONFIDENCE_ENABLED", "1")
    _as_admin(monkeypatch)
    from bot.daily_duel_bot import WELCOME
    from bot.scam_or_safe import SCAM_WELCOME
    bot, gate, _ = _bot(tmp_path)
    admin = _update()
    _run(bot.cmd_start(admin, None))
    assert _texts(admin) == [WELCOME, SCAM_WELCOME]
    assert admin_preview.BANNER not in _texts(admin)[0]


def test_an_admin_is_offered_a_confidence_rating_while_the_flag_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("DUEL_CONFIDENCE_ENABLED", raising=False)
    _as_admin(monkeypatch)
    from tests.test_the_daily_duel_bot import _bot as duel_bot
    bot, gate, _ = duel_bot(tmp_path)
    view = duel_update()
    _run(bot.cmd_duel(view, None))
    assert gate.caller_is_admin is True
    assert "low, medium or high" in _texts(view)[0]
    assert _texts(view)[0].startswith(admin_preview.BANNER)
    stranger = duel_update(user_id=OTHER)
    _run(bot.cmd_duel(stranger, None))
    assert "how sure" not in _texts(stranger)[0].lower()
    assert admin_preview.BANNER not in _texts(stranger)[0]
