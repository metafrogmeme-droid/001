"""Two overlapping auto-confirm cycles for the SAME symbol must place ONE order.

Reported: a GRASS LONG setup was auto-confirmed twice and placed as two limit
orders (doubled exposure). The duplicate guard runs at analysis time, so two
concurrent cycles both clear it before either order lands (TOCTOU). confirm_trade
now serializes per symbol and re-checks live open/pending orders under the lock.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot.core.engine import RuneClawEngine


class _FakeExec:
    def __init__(self):
        self._pos = []

    @property
    def open_positions(self):
        return list(self._pos)


class _FakeEngine:
    confirm_trade = RuneClawEngine.confirm_trade  # exercise the real wrapper
    _drop_pending_idea = RuneClawEngine._drop_pending_idea  # the wrapper's own exit
    # The wrapper asks these before it reads a book. This stand-in has no
    # user store, so the real reading answers "not practice" and the live
    # check runs, unless a test gives it one.
    _self_admitted_practice = RuneClawEngine._self_admitted_practice
    confirm_is_practice = RuneClawEngine.confirm_is_practice
    _practice_book_symbols = RuneClawEngine._practice_book_symbols

    def __init__(self, ideas):
        self._pending_ideas = ideas
        self._pending_pyramid = {}
        self._pending_atr = {}
        self._symbol_entry_locks = {}
        self.live_executor = _FakeExec()
        self.inner_calls = []

    async def _confirm_trade_inner(self, trade_id, user_id=""):
        idea = self._pending_ideas.get(trade_id)
        self.inner_calls.append(trade_id)
        await asyncio.sleep(0)  # yield so a racing caller can interleave
        # Simulate the placed order landing as a pending_fill position.
        self.live_executor._pos.append(
            SimpleNamespace(symbol=idea.asset, status="pending_fill"))
        return f"LIMIT ORDER placed {idea.asset}"


def _idea(asset="GRASS/USDT"):
    return SimpleNamespace(asset=asset,
                           direction=SimpleNamespace(value="LONG"))


@pytest.fixture
def _live(monkeypatch):
    from bot.core import engine as eng_mod
    monkeypatch.setattr(eng_mod, "CONFIG", SimpleNamespace(
        is_live=lambda: True, paper_sim_opt_in_enabled=False))


def test_concurrent_same_symbol_places_one(_live):
    eng = _FakeEngine({"A": _idea(), "B": _idea()})

    async def run():
        return await asyncio.gather(eng.confirm_trade("A"),
                                    eng.confirm_trade("B"))

    results = asyncio.run(run())
    # Exactly one order placed; the other suppressed as a duplicate.
    assert len(eng.inner_calls) == 1
    skipped = [r for r in results if "duplicate suppressed" in r]
    assert len(skipped) == 1
    assert "already have an open/pending order" in skipped[0]
    assert len(eng.live_executor._pos) == 1


def test_different_symbols_both_place(_live):
    eng = _FakeEngine({"A": _idea("GRASS/USDT"), "B": _idea("ONDO/USDT")})

    async def run():
        return await asyncio.gather(eng.confirm_trade("A"),
                                    eng.confirm_trade("B"))

    asyncio.run(run())
    assert len(eng.inner_calls) == 2  # distinct symbols never collide


def test_flagged_pyramid_add_is_allowed(_live):
    eng = _FakeEngine({"A": _idea(), "B": _idea()})
    eng._pending_pyramid["B"] = True  # deliberate pyramid add
    asyncio.run(eng.confirm_trade("A"))
    asyncio.run(eng.confirm_trade("B"))
    # A places; B is a flagged pyramid so it is NOT suppressed by the guard.
    assert eng.inner_calls == ["A", "B"]


# ── a practice confirm reads the practice book it lands on ──────────────────


class _Store:
    """A user store holding one self-admitted practice account."""

    def __init__(self, practice_ids):
        self._ids = set(practice_ids)

    def get(self, user_id):
        from bot.core.practice_fill import SELF_ADMISSION_ROLE
        return {"role": SELF_ADMISSION_ROLE if user_id in self._ids else "trader"}

    def sim_opt_in(self, user_id):
        return False


class _Books:
    """`user_portfolios`: each caller's practice book, as the real get() hands it."""

    def __init__(self):
        self.books = {}

    def get(self, user_id):
        return self.books.setdefault(user_id, _FakeExec())


class _PracticeEngine(_FakeEngine):
    def __init__(self, ideas, practice_ids=("p1",)):
        super().__init__(ideas)
        self._user_store = _Store(practice_ids)
        self.user_portfolios = _Books()

    async def _confirm_trade_inner(self, trade_id, user_id=""):
        idea = self._pending_ideas.get(trade_id)
        self.inner_calls.append(trade_id)
        await asyncio.sleep(0)
        if self.confirm_is_practice(user_id):
            # The practice fill: a row on the caller's own practice book.
            self.user_portfolios.get(user_id)._pos.append(
                SimpleNamespace(asset=idea.asset, status="open"))
            return f"PAPER Simulated {idea.asset}"
        self.live_executor._pos.append(
            SimpleNamespace(symbol=idea.asset, status="pending_fill"))
        return f"LIMIT ORDER placed {idea.asset}"


@pytest.mark.parametrize("live", [True, False])
def test_a_second_practice_tap_on_one_symbol_is_suppressed(monkeypatch, live):
    from bot.core import engine as eng_mod
    monkeypatch.setattr(eng_mod, "CONFIG", SimpleNamespace(
        is_live=lambda: live, paper_sim_opt_in_enabled=False))
    eng = _PracticeEngine({"A": _idea(), "B": _idea("GRASS/USDT:USDT")})

    async def run():
        return await asyncio.gather(eng.confirm_trade("A", user_id="p1"),
                                    eng.confirm_trade("B", user_id="p1"))

    results = asyncio.run(run())
    assert len(eng.inner_calls) == 1
    skipped = [r for r in results if "duplicate suppressed" in r]
    assert len(skipped) == 1
    # It names the book it read: there is no order on a practice book.
    assert "already have an open practice position" in skipped[0]
    assert len(eng.user_portfolios.books["p1"]._pos) == 1


def test_a_practice_tap_on_another_symbol_opens(_live):
    eng = _PracticeEngine({"A": _idea("GRASS/USDT"), "B": _idea("ONDO/USDT")})
    asyncio.run(eng.confirm_trade("A", user_id="p1"))
    asyncio.run(eng.confirm_trade("B", user_id="p1"))
    assert eng.inner_calls == ["A", "B"]
    assert len(eng.user_portfolios.books["p1"]._pos) == 2


def test_the_operator_holding_the_symbol_is_not_the_practice_book(_live):
    eng = _PracticeEngine({"A": _idea()})
    eng.live_executor._pos.append(SimpleNamespace(symbol="GRASS/USDT", status="open"))
    asyncio.run(eng.confirm_trade("A", user_id="p1"))
    assert eng.inner_calls == ["A"]


def test_another_persons_practice_row_is_not_this_persons(_live):
    eng = _PracticeEngine({"A": _idea()}, practice_ids=("p1", "p2"))
    eng.user_portfolios.get("p2")._pos.append(
        SimpleNamespace(asset="GRASS/USDT", status="open"))
    asyncio.run(eng.confirm_trade("A", user_id="p1"))
    assert eng.inner_calls == ["A"]


def test_a_practice_opt_in_reads_the_practice_book_too(monkeypatch):
    """A practice-mode opt-in lands on the practice book as well
    (`confirm_is_practice`), so its duplicate is read there, not on a live one."""
    from bot.core import engine as eng_mod
    monkeypatch.setattr(eng_mod, "CONFIG", SimpleNamespace(
        is_live=lambda: True, paper_sim_opt_in_enabled=True))
    eng = _PracticeEngine({"A": _idea(), "B": _idea()}, practice_ids=())
    eng._user_store.sim_opt_in = lambda uid: uid == "o1"
    eng.live_executor._pos.append(SimpleNamespace(symbol="GRASS/USDT", status="open"))
    first = asyncio.run(eng.confirm_trade("A", user_id="o1"))
    second = asyncio.run(eng.confirm_trade("B", user_id="o1"))
    assert first.startswith("PAPER")
    assert "duplicate suppressed" in second
    assert eng.inner_calls == ["A"]


def test_an_unread_practice_book_is_left_to_the_fill_that_refuses_on_it(_live):
    eng = _PracticeEngine({"A": _idea()})

    class _Broken:
        def get(self, user_id):
            raise OSError("disk")

    eng.user_portfolios = _Broken()
    reached = []

    async def inner(trade_id, user_id=""):
        reached.append(trade_id)
        return "Trade REJECTED: practice book could not be read"

    eng._confirm_trade_inner = inner
    out = asyncio.run(eng.confirm_trade("A", user_id="p1"))
    assert reached == ["A"]
    assert out.startswith("Trade REJECTED")


def test_a_live_pyramid_flag_does_not_stack_a_practice_row(_live):
    """The flag is the live book's verdict; the practice book holds its own row."""
    eng = _PracticeEngine({"A": _idea(), "B": _idea()})
    asyncio.run(eng.confirm_trade("A", user_id="p1"))
    eng._pending_pyramid["B"] = True
    out = asyncio.run(eng.confirm_trade("B", user_id="p1"))
    assert "duplicate suppressed" in out
    assert len(eng.user_portfolios.books["p1"]._pos) == 1
