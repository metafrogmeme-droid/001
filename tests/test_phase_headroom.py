"""A cap you can only observe by breaching it is a cliff, not a budget.

_phase recorded ONLY the timeout. So a phase sitting at 299s of its 300s cap
looked exactly like one taking 30s, right up until the tick died. That is the
blind spot the 2026-07-29 incident lived in: the analyze phase had presumably
been creeping toward the cap, and the first signal anyone received was
thirty-seven consecutive tick failures and a warning-rate breaker rejecting
live trades.

It is also the only way to VERIFY a latency fix. #971 cut the serial worst
case of one analysis from ~195s to ~60s. "The breaches stopped" is equally
consistent with the fix working and with today's load simply being lighter;
peak-vs-cap tells them apart.

Peak rather than mean: a mean hides the tail, and the tail is what trips a
cap.
"""

import pytest

from bot.formatters.rich_cards import render_status_card


class _Rec:
    """The two methods under test, lifted off the engine so this needs no
    exchange, no event loop and no config."""

    def __init__(self):
        self._phase_durations = {}

    _record_phase_duration = None  # bound below
    phase_headroom = None


@pytest.fixture
def eng():
    from bot.core.engine import RuneClawEngine
    e = _Rec()
    e._record_phase_duration = RuneClawEngine._record_phase_duration.__get__(e)
    e.phase_headroom = RuneClawEngine.phase_headroom.__get__(e)
    return e


# ── recording ─────────────────────────────────────────────────────────────

def test_nothing_measured_is_not_a_margin_of_one_hundred_percent(eng):
    assert eng.phase_headroom() is None


def test_a_successful_phase_is_recorded(eng):
    eng._record_phase_duration("analyze", 42.0, 300.0)
    h = eng.phase_headroom()
    assert h["phase"] == "analyze"
    assert h["last_s"] == 42.0 and h["peak_s"] == 42.0
    assert h["used_ratio"] == pytest.approx(0.14)


def test_the_peak_survives_a_faster_run(eng):
    """The tail is what trips a cap; a quiet tick afterwards must not erase
    the one that nearly died."""
    eng._record_phase_duration("analyze", 280.0, 300.0)
    eng._record_phase_duration("analyze", 12.0, 300.0)
    h = eng.phase_headroom()
    assert h["peak_s"] == 280.0, "a mean or a last-value would hide the tail"
    assert h["last_s"] == 12.0, "...while still reporting the current state"


def test_the_tightest_phase_wins_not_the_slowest(eng):
    """Caps differ per phase, so raw seconds rank the wrong thing."""
    eng._record_phase_duration("scan", 100.0, 300.0)      # 33%
    eng._record_phase_duration("positions", 40.0, 45.0)   # 89%, and faster
    assert eng.phase_headroom()["phase"] == "positions"


def test_a_capless_phase_is_skipped_not_divided_by_zero(eng):
    eng._record_phase_duration("analyze", 10.0, 0.0)
    assert eng.phase_headroom() is None


def test_recording_never_breaks_a_tick(eng):
    """Instrumentation is not worth a dead loop."""
    eng._record_phase_duration("analyze", float("nan"), 300.0)  # no raise
    eng._phase_durations = None
    eng._record_phase_duration("analyze", 1.0, 300.0)           # no raise
    assert eng.phase_headroom() is None


def test_the_record_is_bounded_by_the_phase_names(eng):
    for i in range(500):
        eng._record_phase_duration(f"phase{i % 3}", float(i), 300.0)
    assert len(eng._phase_durations) == 3


# ── the phase hook records on SUCCESS ─────────────────────────────────────

def test_phase_records_on_the_success_path():
    from pathlib import Path
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    block = src[src.index("async def _phase(self, coro"):
                src.index("def _record_phase_duration")]
    assert "_record_phase_duration(what, time.monotonic() - _started, cap)" in block
    # ...inside the try, before the except — a duration recorded after the
    # handler would only ever be the cap again.
    assert block.index("_record_phase_duration") < block.index("except asyncio.TimeoutError")


# ── the card ──────────────────────────────────────────────────────────────

def _card(**kw):
    base = dict(mode="PAPER", active=True, equity=1000.0, open_positions=0,
                daily_pnl=0.0, drawdown=0.0, max_drawdown=7.0,
                market_bias="neutral")
    base.update(kw)
    return render_status_card(**base)


def test_the_card_omits_the_line_until_something_has_run():
    assert "Slowest tick phase" not in _card()
    assert "Slowest tick phase" not in _card(phase_headroom={})


def test_the_card_reports_peak_against_cap():
    out = _card(phase_headroom={"phase": "analyze", "peak_s": 128.0,
                                "cap_s": 300.0, "last_s": 40.0,
                                "used_ratio": 128.0 / 300.0})
    assert "analyze" in out
    assert "128s" in out and "300s" in out
    assert "43%" in out


def test_the_card_flags_a_phase_near_its_cap():
    tight = _card(phase_headroom={"phase": "analyze", "peak_s": 270.0,
                                  "cap_s": 300.0, "last_s": 270.0,
                                  "used_ratio": 0.9})
    roomy = _card(phase_headroom={"phase": "analyze", "peak_s": 30.0,
                                  "cap_s": 300.0, "last_s": 30.0,
                                  "used_ratio": 0.1})
    assert "⚠" in tight.split("Slowest tick phase")[1].split("\n")[0]
    assert "⚠" not in roomy.split("Slowest tick phase")[1].split("\n")[0]


def test_headroom_and_the_breach_are_both_shown():
    """They answer different questions — 'did it die' and 'is it about to'."""
    out = _card(phase_timeout={"phase": "analyze", "cap_s": 300.0, "count": 37},
                phase_headroom={"phase": "scan", "peak_s": 90.0, "cap_s": 300.0,
                                "last_s": 90.0, "used_ratio": 0.3})
    assert "Tick phase timed out" in out
    assert "Slowest tick phase" in out


def test_the_status_command_passes_it():
    import inspect
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_status)
    assert "phase_headroom" in src


def test_both_labels_are_translated():
    from bot.utils.i18n import t
    for key in ("lbl_phase_headroom", "val_peak_of"):
        for lang in ("en", "zh"):
            assert t(key, lang) and t(key, lang) != key


def test_no_dollar_amount_reaches_this_line():
    """Seconds and a percentage. The headroom line is a latency fact, and
    latency facts carry no currency."""
    out = _card(phase_headroom={"phase": "analyze", "peak_s": 128.0,
                                "cap_s": 300.0, "last_s": 40.0,
                                "used_ratio": 0.43})
    line = out.split("Slowest tick phase")[1].split("\n")[0]
    assert "$" not in line


# ── a phase that DIES must be the one reported ────────────────────────────
#
# Production, 2026-07-29 10:22 UTC, with everything above already shipped:
#
#     - Tick phase timed out: analyze (exceeded its 300s, x5)
#     - Slowest tick phase: scan 2s peak of 300s (1%)
#
# _record_phase_duration ran only on the success path, so a phase that ALWAYS
# times out never entered the record and phase_headroom() reported the slowest
# of the SURVIVORS. Read alone — which is how a one-line summary gets read —
# the second line says 99% of the budget is spare while the first says the
# loop is dying. The instrument built to show the margin was reassuring about
# the exact phase that was failing.

def test_a_timed_out_phase_is_recorded_at_all(eng):
    eng._record_phase_duration("analyze", 300.0, 300.0, timed_out=True)
    h = eng.phase_headroom()
    assert h is not None, "a phase that only ever dies must still be reported"
    assert h["phase"] == "analyze"


def test_the_dying_phase_outranks_a_healthy_one(eng):
    eng._record_phase_duration("scan", 2.0, 300.0)
    eng._record_phase_duration("analyze", 300.0, 300.0, timed_out=True)
    h = eng.phase_headroom()
    assert h["phase"] == "analyze" and h["used_ratio"] >= 1.0, (
        "reporting scan at 1% while analyze blows its cap is the failure this "
        "test exists for")


def test_the_timeout_flag_survives_a_later_success(eng):
    """Sticky on purpose: one timeout in the window is what an operator needs
    to see, and a later success does not un-happen it."""
    eng._record_phase_duration("analyze", 300.0, 300.0, timed_out=True)
    eng._record_phase_duration("analyze", 40.0, 300.0)
    assert eng.phase_headroom()["timed_out"] is True


def test_phase_records_the_timeout_before_re_raising():
    from pathlib import Path

    from tests.source_scan import code_only

    # code_only, because the comments in this block contain the word "raised"
    # and an ordering assertion against "raise" matched it. Fifth instance of
    # a source-scanning test failing on its own explanation.
    src = code_only(Path("bot/core/engine.py").read_text(encoding="utf-8"))
    # Anchor on _phase itself — engine.py has several TimeoutError handlers
    # and the first one belongs to a different function.
    block = src[src.index("async def _phase(self, coro"):
                src.index("def _record_phase_duration")]
    block = block[block.index("except asyncio.TimeoutError:"):]
    assert '_rec(what, cap, cap, timed_out=True)' in block
    assert block.index("_rec(what") < block.index("raise")
    # Guarded, because this is the FAILURE path: an error from a recorder must
    # not replace a phase timeout the tick machinery knows how to handle with
    # something it has never seen.
    assert 'getattr(self, "_record_phase_duration", None)' in block
    assert "except Exception:" in block


def test_the_card_marks_the_figure_as_a_floor():
    """A cancelled phase ran for AT LEAST the cap. The true duration was never
    observed, so the card must not present the cap as a measurement."""
    out = _card(phase_headroom={"phase": "analyze", "peak_s": 300.0, "cap_s": 300.0,
                                "last_s": 300.0, "used_ratio": 1.0, "timed_out": True})
    line = out.split("Slowest tick phase")[1].split("\n")[0]
    assert "≥" in line, "the cap is a floor on the duration, not a reading of it"
    assert "cap hit" in line
    assert "⚠" in line


def test_a_healthy_phase_carries_no_floor_marker():
    out = _card(phase_headroom={"phase": "scan", "peak_s": 2.0, "cap_s": 300.0,
                                "last_s": 2.0, "used_ratio": 0.0067,
                                "timed_out": False})
    line = out.split("Slowest tick phase")[1].split("\n")[0]
    assert "≥" not in line and "cap hit" not in line


def test_the_cap_hit_label_is_translated():
    from bot.utils.i18n import t
    for lang in ("en", "zh"):
        assert t("val_cap_hit", lang) and t("val_cap_hit", lang) != "val_cap_hit"
