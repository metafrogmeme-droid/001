"""A deadline expiring is not a diagnosis of the exchange.

    🔴 Deepscan timed out. Exchange may be slow — try again.

Sent to the operator at 15:37 on 2026-07-29, roughly three hours after the
same claim was removed from the interactive-scan hint for the same reason,
and on a day when the engine's own measurements said the exchange was fine:
~3.3s per symbol, and the analyze phase failing purely because 161 symbols
did not fit a 300s budget.

A timeout says the work did not fit the time. WHICH of the two was wrong —
too much work, or too little time — is a separate question, and a message
fired by the deadline itself is not entitled to answer it.

The deadline is also now configurable, because the right value depends on the
universe: shorter than `symbols x per-symbol cost` and it will always expire,
which is a sizing fact rather than a fault.
"""

from pathlib import Path

import pytest

from bot.config import CONFIG
from tests.source_scan import code_only

HANDLER = code_only(Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8"))


def _timeout_block() -> str:
    i = HANDLER.index('f"🔴 <b>Deepscan hit its')
    return HANDLER[i - 1200:i + 600]


# ── the verdict is gone ───────────────────────────────────────────────────

@pytest.mark.parametrize("claim", [
    "Exchange may be slow",
    "exchange may be slow",
])
def test_the_exchange_is_no_longer_blamed(claim):
    assert claim not in HANDLER, (
        f"a deadline expiring does not establish {claim!r} — the engine's own "
        f"numbers contradicted it the same day")


def test_it_says_whose_budget_expired():
    block = _timeout_block()
    assert "command's own budget" in block
    assert "not a diagnosis of the exchange" in block


def test_it_points_at_the_instrument_that_can_answer():
    """Ruling nothing out is fine; leaving the reader with nowhere to look is
    not. /status carries the measured scan timing."""
    assert "/status" in _timeout_block()


def test_it_reports_the_actual_budget_not_a_hardcoded_number():
    """The old message said "timed out" with 120 baked into the log line, so
    raising the deadline would have left the text lying."""
    block = _timeout_block()
    assert "_budget:.0f" in block
    assert "120s" not in block, "the number must come from config"


# ── the deadline is configurable ──────────────────────────────────────────

def test_both_deadlines_are_config_driven():
    assert "timeout=(CONFIG.deepscan_multi_timeout_sec if _multi" in HANDLER
    assert "else CONFIG.deepscan_timeout_sec)" in HANDLER


def test_the_defaults_preserve_todays_behaviour():
    """A configurable knob that changes the value on introduction is two
    changes wearing one commit."""
    assert CONFIG.deepscan_timeout_sec == 120.0
    assert CONFIG.deepscan_multi_timeout_sec == 300.0


def test_the_multi_budget_stays_the_larger_of_the_two():
    assert CONFIG.deepscan_multi_timeout_sec > CONFIG.deepscan_timeout_sec, (
        "a multi-timeframe sweep does ~5x the fetches")


def test_the_bounds_reject_a_useless_deadline():
    cfg = Path("bot/config.py").read_text(encoding="utf-8")
    assert '"DEEPSCAN_TIMEOUT_SEC", 120.0, 10.0, 1800.0' in cfg
    assert '"DEEPSCAN_MULTI_TIMEOUT_SEC", 300.0, 10.0, 3600.0' in cfg


def test_the_log_line_records_which_budget_it_was():
    block = _timeout_block()
    assert "multi=%s" in block, (
        "300s and 120s expiring look identical in a log that names neither")
