"""Every idea card says what the fees leave of its reward:risk, in one wording.

12 October: the public post learned to say "6.5x (5.7x after fees)". Every
private card still printed the price ratio alone: the analysis and scan
cards, the signal image, the chart subtitle, the NEW SIGNAL alert, the
`/trade` confirm card and the staged-draft card, which printed no ratio at
all. The ticket and chat cards already said both, from
`trade_costs.net_reward_risk`. Now every card reads `trade_costs.rr_words`.

The chat's ratio corrector made it worse where the bot already said both. The
ticket tool writes "Gross R:R 6.47. Net R:R 5.74."; when the model relayed
it, the corrector checked the net figure against the price ratio and
"corrected" it, so the page said "Net R:R 6.47". A net claim is now left as
written: the fees depend on an order type the page does not state.

Driven where a renderer can be run; the field route is guarded by an AST walk
over `bot/`, with the two sites that still print the gross figure named.
"""
from __future__ import annotations

import ast
import pathlib
import types

import pytest

from bot.core import trade_costs as tc
from bot.nlp.rr_honesty import correct_stated_rr
from bot.utils.models import Direction, TradeIdea
from tests.png_text import capture_text

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The TIA long of 12 October.
TIA = dict(id="T1", asset="TIA/USDT", direction=Direction.LONG,
           entry_price=0.58820, stop_loss=0.58164, take_profit=0.63065,
           confidence=0.71, reasoning="r", strategy_type="swing")


def _idea(**kw) -> TradeIdea:
    idea = TradeIdea(**dict(TIA, **kw))
    idea.blended_confidence_raw = 0.71
    return idea


# ── the one wording ───────────────────────────────────────────────────────


def test_the_wording_says_both_and_names_each_state():
    costed = tc.net_reward_risk(0.58820, 0.58164, 0.63065, order_type="market")
    assert tc.rr_words(costed) == "6.5x (5.7x after fees)"
    assert tc.rr_words(costed, places=2, suffix="") == "6.47 (5.74 after fees)"
    assert tc.rr_words(costed, wrap=lambda s: f"<b>{s}</b>") == "<b>6.5x</b> (5.7x after fees)"
    losing = tc.net_reward_risk(0.58820, 0.58164, 0.58850)
    assert tc.rr_words(losing) == "0.0x (the target does not cover the fees)"
    assert tc.rr_words(None) == "— (a level could not be read)"


def test_an_idea_is_priced_at_its_own_order_type():
    assert tc.idea_rr_words(_idea(order_type="market")) == "6.5x (5.7x after fees)"
    assert tc.idea_rr_words(_idea(order_type="limit")) == "6.5x (6.0x after fees)"


# ── the cards that can be run ─────────────────────────────────────────────


#: The market read the analysis card is drawn over, as
#: `test_an_unread_move_is_not_a_calm_market` draws it.
MARKET = {
    "symbol": "TIA/USDT:USDT", "pair": "TIA", "price": 0.59,
    "high_24h": 0.60, "low_24h": 0.57, "change_pct": 1.0,
    "volume_24h_usd": 1e6, "vol_spike": 1.5, "vwap": 0.585,
    "vwap_pct": 0.8, "rsi": 50.0, "atr": 0.004, "sma9": 0.58,
    "sma20": 0.58, "sma50": 0.58, "bid_depth": 100.0, "ask_depth": 90.0,
    "supports": [(0.57, 0.575)], "resistances": [(0.60,)],
    "structure": "Range-bound", "timeframe": "1H",
}


def test_the_analysis_card_says_both():
    from bot.formatters.rich_cards import render_analysis_card
    card = render_analysis_card(dict(MARKET), _idea())
    assert "- Risk/Reward: 6.5x (5.7x after fees)" in card, card


def test_the_chart_subtitle_says_both():
    from bot.skills.chart_renderer import _idea_meta
    _pair, _dir, subtitle, _levels = _idea_meta(_idea())
    assert "R:R 6.5 (5.7 after fees)" in subtitle, subtitle


def test_the_signal_image_cell_says_both_and_fits():
    from bot.formatters.signal_card import card_rr_text, signal_card_from_idea
    with capture_text() as drawn:
        signal_card_from_idea(_idea(), rank=1)
    assert "6.5 (5.7 after fees)" in [t for t, _ in drawn]
    # The fee-losing sentence would overflow the cell; it says so in fewer words.
    assert card_rr_text(_idea(take_profit=0.58850)) == "0.0 (loses after fees)"


def test_the_scan_image_prices_a_pullback_limit_at_the_maker_rate():
    from bot.formatters.signal_card import render_scan_results_card
    row = {"sym": "TIA/USDT", "dir": "LONG", "entry": 0.5882, "sl": 0.58164,
           "tp": 0.63065, "rr": 6.47, "rsi": 55, "vol_ratio": 1.4, "score": 0.71}
    with capture_text() as limit:
        render_scan_results_card([dict(row, order_type="limit")])
    with capture_text() as market:
        render_scan_results_card([dict(row)])
    assert any(t.startswith("R:R 6.5:1 (6.0:1 after fees)") for t, _ in limit)
    assert any(t.startswith("R:R 6.5:1 (5.7:1 after fees)") for t, _ in market)


def _alert_body(**kw) -> str:
    from bot.core.engine import RuneClawEngine
    from bot.core.proactive_monitor import ProactiveMonitor
    idea = _idea(confidence=0.78, **kw)
    idea.blended_confidence_raw = 0.78
    eng = types.SimpleNamespace(
        _pending_ideas={}, _engine_idea_ids=set(),
        live_executor=types.SimpleNamespace(_last_close_data=None, _positions={}))
    for name in ("_engine_pending_ids", "_register_engine_idea"):
        setattr(eng, name, types.MethodType(getattr(RuneClawEngine, name), eng))
    eng._register_engine_idea(idea)
    alerts = ProactiveMonitor(eng)._check_trade_signals()
    assert alerts, "the drive's own fixture produced no signal"
    return alerts[0].body


def test_the_new_signal_alert_says_both():
    body = _alert_body(order_type="market")
    assert "- R:R Ratio: <code>6.5</code> (5.7 after fees)" in body, body


# ── the chat corrector ────────────────────────────────────────────────────

LEVELS = "Entry 0.5882, stop 0.58164, target 0.63065. "


@pytest.mark.parametrize("claim", [
    "Gross R:R 6.47. Net R:R 5.74.",
    "R:R after fees: 5.74.",
    "That is a 5.74 R:R net of fees.",
])
def test_a_net_claim_is_left_as_written(claim):
    assert correct_stated_rr(LEVELS + claim) == (LEVELS + claim, 0)


def test_a_wrong_gross_claim_is_still_corrected_beside_a_net_one():
    text, n = correct_stated_rr(LEVELS + "R:R 5.10, net 5.74.")
    assert (text, n) == (LEVELS + "R:R 6.47, net 5.74.", 1)
    text, n = correct_stated_rr(LEVELS + "Gross R:R 5.10.")
    assert (text, n) == (LEVELS + "Gross R:R 6.47.", 1)


def test_a_net_column_is_left_and_the_gross_column_is_checked():
    table = ("| Entry | Stop | Target | R:R | Net R:R |\n|---|---|---|---|---|\n"
             "| 0.5882 | 0.58164 | 0.63065 | 5.10 | 5.74 |")
    text, n = correct_stated_rr(table)
    assert n == 1 and text.endswith("| 6.47 | 5.74 |"), text
    net_only = ("| Entry | Stop | Target | Net R:R |\n|---|---|---|---|\n"
                "| 0.5882 | 0.58164 | 0.63065 | 5.74 |")
    assert correct_stated_rr(net_only) == (net_only, 0)


# ── the field route ───────────────────────────────────────────────────────

#: f-strings that still format `risk_reward_ratio` for a person, each with
#: why. A new one fails; a listed one that is gone fails.
GROSS_ON_PURPOSE = {
    # The critique's concern quotes the figure its own 1.5 bar compared.
    ("bot/core/critique.py", "evaluate"): 1,
}


def _formatted_gross_sites() -> dict:
    found: dict = {}
    for path in sorted((ROOT / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.JoinedStr):
                    continue
                for part in node.values:
                    if isinstance(part, ast.FormattedValue) and any(
                            isinstance(n, ast.Attribute) and n.attr == "risk_reward_ratio"
                            for n in ast.walk(part.value)):
                        key = (str(path.relative_to(ROOT)), fn.name)
                        found[key] = found.get(key, 0) + 1
    return found


def test_no_card_formats_the_gross_field_unless_it_is_named():
    found = _formatted_gross_sites()
    assert found == GROSS_ON_PURPOSE, found
