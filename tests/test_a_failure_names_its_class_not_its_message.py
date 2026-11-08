"""Error text reaching a person names the exception class, never its message.

The repo audit of 2026-11-07 found seven sites that put `str(exc)` in front of
a person: two close-failure replies, three skill replies, the unauthenticated
`/ready` body and the /closeall card. A ccxt error carries the request URL and
account detail; only the outbound scrubber's pattern list stood between that
text and the reader. Each site now names the class.

Driven through the real skills, `handle_ready` and `flatten_all_positions`
with an exception whose message carries a marker. The two close replies sit
inside the 1,700-line callback dispatcher, so they are read off its source.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.skills.macro_skills import CheckEventRiskSkill, KillSwitchSkill, RequestLiveApprovalSkill
from tests.source_scan import code_only

MARKER = "MARKER-https://api.example/v2?apiKey=abc"


def _raise(*_a, **_k):
    raise RuntimeError(MARKER)


@pytest.mark.parametrize("skill,engine,kwargs", [
    (CheckEventRiskSkill(), NS(macro_provider=NS(get_context=_raise), macro_calendar=None),
     {"symbol": "BTC"}),
    (RequestLiveApprovalSkill(), NS(approval_manager=NS(issue_token=_raise)),
     {"trade_id": "t1"}),
    (KillSwitchSkill(), NS(circuit_breaker=NS(trip=_raise)), {"reason": "test"}),
])
def test_a_skill_that_failed_names_the_class(skill, engine, kwargs):
    out = asyncio.run(skill.execute(engine, **kwargs))
    assert "RuntimeError" in out, out
    assert "MARKER" not in out and "apiKey" not in out, out


def test_ready_names_the_class_to_an_unauthenticated_caller():
    from bot.web.dashboard_server import handle_ready
    engine = NS(health=NS(snapshot=_raise))
    resp = asyncio.run(handle_ready(NS(app={"engine": engine})))
    body = json.loads(resp.text)
    assert resp.status == 503 and body == {"ready": False, "error": "RuntimeError"}


def test_a_flatten_that_raised_names_the_class_on_the_card(monkeypatch):
    monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)

    class _Book:
        user_id = None

        async def close_all_positions(self, reason=""):
            raise RuntimeError(MARKER)

    eng = RuneClawEngine()
    eng.live_executor = _Book()
    eng._user_executors = {}
    rows = asyncio.run(eng.flatten_all_positions(reason="admin_closeall"))
    assert rows[0]["messages"] == ["close_all_positions failed: RuntimeError"]
    assert rows[0]["ok"] is False, "a failed close is still a failed account"


def test_the_close_failure_replies_name_the_class():
    from pathlib import Path
    src = code_only((Path(__file__).resolve().parents[1]
                     / "bot/skills/callback_handler.py").read_text(encoding="utf-8"))
    assert "html.escape(str(e)" not in src
    assert src.count("The exchange answered {type(e).__name__}") == 2
