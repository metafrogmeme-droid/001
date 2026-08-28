"""The enforcement inventory: plant a posture, assert what the operator reads.

Two things are being protected here.

1. THE ANSWER IS HONEST. A control that could not be read is never rendered as
   "off", shadow is never counted as armed, and a refinement being off is
   never reported as a missing gate. Each of those is a false claim about
   enforcement on the screen whose only job is to prevent false claims about
   enforcement.

2. THE ANSWER IS COMPLETE. `test_every_risk_flag_is_classified` is a ratchet
   against `bot/config.py`: add an `*_enabled` flag to RiskLimits and this
   fails until somebody says what it does. Without it the card would keep
   claiming to be "the inventory" while quietly missing the newest control —
   which is worse than not having the card, because it is trusted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.formatters.gate_card import headline, render_gate_card
from bot.guardian.gate_inventory import (
    GATES,
    KIND_BEHAVE,
    KIND_REFUSE,
    KIND_SEAL,
    KIND_SIZE,
    KIND_TUNE,
    OFF,
    SHADOW,
    UNKNOWN,
    inventory,
    refusal_summary,
)

REPO = Path(__file__).resolve().parents[1]
VALID_KINDS = {KIND_REFUSE, KIND_SIZE, KIND_TUNE, KIND_SEAL, KIND_BEHAVE}


class FakeRisk:
    """A risk config with exactly the attributes a test grants it."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def all_on(**over):
    vals = {a: True for a in GATES}
    vals["validation_gate_mode"] = "enforce"
    vals.update(over)
    return FakeRisk(**vals)


# ── the table itself ──────────────────────────────────────────────────────

def test_every_risk_flag_is_classified():
    """A ratchet against bot/config.py — the half people forget.

    A new `*_enabled` in RiskLimits that nobody classified would be missing
    from a card that presents itself as the complete answer. Silent omission
    from an inventory is worse than no inventory.
    """
    src = (REPO / "bot" / "config.py").read_text(encoding="utf-8")
    block = re.search(r"^class RiskLimits.*?(?=^class )", src, re.M | re.S)
    assert block, "RiskLimits not found — this ratchet is checking nothing"
    flags = set(re.findall(r"^\s{4}(\w+_enabled)\s*:", block.group(0), re.M))
    assert len(flags) >= 20, f"only {len(flags)} flags parsed — the scan broke"

    missing = sorted(flags - set(GATES))
    assert not missing, (
        "these risk flags are not classified in gate_inventory.GATES, so the "
        "enforcement card would silently omit them:\n  " + "\n  ".join(missing)
        + "\n\nAdd each with its kind (refuse/size/tune/seal/behave) and what "
          "OFF actually means, taken from the flag's own comment.")

    stale = sorted(set(GATES) - flags)
    assert not stale, (
        "these are classified but no longer exist in RiskLimits — delete them "
        "in the same commit that removed the flag:\n  " + "\n  ".join(stale))


def test_the_table_is_well_formed():
    for attr, entry in GATES.items():
        assert len(entry) == 3, f"{attr} must be (label, kind, off_means)"
        label, kind, off_means = entry
        assert label and not label.endswith("."), f"{attr}: label is a name"
        assert kind in VALID_KINDS, f"{attr}: unknown kind {kind!r}"
        assert len(off_means) > 20, (
            f"{attr}: 'what off means' must be a consequence a reader can act "
            f"on, not a restatement of the name")


# ── status: absent is not off ─────────────────────────────────────────────

def test_a_flag_we_cannot_read_is_unknown_not_off():
    # THE ONE THAT MATTERS. Rendering "off" for a failed lookup manufactures a
    # confident negative — and on this card, "off" is an accusation.
    rows = inventory(FakeRisk())          # no attributes at all
    assert rows, "the inventory must still list the controls"
    assert all(r["status"] == UNKNOWN for r in rows)

    s = refusal_summary(rows)
    assert s["off"] == 0, "an unread flag must never be counted as off"
    assert s["armed"] == 0, "nor as armed"
    assert s["unknown"] == s["total"]
    assert s["complete"] is False


def test_none_is_unknown_too():
    rows = inventory(all_on(intent_policy_enabled=None))
    row = next(r for r in rows if r["attr"] == "intent_policy_enabled")
    assert row["status"] == UNKNOWN


def test_no_config_at_all_is_unknown_not_a_clean_bill():
    s = refusal_summary(inventory(None))
    assert s["armed"] == 0 and s["off"] == 0
    assert s["complete"] is False
    assert "⚪" in headline(s) and "unknown" in headline(s).lower()


# ── shadow is not armed ───────────────────────────────────────────────────

def test_shadow_is_counted_separately_from_armed():
    rows = inventory(all_on(validation_gate_mode="shadow"))
    row = next(r for r in rows if r["attr"] == "validation_gate_enabled")
    assert row["status"] == SHADOW

    s = refusal_summary(rows)
    assert s["shadow"] == 1
    assert s["armed"] == s["total"] - 1, "shadow must not count as armed"
    assert "Backtest validation" in s["shadow_labels"]


def test_an_enabled_hook_with_mode_off_is_off():
    rows = inventory(all_on(validation_gate_mode="off"))
    row = next(r for r in rows if r["attr"] == "validation_gate_enabled")
    assert row["status"] == OFF


def test_an_unreadable_mode_on_an_enabled_hook_is_unknown():
    rows = inventory(all_on(validation_gate_mode="banana"))
    row = next(r for r in rows if r["attr"] == "validation_gate_enabled")
    assert row["status"] == UNKNOWN, (
        "an enabled hook whose mode we cannot parse is not enforcing and is "
        "not off — we do not know which")


def test_a_disabled_hook_is_off_whatever_its_mode_says():
    rows = inventory(all_on(validation_gate_enabled=False,
                            validation_gate_mode="enforce"))
    row = next(r for r in rows if r["attr"] == "validation_gate_enabled")
    assert row["status"] == OFF, "the master switch wins over the mode"


# ── only refusal gates count as protection ────────────────────────────────

def test_a_refinement_being_off_is_not_a_missing_gate():
    # var_covariance off means portfolio VaR uses the per-trade proxy; the VaR
    # guard still runs. Counting it as a disarmed gate would raise an alarm
    # about a control that is doing its job.
    rows = inventory(all_on(var_covariance_enabled=False))
    s = refusal_summary(rows)
    assert s["off"] == 0, "a `tune` flag must not appear in the refusal count"
    assert s["armed"] == s["total"]
    assert "🟢" in headline(s)


def test_sizing_off_is_not_counted_as_a_refusal_gate():
    rows = inventory(all_on(kelly_sizing_enabled=False,
                            equity_throttle_enabled=False))
    assert refusal_summary(rows)["off"] == 0


def test_every_refuse_gate_off_is_reported_in_full():
    refuse = [a for a, e in GATES.items() if e[1] == KIND_REFUSE]
    rows = inventory(all_on(**{a: False for a in refuse}))
    s = refusal_summary(rows)
    assert s["off"] == len(refuse) and s["armed"] == 0
    assert len(s["off_labels"]) == len(refuse)


# ── the card ──────────────────────────────────────────────────────────────

def test_the_card_leads_with_what_is_not_protecting_you():
    rows = inventory(all_on(authority_envelope_enabled=False,
                            intent_policy_enabled=False))
    out = render_gate_card(rows, refusal_summary(rows))
    assert "What is not refusing right now" in out
    # The consequence, not just the name.
    assert "granted custody envelope" in out
    # And it appears before the full listing.
    assert out.index("What is not refusing") < out.index("Size only")


def test_the_card_never_paints_an_unknown_posture_green():
    rows = inventory(FakeRisk())
    out = render_gate_card(rows, refusal_summary(rows))
    assert "🟢" not in out, "nothing was read — no green may appear"
    assert "⚪" in out


def test_a_fully_armed_book_reads_green_and_says_so():
    rows = inventory(all_on())
    out = render_gate_card(rows, refusal_summary(rows))
    assert "🟢" in out
    assert "What is not refusing right now" not in out, (
        "with nothing off or shadowed there is no exposure section to show")


def test_an_empty_inventory_says_unknown_not_all_clear():
    out = render_gate_card([], None)
    assert "⚪" in out
    assert "not the same as" in out, (
        "an empty card must distinguish 'could not read' from 'nothing active'")


def test_the_card_names_shadow_as_recording_not_refusing():
    rows = inventory(all_on(validation_gate_mode="shadow"))
    out = render_gate_card(rows, refusal_summary(rows))
    assert "refuses nothing" in out


@pytest.mark.parametrize("kind", sorted(VALID_KINDS))
def test_every_kind_renders_under_a_written_heading(kind):
    """A kind with no heading would render its rows under a raw enum name.

    Asserted POSITIVELY. The first draft asserted the raw kind string was
    absent from the card and failed on the footer's own sentence, "Only the
    first group can refuse a trade" — prose matching a short forbidden string,
    the misfire CLAUDE.md records three times in one sweep. The property is
    "every kind has a human heading", so test that.
    """
    rows = inventory(all_on())
    if not any(r["kind"] == kind for r in rows):
        pytest.skip(f"no {kind} rows in the table")
    from bot.formatters.gate_card import _KIND_HEAD
    head = _KIND_HEAD.get(kind)
    assert head, f"{kind} has no heading — its rows would print a raw enum"
    out = render_gate_card(rows, refusal_summary(rows))
    assert head in out, f"the {kind} heading never reached the card"
