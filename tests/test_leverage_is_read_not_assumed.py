"""`position_leverage`, and the fallback it replaced.

THE FALLBACK WAS ONLY EVER EXERCISED IN THE CASE WHERE IT WAS WRONG. Two
position cards derived leverage as `notional_now / sz`, and `sz` came from

    cost_usd if cost_usd > 0 else entry * qty

— the margin, or the NOTIONAL, under one name. So:

    cost_usd > 0    sz is the margin     notional / margin  = the leverage ✓
    cost_usd == 0   sz is the notional   notional / notional ≈ 1.0         ✗

The first row is also the row where a stored leverage is present, so the
fallback is not reached at all. The second is the ORPHAN — the position the
bot did not open, whose margin the venue never reported — and it is the only
case that ever reaches the fallback. A derivation correct exactly when it is
unnecessary.

WHAT 1.0x PRINTS. The ROE is `raw_move * leverage`, so at 1.0x it collapses to
the raw price move. That is the 2026-08-17 incident `leveraged_return`'s own
module docstring is about — the same position reading `-2.56%` on
/open_positions and `-0.13%` on its detail card — arriving through the BASIS
rather than through the formula the docstring blames. Fixing the formula did
not fix it, because the leverage going missing produces the identical output.

AND None RATHER THAN 1.0, because 1.0x is a real leverage: a spot position has
it, and a caller must be able to tell "unlevered" from "nobody could say".
A genuine 1x still reads 1x — derived from notional / margin, which is what an
unlevered position's ratio actually is.
"""

import pytest

from bot.utils.leveraged_return import (
    _leveraged_pnl_usd,
    _leveraged_return_pct,
    position_leverage,
)


class TestAStoredLeverageWins:
    def test_a_recorded_multiple_is_used_as_is(self):
        assert position_leverage(20, 10.0, 200.0) == 20.0

    def test_it_beats_a_derivation_that_would_disagree(self):
        """The venue's own number outranks arithmetic over two fields we may
        have recorded at different moments."""
        assert position_leverage(20, 10.0, 999.0) == 20.0

    def test_a_stored_string_is_still_read(self):
        assert position_leverage("20", 10.0, 200.0) == 20.0

    @pytest.mark.parametrize("junk", ["", "n/a", object(), None, float("nan")])
    def test_junk_falls_through_to_the_derivation(self, junk):
        assert position_leverage(junk, 10.0, 200.0) == 20.0


class TestAStored1xIsNotAuthoritative:
    def test_it_derives_instead(self):
        """`leverage` defaults to 0 or 1 on records that never had one, so a
        stored 1 is far more often "unset" than "this is spot". Deriving costs
        nothing when the margin is known and is right either way: an unlevered
        position's notional/margin IS 1.0.
        """
        assert position_leverage(1, 10.0, 200.0) == 20.0

    def test_and_a_genuine_1x_still_comes_out_1x(self):
        assert position_leverage(1, 50.0, 50.0) == 1.0

    def test_a_stored_zero_derives_too(self):
        assert position_leverage(0, 10.0, 200.0) == 20.0


class TestTheOrphanIsUnknownNotUnlevered:
    @pytest.mark.parametrize("margin", [None, 0, 0.0, -5, "", "n/a"])
    def test_no_margin_means_no_leverage(self, margin):
        """THE WHOLE FINDING. This is the case the old fallback answered 1.0
        for, by dividing the notional by itself."""
        assert position_leverage(0, margin, 200.0) is None

    @pytest.mark.parametrize("notional", [None, 0, -5, "n/a"])
    def test_no_notional_means_no_leverage_either(self, notional):
        assert position_leverage(0, 10.0, notional) is None

    def test_it_never_answers_1_from_an_absence(self):
        """1.0 is a real leverage. Answering it from no data is the whole
        defect, so it must not appear from any unusable combination."""
        for margin in (None, 0, -1, "", "x"):
            for notional in (None, 0, -1, "", "x", 200.0):
                assert position_leverage(0, margin, notional) is None

    def test_the_old_fallback_would_have_said_1x_here(self):
        """The arithmetic, stated so the finding is checkable rather than
        asserted: `sz` for an orphan IS the notional, so `notional / sz` is 1.
        """
        entry, qty, last = 5.0, 3.0, 5.1
        sz_orphan = entry * qty                 # cost_usd was 0
        notional_now = qty * last
        assert round(notional_now / sz_orphan, 2) == 1.02   # ~1.0x
        assert position_leverage(0, None, notional_now) is None


class TestWhatThatLeverageThenPrints:
    """The consequence, end to end through the two helpers it feeds."""

    ENTRY, MARK, MARGIN = 5.0, 5.1, 0.75

    def test_a_1x_roe_is_just_the_price_move(self):
        raw = (self.MARK - self.ENTRY) / self.ENTRY * 100
        at_1x = _leveraged_return_pct(self.ENTRY, self.MARK, "LONG", 1)
        assert at_1x == pytest.approx(raw)
        at_20x = _leveraged_return_pct(self.ENTRY, self.MARK, "LONG", 20)
        assert at_20x == pytest.approx(raw * 20)
        # The gap the incident was reported as: a 20x position rendered at 1x
        # under-reads its own move by the whole leverage multiple.
        assert abs(at_20x - at_1x) > 30

    def test_an_unknown_leverage_prints_nothing_at_all(self):
        lev = position_leverage(0, None, 200.0)
        assert lev is None
        assert _leveraged_return_pct(self.ENTRY, self.MARK, "LONG",
                                     lev if lev is not None else 0) is None
        assert _leveraged_pnl_usd(self.ENTRY, self.MARK, "LONG", self.MARGIN,
                                  lev if lev is not None else 0) is None


class TestTheCallSitesAskItRatherThanDeriving:
    """Reachability. The unit drives above prove the RULE; only the callers
    show the rule is the one in force — the `_lev_mismatch` lesson, and the
    reason a helper nothing calls leaves a card exactly as broken as it was.
    """

    def _src(self, path):
        import pathlib

        from tests.source_scan import code_only
        root = pathlib.Path(__file__).resolve().parent.parent
        return code_only((root / path).read_text(encoding="utf-8"))

    @pytest.mark.parametrize("path", [
        "bot/skills/callback_handler.py",
        "bot/skills/trading_commands.py",
    ])
    def test_the_card_builders_call_it(self, path):
        assert "position_leverage(" in self._src(path), (
            f"{path} no longer asks for the leverage; if it derives one "
            "itself the two answers can differ again")

    @pytest.mark.parametrize("path", [
        "bot/skills/callback_handler.py",
        "bot/skills/trading_commands.py",
    ])
    def test_the_old_derivation_is_gone(self, path):
        src = self._src(path)
        for shape in ("notional_now / sz", "notional / cost"):
            assert shape not in src, (
                f"the margin-or-notional derivation is back in {path}: {shape}")
