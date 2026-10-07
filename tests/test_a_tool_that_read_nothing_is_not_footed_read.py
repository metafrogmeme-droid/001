"""A tool that timed out, was refused, or computed nothing is not footed "read".

`run_tool` returns (it does not raise) for every outcome but a raise, and
hands the model a reading line whose ``read_state`` says what happened. The
provider counted every return as a success, so the footer printed
"read: get_portfolio" under a reply that said the book could not be read, and
the same ``ok`` reached the web caption and the conversation store. The
reading line now decides the mark.

Driven end to end: the real `run_tool` as the executor of the real provider
loop, and the real footer.
"""
import asyncio
from types import SimpleNamespace

from bot.llm.provider import LLMConfig, LLMProvider, llm_complete_with_tools
from bot.nlp import chat_tools
from bot.nlp.grounding import tools_footer

TOOLS = [{"name": "get_portfolio", "description": "d",
          "parameters": {"type": "object", "properties": {}}}]


def _run(coro):
    return asyncio.run(coro)


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5


def _resp(content=None, calls=None):
    msg = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                           usage=_Usage())


class _Client:
    def __init__(self):
        self._r = [_resp(calls=[SimpleNamespace(
            id="c1", function=SimpleNamespace(name="get_portfolio", arguments="{}"))]),
            _resp(content="done")]
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        return self._r.pop(0)


class _Skill:
    def __init__(self, delay, answer="Equity 1,000 USDT, 0 open positions"):
        self.delay, self.answer = delay, answer

    async def execute(self, engine, user_id=None, **kw):
        await asyncio.sleep(self.delay)
        return self.answer


def _footer(skill, offered=("get_portfolio",), timeout=1.0):
    registry = SimpleNamespace(get=lambda n: skill if skill and n == "get_portfolio" else None)
    handler = SimpleNamespace(registry=registry, engine=None, conversations=None)

    async def executor(name, args):
        return await chat_tools.run_tool(handler, "u1", name, args, set(offered),
                                         timeout=timeout)

    events = []
    cfg = LLMConfig(provider=LLMProvider.GROK, api_key="k", model="grok-4.3", timeout_seconds=5.0)
    _run(llm_complete_with_tools(_Client(), cfg, "s", "q", TOOLS, executor, events_out=events))
    return events, tools_footer(events)


def test_a_tool_that_answered_is_footed_read():
    events, footer = _footer(_Skill(0.0))
    assert events[0]["ok"] is True
    assert footer == "read: get_portfolio"


def test_a_tool_that_timed_out_is_marked():
    events, footer = _footer(_Skill(3.0), timeout=1.0)
    assert "TIMED OUT" in events[0]["result"]
    assert events[0]["ok"] is False
    assert footer == "read: get_portfolio✗"


def test_a_tool_that_is_not_wired_up_is_marked():
    events, footer = _footer(None)
    assert "UNAVAILABLE" in events[0]["result"]
    assert footer == "read: get_portfolio✗"


def test_a_tool_that_returned_no_card_is_marked():
    events, footer = _footer(_Skill(0.0, answer=""))
    assert events[0]["ok"] is False
    assert footer == "read: get_portfolio✗"


def test_an_executor_answer_with_no_reading_line_is_a_return_and_nothing_more():
    # Not every executor is `run_tool`. One that hands back its own text has
    # no reading line, and a return is all it can say: it is footed read.
    events = []

    async def executor(name, args):
        return "Equity 1,000 USDT, 0 open positions"

    cfg = LLMConfig(provider=LLMProvider.GROK, api_key="k", model="grok-4.3", timeout_seconds=5.0)
    _run(llm_complete_with_tools(_Client(), cfg, "s", "q", TOOLS, executor, events_out=events))
    assert events[0]["ok"] is True
    assert tools_footer(events) == "read: get_portfolio"
