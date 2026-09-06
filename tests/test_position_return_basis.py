"""One position, one minute apart, two percentages that differ by 20x.

Operator screenshots, 2026-08-17. The same live APT/USDT SHORT:

    card 1   -2.56%  ($-0.64)     /open_positions
    card 2   -0.13%  ($-0.64)     position detail, one minute later

Identical entry (0.51754), identical mark (0.51820), identical dollar. Read in
sequence that is a 2.4-point recovery that never happened. Reconciled:

    raw price move      -0.13%   <- card 2's headline
    x20 leverage (ROE)  -2.55%   <- card 1's headline
    gross PnL           $-0.64   <- shown beside BOTH

Both numbers were individually correct. Neither said which question it was
answering, and they sat in the same slot with the same dollar next to them.

HOW IT HAPPENED, AND WHY IT IS THE INTERESTING PART

`_leveraged_pnl_usd` exists because of this exact defect one layer down. Its
docstring says the dollar was put on one basis "so a leveraged % can never sit
beside an unleveraged $ again". The DOLLAR was then fixed at every call site.
The PERCENT was fixed at one, and nobody audited the others — which is the
repo's own stated lesson ("ask which OTHER surface makes the same claim") going
unapplied in the commit that named it.

The percent now has a partner helper sitting immediately beside the dollar's,
with the same guards and the same leverage convention, so the two cannot
disagree about a basis OR about an unusable input, and a fourth call site
cannot pick one for the dollar and the other for the percent.

SCOPE, STATED PLAINLY. This fixes the basis on the surface the operator saw.
It does NOT convert the percent to a None-capable "unknown" — three of the four
producers substitute the ENTRY price for an unread mark
(`prices.get(sym, entry_price)`), so an unreadable price already renders as a
measured 0.00%, and fixing that needs every format site converted in the same
commit or the unreadable case crashes on `NoneType.__format__`. That is a
separate, larger change and is recorded rather than half-done.
"""

from __future__ import annotations

from bot.utils.leveraged_return import _leveraged_pnl_usd as pnl_usd
from bot.utils.leveraged_return import _leveraged_return_pct as ret_pct


def _leaf_src() -> str:
    """The pair's own module, comments stripped: `bot/utils/leveraged_return.py`
    since the handler split (the position cards left for the trading mixin
    while the detail callback stayed, and both read the pair from the leaf)."""
    import inspect

    import bot.utils.leveraged_return as lr
    from tests.source_scan import code_only
    return code_only(inspect.getsource(lr))


def _handler_src() -> str:
    """Every file the handler class is made of, comments stripped."""
    from tests.source_scan import code_only, handler_sources
    return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())


# The live position from the screenshots.
ENTRY, MARK, DIRECTION, LEV, MARGIN = 0.51754, 0.51820, "SHORT", 20.0, 24.97


def test_the_two_cards_now_agree():
    """The defect itself: same inputs, same answer, whichever card asks."""
    assert round(ret_pct(ENTRY, MARK, DIRECTION, LEV), 2) == -2.55
    assert round(pnl_usd(ENTRY, MARK, DIRECTION, MARGIN, LEV), 2) == -0.64


def test_the_percent_and_the_dollar_share_one_basis():
    """The property, not the arithmetic: ROE x margin must reproduce the
    dollar. If either helper changes basis, this fails — which is what nobody
    had when the dollar was fixed alone."""
    for lev in (1.0, 3.0, 5.0, 20.0, 125.0):
        for direction in ("LONG", "SHORT"):
            pct = ret_pct(ENTRY, MARK, direction, lev)
            usd = pnl_usd(ENTRY, MARK, direction, MARGIN, lev)
            assert abs((pct / 100.0) * MARGIN - usd) < 1e-9, (lev, direction)


def test_leverage_actually_scales_the_percent():
    """The bug was a percent that ignored leverage entirely. If this stops
    holding, the card has silently gone back to reporting the price move."""
    unlev = ret_pct(ENTRY, MARK, DIRECTION, 1.0)
    lev20 = ret_pct(ENTRY, MARK, DIRECTION, 20.0)
    assert abs(lev20 - unlev * 20.0) < 1e-9
    assert round(unlev, 2) == -0.13, "the old, wrong headline"
    assert round(lev20, 2) == -2.55, "the right one"


def test_direction_is_respected():
    """A SHORT gains when price falls. Getting this backwards would report a
    winning position as losing, with a red stripe to match — colour is a claim."""
    up_short = ret_pct(1.0, 1.10, "SHORT", 1.0)
    up_long = ret_pct(1.0, 1.10, "LONG", 1.0)
    assert up_short < 0 < up_long
    assert abs(up_short + up_long) < 1e-9, "symmetric about the entry"


def test_the_two_helpers_agree_about_an_unusable_input():
    """They must not disagree about what a missing leverage or price MEANS.

    Both treat a non-positive leverage as 1.0 — deliberately the same
    convention, deliberately verified together. This is the one place a
    manufactured default is acceptable, and only because the alternative is two
    helpers that quietly diverge on the same bad input.
    """
    for lev in (0, None, -5):
        pct = ret_pct(ENTRY, MARK, DIRECTION, lev)
        usd = pnl_usd(ENTRY, MARK, DIRECTION, MARGIN, lev)
        assert abs((pct / 100.0) * MARGIN - usd) < 1e-9, lev
    # An unusable PRICE is zero from both, not a fabricated move.
    for bad_entry, bad_mark in ((0, MARK), (ENTRY, 0), (-1, MARK)):
        assert ret_pct(bad_entry, bad_mark, DIRECTION, LEV) == 0.0
        assert pnl_usd(bad_entry, bad_mark, DIRECTION, MARGIN, LEV) == 0.0


def test_the_helpers_are_neighbours_so_they_cannot_drift_apart():
    """A source check, and the one thing here a unit test cannot reach.

    The whole failure was that the dollar's fix did not travel to the percent.
    Keeping the two definitions adjacent is what makes the next person editing
    one see the other.
    """
    src = _leaf_src()
    pct_at = src.index("def _leveraged_return_pct(")
    usd_at = src.index("def _leveraged_pnl_usd(")
    between = src[min(pct_at, usd_at):max(pct_at, usd_at)]
    assert between.count("\ndef ") <= 1, (
        "the percent and dollar helpers have drifted apart in the file; they "
        "are a pair and the bug was exactly that one was fixed without the "
        "other")


def test_the_detail_card_routes_through_the_helper():
    """Reachability: the helper is only a fix if the card calls it."""
    assert "def _leveraged_return_pct(" in _leaf_src()
    src = _handler_src()
    assert src.count("_leveraged_return_pct(") >= 1, (
        "expected at least one call site on the handler class — a helper "
        "nothing calls leaves the card exactly as broken as it was")
    # The rescale must come AFTER the dollar is computed, because that is where
    # leverage is finally known; before it, leverage is not yet resolved.
    usd_call = src.index("pnl_usd = _leveraged_pnl_usd(_entry, last_px, _dir, sz, leverage)")
    pct_call = src.index("pnl_pct = _leveraged_return_pct(_entry, last_px, _dir, leverage)")
    assert usd_call < pct_call, (
        "the percent is rescaled before leverage is resolved — it would be "
        "multiplied by a stale or default value")


def test_the_price_based_readouts_are_not_leveraged():
    """Distance-to-stop and R:R are genuinely price facts. Multiplying THOSE by
    leverage would be the same mistake pointed the other way."""
    src = _handler_src()
    block = src[src.index("pnl_pct = _leveraged_return_pct(_entry, last_px, _dir, leverage)"):]
    block = block[:2000]
    assert "sl_dist = abs(last_px - _sl) / last_px * 100 * leverage" not in block
    assert "_leveraged_return_pct(_entry, _sl" not in block
