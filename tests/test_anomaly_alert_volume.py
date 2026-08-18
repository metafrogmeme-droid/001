"""Two messages, same type, same second — and then again every five minutes.

Reported from the live Telegram channel on 2026-08-18, with a screenshot of two
full ANOMALY NOTED cards timestamped 11:57:26, both SPREAD_WIDENING, differing
only in symbol, each carrying the same four lines of footer.

TWO CAUSES, WHICH COMPOUND.

  VOLUME.  `_check_black_swan` clusters exactly one anomaly type —
           CORRELATION_BREAKDOWN, grouped by peer — and its own comment states
           the principle: "One market event, one page". Every other type fell
           through to `singles` and paged once per symbol. SPREAD_WIDENING is
           the worst possible omission: a liquidity event widens spreads on
           EVERYTHING at once, so it is simultaneously the type most likely to
           arrive in bulk and the common one with no clustering at all. The
           control existed and did not cover the case that hurts — the same
           shape as `guard_lint`'s own blind spot, one subsystem over.

  REPEAT.  `DEDUP_COOLDOWN` is 300s and anomalies are STANDING CONDITIONS, not
           events. A spread that stays wide for an hour re-pages twelve times
           per symbol, saying nothing new. An operator told the same thing
           every five minutes stops reading, and the next alert — the one that
           IS new — arrives into that habit.

WHAT MUST NOT BE LOST TO EITHER FIX. A severe anomaly is never clustered and
never suppressed: burying a 0.9 beside a 0.2, or holding it for thirty minutes,
would trade noise for the one failure that actually costs money. Escalation
breaks through immediately. And the filter fails OPEN — anything it cannot
classify is sent, because a suppression filter that silences on its own
confusion is invisible from the outside.
"""

from __future__ import annotations

import time

import pytest

from bot.core.proactive_monitor import Alert, ProactiveMonitor


def _mon() -> ProactiveMonitor:
    return ProactiveMonitor(engine=object())


def _bs(key: str, *, tier: int = 0) -> Alert:
    """An anomaly alert at a given visual tier, shaped like the real ones."""
    body = "\U0001f440 <b>ANOMALY NOTED</b>\n"
    if tier == 1:
        body += "- Severity: \U0001f7e0 <code>0.30</code>\n"
    else:
        body += "- Severity: \U0001f7e1 <code>0.10</code>\n"
    return Alert(
        alert_type="BLACK_SWAN",
        severity="CRITICAL" if tier >= 2 else "WARNING",
        title="Anomaly", body=body, dedup_key=key,
    )


# ── the repeat is suppressed, the news is not ────────────────────────────────

def test_the_first_sighting_always_pages():
    assert _mon()._bs_is_news(_bs("bs_SPREAD_WIDENING_XLM/USDT")) is True


def test_the_same_standing_condition_does_not_page_again():
    """The literal complaint: the same thing, every five minutes."""
    m = _mon()
    key = "bs_SPREAD_WIDENING_XLM/USDT"
    assert m._bs_is_news(_bs(key)) is True
    for _ in range(12):          # an hour of 5-minute re-checks
        assert m._bs_is_news(_bs(key)) is False


def test_it_pages_again_once_the_condition_has_persisted():
    """Suppression, not silence. A condition still true half an hour later is
    worth one reminder — the operator may have joined since."""
    m = _mon()
    key = "bs_SPREAD_WIDENING_XLM/USDT"
    assert m._bs_is_news(_bs(key)) is True
    m._bs_last[key] = (time.time() - ProactiveMonitor.BLACK_SWAN_REPEAT - 1, 0)
    assert m._bs_is_news(_bs(key)) is True


def test_escalation_breaks_through_immediately():
    """A 0.1 that becomes a 0.3 is new information and must not wait out the
    window it entered under."""
    m = _mon()
    key = "bs_SPREAD_WIDENING_XLM/USDT"
    assert m._bs_is_news(_bs(key, tier=0)) is True
    assert m._bs_is_news(_bs(key, tier=0)) is False
    assert m._bs_is_news(_bs(key, tier=1)) is True, (
        "an anomaly that got worse was held back by the suppression window")


def test_a_severe_anomaly_is_never_suppressed():
    m = _mon()
    key = "bs_FLASH_CRASH_BTC/USDT"
    for _ in range(5):
        assert m._bs_is_news(_bs(key, tier=2)) is True, (
            "a CRITICAL anomaly was suppressed — the one page that must always "
            "arrive")


def test_the_filter_fails_open():
    """It cannot classify -> it sends. A filter that silences on its own
    confusion is invisible: the operator cannot see what they were not told."""
    m = _mon()
    assert m._bs_is_news(Alert(alert_type="BLACK_SWAN", severity="WARNING",
                               title="t", body="", dedup_key="")) is True
    assert m._bs_is_news(Alert(alert_type="STATE_CHANGE", severity="CRITICAL",
                               title="t", body="", dedup_key="k")) is True


def test_other_alert_types_are_untouched():
    """The fix is scoped to anomalies. Widening it to every alert would have
    quietly slowed halt and gateway pages, which are events, not conditions."""
    m = _mon()
    a = Alert(alert_type="STATE_CHANGE", severity="CRITICAL", title="Engine HALTED",
              body="x", dedup_key="state_halt")
    for _ in range(3):
        assert m._bs_is_news(a) is True


# ── the clustering covers the type that actually floods ──────────────────────

def test_every_anomaly_type_can_cluster_not_just_correlation():
    """The source check, because the clustering lives inside a method that
    needs a live engine and a populated black_swan detector to run.

    Pinned as a PROPERTY of the code rather than a spelling: the grouping must
    key on the anomaly type for the general case, not name one type.
    """
    from tests.source_scan import code_only
    import pathlib

    src = code_only(pathlib.Path("bot/core/proactive_monitor.py").read_text(encoding="utf-8"))
    body = src[src.index("def _check_black_swan"):src.index("def _severity_tier")]
    assert "by_kind" in body, (
        "anomalies are no longer grouped by type — every non-correlation type "
        "is back to one message per symbol")
    assert "keep_single" in body
    # and severe ones must be pulled OUT of the grouping
    assert "_HALT_SEVERITY" in body and "keep_single.append" in body, (
        "severe anomalies are being clustered; a 0.9 must never be digested "
        "beside a 0.2")


def test_the_cluster_names_every_symbol_it_folded_in():
    """Clustering must not lose information — the operator has to be able to
    see WHICH symbols, or the digest is strictly worse than the flood."""
    from tests.source_scan import code_only
    import pathlib

    src = code_only(pathlib.Path("bot/core/proactive_monitor.py").read_text(encoding="utf-8"))
    body = src[src.index("by_kind: dict = {}"):src.index("for alert_obj in keep_single")]
    assert "names = " in body and "sorted(" in body, "the cluster does not list its symbols"
    assert "Worst severity" in body, "the cluster hides how bad the worst member was"
    assert "len(group)" in body, "the cluster does not say how many it folded in"


def test_the_repeat_window_is_longer_than_the_check_interval():
    """A window shorter than the polling interval suppresses nothing. Stated as
    a relation, not two numbers, so tuning either cannot silently defeat it."""
    assert ProactiveMonitor.BLACK_SWAN_REPEAT > ProactiveMonitor.DEDUP_COOLDOWN
    assert ProactiveMonitor.BLACK_SWAN_REPEAT > ProactiveMonitor.CHECK_INTERVAL


def test_the_filter_is_actually_reached():
    """THE WIRING, WHICH EVERY TEST ABOVE MISSES.

    All of them call `_bs_is_news` directly, so they prove the function is
    correct and prove nothing about whether anything calls it. Deleting the
    call from `_check_black_swan` left this file entirely green — found by
    mutation, and it is the defect the whole repository is organised against:
    a control that works, reached by nothing.

    `_check_black_swan` needs a live engine with a populated detector to run,
    so the call site is checked structurally. That is the narrow case source
    scanning is for — a guard being REACHED, which is a property of the caller
    and invisible from inside the function.
    """
    from tests.source_scan import code_only
    import pathlib

    src = code_only(pathlib.Path("bot/core/proactive_monitor.py").read_text(encoding="utf-8"))
    body = src[src.index("def _check_black_swan"):src.index("def _severity_tier")]
    assert "_bs_is_news" in body, (
        "_check_black_swan no longer passes its alerts through _bs_is_news — "
        "every anomaly re-pages on the 5-minute dedup cooldown again")
    # and it must filter the RETURN, not merely mention the name somewhere
    assert "return [a for a in alerts if self._bs_is_news(a)]" in body, (
        "_bs_is_news is referenced but not applied to the returned alerts")


def test_the_filter_cannot_take_the_alert_system_down_with_it():
    """A crash in the NOISE FILTER must not silence the alarm.

    The first draft read `self._bs_last` directly, and `_bs_is_news` is called
    on `_check_black_swan`'s RETURN line — outside that method's try/except. So
    an AttributeError there propagated into `_check_all()`, whose caller
    swallows exceptions at debug level: a bug in the thing that removes noise
    would have removed EVERY alert, halts and gateway outages included, leaving
    a channel that looks calm because nothing can reach it.

    Found by an existing test building the monitor with `__new__` — a
    legitimate pattern here — not by any of the ten tests written for this
    change, all of which used a fully-constructed monitor.
    """
    m = ProactiveMonitor.__new__(ProactiveMonitor)      # no __init__, no _bs_last
    assert m._bs_is_news(_bs("bs_SPREAD_WIDENING_XLM/USDT")) is True
    # and having created its state on demand, it still suppresses the repeat
    assert m._bs_is_news(_bs("bs_SPREAD_WIDENING_XLM/USDT")) is False


def test_an_unclassifiable_alert_is_sent_not_eaten():
    """Any unforeseen error sends. The alternative is a filter that goes quiet
    on its own confusion, which is invisible from outside."""
    class _Exploding:
        alert_type = "BLACK_SWAN"
        dedup_key = "k"
        @property
        def severity(self):    # noqa: D401 - deliberately hostile
            raise RuntimeError("unreadable")
        @property
        def body(self):
            raise RuntimeError("unreadable")
    assert _mon()._bs_is_news(_Exploding()) is True
