"""Two messages, same type, same second — and then again every thirty seconds.

FIRST ROUND (2026-08-18, morning). Two full ANOMALY NOTED cards at 11:57:26,
both SPREAD_WIDENING, differing only in symbol. Two causes that compounded:
`_check_black_swan` clustered exactly one anomaly type, and `DEDUP_COOLDOWN`
was 300s while anomalies are STANDING CONDITIONS, not events. Fixed by
clustering every type and adding BLACK_SWAN_REPEAT.

SECOND ROUND (same day, afternoon). It did not work, and the reason is the
part worth keeping:

  THE KEY CHURNED.   Mild clusters were keyed
                     `bs_CLUSTER_{type}_{count}_{names}` — deliberately, so a
                     new symbol joining an event would re-page. In a real
                     market-wide event the membership changes on EVERY 30-second
                     pass, so the key changed every pass, every cluster read as
                     a first sighting, and the 30-minute window never applied to
                     anything. Live: `PRICE_ACCELERATION x 31` at 16:27:47 and
                     `x 32` at 16:28:20. A suppression key must be stable across
                     exactly the churn its event produces, and that key was
                     built out of the churn.

  SEVERE WAS EXEMPT. `if tier >= 2: return True` — on the reasoning that the
                     one page which must always arrive must never be held. It
                     does always arrive, and then arrives again on every
                     cooldown for as long as the condition lasts. A spread stuck
                     at 12.9x baseline re-paged beside a second at 7.9x,
                     thirty-three seconds apart. Exempting the loudest alert
                     from noise control is how it stops being read.

  THE COUNT LIED.    `x 31` came from a list holding OPEN/USDT:USDT eight times
                     and INJ/USDT five. `active_alerts` holds one row per
                     DETECTION, and the message presented that as a count of
                     symbols — overstating the breadth of the event on the one
                     line an operator actually reads.

Now: every advisory anomaly, of every type, becomes ONE digest keyed on nothing
that churns. Severe still pages on its own card, immediately, deduplicated by
CONDITION (the hub for a correlation breakdown, the symbol otherwise) and
repeating at half the mild interval rather than never being held at all.

WHAT MUST NOT BE LOST. A severe anomaly is never folded into the digest, never
waits on a first sighting, and never waits on an escalation into its tier. The
digest states that severe alerts arrive separately, because a quiet channel
must not become a claim that the market is calm. And the filter fails OPEN —
anything it cannot classify is sent, because a suppression filter that silences
on its own confusion is invisible from the outside.
"""

from __future__ import annotations

import time
import types


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


def test_a_severe_anomaly_arrives_at_once_and_then_stops_repeating():
    """"NEVER SUPPRESSED" MEANT "METRONOME", AND THAT WAS THIS TEST'S FAULT.

    It looped five times demanding True each time, and the code obliged with an
    unconditional `if tier >= 2: return True`. In production that is a severe
    alert re-sent on every DEDUP_COOLDOWN for as long as the condition lasts —
    observed as a spread stuck at 12.9x baseline paging beside a second at
    7.9x, thirty-three seconds apart, both saying what they had already said.

    What must be unconditional is the FIRST page and any escalation into this
    tier. The ninth repeat of a standing condition is not news at any severity,
    and an operator who learns to skim severe alerts is the failure the
    exemption was written to prevent.
    """
    m = _mon()
    key = "bs_FLASH_CRASH_BTC/USDT"
    assert m._bs_is_news(_bs(key, tier=2)) is True, (
        "a severe anomaly did not page on first sight — the one page that must "
        "always arrive")
    for _ in range(5):
        assert m._bs_is_news(_bs(key, tier=2)) is False, (
            "a severe anomaly is re-paging unchanged, every 30-second pass")
    m._bs_last[key] = (time.time() - ProactiveMonitor.BLACK_SWAN_SEVERE_REPEAT - 1, 2)
    assert m._bs_is_news(_bs(key, tier=2)) is True, (
        "a severe condition still standing after its window went silent")


def test_severe_waits_less_than_mild():
    """Stated as a relation, so tuning either cannot invert the priority."""
    assert (ProactiveMonitor.BLACK_SWAN_SEVERE_REPEAT
            < ProactiveMonitor.BLACK_SWAN_REPEAT)
    assert ProactiveMonitor.BLACK_SWAN_SEVERE_REPEAT > ProactiveMonitor.CHECK_INTERVAL


def test_escalation_into_severe_ignores_the_window():
    m = _mon()
    key = "bs_SPREAD_WIDENING_GME/USDT:USDT"
    assert m._bs_is_news(_bs(key, tier=1)) is True
    assert m._bs_is_news(_bs(key, tier=1)) is False
    assert m._bs_is_news(_bs(key, tier=2)) is True, (
        "an anomaly that crossed into severe was held by the window it entered "
        "under — the escalation is the news")


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

def _an(symbol, kind, severity, *, desc="", action="MONITOR"):
    """One row of `black_swan.active_alerts`, shaped like the real detector's."""
    return types.SimpleNamespace(
        symbol=symbol, anomaly_type=kind, severity=severity,
        description=desc or f"{symbol} {kind.lower()} fired",
        recommended_action=action)


def _engine(rows):
    return types.SimpleNamespace(
        black_swan=types.SimpleNamespace(active_alerts=list(rows)))


# The burst reported from the live channel at 16:27:47 UTC, trimmed but keeping
# what mattered: many rows per symbol, two mild types, two severe singles.
LIVE_BURST = [
    _an("OPEN/USDT:USDT", "PRICE_ACCELERATION", 0.71, action="REDUCE_POSITION_SIZE"),
    _an("OPEN/USDT:USDT", "PRICE_ACCELERATION", 0.44),
    _an("OPEN/USDT:USDT", "PRICE_ACCELERATION", 0.31),
    _an("BTC/USDT", "PRICE_ACCELERATION", 0.35),
    _an("BTC/USDT", "PRICE_ACCELERATION", 0.33),
    _an("INJ/USDT", "PRICE_ACCELERATION", 0.30),
    _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.66),
    _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.51),
    _an("TAO/USDT", "SPREAD_WIDENING", 0.22),
    _an("TAO/USDT", "CORRELATION_BREAKDOWN", 0.46, action="REDUCE_POSITION_SIZE"),
    _an("BBSTOCK/USDT:USDT", "SPREAD_WIDENING", 1.00, action="HALT_NEW_TRADES"),
    _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98, action="HALT_NEW_TRADES"),
]


# ── the live failure, reproduced ─────────────────────────────────────────────

def test_a_standing_event_pages_once_not_once_every_thirty_seconds():
    """THE REPORTED DEFECT, DRIVEN END TO END.

    The previous fix clustered mild anomalies per type and keyed them
    `bs_CLUSTER_{type}_{count}_{names}` — deliberately, so that a new symbol
    joining an event would re-page. During a real market-wide event the
    membership changes on EVERY 30-second pass, so the key changed every pass,
    every cluster read as a first sighting, and the 30-minute window never
    applied to anything. Live: `PRICE_ACCELERATION x 31` at 16:27:47 and
    `x 32` at 16:28:20.

    A suppression key must be stable across exactly the churn the event
    produces. This drives three passes with the membership shifting the way it
    actually shifted and requires silence after the first.
    """
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    first = m._check_black_swan()
    assert first, "the first pass said nothing at all"

    # pass 2: one symbol joins, one leaves — the exact churn that defeated the
    # old key, and nothing an operator needs told again.
    m.engine = _engine(LIVE_BURST[1:] + [_an("XLM/USDT", "PRICE_ACCELERATION", 0.29)])
    assert m._check_black_swan() == [], (
        "the same standing event re-paged 30 seconds later because its dedup "
        "key carries the membership")
    # pass 3: churn again
    m.engine = _engine(LIVE_BURST[2:] + [_an("ACE/USDT", "SPREAD_WIDENING", 0.24)])
    assert m._check_black_swan() == []


def test_one_advisory_message_not_one_per_type():
    """Two mild types and a third with a single member used to be three
    messages. The operator's complaint was volume, and a per-type cluster is
    still per-type volume."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    mild = [a for a in m._check_black_swan() if a.severity != "CRITICAL"]
    assert len(mild) == 1, (
        f"{len(mild)} advisory messages for one market event; expected a digest")
    assert mild[0].dedup_key == "bs_DIGEST"


def test_the_digest_counts_symbols_not_detector_rows():
    """`x 31` came from a list holding OPEN/USDT:USDT eight times and INJ/USDT
    five. Presenting a count of DETECTIONS as a count of SYMBOLS overstates the
    breadth of the event on the one line an operator reads."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    body = [a for a in m._check_black_swan() if a.severity != "CRITICAL"][0].body
    # PRICE_ACCELERATION has 6 rows over 3 distinct symbols.
    assert "PRICE_ACCELERATION</code> — 3 symbols" in body, body
    # and no symbol is listed twice
    line = [ln for ln in body.split("\n") if "OPEN/USDT:USDT" in ln][0]
    assert line.count("OPEN/USDT:USDT") == 1, f"duplicated symbols: {line}"


def test_the_digest_reports_the_worst_of_each_type():
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    body = [a for a in m._check_black_swan() if a.severity != "CRITICAL"][0].body
    assert "<code>0.71</code> (OPEN/USDT:USDT)" in body
    assert "<code>0.66</code> (GME/USDT:USDT)" in body


# ── what the quiet must not cost ─────────────────────────────────────────────

def test_severe_is_never_folded_into_the_digest():
    """Burying a 1.00 beside a 0.22 is how the one that mattered gets skimmed
    past. Both severe rows must arrive as their own message."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    out = m._check_black_swan()
    crit = [a for a in out if a.severity == "CRITICAL"]
    assert len(crit) == 2, f"expected both severe singles, got {len(crit)}"
    keys = sorted(a.dedup_key for a in crit)
    assert keys == ["bs_SPREAD_WIDENING_BBSTOCK/USDT:USDT",
                    "bs_SPREAD_WIDENING_GME/USDT:USDT"], keys
    digest = [a for a in out if a.severity != "CRITICAL"][0]
    assert "1.00" not in digest.body and "0.98" not in digest.body, (
        "a severe severity leaked into the advisory digest")


def test_a_severe_symbol_pages_once_per_pass_not_once_per_detection():
    """`active_alerts` holds one row per DETECTION. GME appears twice at severe
    severity in the burst below; that is one condition, not two pages."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine([
        _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98, action="HALT_NEW_TRADES"),
        _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.91, action="HALT_NEW_TRADES"),
    ])
    crit = [a for a in m._check_black_swan() if a.severity == "CRITICAL"]
    assert len(crit) == 1, "one condition paged twice in a single pass"
    assert "0.98" in crit[0].body, "the milder detection won — report the worst"


def test_the_digest_says_what_the_quiet_means():
    """A collected digest could be read as "the market is calm". It must state
    that severe alerts still arrive separately, or the silence becomes a claim
    the code cannot support."""
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    body = [a for a in m._check_black_swan() if a.severity != "CRITICAL"][0].body
    assert "0.80+" in body and "separately" in body, body
    assert "not a claim that the market is calm" in body


def test_no_anomalies_means_no_message():
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine([])
    assert m._check_black_swan() == []
    m.engine = types.SimpleNamespace()          # no detector at all
    assert m._check_black_swan() == []


def test_a_broken_detector_cannot_take_the_alert_pipeline_down():
    """`_check_black_swan` is one line in `_check_all`. An exception here used
    to be swallowed at debug level by the loop's caller, so a crash in the
    anomaly path would have removed every other alert with it."""
    class _Exploding:
        @property
        def active_alerts(self):
            raise RuntimeError("detector is confused")
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = types.SimpleNamespace(black_swan=_Exploding())
    assert m._check_black_swan() == []


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

    DRIVEN NOW, NOT SCANNED. This was `assert "return [a for a in alerts if
    self._bs_is_news(a)]" in body` — the right property pinned to one exact
    spelling of one line, so splitting that return into two statements failed
    it while the filter was still applied. The docstring claimed
    `_check_black_swan` "needs a live engine", and the tests directly above
    this one had been building it with `__new__` all along.

    Two passes over an unchanged severe condition: the first is a first
    sighting and pages, the second is inside BLACK_SWAN_SEVERE_REPEAT and must
    not. That is what the scan was standing in for, and it cannot pass against
    a filter that is present but unreached.
    """
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine([
        _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98, action="HALT_NEW_TRADES"),
    ])
    first = [a for a in m._check_black_swan() if a.severity == "CRITICAL"]
    assert len(first) == 1, "a first sighting must page"
    second = [a for a in m._check_black_swan() if a.severity == "CRITICAL"]
    assert second == [], (
        "an unchanged severe condition re-paged on the next pass — "
        "_bs_is_news is not reached from _check_black_swan's return")


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


def test_the_card_renders_once_not_sixteen_times():
    """FOUND BY RENDERING IT, NOT BY READING IT.

    The body was built as

        "\U0001f6a8 <b>ANOMALY DETECTED</b>\n"
        "─" * 16 + "\n"

    and Python concatenates the adjacent literals BEFORE applying `*`, so the
    separator's repeat count was applied to the header too: sixteen copies of
    "ANOMALY DETECTED" above every severe card. Every assertion in this file
    passed — they all check for substrings, and sixteen of a thing contains one
    of it. Only printing the message showed it.
    """
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(LIVE_BURST)
    for a in m._check_black_swan():
        assert a.body.count("ANOMALY") == 1, (
            f"the header repeats {a.body.count('ANOMALY')} times in {a.title!r}")
        assert a.body.count("─" * 16) == 2, (
            "a card lost or multiplied its separator rules")


# ── the hourly budget, and what it used to be spent on ────────────────────

class TestTheBudgetIsChargedForWhatIsSent:
    """A STANDING CONDITION DRAINED THE WHOLE ALLOWANCE IN FOUR MINUTES.

    `apply_hourly_budget` assigned `self._bs_card_times` at BUILD time and
    `_bs_is_news` runs on this method's RETURN — afterwards. A severe condition
    that persists is rebuilt on every 30-second tick, so it was charged every
    tick and sent about once per BLACK_SWAN_SEVERE_REPEAT. Eight charges at
    eight per hour is four minutes; for the remaining 56 the room was zero and
    every genuinely NEW severe anomaly was demoted to the "+N more" line.

    That inverts what the budget is for. It exists so a market-wide event
    cannot page fifty times; it was instead letting the FIRST condition of the
    hour lock out every condition behind it — and lowering the number would
    have made that strictly worse, not better.
    """

    def test_an_unchanged_condition_is_charged_once_not_once_per_pass(self):
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m.engine = _engine([
            _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98, action="HALT_NEW_TRADES"),
        ])
        for _ in range(10):
            m._check_black_swan()
        assert len(m._bs_card_times) == 1, (
            f"ten passes over one unchanged condition charged "
            f"{len(m._bs_card_times)} against the hourly budget")

    def test_a_new_condition_still_pages_behind_a_standing_one(self):
        """THE COST, END TO END. Under the old charge the budget was gone and
        this card became a name in the overflow line."""
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        standing = _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98,
                       action="HALT_NEW_TRADES")
        m.engine = _engine([standing])
        for _ in range(20):          # 10 minutes of ticks at the old rate
            m._check_black_swan()
        m.engine = _engine([
            standing,
            _an("BTC/USDT:USDT", "VOLUME_COLLAPSE", 0.95, action="HALT_NEW_TRADES"),
        ])
        crit = [a for a in m._check_black_swan() if a.severity == "CRITICAL"]
        keys = {a.dedup_key for a in crit}
        assert any("BTC" in k for k in keys), (
            "a new severe condition was locked out by a standing one's repeats")

    def test_the_overflow_line_does_not_draw_on_the_budget(self):
        """It is one rate-limited message about N conditions, not a card each —
        charging it would let breadth eat the allowance for depth.

        EXACT, because `<= _SEVERE_CARDS_PER_HOUR` was not: nine conditions
        produce three cards under the per-tick cap plus one overflow line, and
        both 3 and 4 clear a cap of six. The mutation that charged the overflow
        survived that assertion.
        """
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m.engine = _engine([
            _an(f"S{i}/USDT:USDT", "SPREAD_WIDENING", 0.9, action="HALT_NEW_TRADES")
            for i in range(9)
        ])
        out = m._check_black_swan()
        cards = [a for a in out
                 if a.severity == "CRITICAL" and a.dedup_key != "bs_overflow"]
        overflow = [a for a in out if a.dedup_key == "bs_overflow"]
        assert overflow, "nine severe conditions must produce an overflow line"
        assert len(m._bs_card_times) == len(cards), (
            f"charged {len(m._bs_card_times)} for {len(cards)} card(s) beside "
            f"{len(overflow)} overflow line(s) — the overflow was charged")

    def test_the_allowance_recovers_once_the_window_passes(self):
        """A budget that never prunes is a budget spent once and gone.

        The prune was covered by nothing: every test above runs inside a single
        window, so replacing the filter with `list(_budget)` changed no result.
        Seeded with charges older than the window, a pass must drop them and
        still page.
        """
        import time as _time
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        stale = _time.time() - ProactiveMonitor._SEVERE_BUDGET_WINDOW - 60
        m._bs_card_times = [stale] * ProactiveMonitor._SEVERE_CARDS_PER_HOUR
        m.engine = _engine([
            _an("GME/USDT:USDT", "SPREAD_WIDENING", 0.98, action="HALT_NEW_TRADES"),
        ])
        crit = [a for a in m._check_black_swan() if a.severity == "CRITICAL"]
        assert crit, "an exhausted-but-expired allowance must not still block"
        assert all(t > stale for t in m._bs_card_times), (
            "charges older than the window were carried forward")


def test_the_budget_leaves_room_behind_one_persisting_condition():
    """WHY SIX AND NOT FOUR. BLACK_SWAN_SEVERE_REPEAT (900s) caps ONE unchanged
    condition at four cards an hour, so the budget is denominated in
    conditions. Four would be exactly one — a single standing anomaly starving
    every other symbol for the rest of the hour, which is the failure this
    commit removes. Six leaves a persisting condition plus two new ones.
    """
    per_hour = ProactiveMonitor._SEVERE_CARDS_PER_HOUR
    repeats_per_hour = 3600 // ProactiveMonitor.BLACK_SWAN_SEVERE_REPEAT
    assert per_hour > repeats_per_hour, (
        f"{per_hour}/hour is at most one persisting condition "
        f"({repeats_per_hour} cards/hour) with nothing left for a new one")
