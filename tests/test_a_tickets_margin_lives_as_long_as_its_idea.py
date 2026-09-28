"""A manual ticket's margin lives exactly as long as its idea.

`_confirm_trade_inner` POPPED the ticket's margin off `_manual_margin_override`
on its way to the executor, and `execute` then refused (the venue minimum, a
cap, a leverage the venue would not set, an outcome it confirmed neither way).
C-05 keeps a refused idea pending so the person can retry -- and the retry ran
with no margin on record, sized by the risk engine. Driven through the real
`confirm_trade` on the unfixed tree:

    attempt 1 (venue refuses)  size handed $50.0   idea pending, margin GONE
    attempt 2 (the retry)      size handed $100.0  the risk engine's figure

On the web-live path the retry's envelope authorization then read `margin
None` -> "notional not stated" -> denied under any cap: fail-closed, and a
sentence about a figure the person had typed. On Telegram the retry placed
double the ticket. The margin is READ where the executor is sized now, and it
leaves the book with the idea, through the one helper every exit takes
(`_drop_pending_idea`): a placement, a rejection, a TTL expiry, a duplicate
suppression, a practice fill, the force-scan sweep, the engine's own dedup
and the kill-switch clear.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LivePosition
from tests.source_scan import code_only
from tests.test_a_refused_web_live_confirm_takes_its_spend_back import (
    UID,
)
from tests.test_a_refused_web_live_confirm_takes_its_spend_back import (
    envelope as _envelope_fixture,
)
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    FILLED,
    _confirm,
    _engine,
)
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _no_website_sync as _seal_no_website_sync,
)
from tests.test_an_unverified_submission_is_neither_a_fill_nor_a_failure import UNVERIFIED_CARD

envelope = _envelope_fixture  # the sibling suite's fixture, under its own name
# A fill syncs the portfolio to the website on a background thread, which
# reaches the network; the seal suite's autouse stub is re-registered here,
# because autouse binds on the fixture and a fixture is per module.
_no_website_sync = _seal_no_website_sync

REFUSED = "⚠️ EXECUTION FAILED: Bitget requires >= $60.00 notional"
MARGIN = 50.0


def _ticket(tmp_path, margin=MARGIN):
    engine, idea = _engine(tmp_path)
    engine._manual_margin_override = {idea.id: margin}
    return engine, idea


class _Executor:
    """`execute` answers a planted sequence and records the size it was handed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sizes: list = []

    async def __call__(self, idea, size_usd=None, **kw):
        self.sizes.append(size_usd)
        return self.answers.pop(0)


# ── the confirm path ─────────────────────────────────────────────────────────


class TestARefusalKeepsTheMarginWithTheIdea:

    def test_the_retry_after_a_venue_refusal_is_the_ticket_the_person_typed(self, tmp_path):
        engine, idea = _ticket(tmp_path)
        ex = _Executor([REFUSED, FILLED])
        engine.live_executor.execute = ex
        first = _confirm(engine, idea)
        assert first.startswith(REFUSED)
        assert idea.id in engine._pending_ideas, "C-05: a refused idea stays for the retry"
        assert engine._manual_margin_override == {idea.id: MARGIN}, "and so does its margin"
        second = _confirm(engine, idea)
        assert second.startswith(FILLED)
        assert ex.sizes == [MARGIN, MARGIN], "both attempts were sized at the ticket's margin"

    def test_a_placement_takes_the_margin_with_the_idea(self, tmp_path):
        engine, idea = _ticket(tmp_path)
        engine.live_executor.execute = _Executor([FILLED])
        assert _confirm(engine, idea).startswith(FILLED)
        assert idea.id not in engine._pending_ideas
        assert engine._manual_margin_override == {}

    def test_an_unverified_submission_keeps_both(self, tmp_path):
        """The venue may hold that order; the idea stays pending (its own
        chapter) and the margin stays with it, so a retry the reconcile has
        not yet ruled on is still the ticket's own size."""
        engine, idea = _ticket(tmp_path)
        engine.live_executor.execute = _Executor([UNVERIFIED_CARD])
        _confirm(engine, idea)
        assert idea.id in engine._pending_ideas
        assert engine._manual_margin_override == {idea.id: MARGIN}

    def test_the_web_envelope_reads_the_typed_notional_on_the_retry(self, tmp_path, envelope):
        """The other surface: after a refusal, `_authorize_web_live_trade`
        still finds the margin, so the retry is authorized on the notional the
        person typed and not denied as "not stated"."""
        from bot.web import user_gateway as ug
        engine, idea = _ticket(tmp_path)
        engine.live_executor.execute = _Executor([REFUSED])
        engine._pending_ideas[idea.id] = idea.model_copy(update={"asset": "SOL/USDT"})
        _confirm(engine, engine._pending_ideas[idea.id])
        assert idea.id in engine._pending_ideas
        engine._executor_for = lambda tg, venue=None: SimpleNamespace(
            _compute_target_leverage=lambda symbol, i=None: 5)
        auth = ug._authorize_web_live_trade({}, engine, UID, idea.id)
        assert auth.ok is True and auth.recorded is True, auth
        assert envelope.ledger.spent(UID, __import__("time").time()) == MARGIN * 5
        # And the authorization READS the margin: asked again for the same
        # pending ticket it still finds the figure (the ledger dedupes the ref).
        again = ug._authorize_web_live_trade({}, engine, UID, idea.id)
        assert again.ok is True and again.recorded is False, again
        assert engine._manual_margin_override == {idea.id: MARGIN}


class TestEveryOtherExitTakesTheMarginToo:

    def test_a_rejection(self, tmp_path):
        engine, idea = _ticket(tmp_path)
        engine.reject_trade(idea.id)
        assert idea.id not in engine._pending_ideas
        assert engine._manual_margin_override == {}

    def test_an_expiry(self, tmp_path):
        engine, idea = _ticket(tmp_path)
        engine._pending_ideas[idea.id] = idea.model_copy(
            update={"timestamp": datetime.now(UTC) - timedelta(seconds=10_000)})
        fresh = idea.model_copy(update={"id": "TI-FRESH"})
        engine._pending_ideas[fresh.id] = fresh
        engine._manual_margin_override[fresh.id] = 20.0
        assert engine._expire_pending_ideas() == [idea.id]
        assert idea.id not in engine._pending_ideas and idea.id not in engine._pending_atr
        assert engine._manual_margin_override == {fresh.id: 20.0}, "only the expired one"

    def test_a_duplicate_suppression(self, tmp_path):
        """The confirm wrapper drops an idea whose symbol the book already
        holds, before the inner path runs."""
        engine, idea = _ticket(tmp_path)
        engine.live_executor._positions = {"P1": LivePosition(
            trade_id="P1", symbol="BTC/USDT", direction="LONG", entry_price=64000.0,
            quantity=0.001, cost_usd=12.8, stop_loss=62000.0, take_profit=66000.0,
            leverage=5, is_spot=False, status="open")}
        inner = AsyncMock()
        engine._confirm_trade_inner = inner
        msg = _confirm(engine, idea)
        assert "Duplicate entry suppressed" in msg or "already" in msg, msg
        inner.assert_not_awaited()
        assert idea.id not in engine._pending_ideas
        assert engine._manual_margin_override == {}

    def test_the_helper_answers_the_idea_it_dropped_and_none_for_a_stranger(self, tmp_path):
        engine, idea = _ticket(tmp_path)
        assert engine._drop_pending_idea("TI-nobody") is None
        assert engine._manual_margin_override == {idea.id: MARGIN}
        assert engine._drop_pending_idea(idea.id) is idea
        assert engine._manual_margin_override == {}

    def test_an_engine_that_never_held_a_manual_ticket_drops_cleanly(self, tmp_path):
        """`register_manual_idea` creates the map lazily; an engine that has
        never seen a manual ticket has none, and the helper must not mind."""
        engine, idea = _engine(tmp_path)
        assert not hasattr(engine, "_manual_margin_override") or isinstance(
            engine._manual_margin_override, dict)
        if hasattr(engine, "_manual_margin_override"):
            del engine._manual_margin_override
        assert engine._drop_pending_idea(idea.id) is idea


# ── the class, as a rule over the module ─────────────────────────────────────


def test_every_exit_from_the_book_goes_through_the_helper():
    """No method but `_drop_pending_idea` pops `_pending_ideas` -- the idea
    added to the book tomorrow must not be popped somewhere the margin is
    forgotten. Read as an AST over the class, comments and docstrings blanked
    (this test's own subject is spelled in them)."""
    import textwrap
    src = code_only(textwrap.dedent(inspect.getsource(RuneClawEngine)))
    tree = ast.parse(src)
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "pop"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "_pending_ideas"
                    and fn.name != "_drop_pending_idea"):
                offenders.append(f"{fn.name}:{node.lineno}")
    assert offenders == [], offenders


def test_the_kill_switch_clear_takes_the_margins_too():
    """The one `.clear()` of the book, and the margin map cleared beside it --
    a scan, stated as one: `emergency_halt_all` halts every risk engine and
    flattens the live book, which a unit fixture cannot stand up honestly."""
    src = code_only(inspect.getsource(RuneClawEngine.emergency_halt_all))
    i = src.index("self._pending_ideas.clear()")
    window = src[i:i + 400]
    assert "self._manual_margin_override.clear()" in window
    assert src.count("_pending_ideas.clear()") == 1


def test_the_tick_expires_through_the_seam():
    src = code_only(inspect.getsource(RuneClawEngine._tick))
    assert "self._expire_pending_ideas()" in src
    assert "_pending_ideas.pop" not in src


def test_the_executor_is_handed_a_margin_that_is_read_not_popped():
    src = code_only(inspect.getsource(RuneClawEngine._confirm_trade_inner))
    assert "manual_margin = self._manual_margin_override[idea.id]" in src
    assert "_manual_margin_override.pop" not in src
