"""A crashed escape planner told the operator the book was flat.

CLAUDE.md names `_cmd_escape` as one of the surfaces still building its card
inline, and inline is why none of this was findable: nothing could plant a
crashed planner and read what the operator would see.

Four defects, every one leaning the same way — toward calm:

  1. `escape_agent.plan()` returned the SAME document for "no positions" and
     for "an exception happened": `position_count: 0`, `risk: "none"`,
     `recommended: "no open positions — nothing to unwind"`. `/escape`
     rendered that as "🪂 no open positions to unwind" — an all-clear shown to
     an operator who opened this screen because something is wrong.

  2. `_RISK_ICON.get(report.get("risk", "none"), "⚪")`. The OUTER default is
     correct: ⚪ for a risk word nobody recognises. The INNER one guarantees it
     can never fire for the case that matters — an absent `risk` becomes the
     string "none", which IS in the map, so it comes out 🟢. A guard that
     works, applied to a set that excludes the case that hurts.

  3. `steps[:12]`, silently. An ORDERED emergency-exit plan truncated with
     nothing saying so: the operator runs twelve closes believing the book is
     then flat. The same cap was on `escape_payload`, which seals its record
     to the TAMPER-EVIDENT CHAIN — a permanent record of a partial plan,
     indistinguishable from a record of a whole one.

  4. `_book_risk(None)` — reached when NO position had a readable leverage, so
     nothing knew how close anything sat to liquidation — returned "none", the
     calmest verdict there is, on the exact evidence that it could not be
     assessed.

THE RED HERRING, planted below: a book that is genuinely flat. It produces the
same empty `steps` as a crash, and it is the one case that SHOULD say "nothing
to unwind". A fix that makes every empty plan shout is as wrong as one that
makes every empty plan reassure.
"""

from __future__ import annotations

import re

import pytest

from bot.formatters.escape_card import MAX_STEPS, render_escape_card, risk_icon
from bot.guardian import escape_agent
from bot.guardian.escape_agent import escape_payload, plan


def text(html_: str) -> str:
    return re.sub(r"<[^>]+>", "", html_)


def book(n: int, leverage=10):
    return [{"symbol": f"C{i}", "direction": "LONG", "entry": 100.0, "qty": 1.0,
             "cost_usd": 10.0, "leverage": leverage, "group": "*"} for i in range(n)]


# ── 1. a crash is not a flat book ───────────────────────────────────────────

class TestAFailedPlanIsNotAnAllClear:
    def test_the_planner_marks_a_failure(self, monkeypatch):
        monkeypatch.setattr(escape_agent, "_notional",
                            lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
        p = plan(book(3))
        assert p["ok"] is False
        assert p["risk"] is None, "a crashed plan reported an urgency"
        assert p["position_count"] is None, "a crashed plan counted positions"

    def test_a_flat_book_is_still_marked_ok(self):
        """THE RED HERRING. Same empty `steps`, opposite meaning."""
        p = plan([])
        assert p["ok"] is True
        assert p["risk"] == "none"
        assert p["position_count"] == 0

    @pytest.mark.parametrize("report", [None, {"ok": False, "steps": []}])
    def test_the_card_calls_a_failure_a_failure(self, report):
        out = text(render_escape_card(report))
        # MUST NOT SAY
        assert "no open positions" not in out.lower(), (
            f"a failure was rendered as an empty book:\n{out}")
        assert "nothing to plan" not in out.lower()
        # MUST SAY
        assert "could not be built" in out
        assert "not take it as an all-clear" in out
        assert "UNKNOWN" in out

    def test_the_card_still_says_flat_when_it_is_flat(self):
        out = text(render_escape_card(plan([])))
        assert "no open positions to unwind" in out
        assert "could not be built" not in out, (
            "a genuinely flat book was reported as a failure — a fix that "
            "makes every empty plan shout is as wrong as the defect")

    def test_the_failure_card_names_what_to_do_instead(self):
        out = text(render_escape_card(None))
        assert "/open_positions" in out and "/closeall" in out


# ── 2. colour is a claim ────────────────────────────────────────────────────

class TestUnknownUrgencyIsNotGreen:
    def test_none_risk_is_muted_not_green(self):
        assert risk_icon(None) == "⚪"

    def test_an_unrecognised_word_is_muted_too(self):
        assert risk_icon("catastrophic") == "⚪"

    @pytest.mark.parametrize("risk,icon", [("none", "🟢"), ("low", "🟡"),
                                           ("medium", "🟠"), ("high", "🔴")])
    def test_a_real_reading_keeps_its_colour(self, risk, icon):
        assert risk_icon(risk) == icon

    def test_an_unreadable_leverage_book_is_unknown_not_calm(self):
        # No position reports a leverage, so nothing knows how close this book
        # sits to liquidation. That is not the same as sitting far from it.
        p = plan(book(3, leverage=None))
        assert p["risk"] is None, "unmeasurable fragility reported as calm"
        out = text(render_escape_card(p))
        assert "⚪" in out and "UNKNOWN" in out
        assert "🟢" not in out.split("Execute with")[0]

    def test_the_unknown_card_still_offers_the_order(self):
        """The ORDER is still a real output — only the urgency is unknown. An
        honest card must not throw away the part it does know."""
        out = text(render_escape_card(plan(book(3, leverage=None))))
        assert "1. close" in out
        assert "the ORDER below still holds" in out

    def test_a_genuinely_urgent_book_still_reads_red(self):
        """CONTROL: the fix must not mute a real emergency."""
        p = plan(book(2, leverage=100))
        assert p["risk"] == "high"
        assert "🔴" in render_escape_card(p)


# ── 3. no silent caps ───────────────────────────────────────────────────────

class TestATruncatedExitPlanSaysSo:
    def test_the_card_names_the_total_and_the_remainder(self):
        out = text(render_escape_card(plan(book(20))))
        assert f"Showing the {MAX_STEPS} most urgent of 20" in out
        assert "8 more remain open after these closes" in out
        assert "not the whole book" in out

    def test_a_short_plan_says_nothing(self):
        # A caveat printed on every plan trains the reader to skip the one
        # that matters — the rule scan_coverage already states.
        out = text(render_escape_card(plan(book(3))))
        assert "Showing the" not in out

    def test_exactly_max_steps_is_not_truncated(self):
        out = text(render_escape_card(plan(book(MAX_STEPS))))
        assert "Showing the" not in out, "an untruncated plan claimed truncation"

    def test_one_over_is(self):
        out = text(render_escape_card(plan(book(MAX_STEPS + 1))))
        assert "1 more remain" in out

    def test_the_sealed_record_carries_the_total(self):
        """The chain entry is the permanent proof of what the plan WAS. A
        capped list with no total on a tamper-evident record is a partial plan
        that will read as a whole one forever."""
        ep = escape_payload(book(20))
        assert ep["order_total"] == 20
        assert ep["order_truncated"] == 8
        assert len(ep["order"]) == 12

    def test_the_sealed_record_marks_a_failed_plan(self, monkeypatch):
        monkeypatch.setattr(escape_agent, "_notional",
                            lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
        ep = escape_payload(book(3))
        assert ep["ok"] is False, (
            "the chain would seal a failed plan indistinguishably from a real one")
        assert ep["risk"] is None


# ── 4. the numbers ──────────────────────────────────────────────────────────

class TestNoDollarFigureIsInvented:
    def test_absent_totals_render_as_dashes_not_zero(self):
        out = text(render_escape_card(
            {"ok": True, "risk": "high", "position_count": None,
             "gross_notional_usd": None, "total_margin_usd": None,
             "steps": [{"order": 1, "symbol": "BTC", "direction": "LONG",
                        "reason": "x", "notional_usd": None,
                        "margin_freed_cum_usd": None, "liq_move_pct": None}]}))
        assert "$0" not in out, f"an unread figure printed as a measurement:\n{out}"
        assert "—" in out

    def test_a_real_zero_is_still_a_number(self):
        out = text(render_escape_card(
            {"ok": True, "risk": "none", "position_count": 0,
             "gross_notional_usd": 0.0, "total_margin_usd": 0.0,
             "steps": [{"order": 1, "symbol": "BTC", "direction": "LONG",
                        "reason": "x", "notional_usd": 0.0,
                        "margin_freed_cum_usd": 0.0, "liq_move_pct": None}]}))
        assert "$0" in out

    def test_an_absent_liq_distance_is_omitted_not_zeroed(self):
        # This one was already right — `if liq is not None` — and is pinned so
        # a later tidy-up does not "simplify" it into `or 0`.
        out = text(render_escape_card(plan(book(2, leverage=None))))
        # "% to liq" is the rendered token. Matching bare "to liq" also matched
        # "sits to liquidation" in the card's own caveat sentence — a test
        # asserting the absence of a substring has to pick one the prose cannot
        # accidentally contain.
        assert "% to liq" not in out, f"a liq distance appeared from nothing:\n{out}"
        assert "~0%" not in out


# ── the console makes the same claim ────────────────────────────────────────

class TestTheGuardianConsoleDoesNotDefaultToCalm:
    def _engine(self):
        from bot.core.engine import RuneClawEngine
        return RuneClawEngine.__new__(RuneClawEngine)

    def test_an_unassessable_book_has_no_posture(self):
        """`posture: "none"` was the console's fail-open default, and the whole
        book assessment sits inside a try/except — so a console that assessed
        nothing reported the calmest posture it has."""
        from bot.core import engine as _eng
        src = open(_eng.__file__, encoding="utf-8").read()
        assert '"posture": "none",' not in src, (
            "the console still defaults to the calmest posture")
        assert '"escape": {"risk": None},' in src

    def test_the_rollup_does_not_rank_unknown_as_safest(self):
        from bot.core import engine as _eng
        src = open(_eng.__file__, encoding="utf-8").read()
        assert "key=lambda r: order.get(r, 0)" not in src, (
            "an unknown risk still sorts as the safest input, so max() "
            "discards it in favour of any reading that happened to work")
        assert "known = [r for r in reads if r in order]" in src
