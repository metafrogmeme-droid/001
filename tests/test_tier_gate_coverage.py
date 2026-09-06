"""Every dispatch of a gated skill is actually behind the gate.

WHY THIS IS STRUCTURAL

This is the third time in this audit that a correct guard existed and call
sites simply never received it:

  1. ``assertDevnet``            — right where it ran, missing from 5 of 9
                                    presale commands (token/presale).
  2. ``assertKeyfilePermissions`` — right where it ran, missing from the
                                    keypair loader that mattered most.
  3. ``_token_gate_blocks``      — right where it ran, missing from 8 of 12
                                    dispatch sites, including the entire
                                    natural-language path and the web
                                    dashboard.

Three instances is not a coincidence, it is the shape of the codebase: a guard
that must be remembered at each call site eventually is not. Tests that exercise
a guarded path can only ever confirm the paths somebody remembered to guard, so
this one reads the source and asks the other question — *is it reached?*

WHAT (3) ACTUALLY MEANT

``/scalp`` was gated. Typing "scalp" was not: the intent branch dispatched the
same skill under different words. ``/deepscan``, ``/backtest``, ``/optimize``,
``/patterns``, ``/analyze``, ``/learn`` and ``/walkforward`` were ungated
outright, and the web dashboard reached ``learning`` without passing the gate at
all. The paywall was, in practice, a spelling test.
"""

import ast
import pathlib

import pytest

import bot.token.tier_gate as tg
from tests.source_scan import segment_reader

HANDLER = pathlib.Path(__file__).resolve().parent.parent / "bot" / "skills" / "telegram_handler.py"

# Skill name as dispatched -> feature key as gated. They differ in exactly one
# place (`pro_scan` is sold as `premium_scan`), and that mapping is the sort of
# thing that silently rots, so it is asserted rather than assumed.
SKILL_TO_FEATURE = {"pro_scan": "premium_scan"}

GATE_CALLS = ("_token_gate_blocks", "_pane_gate_blocks")


def _functions_dispatching_gated_skills():
    """Every file the handler class is made of, not HANDLER alone: the
    handler is being split into mixins, and a gated dispatch that moved into
    one would otherwise drop out of this check without a word."""
    from tests.source_scan import handler_sources

    mapping = {**{k: k for k in tg.FEATURE_MIN_TIER}, **SKILL_TO_FEATURE}
    out = []
    for path in handler_sources():
        src = path.read_text()
        # `ast.get_source_segment` re-splits the WHOLE source on every call, so
        # this loop was quadratic: 260 function nodes x 13,575 lines = 29.6s,
        # and under full-suite load it crossed the 60s pytest-timeout and was
        # filed by the CI gate as "passes alone (flaky/order-dependent)".
        # `segment_reader` splits once and is byte-identical; 0.004s here.
        seg_of = segment_reader(src)
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seg = seg_of(node) or ""
            hit = sorted({mapping[n] for n in mapping if f'dispatch("{n}"' in seg})
            if hit:
                out.append((node.name, hit, seg))
    return out


def test_every_gated_skill_dispatch_is_behind_the_gate():
    rows = _functions_dispatching_gated_skills()
    assert len(rows) >= 10, (
        f"only found {len(rows)} dispatch sites — the handler moved or the "
        "dispatch spelling changed, and this test is no longer looking at anything"
    )
    ungated = [
        f"{name} (dispatches {', '.join(feats)})"
        for name, feats, seg in rows
        if not any(call in seg for call in GATE_CALLS)
    ]
    assert not ungated, (
        "these dispatch a gated skill without checking the tier gate:\n  "
        + "\n  ".join(ungated)
        + "\n\nA paywall that depends on which command — or which client — the user "
        "happens to use is not a paywall."
    )


def test_the_pro_scan_alias_still_resolves():
    """`pro_scan` is dispatched but sold as `premium_scan`; if that mapping
    breaks, the test above silently stops checking the scan commands."""
    assert "premium_scan" in tg.FEATURE_MIN_TIER
    src = HANDLER.read_text()
    assert 'dispatch("pro_scan"' in src, "pro_scan is no longer dispatched — update SKILL_TO_FEATURE"


def test_both_tiers_actually_buy_something():
    """`elite` used to unlock nothing that `pro` did not.

    The ladder had a single entry, so staking 100,000 tokens bought exactly what
    10,000 did. A tier nobody can tell apart from the one below it is not a tier.
    """
    tiers = set(tg.FEATURE_MIN_TIER.values())
    assert "pro" in tiers and "elite" in tiers, f"ladder is flat: {tg.FEATURE_MIN_TIER}"


def test_safety_commands_can_never_be_gated():
    """The product-safety line, enforced rather than documented.

    A user whose stake lapsed, or whose RPC is down, must still be able to see
    their exposure and hit the kill switch.
    """
    assert not (set(tg.FEATURE_MIN_TIER) & tg.NEVER_GATED)
    for command in ("halt", "check_risk", "get_portfolio", "whynot"):
        assert command in tg.NEVER_GATED, f"{command} is no longer protected"


def test_gating_a_safety_command_is_refused_not_warned(monkeypatch):
    with pytest.raises(tg.GateMisconfigured, match="refusing to gate"):
        tg._assert_never_gated({"halt": "elite"})


def test_operator_override_parses_and_is_still_safety_checked(monkeypatch):
    monkeypatch.setenv("RCLAW_TIER_FEATURES", "deepscan:elite,patterns:basic")
    table = tg._load_feature_min_tier()
    assert table["deepscan"] == "elite", "override did not raise a feature's tier"
    assert "patterns" not in table, "':basic' must ungate, not set a bogus tier"

    # The override must not be a way around the safety line.
    monkeypatch.setenv("RCLAW_TIER_FEATURES", "halt:elite")
    with pytest.raises(tg.GateMisconfigured, match="refusing to gate"):
        tg._load_feature_min_tier()


def test_a_typo_in_the_override_is_refused_not_ignored(monkeypatch):
    """Silently dropping a malformed entry ungates a feature nobody notices."""
    for bad in ("deepscan:platinum", "deepscan", ":pro"):
        monkeypatch.setenv("RCLAW_TIER_FEATURES", bad)
        with pytest.raises(tg.GateMisconfigured):
            tg._load_feature_min_tier()
