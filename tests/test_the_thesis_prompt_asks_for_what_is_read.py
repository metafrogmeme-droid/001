"""The thesis prompt asks for what the bot reads, and the counter-case is shown.

The analyzer's system prompt listed EIGHT output keys -- entry, stop, target,
signals and order type among them -- while `_parse_llm_response` reads three and
`THESIS_JSON_SCHEMA` (the structured-output path) forbids any other with
``additionalProperties: false``. The engine sets levels and size itself, from
ATR and its risk rules, so the model's levels were written and discarded, and
the prompt's own numbers disagreed with the engine: "Minimum 1.2:1" and "TP at
least 1.2x the SL distance" against a per-strategy minimum of 1.5 for a swing.

What it asks for now: exactly the schema's keys; a no-trade that names what the
setup lacks (it reaches /whynot as the refusal's reason); and a reasoning that
ends "Against:" with the strongest reason the trade fails. The cards cut the
prose at 150-280 characters, and a counter-case written last is the part a cut
removes, so every card that cuts gives it its own line.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bot.core.analyzer import THESIS_JSON_SCHEMA, Analyzer
from bot.formatters.thesis_text import split_counter_case
from bot.utils.models import MarketSignal

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _system_prompt() -> str:
    tree = ast.parse((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    hits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_TRADING_SYSTEM_PROMPT"]
    assert len(hits) == 1, len(hits)
    return ast.literal_eval(hits[0])


def _output_keys(prompt: str) -> list[str]:
    block = prompt[prompt.index("## Output Format"):prompt.index("If no clear setup")]
    return [line[2:].split(":", 1)[0] for line in block.splitlines() if line.startswith("- ")]


# ── the contract ────────────────────────────────────────────────────────────

def test_the_prompt_asks_for_exactly_the_keys_the_schema_allows():
    assert _output_keys(_system_prompt()) == list(THESIS_JSON_SCHEMA["required"])


@pytest.mark.parametrize("gone", ["entry_price", "stop_loss", "take_profit",
                                  "signals_used", "order_type", "1.2:1", "1.2x the SL"])
def test_nothing_the_engine_owns_is_asked_of_the_model(gone):
    assert gone not in _system_prompt()


def test_a_no_trade_names_what_the_setup_lacks():
    p = _system_prompt()
    assert '"direction": null' in p and "missing:" in p
    assert "No actionable setup" not in p


def test_the_reasoning_ends_with_the_counter_case():
    assert '"Against: "' in _system_prompt()


def test_the_models_own_skip_threshold_is_kept():
    # Not the engine's floor (that applies to the blended confidence) but the
    # model's filter on its own score; dropping it would let low-confidence
    # directional answers into the blend, which loosens the gate.
    assert "below 0.55, return direction null" in _system_prompt()


def test_the_data_prompts_reply_line_says_the_same():
    signal = MarketSignal(symbol="BTC/USDT", price=65000, change_pct_24h=2.5,
                          volume_usd_24h=1e9, volume_spike=True)
    tail = Analyzer._build_prompt(signal, {"regime": "RANGE", "confluence": 0.5}).splitlines()
    reply = "\n".join(tail[tail.index(next(ln for ln in tail if ln.startswith("Respond in json"))):])
    assert "null" in reply and "Against:" in reply


# ── the split ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prose,body,against", [
    (None, None, None),
    ("RSI 58 over VWAP.", "RSI 58 over VWAP.", None),
    ("RSI 58 over VWAP. Against: funding is crowded.", "RSI 58 over VWAP.", "funding is crowded."),
    ("Against: only a counter.", None, "only a counter."),
    ("RSI 58. Against:", "RSI 58.", None),
    # The LAST marker opens the section: a quoted word earlier does not.
    ("Bears argue against: nothing. RSI 58. Against: crowded longs.",
     "Bears argue against: nothing. RSI 58.", "crowded longs."),
])
def test_the_counter_case_is_split_off(prose, body, against):
    assert split_counter_case(prose) == (body, against)


# ── every card that cuts shows it ───────────────────────────────────────────

LONG = "[gpt|TREND_UP|swing|momentum|C=0.70] " + "RSI 58 rising, volume 1.8x, price over VWAP. " * 8
REASONING = LONG + "Against: funding +0.04% with longs crowded."


def test_the_card_quote_keeps_the_counter_case_past_its_cut():
    from bot.skills.skill_registry import _thesis_bq
    out = _thesis_bq(REASONING, 200)
    assert "funding +0.04% with longs crowded." in out
    assert "Against:" not in out.split("</blockquote>")[0]


def test_a_thesis_without_a_counter_case_renders_as_before():
    from bot.skills.skill_registry import _thesis_bq
    assert _thesis_bq("[a|b] RSI 58 over VWAP.", 250) == "<blockquote>RSI 58 over VWAP.</blockquote>"


def test_the_counter_case_is_escaped():
    from bot.skills.skill_registry import _thesis_bq
    assert "&lt;b&gt;" in _thesis_bq("[a|b] x. Against: <b>crowded</b>", 250)


def test_the_fill_explanation_carries_it_on_its_own_line():
    from bot.guardian.explain_fill import explain
    why = explain({"symbol": "BTC/USDT", "idea": {"reasoning": REASONING}})["why"]
    assert "Against: funding +0.04% with longs crowded." in why
    assert not any(w.startswith("Thesis:") and "Against:" in w for w in why)


def test_the_signal_text_fallback_splits_it_too():
    # The fallback is a branch of a 400-line handler behind a card renderer
    # and an exchange; that it asks the split, and prints both halves, is a shape.
    from tests.source_scan import code_only
    src = code_only((ROOT / "bot" / "skills" / "trading_commands.py").read_text())
    i = src.index("split_counter_case(thesis_prose(idea.reasoning))")
    window = src[i:i + 900]
    assert "_why[:150]" in window and "_against[:150]" in window
