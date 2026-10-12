"""The day's LLM spend outlives a restart, so no restart hands a cap its budget back.

`CostTracker` kept the day in memory and the engine builds a fresh one at
start, so every deploy began the day at $0: the combined dollar total, chat's
share, the public preview's slice and the call limit were all spendable again.
The bot's ledger is now kept on disk and read back at start.

What these drive, through the real tracker, the real engine and the real card:

  * a start on the same day restores every figure the gates read, the
    public preview's subset included, so a cap reached before a restart is
    still reached after it;
  * an earlier day's file is folded into the lifetime total, as the day
    boundary folds it, and the new day starts at nothing;
  * a file that will not read, or reads with a figure missing, is never
    written over, and the card says the figures count from the restart;
  * a ledger with no path writes nothing, and an engine that is not the bot
    (`detach_state_persistence`) neither writes the bot's file nor shows a
    frozen copy of the bot's day.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from bot.core import cost as C
from bot.core.cost import CostTracker, budget_reading, chat_budget_bound, public_chat_budget_bound
from bot.skills import skill_registry as SR

OP = "7400001"


def _book(ct: CostTracker) -> None:
    ct.record_llm("gpt-4o-mini", 50_000, 20_000, category="scan")
    ct.record_llm("gpt-4o", 30_000, 5_000, category="thesis")
    ct.record_llm("gpt-4o", 20_000, 4_000, category="chat")
    ct.record_llm("gpt-4o", 10_000, 2_000, category="chat", public=True)
    ct.record_llm("a-model-nobody-priced", 1_000, 100, category="chat")
    ct.record_infra(0.05)


def _figures(s) -> dict:
    return {**{k: getattr(s, k) for k in C._SUMMARY_NUMBERS},
            "cost_by_category": dict(s.cost_by_category),
            "calls_by_category": dict(s.calls_by_category)}


def _card(cost) -> str:
    engine = NS(cost=cost, _is_operator_user=lambda uid: uid == OP)
    return asyncio.run(SR.CostBreakdownSkill().execute(engine, user_id=OP))


@pytest.fixture
def llm():
    return replace(SR.CONFIG.llm, daily_budget_usd=1.0, chat_budget_share=0.5,
                   chat_budget_usd=None, thesis_budget_usd=None, daily_call_limit=500,
                   public_chat_budget_share=0.2)


# ── the same day ─────────────────────────────────────────────────────────

def test_a_start_on_the_same_day_restores_every_figure(tmp_path, llm):
    path = str(tmp_path / C.SPEND_FILE)
    before = CostTracker(path=path)
    assert before.persistence == C.SPEND_FRESH
    _book(before)

    after = CostTracker(path=path)
    assert after.persistence == C.SPEND_RESTORED
    assert _figures(after.snapshot()) == _figures(before.snapshot())
    assert after.snapshot().public_chat_calls == 1, "the preview's subset too"
    assert budget_reading(after.snapshot(), llm) == budget_reading(before.snapshot(), llm)


def test_a_cap_reached_before_a_restart_is_still_reached_after_it(tmp_path, llm):
    path = str(tmp_path / C.SPEND_FILE)
    before = CostTracker(path=path)
    for _ in range(3):
        before.record_llm("gpt-4o", 100_000, 30_000, category="chat", public=True)
    assert chat_budget_bound(before.snapshot(), llm), "fixture: chat's share is spent"
    assert public_chat_budget_bound(before.snapshot(), llm), "and the preview's slice"

    after = CostTracker(path=path)
    assert chat_budget_bound(after.snapshot(), llm) == chat_budget_bound(before.snapshot(), llm)
    assert public_chat_budget_bound(after.snapshot(), llm) == \
        public_chat_budget_bound(before.snapshot(), llm)
    assert not chat_budget_bound(CostTracker().snapshot(), llm), (
        "the other arm: a ledger that read nothing has the whole budget")


# ── an earlier day ───────────────────────────────────────────────────────

def test_an_earlier_day_is_folded_into_the_lifetime_total(tmp_path):
    path = str(tmp_path / C.SPEND_FILE)
    old = CostTracker(path=path)
    _book(old)
    data = json.loads(open(path).read())
    data["day"] = "2026-10-10"
    open(path, "w").write(json.dumps(data))

    today = CostTracker(path=path)
    assert today.persistence == C.SPEND_FRESH
    assert today.snapshot().llm_calls == 0 and today.snapshot().llm_cost_usd == 0.0
    life = today.snapshot_lifetime()
    assert life.llm_calls == old.snapshot().llm_calls
    assert life.llm_cost_usd == pytest.approx(old.snapshot().llm_cost_usd)


# ── a file that will not read ────────────────────────────────────────────

def _one_figure_missing() -> str:
    """A whole ledger but for the day's call count: not a day of no calls."""
    full = C._summary_json(C.CostSummary(llm_cost_usd=0.4, llm_calls=9))
    daily = {k: v for k, v in full.items() if k != "llm_calls"}
    return json.dumps({"day": "2026-10-11", "daily": daily, "lifetime": full})


@pytest.mark.parametrize("body", [
    "{not json",
    json.dumps({"day": "2026-10-11", "daily": {"llm_cost_usd": 0.4}, "lifetime": {}}),
    _one_figure_missing(),
    json.dumps(["a", "list"]),
])
def test_an_unreadable_file_is_never_written_over_and_the_card_says_so(tmp_path, body):
    path = tmp_path / C.SPEND_FILE
    path.write_text(body)
    ct = CostTracker(path=str(path))
    assert ct.persistence == C.SPEND_UNREADABLE
    _book(ct)
    assert path.read_text() == body, "somebody's ledger, kept as it is"
    card = _card(ct)
    assert "could not be read" in card and "count from that restart" in card, card


def test_a_ledger_on_record_puts_no_restart_line_on_the_card(tmp_path):
    ct = CostTracker(path=str(tmp_path / C.SPEND_FILE))
    _book(ct)
    card = _card(CostTracker(path=str(tmp_path / C.SPEND_FILE)))
    assert "could not be read" not in card and "this process's own" not in card, card


# ── no file, and a process that is not the bot ───────────────────────────

def test_a_ledger_with_no_path_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ct = CostTracker()
    _book(ct)
    assert ct.persistence == C.SPEND_OFF
    assert list(tmp_path.iterdir()) == []
    assert "this process's own" in _card(ct)


def test_a_reader_neither_writes_the_bot_s_file_nor_shows_a_frozen_day(tmp_path):
    path = tmp_path / C.SPEND_FILE
    bot = CostTracker(path=str(path))
    _book(bot)
    on_disk = path.read_text()

    other = CostTracker(path=str(path))
    other.detach_from_file()
    assert other.snapshot().llm_calls == 0, "not a copy of the bot's day that never moves"
    other.record_llm("gpt-4o", 1_000, 1_000, category="chat")
    assert path.read_text() == on_disk
    assert other.persistence == C.SPEND_OFF


def test_the_engine_keeps_its_ledger_and_a_detached_engine_does_not():
    from bot.core.engine import RuneClawEngine
    from bot.utils.paths import state_path
    eng = RuneClawEngine()
    assert eng.cost._path == str(state_path(f"data/{C.SPEND_FILE}"))
    assert eng.cost.persistence in (C.SPEND_FRESH, C.SPEND_RESTORED)
    eng.detach_state_persistence()
    assert eng.cost._path is None and eng.cost.persistence == C.SPEND_OFF
