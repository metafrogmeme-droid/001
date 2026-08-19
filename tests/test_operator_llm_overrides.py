"""Two operator switches, carried as hand-applied patches on the box.

Both were real and both worked. What neither survived was being applied at the
place the author reached first, and this file pins the level each belongs at.

LLM_TIER1_TO_LLM — force tier-1 (rule-only) signals through the LLM.

    The patch bumped `tier` immediately after `classify_tier` in `analyze()`.
    But `analyzer._thesis_cache_salt` INDEPENDENTLY recomputes `classify_tier`
    to salt the LLM cache key, and its own docstring says the salt exists so
    "a tier-1 rule result" cannot leak across contexts. A promotion visible to
    one caller and not the other caches an LLM thesis under the `t1` salt — so
    with the flag off again, or in another process, a rule-based lookup is
    served that LLM answer, and a promoted call is served a stale rule result.
    Same signal, same key, two different kinds of answer.

    The promotion is part of what the tier IS, so it lives with the tier.

LLM_TIER_{N}_PROVIDER for admins — route a tier through a named provider even
for admin users.

    The patch inserted a new resolver above the admin branch: a provider map of
    five entries, a keyless rule, a catalog lookup. All of that already existed
    forty lines below, with EIGHT providers, the same keyless rule, and a
    primary-key fallback the copy lacked — gated only on `not
    use_table_directly`.

    So the change is one line at the gate, and admins fall into the existing
    resolver. A second, partial copy of key resolution that has to be kept in
    step with the first is a worse bug than the one being fixed, and it would
    have quietly failed for Alibaba, Grok and Anthropic.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from bot.core.token_optimizer import TieredPipeline

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── LLM_TIER1_TO_LLM ────────────────────────────────────────────────────────

class _Sig:
    symbol = "BTC/USDT"


def _tier_one(monkeypatch):
    """Pin classify_tier at 1 so the promotion is what is under test, not the
    classifier's own judgement."""
    monkeypatch.setattr(TieredPipeline, "classify_tier",
                        staticmethod(lambda indicators, signal: 1))


def test_off_by_default(monkeypatch):
    _tier_one(monkeypatch)
    monkeypatch.delenv("LLM_TIER1_TO_LLM", raising=False)
    assert TieredPipeline.effective_tier({}, _Sig()) == 1


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", " true "])
def test_the_switch_promotes_tier_one_to_the_llm(monkeypatch, val):
    _tier_one(monkeypatch)
    monkeypatch.setenv("LLM_TIER1_TO_LLM", val)
    assert TieredPipeline.effective_tier({}, _Sig()) == 2


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "maybe"])
def test_anything_that_is_not_an_opt_in_leaves_the_tier_alone(monkeypatch, val):
    _tier_one(monkeypatch)
    monkeypatch.setenv("LLM_TIER1_TO_LLM", val)
    assert TieredPipeline.effective_tier({}, _Sig()) == 1


def test_it_only_ever_promotes_tier_one(monkeypatch):
    """A tier-3 signal must not be demoted to 2 by a switch about tier 1."""
    monkeypatch.setenv("LLM_TIER1_TO_LLM", "true")
    for n in (2, 3):
        monkeypatch.setattr(TieredPipeline, "classify_tier",
                            staticmethod(lambda i, s, _n=n: _n))
        assert TieredPipeline.effective_tier({}, _Sig()) == n


def test_the_cache_salt_and_the_pipeline_read_the_same_tier():
    """THE DEFECT THE PATCH WOULD HAVE SHIPPED.

    Two call sites in analyzer.py ask what tier a signal is: the LLM cache salt
    and the analysis itself. Promoting in one and not the other collides an LLM
    thesis and a rule result in a single cache slot. Checked from the callers,
    which is the only place the disagreement is visible.
    """
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    assert "TieredPipeline.classify_tier(" not in src, (
        "analyzer.py reads classify_tier directly again — one of its two "
        "readers now sees a tier the other does not, and they share a cache key")
    assert src.count("TieredPipeline.effective_tier(") == 2, (
        "both the cache salt and the analysis must read the promoted tier")


def test_the_promotion_lives_with_the_tier_not_at_a_call_site():
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "token_optimizer.py").read_text(encoding="utf-8"))
    assert "LLM_TIER1_TO_LLM" in src
    assert isinstance(inspect.getattr_static(TieredPipeline, "effective_tier"),
                      staticmethod)


# ── LLM_TIER_{N}_PROVIDER for admins ────────────────────────────────────────

def test_an_explicit_tier_provider_opens_the_env_path_for_admins():
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "llm" / "provider.py").read_text(encoding="utf-8"))
    assert "_explicit_tier_provider" in src, (
        "the admin path no longer consults LLM_TIER_{N}_PROVIDER, so setting "
        "it looks broken: set, restart, calls still go to the admin table")
    assert "is_admin and not _explicit_tier_provider" in src, (
        "the gate no longer lets an explicitly-named provider past the admin "
        "table")


def test_there_is_exactly_one_provider_to_key_map():
    """THE PATCH WAS TREATING A SYMPTOM OF THIS, and this test found it.

    Refusing to add a fifth copy of the provider-to-key-env map turned up four
    that were already there — a canonical `_PROVIDER_KEY_ENV` with eleven
    providers, and three inline re-declarations carrying 8, 6 and 7:

        tier env override   8/11  missing MISTRAL, OPENROUTER, TOGETHER
        default tier key    6/11  also missing GROK, RUNECLAW
        client construction 7/11  missing GROK, MISTRAL, OPENROUTER, TOGETHER

    So `LLM_TIER_2_PROVIDER=mistral` with `MISTRAL_API_KEY` set resolved
    through the canonical map and through NONE of the paths that actually run
    — the override found no key, fell through in silence, and the operator saw
    a variable that does not work. Which is the symptom the hand-applied patch
    was written to route around.

    Duplicated key resolution does not fail loudly. It fails for whichever
    providers the copy forgot, and only for those.
    """
    from tests.source_scan import code_only
    import re

    src = code_only((ROOT / "bot" / "llm" / "provider.py").read_text(encoding="utf-8"))
    maps = re.findall(r'\{[^{}]*LLMProvider\.\w+:\s*"[A-Z_]+"[^{}]*\}', src, re.S)
    assert len(maps) == 1, (
        f"{len(maps)} provider-to-key-env maps in provider.py — there is one "
        "canonical `_PROVIDER_KEY_ENV`, and every copy silently drops whatever "
        "providers it forgot")
    assert "_PROVIDER_KEY_ENV" in src
    # and every resolution reads it
    assert src.count("_PROVIDER_KEY_ENV.get(") >= 3, (
        "a key resolution stopped using the canonical map")


def test_the_untouched_paths_stay_untouched():
    """CONTROLS. Per-user tier routing is a different mechanism and an
    operator env var must not reach past it; and with nothing set, admins keep
    the premium table they had."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "llm" / "provider.py").read_text(encoding="utf-8"))
    assert "routing_override is not None\n" in src or \
           "routing_override is not None" in src, "the user-tier table gate is gone"
    i = src.index("use_table_directly = ")
    gate = src[i:i + 220]
    assert "routing_override is not None" in gate and "or (is_admin" in gate, (
        "an env override is now overriding the per-user tier table too, which "
        "is a different mechanism with different owners")


def test_anthropic_is_still_never_taken_from_the_env():
    """It was excluded before because that branch was non-admin only. That
    reason is gone — admins reach it now — so the guard needs its real one:
    the admin branch resolves Anthropic through key_health's candidate order,
    which skips keys a 401 has condemned. A bare env var steps around that."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "llm" / "provider.py").read_text(encoding="utf-8"))
    assert "tier_provider != LLMProvider.ANTHROPIC" in src, (
        "an env override can now pin Anthropic directly, bypassing the "
        "key_health resolution that auto-heals off a condemned key")
