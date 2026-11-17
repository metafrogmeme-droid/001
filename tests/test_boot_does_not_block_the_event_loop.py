"""Boot held the event loop for over a minute, twice.

17 November 2026, two restarts in a row: about 78 seconds from "Starting
Telegram bot" until the gateway's health port answered. The first tripped
systemd's start timeout. During it the price feed's WebSocket handshake timed
out and a balance read in flight timed out with it, which the startup check
then reported as a rejected key.

Everything that failed runs on ONE loop: the health port, the WebSocket feed,
every Telegram handler, and the stop-loss monitoring. So the question for any
boot step is not how long it takes but whether the loop keeps turning while it
does.

What ran inline: the proactive monitor's first pass, which starts right after
Telegram does, called `assess_readiness()` on the loop. That assessment parses
the whole decision log more than once and resamples it. Measured the same day
on a synthetic log, it costs about 0.14 s per 1,000 rows and is linear, 8.5 s
at 60,000 rows, all of it with nothing else on the loop running. Its pacing
stamp starts at zero, so every boot paid for it, and every hour after.
(Whether the production log is large enough to account for the whole 78 s was
not measured: the file was not available to this change.)

The measurement here is a heartbeat: a task that wants to tick every 10 ms
while the step under test runs. A step that holds the loop shows as one long
gap. The harness is checked against a step that really does hold it, so a
pass means the loop kept turning and not that nothing was measured.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import ccxt.async_support as ccxt_async
import pytest

import bot.core.live_executor as le
from bot.core.close_lookup import client_is_uta
from bot.core.live_executor import LiveExecutor
from bot.core.proactive_monitor import ProactiveMonitor
from bot.skills.research_commands import ResearchCommands
from tests.test_a_classic_bitget_account_is_read_through_v2 import MARKET, _Account

TICK = 0.01
#: How long a slow step takes in these tests, and the longest stall a boot
#: step may cause. The budget is half the step, so a step that ran on the
#: loop cannot pass, and thirty ticks, so a busy machine does not fail.
SLOW_S = 0.6
STALL_BUDGET_S = SLOW_S * 0.5


async def _longest_stall(make_step):
    """Run the step while a heartbeat ticks. Returns (result, longest gap,
    seconds the step took). The heartbeat settles before and after, so a
    total freeze cannot hide as "no gap was seen"."""
    ticks: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(TICK)

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    result = await make_step()
    took = time.monotonic() - t0
    await asyncio.sleep(0.1)
    stop.set()
    await hb
    return result, max(b - a for a, b in zip(ticks, ticks[1:])), took


def _slow_assessment(state="VALIDATING"):
    """An assessment that takes `SLOW_S` of blocking work, as the real one
    does on a large decision log."""
    def assess(store=None):
        time.sleep(SLOW_S)
        return {"resolved_samples": 60, "recommendations": [],
                "components": {"voter_weights": {
                    "state": state, "applied": False,
                    "flag": "VOTER_WEIGHT_LEARNING_ENABLED"}}}
    return assess


def _monitor():
    return ProactiveMonitor(SimpleNamespace(risk=SimpleNamespace()))


# ── the harness can see a blocked loop ──────────────────────────────────────

async def test_the_heartbeat_sees_a_step_that_holds_the_loop():
    async def held():
        time.sleep(SLOW_S)          # what an inline assessment is to the loop

    _, stall, _ = await _longest_stall(held)
    assert stall >= SLOW_S * 0.8, "the harness cannot see a blocked loop"


async def test_the_heartbeat_sees_a_step_that_does_not():
    async def yielding():
        await asyncio.sleep(SLOW_S)

    _, stall, took = await _longest_stall(yielding)
    assert stall < STALL_BUDGET_S and took >= SLOW_S * 0.8


# ── the monitor's first pass ────────────────────────────────────────────────

class TestTheMonitorsFirstPass:

    async def test_the_readiness_assessment_does_not_hold_the_loop(self):
        mon = _monitor()
        assert mon._readiness_next_check == 0.0, "the first pass is the one that assesses"

        async def first_pass():
            alerts = mon._check_learning_readiness()
            await mon._readiness_job             # the assessment itself
            return alerts

        with patch("bot.learning.readiness.assess_readiness", _slow_assessment()):
            alerts, stall, took = await _longest_stall(first_pass)
        assert alerts == []
        assert took >= SLOW_S * 0.8, "the fixture's assessment did not run"
        assert stall < STALL_BUDGET_S, (
            f"the monitor's first pass held the event loop for {stall:.2f}s: the "
            "health port, the price feed and every handler waited behind it")

    async def test_the_check_returns_at_once_and_the_pass_goes_on(self):
        mon = _monitor()
        with patch("bot.learning.readiness.assess_readiness", _slow_assessment()):
            t0 = time.monotonic()
            assert mon._check_learning_readiness() == []
            assert time.monotonic() - t0 < STALL_BUDGET_S
            # A pass while the assessment is still running starts no second one.
            job = mon._readiness_job
            mon._readiness_next_check = 0.0
            assert mon._check_learning_readiness() == []
            assert mon._readiness_job is job
            await job

    async def test_the_alert_still_fires_one_pass_later(self):
        mon = _monitor()

        async def assessed(state):
            with patch("bot.learning.readiness.assess_readiness",
                       _slow_assessment(state)):
                mon._readiness_next_check = 0.0
                out = mon._check_learning_readiness()
                await mon._readiness_job
            return out

        assert await assessed("VALIDATING") == []        # nothing finished yet
        # This pass reads the VALIDATING result (the baseline, silent) and
        # starts the READY assessment.
        assert await assessed("READY") == []
        mon._readiness_next_check = time.time() + 1800   # not due: only read
        alerts = mon._check_learning_readiness()
        assert [a.alert_type for a in alerts] == ["LEARNING_READY"]
        assert mon._check_learning_readiness() == [], "the result is read once"

    async def test_an_assessment_that_fails_raises_nothing_and_is_tried_again(self):
        mon = _monitor()

        def boom(store=None):
            raise RuntimeError("the decision log would not read")

        with patch("bot.learning.readiness.assess_readiness", boom):
            assert mon._check_learning_readiness() == []
            await mon._readiness_job
        assert mon._readiness_result is None
        mon._readiness_next_check = 0.0
        with patch("bot.learning.readiness.assess_readiness", _slow_assessment()):
            mon._check_learning_readiness()
            await mon._readiness_job
        assert mon._readiness_result is not None

    def test_a_caller_with_no_loop_still_assesses_inline(self):
        """Nothing to block there, and the alert comes back from the call."""
        mon = _monitor()
        with patch("bot.learning.readiness.assess_readiness", _slow_assessment("VALIDATING")):
            assert mon._check_learning_readiness() == []
        with patch("bot.learning.readiness.assess_readiness", _slow_assessment("READY")):
            mon._readiness_next_check = 0.0
            alerts = mon._check_learning_readiness()
        assert [a.alert_type for a in alerts] == ["LEARNING_READY"]
        assert mon._readiness_job is None


def test_the_readiness_command_hands_the_assessment_to_a_thread():
    """The other caller on the loop. A handler cannot be timed without the
    whole bot, so the call site's shape is read: the assessment is an
    argument to `asyncio.to_thread`, and is never called inline."""
    src = textwrap.dedent(inspect.getsource(ResearchCommands._cmd_readiness))
    tree = ast.parse(src)
    threaded, inline = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "to_thread"
                and node.args and isinstance(node.args[0], ast.Name)):
            threaded.append(node.args[0].id)
        if isinstance(fn, ast.Name) and fn.id == "assess_readiness":
            inline.append(node.lineno)
    assert "assess_readiness" in threaded and inline == []


# ── the first use of the venue client ───────────────────────────────────────

class _NeverAnswers(_Account):
    """A venue that takes every request and answers none."""

    def attach(self, ex):
        super().attach(ex)

        async def fetch(url, method="GET", headers=None, body=None):
            self.calls.append((method, urlparse(url).path))
            await asyncio.Event().wait()

        ex.fetch = fetch
        return ex


@pytest.fixture
def silent_venue(monkeypatch):
    acct = _NeverAnswers(classic=True)
    real = ccxt_async.bitget

    def factory(cfg):
        ex = acct.attach(real(cfg))
        ex.set_markets([ex.safe_market_structure(dict(MARKET))])
        return ex

    monkeypatch.setattr(ccxt_async, "bitget", factory)
    monkeypatch.setattr(le, "ACCOUNT_FAMILY_READ_S", 0.2)
    return acct


def _fresh(tmp_path):
    e = LiveExecutor(state_dir=str(tmp_path), venue="bitget", user_id="u1",
                     credentials={"api_key": "k", "api_secret": "s", "passphrase": "p"})
    e._risk_engine = MagicMock()
    return e


class TestTheFirstUseOfTheVenueClient:

    async def test_a_venue_that_never_answers_costs_the_time_limit_and_no_more(
            self, tmp_path, silent_venue):
        e = _fresh(tmp_path)
        ex, stall, took = await _longest_stall(e._get_exchange)
        assert stall < STALL_BUDGET_S, f"the family read held the loop for {stall:.2f}s"
        assert 0.15 <= took < 1.0, f"the first use took {took:.2f}s against a 0.2s limit"
        assert client_is_uta(ex), "an answer nobody got decided the family"
        assert len(silent_venue.calls) == 1

    async def test_callers_waiting_on_the_read_are_all_released_by_the_limit(
            self, tmp_path, silent_venue):
        e = _fresh(tmp_path)

        async def three():
            return await asyncio.gather(*(e._get_exchange() for _ in range(3)))

        got, stall, took = await _longest_stall(three)
        assert stall < STALL_BUDGET_S and took < 1.0
        assert got[0] is got[1] is got[2]
        assert len(silent_venue.calls) == 1, "each waiting caller asked for itself"


# ── together ────────────────────────────────────────────────────────────────

async def test_the_boot_steps_together_stay_within_the_budget(tmp_path, silent_venue):
    """The monitor's first pass and the first use of the venue client, as they
    overlap at boot: a slow decision log and a venue that does not answer."""
    mon = _monitor()
    e = _fresh(tmp_path)

    async def boot():
        with patch("bot.learning.readiness.assess_readiness", _slow_assessment()):
            mon._check_learning_readiness()
            await asyncio.gather(e._get_exchange(), mon._readiness_job)

    _, stall, took = await _longest_stall(boot)
    assert stall < STALL_BUDGET_S, (
        f"boot held the event loop for {stall:.2f}s (budget {STALL_BUDGET_S}s)")
    assert took < SLOW_S + 1.0, f"boot took {took:.2f}s; nothing here should wait longer"
