"""The audit card says which way each proposal moves its guard, from the code.

On 2026-10-15 the nightly card printed:

    LIVE_PERF_REDUCE_WINRATE=0.35 (in force: 0.4)
      win rate of 0.275 is below the 0.40 floor; raising it cuts size
      sooner (tightens)

The model had copied the flag's meaning while proposing to LOWER the floor,
which makes the governor cut size later: a loosening, during a 28%-win
window, printed under the word "tightens". The prompt already asked a
loosening proposal to say so; nothing checked. The same card carried "a loss
streak of 21 crosses the 3-threshold", a run the model had counted out of the
closes itself and set against a per-symbol threshold whose engine counter
cannot read 21.

Now the card computes each proposal's direction from FLAG_UP_DIRECTION and
the value in force and prints it above the rationale, with a warning when
the rationale claims the opposite; and the evidence carries the engine's own
per-symbol counters. Driven through `SelfAudit.render_report` and
`gather_evidence`, both arms each time.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.self_audit as sa
from bot.core.self_audit import SelfAudit

ROOT = Path(__file__).resolve().parents[1]
_BASE = {"return_pct": -1.07, "pf": 0.92, "trades": 15}


def _card(rows):
    return SelfAudit().render_report(
        {"summary": {"n": 40, "win_rate": 0.275, "pf": 0.24, "net_pnl": -15.42}},
        rows, dict(_BASE), "alts_1h")


def _row(flag, value, current, rationale):
    return {"flag": flag, "value": value, "current": current,
            "rationale": rationale, "measured": dict(_BASE)}


def test_the_2026_10_15_card_says_the_floor_cut_loosens():
    card = _card([_row("LIVE_PERF_REDUCE_WINRATE", "0.35", "0.4",
                       "win rate of 0.275 is below the 0.40 floor; raising it "
                       "cuts size sooner (tightens)")])
    assert "lowers it 0.4 → 0.35" in card, card
    assert "<b>LOOSENS</b>" in card and "<b>TIGHTENS</b>" not in card, card
    assert "calls it tightening; the change loosens it" in card, card
    # The computed line comes BEFORE the model's sentence it corrects.
    assert card.index("LOOSENS") < card.index("raising it cuts size sooner")


def test_a_rationale_that_agrees_gets_no_warning():
    card = _card([_row("SYMBOL_LOSS_STREAK_THRESHOLD", "4", "3",
                       "raising it benches later (loosens)")])
    assert "raises it 3 → 4" in card and "<b>LOOSENS</b>" in card, card
    assert "⚠" not in card, card
    # The other direction: a real tightening, said as one.
    card = _card([_row("OF_MIN_DEPTH_USD", "2000", "1000",
                       "deeper books would tighten entries")])
    assert "raises it 1000 → 2000" in card and "<b>TIGHTENS</b>" in card, card
    assert "⚠" not in card, card


def test_a_tightening_called_loosening_is_flagged_too():
    card = _card([_row("REENTRY_COOLDOWN_SECONDS", "1800", "900",
                       "loosen the cooldown to let entries back sooner")])
    assert "<b>TIGHTENS</b>" in card
    assert "calls it loosening; the change tightens it" in card, card


@pytest.mark.parametrize("value,current,effect", [
    ("1", "off", "TIGHTENS"), ("0", "on", "LOOSENS")])
def test_a_switch_says_which_way_turning_it_moves(value, current, effect):
    card = _card([_row("ENTRY_TIMING_ENABLED", value, current, "")])
    assert f"<b>{effect}</b>" in card, card
    assert ("turns it on" if value == "1" else "turns it off") in card


def test_an_unread_value_in_force_is_an_unknown_direction_not_a_guess():
    for flag, value in (("VOLATILITY_GUARD_ATR_PCT", "5"), ("ENTRY_TIMING_ENABLED", "1")):
        # A switch too: an unread value is not "off".
        card = _card([_row(flag, value, None, "")])
        assert "Direction: <b>unknown</b>" in card, card
        assert "LOOSENS" not in card and "TIGHTENS" not in card, card
    # A result from a build that never recorded the value in force: no key,
    # no line at all, rather than a direction computed from nothing.
    row = _row("VOLATILITY_GUARD_ATR_PCT", "5", None, "")
    del row["current"]
    card = _card([row])
    assert "Direction" not in card and "LOOSENS" not in card and "TIGHTENS" not in card


# ── one reading: the table, the meanings and the allow-list agree ─────────

def test_every_allowlisted_flag_has_a_direction_and_nothing_else_does():
    assert set(sa.FLAG_UP_DIRECTION) == set(sa.ALLOWED_FLAGS)
    assert set(sa.FLAG_UP_DIRECTION.values()) <= {"tightens", "loosens"}


@pytest.mark.parametrize("flag", sorted(sa.ALLOWED_FLAGS))
def test_the_meaning_the_model_reads_names_the_same_direction(flag):
    """The model is told a flag's direction in FLAG_MEANINGS; the card computes
    it from FLAG_UP_DIRECTION. Two readings of one fact must say one thing."""
    text = sa.FLAG_MEANINGS[flag]
    if sa.ALLOWED_FLAGS[flag]["type"] == "bool":
        m = re.search(r"turning it on (tightens|loosens)", text)
    else:
        m = re.search(r"raising it[^;]*?\b(tightens|loosens)\b", text)
    assert m, f"{flag}: its meaning names no direction: {text!r}"
    assert m.group(1) == sa.FLAG_UP_DIRECTION[flag], (flag, text)


# ── the evidence carries the engine's own counters ─────────────────────────

def test_the_evidence_carries_the_engines_per_symbol_counters():
    ev = SelfAudit().gather_evidence(SimpleNamespace(
        _symbol_loss_streaks={"BTC/USDT": 2, "ETH/USDT": 0, "SOL/USDT": 1}))
    # Zero is what an unlisted symbol holds, so it is left out, not lost.
    assert ev["symbol_loss_streaks"] == {"BTC/USDT": 2, "SOL/USDT": 1}
    # An engine whose counters are not a readable map is null, never {}.
    ev = SelfAudit().gather_evidence(SimpleNamespace(_symbol_loss_streaks=None))
    assert ev["symbol_loss_streaks"] is None
    assert "symbol_loss_streaks" in sa._SYSTEM_PROMPT


def test_the_audit_reads_the_counter_the_engine_writes():
    """`gather_evidence` reads `_symbol_loss_streaks` off the engine; this pins
    that the live engine is what writes that name, as the counter the
    threshold is compared with, so the read cannot drift to a stale name."""
    tree = ast.parse((ROOT / "bot" / "core" / "engine.py").read_text())
    writes = {n.attr for n in ast.walk(tree)
              if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
              and isinstance(n.value, ast.Name) and n.value.id == "self"}
    assert "_symbol_loss_streaks" in writes
    compared = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)
                and any(isinstance(c, ast.Attribute)
                        and c.attr == "symbol_loss_streak_threshold"
                        for c in n.comparators)]
    assert compared, "the engine no longer compares a streak with the threshold"
