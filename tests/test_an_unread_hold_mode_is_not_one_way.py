"""A hold-mode probe that failed leaves the mode UNKNOWN, and both loops ask.

`LiveExecutor._hedge_mode` decides three things on the exit path: the plan
cleanup before a re-place sweeps BOTH sides' stops when the account is
one-way (`plan_rows_to_cancel`), the close-side fill reader prices an
unmarked fill as a close when it is one-way (`_fill_by_close_side`), and
reconcile reads the book by side when it is hedge. Driven on the base tree,
`_detect_hold_mode` answered every failure -- the v2 probe raising anything
but 40085, the v3 probe raising, a v3 answer with a non-00000 code -- with
`_hedge_mode = False`, cached for the life of the process: a network blip on
the first order after a restart labelled a hedge account one-way for good,
and the two readers above acted on a reading nobody made. And the probe ran
only inside `_ensure_leverage`, i.e. on the first ORDER, so a restarted bot
holding positions and placing none (halted, paused, refused) monitored and
reconciled them on an unread mode until something traded.

A failed probe leaves `None` now -- the value every reader already handles
in the cheaper direction -- says so ONCE at WARNING with the exception's
CLASS and never its text, audits it, counts it on the warning-rate feed, and
both loops ask at the top of every pass while it is unknown.
"""
import ast
import asyncio
import inspect
import logging
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.core.live_executor as le
from bot.core import bitget_v3_client as v3mod
from bot.core.live_executor import LiveExecutor
from bot.core.venues import get_venue
from tests.test_an_unread_entry_is_an_unpriced_close import (
    _executor as _close_executor,
)
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _no_sleep,  # a fixture, taken by pytest.mark.usefixtures
    _pos100,
)

SECRET = "sign=SECRETVALUE9"


def _ex(monkeypatch, *, v2=None, v3=None, venue="bitget"):
    """A bare executor whose v2 probe answers `v2` (an AsyncMock) and whose
    v3 probe calls `v3()` (returns the settings dict, or raises)."""
    ex = LiveExecutor()
    ex._venue = get_venue(venue)
    ex._save_positions = MagicMock()
    ex._record_warning = MagicMock()
    x = AsyncMock()
    if v2 is not None:
        x.privateMixGetV2MixAccountAccount = v2
    ex._get_exchange = AsyncMock(return_value=x)
    audits = []
    monkeypatch.setattr(le, "audit", lambda *a, **k: audits.append((a, k)))
    if v3 is not None:
        class _V3:
            def request(self, *a, **k):
                return v3()
        monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                            classmethod(lambda cls, creds: _V3()))
    return ex, x, audits


def _warnings(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and "hold mode" in r.getMessage().lower()]


def _raise(exc):
    def _f(*a, **k):
        raise exc
    return _f


# ── the probe ──────────────────────────────────────────────────────────────

def test_a_v2_probe_that_raises_leaves_the_mode_unknown_and_says_so_once(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="bot.core.live_executor")
    ex, x, audits = _ex(monkeypatch, v2=AsyncMock(side_effect=RuntimeError(SECRET)))
    asyncio.run(ex._detect_hold_mode())
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is None, "a failed probe was read as one-way"
    assert ex._is_uta is None
    assert x.privateMixGetV2MixAccountAccount.await_count == 2, "the probe is asked again"
    ws = _warnings(caplog)
    assert len(ws) == 1, [w.getMessage() for w in ws]
    assert "UNKNOWN" in ws[0].getMessage() and "bitget" in ws[0].getMessage()
    assert "RuntimeError" in ws[0].getMessage()
    assert all("SECRETVALUE" not in r.getMessage() for r in caplog.records), \
        "the venue's own text reached the operator log"
    assert len(audits) == 1
    _a, kw = audits[0]
    assert kw["action"] == "hold_mode" and kw["result"] == "UNREAD"
    assert kw["data"] == {"venue": "bitget", "detail": "RuntimeError"}
    assert "SECRETVALUE" not in str(_a) + str(kw)
    assert ex._record_warning.call_args_list[0].args == ("hold_mode_unread",)
    assert ex._record_warning.call_count == 1, (
        "a retry inside one unread streak counted as a new fault on the breaker feed")
    assert f"{le.HOLD_MODE_RETRY_S:.0f}s" in ws[0].getMessage()


def test_a_uta_account_whose_v3_probe_raises_is_unknown_and_uta(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    ex, _x, audits = _ex(monkeypatch, v2=AsyncMock(side_effect=Exception("40085 not unified")),
                         v3=_raise(ConnectionError(SECRET)))
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is None and ex._is_uta is True
    assert audits[0][1]["data"]["detail"] == "ConnectionError"
    assert "SECRETVALUE" not in "".join(r.getMessage() for r in caplog.records)


def test_a_v3_answer_that_is_not_ok_is_unknown(monkeypatch):
    ex, _x, audits = _ex(monkeypatch, v2=AsyncMock(side_effect=Exception("40085")),
                         v3=lambda: {"code": "40001", "msg": SECRET})
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is None
    assert audits[0][1]["result"] == "UNREAD"
    assert "SECRETVALUE" not in str(audits)


@pytest.mark.parametrize("v2,v3,expected", [
    (AsyncMock(return_value={"data": {"holdMode": "double_hold"}}), None, True),
    (AsyncMock(return_value={"data": [{"holdMode": "single_hold"}]}), None, False),
    (AsyncMock(side_effect=Exception("40085")),
     lambda: {"code": "00000", "data": {"holdMode": "hedge_mode"}}, True),
    (AsyncMock(side_effect=Exception("40085")),
     lambda: {"code": "00000", "data": {"holdMode": "one_way_mode"}}, False),
])
def test_a_probe_that_answers_is_a_reading(monkeypatch, v2, v3, expected, caplog):
    caplog.set_level(logging.WARNING)
    ex, _x, audits = _ex(monkeypatch, v2=v2, v3=v3)
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is expected
    assert not audits and not _warnings(caplog)


def test_a_venue_without_hedge_mode_is_one_way_without_a_probe(monkeypatch):
    ex, x, audits = _ex(monkeypatch, venue="hyperliquid")
    asyncio.run(ex._probe_hold_mode_if_unknown())
    assert ex._hedge_mode is False and ex._is_uta is False
    assert ex._get_exchange.await_count == 0, "nothing to ask a one-way venue"
    assert not audits


def test_a_probe_that_answers_ends_the_streak_so_a_later_failure_is_said_again(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    v2 = AsyncMock(side_effect=[RuntimeError("blip"),
                                {"data": {"holdMode": "double_hold"}},
                                RuntimeError("blip again")])
    ex, _x, audits = _ex(monkeypatch, v2=v2)
    asyncio.run(ex._detect_hold_mode())
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is True
    ex._hedge_mode = None                       # as a later reader would re-ask
    asyncio.run(ex._detect_hold_mode())
    assert ex._hedge_mode is None
    assert len(_warnings(caplog)) == 2 and len(audits) == 2
    assert ex._record_warning.call_count == 2, "two streaks are two faults"


# ── the loop probe ─────────────────────────────────────────────────────────

def test_the_loop_probe_retries_after_the_interval_not_every_pass(monkeypatch):
    ex, x, _a = _ex(monkeypatch, v2=AsyncMock(side_effect=RuntimeError("down")))
    clock = {"t": 1_000_000.0}                  # far from monotonic's near-zero start
    monkeypatch.setattr(le.time, "monotonic", lambda: clock["t"])
    for _ in range(3):                          # three passes inside the interval
        asyncio.run(ex._probe_hold_mode_if_unknown())
        clock["t"] += 60.0
    assert x.privateMixGetV2MixAccountAccount.await_count == 1, "the loops probed every pass"
    clock["t"] += le.HOLD_MODE_RETRY_S           # past the interval: asked again
    asyncio.run(ex._probe_hold_mode_if_unknown())
    assert x.privateMixGetV2MixAccountAccount.await_count == 2
    assert ex._hedge_mode is None


def test_a_probe_that_raises_out_of_the_exchange_is_spaced_too(monkeypatch):
    ex, _x, _a = _ex(monkeypatch)
    ex._get_exchange = AsyncMock(side_effect=RuntimeError("down"))
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(le.time, "monotonic", lambda: clock["t"])
    asyncio.run(ex._probe_hold_mode_if_unknown())
    clock["t"] += 60.0
    asyncio.run(ex._probe_hold_mode_if_unknown())
    assert ex._get_exchange.await_count == 1, "the stamp is taken before the probe, or a raise is retried every pass"


def test_the_order_path_is_not_spaced(monkeypatch):
    """`_ensure_leverage` asks whenever the mode is unknown: an order is the
    one moment the answer changes what is sent."""
    src = inspect.getsource(LiveExecutor._ensure_leverage)
    assert "if self._hedge_mode is None:\n            await self._detect_hold_mode()" in src



def test_the_loop_probe_asks_only_while_the_mode_is_unknown(monkeypatch):
    ex, _x, _a = _ex(monkeypatch)
    ex._detect_hold_mode = AsyncMock()
    for known in (True, False):
        ex._hedge_mode = known
        asyncio.run(ex._probe_hold_mode_if_unknown())
    assert ex._detect_hold_mode.await_count == 0
    ex._hedge_mode = None
    asyncio.run(ex._probe_hold_mode_if_unknown())
    assert ex._detect_hold_mode.await_count == 1


def test_a_probe_raising_out_of_the_exchange_is_a_failed_read_not_a_crash(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    ex, _x, audits = _ex(monkeypatch)
    ex._get_exchange = AsyncMock(side_effect=RuntimeError(SECRET))
    asyncio.run(ex._probe_hold_mode_if_unknown())
    assert ex._hedge_mode is None
    assert audits and audits[0][1]["data"]["detail"] == "RuntimeError"
    assert "SECRETVALUE" not in "".join(r.getMessage() for r in caplog.records)


# ── both loops ask, and go on when the answer does not come ────────────────

def _present_row():
    return {"symbol": "XYZ/USDT:USDT", "contracts": 10, "side": "long",
            "info": {"holdSide": "long"}}


@pytest.mark.usefixtures("_no_sleep")
def test_reconcile_asks_the_venue_before_reading_the_book(monkeypatch):
    ex, x = _close_executor(venue_rows=[_present_row()])
    ex._hedge_mode = None
    monkeypatch.setattr(le, "audit", MagicMock())
    x.privateMixGetV2MixAccountAccount = AsyncMock(
        return_value={"data": {"holdMode": "double_hold"}})
    p = _pos100()
    ex._positions = {p.trade_id: p}
    asyncio.run(ex.reconcile_positions())
    assert ex._hedge_mode is True, "reconcile read the book on an unread mode"
    assert p.status == "open"


@pytest.mark.usefixtures("_no_sleep")
def test_reconcile_still_runs_when_the_probe_fails(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    ex, x = _close_executor(venue_rows=[_present_row()])
    ex._hedge_mode = None
    monkeypatch.setattr(le, "audit", MagicMock())
    x.privateMixGetV2MixAccountAccount = AsyncMock(side_effect=RuntimeError(SECRET))
    p = _pos100()
    ex._positions = {p.trade_id: p}
    asyncio.run(ex.reconcile_positions())
    assert ex._hedge_mode is None
    assert p.status == "open", "a failed probe must not decide the book"
    assert x.fetch_positions.await_count >= 1, "the pass went on"
    assert len(_warnings(caplog)) == 1


@pytest.mark.usefixtures("_no_sleep")
def test_the_monitor_asks_too(monkeypatch):
    ex, x = _close_executor(venue_rows=[_present_row()], ticker=100.5)
    ex._hedge_mode = None
    monkeypatch.setattr(le, "audit", MagicMock())
    x.privateMixGetV2MixAccountAccount = AsyncMock(
        return_value={"data": {"holdMode": "single_hold"}})
    p = _pos100()
    ex._positions = {p.trade_id: p}
    asyncio.run(ex.check_positions())
    assert ex._hedge_mode is False
    assert p.status == "open"


def test_both_loops_ask_before_their_first_read_of_the_book():
    """The claim is an ORDER of two lines inside two 400-line methods, so it
    is pinned as a shape: the probe's await sits above the loop over
    positions in each."""
    for fn in (LiveExecutor.check_positions, LiveExecutor.reconcile_positions):
        src = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(src)
        probe_line = next(
            (n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Attribute)
             and n.value.func.attr == "_probe_hold_mode_if_unknown"), None)
        assert probe_line is not None, f"{fn.__name__} never asks"
        first_loop = next(n.lineno for n in ast.walk(tree)
                          if isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                          and n.target.id in ("pos", "sym"))
        assert probe_line < first_loop, f"{fn.__name__} reads the book before asking"


def test_nothing_in_the_executor_reads_a_failed_probe_as_one_way():
    """`_hedge_mode = False` is written only where it is a MEASUREMENT: a
    venue without hedge topology, or a venue that answered single-hold."""
    src = inspect.getsource(le.LiveExecutor._detect_hold_mode)
    assert "defaulting to one-way" not in src
    assert src.count("self._hedge_mode = False") == 1, src.count("self._hedge_mode = False")
