"""/risk must measure each reading against the control that enforces it.

Second sweep, bot side, after the same class was found on /status (#959,
#960). /risk was worse, because the mismatch drove a VERDICT rather than
just a label:

    healthy    = dd < daily_loss_limit
    risk_score = 100 - dd / daily_loss_limit * 100
    gauge      = _gauge('Drawdown', dd, daily_loss_limit)

The reading was `state.max_drawdown_pct` — the PAPER portfolio's monotonic
worst-EVER — and it was measured against the DAILY-LOSS cap. Two independent
faults on one card:

  * wrong SOURCE: in pure-live operation the paper snapshot never moves, so
    dd was ~0 forever and the card reported HEALTHY regardless of the real
    live drawdown. That is the direction that costs money — a risk dashboard
    that cannot report unhealthy is decoration.
  * wrong CONTROL: drawdown compared against the daily-loss cap. Even with a
    correct reading the verdict would have been drawn from the wrong limit.

Both halves now come from drawdown_status(): the reading from the gate, the
cap from effective_limit_pct (which already accounts for live-vs-paper and
any runtime override).

Also fixed: in LIVE mode two independent caps bound the position count — the
risk engine's max_open_positions and the executor's max_live_open_positions.
The card showed only the first. They coincide at the defaults, so nothing was
wrong today, but raising one alone would have had the card promise room the
other refuses.
"""

import re
from pathlib import Path

from bot.config import CONFIG
from bot.warroom.warroom_bot import render_risk


SRC = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")


def _text(**kw):
    base = {"current_drawdown": 0.0, "daily_loss_limit": 5.0,
            "max_open_trades": 5, "open_trades": 0, "leverage_cap": 5}
    base.update(kw)
    return render_risk(base)["text"]


def _status(text):
    return "HEALTHY" if "HEALTHY" in text else "WARNING"


# ── the verdict ───────────────────────────────────────────────────────────

def test_the_verdict_uses_the_drawdown_cap_not_the_daily_loss_cap():
    # 6% drawdown: under the 7% drawdown cap, over the 5% daily-loss cap.
    assert _status(_text(current_drawdown=6.0, drawdown_limit=7.0)) == "HEALTHY"
    # and a genuine breach still reports
    assert _status(_text(current_drawdown=7.5, drawdown_limit=7.0)) == "WARNING"


def test_the_gauge_and_the_limits_block_both_show_the_drawdown_cap():
    text = _text(current_drawdown=6.0, drawdown_limit=7.0)
    gauge = next(l for l in text.split("\n") if "Drawdown" in l and "│" in l)
    assert "/ 7%" in gauge or "/ 7%" in gauge, gauge
    assert "Drawdown" in text and "7.0%" in text, "the cap is stated, not implied"
    # the daily-loss cap is still shown — it is a real limit, just a different one
    assert "Daily Loss" in text and "5.0%" in text


def test_the_health_score_is_computed_against_the_drawdown_cap():
    # Same reading, different caps ⇒ different scores. If the score ignored
    # the cap this would not move.
    a = re.search(r"(\d+)%", _text(current_drawdown=3.5, drawdown_limit=7.0)).group(1)
    b = re.search(r"(\d+)%", _text(current_drawdown=3.5, drawdown_limit=14.0)).group(1)
    assert int(b) > int(a), "more headroom must score healthier"


def test_an_old_caller_degrades_to_the_previous_behaviour():
    """No drawdown_limit supplied ⇒ fall back to dll rather than divide by zero."""
    text = _text(current_drawdown=1.0)          # no drawdown_limit key
    assert _status(text) == "HEALTHY"
    assert "Drawdown" in text


def test_a_zero_cap_never_divides_by_zero():
    text = _text(current_drawdown=1.0, drawdown_limit=0.0, daily_loss_limit=0.0)
    assert "Status" in text, "must still render"


# ── the caller's sources ──────────────────────────────────────────────────

def test_the_reading_comes_from_the_gate_not_the_paper_worst_ever():
    """RE-ANCHORED, and the reason is worth keeping.

    This sliced from `_dd_now = round(state.max_drawdown_pct` — the paper seed
    — to the leverage line, and asserted the gate's reading overwrote it. Its
    intent was always "do not report the paper number as the enforced one";
    its ANCHOR was the paper number itself, so removing the seed entirely —
    a strictly stronger version of what it asks for — broke it.

    The seed is now None, so a failed read stays a failed read instead of
    silently becoming the paper snapshot, and the assertion below says so
    directly rather than relying on an overwrite happening later.
    """
    block = SRC[SRC.index("_dd_now = None"):
                SRC.index('"leverage_cap": CONFIG.exchange.default_leverage,')]
    assert "drawdown_status()" in block, \
        "the paper monotonic worst-ever is ~0 forever in live mode"
    assert '_st["drawdown_pct"]' in block
    assert '_st["effective_limit_pct"]' in block
    assert "except Exception:" in block, "a reporter outage must not break /risk"
    assert "state.max_drawdown_pct" not in block, (
        "the paper snapshot is being used as the fallback again — that is the "
        "substitution this test is named after")


def test_the_card_passes_the_drawdown_limit_through():
    assert '"drawdown_limit": _dd_limit,' in SRC
    assert '"current_drawdown": _dd_now,' in SRC


def test_live_mode_shows_the_BINDING_position_cap():
    block = SRC[SRC.index("_max_trades = CONFIG.risk.max_open_positions"):
                SRC.index('"daily_loss_limit": CONFIG.risk.max_daily_loss_pct,')]
    assert "min(" in block and "max_live_open_positions" in block, \
        "two caps bound the count in live; the binding one is the lower"
    assert "CONFIG.is_live()" in block, "paper mode is unaffected"


def test_the_two_caps_exist_and_are_distinct_controls():
    assert CONFIG.risk.max_daily_loss_pct != CONFIG.risk.max_drawdown_pct
    assert hasattr(CONFIG.execution, "max_live_open_positions")
    assert hasattr(CONFIG.risk, "max_open_positions")
