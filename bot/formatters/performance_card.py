"""The /performance PNG card, as a pure payload builder.

RC-2026-009 and RC-2026-010. This card was assembled inline inside
``_cmd_performance`` in a 12,000-line handler, which is why three defects sat
in it that no test could reach -- nothing could plant a reading and look at
what the operator would see.

The rule this file exists to hold, from CLAUDE.md: *unreadable is never zero,
and absent is never a measurement*. A performance card is the surface where
that matters most, because every value on it is a claim about money.

Three states per figure, not two:

    a number   -- measured; render it, colour it by its sign
    None       -- nobody computed it; render an em dash, colour it gray
    0.0        -- MEASURED break-even. Real, and NOT a profit, so not green.

The distinction in that third line is the one the original lost: the week tile
coloured ``"green" if week_pnl >= 0``, and the paper branch fed it the literal
``0.0``, so a figure nothing computed was published in the colour that claims a
profitable week.
"""
from typing import Any, Dict, Optional

# An em dash, not "0", "N/A" or "". Distinct at a glance from every measured
# value on the card.
DASH = "—"


def _num(v: Any) -> Optional[float]:
    """A real number, or None. Never a default.

    Deliberately ``is None``-based rather than truthy: ``0.0`` is falsy and
    ``0.0`` is a real, measured, break-even result.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN is not a measurement either


def _money(v: Any) -> str:
    n = _num(v)
    return DASH if n is None else f"${n:+,.2f}"


def _money_color(v: Any) -> str:
    """Colour is a claim, so unknown gets the one that claims nothing."""
    n = _num(v)
    if n is None:
        return "gray"
    if n > 0:
        return "green"
    if n < 0:
        return "red"
    return "white"        # measured break-even: real, but not a gain


def _pct(v: Any) -> str:
    n = _num(v)
    return DASH if n is None else f"{n:.0f}%"


def performance_card_payload(data: Dict[str, Any],
                             subtitle: str = "") -> Dict[str, Any]:
    """Build the render_stats_card payload for /performance.

    ``data`` is the dict ``_cmd_performance`` assembles for either the live or
    the paper branch. Every field is optional: a missing one means nobody
    measured it, and is rendered as such rather than as zero.
    """
    today = data.get("today_pnl")
    week = data.get("week_pnl")
    total = data.get("total_pnl")

    # The hero used to be `data.get("total_pnl", data.get("today_pnl", 0.0))`
    # under a fixed "Total PnL" label, so the paper branch -- which has no
    # total_pnl -- published TODAY'S figure as the all-time one. The label now
    # follows the number instead of the number following the label.
    if total is not None:
        hero_label, hero_value = "Total PnL", total
    else:
        hero_label, hero_value = "Today PnL", today

    wr = _num(data.get("win_rate"))
    # A partial score is stated with its denominator: "of 4" says how many
    # closes could be scored at all, so a rate over a subset cannot read as a
    # rate over everything.
    wr_label = "Win Rate"
    if data.get("win_rate_unscored"):
        scored = data.get("win_rate_scored")
        wr_label = f"Win Rate (of {scored})" if scored is not None else "Win Rate (partial)"

    trades = data.get("total_trades", data.get("trades_today"))

    return {
        "title": "PERFORMANCE",
        "subtitle": subtitle,
        "hero": {"label": hero_label, "value": _money(hero_value),
                 "color": _money_color(hero_value)},
        "tiles": [
            {"label": "Today PnL", "value": _money(today),
             "color": _money_color(today)},
            {"label": "Week PnL", "value": _money(week),
             "color": _money_color(week)},
            {"label": wr_label, "value": _pct(wr),
             "color": "gray" if wr is None else "cyan"},
            {"label": "Trades", "value": DASH if trades is None else str(trades),
             "color": "gray" if trades is None else "white"},
            # "N/A" is what these carried for "no trades yet". Kept out of the
            # measured vocabulary: an absent pair is an absent reading.
            {"label": "Best", "value": str(data.get("best_pair") or DASH),
             "color": "green" if data.get("best_pair") else "gray"},
            {"label": "Worst", "value": str(data.get("worst_pair") or DASH),
             "color": "red" if data.get("worst_pair") else "gray"},
        ],
    }
