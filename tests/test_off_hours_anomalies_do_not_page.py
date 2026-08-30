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
# Stock PERPS, not US-session equities: order_rules trades them 02:30-09:00
# UTC on weekdays (US hours during EDT) and closes them all weekend. The first
# draft of this file used Wednesday 15:00 UTC as "open" — that is outside the
# window, so three tests failed on a wrong premise rather than wrong code.
WEDNESDAY = datetime(2026, 8, 26, 5, 0, 0, tzinfo=UTC)   # inside the session


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
    monday_open = SUNDAY + timedelta(days=1)          # Mon 05:01 — in session
    monday_shut = SUNDAY + timedelta(days=1, hours=10)  # Mon 15:01 — after close
    saturday = SUNDAY - timedelta(days=1)              # Sat 05:01 — weekend
    assert off_hours_reason("META/USDT:USDT", now=monday_open) == "", (
        "attenuating during the Monday session window")
    assert off_hours_reason("META/USDT:USDT", now=monday_shut), (
        "15:01 UTC is outside the 02:30-09:00 window — should be attenuated")
    assert off_hours_reason("META/USDT:USDT", now=saturday), (
        "Saturday is closed for stock perps")
