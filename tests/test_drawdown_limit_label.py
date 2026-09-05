"""The LIMIT beside a reading must be the limit that is enforced.

Housekeeping sweep after #959, applying the same lesson one level over.
#959 fixed the drawdown NUMERATOR — /status was showing the paper figure
while the breaker gated on the live one. The DENOMINATOR was wrong too and
I missed it:

    max_drawdown=CONFIG.risk.max_daily_loss_pct

That is the DAILY-LOSS cap, a different control entirely. So the card read

    Drawdown: +0.0% / +5.0% limit

while the live drawdown breaker was set at 7%. It advertised a TIGHTER cap
than exists — an operator watching for a halt at 5% would have had 2% of
unannounced room — and the gauge bar divides by the same number, so the bar
was wrong as well.

Both halves of that line now come from the gate: drawdown_status() supplies
the reading and effective_limit_pct supplies the cap, the latter already
accounting for live-vs-paper and any runtime operator override.
"""

import re
from pathlib import Path

from bot.config import CONFIG
from bot.formatters.rich_cards import render_status_card


SRC = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")


def _card(drawdown, limit):
    return render_status_card(
        mode="LIVE", active=True, equity=450.69, open_positions=0,
        daily_pnl=0.2, drawdown=drawdown, max_drawdown=limit,
        market_bias="Normal")


def test_the_daily_loss_cap_is_no_longer_used_as_the_drawdown_limit():
    assert "max_drawdown=CONFIG.risk.max_daily_loss_pct" not in SRC, (
        "the daily-loss cap and the drawdown cap are different controls; "
        "labelling one as the other advertises a limit nothing enforces")
    assert "max_drawdown=drawdown_limit," in SRC


def test_the_limit_comes_from_the_gate_with_a_drawdown_shaped_fallback():
    """DRIVEN, not sliced.

    This bounded a source window with `SRC.index("drawdown_limit = CONFIG.risk")`
    and broke the moment the resolution moved into a shared helper — while all
    three properties it names survived intact. A window anchored on one
    implementation's exact line is a claim about how the code is spelled, not
    about what it does. The behaviour is the same three assertions, driven.
    """
    from bot.formatters.drawdown_card import resolve_display_drawdown as _r

    # The preferred source is the number the breaker actually enforces...
    assert _r(3.1, {"drawdown_pct": 3.1, "drawdown_source": "live",
                    "effective_limit_pct": 7.0}, 99.0)[2] == 7.0

    # ...and it must survive the reporter being unavailable, falling back to
    # the DRAWDOWN cap the caller hands it.
    assert _r(3.1, None, 7.0)[2] == 7.0
    assert _r(3.1, {}, 7.0)[2] == 7.0

    # A zero or negative "limit" is not a limit; it would make the gauge
    # divide by zero and print a cap nothing enforces.
    assert _r(3.1, {"effective_limit_pct": 0}, 7.0)[2] == 7.0
    assert _r(3.1, {"effective_limit_pct": -1}, 7.0)[2] == 7.0


def test_the_caller_hands_the_seam_a_drawdown_cap_not_a_daily_loss_one():
    """The fallback's SHAPE is the #959 lesson and lives at the call site.

    Driving cannot reach this: the seam takes whatever default it is given,
    so which CONFIG field is passed is a wiring fact. Anchored to the call
    itself rather than a byte window.
    """
    from tests.source_scan import code_only
    code = code_only(SRC)
    i = code.index("resolve_display_drawdown(")
    call = code[i:i + 300]
    assert "max_drawdown_pct" in call, \
        "the fallback must still be a DRAWDOWN cap, not a daily-loss one"
    assert "max_daily_loss_pct" not in call


def test_the_two_caps_really_are_different_numbers():
    """If these ever coincided the bug would be invisible; they do not."""
    assert CONFIG.risk.max_daily_loss_pct != CONFIG.risk.live_max_drawdown_pct
    assert CONFIG.risk.max_daily_loss_pct != CONFIG.risk.max_drawdown_pct


def test_the_rendered_line_states_both_enforced_numbers():
    out = _card(9.08, 7.0)
    line = next(l for l in out.split("\n") if "Drawdown" in l)
    assert "+9.1%" in line and "+7.0%" in line
    assert "5.0" not in line, "the daily-loss cap must not appear on this line"


def test_the_gauge_divides_by_the_same_limit_it_prints():
    """A bar drawn against a different denominator lies twice."""
    over = _card(7.5, 7.0)      # past the cap
    under = _card(1.0, 7.0)     # comfortable
    def filled(card):
        bar = next(l for l in card.split("\n") if "│" in l)
        return bar.count("█") if "█" in bar else bar.count("═")
    assert filled(over) >= filled(under), "the bar must track the printed ratio"


def test_a_reading_over_the_cap_is_visible_as_such():
    line = next(l for l in _card(9.08, 7.0).split("\n") if "Drawdown" in l)
    nums = [float(x) for x in re.findall(r"([0-9.]+)%", line)]
    assert nums[0] > nums[1], (
        "an operator must be able to read 'over the limit' straight off the "
        "card — that is the whole point of putting the two together")
