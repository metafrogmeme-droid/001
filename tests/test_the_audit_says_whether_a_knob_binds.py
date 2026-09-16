"""The nightly audit says whether a governor knob would change anything.

A LIVE CARD ON 2026-09-16 proposed two changes to the live-performance
governor and could not say whether either did anything::

    Live window: 40 closes · win 22% · PF 0.6 · net $-18.85
    LIVE_PERF_REDUCE_WINRATE=0.55 … Apply: (env + restart)
    LIVE_PERF_REDUCE_MULT=0.25    … Apply: (env + restart)

Three separate things were wrong with that, and the third is the one that
makes the other two matter.

**THE STATE WAS GATHERED AND SHOWN TO NOBODY.** `gather_evidence` has called
`risk.live_performance_state()` into `ev["governor"]` since it was written,
and the module docstring advertises "governor/throttle state" as evidence —
but `render_report` printed no governor line at all. The word appeared twice
in the whole file: the docstring and the gather. So the status reached the
MODEL and never the human, and the human is who the card ends by instructing.

**THE WINDOW ON THE CARD IS NOT THE WINDOW THE KNOBS ACT ON.**
`gather_evidence` reads ``closed[-40:]`` — a hardcoded 40. The governor scores
``CONFIG.risk.live_perf_window``, 20 by default. Forty closes at 22.5% is
consistent with a most-recent-20 in PAUSE, in REDUCE or in OK, so no reader
could derive the governor's branch from the figures printed above the
proposals. That is `drawdown_source`'s lesson — *"an operator could read ~0%
from a gate that was refusing trades at 9%"* — pointed at the governor window.

**AND THE BRANCH DECIDES WHETHER A KNOB IS REACHED AT ALL.**
`LIVE_PERF_REDUCE_MULT` is the size applied in the REDUCE branch and is
reached nowhere else: in PAUSE the multiplier is 0.0, in WARMUP and OFF
nothing is applied. `LIVE_PERF_REDUCE_WINRATE` decides ENTRY to that branch
and sits in an ``or net < 0``, so on a net-negative window the branch is
entered whatever the bar says. Either proposal can be a change to a number
nobody reads, and the card held the state that would have said so.

WHAT THIS SUITE CLAIMS. That the branch has ONE definition three readers ask
(patch it and the engine's own answer moves); that the card prints the
governor's status, multiplier and its OWN window; that every governor
proposal carries a binding line which is a MEASUREMENT — `governor_verdict`
run twice over that window, as configured and with the candidate — and that
the states which apply no multiplier decline to quote one.
"""
from __future__ import annotations

from collections import deque
from unittest.mock import patch

import pytest

from bot.core.self_audit import SelfAudit, governor_line, proposal_binding
from bot.risk.live_perf_gate import (
    OFF,
    OK,
    PAUSE,
    REDUCE,
    WARMUP,
    governor_verdict,
)
from bot.risk.risk_engine import RiskEngine

# ── the branch, once ─────────────────────────────────────────────────────────

def _inp(**kw):
    base = dict(enabled=True, samples=20, win_rate=0.5, net=10.0,
                min_samples=10, pause_winrate=0.25, reduce_winrate=0.40,
                reduce_mult=0.5)
    base.update(kw)
    return base


@pytest.mark.parametrize("kw,want_mult,want_status", [
    # PAUSE is an AND and is tested FIRST: a window qualifying for both
    # returns 0.0, not the reduce multiplier.
    (dict(win_rate=0.20, net=-5.0), 0.0, PAUSE),
    # …and the AND is real: the same win rate with a positive net is REDUCE.
    (dict(win_rate=0.20, net=5.0), 0.5, REDUCE),
    # REDUCE is an OR — a net-negative window is in it however good the rate.
    (dict(win_rate=0.90, net=-5.0), 0.5, REDUCE),
    (dict(win_rate=0.30, net=5.0), 0.5, REDUCE),
    (dict(win_rate=0.90, net=5.0), 1.0, OK),
    # Below the floor: fail OPEN at 1.0, and the word is WARMUP, because a
    # cold start is not a healthy window.
    (dict(samples=3), 1.0, WARMUP),
    # An absent measurement is not a bad one.
    (dict(win_rate=None), 1.0, WARMUP),
    (dict(net=None), 1.0, WARMUP),
])
def test_the_branch_answers_each_state(kw, want_mult, want_status):
    mult, status = governor_verdict(**_inp(**kw))
    assert (mult, status) == (want_mult, want_status)


def test_enabled_decides_the_word_and_never_the_multiplier():
    """The caller tests the flag itself before applying any reduction, so the
    multiplier is scored for a disabled governor too and simply goes unused.
    Folding the flag into the multiplier would make a switched-off governor
    return 1.0 — a config state rendered as a measured healthy window."""
    on = governor_verdict(**_inp(win_rate=0.20, net=-5.0, enabled=True))
    off = governor_verdict(**_inp(win_rate=0.20, net=-5.0, enabled=False))
    assert on == (0.0, PAUSE)
    assert off == (0.0, OFF)      # same multiplier, different word


# ── one definition, three readers ───────────────────────────────────────────

def _eng(pnls):
    e = RiskEngine.__new__(RiskEngine)
    e._realized_pnl_window = deque(pnls)
    return e


def _cfg(enabled=True, window=20, min_samples=10,
         reduce_wr=0.40, pause_wr=0.25, reduce_mult=0.5):
    p = patch("bot.risk.risk_engine.CONFIG")
    m = p.start()
    m.risk.live_performance_governor_enabled = enabled
    m.risk.live_perf_window = window
    m.risk.live_perf_min_samples = min_samples
    m.risk.live_perf_reduce_winrate = reduce_wr
    m.risk.live_perf_pause_winrate = pause_wr
    m.risk.live_perf_reduce_mult = reduce_mult
    return p


def test_the_engine_asks_the_leaf_rather_than_agreeing_with_it():
    """A byte-identical copy agrees with every fixture and diverges on the
    first edit to either, which is exactly what a second copy looks like from
    outside. So the leaf is PATCHED and the engine's own answers are read: if
    they move, they came from it.
    """
    p = _cfg()
    try:
        with patch("bot.risk.risk_engine.governor_verdict",
                   return_value=(0.37, "INVENTED")) as fake:
            e = _eng([1.0] * 20)
            assert e.live_performance_size_multiplier == 0.37
            st = e.live_performance_state()
            assert st["multiplier"] == 0.37 and st["status"] == "INVENTED"
            assert fake.call_count == 2
    finally:
        p.stop()


def test_the_state_carries_the_window_its_figures_are_over():
    """The whole point: every other surface printing the bot's recent record
    picks its own span, and the audit's is 40. Without this the reader cannot
    tell that the governor scored a different set of closes."""
    p = _cfg(window=20)
    try:
        assert _eng([1.0] * 30).live_performance_state()["window"] == 20
    finally:
        p.stop()
    p = _cfg(window=7)
    try:
        s = _eng([1.0] * 30).live_performance_state()
        assert s["window"] == 7 and s["samples"] == 7
    finally:
        p.stop()


def test_the_fail_safe_snapshot_claims_no_window():
    """`window: None` rather than a plausible integer — the fail-safe branch
    measured nothing, and a span quoted there would be the one figure a
    reader trusts about a read that did not happen."""
    e = RiskEngine.__new__(RiskEngine)
    e._realized_pnl_window = None            # list(None) raises
    p = _cfg()
    try:
        assert e.live_performance_state()["window"] is None
    finally:
        p.stop()


# ── the card's governor line ────────────────────────────────────────────────

def _gov(status, mult, wr=0.3, net=-5.0, samples=20, window=20):
    return {"enabled": status != OFF, "samples": samples, "win_rate": wr,
            "net_pnl": net, "multiplier": mult, "status": status,
            "window": window}


def test_the_line_names_the_status_the_size_and_its_own_window():
    line = governor_line(_gov(PAUSE, 0.0, wr=0.15, net=-22.1))
    assert "PAUSE" in line
    assert "0.00" in line                       # the size it is applying
    assert "last 20 closes" in line             # ITS window, not the card's
    assert "15%" in line and "-22.10" in line


def test_an_unread_governor_is_not_a_switched_off_one():
    """Absent and OFF call for different actions — one is a redeploy or a
    broken engine, the other is a switch somebody set — so they get different
    sentences and neither says the other."""
    unread = governor_line(None)
    off = governor_line(_gov(OFF, 0.0))
    assert "could not be read" in unread
    assert "OFF" not in unread and "switched off" not in unread
    assert "switched off" in off
    assert "could not be read" not in off


def test_a_switched_off_governor_quotes_no_size():
    """OFF applies nothing, and ×0.00 is the single figure a reader takes as
    'sizing is stopped' — the opposite of what OFF means."""
    assert "×" not in governor_line(_gov(OFF, 0.0))


def test_warmup_says_it_measured_nothing():
    line = governor_line(_gov(WARMUP, 1.0, samples=4))
    assert "WARMUP" in line and "4 closes" in line
    assert "measures nothing" in line


def test_an_unreported_sample_count_is_not_zero_closes():
    """The mutation round found this one: every fixture carried a real
    `samples`, so `int(n or 0)` — "0 closes" for a count nobody reported —
    changed no verdict in the suite. It is the WARMUP branch, whose whole
    subject is how few closes there are, so a fabricated zero there is the
    most confident possible version of the wrong answer."""
    # ANCHORED TO THE FIELD'S OWN SEGMENT. The first draft of this asserted
    # `"0 closes" not in line` and failed on the WINDOW — "last 2*0 closes*"
    # contains it — which is this file's own recorded trap about asserting a
    # short string is absent. The count is everything before " in ".
    head = governor_line(_gov(WARMUP, 1.0, samples=None)).split(" in ")[0]
    assert "unreported" in head and "closes" not in head
    # and a bool is not a count, however much `isinstance(True, int)` says so
    head = governor_line(_gov(WARMUP, 1.0, samples=True)).split(" in ")[0]
    assert "unreported" in head and "closes" not in head


def test_a_status_the_card_does_not_know_is_not_rendered_as_healthy():
    line = governor_line(_gov("SOMETHING_NEW", 1.0))
    assert "not recognised" in line
    assert "OK" not in line


# ── the binding measurement ─────────────────────────────────────────────────

def _live(**kw):
    """The governor's OWN window, as `live_perf_inputs()` hands it over."""
    return _inp(**kw)


def test_the_reduce_multiplier_binds_only_inside_the_reduce_branch():
    # REDUCE: the knob IS the size being applied, so moving it moves sizing.
    binds = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25,
                             _live(win_rate=0.30, net=5.0))
    assert "binds" in binds and "0.50" in binds and "0.25" in binds
    # PAUSE: the multiplier is 0.0 and the reduce branch is never reached.
    paused = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25,
                              _live(win_rate=0.15, net=-5.0))
    assert "changes nothing" in paused and PAUSE in paused


def test_raising_the_bar_binds_nothing_on_a_net_negative_window():
    """The REDUCE test is an OR with `net < 0`, so a losing window is already
    in the branch whatever the bar says. This is the proposal the live card
    made, with the reasoning that the window was losing — which is precisely
    why it could not do anything."""
    out = proposal_binding("LIVE_PERF_REDUCE_WINRATE", 0.55,
                           _live(win_rate=0.15, net=-5.0))
    assert "changes nothing" in out


def test_raising_the_bar_binds_when_it_pulls_a_healthy_window_in():
    """And it is not inert in general: on a net-POSITIVE window whose rate
    sits between the old bar and the new one, it moves sizing."""
    out = proposal_binding("LIVE_PERF_REDUCE_WINRATE", 0.55,
                           _live(win_rate=0.50, net=5.0))
    assert "binds" in out and "1.00" in out and "0.50" in out


@pytest.mark.parametrize("status_kw", [
    dict(enabled=False),          # OFF
    dict(samples=2),              # WARMUP
])
def test_the_states_that_apply_nothing_quote_no_multiplier(status_kw):
    """The first draft of `proposal_binding` printed 'size stays ×0.00' under
    a governor reading OFF — a number quoted where nothing uses it. Found by
    rendering the card, not by reading the diff."""
    out = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25, _live(**status_kw))
    assert "changes nothing" in out
    assert "×" not in out


def test_an_unread_window_is_not_a_verdict_either_way():
    out = proposal_binding("LIVE_PERF_REDUCE_MULT", 0.25, None)
    assert "not checked" in out
    assert "binds" not in out and "changes nothing" not in out


def test_a_flag_that_is_not_a_governor_knob_gets_no_line():
    """A row printed on every proposal is a row readers learn to skip, and
    the question genuinely does not apply."""
    assert proposal_binding("SOME_OTHER_FLAG", 3, _live()) is None


# ── the card, end to end ────────────────────────────────────────────────────

_BASE = {"return_pct": 3.14, "pf": 1.87, "trades": 39}
_SUMMARY = {"n": 40, "scored": 40, "unpriced": 0, "win_rate": 0.225,
            "net_pnl": -18.85, "pf": 0.6}
_RESULTS = [{"flag": "LIVE_PERF_REDUCE_MULT", "value": 0.25,
             "rationale": "Performance is poor.", "measured": dict(_BASE)}]


def _render(ev):
    return SelfAudit.render_report(ev, _RESULTS, _BASE, "alts_1h")


def test_the_card_shows_the_state_it_gathers():
    out = _render({"summary": _SUMMARY,
                   "governor": _gov(REDUCE, 0.5, wr=0.3, net=293.0),
                   "governor_inputs": _live(win_rate=0.30, net=293.0)})
    assert "Governor:" in out and REDUCE in out
    assert "last 20 closes" in out
    # and the two windows are both on the card, each with its own figures
    assert "40 closes" in out


def test_a_build_that_reported_no_governor_prints_no_governor_line():
    """Membership, then the value. An absent key is an older build; `None` is
    a read that failed. Collapsing them is what let a bare `except: pass` in
    the gatherer render as silence."""
    out = _render({"summary": _SUMMARY})
    assert "Governor:" not in out


def test_a_failed_read_says_so_on_the_card():
    out = _render({"summary": _SUMMARY, "governor": None,
                   "governor_inputs": None})
    assert "Governor: <b>could not be read</b>" in out
    assert "not checked" in out


def test_the_binding_line_sits_between_the_verdict_and_the_instruction():
    """It is evidence, and `Apply:` is what the reader does with it. Below the
    instruction it would be read after the decision."""
    out = _render({"summary": _SUMMARY,
                   "governor": _gov(REDUCE, 0.5),
                   "governor_inputs": _live(win_rate=0.30, net=5.0)})
    bind = out.index("↳")
    apply_at = out.index("Apply:")
    verdict = out.index("NOT DISTINGUISHED")
    assert verdict < bind < apply_at


def test_the_gatherer_records_an_unreadable_governor_as_unreadable():
    """Not an absent key. The engine has a risk object; asking it raised."""
    class _Raises:
        def live_performance_state(self):
            raise RuntimeError("boom")

        def live_perf_inputs(self):          # pragma: no cover - unreachable
            raise RuntimeError("boom")

    class _Engine:
        risk = _Raises()
        live_executor = None
        analyzer = None

    ev = SelfAudit().gather_evidence(_Engine())
    assert "governor" in ev and ev["governor"] is None
    assert ev["governor_inputs"] is None
