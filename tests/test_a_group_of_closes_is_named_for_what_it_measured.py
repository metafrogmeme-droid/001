"""/patterns names a group by the condition it was cut on, and only the operator reads it.

`PatternLearner.detect_patterns` named the learning store's groups after
patterns nobody looked for: a trend-regime group winning over 55% of five or
more closes was TREND_CONTINUATION, and a range or chop group winning under
40% was BREAKOUT_FAILURE, though nothing checked that one of those trades was
a breakout. Its "confidence" was `min(win_rate, 0.85)`. The card printed that
confidence, the win rate with no interval and an average in DOLLARS, and the
`patterns` permission was held by viewers, self-admitted paper users and web
chat, while the store is every account's closes with no owner.

What these drive, through the real learner, store, orchestrator, user store
and the engine's own operator reading:

  * a group is named by its dimension and value, and every group over the
    floor is reported, not only the ones whose rate looked remarkable;
  * the rate carries its 95% Wilson interval (`readiness.wilson_interval`,
    the arithmetic parity reads), a 0.0 close is flat and an unpriced one is
    unscored;
  * the card prints counts and the interval, never a dollar or a confidence,
    and says the window it read;
  * a decision log that will not read is said to be unreadable, never "no
    condition has enough closes";
  * the card is the operator's at every door: no role but admin holds the
    permission, chat offers the tool to nobody else, the command hands the
    card the caller's id, and the card asks who is reading.
"""
from __future__ import annotations

import asyncio
import math
import types
from types import SimpleNamespace as NS

import pytest

from bot.learning.models import DecisionMemory, PatternRecord
from bot.learning.orchestrator import LearningOrchestrator
from bot.learning.patterns import DECISION_WINDOW, MIN_GROUP_CLOSES, PatternLearner
from bot.learning.readiness import wilson_interval
from bot.utils.user_store import ROLE_PERMISSIONS, UserStore

OP, VIEWER = "7300001", "7300002"

#: The names the learner used to invent.
_INVENTED = {"trend_continuation", "breakout_failure", "macro_event_whipsaw",
             "volatility_compression"}


def _rows(regime: str, wins: int, losses: int, flat: int = 0, **kw) -> list:
    pnls = [25.0] * wins + [-25.0] * losses + [0.0] * flat
    return [DecisionMemory(market_regime=regime, pnl_result=p, **kw) for p in pnls]


@pytest.fixture
def orch(tmp_path):
    return LearningOrchestrator(str(tmp_path / "learning"))


@pytest.fixture
def users(tmp_path):
    st = UserStore(str(tmp_path / "users.json"))
    assert st.authorize(OP, "admin", by="999")
    assert st.authorize(VIEWER, "viewer", by="999")
    return st


def _engine(orch, users):
    """A stand-in carrying the REAL operator reading, bound, not restated."""
    from bot.core.engine import RuneClawEngine
    eng = NS(learning=orch, _user_store=users)
    eng._is_operator_user = types.MethodType(RuneClawEngine._is_operator_user, eng)
    return eng


def _card(eng, **kw) -> str:
    from bot.skills.skill_registry import PatternsSkill
    return asyncio.run(PatternsSkill().execute(eng, **kw))


def _record(orch, rows) -> None:
    for r in rows:
        orch.store.record_decision(r)


# ── 1. the learner names the condition, and reports every group ────────────

class TestTheLearnerNamesTheCondition:
    def test_a_losing_range_group_is_a_range_group_not_a_breakout(self):
        reading = PatternLearner(None).outcome_groups(_rows("RANGE", 2, 6))
        (g,) = reading.groups
        assert (g.pattern_type, g.market_regime, g.wins, g.losses) == ("regime", "RANGE", 2, 6)
        assert g.pattern_type not in _INVENTED

    def test_every_group_over_the_floor_is_reported_not_only_the_striking_ones(self):
        """50% in a trend and 50% in a range: the old thresholds (over 55%,
        under 40%) dropped both, so the card showed only the groups whose
        rate looked like a finding."""
        rows = _rows("TREND_UP", 3, 3) + _rows("RANGE", 3, 3)
        found = {g.market_regime for g in PatternLearner(None).outcome_groups(rows).groups}
        assert found == {"TREND_UP", "RANGE"}

    def test_a_group_below_the_floor_is_not_reported(self):
        below = _rows("CHOP", MIN_GROUP_CLOSES - 1, 0)
        at = _rows("RANGE", MIN_GROUP_CLOSES, 0)
        found = {g.market_regime for g in PatternLearner(None).outcome_groups(below + at).groups}
        assert found == {"RANGE"}

    def test_the_rate_carries_its_wilson_interval(self):
        (g,) = PatternLearner(None).outcome_groups(_rows("TREND_UP", 7, 3)).groups
        assert g.historical_win_rate == pytest.approx(0.7)
        assert g.win_rate_interval == wilson_interval(7, 10)
        lo, hi = g.win_rate_interval
        assert 0 < lo < 0.7 < hi < 1

    def test_a_flat_close_is_flat_and_an_unpriced_one_is_unscored(self):
        rows = _rows("RANGE", 3, 1, flat=1) + [
            DecisionMemory(market_regime="RANGE", pnl_result=math.nan),
            DecisionMemory(market_regime="RANGE")]          # a decision, not a close
        reading = PatternLearner(None).outcome_groups(rows)
        (g,) = reading.groups
        assert (g.wins, g.losses, g.flat, g.sample_size) == (3, 1, 1, 5)
        assert g.historical_win_rate == pytest.approx(0.6)
        assert (reading.rows_read, reading.closes, reading.unscored) == (7, 5, 1)

    def test_macro_and_volatility_groups_share_the_floor_and_normal_is_no_group(self):
        rows = (_rows("", MIN_GROUP_CLOSES, 0, macro_state="FOMC")
                + _rows("", MIN_GROUP_CLOSES - 1, 0, macro_state="CPI")
                + _rows("", MIN_GROUP_CLOSES, 0, macro_state="NORMAL")
                + _rows("", MIN_GROUP_CLOSES, 0, volatility_state="low"))
        got = {(g.pattern_type, g.market_regime)
               for g in PatternLearner(None).outcome_groups(rows).groups}
        assert ("macro_state", "FOMC") in got and ("volatility", "low") in got
        assert ("macro_state", "CPI") not in got, "the macro floor was three of its own"
        assert ("macro_state", "NORMAL") not in got

    def test_no_record_carries_a_confidence_or_a_dollar_average(self):
        assert "confidence" not in PatternRecord.model_fields
        assert "avg_pnl" not in PatternRecord.model_fields
        assert PatternRecord().historical_win_rate is None


# ── 2. the card ───────────────────────────────────────────────────────────

class TestTheCard:
    def test_the_operator_reads_counts_and_an_interval_and_no_dollar(self, orch, users):
        _record(orch, _rows("RANGE", 2, 6))
        out = _card(_engine(orch, users), user_id=OP)
        lo, hi = wilson_interval(2, 8)
        assert "Regime RANGE" in out and "8 closes" in out and "2W 6L" in out
        assert f"won 25% (95%: {lo:.0%}–{hi:.0%})" in out
        assert f"newest {DECISION_WINDOW:,} rows" in out
        assert "$" not in out
        assert "Conf" not in out and "BREAKOUT" not in out.upper()

    @pytest.mark.parametrize("who", [VIEWER, "", None])
    def test_anyone_else_is_told_it_is_the_operators_and_sees_no_group(self, orch, users, who):
        _record(orch, _rows("RANGE", 2, 6))
        out = _card(_engine(orch, users), user_id=who)
        assert "shown to the operator only" in out
        assert "RANGE" not in out and "won " not in out

    @pytest.mark.parametrize("who", ["", None])
    def test_no_caller_is_nobody_whatever_the_engine_answers(self, orch, who):
        """The card's own rule, not the engine's: an identity reading that
        would say yes to an empty id still does not open it."""
        _record(orch, _rows("RANGE", 2, 6))
        eng = NS(learning=orch, _is_operator_user=lambda uid: True)
        out = _card(eng, user_id=who)
        assert "shown to the operator only" in out and "RANGE" not in out
        assert "RANGE" in _card(eng, user_id=OP)

    def test_an_identity_check_that_raises_is_not_a_yes(self, orch):
        _record(orch, _rows("RANGE", 2, 6))

        def _raises(uid):
            raise RuntimeError("user store unreadable")
        out = _card(NS(learning=orch, _is_operator_user=_raises), user_id=OP)
        assert "shown to the operator only" in out and "RANGE" not in out

    def test_an_engine_with_no_identity_reading_opens_it_to_nobody(self, orch):
        _record(orch, _rows("RANGE", 2, 6))
        out = _card(NS(learning=orch), user_id=OP)
        assert "shown to the operator only" in out and "RANGE" not in out

    def test_an_unreadable_log_is_not_a_log_with_no_closes(self, orch, users):
        path = orch.store._files["decision"]
        if path.exists():
            path.unlink()
        path.mkdir()                     # there, and open() cannot read it
        out = _card(_engine(orch, users), user_id=OP)
        assert "could not be read (IsADirectoryError)" in out
        assert "No condition has" not in out

    def test_a_readable_empty_log_says_nothing_reached_the_floor(self, orch, users):
        out = _card(_engine(orch, users), user_id=OP)
        assert f"No condition has {MIN_GROUP_CLOSES} scored closes" in out
        assert "could not be read" not in out

    def test_only_the_strict_read_raises(self, orch):
        path = orch.store._files["decision"]
        path.mkdir()
        with pytest.raises(IsADirectoryError):
            orch.store.get_decisions(limit=10, strict=True)
        # Every other reader keeps what it had: the learners load and refit
        # on their own schedule and were not asked about this.
        assert orch.store.get_decisions(limit=10) == []


# ── 3. every door ─────────────────────────────────────────────────────────

class TestEveryDoorIsTheOperators:
    def test_no_role_but_admin_holds_the_permission(self, users):
        holders = {role for role, perms in ROLE_PERMISSIONS.items()
                   if "patterns" in perms or "*" in perms}
        assert holders == {"admin"}
        assert users.permission_denial(VIEWER, "patterns") == "role"
        assert users.permission_denial(OP, "patterns") is None

    @pytest.mark.parametrize("surface", ["telegram", "web"])
    def test_chat_offers_the_tool_to_the_operator_alone(self, users, monkeypatch, surface):
        from bot.nlp import chat_tools
        from bot.token import tier_gate
        monkeypatch.setattr(tier_gate, "check_user", lambda u, uid, f: (True, "ok"))
        def names(uid):
            return {t.name for t in chat_tools.tools_for(users, uid, surface)}
        assert "patterns" in names(OP)
        assert "patterns" not in names(VIEWER)

    def test_the_command_hands_the_card_the_callers_id(self):
        from bot.skills.telegram_handler import TelegramHandler

        h = TelegramHandler.__new__(TelegramHandler)
        asked, handed, sent = [], [], []

        async def _guard(update, command, ctx=None):
            asked.append(command)
            return True

        async def _no_gate(*a, **k):
            return False

        async def _dispatch(name, engine, **kw):
            handed.append((name, kw))
            return "card"

        async def _send(update, text, **kw):
            sent.append(text)

        h._guard, h._token_gate_blocks, h._send = _guard, _no_gate, _send
        h.registry = NS(dispatch=_dispatch)
        h.engine = NS()
        update = NS(effective_user=NS(id=int(VIEWER)), effective_chat=NS(id=1),
                    message=NS(text="/patterns"))
        asyncio.run(h._cmd_patterns(update, NS(args=[])))
        assert asked == ["patterns"]
        assert handed == [("patterns", {"user_id": VIEWER})]
        assert sent == ["card"]
