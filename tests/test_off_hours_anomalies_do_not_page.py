"""A shut market is not a liquidity emergency.

On Sunday 2026-08-30, between 05:01 and 05:22 UTC, the operator's channel took
three severe pages and a digest covering 81 symbols: SPREAD_WIDENING on 78,
PRICE_ACCELERATION on 76, CORRELATION_BREAKDOWN on 65, VOLUME_COLLAPSE on 39.
The three loudest were META/USDT:USDT at 47.2x baseline, DFEN at 25.8x and
COIN at 22.6x, each at severity 1.00 advising HALT_NEW_TRADES.

Every one of those classifies as Stock or ETF, and it was a Sunday. A
tokenized equity outside its market's hours has almost no liquidity — the
spread widens, volume collapses, prints go stale, and correlation against a
24/7 crypto peer breaks because one side is frozen. All four detectors fire on
the same non-event.

THIS WAS ALREADY DIAGNOSED ONCE. `_check_spread_widening` carries the note
from the 2026-08-19 flood: BBSTOCK at 8.4x and RTXSTOCK at 10.6x, named there
as "tokenized equities outside their market's hours, where a spread several
times baseline is what the instrument does, not an emergency". The fix applied
was to raise the severity ceiling 8x -> 20x so they would land mid-scale. Nine
days later META reached 47.2x and went straight back through it. A ratio is
unbounded exactly BECAUSE the condition is unbounded, so no ceiling holds —
the comment even predicts its own next move ("this number is the one to
move"). The detector needed to know what the rest of the repo already knew:
`order_rules.is_market_open()` and `market_scanner._classify_symbol()` were
both already in use for weekend sizing and gap-risk stops.

THE NOISE AND THE REPEATS ARE ONE PROBLEM, NOT TWO. The "+N more severe
anomalies this pass" card appeared three times ~15 minutes apart. That is
BLACK_SWAN_SEVERE_REPEAT (900s) working exactly as designed for a persisting
severe condition — not a dedup bug. Stop these reaching severity 0.8 and they
leave the severe path entirely, folding into the 30-minute digest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.core.black_swan import (
    _HALT_SEVERITY,
    AnomalyAlert,
    AnomalyType,
    attenuate_off_hours,
    off_hours_reason,
)

UTC = timezone.utc
SUNDAY = datetime(2026, 8, 30, 5, 1, 28, tzinfo=UTC)     # the actual flood

# THIS PREMISE WAS BACKWARDS, AND THE COMMENT EXPLAINING IT SAYS HOW.
#
# It read: "order_rules trades them 02:30-09:00 UTC ... The first draft of this
# file used Wednesday 15:00 UTC as 'open' — that is outside the window, so
# three tests failed on a wrong premise rather than wrong code." The first
# draft was RIGHT. 15:00 UTC is 11:00 in New York, the middle of the session;
# 05:00 UTC is midnight there. The tests disagreed with the code, and the code
# was taken as the authority — which is how a wrong constant acquires a test
# that defends it.
#
# Two separate facts settle it. Bitget moved stock perps to 24/7 trading on
# 2026-02-07, so there is no venue window at all; and the equities they track
# run 09:30-16:00 America/New_York, which is 13:30-20:00 UTC under EDT. The old
# window was neither. Attenuation is about the UNDERLYING's clock — see
# `is_reference_session_open` — so these times are now stated in New York terms
# and converted, rather than written directly as UTC constants where a wrong
# one looks exactly like a right one.
_NY = ZoneInfo("America/New_York")


def _at_ny(y, m, d, hh, mm):
    """A UTC instant given as New York wall-clock — DST handled for us."""
    return datetime(y, m, d, hh, mm, tzinfo=_NY).astimezone(UTC)


WEDNESDAY = _at_ny(2026, 8, 26, 11, 0)      # 11:00 ET = 15:00 UTC, mid-session
WEDNESDAY_NIGHT = _at_ny(2026, 8, 26, 1, 0)  # 01:00 ET = 05:00 UTC, shut


def _alert(symbol, severity=1.00, kind=AnomalyType.SPREAD_WIDENING, ratio=47.2):
    return AnomalyAlert(
        anomaly_type=kind, severity=severity, symbol=symbol,
        description=f"{symbol} estimated spread widened to {ratio}x baseline "
                    f"(threshold 2x)",
        metric_value=ratio, threshold=2.0,
        recommended_action="HALT_NEW_TRADES",
    )


# ── the flood, symbol by symbol ───────────────────────────────────────
@pytest.mark.parametrize("symbol", [
    "META/USDT:USDT", "DFEN/USDT:USDT", "COIN/USDT:USDT",
    "AAPL/USDT:USDT", "AMZN/USDT:USDT", "ARM/USDT:USDT",
    "BBSTOCK/USDT:USDT", "CRCL/USDT:USDT",
])
def test_the_symbols_that_paged_on_sunday_no_longer_page(symbol):
    out = attenuate_off_hours(_alert(symbol), now=SUNDAY)
    assert out.severity < _HALT_SEVERITY, (
        f"{symbol} still pages at {out.severity} on a Sunday")
    assert out.recommended_action == "MONITOR"


def test_crypto_is_untouched_on_the_same_sunday():
    # The whole point: a real crypto liquidity failure must still page. Crypto
    # markets are open on Sundays, so nothing about this is off-hours.
    for symbol in ("BTC/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT"):
        out = attenuate_off_hours(_alert(symbol), now=SUNDAY)
        assert out.severity == 1.00, f"{symbol} was wrongly attenuated"
        assert out.recommended_action == "HALT_NEW_TRADES"


def test_an_equity_still_pages_during_its_own_session():
    # Attenuation is about the CLOCK, not the instrument. A genuine spread
    # blowout on META while US markets are open is a real event.
    out = attenuate_off_hours(_alert("META/USDT:USDT"), now=WEDNESDAY)
    assert out.severity == 1.00
    assert out.recommended_action == "HALT_NEW_TRADES"


# ── attenuated, not suppressed ────────────────────────────────────────
def test_the_alert_survives_and_says_why():
    # Never hidden — a quiet channel is not a claim the market is calm.
    out = attenuate_off_hours(_alert("META/USDT:USDT"), now=SUNDAY)
    assert out is not None
    assert "47.2x baseline" in out.description, "the measurement was lost"
    assert "closed" in out.description.lower() or "off-hours" in out.description.lower()
    assert out.metric_value == 47.2, "the raw metric must survive for the digest"


def test_ordering_is_preserved_so_the_digest_still_ranks_them():
    # SCALED, not clamped. `min(severity, cap)` would flatten 47x and 22x onto
    # the same number and lose the ordering the digest sorts by.
    # BOTH inputs must sit ABOVE the cap, or the test cannot tell clamping
    # from scaling: with 1.00/0.60/0.30 a `min()` yields 0.79/0.60/0.30, which
    # is still ordered, and a clamp mutation survived the first draft.
    loud = attenuate_off_hours(_alert("META/USDT:USDT", 1.00), now=SUNDAY)
    also_loud = attenuate_off_hours(_alert("COIN/USDT:USDT", 0.90), now=SUNDAY)
    quiet = attenuate_off_hours(_alert("AAPL/USDT:USDT", 0.30), now=SUNDAY)
    assert loud.severity > also_loud.severity, (
        "two alerts above the cap collapsed onto the same severity — the "
        "digest can no longer rank them")
    assert also_loud.severity > quiet.severity
    assert loud.severity < _HALT_SEVERITY


# ── every detector, not just spread ───────────────────────────────────
@pytest.mark.parametrize("kind", list(AnomalyType))
def test_all_four_detectors_are_attenuated_off_hours(kind):
    # The Sunday digest fired SPREAD_WIDENING, PRICE_ACCELERATION,
    # CORRELATION_BREAKDOWN and VOLUME_COLLAPSE on the same frozen tape.
    out = attenuate_off_hours(_alert("META/USDT:USDT", 1.00, kind), now=SUNDAY)
    assert out.severity < _HALT_SEVERITY, f"{kind} still pages off-hours"


# ── fail-open ─────────────────────────────────────────────────────────
def test_a_classification_fault_is_never_quietly_demoted(monkeypatch):
    # Missing an attenuation costs noise. A wrong one costs a page nobody
    # sends, so a fault must leave the alert alone.
    #
    # Drive a REAL exception. The first draft passed a garbage symbol and
    # asserted it stayed at 1.00 — but `_classify_symbol` falls through to
    # "Crypto" for anything it does not recognise, so nothing raised, the
    # except branch was never reached, and a fail-CLOSED mutation survived.
    import bot.core.market_scanner as ms

    def _boom(_symbol):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(ms, "_classify_symbol", _boom)
    out = attenuate_off_hours(_alert("META/USDT:USDT", 1.00), now=SUNDAY)
    assert out.severity == 1.00, "a classifier fault silenced a severe alert"
    assert off_hours_reason("META/USDT:USDT", now=SUNDAY) == ""


def test_off_hours_reason_is_empty_when_open():
    assert off_hours_reason("BTC/USDT", now=SUNDAY) == ""
    assert off_hours_reason("META/USDT:USDT", now=WEDNESDAY) == ""


def test_off_hours_reason_names_the_cause():
    reason = off_hours_reason("META/USDT:USDT", now=SUNDAY)
    assert reason and ("closed" in reason.lower() or "weekend" in reason.lower())


# ── the boundary ──────────────────────────────────────────────────────
def test_the_boundary_moves_with_both_the_day_and_the_clock():
    monday_night = SUNDAY + timedelta(days=1)            # Mon 05:01 UTC = 01:01 ET
    monday_session = SUNDAY + timedelta(days=1, hours=10)  # Mon 15:01 UTC = 11:01 ET
    saturday = SUNDAY - timedelta(days=1)                 # Sat 05:01 UTC
    assert off_hours_reason("META/USDT:USDT", now=monday_night), (
        "01:01 in New York is the middle of the night — thin books there are "
        "ordinary and must not page")
    assert off_hours_reason("META/USDT:USDT", now=monday_session) == "", (
        "11:01 in New York is mid-session — a 47x spread there is a real event")
    assert off_hours_reason("META/USDT:USDT", now=saturday), (
        "Saturday: the underlying does not trade")


def test_the_session_follows_new_york_across_the_dst_change():
    """A fixed UTC window is wrong for ~4 months a year, and the window this
    file used to assert could not have expressed the difference at all."""
    summer = _at_ny(2026, 7, 15, 9, 45)   # EDT — 13:45 UTC
    winter = _at_ny(2026, 1, 14, 9, 45)   # EST — 14:45 UTC
    assert summer.hour == 13 and winter.hour == 14, "fixture lost the DST shift"
    for t in (summer, winter):
        assert off_hours_reason("META/USDT:USDT", now=t) == "", (
            f"09:45 ET is in session; attenuated at {t:%H:%M} UTC")
    # The same UTC clock time is on opposite sides of the open across the change.
    assert off_hours_reason("META/USDT:USDT",
                            now=datetime(2026, 1, 14, 13, 45, tzinfo=UTC)), (
        "13:45 UTC in January is 08:45 ET — before the open")


def test_a_stock_perp_order_is_never_gated_on_a_session():
    """The venue is 24/7. Gating orders on a session widened live stops 25-50%
    as `weekend gap risk` on an instrument with no weekend.

    Swept across a FULL WEEK, hour by hour, rather than at three chosen
    instants. Any re-added window has some shape, and a spot check only finds
    the shapes you thought of — the old one passed its own tests for months.
    """
    from bot.core.order_rules import is_market_open, is_weekend_queued
    start = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)   # a Monday
    for h in range(24 * 7):
        t = start + timedelta(hours=h)
        assert is_market_open("Stock", t)[0], f"stock perp reported shut at {t}"
        assert not is_weekend_queued("Stock", t), (
            f"stop-widening armed at {t} on a 24/7 instrument")


def test_the_24_7_guarantee_is_explicit_not_a_fallback():
    """`is_market_open` ends with "Unknown class — assume open", so Stock would
    read as open even if it were dropped from the always-open set. That makes
    the right answer an accident of a default meant for classes nobody has
    classified. State it, so the guarantee has somewhere to fail."""
    from bot.core.order_rules import _ALWAYS_OPEN, _SESSION_HOURS
    assert "Stock" in _ALWAYS_OPEN
    assert "Stock" not in _SESSION_HOURS


def test_a_class_with_no_reference_market_is_never_in_session():
    """Crypto has no outside clock to be shut by. Returning True here would
    mark every crypto alert "in session" and, worse, imply there is a session."""
    from bot.core.order_rules import is_reference_session_open
    for t in (SUNDAY, WEDNESDAY, WEDNESDAY_NIGHT):
        assert not is_reference_session_open("Crypto", t)


def test_a_missing_timezone_database_attenuates_rather_than_pages(monkeypatch):
    """Container images ship without tzdata often enough that this is a real
    state, not a hypothetical. Failing the other way would page an operator on
    ordinary thin-book spreads because a package was absent — noise on the
    channel that exists for genuine emergencies."""
    import builtins

    from bot.core import order_rules as orr
    real_import = builtins.__import__

    def _no_zoneinfo(name, *a, **kw):
        if name == "zoneinfo":
            raise ModuleNotFoundError("No module named 'zoneinfo'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_zoneinfo)
    # Mid-session in New York: the answer would be True if the clock were readable.
    assert not orr.is_reference_session_open("Stock", WEDNESDAY)
    assert off_hours_reason("META/USDT:USDT", now=WEDNESDAY), (
        "an unreadable clock must attenuate, not page")
