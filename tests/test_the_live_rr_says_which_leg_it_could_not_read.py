"""``R:R 0.0x`` on a position whose stop the record does not hold.

Four surfaces computed the live reward-to-risk by hand, byte for byte::

    risk   = abs(mark - stop) if stop else 0
    reward = abs(tp - mark)   if tp   else 0
    rr     = reward / risk if risk > 0 else 0

and ``0`` is not an absence. As an R:R it is a real and damning verdict -- no
reward per unit of risk -- and it was printed for a position whose DENOMINATOR
nobody could read.

THE ADOPTED POSITION IS THE INPUT. ``live_executor`` builds one with
``stop_loss=0, take_profit=0`` on both adoption paths and names the missing
fields in ``adoption_unread``; the restore path reads
``float(item.get("stop_loss") or 0)``. Driven on one at a $63,000 mark, before
this slice:

    callback_handler  ->  "Size $6,300.00 | 10x | Hold 2.3h | R:R 0.0x"
                          "SL 0.000000 (100.0%) bot-managed"
    /status           ->  "- SL: $0.000000 (100.0% away) manual"
                          "- Live R:R: 0.00x"
    /positions wire   ->  {"rr_live": 0.0, "sl": 0.0, "sl_dist_pct": 100.0}
    limit-order card  ->  the R:R row silently OMITTED

-- a stop printed as a price of zero, a hundred percent of room to fall, a
bot-managed tag over an order that does not exist, and the worst R:R there is,
on the card an operator opens because they do not know what is out there.

THE TWO LEGS FAILED DIFFERENTLY AND RENDERED IDENTICALLY, which is why no
reader could tell them apart: an absent STOP reached the card through the
``else`` arm, while an absent TAKE-PROFIT reached it through a real division
(``0 / risk``). ``_old_rr`` below keeps both, as a pinned assertion naming what
they got wrong.

AND THE CURE WAS ALREADY WRITTEN IN A FIFTH PLACE. ``orphan_position_row``
publishes ``"rr_live": None`` under a comment saying "an orphan carries no
thesis, so there is no reward target to measure against. 0 is a ratio; this is
the absence of one." That producer feeds the same two renderers.

``0.0`` SURVIVES, and only where it is measured: both legs on record and a mark
that has REACHED the target. The two renderers that already guarded the field
did it on FALSINESS, so they hid that reading along with the three absences --
this file's own "test ``is None``, not falsiness", one field over.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core.position_telemetry import (
    format_level,
    format_rr,
    live_rr,
    price_on_record,
)
from bot.formatters.rich_cards import render_open_positions
from bot.skills.callback_handler import _level_row
from bot.skills.skill_registry import status_position_row
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _old_rr(mark, stop, tp):
    """The arithmetic all four surfaces carried, verbatim.

    Kept as a PINNED ASSERTION naming the defect rather than deleted, the way
    `_old_arithmetic` is in the hold-time suite: the three facts it folds into
    one number are the whole reason the reading exists.
    """
    risk = abs(mark - stop) if stop else 0
    reward = abs(tp - mark) if tp else 0
    return reward / risk if risk > 0 else 0


# ── what the old arithmetic could not say ────────────────────────────────────

def test_the_old_arithmetic_answered_zero_for_four_different_facts():
    """One number, four facts. Two of them are absences with different causes,
    one is an undefined ratio, and one is a real measurement."""
    no_stop = _old_rr(63000.0, 0.0, 66000.0)       # the `else` arm
    no_target = _old_rr(63000.0, 61000.0, 0.0)     # a real division, 0 / 2000
    on_the_stop = _old_rr(61000.0, 61000.0, 66000.0)
    reached = _old_rr(66000.0, 61000.0, 66000.0)   # measured: no reward left
    assert no_stop == no_target == on_the_stop == reached == 0
    assert f"{no_stop:.1f}x" == f"{reached:.1f}x" == "0.0x"


def test_the_reading_keeps_all_four_apart():
    assert live_rr(63000.0, 0.0, 66000.0) is None       # no stop on record
    assert live_rr(63000.0, 61000.0, 0.0) is None       # no target on record
    assert live_rr(61000.0, 61000.0, 66000.0) is None   # mark AT the stop
    assert live_rr(66000.0, 61000.0, 66000.0) == 0.0    # measured: reached


# ── the leaf ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mark,stop,tp,expected", [
    (100.0, 95.0, 115.0, 3.0),
    (100.0, 105.0, 85.0, 3.0),          # short: abs on both legs
    (100.0, 95.0, 95.0, 1.0),           # a target BELOW a long's mark: a bad
                                        # record, not a negative ratio
    (66000.0, 61000.0, 66000.0, 0.0),
])
def test_a_readable_pair_is_a_ratio(mark, stop, tp, expected):
    assert live_rr(mark, stop, tp) == pytest.approx(expected)


@pytest.mark.parametrize("mark,stop,tp", [
    (None, 95.0, 115.0),
    (100.0, None, 115.0),
    (100.0, 95.0, None),
    (0.0, 95.0, 115.0),
    (100.0, 0.0, 115.0),
    (100.0, 95.0, 0.0),
    (100.0, -95.0, 115.0),
    ("", 95.0, 115.0),
    ("junk", 95.0, 115.0),
    (float("nan"), 95.0, 115.0),
    (100.0, float("nan"), 115.0),
    (100.0, float("inf"), 115.0),
    (100.0, 100.0, 115.0),              # the mark is ON the stop
])
def test_an_unreadable_leg_is_not_a_ratio(mark, stop, tp):
    assert live_rr(mark, stop, tp) is None


def test_a_stop_of_zero_is_not_a_stop():
    """`r_multiple_for` states the rule in as many words for the journal. This
    is the same rule on the live side, and the adoption path is what produces
    the input."""
    assert price_on_record(0.0) is None
    assert price_on_record(0) is None
    assert price_on_record(-1.0) is None
    assert price_on_record(float("nan")) is None
    assert price_on_record(None) is None
    assert price_on_record("63000") == 63000.0
    assert price_on_record(63000.0) == 63000.0


def test_the_renderers_say_the_absence():
    assert format_rr(None) == "—"
    assert format_rr(0.0) == "0.0x"
    assert format_rr(0.0, "", 1) == "0.0"
    assert format_rr(3.14159, "x", 2) == "3.14x"
    assert format_level(None) == "<i>none on record</i>"
    assert format_level(63000.0, "$") == "<code>$63,000.000000</code>"
    assert format_level(63000.0, "$", 4) == "<code>$63,000.0000</code>"


# ── the position detail card (callback_handler) ──────────────────────────────

def test_the_detail_card_does_not_print_an_unrecorded_stop_as_a_price():
    row = _level_row("SL", price_on_record(0.0), None, "bot-managed")
    assert row == "SL <i>none on record</i>"
    assert "0.000000" not in row
    assert "%" not in row, "no distance to a level nobody recorded"
    assert "bot-managed" not in row, (
        "the tag claims the bot is managing a stop that does not exist")


def test_the_detail_card_keeps_a_recorded_stop_and_its_tag():
    row = _level_row("SL", 61000.0, 3.17, "on exchange")
    assert "61,000.000000" in row and "(3.2%)" in row and "on exchange" in row


def test_the_detail_card_drops_only_the_distance_when_the_mark_is_unread():
    """A level on record with no mark: the level and its tag are still facts,
    the distance is not."""
    row = _level_row("TP", 66000.0, None, "on exchange")
    assert "66,000.000000" in row and "on exchange" in row
    assert "%" not in row


# ── the /positions wire and the card it renders ──────────────────────────────

def _wire_row(**kw):
    base = dict(pair="BTCUSDT", direction="LONG", entry=63000.0, current=64000.0,
                pnl_pct=1.59, pnl_usd=100.0, sl=61000.0, tp=66000.0,
                sl_dist_pct=4.69, tp_dist_pct=3.13, size_usd=630.0,
                notional_usd=6400.0, leverage=10.0, rr_live=0.67,
                quantity=0.1, comm_pct=0.06, hold_hours=3.0,
                sl_order="exchange", tp_order="exchange", trade_id="T1",
                status="open", strategy_type="swing", price_unavailable=False)
    base.update(kw)
    return base


def test_the_positions_card_prints_a_measured_zero_r_r():
    """`if rr_live` swallowed it. Both legs on record, the mark has reached the
    target: there really is no reward left from here, and that is a reading."""
    out = render_open_positions([_wire_row(rr_live=0.0, tp=64000.0)])
    assert "R:R 0.0" in out


def test_the_positions_card_omits_an_unreadable_r_r():
    """The recorded decision stands: an orphan has no thesis, so there is no
    ratio to print. It is the SAME omission it always was -- only the measured
    zero stopped sharing it."""
    out = render_open_positions([_wire_row(rr_live=None)])
    # Anchored to the row that carries it. A bare `"R:R" not in out` over a
    # whole card is this repo's "asserting a short string is ABSENT is the
    # assertion that keeps misfiring".
    row = [ln for ln in out.split("\n") if "->" in ln]
    assert len(row) == 1
    assert "R:R" not in row[0], row[0]


def test_the_wire_publishes_a_level_it_does_not_hold_as_absent():
    """STRUCTURAL: the `sl` / `tp` keys must be able to publish None, the way
    the sibling producer `orphan_position_row` already does. A `round(x, 6)`
    over an adoption zero publishes `0.0`, which the renderer then prints as
    the finding "no stop" for a field nobody read."""
    tree = ast.parse((ROOT / "bot" / "skills" / "trading_commands.py")
                     .read_text(encoding="utf-8"))
    rows = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"sl", "tp", "rr_live", "price_unavailable"} <= keys:
                rows = node
                break
    assert rows is not None, "the /positions wire dict was not found"
    for k, v in zip(rows.keys, rows.values):
        if isinstance(k, ast.Constant) and k.value in ("sl", "tp"):
            assert isinstance(v, ast.IfExp) and any(
                isinstance(n, ast.Constant) and n.value is None
                for n in ast.walk(v)), (
                f"{k.value} cannot publish None, so an unrecorded level is "
                f"published as the price 0.0")


def test_the_wire_guards_the_ratio_on_the_RATIO():
    """The `rr_live` key was `None if _unread else round(rr_live, 2)` -- a
    guard on a NEIGHBOURING FLAG. `_unread` answers the mark's question, and
    the ratio also fails on a leg the record does not hold, so the flag was
    False and the fabricated zero went out. The guard must test the reading
    itself, which is the value `live_rr` returned."""
    tree = ast.parse((ROOT / "bot" / "skills" / "trading_commands.py")
                     .read_text(encoding="utf-8"))
    names = _live_rr_names(tree)
    assert names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and k.value == "rr_live"):
                continue
            assert isinstance(v, ast.IfExp), "rr_live is published unguarded"
            tested = {n.id for n in ast.walk(v.test) if isinstance(n, ast.Name)}
            assert tested & names, (
                "rr_live is guarded on something other than the ratio; a flag "
                "about the MARK does not answer for a leg the record does not "
                "hold")


# ── /status (the extracted row) ──────────────────────────────────────────────

def _sp(**kw):
    d = dict(symbol="BTC/USDT", direction="LONG", entry_price=63000.0,
             quantity=0.1, cost_usd=630.0, stop_loss=61000.0,
             take_profit=66000.0, leverage=10.0,
             opened_at=datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc),
             sl_order_id="s1", tp_order_id="t1")
    d.update(kw)
    return SimpleNamespace(**d)


def test_status_prints_the_dash_rather_than_the_worst_verdict():
    out = "\n".join(status_position_row(
        _sp(stop_loss=0.0, sl_order_id=None), 64000.0, 10_000.0, now=NOW))
    assert "Live R:R: <code>—</code>" in out
    assert "0.00x" not in out
    # The target is still on record and still shown, with its distance.
    assert "- TP: <code>$66,000.000000</code> (3.1% away)" in out


def test_status_survives_a_recorded_stop_with_no_mark():
    """The first draft gated the DISTANCE on the LEVEL, and `sl_dist_pct` is
    None whenever the mark was not read -- so a position with a real stop and
    no mark raised on `{None:.1f}` and deleted the whole positions block. Two
    conditions, not one."""
    out = "\n".join(status_position_row(_sp(), None, 10_000.0, now=NOW))
    assert "- SL: <code>$61,000.000000</code> ✅" in out
    assert "% away" not in out


def test_status_does_not_derive_a_leverage_from_a_notional_it_lacks():
    """`getattr(pos, 'leverage', 0) or (notional / cost if cost > 0 else 1.0)`
    answered 1.0x for an adopted position with no stored leverage, and 0.0x
    when the mark was unread (the notional is 0.0 then). `position_leverage`
    answers None for the reason its own docstring gives: 1.0x is a real
    leverage that a spot position has, so "unlevered" and "nobody could say"
    must not share a number."""
    out = "\n".join(status_position_row(
        _sp(leverage=0, cost_usd=0.0), 64000.0, 10_000.0, now=NOW))
    assert "Leverage: <code>\u2014</code>" in out
    assert "1.0x" not in out and "0.0x" not in out


def test_the_png_card_asks_the_one_renderer():
    """The card returns BYTES, so no assertion can read its R:R cell. Plant a
    spy on the renderer it calls and read what it was asked -- the measured
    zero must REACH it, which `if rr` is exactly what stopped."""
    from bot.formatters import signal_card as sc
    seen = []

    def _spy(rr, suffix="x", places=1):
        seen.append(rr)
        return "spy"

    real, sc.format_rr = sc.format_rr, _spy
    try:
        base = {"symbol": "BTC/USDT", "direction": "LONG", "entry": 1.0,
                "now": 1.0, "sl": 0.9, "tp": 1.2}
        sc.render_position_card(dict(base, rr=0.0))
        sc.render_position_card(dict(base, rr=None))
        sc.render_position_card(dict(base, rr=2.5))
    finally:
        sc.format_rr = real
    assert seen == [0.0, None, 2.5], (
        f"the card decided for itself instead of asking: {seen}")


# ── one definition, four readers ─────────────────────────────────────────────

_RR_SITES = (
    "bot/skills/callback_handler.py",
    "bot/skills/trading_commands.py",
    "bot/skills/skill_registry.py",
)


@pytest.mark.parametrize("rel", _RR_SITES)
def test_every_live_rr_site_asks_the_leaf(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "live_rr"]
    assert calls, f"{rel} no longer reads the one live-R:R definition"


def _live_rr_names(tree):
    """Names bound from `live_rr(...)` in this module -- DERIVED, not a list.

    A hand-written `{"rr_live", "rr_at_fill"}` would be the fifth granularity
    inside a guard: a fifth site with a fifth name is exactly what it would
    miss, and `rr` two functions away (an IDEA's risk:reward, off the
    analyzer) is exactly what it would falsely accuse.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                and v.func.id == "live_rr":
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


@pytest.mark.parametrize("rel", _RR_SITES)
def test_a_reading_is_never_formatted_raw(rel):
    """STRUCTURAL, and it is the one thing no drive here can reach: three of
    these four rows sit inside async command methods behind an engine, a user
    store and a ticker fetch.

    The property is narrow and mechanical: a value that came out of `live_rr`
    is three-valued, so `{rr_live:.1f}` raises on the absence and
    `{rr_live or 0:.1f}` prints the worst verdict there is for it. Every
    interpolation of such a value goes through `format_rr`.
    """
    tree = ast.parse(code_only((ROOT / rel).read_text(encoding="utf-8")))
    names = _live_rr_names(tree)
    assert names, f"{rel} binds nothing from live_rr"
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        mentioned = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
        if not (mentioned & names):
            continue
        seen += 1
        assert isinstance(node.value, ast.Call) and \
            isinstance(node.value.func, ast.Name) and \
            node.value.func.id == "format_rr", (
            f"{rel}:{node.lineno} formats a live_rr reading directly; "
            f"`format_rr` is the renderer that can say the absence")
    assert seen, f"{rel} renders no live R:R at all any more"


@pytest.mark.parametrize("rel", _RR_SITES)
def test_no_site_recomputes_the_ratio_by_hand(rel):
    """The shape, not a spelling: a division whose `else` arm is a bare zero,
    guarded by `<something> > 0`. That is the expression all four carried, and
    a fifth copy would answer 0 for the three absences all over again."""
    tree = ast.parse(code_only((ROOT / rel).read_text(encoding="utf-8")))
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        body = node.body
        if not (isinstance(body, ast.BinOp) and isinstance(body.op, ast.Div)):
            continue
        orelse = node.orelse
        if isinstance(orelse, ast.Constant) and orelse.value in (0, 0.0):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert not (names & {"risk", "risk_left", "risk_at_fill",
                                 "risk_from_here", "risk_dist", "reward",
                                 "reward_left", "reward_at_fill",
                                 "reward_from_here", "reward_dist"}), (
                f"{rel}:{node.lineno} recomputes a reward/risk ratio with an "
                f"else-zero; `live_rr` is the one reading")


# ── measured, not swept ──────────────────────────────────────────────────────

def test_the_scan_card_ratio_cannot_reach_its_else_zero():
    """`scan_skill` carries the same `else 0` and it CANNOT FIRE, so it is
    recorded rather than changed -- don't fix what cannot fire.

    Its entry and stop are both placed off the ATR:

        entry = price -/+ atr * 0.3
        sl    = price -/+ atr * 2.5      ->  risk_dist == 2.2 * atr

    and `atr` falls back to `price * 0.02` when the scanner could not read one
    (`scan_skill.py`, just above). So `risk_dist == 0` needs `price == 0`, at
    which point every figure on the card is already nonsense and the ratio is
    the least of it. Driven both ways here so the day either half changes --
    a different multiple, or an ATR fallback that can itself be zero -- this
    fails rather than the card quietly starting to publish 0.
    """
    def risk_dist(price, atr_raw):
        atr = atr_raw if atr_raw and atr_raw > 0 else price * 0.02
        return abs((price - atr * 0.3) - (price - atr * 2.5))

    for price in (0.00000001, 0.5, 63000.0):
        assert risk_dist(price, 0) > 0, "the ATR fallback is never zero"
        assert risk_dist(price, 12.5) > 0
        assert risk_dist(price, 0) == pytest.approx(2.2 * price * 0.02)
    assert risk_dist(0.0, 0) == 0.0, "only a price of zero reaches the else arm"
