"""The nightly audit must not report a null it did not measure.

The 2026-08-31 card is the specification. Both proposals rendered:

    ⬜ measured +3.14% (+0.00pp vs baseline) · PF 1.87 · 39tr

identical to the baseline and to each other, under rationales promising an
improvement. Three separate defects produced that, and none of them is "the
change didn't help":

  1. SYMBOL_LOSS_STREAK_THRESHOLD=3 was NOT A CHANGE. config.py:431 already
     defaults to 3. The no-op filter existed but was gated on
     `env.get(flag) is not None`, so a flag sitting at its code default had
     nothing to compare against and skipped the check. Absent from the
     environment is not absent from the config.

  2. LIVE_PERF_REDUCE_WINRATE 0.40 -> 0.35 only binds when win rate is in
     (0.35, 0.40] AND the window is net-positive. A benchmark running at
     PF 1.87 never enters that band, so the run returned the baseline
     unchanged. Rendering that as a measured neutral is an absence dressed as
     a measurement — and the live book it was proposed for sat at 25% win
     rate and net-negative, where the knob binds on nearly every call.

  3. "LIQUIDITY: LIQUIDITY: spread" — a doubled prefix, cut at exactly 28
     characters with no marker, so it read as a finished phrase.

The fourth, found while fixing the third and worse than all of them, is in
`tests/test_shadow_gate_buckets.py`.
"""

from __future__ import annotations

import pytest

from bot.core.self_audit import (
    ALLOWED_FLAGS,
    FLAG_CONFIG_PATHS,
    SelfAudit,
    effective_value,
    validate_proposals,
)

# ── 1. a proposal that is not a change ────────────────────────────────────

def test_a_value_equal_to_the_config_default_is_dropped():
    """The card's own case: the default is 3 and the audit proposed 3."""
    out = validate_proposals(
        [{"flag": "SYMBOL_LOSS_STREAK_THRESHOLD", "value": 3,
          "rationale": "tightens reentry discipline"}],
        current_env={k: None for k in ALLOWED_FLAGS})
    assert out == [], "a no-op proposal reached the benchmark"


def test_a_real_change_still_gets_through():
    """The failure mode of every filter: filtering everything."""
    out = validate_proposals(
        [{"flag": "SYMBOL_LOSS_STREAK_THRESHOLD", "value": 5,
          "rationale": "streak too loose"}],
        current_env={k: None for k in ALLOWED_FLAGS})
    assert [p["flag"] for p in out] == ["SYMBOL_LOSS_STREAK_THRESHOLD"]
    assert out[0]["value"] == "5"


def test_an_explicit_env_setting_still_wins_over_the_default():
    """env is the value in force when it is set; config only fills the gap."""
    env = {k: None for k in ALLOWED_FLAGS}
    env["SYMBOL_LOSS_STREAK_THRESHOLD"] = "5"
    assert validate_proposals(
        [{"flag": "SYMBOL_LOSS_STREAK_THRESHOLD", "value": 5, "rationale": "x"}],
        current_env=env) == []
    # ...and 3 is now a real change, because the env moved it off the default.
    assert len(validate_proposals(
        [{"flag": "SYMBOL_LOSS_STREAK_THRESHOLD", "value": 3, "rationale": "x"}],
        current_env=env)) == 1


def test_a_bool_flag_at_its_default_is_also_a_no_op():
    """The comparison has to work across types, not just floats."""
    assert validate_proposals(
        [{"flag": "STRUCTURE_TRAIL_ENABLED", "value": False, "rationale": "x"}],
        current_env={k: None for k in ALLOWED_FLAGS}) == []


@pytest.mark.parametrize("flag", sorted(ALLOWED_FLAGS))
def test_every_allowlisted_flag_resolves_to_a_live_value(flag):
    """The table cannot fall behind the allow-list.

    A flag with no path, or a path that no longer resolves, silently stops
    being no-op-checked — the same hole one level up. Parametrised so the
    failure names the flag. This caught FOUR wrong paths on its first run
    (entry_timing, candle_entry_veto, and both order-flow knobs, which are not
    on CONFIG at all).
    """
    assert flag in FLAG_CONFIG_PATHS, f"{flag} has no config path"
    assert effective_value(flag, {}) is not None, (
        f"{flag} -> {FLAG_CONFIG_PATHS[flag]} did not resolve")


# ── 2. a run that did not discriminate ────────────────────────────────────

_BASE = {"return_pct": 3.14, "pf": 1.87, "trades": 39}


def _render(measured, baseline=None):
    return SelfAudit().render_report(
        {"summary": {"n": 40, "win_rate": 0.25, "pf": 0.35, "net_pnl": -45.88}},
        [{"flag": "LIVE_PERF_REDUCE_WINRATE", "value": "0.35",
          "rationale": "performance is poor", "measured": measured}],
        baseline if baseline is not None else dict(_BASE),
        "alts_1h")


def test_an_identical_run_is_not_reported_as_a_measured_neutral():
    out = _render(dict(_BASE))
    assert "NOT DISTINGUISHED" in out
    assert "+0.00pp" not in out, "the baseline's own delta printed as a verdict"
    assert "not evidence the change is neutral" in out


def test_a_real_difference_is_still_reported_as_measured():
    out = _render({"return_pct": 4.20, "pf": 2.10, "trades": 41})
    assert "measured +4.20%" in out
    assert "+1.06pp" in out
    assert "NOT DISTINGUISHED" not in out


def test_a_same_return_but_different_trade_count_is_a_real_difference():
    """+0.00pp alone cannot tell a null from a genuine wash."""
    out = _render({"return_pct": 3.14, "pf": 1.87, "trades": 44})
    assert "NOT DISTINGUISHED" not in out
    assert "44tr" in out


def test_a_failed_benchmark_stays_NOT_VERIFIED_and_is_not_called_identical():
    """Unreadable is not identical. An empty result must not match anything."""
    out = _render({})
    assert "NOT VERIFIED" in out
    assert "NOT DISTINGUISHED" not in out


def test_a_partially_read_run_is_not_called_identical():
    """The case the empty-result test above never reaches.

    `_render({})` short-circuits at NOT VERIFIED because `return_pct` is None,
    so it never consults `_identical_run` at all — a mutation making a missing
    figure count as a match passed the whole file. This drives a run whose
    return MATCHES the baseline but whose PF was not read: two of three
    figures agreeing is not agreement, and calling it "NOT DISTINGUISHED"
    would assert a match from a number nobody has.
    """
    out = _render({"return_pct": 3.14, "trades": 39})     # no pf
    assert "NOT DISTINGUISHED" not in out
    assert "PF ?" in out, "the unread PF must show as unknown, not as a value"


def test_the_baseline_line_carries_its_trade_count():
    """Without it a reader cannot see a candidate repeating the baseline."""
    assert "39tr" in _render({"return_pct": 4.2, "pf": 2.1, "trades": 41})


# ── 3. the gate label ─────────────────────────────────────────────────────

def _render_gate(key):
    return SelfAudit().render_report(
        {"summary": {"n": 40}, "shadow_gates": {key: {"net_r": 10.4, "n": 4}}},
        [], {}, "alts_1h")


def test_a_truncated_gate_name_says_it_was_truncated():
    assert "…" in _render_gate("X" * 60), "the name was cut with nothing to show it"


def test_a_short_gate_name_is_not_decorated():
    out = _render_gate("CONFIDENCE")
    assert "CONFIDENCE" in out
    assert "…" not in out
