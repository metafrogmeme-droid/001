"""The /performance card must not publish figures nobody computed.

RC-2026-009 and RC-2026-010, which share a card and a file.

Three defects, all measured by executing the original expressions at HEAD
rather than reading them:

  Week tile  -> `$+0.00` color=green
      The paper branch set `"week_pnl": 0.0` as a LITERAL -- nothing computes a
      week for paper -- and the tile coloured it `"green" if week_pnl >= 0`.
      `0.0 >= 0` is true, so an un-computed number was published in the colour
      that claims a profitable week. Colour is a claim.

  Hero       -> label='Total PnL' value=$-412.30
      `_tp = data.get("total_pnl", data.get("today_pnl", 0.0))`. The paper dict
      has no `total_pnl`, so TODAY'S figure was published under an all-time
      label.

  Win tile   -> TypeError: unsupported format string passed to NoneType
      The live branch honestly sets `win_rate = None` when no close is
      scoreable (telegram_handler.py:12547). `f"{None:.0f}%"` raises, the
      `except Exception` at 12690 swallows it, and the ENTIRE card disappears.
      Being honest upstream deleted the card -- so the honest path was the one
      that looked broken.

The card was assembled inline inside a 12,000-line handler, so none of this
could be planted and read. `performance_card_payload` is that seam.
"""
import pytest

from bot.formatters.performance_card import performance_card_payload


def _tile(payload, label_startswith):
    for t in payload["tiles"]:
        if t["label"].startswith(label_startswith):
            return t
    raise AssertionError(
        f"no tile labelled {label_startswith!r} in {[t['label'] for t in payload['tiles']]}"
    )


# ── RC-2026-009: a figure nobody computed ─────────────────────────────────

def test_an_uncomputed_week_is_not_published_as_zero():
    p = performance_card_payload({"today_pnl": -412.30, "week_pnl": None})
    week = _tile(p, "Week")
    assert "0.00" not in week["value"], (
        f"week tile reads {week['value']!r} for a week nobody computed"
    )
    assert week["value"] == "—"


def test_an_uncomputed_week_is_not_painted_green():
    p = performance_card_payload({"today_pnl": -412.30, "week_pnl": None})
    assert _tile(p, "Week")["color"] == "gray", (
        "an unknown figure carries a colour that claims a result"
    )


def test_a_measured_flat_week_is_not_a_profit():
    """0.00 IS a real result -- but it is break-even, and green says profit."""
    p = performance_card_payload({"today_pnl": 0.0, "week_pnl": 0.0})
    week = _tile(p, "Week")
    assert week["value"] == "$+0.00"
    assert week["color"] == "white", "a break-even week is painted as a gain"


def test_a_measured_week_still_reports_normally():
    gain = _tile(performance_card_payload({"week_pnl": 120.5}), "Week")
    loss = _tile(performance_card_payload({"week_pnl": -80.0}), "Week")
    assert gain["value"] == "$+120.50" and gain["color"] == "green"
    assert loss["value"] == "$-80.00" and loss["color"] == "red"


def test_todays_figure_is_not_published_under_an_all_time_label():
    p = performance_card_payload({"today_pnl": -412.30})   # no total_pnl
    assert "Total" not in p["hero"]["label"], (
        f"hero label is {p['hero']['label']!r} while the value is today's figure"
    )
    assert p["hero"]["value"] == "$-412.30"


def test_a_real_all_time_total_keeps_its_label():
    p = performance_card_payload({"today_pnl": -412.30, "total_pnl": 980.0})
    assert p["hero"]["label"] == "Total PnL"
    assert p["hero"]["value"] == "$+980.00"


# ── RC-2026-010: honesty upstream must not delete the card ────────────────

def test_an_unscored_win_rate_does_not_destroy_the_card():
    p = performance_card_payload({"win_rate": None, "today_pnl": 1.0})
    assert p["tiles"], "the card came back with no tiles"
    assert _tile(p, "Win Rate")["value"] == "—"


def test_an_unscored_win_rate_is_not_zero_percent():
    wr = _tile(performance_card_payload({"win_rate": None}), "Win Rate")
    assert wr["value"] != "0%", "an unscored win rate reads as a measured 0%"
    assert wr["color"] == "gray"


def test_a_measured_win_rate_still_renders():
    wr = _tile(performance_card_payload({"win_rate": 62.5}), "Win Rate")
    assert wr["value"] == "62%"
    assert wr["color"] == "cyan"


def test_a_genuine_zero_win_rate_is_reported_not_hidden():
    """0% over real scored closes is a measurement and must survive."""
    wr = _tile(
        performance_card_payload({"win_rate": 0.0, "win_rate_scored": 8}), "Win Rate"
    )
    assert wr["value"] == "0%"
    assert wr["color"] != "gray", "a measured 0% is being shown as unknown"


def test_the_scored_denominator_is_carried_when_partial():
    wr = _tile(
        performance_card_payload(
            {"win_rate": 50.0, "win_rate_unscored": True, "win_rate_scored": 4}
        ),
        "Win Rate",
    )
    assert "4" in wr["label"], f"label {wr['label']!r} hides how many were scoreable"


# ── the shape the renderer requires ───────────────────────────────────────

def test_every_colour_is_one_the_renderer_knows():
    from bot.formatters.signal_card import _STAT_COLORS

    payloads = [
        {"win_rate": None, "week_pnl": None, "today_pnl": None},
        {"win_rate": 50.0, "week_pnl": 1.0, "today_pnl": -1.0, "total_pnl": 0.0},
    ]
    for data in payloads:
        p = performance_card_payload(data)
        for t in p["tiles"] + [p["hero"]]:
            assert t["color"] in _STAT_COLORS, (
                f"{t['label']}: colour {t['color']!r} is not in _STAT_COLORS, so "
                "the renderer would silently fall back to white"
            )


def test_a_completely_empty_reading_states_nothing():
    """Every field missing must not produce a card full of confident zeros."""
    p = performance_card_payload({})
    for t in p["tiles"]:
        assert "0.00" not in t["value"], f"{t['label']} invented {t['value']!r}"


# ── the text card, which is the fallback when the PNG cannot render ───────

def test_the_text_card_survives_a_fully_unmeasured_reading():
    """Honest data must not take the fallback down.

    Making the paper branch honest broke `render_performance` in THREE places
    that all had the same shape as the finding being fixed:

      _money(None) / _pnl_arrow(None)  -> TypeError
      `today != 0` was True for None   -> arithmetic on None in the sparkline
      `.get("best_pair", "N/A")`       -> the default only fires on a MISSING
                                          key, so an explicit None went
                                          straight into a concatenation

    Each would have been swallowed by a caller's `except` and shown the
    operator nothing -- which is how the honest value ends up looking like the
    bug and a confident zero looks like the fix.
    """
    from bot.warroom.warroom_bot import render_performance

    out = render_performance({
        "today_pnl": None, "week_pnl": None, "win_rate": None,
        "win_rate_scored": 0, "win_rate_unscored": 2, "trades_today": 2,
        "best_pair": None, "worst_pair": None,
    })["text"]
    assert "$0.00" not in out and "$+0.00" not in out
    assert "0%" not in out


def test_the_text_card_still_reports_measured_values():
    from bot.warroom.warroom_bot import render_performance

    out = render_performance({
        "today_pnl": -412.30, "week_pnl": 120.5, "win_rate": 62.5,
        "trades_today": 8, "best_pair": "BTC", "worst_pair": "ETH",
    })["text"]
    assert "412.30" in out and "62%" in out and "BTC" in out


def test_unknown_and_measured_flat_do_not_share_a_glyph():
    """A flat day and a day nobody measured must be distinguishable."""
    from bot.warroom.warroom_bot import _money, _pnl_arrow, _spark

    assert _pnl_arrow(None) != _pnl_arrow(0.0)
    assert _spark(None) != _spark(0.0)
    assert _money(None) != _money(0.0)
