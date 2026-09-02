"""The engine knew whether the stops were watched; nobody could read it.

`_backstop_position_monitor` has always audited one of two opposite facts —
`positions_backstop / RAN` or `INCOMPLETE` — and that line went to a log and
nowhere else. Meanwhile the degraded alert told the operator that open
positions "could be <b>unmonitored</b>" and pointed at /status and /positions,
neither of which could resolve it. Live incident, 2026-09-02: analyze blew its
300s cap three ticks running and the only two screens the alert names repeated
the symptom back.

That is the same hole the phase-cause carry fixed one level up, in this
codebase's own words: *"the cause was already known to the process and simply
never travelled to the surfaces anyone reads. A diagnosis that does not reach
the operator is not a diagnosis."*

FOUR outcomes are pinned here, not two, and the fourth is the one that makes
the surface honest: **no reading**. An engine that has not completed a tick
has not watched the stops and has not failed to — printing nothing there would
read as "fine" on the exact screen someone opens when they have been told it
might not be.

The /positions half is driven through `_cmd_open_positions` rather than
scanned. #999 shipped a card that was built and rendered zero times; a source
scan cannot tell a line that is present from one that is reached, and
reachability is a property of the caller.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from bot.core.engine import RuneClawEngine
from bot.formatters.rich_cards import position_watch_line, render_status_card


# ── A minimal engine carrying the REAL methods under test ────────────────
class _Eng:
    _POSITION_WATCH_WATCHED = RuneClawEngine._POSITION_WATCH_WATCHED
    _POSITION_WATCH_UNWATCHED = RuneClawEngine._POSITION_WATCH_UNWATCHED
    _record_position_watch = RuneClawEngine._record_position_watch
    position_watch = RuneClawEngine.position_watch
    _backstop_position_monitor = RuneClawEngine._backstop_position_monitor

    def __init__(self, *, monitored_after=None, phase_raises=None):
        self._last_position_watch = None
        self._positions_monitored_tick = False
        self._monitored_after = monitored_after
        self._phase_raises = phase_raises

    async def _check_open_positions(self):
        return None

    async def _phase(self, coro, what, fatal=True):
        # Consume the coroutine so nothing warns about it never being awaited.
        await coro
        if self._phase_raises is not None:
            raise self._phase_raises
        # The flag is set at the END of the real _check_open_positions; a
        # back-stop cancelled at its cap never reaches it. That is the only
        # thing in the process that tells "returned" from "watched".
        if self._monitored_after:
            self._positions_monitored_tick = True
        return None


def _run(eng):
    asyncio.run(eng._backstop_position_monitor())
    return eng.position_watch()


# ── 1. The engine records a verdict, and the right one ───────────────────

def test_normal_tick_records_watched_without_running_the_backstop():
    eng = _Eng()
    eng._positions_monitored_tick = True          # the tick did its own check
    ran = []
    eng._check_open_positions = lambda: ran.append(1)  # must NOT be called
    rec = _run(eng)
    assert rec["outcome"] == "tick"
    assert rec["unwatched_streak"] == 0
    assert not ran, "back-stop re-ran a monitor the tick had already run"


def test_backstop_that_completes_is_recorded_as_backstop_not_as_normal():
    """Both mean the stops were watched. Only one of them means the loop is
    healthy, and an operator reading /positions during a degraded tick is
    entitled to know which guarantee is holding their money."""
    rec = _run(_Eng(monitored_after=True))
    assert rec["outcome"] == "backstop"
    assert rec["unwatched_streak"] == 0


def test_backstop_that_returns_without_watching_is_recorded_unwatched():
    """`fatal=False` returns None on a timeout, so a back-stop cancelled at
    its cap arrives at the end of the function exactly like one that finished.
    Returning is not evidence it ran."""
    rec = _run(_Eng(monitored_after=False))
    assert rec["outcome"] == "incomplete"
    assert rec["unwatched_streak"] == 1


def test_backstop_that_raises_is_recorded_unwatched():
    rec = _run(_Eng(monitored_after=False, phase_raises=RuntimeError("venue down")))
    assert rec["outcome"] == "error"
    assert rec["unwatched_streak"] == 1


def test_cancellation_is_not_recorded_as_a_verdict():
    """Shutting the process down is not a finding about the stops. Recording
    one would put a red line on /positions after every clean stop."""
    eng = _Eng(monitored_after=False, phase_raises=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eng._backstop_position_monitor())
    assert eng.position_watch() is None


# ── 2. The streak, which is what separates a blip from an incident ───────

def test_consecutive_unwatched_ticks_accumulate_and_a_good_tick_clears_it():
    eng = _Eng(monitored_after=False)
    for expected in (1, 2, 3):
        assert _run(eng)["unwatched_streak"] == expected
    eng._monitored_after = True
    assert _run(eng)["unwatched_streak"] == 0


def test_an_unrecognised_outcome_does_not_clear_the_streak():
    """A verdict added upstream and not taught to this method is not evidence
    that the stops were watched."""
    eng = _Eng()
    eng._record_position_watch("incomplete")
    eng._record_position_watch("incomplete")
    eng._record_position_watch("some_future_verdict")
    assert eng.position_watch()["unwatched_streak"] == 2


# ── 3. Absent is never a measurement ─────────────────────────────────────

def test_an_engine_that_has_not_ticked_reports_nothing_not_health():
    assert _Eng().position_watch() is None


def test_an_unreadable_timestamp_gives_no_age_rather_than_zero():
    """0.0s would render as "just now" — the most reassuring answer there is,
    manufactured from a failed read."""
    eng = _Eng()
    eng._record_position_watch("incomplete")
    eng._last_position_watch["at"] = None
    assert eng.position_watch()["age_s"] is None


def test_recording_never_raises_into_the_tick_failure_path():
    """It runs from inside _tick_guarded's finally. An exception here would
    propagate out of the finally and REPLACE the real error the tick carries."""
    eng = _Eng()
    eng._last_position_watch = {"unwatched_streak": object()}   # unusable
    eng._record_position_watch("incomplete")                    # must not raise


# ── 4. What the operator actually reads ──────────────────────────────────
#
# MUST_SAY / MUST_NOT_SAY, anchored to the monitor's own line. Asserting a
# short string is ABSENT from a whole card is the assertion that keeps
# misfiring in this repo — "0.0%" matched inside "(default 10.0%)", and a
# no-green rule matched the direction icon, which was telling the truth.

def _line(out):
    return next((ln for ln in out.split("\n") if "SL/TP monitor" in ln), "")


def test_no_reading_renders_explicitly_and_is_not_green():
    line = position_watch_line(None)
    assert "not recorded" in line
    assert "\U0001f7e2" not in line and "✅" not in line
    assert "⚪" in line, "unknown must get the muted accent, not a verdict colour"


def test_an_unrecognised_outcome_renders_as_unknown_not_as_healthy():
    line = position_watch_line({"outcome": "brand_new", "unwatched_streak": 0})
    assert "not recorded" in line
    assert "✅" not in line


def test_unwatched_says_so_in_words_and_carries_the_streak():
    line = position_watch_line(
        {"outcome": "incomplete", "unwatched_streak": 4, "age_s": 300})
    assert "unwatched" in line
    assert "DID NOT RUN" in line
    assert "×4" in line, "a run of unwatched ticks is the incident; show it"
    assert "\U0001f534" in line
    assert "✅" not in line and "⚪" not in line


def test_backstop_is_a_warning_not_an_all_clear_and_not_a_failure():
    line = position_watch_line({"outcome": "backstop", "unwatched_streak": 0})
    assert "back-stop" in line
    assert "unwatched" not in line, "the stops WERE watched — do not cry wolf"
    assert "\U0001f534" not in line, "red would send an operator hunting a fault"
    assert "⚠" in line


def test_a_single_unwatched_tick_does_not_print_a_multiplier():
    line = position_watch_line({"outcome": "incomplete", "unwatched_streak": 1})
    assert "×1" not in line


# ── 5. Both surfaces the degraded alert names ────────────────────────────

def test_status_card_answers_the_question_the_alert_raises():
    out = render_status_card(
        mode="LIVE", active=True, equity=1000.0, open_positions=2, daily_pnl=1.0,
        drawdown=0.02, max_drawdown=0.1, market_bias="Neutral",
        position_watch={"outcome": "incomplete", "unwatched_streak": 3, "age_s": 240},
        # RED HERRING: the loop reports itself alive and recently ticked. A
        # live loop is exactly the state in which the stops go unwatched —
        # analyze blows its cap, the tick unwinds before its position check,
        # and the engine keeps ticking. Reading "active" as "watched" is the
        # inference this line exists to prevent.
        tick_age_s=12.0, tick_stalled=False)
    line = _line(out)
    assert "unwatched" in line and "\U0001f534" in line


def test_status_card_stays_quiet_on_the_healthy_path():
    out = render_status_card(
        mode="LIVE", active=True, equity=1000.0, open_positions=2, daily_pnl=1.0,
        drawdown=0.02, max_drawdown=0.1, market_bias="Neutral",
        position_watch={"outcome": "tick", "unwatched_streak": 0, "age_s": 9})
    assert _line(out) == ""


def test_status_card_renders_no_reading_rather_than_omitting_it():
    """Omitting it would make "never looked" indistinguishable from "looked
    and all is well" on the screen the alert sends people to."""
    out = render_status_card(
        mode="LIVE", active=True, equity=1000.0, open_positions=2, daily_pnl=1.0,
        drawdown=0.02, max_drawdown=0.1, market_bias="Neutral",
        position_watch=None)
    assert "not recorded" in _line(out)


# ── 6. /positions — DRIVEN, not scanned ──────────────────────────────────
#
# #999 shipped a per-position outcome that was source-scanned, passed, and
# rendered zero times in production: the code was present and never reached,
# and no scan distinguishes those. The header line added here is one `if`
# inside a 300-line handler with three early returns above it, so it gets
# exercised through the real command.

class _Pos:
    def __init__(self):
        self.asset = "BTC/USDT"
        self.direction = NS(value="LONG")
        self.entry_price = 50000.0
        self.stop_loss = 49000.0
        self.take_profit = 52000.0
        self.trade_id = "t1"
        self.leverage = 3
        self.opened_at = datetime.now(timezone.utc) - timedelta(hours=3)
        self.size = 0.01
        self.quantity = 0.01
        self.notional = 500.0
        self.strategy = "swing"


class _Portfolio:
    def __init__(self):
        self._lock = threading.RLock()
        p = _Pos()
        self._positions = {"t1": p}
        self._last_prices = {"BTC/USDT": 50500.0}
        self.open_positions = [p]

    def mark_to_market(self, fresh):
        pass


async def _drive_positions(watch):
    """Run the real /positions handler and return everything it sent."""
    from bot.skills import telegram_handler as th

    h = object.__new__(th.TelegramHandler)
    h.engine = NS(
        user_portfolios={7: _Portfolio()},
        position_watch=lambda: watch,
        # The paper path reaches for a live ticker refresh; it is wrapped in a
        # bare except, so a scanner that refuses is a supported state.
        scanner=NS(_get_exchange=_boom),
        pending_ideas=[],
    )
    sent: list[str] = []
    h._get_tg_id = lambda update: 7
    h._lang = lambda update: "en"
    h._guard = _allow

    async def _send(update, text, **kw):
        sent.append(text)
    h._send = _send

    async def _send_photo(update, png, caption, reply_markup=None):
        sent.append(caption)
        return True
    h._send_photo = _send_photo

    await h._cmd_open_positions(None, None)
    return "\n".join(sent)


async def _allow(update, command="", ctx=None):
    """The @guard decorator's auth/rate-limit seam. Not what is under test."""
    return True


async def _boom():
    raise RuntimeError("no exchange in this test")


@pytest.mark.parametrize("watch,must_say", [
    ({"outcome": "incomplete", "unwatched_streak": 3, "age_s": 240}, "unwatched"),
    ({"outcome": "backstop", "unwatched_streak": 0, "age_s": 8}, "back-stop"),
    ({"outcome": "tick", "unwatched_streak": 0, "age_s": 9}, "ran this tick"),
    (None, "not recorded"),
])
def test_positions_card_states_the_verdict_for_every_outcome(watch, must_say):
    """Including the healthy one. /status may stay short; a card whose entire
    subject is whether the stops are in place must not leave the reader to
    infer that the monitor ran — inferring is what the alert asked them not
    to do."""
    from bot.config import CONFIG
    assert not CONFIG.is_live(), "this harness drives the PAPER branch"
    out = asyncio.run(_drive_positions(watch))
    assert "SL/TP monitor" in out, "the line never reached the card"
    assert must_say in out


def test_positions_card_does_not_call_an_engine_without_the_reader():
    """Deployed mid-rollout, the engine may predate `position_watch`. The card
    must degrade to "no reading", not to a crash and not to silence."""
    from bot.skills import telegram_handler as th

    async def _go():
        h = object.__new__(th.TelegramHandler)
        h.engine = NS(user_portfolios={7: _Portfolio()},
                      scanner=NS(_get_exchange=_boom), pending_ideas=[])
        sent: list[str] = []
        h._get_tg_id = lambda update: 7
        h._lang = lambda update: "en"
        h._guard = _allow

        async def _send(update, text, **kw):
            sent.append(text)
        h._send = _send

        async def _send_photo(update, png, caption, reply_markup=None):
            sent.append(caption)
            return True
        h._send_photo = _send_photo
        await h._cmd_open_positions(None, None)
        return "\n".join(sent)

    out = asyncio.run(_go())
    assert "not recorded" in out
