"""The nightly audit's "pass" was the else-branch of `if not results:`.

THE CARD THAT PROMPTED THIS, from the live bot on 2026-09-08::

    🧾 Nightly self-audit
    ────────────────
    Live window: 40 closes · win 28% · PF 0.4 · net $-26.86
    Shadow book: MTF_ALIGNMENT is the costliest gate (net +4.1R over 97
                 blocked trades)

    No changes proposed — the evidence supports the current configuration.
    (An empty audit is a pass, not a failure.)

Three separate claims in eight lines, and none of them was measured.

1. **"the evidence supports the current configuration"** was printed without
   consulting the evidence. It is the `else` of `if not results:` — a literal
   string reached whenever the proposal list is empty — and it sat directly
   under a window at PF 0.4 and net -$26.86. `window_reading()` is the reading
   now, with four outcomes, and `no_change_verdict()` the sentence.

2. **"No changes proposed"** could not tell a proposal of nothing from a reply
   nobody could read. `parse_llm_json` returned `[]` "when unparseable" *by its
   own docstring*, so a truncated reply, a refusal or a page of prose became an
   endorsement. It answers `None` for those now — the same
   unreadable-is-not-a-measurement rule the evidence gatherer one function up
   already applies to the trade record.

3. **"is the costliest gate"** keyed on `net_r > 0.5`, a bar on a TOTAL.
   +4.1R over 97 blocked trades is **+0.042R per trade**. That is the
   voter-weights defect ("62% of 34 voters" is 21 of 34, a coin flip) in a
   second module, on the scoreboard that decides which risk gate to loosen on
   a live account. `shadow_book.mean_r_interval` bounds the per-trade figure;
   the claim needs the whole interval clear of zero.

A fourth outcome fell out of writing it: proposals that were made and then
dropped at validation (unknown flag, out of bounds, already in force) also
landed on "the evidence supports the current configuration" — the audit
reporting a rejection of its own output as an endorsement.
"""

import asyncio
import math
import types

import pytest

from bot.core.self_audit import (
    ALLOWED_FLAGS,
    SelfAudit,
    costliest_gate_line,
    no_change_verdict,
    parse_llm_json,
    validate_proposals,
    window_reading,
)
from bot.core.shadow_book import (
    MIN_GATE_TRADES,
    ShadowBook,
    mean_r_interval,
)

#: The live window off the card above.
LOSING = {"n": 40, "scored": 40, "unpriced": 0, "win_rate": 0.28,
          "pf": 0.4, "net_pnl": -26.86}
SOUND = {"n": 40, "scored": 40, "unpriced": 0, "win_rate": 0.58,
         "pf": 2.1, "net_pnl": 310.40}


# ── 1. the window is read before it is endorsed ───────────────────────────

class TestWindowReading:
    def test_the_card_that_started_this_is_losing(self):
        assert window_reading(LOSING) == "losing"

    def test_a_profitable_window_is_sound(self):
        assert window_reading(SOUND) == "sound"

    def test_an_unreadable_record_is_its_own_answer(self):
        assert window_reading({"error": "closed_trade_record_unreadable"}) \
            == "unreadable"

    def test_no_window_at_all_is_thin_not_sound(self):
        assert window_reading({}) == "thin"
        assert window_reading(None) == "thin"
        assert window_reading({"n": 0}) == "thin"

    def test_a_window_nobody_could_price_is_thin(self):
        """40 closes of which 0 carry a P&L is not a 40-close record."""
        assert window_reading({"n": 40, "scored": 0, "unpriced": 40,
                               "win_rate": None, "pf": None,
                               "net_pnl": None}) == "thin"

    def test_missing_coverage_is_not_full_coverage(self):
        """`scored` absent means unknown, and unknown clears no floor."""
        assert window_reading({"n": 40, "pf": 3.0, "net_pnl": 100.0}) == "thin"

    def test_a_flat_book_is_not_read_as_losing(self):
        """`is not None`, not falsiness: 0.0 is a measured break-even."""
        assert window_reading({"n": 40, "scored": 40, "net_pnl": 0.0,
                               "pf": 1.0}) == "sound"

    def test_a_losing_pf_counts_even_when_the_net_is_positive(self):
        assert window_reading({"n": 40, "scored": 40, "net_pnl": 5.0,
                               "pf": 0.9}) == "losing"

    def test_a_losing_net_counts_on_its_own(self):
        """Each half of the `or` driven alone.

        `LOSING` trips both (PF 0.4 AND net -$26.86), so the mutation
        disabling the NET half survived every assertion in this class — the
        PF half kept answering for it. A window whose PF could not be
        computed but whose net is clearly negative is an ordinary shape:
        `profit_factor` returns None when there is no gross loss to divide by.
        """
        assert window_reading({"n": 40, "scored": 40, "net_pnl": -50.0,
                               "pf": None}) == "losing"
        # And the reverse: PF alone, with the net unreadable.
        assert window_reading({"n": 40, "scored": 40, "net_pnl": None,
                               "pf": 0.7}) == "losing"


# ── 2. four outcomes where there was one ──────────────────────────────────

class TestNoChangeVerdict:
    def test_a_losing_window_is_never_endorsed(self):
        out = no_change_verdict(LOSING, [])
        assert "not an endorsement" in out
        assert "the evidence supports the current configuration" not in out, (
            "the card endorsed a config it never read, over PF 0.4")

    def test_it_says_what_the_audit_could_not_rule_out(self):
        """A heuristic is never a verdict: 12 flags is not the whole system."""
        out = no_change_verdict(LOSING, [])
        assert str(len(ALLOWED_FLAGS)) in out
        assert "rules out no cause outside them" in out

    def test_an_unreadable_reply_is_a_failed_audit(self):
        out = no_change_verdict(SOUND, None)
        assert "could not be read" in out
        assert "failed audit" in out
        for endorsement in ("the evidence supports the current configuration",
                            "is a pass"):
            assert endorsement not in out, (
                f"an unreadable reply rendered as {endorsement!r}")

    def test_proposals_dropped_at_validation_are_not_an_endorsement(self):
        out = no_change_verdict(SOUND, [{"flag": "NOT_A_FLAG", "value": 1},
                                        {"flag": "TREND_UP_SIZE_MULT",
                                         "value": 99}])
        assert "2 change(s) proposed" in out
        assert "dropped before measurement" in out
        assert "the evidence supports the current configuration" not in out

    def test_a_thin_record_is_neither_a_pass_nor_a_failure(self):
        out = no_change_verdict({"n": 4, "scored": 4, "pf": 3.0,
                                 "net_pnl": 12.0}, [])
        assert "too thin" in out
        assert "the evidence supports the current configuration" not in out

    def test_a_sound_window_with_nothing_proposed_is_still_a_pass(self):
        """The fix must not turn every quiet night into an alarm."""
        out = no_change_verdict(SOUND, [])
        assert "the evidence supports the current configuration" in out
        assert "not an endorsement" not in out

    def test_an_unreadable_window_does_not_borrow_the_pass(self):
        out = no_change_verdict({"error": "closed_trade_record_unreadable"}, [])
        assert "could not be read" in out
        assert "the evidence supports the current configuration" not in out


# ── 3. the gate line has a bar ────────────────────────────────────────────

def _gate(n, net_r, sum_r2, **over):
    """A gate row shaped exactly as `gate_report()` builds one."""
    row = {"n": n, "net_r": net_r, "sum_r2": sum_r2,
           "avg_r": round(net_r / n, 3), "wins": 0, "losses": 0}
    iv = mean_r_interval(n, net_r, sum_r2)
    row["lower_r"] = None if iv is None else iv[0]
    row["upper_r"] = None if iv is None else iv[1]
    if iv is None or n < MIN_GATE_TRADES:
        row["verdict"] = None
    elif iv[0] > 0:
        row["verdict"] = "eating_edge"
    elif iv[1] < 0:
        row["verdict"] = "saving"
    else:
        row["verdict"] = "undistinguished"
    row.update(over)
    return row


class TestCostliestGateLine:
    def test_the_live_card_is_not_distinguishable_from_noise(self):
        line = costliest_gate_line({"MTF_ALIGNMENT": _gate(97, 4.1, 210.0)})
        assert "costliest gate" not in line
        assert "not distinguishable from noise" in line
        assert "No gate is established" in line

    def test_a_real_per_trade_effect_is_still_named(self):
        # 30 trades averaging +0.9R with little spread: a genuine finding.
        line = costliest_gate_line({"CORRELATION": _gate(30, 27.0, 26.0)})
        assert "is the costliest gate" in line
        assert "CORRELATION" in line

    def test_the_per_trade_figure_is_always_shown(self):
        """A total beside a count that no reader can divide is the defect."""
        for row in (_gate(97, 4.1, 210.0), _gate(30, 27.0, 26.0)):
            assert "R/trade" in costliest_gate_line({"G": row})

    def test_a_thin_sample_is_not_certainty_even_at_zero_spread(self):
        """Three blocked trades all at exactly +1.8R have sd 0.

        The interval collapses to a point and would read as a lower bound of
        +1.8R/trade. The sd is zero because the sample is degenerate, which is
        why MIN_GATE_TRADES exists beside the interval rather than instead
        of it.
        """
        row = _gate(3, 5.4, 3 * 1.8 * 1.8)
        assert row["lower_r"] == pytest.approx(1.8)
        line = costliest_gate_line({"TINY": row})
        assert "costliest gate" not in line
        assert "not established" in line

    def test_an_established_gate_beats_a_larger_unestablished_total(self):
        """"Costliest" ranks by total; only an established gate is actionable.

        `gate_report` sorts by net_r, so the top row is the biggest total. When
        that row is noise and a smaller one is real, naming the top row would
        point the operator at the gate with LESS evidence behind it.
        """
        line = costliest_gate_line({
            "NOISY": _gate(97, 4.1, 210.0),        # bigger total, no effect
            "REAL": _gate(30, 27.0, 26.0),         # smaller total, established
        })
        assert "REAL" in line and "is the costliest gate" in line

    def test_a_gate_that_saved_money_is_not_accused(self):
        line = costliest_gate_line({"SAVER": _gate(40, -30.0, 40.0)})
        assert line is None, "a gate blocking losers was named as costing edge"

    def test_an_unreadable_scoreboard_says_so(self):
        line = costliest_gate_line(None)
        assert line is not None and "could not be read" in line

    def test_an_empty_ledger_is_silent(self):
        assert costliest_gate_line({}) is None

    def test_a_row_with_no_interval_is_not_promoted(self):
        """An older or hand-written row carrying no bound makes no claim."""
        row = {"n": 50, "net_r": 12.0, "avg_r": 0.24}
        line = costliest_gate_line({"OLD": row})
        assert "costliest gate" not in line
        assert "not established" in line

    def test_a_verdict_without_its_bound_is_not_quoted(self):
        """`gate_report` never emits this pair; a stale cache can.

        The line quotes `lower_r`, so promoting a row that claims
        `eating_edge` with no bound behind it reads a number off a row that
        has none — and the first draft indexed it directly, which is a
        KeyError on the card rather than a wrong one.
        """
        row = {"n": 50, "net_r": 12.0, "avg_r": 0.24,
               "verdict": "eating_edge", "lower_r": None}
        line = costliest_gate_line({"BAD": row})
        assert line is not None
        assert "costliest gate" not in line

    def test_a_row_with_nothing_readable_on_it_does_not_print_a_zero(self):
        """Every figure `is None`-tested rather than coerced."""
        line = costliest_gate_line({"X": {"net_r": 3.0, "verdict": None}})
        assert "over ? blocked trades" in line, "an absent count printed raw"
        assert "avg —" in line, "an absent per-trade figure printed as 0.000"

    def test_a_non_finite_or_junk_figure_is_treated_as_unreadable(self):
        """`json.loads` accepts `Infinity` and `NaN` by default, so a corrupt
        scan cache can put either on a row. Neither is a measurement."""
        for junk in (float("inf"), float("nan"), "n/a", True, [1]):
            line = costliest_gate_line({"X": {"n": 40, "net_r": junk,
                                              "verdict": None}})
            assert line is None, f"{junk!r} was read as a net R"
        # And on the count, where it must not clear or fail the sample floor
        # by accident.
        line = costliest_gate_line({"X": {"n": float("nan"), "net_r": 5.0,
                                          "avg_r": 0.1, "verdict": None}})
        assert line is not None and "no interval could be computed" in line

    def test_a_row_with_no_net_r_is_not_reported_as_blocking_winners(self):
        assert costliest_gate_line({"B": {"n": 40, "avg_r": 0.1,
                                          "verdict": None}}) is None

    def test_an_undistinguished_verdict_with_no_interval_does_not_crash(self):
        """The other contradictory pair, and the one that RAISES.

        The line formats `lower_r` and `upper_r` directly. A row claiming
        `undistinguished` with neither is a TypeError inside the format
        string — outside the try, so it takes the whole card down rather than
        one line of it. Every fixture in this file computes an interval, so
        the mutation dropping that guard survived until this drove it.
        """
        line = costliest_gate_line({"X": {
            "n": 40, "net_r": 5.0, "avg_r": 0.125,
            "verdict": "undistinguished", "lower_r": None, "upper_r": None}})
        assert line is not None
        assert "not established" in line
        assert "None" not in line

    def test_a_row_with_no_count_does_not_claim_one(self):
        """`_num` answering 0.0 for an absent figure would print `None
        blocked trade(s), fewer than the 10 needed` — a sample floor applied
        to a sample nobody counted."""
        line = costliest_gate_line({"X": {"net_r": 3.0, "verdict": None}})
        assert "blocked trade(s), fewer than" not in line, (
            "an absent count was measured against the sample floor")
        assert "no interval could be computed" in line
        assert "None" not in line

    def test_an_established_row_with_no_total_prints_a_dash_not_a_zero(self):
        """The ONE path into `_gate_stat` that is not already net-guarded.

        `costliest_gate_line` checks `net_r is not None` before describing the
        top of the sort — but the ESTABLISHED branch selects on `verdict` and
        `lower_r`, neither of which implies a total. So a row with a verdict
        and no `net_r` reaches the formatter, and the mutation restoring
        `float(net or 0)` there survived until this drove it: the card would
        have read `net +0.0R`, a measured break-even, on the gate it was
        simultaneously naming as the costliest.
        """
        line = costliest_gate_line({"G": {
            "n": 40, "avg_r": 0.4, "lower_r": 0.18, "upper_r": 0.62,
            "verdict": "eating_edge"}})
        assert "is the costliest gate" in line
        assert "net —" in line, "an absent total rendered as a measured zero"
        assert "+0.0R" not in line


# ── 4. the interval itself ────────────────────────────────────────────────

class TestMeanRInterval:
    def test_it_brackets_the_mean(self):
        lo, hi = mean_r_interval(50, 25.0, 100.0)
        assert lo < 0.5 < hi

    def test_one_sample_has_no_interval(self):
        assert mean_r_interval(1, 2.0, 4.0) is None
        assert mean_r_interval(0, 0.0, 0.0) is None

    def test_a_zero_variance_sample_collapses_to_a_point(self):
        lo, hi = mean_r_interval(20, -20.0, 20.0)     # every r == -1.0
        assert lo == pytest.approx(-1.0) and hi == pytest.approx(-1.0)

    def test_float_cancellation_does_not_produce_a_negative_variance(self):
        """`sum_r2 - n*mean^2` lands just BELOW zero on a constant sample.

        Not hypothetical and not rare: sweeping the rounded accumulators
        `gate_report` actually stores (`round(net_r, 3)`, `round(sum_r2, 6)`)
        over n in 2..500 and a handful of realistic R values finds 802 inputs
        where the raw subtraction is negative. This is one of them — three
        blocked trades that all took profit at +1.73R, which is an ordinary
        row — and without the clamp it is `sqrt` of a negative, so the whole
        interval comes back None and the gate silently loses its verdict.

        The first draft of this test used n=400 at r=0.1, where the
        cancellation happens to land on exactly 0.0, and the mutation removing
        the clamp SURVIVED it.
        """
        n, r = 3, 1.73
        sum_r = round(n * r, 3)
        sum_r2 = round(n * r * r, 6)
        assert (sum_r2 - n * (sum_r / n) ** 2) < 0, (
            "this input no longer exercises the clamp — pick another")
        out = mean_r_interval(n, sum_r, sum_r2)
        assert out is not None, "a constant sample lost its interval to float noise"
        assert all(math.isfinite(v) for v in out)
        assert out[0] == pytest.approx(r) and out[1] == pytest.approx(r)

    def test_garbage_answers_none_rather_than_raising(self):
        assert mean_r_interval(10, None, 4.0) is None      # type: ignore[arg-type]
        assert mean_r_interval(10, "x", 4.0) is None       # type: ignore[arg-type]

    def test_a_wider_sample_narrows_the_interval(self):
        """The whole point: the same mean with more evidence says more."""
        narrow = mean_r_interval(200, 40.0, 208.0)   # mean 0.2
        wide = mean_r_interval(10, 2.0, 10.4)        # mean 0.2, same spread
        assert narrow is not None and wide is not None
        assert narrow[0] > wide[0]


# ── 5. driven through the real ledger ─────────────────────────────────────

class TestGateReportCarriesTheVerdict:
    @staticmethod
    def _book(tmp_path, rows):
        book = ShadowBook(state_file=str(tmp_path / "sb.json"))
        book._loaded = True
        book._trades = [
            {"status": "closed", "r": r, "gate": f"{gate}: reading",
             "symbol": "BTC/USDT", "regime": "TREND"}
            for gate, r in rows]
        return book

    def test_a_large_total_of_small_effects_is_undistinguished(self, tmp_path):
        # 60 stops and 37 wins at +1.73 -> net ~+4R over 97, like the live row.
        rows = [("MTF_ALIGNMENT", -1.0)] * 60 + [("MTF_ALIGNMENT", 1.73)] * 37
        rep = self._book(tmp_path, rows).gate_report()
        g = rep["MTF_ALIGNMENT"]
        assert g["n"] == 97
        assert g["verdict"] == "undistinguished"
        assert g["lower_r"] < 0 < g["upper_r"]

    def test_a_consistent_winner_is_established(self, tmp_path):
        rows = [("CORRELATION", 1.5)] * 12 + [("CORRELATION", 0.9)] * 12
        rep = self._book(tmp_path, rows).gate_report()
        assert rep["CORRELATION"]["verdict"] == "eating_edge"
        assert rep["CORRELATION"]["lower_r"] > 0

    def test_a_gate_that_only_stops_losers_is_established_as_saving(self, tmp_path):
        rep = self._book(tmp_path, [("VOLGUARD", -1.0)] * 20).gate_report()
        assert rep["VOLGUARD"]["verdict"] == "saving"
        assert rep["VOLGUARD"]["upper_r"] < 0

    def test_too_few_trades_has_no_verdict_at_all(self, tmp_path):
        rep = self._book(tmp_path, [("THIN", 2.0)] * 5).gate_report()
        assert rep["THIN"]["verdict"] is None, (
            "5 blocked trades produced a verdict; None is the honest answer")

    def test_the_scoreboard_paints_no_colour_it_cannot_support(self, tmp_path):
        """Colour is a claim, and `/shadow` made it off a bare total too."""
        rows = ([("MTF_ALIGNMENT", -1.0)] * 60
                + [("MTF_ALIGNMENT", 1.73)] * 37)
        out = self._book(tmp_path, rows).render_report()
        assert "MTF_ALIGNMENT" in out
        assert "\U0001f7e5" not in out, (
            "a gate at +0.04R/trade was painted red off net_r > 0.5")
        assert "⬜" in out, "no muted icon for the undistinguished row"

    def test_the_scoreboard_still_paints_a_real_finding(self, tmp_path):
        rows = [("CORRELATION", 1.5)] * 12 + [("CORRELATION", 0.9)] * 12
        out = self._book(tmp_path, rows).render_report()
        assert "\U0001f7e5" in out

    def test_a_thin_row_says_why_it_has_no_colour(self, tmp_path):
        out = self._book(tmp_path, [("THIN", 2.0)] * 5).render_report()
        assert "too few to bound" in out


# ── 6. end to end: the card the operator reads ────────────────────────────

def _card(summary, parsed, gates=None):
    ev = {"summary": summary}
    if gates is not None:
        ev["shadow_gates"] = gates
    return SelfAudit.render_report(ev, [], {}, "alts_1h", parsed=parsed)


def test_the_live_card_no_longer_endorses_a_losing_window():
    out = _card(LOSING, [], {"MTF_ALIGNMENT": _gate(97, 4.1, 210.0)})
    assert "40 closes" in out and "PF 0.4" in out
    assert "the evidence supports the current configuration" not in out
    assert "not an endorsement" in out
    assert "costliest gate" not in out


def test_an_unreadable_reply_reaches_the_card_as_a_warning():
    out = _card(SOUND, None)
    assert "could not be read" in out
    assert "is a pass" not in out


def test_an_older_caller_without_the_reply_still_renders():
    """`render_report`'s 4-arg form is used in three test files and must not
    start reporting a failed audit because the reply was never passed."""
    out = SelfAudit.render_report({"summary": SOUND}, [], {}, "alts_1h")
    assert "the evidence supports the current configuration" in out
    assert "could not be read" not in out


def test_a_measured_proposal_is_unaffected_by_any_of_this():
    """The verdict branch is only reached when there is nothing to measure."""
    out = SelfAudit.render_report(
        {"summary": LOSING},
        [{"flag": "TREND_UP_SIZE_MULT", "value": "0.8", "rationale": "r",
          "measured": {"return_pct": 4.2, "pf": 2.1, "trades": 41}}],
        {"return_pct": 3.14, "pf": 1.87, "trades": 39}, "alts_1h", parsed=None)
    assert "measured +4.20%" in out
    assert "could not be read" not in out, (
        "a reply that DID parse into measured results reported as unreadable")


# ── 7. the evidence marks the failure rather than dropping the key ────────

def test_an_unreadable_shadow_book_reaches_the_evidence_as_null(monkeypatch):
    """`except: pass` left the key absent, which renders as an empty ledger.

    The model is told, in the system prompt, that `null` means NOT MEASURED. A
    MISSING key carries no such instruction and reads as "no gates have cost
    anything" — a confident negative assembled from a failed read, fed into a
    prompt that proposes config changes off it.
    """
    import bot.core.shadow_book as sb

    class _Exploding:
        def gate_report(self):
            raise RuntimeError("shadow_book.json unreadable")

        def counts(self):
            raise RuntimeError("shadow_book.json unreadable")

    monkeypatch.setattr(sb, "SHADOW_BOOK", _Exploding())
    ev = SelfAudit().gather_evidence(
        type("_E", (), {"live_executor": None, "risk": None})())
    assert "shadow_gates" in ev, "the key was dropped, not marked"
    assert ev["shadow_gates"] is None
    assert ev["shadow_counts"] is None
    # And the card says so rather than going quiet.
    assert "could not be read" in SelfAudit.render_report(ev, [], {}, "alts_1h")


def test_a_readable_shadow_book_still_reaches_the_evidence(monkeypatch):
    import bot.core.shadow_book as sb

    book = sb.ShadowBook(state_file="/nonexistent/sb.json")
    book._loaded = True
    book._trades = [{"status": "closed", "r": 1.2, "gate": "CORRELATION: x",
                     "symbol": "BTC/USDT"}]
    monkeypatch.setattr(sb, "SHADOW_BOOK", book)
    ev = SelfAudit().gather_evidence(
        type("_E", (), {"live_executor": None, "risk": None})())
    assert ev["shadow_gates"] == {"CORRELATION": ev["shadow_gates"]["CORRELATION"]}
    assert ev["shadow_counts"] == {"closed": 1}


# ── 8. driven through run(), because the renderer is not the caller ───────

def _engine_with_no_record():
    """An engine whose analyzer yields a client, and nothing else to read."""
    class _Analyzer:
        def _resolve_llm_config(self):
            return {"provider": "test"}

        def _build_client_for_config(self, cfg):
            return object()

    return types.SimpleNamespace(analyzer=_Analyzer(), live_executor=None,
                                 risk=None)


def _run_with_reply(tmp_path, monkeypatch, reply):
    import bot.core.shadow_book as sb
    import bot.llm.provider as prov

    async def _complete(client, cfg, sys_p, user_p):
        return reply

    monkeypatch.setattr(prov, "llm_complete", _complete)
    # The real singleton reads data/shadow_book.json, which conftest's cleanup
    # list does not cover — so without this the card's shadow line depends on
    # whatever an earlier test left behind. An empty book, pinned.
    empty = sb.ShadowBook(state_file=str(tmp_path / "sb.json"))
    empty._loaded = True
    monkeypatch.setattr(sb, "SHADOW_BOOK", empty)
    sa = SelfAudit(state_file=str(tmp_path / "sa.json"),
                   run_backtest=lambda *a, **k: {})
    return asyncio.run(sa.run(_engine_with_no_record()))


def test_an_unparseable_reply_reaches_the_card_through_run(tmp_path, monkeypatch):
    """A FIX THAT LANDS IN THE RENDERER AND NOT THE CALLER HAS NOT LANDED.

    `render_report` grew a `parsed` argument; nothing proved `run()` passes
    it, and the mutation dropping `parsed=parsed` from that one call survived
    the whole file. The renderer was thoroughly covered and the wiring was
    covered by reading it — #999 exactly.
    """
    report = _run_with_reply(tmp_path, monkeypatch,
                             "I have reviewed the evidence and would leave "
                             "everything as it is.")
    assert report is not None
    assert "could not be read" in report
    assert "failed audit" in report
    assert "the evidence supports the current configuration" not in report


def test_a_real_empty_array_still_reaches_the_card_as_a_proposal_of_nothing(
        tmp_path, monkeypatch):
    report = _run_with_reply(tmp_path, monkeypatch, "[]")
    assert report is not None
    assert "No changes proposed" in report
    assert "could not be read" not in report or "live window" in report.lower()


# ── 9. the validator still swallows None ──────────────────────────────────

def test_validate_proposals_takes_none_without_raising():
    """The parser's new third state reaches it, and a validator genuinely
    cannot distinguish 'nothing proposed' from 'nothing readable'. That
    distinction is made one layer up, which is why it is tested there."""
    assert validate_proposals(None) == []
    assert validate_proposals(parse_llm_json("not json at all")) == []
