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
    vanished entirely the test above would pass while nothing worked."""
    assert "_last_prices.get(pos.asset)" in TH
    assert "live_prices.get(pos.symbol)" in SR


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

def test_status_omits_every_price_derived_figure_when_unpriced():
    block = SR[SR.index("_mark = live_prices.get(pos.symbol)"):]
    block = block[:block.index('lines.append("\\n".join(_row))')]
    for name in ("upnl", "upnl_pct", "sl_dist_pct", "tp_dist_pct", "rr_live"):
        assert f"{name} = None" in block or f"{name} = tp_dist_pct = rr_live = None" in block \
            or "sl_dist_pct = tp_dist_pct = rr_live = None" in block, (
            f"{name} is still computed from a fallback price when unpriced")
    assert "⚪" in block, (
        "the PnL icon must be neutral when there is no PnL — green or red both "
        "assert a direction nobody measured")


def test_status_still_reports_the_facts_that_do_not_need_a_mark():
    """OMIT, not blank. Entry, SL, TP, size, leverage and quantity are all true
    without a price, and dropping the whole position would be its own dishonesty
    — the operator would not know it exists."""
    block = SR[SR.index("_row = ["):SR.index('lines.append("\\n".join(_row))')]
    for fact in ("pos.entry_price", "pos.stop_loss", "pos.take_profit",
                 "pos.quantity", "_money(cost)"):
        assert fact in block, f"{fact} needs no mark and must still be shown"
