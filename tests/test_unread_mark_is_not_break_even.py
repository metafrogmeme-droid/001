"""An unfetchable price is not a position sitting exactly at its entry.

Five producers did this:

    last = portfolio._last_prices.get(pos.asset, pos.entry_price)

An asset that had never been priced fell back to its own entry, so every figure
derived from the mark came out as a measured, confident zero:

    PnL            +0.00%  ($0.00)     with a GREEN circle beside it
    current price  exactly the entry   "the market is sitting right there"
    SL distance    a real percentage   "your stop is 3.1% away"

A position that may be 15% underwater, rendered as break-even, in green,
because the price lookup missed. This is the house rule's canonical violation —
"unreadable is never zero" — and it survived long enough to appear in five
places because the fallback reads as defensive coding rather than as a claim.

THE RENDERERS WERE ALREADY HONEST. `render_open_positions` has handled
`pnl_pct is None` correctly all along — white circle, no colour, no fabricated
dollar — and `render_position_card` has `pnl_unknown` with a muted accent. The
producers' `.get(k, default)` was the only reason either branch could never be
reached. Correct code, unreachable: the same shape as twenty chart tests that
skipped rather than run.

WHERE IT MATTERED MOST, and the reason /status and the chat prompt are in here
too: one unread mark in `/status` fabricated SIX figures at once (PnL, current,
notional, both SL/TP distances, live R:R), and in `_build_chat_system_prompt`
the fabricated "+0.00%" became the MODEL's evidence about the user's own money.
A number laundered through a sentence is harder to catch than a number on a
card, because the sentence sounds considered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.formatters.rich_cards import render_open_positions
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parent.parent
TH = code_only((ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8"))
SR = code_only((ROOT / "bot" / "skills" / "skill_registry.py").read_text(encoding="utf-8"))


# ── the call-site property: no producer substitutes entry for a missing mark ──

@pytest.mark.parametrize("pattern,label", [
    (r"_last_prices\.get\(\s*pos\.asset\s*,\s*pos\.entry_price\s*\)", "paper mark"),
    (r"prices\.get\(\s*sym\s*,\s*entry_price\s*\)", "orphan mark"),
    (r"live_prices\.get\(\s*pos\.symbol\s*,\s*pos\.entry_price\s*\)", "/status mark"),
])
def test_no_producer_falls_back_to_the_entry_price(pattern, label):
    """A source check because the property is about CALL SITES — that none of
    them substitutes. No single unit test can stand at five places at once, and
    the defect is precisely that one of them stops doing it."""
    hits = re.findall(pattern, TH) + re.findall(pattern, SR)
    assert hits == [], (
        f"{label}: a missing price is being replaced by the entry price, which "
        f"renders as a measured 0.00% — {len(hits)} site(s)")


def test_the_producers_still_read_the_price_at_all():
    """A derived guard that stops matching passes vacuously. If these lookups
    vanished entirely the test above would pass while nothing worked.

    The paper row's mark comes through ``_paper_mark_of`` now — the chat's
    evidence carries the mark's AGE, so the lookup moved into a seam that
    answers ``(mark, age)`` — and a pin on the old ``_last_prices.get(...)``
    spelling failed the day it moved. The seam is DRIVEN instead: a book that
    never priced the asset answers None, never the entry."""
    assert "_paper_mark_of(user_portfolio, pos.asset)" in TH
    assert "live_prices.get(pos.symbol)" in SR
    from types import SimpleNamespace as NS

    from bot.skills.telegram_handler import _paper_mark_of
    book = NS(_last_prices={"ETH/USDT": 3000.0})
    assert _paper_mark_of(book, "ETH/USDT") == (3000.0, None)
    assert _paper_mark_of(book, "BTC/USDT") == (None, None), (
        "a missing price is None, not the entry — and not a zero")


# ── the renderers do the right thing once they can see a None ────────────────

def _row(**over):
    row = {"pair": "APT/USDT", "direction": "SHORT", "entry": 0.51754,
           "size_usd": 24.97, "leverage": 20, "sl": 0.53436, "tp": 0.48767}
    row.update(over)
    return row


def test_an_unknown_pnl_renders_without_a_number_or_a_colour():
    out = render_open_positions([_row(pnl_pct=None, current=None)])
    assert "0.0%" not in out, "an unreadable mark must not print a measured zero"
    assert "+0.00" not in out and "-0.00" not in out
    assert "P&L unknown" in out and "price unavailable" in out
    # Scoped to the P&L segment: the leading icon is the DIRECTION (a SHORT is
    # red whatever its P&L), so scanning the whole string would fail on correct
    # output. The claim under test is that the P&L carries no colour.
    pnl_seg = out[out.index("SHORT |"):out.index("\n", out.index("SHORT |"))]
    assert "\U0001f7e2" not in pnl_seg, (
        "no green — colour is a claim, and green says 'in profit' as loudly as "
        "a number does")
    assert "\U0001f534" not in pnl_seg, "and no red, which claims the opposite"


def test_a_measured_zero_is_still_shown_as_a_measured_zero():
    """THE CONTROL, and the reason `is None` matters rather than falsiness.
    0.0 is falsy AND it is a real, measured, break-even position. If this test
    ever starts failing the fix has thrown away true information."""
    out = render_open_positions([_row(pnl_pct=0.0, current=0.51754, pnl_usd=0.0)])
    assert "0.0%" in out, "a genuinely flat position must still report a zero"
    assert "P&L unknown" not in out, (
        "a measured break-even is not an unknown — conflating them throws away "
        "true information, which is the mirror of the bug being fixed")


def test_a_real_loss_is_unaffected():
    out = render_open_positions([_row(pnl_pct=-2.56, current=0.51820, pnl_usd=-0.64)])
    assert "-2.6%" in out and "$-0.64" in out
    pnl_seg = out[out.index("SHORT |"):out.index("\n", out.index("SHORT |"))]
    assert "\U0001f534" in pnl_seg, "a measured loss keeps its red"


# ── the chat prompt must not teach the model a number it does not have ───────

def test_the_chat_prompt_says_the_price_is_missing_rather_than_implying_zero():
    """This text is the model's evidence about the user's money. 'PnL +0.00%'
    from a failed lookup becomes 'your position is flat' in the reply."""
    assert "CURRENT PRICE UNAVAILABLE" in TH
    block = TH[TH.index("CURRENT PRICE UNAVAILABLE") - 900:
               TH.index("CURRENT PRICE UNAVAILABLE") + 400]
    assert "do not estimate it" in block, (
        "the model will fill a gap it is not told to leave alone")


# ── /status: one unread mark used to fabricate six figures ───────────────────

# ── /status: DRIVEN, because these two were scans and the scans were the ──
# ── defect. Both asserted spellings -- `sl_dist_pct = None`, `_money(cost)` --
# ── inside a ninety-line block that had no seam, and both failed on a change
# ── that made the same figures absent for MORE reasons while the property
# ── they guard held. `status_position_row` is that seam now; it takes the
# ── mark, the equity and a clock, so every case below is one call.

def _sp(**kw):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    d = dict(symbol="BTC/USDT", direction="LONG", entry_price=63000.0,
             quantity=0.1, cost_usd=630.0, stop_loss=61000.0,
             take_profit=66000.0, leverage=10.0,
             opened_at=datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc),
             sl_order_id="s1", tp_order_id="t1")
    d.update(kw)
    return SimpleNamespace(**d)


def _status_row(pos, mark, equity=10_000.0):
    from datetime import datetime, timezone

    from bot.skills.skill_registry import status_position_row
    return "\n".join(status_position_row(
        pos, mark, equity,
        now=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)))


def test_status_omits_every_price_derived_figure_when_unpriced():
    """ONE unread mark fabricated six figures here. Drive it: no mark, and no
    figure that needs one may appear."""
    out = _status_row(_sp(), mark=None)
    assert "price unavailable" in out
    assert "⚪" in out, (
        "the PnL icon must be neutral when there is no PnL — green or red both "
        "assert a direction nobody measured")
    # Nothing computed off a mark. The entry is 63000 and a fallback would put
    # it in the Current cell, make the SL 3.2% away and the R:R 1.5x.
    assert "- Current: <code>—</code>" in out
    assert "% away" not in out, "a distance needs a mark"
    assert "Live R:R: <code>—</code>" in out
    assert "Notional: <code>--</code>" in out, (
        "the notional cell is the notional NOW; falling back to the entry\n"
        "        notional puts two quantities under one label")
    for lie in ("+0.00%", "$0.00", "0.0%", "0.00x"):
        assert lie not in out, f"{lie} is a measurement nobody made"


def test_status_still_reports_the_facts_that_do_not_need_a_mark():
    """OMIT, not blank. Entry, the levels, quantity and margin are all true
    without a price, and dropping the whole position would be its own
    dishonesty — the operator would not know it exists."""
    out = _status_row(_sp(), mark=None)
    assert "63,000.000000" in out, "the entry needs no mark"
    assert "61,000.000000" in out, "the stop needs no mark"
    assert "66,000.000000" in out, "the target needs no mark"
    assert "0.1000" in out, "the quantity needs no mark"
    assert "$630.00" in out, "the recorded margin needs no mark"
    assert "BTC/USDT" in out


def test_status_says_a_level_the_record_does_not_hold_is_not_a_price():
    """An adopted position is built with `stop_loss=0, take_profit=0`. The card
    printed `$0.000000`, `100.0% away` and `Live R:R 0.00x` for it — three
    confident statements, the most reassuring available, about the least
    protected position on the book."""
    out = _status_row(_sp(stop_loss=0.0, take_profit=0.0,
                          sl_order_id=None, tp_order_id=None), mark=64000.0)
    assert "- SL: <i>none on record</i>" in out
    assert "- TP: <i>none on record</i>" in out
    assert "Live R:R: <code>—</code>" in out
    # ANCHORED TO THE TWO ROWS THAT MAKE THE CLAIM. A bare `"0.000000" not in
    # out` matches inside `$63,000.000000` on the Entry row -- this file's own
    # "asserting a short string is ABSENT is the assertion that keeps
    # misfiring", which it did on the first run of this very test.
    _levels = [ln for ln in out.split("\n")
               if ln.startswith("  - SL:") or ln.startswith("  - TP:")]
    assert len(_levels) == 2
    for ln in _levels:
        assert "0.000000" not in ln, f"a level of zero is not a price: {ln}"
        assert "% away" not in ln, f"no distance to a level nobody recorded: {ln}"
    assert "manual" not in out, (
        "the order-status tag claims a stop is being managed; there is none")


def test_status_prints_a_measured_zero_r_r():
    """The ONE case 0.00x is earned: both legs on record and a mark that has
    reached the target. `if rr` would have hidden it with the three absences."""
    out = _status_row(_sp(take_profit=64000.0), mark=64000.0)
    assert "Live R:R: <code>0.00x</code>" in out


def test_status_survives_an_equity_it_could_not_read():
    """`Exposure: {exp_pct:.1f}%` was unconditional over a value that is None
    whenever the equity read failed, so an unreadable equity did not print a
    dash — it raised, and took the whole ACTIVE POSITIONS block with it."""
    out = _status_row(_sp(), mark=64000.0, equity=None)
    assert "Exposure: <code>—</code>" in out
    assert "Leverage: <code>10.0x</code>" in out, "the leverage still reads"


def test_status_does_not_publish_the_notional_under_the_name_size():
    """`cost_usd if cost_usd > 0 else entry * quantity` is the margin OR the
    notional, ten times larger here, chosen by the falsy check whose zero means
    the venue never said. The Exposure beneath it was computed from the same
    value."""
    out = _status_row(_sp(cost_usd=0.0), mark=64000.0)
    assert "- Margin: <code>--</code>" in out, "unrecorded margin stays unrecorded"
    assert "Exposure: <code>—</code>" in out, (
        "an exposure computed from the notional is ten times the truth")
    assert "$6,300.00" not in out.split("Notional")[0], (
        "the notional must not appear on the margin side of the row")
