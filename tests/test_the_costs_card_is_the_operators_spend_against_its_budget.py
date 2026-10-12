"""/costs is the operator's model spend, every category of it, against the budget the gates enforce.

The card printed "Net" as the CALLER's equity minus the OPERATOR's operating
cost, "Per Trade" as the operator's cost over the caller's trade count, and a
breakdown that walked five categories and skipped `chat` while the total
counted it. The chat tool said "against the daily budget" and the card had no
budget on it. And `costs` was held by viewers, self-admitted paper users and
web chat. Two more cards made the same claim: the status card's "Net" and
operating-cost rows, and the paper portfolio's waterfall ("LLM", "Infra",
"NET", "Per Trd").

What these drive, through the real `CostTracker`, `budget_reading` and the
cards themselves:

  * every category the ledger booked is printed, chat included, and the
    printed parts add up to the printed total;
  * no line on any of the three cards subtracts the deployment's spend from
    a caller's book;
  * the budget lines are the gates' own readings: the daily total, chat's cap
    (share or its own), the analysis cap, what is left, and which bound chat
    has reached;
  * a call on an unpriced model makes every figure a floor;
  * the card is the operator's at every door, and nobody else's card carries
    the operator's dollars.
"""
from __future__ import annotations

import asyncio
import re
import types
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from bot.core.cost import CHAT_SHARE_BOUND, CostTracker, budget_reading, chat_budget_bound, chat_cap_usd
from bot.skills import skill_registry as SR
from bot.utils.user_store import ROLE_PERMISSIONS, UserStore

OP, VIEWER = "7400001", "7400002"


@pytest.fixture
def llm(monkeypatch):
    """A known budget: $1.00 a day, chat's share half of it, no own caps."""
    def set_(**kw):
        cfg = replace(SR.CONFIG.llm, daily_budget_usd=1.0, chat_budget_share=0.5,
                      chat_budget_usd=None, thesis_budget_usd=None,
                      daily_call_limit=500)
        cfg = replace(cfg, **kw)
        monkeypatch.setattr(SR, "CONFIG", replace(SR.CONFIG, llm=cfg))
        return cfg
    return set_


def _ledger(*, chat=True, unpriced=False) -> CostTracker:
    ct = CostTracker()
    ct.record_llm("gpt-4o-mini", 50_000, 20_000, category="scan")
    ct.record_llm("gpt-4o", 30_000, 5_000, category="thesis")
    if chat:
        ct.record_llm("gpt-4o", 20_000, 4_000, category="chat")
    if unpriced:
        ct.record_llm("a-model-nobody-priced", 1_000, 100, category="chat")
    ct.record_infra(0.05)
    return ct


def _card(cost, who=OP, **eng) -> str:
    engine = NS(cost=cost, _is_operator_user=lambda uid: uid == OP, **eng)
    return asyncio.run(SR.CostBreakdownSkill().execute(engine, user_id=who))


def _amounts(line: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+\.\d+)", line)]


# ── 1. the parts are the whole ──────────────────────────────────────────

class TestEveryCategoryIsPrinted:
    def test_chat_is_a_category_and_the_parts_are_the_total(self, llm):
        llm()
        ct = _ledger()
        out = _card(ct)
        block = out[out.index("By category"):out.index("Budget")]
        rows = [ln for ln in block.splitlines() if ln.startswith("- ")]
        assert any(ln.startswith("- Chat:") for ln in rows), rows
        spent = _amounts(next(ln for ln in out.splitlines() if ln.startswith("- Spent:")))[0]
        assert sum(_amounts(ln)[0] for ln in rows) == pytest.approx(spent, abs=5e-4)
        assert spent == pytest.approx(ct.snapshot().llm_cost_usd, abs=5e-5)
        assert "add up to" not in out

    def test_a_day_with_no_chat_prints_no_chat_row(self, llm):
        llm()
        out = _card(_ledger(chat=False))
        block = out[out.index("By category"):out.index("Budget")]
        assert "- Chat:" not in block and "- Scan:" in block

    def test_a_split_that_does_not_reach_the_total_says_so(self, llm):
        llm()
        snap = _ledger().snapshot()
        snap.cost_by_category["chat"] = 0.0          # booked to no category
        out = _card(NS(snapshot=lambda: snap))
        assert "the rest was booked under no category" in out


# ── 2. nothing subtracts the operator's bill from a caller's book ──────────

class TestNoLineMixesTwoBooks:
    def test_the_costs_card_has_no_net_no_per_trade_and_no_equity(self, llm):
        llm()
        book = NS(snapshot=lambda: NS(equity_usd=12345.67, total_trades=3))
        out = _card(_ledger(), user_portfolios=NS(get=lambda uid, *a: book),
                    portfolio=book)
        for word in ("Net", "Per Trade", "Equity"):
            assert not re.search(rf"^- (<b>)?{word}\b", out, re.M), word
        assert "12,345.67" not in out

    def _status(self, who, llm):
        from bot.config import CONFIG
        from tests.test_a_card_reads_the_callers_own_breaker_and_drawdown import _Risk
        llm()
        risk = _Risk(0.4)
        state = NS(max_drawdown_pct=0.0, equity_usd=10000.0, open_positions=0,
                   total_trades=0, daily_pnl=0.0, win_rate=None)
        pf = NS(snapshot=lambda: state, open_positions=[])

        async def eq(uid):
            return 250.0
        ex = NS(open_positions=[], closed_positions=[], closed_trades_read_failed=False)
        engine = NS(risk=risk, risk_for=lambda uid: risk,
                    user_portfolios=NS(get=lambda uid, *a: pf), cost=_ledger(),
                    viewer_executor=lambda uid: ex, get_effective_equity_async=eq,
                    _halted=False, _is_operator_user=lambda uid: uid == OP,
                    macro_calendar=NS(evaluate=lambda: NS(state=NS(value="NORMAL"))),
                    live_auth_healthy=lambda uid: True, _live_auth_detail={})
        with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
            out = asyncio.run(SR.CheckRiskSkill().execute(engine, user_id=who, mode="status"))
        return re.sub(r"<[^>]+>", "", out)

    def test_the_status_card_has_no_net_and_only_the_operator_sees_the_spend(self, llm):
        mine, theirs = self._status(OP, llm), self._status(VIEWER, llm)
        for card in (mine, theirs):
            assert not re.search(r"^- Net:", card, re.M), card
            assert "Equity: $250.00" in card
        assert "Costs today (UTC)" in mine and "- LLM: $" in mine
        assert "Costs today" not in theirs and "- LLM:" not in theirs

    def test_the_paper_waterfall_stops_at_the_books_own_pnl(self, llm):
        from bot.config import CONFIG
        llm()
        state = NS(equity_usd=10_050.0, balance_usd=10_000.0, win_rate=0.5,
                   total_gross_pnl=60.0, total_commission=10.0, total_pnl=50.0,
                   total_trades=4)
        pf = NS(snapshot=lambda: state, open_positions=[])
        engine = NS(cost=_ledger(), user_portfolios=NS(get=lambda uid, *a: pf))
        with mock.patch.object(type(CONFIG), "is_live", lambda self: False):
            out = asyncio.run(SR.GetPortfolioSkill().execute(engine, user_id=VIEWER))
        for row in ("LLM", "Infra", "NET", "Per Trd"):
            assert not re.search(rf"▸ {row}\b|▶ {row}\b", out), row
        assert re.search(r"▶ Trading\s+\$\+50\.00", out), out
        assert "All-time: 4 trades • Trading $+50.00" in out
        assert "Session:" not in out


# ── 3. the budget is the gates' reading ────────────────────────────────

class TestTheBudgetIsTheGatesReading:
    def test_the_daily_and_chat_lines_are_budget_reading(self, llm):
        cfg = llm()
        ct = _ledger()
        b = budget_reading(ct.snapshot(), cfg)
        out = _card(ct)
        daily = next(ln for ln in out.splitlines() if ln.startswith("- Daily:"))
        assert _amounts(daily) == pytest.approx(
            [b["spent"], 1.00, b["daily_left"]], abs=5e-5)
        chat = next(ln for ln in out.splitlines() if ln.startswith("- Chat:")
                    and "of <code>" in ln)
        assert _amounts(chat) == pytest.approx(
            [b["chat_spent"], 0.50, b["chat_left"]], abs=5e-5)
        assert "50% of the daily budget" in chat and "1 of 500 calls" in chat
        assert "no cap of its own" in out
        assert "stopped" not in out

    def test_chat_left_is_clipped_to_the_daily_total(self, llm):
        cfg = llm(daily_budget_usd=0.25, chat_budget_share=1.0)
        b = budget_reading(_ledger().snapshot(), cfg)
        assert b["chat_left"] == pytest.approx(b["daily_left"])
        assert b["chat_left"] < b["chat_cap"] - b["chat_spent"]

    def test_chat_on_its_own_cap_says_so(self, llm):
        cfg = llm(chat_budget_usd=0.2)
        out = _card(_ledger())
        assert chat_cap_usd(cfg) == 0.2
        assert "of <code>$0.20</code> (its own cap)" in out

    def test_a_reached_bound_is_named_with_the_gates_word(self, llm):
        cfg = llm(chat_budget_share=0.05)         # chat's $0.09 is past $0.05
        ct = _ledger()
        assert chat_budget_bound(ct.snapshot(), cfg) == CHAT_SHARE_BOUND
        out = _card(ct)
        assert f"Chat has stopped for today: {CHAT_SHARE_BOUND} reached." in out.replace("&#x27;", "'")

    def test_the_analysis_cap_when_set(self, llm):
        cfg = llm(thesis_budget_usd=0.5)
        ct = _ledger()
        b = budget_reading(ct.snapshot(), cfg)
        out = _card(ct)
        line = next(ln for ln in out.splitlines() if ln.startswith("- Analysis:"))
        assert _amounts(line) == pytest.approx(
            [b["thesis_spent"], 0.50, b["thesis_left"]], abs=5e-5)
        assert b["thesis_spent"] == pytest.approx(b["spent"] - b["chat_spent"])

    def test_the_analysis_cap_is_not_clipped_to_the_total(self, llm):
        """With its own cap set, the thesis gate reads that cap alone
        (`thesis_dollar_exhausted`), so what is left is not the daily total's."""
        cfg = llm(thesis_budget_usd=5.0, daily_budget_usd=0.2)
        b = budget_reading(_ledger().snapshot(), cfg)
        assert b["daily_left"] == 0.0
        assert b["thesis_left"] == pytest.approx(5.0 - b["thesis_spent"])
        assert b["thesis_exhausted"] is False

    def test_an_unreadable_split_leaves_analysis_unknown(self, llm):
        cfg = llm(thesis_budget_usd=0.5)
        snap = _ledger().snapshot()
        del snap.cost_by_category["chat"]
        b = budget_reading(snap, cfg)
        assert b["thesis_spent"] is None and b["thesis_left"] is None
        out = _card(NS(snapshot=lambda: snap))
        assert "what is left is unknown" in out

    def test_an_exhausted_analysis_cap_is_said(self, llm):
        llm(thesis_budget_usd=0.01)
        out = _card(_ledger())
        assert "Trade analysis is on the rule engine" in out


class TestTheChatCapIsOneReading:
    def test_the_gate_and_the_card_read_one_cap(self, llm):
        share = llm()
        own = llm(chat_budget_usd=0.07)
        assert chat_cap_usd(share) == pytest.approx(0.5)
        assert chat_cap_usd(own) == pytest.approx(0.07)
        snap = _ledger().snapshot()          # chat spent $0.09
        assert chat_budget_bound(snap, share) == ""
        assert chat_budget_bound(snap, own) != ""

    def test_the_reply_audit_reads_it_too(self):
        import inspect

        from bot.skills import telegram_handler as th
        from tests.source_scan import code_only
        src = code_only(inspect.getsource(th.TelegramHandler))
        assert "_chat_cap = chat_cap_usd(CONFIG.llm)" in src
        assert "daily_budget_usd * CONFIG.llm.chat_budget_share" not in src


# ── 4. an unpriced call ─────────────────────────────────────────────────

class TestAnUnpricedCallIsAFloor:
    def test_every_figure_says_floor_or_ceiling(self, llm):
        llm()
        out = _card(_ledger(unpriced=True))
        assert "1 call(s) used a model with no price on record" in out
        daily = next(ln for ln in out.splitlines() if ln.startswith("- Daily:"))
        assert "at least $" in daily and "at most $" in daily
        assert "- Total: <code>at least $" in out

    def test_a_priced_day_claims_no_floor(self, llm):
        llm()
        out = _card(_ledger())
        assert "at least" not in out and "at most" not in out
        assert "no price on record" not in out


# ── 5. every door is the operator's ─────────────────────────────────────

class TestEveryDoorIsTheOperators:
    @pytest.mark.parametrize("who", [VIEWER, "", None])
    def test_anyone_else_is_told_and_shown_no_dollar(self, llm, who):
        llm()
        out = _card(_ledger(), who=who)
        assert "shown to the operator only" in out and "$" not in out

    def test_an_unreadable_ledger_says_so(self, llm):
        llm()

        def _raises():
            raise RuntimeError("ledger locked")
        out = _card(NS(snapshot=_raises))
        assert "could not be read (RuntimeError)" in out and "ledger locked" not in out

    @pytest.fixture
    def users(self, tmp_path):
        st = UserStore(str(tmp_path / "users.json"))
        assert st.authorize(OP, "admin", by="999")
        assert st.authorize(VIEWER, "viewer", by="999")
        return st

    def test_no_role_but_admin_holds_the_permission(self, users):
        holders = {role for role, perms in ROLE_PERMISSIONS.items()
                   if "costs" in perms or "*" in perms}
        assert holders == {"admin"}
        assert users.permission_denial(VIEWER, "costs") == "role"
        assert users.permission_denial(OP, "costs") is None

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_chat_offers_the_tool_to_the_operator_alone(self, users, monkeypatch, surface):
        from bot.nlp import chat_tools
        from bot.token import tier_gate
        monkeypatch.setattr(tier_gate, "check_user", lambda u, uid, f: (True, "ok"))

        def names(uid):
            return {t.name for t in chat_tools.tools_for(users, uid, surface)}
        assert "costs" in names(OP)
        assert "costs" not in names(VIEWER)

    def test_the_card_asks_the_engines_operator_reading(self, users, llm):
        """The real reading, bound: an admin in the store is the operator."""
        from bot.core.engine import RuneClawEngine
        llm()
        eng = NS(cost=_ledger(), _user_store=users)
        eng._is_operator_user = types.MethodType(RuneClawEngine._is_operator_user, eng)
        mine = asyncio.run(SR.CostBreakdownSkill().execute(eng, user_id=OP))
        theirs = asyncio.run(SR.CostBreakdownSkill().execute(eng, user_id=VIEWER))
        assert "Budget" in mine and "shown to the operator only" in theirs
