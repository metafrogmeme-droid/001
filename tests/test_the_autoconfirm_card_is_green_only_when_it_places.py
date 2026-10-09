"""The /autoconfirm card is green only when an order is placed with no tap.

9 October, the operator's live bot, read off Telegram:

    🟢 ON — ideas with confidence ≥ 90% are confirmed with no tap
    ⛔ Live: no order is placed without a tap, because no eligibility record
    exists for the running strategy (f8c64a40d2e3).

The second line was right and the first said the opposite in green. Colour is
a claim. `autoconfirm_status_line` decides the headline now, for the status
card and for the reply to `/autoconfirm 0.75` alike: green when live and the
live gate is open, yellow when the threshold is set and nothing is placed on
its own (the gate refuses, the gate could not be read, or paper mode), red
when off. Driven through the real command, both replies.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.config import CONFIG
from bot.skills import engine_ops_commands as eoc

GREEN, YELLOW, RED = "\U0001f7e2", "\U0001f7e1", "\U0001f534"


def _engine(refusal=None, raises=False):
    def _r():
        if raises:
            raise RuntimeError("down")
        return refusal
    return SimpleNamespace(_autonomous_live_refusal=_r)


def test_live_and_held_is_yellow_and_says_why():
    line = eoc.autoconfirm_status_line(_engine("no record"), 0.9, is_live=True)
    assert line.startswith(f"{YELLOW} <b>SET</b> at <b>90%</b>, placing nothing on its own")
    assert GREEN not in line
    assert "no order is placed without a tap, because no record" in line


def test_live_and_placing_is_green():
    line = eoc.autoconfirm_status_line(_engine(None), 0.9, is_live=True)
    assert line.startswith(f"{GREEN} <b>ON</b>")
    assert "<b>90%</b>" in line
    assert "placed with no tap" in line


def test_a_gate_that_could_not_be_read_is_not_green():
    line = eoc.autoconfirm_status_line(_engine(raises=True), 0.9, is_live=True)
    assert line.startswith(YELLOW) and GREEN not in line


def test_paper_mode_is_yellow():
    line = eoc.autoconfirm_status_line(_engine(None), 0.9, is_live=False)
    assert line.startswith(YELLOW) and "Paper mode" in line


def test_off_is_red():
    assert eoc.autoconfirm_status_line(_engine(None), 1.0, is_live=True).startswith(
        f"{RED} <b>OFF</b>")


# ── the real command, both replies ──────────────────────────────────────────

def _drive(args, refusal):
    sent: list = []

    async def _send(update, text, **kw):
        sent.append(text)

    host = SimpleNamespace(engine=_engine(refusal), _send=_send,
                           _guard=AsyncMock(return_value=True),
                           _get_tg_id=lambda u: "1")
    fn = eoc.EngineOpsCommands._cmd_autoconfirm
    fn = getattr(fn, "__wrapped__", fn)
    loop = asyncio.new_event_loop()
    try:
        with patch.object(type(CONFIG), "is_live", return_value=True):
            loop.run_until_complete(fn(host, SimpleNamespace(), SimpleNamespace(args=args)))
    finally:
        loop.close()
    assert len(sent) == 1, sent
    return sent[0]


def test_the_status_card_on_a_held_live_bot(monkeypatch):
    from bot.config import RUNTIME
    monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 0.9)
    text = _drive([], "no eligibility record exists for the running strategy")
    assert GREEN not in text and "confirmed with no tap" not in text
    assert f"{YELLOW} <b>SET</b> at <b>90%</b>" in text


def test_the_reply_to_setting_it_on_a_held_live_bot(monkeypatch):
    from bot.config import RUNTIME
    monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 1.0)
    text = _drive(["0.75"], "no eligibility record exists for the running strategy")
    assert "confirmed with no tap" not in text
    assert f"{YELLOW} <b>SET</b> at <b>75%</b>" in text


def test_the_status_card_on_a_placing_live_bot(monkeypatch):
    from bot.config import RUNTIME
    monkeypatch.setattr(RUNTIME, "auto_confirm_threshold", 0.9)
    text = _drive([], None)
    assert f"{GREEN} <b>ON</b>" in text
