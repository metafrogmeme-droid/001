"""The /status liveness line must agree with the watchdog.

Shipped bug this pins (production, 2026-07-28): the status card carried its
OWN 120s rule — anything older than two minutes rendered
"⚠ Nm ago — the loop looks stalled". Two entirely legitimate waits exceed
that by design:

  - the smart-scan quiet-market sleep, up to 600s;
  - the run loop's exponential failure backoff, capped at 300s.

The engine stamps _next_tick_due_ts before parking for BOTH, and the
watchdog's _is_tick_stalled treats time inside a declared wait as healthy.
The card ignored all of it, so a bot that was merely waiting — or backing off
after tick failures, which is exactly when an operator reads /status — was
reported as stalled. A liveness line that cries stall while the engine is
deliberately waiting spends the trust it exists to earn.

The contract now: the card renders the WATCHDOG's verdict, never its own.
"""

import time

from bot.core.proactive_monitor import ProactiveMonitor
from bot.formatters.rich_cards import render_status_card
from bot.skills.telegram_handler import TelegramHandler


class _Engine:
    """Bare stand-in carrying only the two stamps liveness reads."""

    def __init__(self, last=None, due=None):
        self._last_tick_started_ts = last
        self._next_tick_due_ts = due


def _card(**kw):
    base = dict(
        mode="LIVE", active=True, equity=495.69, open_positions=0,
        daily_pnl=0.2, drawdown=0.0, max_drawdown=5.0, market_bias="Normal",
    )
    base.update(kw)
    return render_status_card(**base)


# ── the verdict helper ────────────────────────────────────────────────────

def test_declared_backoff_is_not_a_stall():
    """The reported false alarm: a tick 9m old while the loop sits in its
    declared failure backoff. The watchdog calls this healthy; so must /status."""
    now = time.monotonic()
    eng = _Engine(last=now - 540, due=now + 60)   # 9m old, but due in 60s
    stalled, next_in = TelegramHandler._tick_liveness(eng)
    assert stalled is False, "time inside a DECLARED wait is not a stall"
    assert next_in is not None and 0 < next_in <= 60


def test_long_quiet_market_sleep_is_not_a_stall():
    now = time.monotonic()
    eng = _Engine(last=now - 590, due=now + 10)   # smart-scan 600s interval
    stalled, _ = TelegramHandler._tick_liveness(eng)
    assert stalled is False


def test_a_real_hang_is_still_reported():
    now = time.monotonic()
    # Past the threshold AND past the declared wake-up plus its grace.
    eng = _Engine(last=now - 1200,
                  due=now - ProactiveMonitor.TICK_DUE_GRACE_S - 60)
    stalled, next_in = TelegramHandler._tick_liveness(eng)
    assert stalled is True
    assert next_in is None, "a stalled loop has no meaningful next-tick time"


def test_verdict_matches_the_watchdog_predicate_exactly():
    """Not merely 'similar' — the same answer on every case, because the card
    must never disagree with the CRITICAL alert the operator also receives."""
    now = time.monotonic()
    cases = [
        (now - 10, None), (now - 10, now + 60),
        (now - 540, now + 60), (now - 590, now + 10),
        (now - 700, None), (now - 1200, now - 300),
        (None, None), (None, now + 60),
    ]
    for last, due in cases:
        eng = _Engine(last=last, due=due)
        mine, _ = TelegramHandler._tick_liveness(eng)
        theirs = ProactiveMonitor._is_tick_stalled(
            last, time.monotonic(), ProactiveMonitor.TICK_STALL_THRESHOLD_S,
            next_due=due)
        assert mine is bool(theirs), f"disagreed on last={last} due={due}"


def test_unreadable_engine_never_manufactures_a_stall():
    stalled, next_in = TelegramHandler._tick_liveness(object())
    assert stalled is False and next_in is None
    stalled2, _ = TelegramHandler._tick_liveness(None)
    assert stalled2 is False


def test_engine_that_never_ticked_is_not_stalled():
    stalled, _ = TelegramHandler._tick_liveness(_Engine(last=None))
    assert stalled is False


# ── the rendered line ─────────────────────────────────────────────────────

def test_card_says_stalled_only_when_told_to():
    hung = _card(tick_age_s=1200.0, tick_stalled=True)
    assert "stalled" in hung and "20m" in hung

    waiting = _card(tick_age_s=540.0, tick_stalled=False, next_tick_in_s=60.0)
    assert "stalled" not in waiting, \
        "a declared wait must never render as a stall"
    assert "540s" in waiting


def test_card_shows_when_the_next_tick_is_due():
    out = _card(tick_age_s=540.0, tick_stalled=False, next_tick_in_s=60.0)
    assert "60s" in out, "the operator should see the loop has a plan"


def test_card_omits_the_next_due_hint_when_there_is_none():
    out = _card(tick_age_s=30.0, tick_stalled=False, next_tick_in_s=None)
    assert "30s" in out and "next due" not in out
    zero = _card(tick_age_s=30.0, tick_stalled=False, next_tick_in_s=0.0)
    assert "next due" not in zero, "a zero countdown is noise, not information"


def test_card_omits_the_line_entirely_when_liveness_is_unknown():
    out = _card(tick_age_s=None)
    assert "Last tick" not in out


def test_the_120s_rule_is_gone():
    """A 3-minute-old tick inside a declared wait used to read as stalled."""
    out = _card(tick_age_s=180.0, tick_stalled=False, next_tick_in_s=120.0)
    assert "stalled" not in out
