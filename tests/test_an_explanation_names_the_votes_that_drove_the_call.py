"""`explain_trade` explains a pending idea by the votes that drove it, and claims nothing more.

Its one door, the MCP tool `runeclaw_explain`, said "Explain a pending or
historical trade idea" and "Omit to list pending ideas". The skill searched
the pending book only, so a historical idea was "not found", and an omitted
id answered "Trade  not found" too. Its "Signals:" line printed
`idea.signals_used`, which the analyzer fills with `list(indicators.keys())`:
`session_high`, `_top_votes` and every other key it computed, as if those
were the signals behind the call.

What these drive, through the real skill, the real `TradeIdea`, the flight
recorder's own ranking, the engine's own pending-book reading and the real
MCP adapter:

  * the card prints the voters that moved the score, strongest first, the
    same voters the sealed decision record names, and none of the indicator
    keys;
  * an idea with no breakdown, and one whose breakdown moved nothing, each
    say so in their own words;
  * `signals_used` itself is left as it was (other readers depend on it);
  * an omitted id lists the engine's own pending ideas, and leaves a
    person's ticket out while saying it did;
  * an id that is not pending says the card is for pending ideas only;
  * the MCP description claims pending only, and an omitted id through the
    adapter answers with the list.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.engine import RuneClawEngine
from bot.guardian.flight_recorder import compact_votes, decision_idea_payload
from bot.skills.skill_registry import ExplainTradeSkill
from bot.utils.models import Direction, TradeIdea

#: What `signals_used` really holds on an analyzer idea: indicator keys.
_KEYS = ["rsi", "session_high", "session_low", "_top_votes", "vwap", "regime"]

_VOTES = [("trend_ema", 1.0, 0.5), ("rsi_div", -0.5, 0.4), ("vwap_dist", 0.0, 1.0),
          ("obv", 0.3, 0.3), ("funding", -0.9, 0.9), ("mtf", 0.6, 0.2),
          ("smart_money", 0.2, 0.2), ("sweep", 0.1, 0.5)]


def _idea(iid="TI-1a2b3c4d", asset="SOL/USDT", votes=_VOTES, **kw) -> TradeIdea:
    idea = TradeIdea(id=iid, asset=asset, direction=Direction.LONG,
                     entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                     confidence=0.62, signals_used=list(_KEYS),
                     reasoning="[gpt-4o|TREND_UP|swing|momentum|C=0.68] "
                               "Trend and funding disagree; trend is stronger.",
                     **kw)
    if votes is not None:
        idea._confluence_votes = list(votes)
    return idea


class _Book:
    """The engine's pending book, with the engine's own reading bound."""
    pending_ideas = RuneClawEngine.pending_ideas
    _engine_pending_ids = RuneClawEngine._engine_pending_ids

    def __init__(self, engine_ideas=(), person_ideas=()):
        self._pending_ideas = {i.id: i for i in (*engine_ideas, *person_ideas)}
        self._engine_idea_ids = {i.id for i in engine_ideas}


def _explain(engine, **kw) -> str:
    return asyncio.run(ExplainTradeSkill().execute(engine, **kw))


# ── 1. the votes, not the indicator keys ──────────────────────────────────

class TestTheVotesThatDroveTheCall:
    def test_the_strongest_votes_print_and_no_indicator_key_does(self):
        out = _explain(_Book([_idea()]), trade_id="TI-1a2b3c4d")
        line = next(ln for ln in out.splitlines() if ln.startswith("- Votes"))
        assert ("funding -0.81 · trend_ema +0.50 · rsi_div -0.20 · "
                "mtf +0.12 · obv +0.09 · sweep +0.05") in line, line
        assert "+1 more" in line                  # smart_money, the seventh
        assert "vwap_dist" not in out, "a zero vote moved nothing"
        for key in ("session_high", "session_low", "_top_votes", "Signals:"):
            assert key not in out, key

    def test_the_card_names_the_voters_the_sealed_record_names(self):
        idea = _idea()
        out = _explain(_Book([idea]), trade_id=idea.id)
        sealed = [v["name"] for v in decision_idea_payload(idea)["votes"]][:6]
        line = next(ln for ln in out.splitlines() if ln.startswith("- Votes"))
        printed = [part.strip().split(" ")[0]
                   for part in line.split("<code>")[1].split("</code>")[0].split("·")]
        assert printed == sealed

    def test_an_idea_with_no_breakdown_says_none_was_recorded(self):
        out = _explain(_Book([_idea(votes=None)]), trade_id="TI-1a2b3c4d")
        assert "- Votes: <i>not recorded on this idea</i>" in out

    @pytest.mark.parametrize("votes", [[("vwap_dist", 0.0, 1.0)], []])
    def test_a_breakdown_that_moved_nothing_says_so(self, votes):
        out = _explain(_Book([_idea(votes=votes)]), trade_id="TI-1a2b3c4d")
        assert "- Votes: <i>recorded, and none moved the score</i>" in out
        assert "not recorded" not in out

    def test_signals_used_is_left_for_its_other_readers(self):
        idea = _idea()
        _explain(_Book([idea]), trade_id=idea.id)
        assert idea.signals_used == _KEYS
        assert compact_votes(idea._confluence_votes)[0]["name"] == "funding"

    def test_the_rationale_and_provenance_still_print(self):
        out = _explain(_Book([_idea()]), trade_id="TI-1a2b3c4d")
        assert "Trend and funding disagree" in out and "<blockquote>" in out
        assert "- Provenance: <code>gpt-4o|TREND_UP" in out


# ── 2. an omitted id lists, and a missing one says what the card is for ───

class TestTheListAndTheMiss:
    @pytest.mark.parametrize("given", [{}, {"trade_id": ""}, {"trade_id": "  "},
                                       {"trade_id": None}])
    def test_an_omitted_id_lists_the_engines_pending_ideas(self, given):
        mine = _idea("TI-engine01", "BTC/USDT")
        theirs = _idea("TI-person01", "ETH/USDT")
        out = _explain(_Book([mine], [theirs]), **given)
        assert "PENDING IDEAS" in out
        assert "TI-engine01" in out and "BTC/USDT" in out
        assert "TI-person01" not in out and "ETH/USDT" not in out
        assert "1 pending ticket(s) opened by a person are not listed" in out
        assert "not found" not in out.lower() and "No pending idea" not in out

    def test_an_empty_book_says_nothing_is_pending(self):
        out = _explain(_Book())
        assert "No idea of the engine's is pending right now." in out
        assert "opened by a person" not in out

    def test_a_book_whose_owner_split_cannot_be_read_lists_nothing(self):
        class _Unsplit(_Book):
            def _engine_pending_ids(self):
                raise RuntimeError("split unreadable")
        out = _explain(_Unsplit([_idea("TI-engine01")]))
        assert "Could not tell the engine's ideas from people's tickets (RuntimeError)" in out
        assert "TI-engine01" not in out

    def test_an_unreadable_pending_book_says_so(self):
        class _Broken:
            @property
            def pending_ideas(self):
                raise RuntimeError("book locked")
        out = _explain(_Broken(), trade_id="TI-x")
        assert "The pending ideas could not be read (RuntimeError)." in out
        assert "book locked" not in out

    def test_an_id_that_is_not_pending_says_the_card_is_for_pending_ideas(self):
        out = _explain(_Book([_idea()]), trade_id="TI-closed99")
        assert "No pending idea" in out and "TI-closed99" in out
        assert "Only an idea still pending is explained here" in out
        assert "/postmortem" in out

    def test_a_persons_ticket_is_still_explained_by_its_id(self):
        out = _explain(_Book([], [_idea("TI-person01")]), trade_id="TI-person01")
        assert "EXPLANATION" in out and "TI-person01" in out


# ── 3. the door says what the skill does ─────────────────────────────────

class TestTheMcpDoor:
    def test_the_description_claims_pending_only(self):
        from bot.mcp.server import TOOL_CATALOGUE
        tool = next(t for t in TOOL_CATALOGUE if t.mcp_name == "runeclaw_explain")
        assert "historical" not in tool.description.lower()
        assert "PENDING" in tool.description
        (param,) = tool.params
        assert "Omit to list the engine's own pending ideas" in param.description

    def test_an_omitted_id_through_the_adapter_answers_with_the_list(self, monkeypatch):
        import importlib

        import bot.mcp.server as mod
        monkeypatch.setenv("MCP_AUTH_TOKEN", "t" * 32)
        mod = importlib.reload(mod)
        from bot.skills.skill_registry import build_default_registry
        book = _Book([_idea("TI-engine01", "BTC/USDT")])
        srv = mod.RuneClawMCPServer(engine=book, registry=build_default_registry())
        listed = asyncio.run(srv.call_tool("runeclaw_explain", {}, auth_token="t" * 32))
        assert listed["status"] == "success", listed
        assert "TI-engine01" in listed["result"] and "not found" not in listed["result"]
        one = asyncio.run(srv.call_tool("runeclaw_explain", {"trade_id": "TI-engine01"},
                                        auth_token="t" * 32))
        assert "funding -0.81" in one["result"]
