"""A confirm that placed nothing is never "✅ executed", and never public.

`confirm_trade` answers with a sentence, and the Confirm button decided from a
private prefix list whether to say "✅ Trade executed" and post the idea to the
public channels as a TRADE OPENED. The list knew "Trade REJECTED" and missed
every refusal written without it, so each of these was announced as a trade
and posted publicly under the RUNECLAW name:

* the person's chosen strategy refusing the idea (🛡),
* the duplicate skip (⏭️ ... already have an open/pending order),
* "⛔ Paper trading is disabled on this bot",
* the practice fill's cooldown (⏸) and its failure.

And the post was not limited to the agent's own book at all: a person's trade
on their OWN account, and a practice fill, were posted the same way, the
second labelled LIVE whenever the person held live authority. The post is now
made when the operator's executor holds the trade, which is measured.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest

import bot.config as bot_config
from bot.core.confirm_result import REFUSAL_PREFIXES, held_on_operator_book, placed_nothing
from bot.core.engine import RuneClawEngine
from bot.skills.manual_trade import build_manual_idea
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS

UID = "424242"

REFUSALS = [
    "\U0001f6e1 Your chosen strategy 'safe' only trades BTC and ETH.",
    "⏭️ Skipped BTC/USDT: already have an open/pending order for it "
    "(duplicate suppressed).",
    "⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY.",
    "⏸ [PAPER] Practice cooldown: 90s left after a losing close.",
    "⚠️ [PAPER] Simulated fill failed: boom",
    "Trade REJECTED: engine halted (kill-switch) before execution.",
    "Trade not found or expired.",
]


async def _noop(*a, **k):
    return None


class _Forwarder:
    def __init__(self):
        self.posts = []

    async def post_trade_opened(self, idea, mode="PAPER"):
        self.posts.append((idea, mode))


def _tap(result, *, lands_on_operator_book=False):
    """One owner-tagged Confirm tap through the REAL dispatcher.

    `confirm_trade` is planted: it pops the idea as the real one does, and
    when told to, puts the position on the operator's executor the way
    `LiveExecutor.execute` keys it (by the idea's id).
    """
    replies: list = []
    forwarder = _Forwarder()
    idea = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)
    op = SimpleNamespace(_positions={})
    engine = SimpleNamespace(live_executor=op, _pending_ideas={idea.id: idea})

    async def _confirm(trade_id, user_id=""):
        engine._pending_ideas.pop(trade_id, None)
        if lands_on_operator_book:
            op._positions[trade_id] = SimpleNamespace(status="open")
        return result

    engine.confirm_trade = _confirm

    class _Users:
        def get(self, tid):
            return {"role": "trader", "authorized": True}

        def has_permission(self, tid, perm):
            return perm in ROLE_PERMISSIONS["trader"]

        def is_authorized(self, *a, **k):
            return True

        def is_admitted(self, *a, **k):
            return True

        def permission_denial(self, *a, **k):
            return None

        def get_tier(self, *a, **k):
            return "free"

        def register(self, *a, **k):
            return None

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    h.forwarder = forwarder
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h._check_auth = lambda update: True
    h._is_admin = lambda update: False
    h._can_trade_live = lambda tg_id: True
    h._lang = lambda update: "en"

    async def _send(update, text, **kw):
        replies.append(text)

    h._send = _send
    query = SimpleNamespace(
        data=f"confirm:{idea.id}:{UID}",
        message=SimpleNamespace(edit_reply_markup=_noop, chat_id=int(UID)),
        answer=_noop,
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=int(UID), first_name="X"),
        effective_chat=SimpleNamespace(id=int(UID)),
    )
    orig = type(bot_config.CONFIG).is_live
    type(bot_config.CONFIG).is_live = lambda self: True
    try:
        asyncio.new_event_loop().run_until_complete(
            h._handle_callback(update, SimpleNamespace()))
    finally:
        type(bot_config.CONFIG).is_live = orig
    return replies, forwarder.posts, idea


@pytest.mark.parametrize("result", REFUSALS, ids=lambda r: r[:14])
def test_a_refusal_is_not_announced_or_posted(result):
    replies, posts, _ = _tap(result)
    assert replies and replies[-1].startswith("❌"), replies
    assert posts == [], "a refusal was posted publicly as a TRADE OPENED"


def test_a_fill_on_the_operators_book_is_announced_and_posted():
    replies, posts, idea = _tap("✅ LIVE LONG BTC/USDT filled", lands_on_operator_book=True)
    assert replies[-1].startswith("✅")
    assert [(p[0].id, p[1]) for p in posts] == [(idea.id, "LIVE")], (
        "the post names the idea this button confirmed, read before the confirm "
        "popped it")


def test_a_fill_on_a_persons_own_book_is_announced_and_not_posted():
    """The person's trade executed, so they are told; the public channels
    carry the agent's book, which does not hold it."""
    replies, posts, _ = _tap("✅ LIVE LONG BTC/USDT filled")
    assert replies[-1].startswith("✅")
    assert posts == []


def test_a_practice_fill_is_not_posted():
    replies, posts, _ = _tap(
        "\U0001f4dd <b>[PAPER]</b> Simulated LONG <b>BTC/USDT</b>")
    assert replies[-1].startswith("✅")
    assert posts == [], "a practice fill was posted publicly (as LIVE, for a live-enabled person)"


# ── the operator-book reading ──────────────────────────────────────────────


@pytest.mark.parametrize("status, held", [
    ("open", True), ("pending_fill", True), ("closing", False), ("closed", False)])
def test_the_operator_book_holds_only_what_is_open_or_resting(status, held):
    engine = SimpleNamespace(live_executor=SimpleNamespace(
        _positions={"T": SimpleNamespace(status=status)}))
    assert held_on_operator_book(engine, "T") is held


def test_no_operator_executor_holds_nothing():
    assert held_on_operator_book(SimpleNamespace(live_executor=None), "T") is False
    assert held_on_operator_book(SimpleNamespace(), "T") is False


# ── every answer confirm_trade can give is read correctly ─────────────────


def _render(node):
    """The literal text of a returned string, with each interpolation as X."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(p.value if isinstance(p, ast.Constant) else "X"
                       for p in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _render(node.left)
        return None if left is None else left + (_render(node.right) or "X")
    return None


# Answers that are not a literal, each with why it is not read here.
PASSED_THROUGH = {
    "result + seal_note": (
        "the executor's own answer, read by execution_indicates_failure, with "
        "the note that its audit record could not be sealed appended (empty "
        "when it sealed); the note is driven both ways in "
        "test_a_seal_failure_does_not_unplace_a_trade.py"),
    "await self._confirm_trade_inner(trade_id, user_id)": "the inner path, walked below",
    "await self._simulate_paper_fill(idea, recheck, user_id, trade_id)":
        "the practice fill, walked below",
}
# Literal answers that DID place something, each with what it placed.
PLACED = {
    "\U0001f4dd <b>[PAPER]</b> Simulated": "a practice position in the person's own paper book",
}


def _answers():
    out = []
    for fn in (RuneClawEngine.confirm_trade, RuneClawEngine._confirm_trade_inner,
               RuneClawEngine._simulate_paper_fill):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                out.append((fn.__name__, node.value))
    return out


def test_every_literal_answer_is_read_correctly():
    seen_passed = set()
    for fn, node in _answers():
        text = _render(node)
        if text is None:
            key = ast.unparse(node)
            assert key in PASSED_THROUGH, f"{fn}: an answer nobody classified: {key}"
            seen_passed.add(key)
            continue
        placed = any(text.startswith(p) for p in PLACED)
        assert placed_nothing(text) is not placed, f"{fn}: {text[:90]!r}"
    assert seen_passed == set(PASSED_THROUGH), (
        "a pass-through row stopped matching any answer: delete it")


def test_the_executors_answers_are_read_both_ways():
    """The executor's answer reaches the button verbatim. Its successes must
    not begin with a refusal prefix, and its refusals are the executor's own
    vocabulary, which the reading must still ask."""
    from bot.core.live_executor import LiveExecutor
    tree = ast.parse(textwrap.dedent(inspect.getsource(LiveExecutor.execute)))
    texts = [t for n in ast.walk(tree)
             if isinstance(n, ast.Return) and n.value is not None
             for t in [_render(n.value)] if t]
    placed = [t for t in texts if "opened" in t or "LIMIT ORDER" in t]
    refused = [t for t in texts if t not in placed]
    assert placed and refused, "the premise: execute() answers both ways"
    for text in placed:
        assert not placed_nothing(text), text
    for text in refused:
        assert placed_nothing(text), text


def test_a_non_string_answer_is_nothing_placed():
    assert placed_nothing(None) is True


def test_the_prefixes_are_exact():
    """The old list lower-cased the answer for a "Trade rejected" spelling;
    no answer in the tree is spelled that way, so the reading does not claim
    to handle one."""
    assert "Trade REJECTED" in REFUSAL_PREFIXES
    assert placed_nothing("\u2705 LIVE LONG BTC/USDT filled") is False


# ── the scan card's confirm reads the same way ────────────────────────────
#
# The scan card's ✅ is `confirm:<id>` on the card's own registered idea now,
# so its answer is read by the dispatcher above (`_tap`). An OLD card's
# `scan_confirm:` payload is refused before anything is asked of
# `confirm_trade`, so whatever confirm_trade would have answered, it is never
# announced as executed.


@pytest.mark.asyncio
@pytest.mark.parametrize("result", REFUSALS[:4], ids=lambda r: r[:14])
async def test_an_old_scan_confirm_does_not_say_executed(result, monkeypatch):
    from bot.skills import scan_skill
    from tests.test_every_confirm_trade_door_is_gated import _handler_stub, _scan_confirm_fixture, _sent

    update, context, engine, query = _scan_confirm_fixture(result)
    context.bot_data["telegram_handler"] = _handler_stub(admin=True, live_ok=True)
    monkeypatch.setattr(type(bot_config.CONFIG), "is_live", lambda self: True)
    await scan_skill.callback_confirm_reject(update, context)
    out = _sent(query)
    assert engine.confirm_trade.await_count == 0
    assert "nothing was placed" in out and "EXECUTED" not in out, out
