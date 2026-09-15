"""The funding-arb paper record gets a VERDICT, and the verdict has four outcomes.

`/arb` printed a total paper carry and a fee sentence — "carry must beat that
before the capture strategy is worth gating in" — and no verdict, so the
reader made one from a sum over however few entries happened to accrue it.
`arb_verdict` is the discipline the voter card and the shadow scoreboard
already use: the WHOLE 95% interval on the per-entry net (the on-period's
gross carry minus one round-trip fee) has to clear zero, on a sample past
both floors. Four outcomes, one seam, three readers:

  survives   the whole interval above zero, floors met
  does_not   the whole interval below zero, floors met
  thin       a floor unmet, or an interval that straddles zero
  unread     the record on disk could not be read — not an empty one

`scored` is the closed entries the interval is over and `total` counts the
one still open beside them: an on-period running at the last snapshot has
not paid its exit fee and its carry is partial, so it is counted and never
scored. The public wire carries the same verdict in percent of the notional
and no dollar figure.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import bot.core.arb_tracker as at
from bot.compat import UTC
from bot.core.arb_tracker import (
    MIN_VERDICT_ENTRIES,
    MIN_VERDICT_HELD_HOURS,
    PAPER_NOTIONAL_USD,
    ROUND_TRIP_FEE_PCT,
    VERDICT_STATES,
    ArbVerdict,
    arb_reading,
    arb_verdict,
    compute_paper_carry,
    format_arb_html,
    public_verdict_sentence,
)

T0 = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
FEE = PAPER_NOTIONAL_USD * ROUND_TRIP_FEE_PCT / 100.0   # $2.40 on $1,000


def _snap(base, ts, spread, long_v="bitget", short_v="hyperliquid"):
    return {"ts": ts.isoformat(), "base": base, "spread_apr": spread,
            "long_venue": long_v, "short_venue": short_v,
            "rates": {long_v: 0.0, short_v: spread}}


def _entries(base, n, *, hours_on, spread, start=T0, closed=True):
    """`n` on-periods each earning `hours_on` hours at `spread`, separated by
    one flat snapshot (sub-threshold) so each closes. An interval earns when
    its EARLIER snapshot is on, so `hours_on` on-snapshots followed by the
    flat one earn exactly `hours_on` hours. `closed=False` leaves the LAST
    period running at the final snapshot — no flat snapshot after it, so it
    earns `hours_on - 1` hours and is never scored."""
    snaps, t = [], start
    for k in range(n):
        for _ in range(hours_on):
            snaps.append(_snap(base, t, spread))
            t += timedelta(hours=1)
        last = (k == n - 1)
        if not (last and not closed):
            snaps.append(_snap(base, t, 0.0))       # flat: closes the period
            t += timedelta(hours=1)
    return snaps


def _carry_per_entry(hours_on, spread):
    return PAPER_NOTIONAL_USD * (spread / 100.0) * (hours_on / (24 * 365))


# ── the samples: one gross carry per CLOSED on-period ────────────────────────

class TestTheSamples:
    def test_each_closed_on_period_is_one_sample_and_the_open_one_is_counted_not_scored(self):
        snaps = _entries("BTC", 3, hours_on=10, spread=87.6, closed=False)
        (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
        assert pc.entries == 3 and pc.open_entry is True
        assert len(pc.entry_carry_usd) == 2, "the running period is not a sample"
        per = _carry_per_entry(10, 87.6)
        assert all(abs(c - per) < 1e-9 for c in pc.entry_carry_usd)
        # The running period earned its nine observed hours; the total is the
        # closed samples plus that — the samples decompose the total.
        assert abs(sum(pc.entry_carry_usd) + _carry_per_entry(9, 87.6) - pc.earned_usd) < 1e-9

    def test_a_gap_closes_the_period_and_scores_it(self):
        snaps = [_snap("ETH", T0 + timedelta(hours=i), 20.0) for i in range(3)]
        snaps += [_snap("ETH", T0 + timedelta(hours=20 + i), 20.0) for i in range(3)]
        (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
        assert pc.entries == 2 and pc.open_entry is True
        assert len(pc.entry_carry_usd) == 1
        assert abs(pc.entry_carry_usd[0] - _carry_per_entry(2, 20.0)) < 1e-9

    def test_an_unreadable_last_spread_is_not_an_observed_exit(self):
        # The period's fate is read off the LAST snapshot; a row with no
        # spread is unread, not flat, so the period stays open — counted,
        # never scored — rather than closing on an exit nobody observed.
        snaps = _entries("BTC", 2, hours_on=10, spread=87.6, closed=False)
        snaps.append(dict(snaps[-1], ts=(T0 + timedelta(hours=len(snaps))).isoformat(),
                          spread_apr=None))
        (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
        assert pc.entries == 2 and pc.open_entry is True
        assert len(pc.entry_carry_usd) == 1

    def test_the_old_totals_are_unchanged(self):
        # The accrual math the existing suite pins — the samples are a
        # decomposition of it, never a second computation.
        snaps = [_snap("BTC", T0 + timedelta(hours=i), 8.76) for i in range(11)]
        (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
        assert abs(pc.earned_usd - 0.10) < 1e-9 and pc.held_hours == 10 and pc.entries == 1
        assert pc.entry_carry_usd == [] and pc.open_entry is True


# ── the verdict ──────────────────────────────────────────────────────────────

def _verdict_over(n, hours_on, spread, **kw):
    return arb_verdict(compute_paper_carry(_entries("BTC", n, hours_on=hours_on, spread=spread),
                                           min_spread_apr=3.0), **kw)


class TestTheVerdict:
    def test_a_record_that_clears_the_fee_on_every_entry_survives(self):
        # 12 entries × 48h at 200%/yr: $10.96 gross each, $8.56 net of the
        # $2.40 round trip, identical samples — a zero-variance interval whose
        # lower end is the mean, and the entry floor is what stands between
        # that and "three identical entries prove anything".
        v = _verdict_over(12, 48, 200.0)
        assert v.state == "survives", v.reason
        assert v.scored == 12 and v.total == 12 and v.held_hours == 12 * 48
        assert v.mean_net_usd == pytest.approx(_carry_per_entry(48, 200.0) - FEE, abs=1e-3)
        assert v.interval_usd[0] > 0
        assert "survives fees" in v.reason and "$" in v.reason and "12 closed entries" in v.reason

    def test_a_record_that_never_covers_the_fee_does_not(self):
        # 12 entries × 8h at 10%/yr: $0.09 gross each, −$2.31 net.
        v = _verdict_over(12, 8, 10.0)
        assert v.state == "does_not", v.reason
        assert v.interval_usd[1] < 0
        assert "does not survive fees" in v.reason

    def test_too_few_entries_is_thin_however_good_they_look(self):
        v = _verdict_over(MIN_VERDICT_ENTRIES - 1, 48, 200.0)
        assert v.state == "thin" and "record too thin" in v.reason
        assert f"{MIN_VERDICT_ENTRIES - 1} closed entries" in v.reason
        assert f"the bar is {MIN_VERDICT_ENTRIES} closed entries" in v.reason

    def test_too_few_hours_is_thin_however_many_entries(self):
        # 20 entries × 2h = 40h held: under the hours floor.
        v = _verdict_over(20, 2, 500.0)
        assert v.held_hours < MIN_VERDICT_HELD_HOURS
        assert v.state == "thin" and "record too thin" in v.reason

    def test_an_interval_that_straddles_zero_is_thin_not_a_verdict(self):
        # A third of the entries earn well above the fee, the rest nowhere
        # near it: the mean is positive and the interval crosses zero.
        good = _entries("BTC", 4, hours_on=48, spread=200.0)
        bad = _entries("ETH", 8, hours_on=8, spread=10.0)
        v = arb_verdict(compute_paper_carry(good + bad, min_spread_apr=3.0))
        assert v.scored == 12 and v.held_hours >= MIN_VERDICT_HELD_HOURS
        lo, hi = v.interval_usd
        assert lo < 0 < hi, (lo, hi)
        assert v.state == "thin" and "straddles zero" in v.reason
        assert v.mean_net_usd is not None

    def test_a_negative_mean_with_an_interval_reaching_above_zero_is_thin_too(self):
        # The other direction of the straddle: a losing mean whose interval
        # still reaches above zero is not "does not survive" — the point
        # estimate decides nothing on its own in either direction.
        good = _entries("BTC", 2, hours_on=48, spread=200.0)
        bad = _entries("ETH", 10, hours_on=8, spread=10.0)
        v = arb_verdict(compute_paper_carry(good + bad, min_spread_apr=3.0))
        assert v.scored == 12 and v.mean_net_usd < 0
        lo, hi = v.interval_usd
        assert lo < 0 < hi, (lo, hi)
        assert v.state == "thin" and "straddles zero" in v.reason

    def test_one_closed_entry_has_no_interval_and_says_so_in_the_singular(self):
        v = _verdict_over(1, 48, 200.0)
        assert v.scored == 1 and v.interval_usd is None and v.mean_net_usd is not None
        assert v.state == "thin" and "1 closed entry over" in v.reason

    def test_an_open_entry_is_counted_beside_the_scored_ones(self):
        snaps = _entries("BTC", 12, hours_on=48, spread=200.0, closed=False)
        v = arb_verdict(compute_paper_carry(snaps, min_spread_apr=3.0))
        assert v.scored == 11 and v.total == 12
        assert "(1 still open, not scored)" in v.reason

    def test_no_history_is_thin_with_its_own_words(self):
        v = arb_verdict([])
        assert v.state == "thin" and "no tracked history" in v.reason
        assert v.scored == 0 and v.total == 0 and v.mean_net_usd is None

    def test_every_state_is_one_the_vocabulary_names(self):
        for v in (_verdict_over(12, 48, 200.0), _verdict_over(12, 8, 10.0),
                  _verdict_over(2, 48, 200.0), arb_verdict([])):
            assert v.state in VERDICT_STATES
        assert set(VERDICT_STATES) == {"survives", "does_not", "thin", "unread"}

    def test_the_fee_is_charged_once_per_closed_entry(self):
        v = _verdict_over(12, 48, 200.0)
        assert v.fee_usd == pytest.approx(FEE, abs=1e-6)
        assert v.net_usd == pytest.approx(v.gross_usd - 12 * FEE, abs=1e-3)

    def test_the_floors_are_the_shadow_scoreboards_kind(self):
        assert MIN_VERDICT_ENTRIES >= 10 and MIN_VERDICT_HELD_HOURS >= 72


# ── the reading: unread is not empty ─────────────────────────────────────────

class TestTheReading:
    def test_a_missing_record_is_no_history_not_unread(self, tmp_path):
        carries, v = arb_reading(tmp_path / "absent.jsonl")
        assert carries == [] and v.state == "thin" and "no tracked history" in v.reason

    def test_a_record_that_will_not_open_is_unread(self, tmp_path):
        # A directory where the file should be: read_text raises, and the
        # answer is the record could not be read — never "nothing tracked".
        carries, v = arb_reading(tmp_path)
        assert carries == [] and v.state == "unread"
        assert "could not read the record" in v.reason and "IsADirectoryError" in v.reason
        assert "no tracked history" not in v.reason

    def test_a_readable_record_is_scored(self, tmp_path):
        import json
        p = tmp_path / "arb.jsonl"
        p.write_text("\n".join(json.dumps(s) for s in _entries("BTC", 12, hours_on=48, spread=200.0)) + "\n")
        carries, v = arb_reading(p)
        assert [c.base for c in carries] == ["BTC"] and v.state == "survives"


# ── the words on each surface ────────────────────────────────────────────────

class TestTheCard:
    def test_the_card_prints_the_verdict_and_never_derives_one_from_the_total(self):
        carries = compute_paper_carry(_entries("BTC", 12, hours_on=48, spread=200.0), min_spread_apr=3.0)
        v = arb_verdict(carries)
        html = format_arb_html(carries, verdict=v)
        assert "🟢 Verdict: <b>survives fees" in html
        assert "Total paper carry" in html
        without = format_arb_html(carries)
        assert "Verdict" not in without, "no verdict handed in, none printed"

    def test_the_card_colours_by_state_word_only(self):
        bad = compute_paper_carry(_entries("BTC", 12, hours_on=8, spread=10.0), min_spread_apr=3.0)
        assert "🔴 Verdict: <b>does not survive fees" in format_arb_html(bad, verdict=arb_verdict(bad))
        thin = compute_paper_carry(_entries("BTC", 3, hours_on=8, spread=10.0), min_spread_apr=3.0)
        assert "🟡 Verdict: <b>record too thin" in format_arb_html(thin, verdict=arb_verdict(thin))

    def test_an_unread_record_is_a_card_that_scores_nothing(self):
        v = ArbVerdict("unread", "could not read the record: PermissionError")
        html = format_arb_html([], verdict=v)
        assert "🔴 Verdict: <b>could not read the record" in html
        assert "No tracked history" not in html and "nothing below was scored" in html
        assert "Total paper carry" not in html

    def test_the_empty_state_keeps_its_sentence(self):
        html = format_arb_html([], verdict=arb_verdict([]))
        assert "No tracked history yet" in html and "Verdict" not in html


class TestThePublicWire:
    def test_the_public_sentence_is_percent_of_notional_with_no_dollar_figure(self):
        for n, h, s in ((12, 48, 200.0), (12, 8, 10.0), (3, 8, 10.0)):
            v = _verdict_over(n, h, s)
            words = public_verdict_sentence(v)
            assert "$" not in words, words
            assert v.reason.split(" — ")[0] in words, (v.reason, words)
        v = _verdict_over(12, 48, 200.0)
        words = public_verdict_sentence(v)
        assert f"{v.mean_net_pct:+.3f}%" in words and "0.24% round trip" in words
        assert public_verdict_sentence(ArbVerdict("unread", "x")) == "could not read the record (x)"
        assert public_verdict_sentence(arb_verdict([])).startswith("no tracked history")

    def test_the_web_section_carries_the_verdict_in_percent_and_no_sample_list(self, monkeypatch, tmp_path):
        import json

        from bot.core import web_reports
        p = tmp_path / "arb.jsonl"
        p.write_text("\n".join(json.dumps(s) for s in _entries("BTC", 12, hours_on=48, spread=200.0)) + "\n")
        monkeypatch.setattr(at, "_RECORD_FILE", p)
        sec = web_reports._arb_section()
        assert sec["verdict"]["state"] == "survives"
        assert "$" not in sec["verdict"]["sentence"]
        assert set(sec["verdict"]) == {"state", "sentence", "scored", "total", "held_hours",
                                       "fee_pct", "mean_net_pct", "interval_pct"}
        assert sec["verdict"]["scored"] == 12 and sec["verdict"]["fee_pct"] == 0.24
        assert all("entry_carry_usd" not in row for row in sec["carries"])
        assert sec["carries"][0]["base"] == "BTC" and "earned_usd" in sec["carries"][0]

    def test_the_readers_share_one_seam(self):
        from tests.source_scan import code_only
        root = Path(__file__).resolve().parent.parent
        cmd = code_only((root / "bot" / "skills" / "market_commands.py").read_text(encoding="utf-8"))
        i = cmd.index("async def _cmd_arb")
        body = cmd[i:cmd.index("async def _cmd_fundingscan", i)]
        assert "arb_reading" in body and "verdict=verdict" in body.replace(" ", "")
        assert "load_snapshots" not in body, "the command reads through the seam, not beside it"
        web = code_only((root / "bot" / "core" / "web_reports.py").read_text(encoding="utf-8"))
        assert "arb_verdict" in web and "public_verdict_sentence" in web
