"""The back-stop re-runs the monitor. That is only safe if re-running is safe.

`_backstop_position_monitor` runs `_check_open_positions` again whenever
`_positions_monitored_tick` is false — which is exactly the partial-completion
case: the monitor was cancelled at its per-phase cap having acted on some
positions and not others.

The design depends on that re-run not repeating an action, and NOTHING SAID SO.
`_check_open_positions` re-reads `self.portfolio.open_positions` at its start,
so a position the first pass closed is simply not in the list the second time —
idempotency by state re-read rather than by bookkeeping. That is the right
mechanism, and it is invisible: a future refactor that hoists the read, caches
the list, or passes positions in as an argument would break the back-stop
silently, on the path that protects money already at risk.

Raised as F-5 of an external review of this session's writeup, which asked the
right question and could not answer it from prose: "if the monitor timed out
AFTER acting on some positions, does the back-stop's re-run repeat that
action?" It does not, and this is that fact written down.

F-4 from the same review — "is the back-stop bounded in duration?" — is
answered in the code and covered by tests/test_tick_phase_caps.py: the
back-stop's await goes through `_phase(..., fatal=False)`, which applies
`tick_phase_timeout_sec`. It is not re-tested here.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.engine import RuneClawEngine


class _Portfolio:
    """A book whose open_positions shrinks as they are closed."""

    def __init__(self, symbols):
        self._open = list(symbols)

    @property
    def open_positions(self):
        return list(self._open)

    def close(self, sym):
        if sym in self._open:
            self._open.remove(sym)


class _UserPortfolios:
    @staticmethod
    def all_portfolios():
        return {}


def _engine(portfolio, on_paper_check):
    """A bare engine with only what _check_open_positions touches."""
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.portfolio = portfolio
    eng.user_portfolios = _UserPortfolios()
    eng._check_paper_positions = on_paper_check
    eng._positions_monitored_tick = False
    return eng


@pytest.fixture(autouse=True)
def _paper_mode():
    """Paper mode: the live branch is skipped, leaving the shared path only.

    Patched on the TYPE, not the instance — CONFIG is a frozen dataclass, and
    the house pattern for this is `patch.object(type(CONFIG), ...)`.
    """
    from unittest.mock import patch

    from bot.core import engine as eng_mod
    with patch.object(type(eng_mod.CONFIG), "is_live", return_value=False):
        yield


def test_a_rerun_does_not_act_on_a_position_the_first_pass_closed():
    acted: list[list[str]] = []
    pf = _Portfolio(["BTC/USDT", "ETH/USDT"])

    async def paper_check(positions):
        acted.append([getattr(p, "symbol", p) for p in positions])
        # The partial-completion case: act on the first, then "time out".
        if positions:
            pf.close(positions[0])

    eng = _engine(pf, paper_check)
    asyncio.run(eng._check_open_positions())      # acts on BTC, closes it
    eng._positions_monitored_tick = False          # pretend the cap fired
    asyncio.run(eng._check_open_positions())      # the back-stop's re-run

    assert acted[0] == ["BTC/USDT", "ETH/USDT"]
    assert acted[1] == ["ETH/USDT"], (
        "the re-run was handed a position the first pass already closed — "
        "the back-stop would act on it twice"
    )
    assert "BTC/USDT" not in acted[1]


def test_the_position_list_is_read_fresh_on_every_call():
    """The mechanism itself, stated. If a refactor caches or hoists the read,
    idempotency goes with it and nothing else would notice."""
    seen: list[list[str]] = []
    pf = _Portfolio(["BTC/USDT"])

    async def paper_check(positions):
        seen.append(list(positions))

    eng = _engine(pf, paper_check)
    asyncio.run(eng._check_open_positions())
    pf.close("BTC/USDT")
    pf._open.append("SOL/USDT")
    asyncio.run(eng._check_open_positions())

    assert seen[0] == ["BTC/USDT"]
    assert seen[1] == ["SOL/USDT"], (
        "the second call saw a stale position list, so _check_open_positions "
        "is no longer reading the book fresh"
    )


def test_completing_sets_the_flag_the_backstop_reads():
    """The flag is set at the END — the only thing that tells a completed
    monitor from one cancelled at its cap."""
    pf = _Portfolio([])

    async def paper_check(_positions):
        return None

    eng = _engine(pf, paper_check)
    assert eng._positions_monitored_tick is False
    asyncio.run(eng._check_open_positions())
    assert eng._positions_monitored_tick is True, (
        "a completed monitor did not record that it ran, so the back-stop "
        "will run it a second time every tick"
    )


def test_an_empty_book_still_counts_as_monitored():
    """Nothing to watch is a completed check, not a skipped one — otherwise
    the back-stop fires on every flat tick and audits INCOMPLETE forever."""
    pf = _Portfolio([])
    calls = []

    async def paper_check(positions):
        calls.append(positions)

    eng = _engine(pf, paper_check)
    asyncio.run(eng._check_open_positions())
    assert eng._positions_monitored_tick is True
    assert calls == [], "the paper check ran with no open positions anywhere"


def test_a_monitor_that_did_not_finish_does_not_claim_it_did():
    """The flag must be set at the END, and only reaching the end proves it.

    This is the property the whole back-stop rests on: `_check_open_positions`
    runs under a per-phase cap, so a call cancelled partway monitored some
    positions and not others. If the flag were set at the START, that call
    would look identical to a completed one, the back-stop would skip its
    re-run, and the positions it never reached would go unwatched for the tick
    — silently, on the guard that exists because the stops were missed once
    already.

    Mutation-tested: moving the assignment to the top of the method passes
    every other assertion in this file.
    """
    pf = _Portfolio(["BTC/USDT"])

    async def paper_check(_positions):
        raise asyncio.CancelledError()      # the cap firing mid-monitor

    eng = _engine(pf, paper_check)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eng._check_open_positions())
    assert eng._positions_monitored_tick is False, (
        "a monitor cancelled partway recorded that it ran, so the back-stop "
        "will skip the re-run and the unreached positions stay unwatched"
    )


def test_a_monitor_that_raised_does_not_claim_it_did():
    """Same property, ordinary exception rather than cancellation."""
    pf = _Portfolio(["BTC/USDT"])

    async def paper_check(_positions):
        raise RuntimeError("venue read failed")

    eng = _engine(pf, paper_check)
    with pytest.raises(RuntimeError):
        asyncio.run(eng._check_open_positions())
    assert eng._positions_monitored_tick is False
