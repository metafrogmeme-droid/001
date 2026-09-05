"""Six surfaces read the PAPER book and printed it under a LIVE label.

`state = portfolio.snapshot()` is the paper tracker. risk_engine's own comment
records the fact that makes reading it in live mode wrong: live fills never
touch the paper portfolio, so in pure-live operation `max_drawdown_pct` and
`daily_pnl` do not move. CLAUDE.md records the consequence in an operator's
words — reading "~0% from a gate that was refusing trades at 9%" — and logs it
as FIXED. It was fixed in `_cmd_risk`. Five siblings kept it.

The corollary is the whole lesson of that file: ask which OTHER surface makes
the same claim. `/status` at least called `drawdown_status()`; `CheckRiskSkill`
never did. `/portfolio`'s PNG went further and printed six paper tiles under a
hero that was the real exchange equity — then `return`ed, so the carefully
tri-stated text lines below it only ran when Pillow failed.
"""
import re

import pytest

from bot.formatters.drawdown_card import drawdown_source_note, enforced_drawdown
from bot.formatters.rich_cards import render_status_card
from bot.guardian.risk_sentry import assess, human_readable

LIVE = {"drawdown_pct": 4.7, "drawdown_source": "live",
        "effective_limit_pct": 7.0}


class TestTheSeam:
    """One reader, because four surfaces answered this differently."""

    @pytest.mark.parametrize("st", [None, {}])
    def test_unread_is_three_nones_not_a_zero(self, st):
        assert enforced_drawdown(st) == (None, None, None)

    def test_a_live_reading_survives_whole(self):
        assert enforced_drawdown(LIVE) == (4.7, "live", 7.0)

    def test_a_measured_zero_is_kept(self):
        # 0.0 is falsy and 0.0 is a real, measured, flat book — the commonest
        # state the bot is ever in.
        dd, src, _ = enforced_drawdown(dict(LIVE, drawdown_pct=0.0))
        assert dd == 0.0 and dd is not None and src == "live"

    def test_each_field_is_validated_alone(self):
        # A payload can carry a good limit and an unreadable drawdown. Folding
        # the pair together discards the half that arrived.
        dd, src, lim = enforced_drawdown(
            {"drawdown_pct": None, "drawdown_source": "paper",
             "effective_limit_pct": 7.0})
        assert (dd, src, lim) == (None, "paper", 7.0)

    @pytest.mark.parametrize("junk", [None, "4.7", True, float("nan")])
    def test_junk_reads_as_unread(self, junk):
        assert enforced_drawdown(dict(LIVE, drawdown_pct=junk))[0] is None

    def test_an_unknown_source_word_licences_no_claim(self):
        assert enforced_drawdown(dict(LIVE, drawdown_source="live-ish"))[1] is None

    def test_the_note_is_never_empty(self):
        # An omitted note reads as "the usual source", which is the assumption
        # that made the number unattributable in the first place.
        for src in ("live", "paper", None, "nonsense"):
            assert drawdown_source_note(src).strip()


class TestTheStatusCard:
    @staticmethod
    def _text(**kw):
        base = dict(mode="LIVE", active=True, equity=524.52, open_positions=1,
                    daily_pnl=0.0, drawdown=4.7, max_drawdown=7.0,
                    market_bias="Normal")
        return re.sub(r"<[^>]+>", "", render_status_card(**{**base, **kw}))

    def test_a_live_reading_is_attributed(self):
        out = self._text(drawdown=4.7, drawdown_source="live")
        assert "4.7%" in out and "live equity high-water mark" in out

    def test_the_paper_fallback_says_it_is_the_paper_one(self):
        # The fallback is defensible — blanking the line on a transient fault
        # is worse. It is only defensible if the card SAYS SO.
        out = self._text(drawdown=0.0, drawdown_source="paper")
        assert "paper snapshot" in out

    def test_an_unread_drawdown_is_not_a_flat_curve(self):
        out = self._text(drawdown=None, drawdown_source=None)
        assert "not read" in out
        # No bar. An empty gauge reads as zero drawdown, which is the claim
        # this whole block exists to avoid.
        assert "━" not in out.split("Drawdown")[1]
        assert "🟢" not in out.split("Drawdown")[1]

    def test_an_unread_drawdown_still_explains_itself(self):
        assert "failed read" in self._text(drawdown=None, drawdown_source=None)

    def test_a_measured_zero_keeps_its_gauge(self):
        out = self._text(drawdown=0.0, drawdown_source="live")
        assert "+0.0%" in out and "🟢" in out.split("Drawdown")[1]

    def test_an_unreadable_limit_does_not_divide(self):
        # `drawdown / max_drawdown` with max_drawdown None was a TypeError
        # waiting on the one payload that omits it.
        assert "not read" in self._text(drawdown=None, max_drawdown=None)


class TestTheRiskSentry:
    def test_an_unread_book_is_not_a_flat_book(self):
        r = assess(None)
        assert r["worst_level"] == "unknown"
        assert r["book_read"] is False
        assert r["gross_usd"] is None

    def test_an_unread_book_never_says_nothing_flagged(self):
        # An all-clear assembled from a crash, on a risk surface.
        assert "nothing flagged" not in human_readable(assess(None))
        assert "🟢" not in human_readable(assess(None))

    def test_a_genuinely_flat_book_still_reads_clear(self):
        # Not every match is a defect: an empty book really is clear.
        r = assess([])
        assert r["worst_level"] == "clear" and r["book_read"] is True
        assert "nothing flagged" in human_readable(r)

    def test_an_unread_ledger_cannot_clear_the_daily_cap(self):
        # `_f(spent) or 0.0` could never reach `spent >= 0.8 * cap`, so the
        # warning was structurally unreachable on any ledger fault.
        r = assess([], envelope={"max_notional_daily_usd": 100.0},
                   spent_today_usd=None)
        assert any(a["category"] == "daily_spend" and a["level"] == "unknown"
                   for a in r["alerts"])

    def test_a_read_ledger_below_the_cap_stays_quiet(self):
        r = assess([], envelope={"max_notional_daily_usd": 100.0},
                   spent_today_usd=10.0)
        assert not [a for a in r["alerts"] if a["category"] == "daily_spend"]

    def test_a_missing_report_is_a_failure_not_an_all_clear(self):
        assert "🟢" not in human_readable(None)
