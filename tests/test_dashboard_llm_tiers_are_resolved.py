"""/api/state served a module constant and called it engine state.

    from bot.llm.provider import DEFAULT_TIER_ROUTING, LLMTier
    for tier in LLMTier:
        route = DEFAULT_TIER_ROUTING.get(tier, {})
        tiers[tier.value] = {"provider": ..., "model": ..., "reason": ...}

`resolve_tier_config` was never called, so the panel could not reflect an env
pin, a runtime /settier override, the admin premium table, or whether a key
exists. It showed the same four rows on every install of RUNECLAW that has
ever run, inside a payload named `state`, refreshing every three seconds.

THIS IS THE THIRD RENDERING OF ONE FACT and all three disagreed: `/llmtiers`
resolved as a non-admin, `/llmstatus` resolved as an admin, and this one did
not resolve. Asking which OTHER surface makes the same claim is the practice
that found it — the sweep was run because `/llmtiers` had just been rebuilt,
not because anything pointed here.

So the collection moved into `provider.tier_report()` and both surfaces read
it. `test_the_payload_and_the_card_cannot_drift` is the one that keeps them
together; the rest would all still pass with two copies quietly diverging.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.llm.provider import LLMConfig, LLMProvider, LLMTier, tier_report

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(monkeypatch):
    for tier in LLMTier:
        monkeypatch.delenv(f"LLM_TIER_{tier.value.upper()}_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RUNECLAW_LLM_BASE_URL", "https://x.trycloudflare.com/v1")
    monkeypatch.setenv("RUNECLAW_LLM_API_KEY", "k" * 32)
    return monkeypatch


@pytest.fixture
def primary():
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="sk-ant-test")


# ── the report is resolved, not recited ─────────────────────────────────────

def test_the_report_reflects_an_env_pin(clean_env, primary):
    """The property the constant could never have. Before this, pinning a tier
    changed nothing on the dashboard, ever."""
    clean_env.setenv("LLM_TIER_SCAN_PROVIDER", "runeclaw")
    row = {r["tier"]: r for r in tier_report(primary)}["scan"]
    assert row["provider"] == "runeclaw"
    assert row["source"] == "env"


def test_the_report_covers_every_tier(clean_env, primary):
    assert {r["tier"] for r in tier_report(primary)} == {t.value for t in LLMTier}


def test_the_report_carries_what_the_panel_needs_to_be_honest(clean_env, primary):
    for row in tier_report(primary):
        assert set(row) >= {"tier", "provider", "model", "source", "key_state",
                            "env_var", "env_value", "ignored_reason",
                            "table_reason"}


def test_the_report_never_carries_a_credential(clean_env, primary):
    """A routing report is a message to a person and travels over HTTP. The
    repo rule is that keys never reach user-facing text; `key_state` is a state
    word for exactly this reason."""
    blob = repr(tier_report(primary))
    assert "sk-ant-test" not in blob
    assert "k" * 32 not in blob
    assert "api_key" not in blob


def test_a_dropped_pin_is_visible_in_the_report(clean_env, primary):
    clean_env.setenv("LLM_TIER_SCAN_PROVIDER", "mistral")
    clean_env.delenv("MISTRAL_API_KEY", raising=False)
    row = {r["tier"]: r for r in tier_report(primary)}["scan"]
    assert row["env_value"] == "mistral"
    assert row["source"] != "env"
    assert "MISTRAL_API_KEY" in row["ignored_reason"]


def test_a_rationale_is_only_carried_for_the_table_that_won(clean_env, primary):
    clean_env.setenv("LLM_TIER_SCAN_PROVIDER", "runeclaw")
    row = {r["tier"]: r for r in tier_report(primary)}["scan"]
    assert row["table_reason"] == "", (
        "a routing table's justification is riding along with a route it did "
        "not choose")


# ── the payload ─────────────────────────────────────────────────────────────

def test_the_payload_resolves_instead_of_reciting_the_constant():
    """The guard. Walking DEFAULT_TIER_ROUTING here is the defect itself."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "web" / "dashboard_server.py")
                    .read_text(encoding="utf-8"))
    i = src.index('data["llm_tiers"]')
    block = src[max(0, i - 1400):i + 200]
    assert "tier_report(" in block, (
        "/api/state no longer resolves the routing — it is reporting a table")
    assert "DEFAULT_TIER_ROUTING" not in block, (
        "/api/state is walking the default routing constant again, which "
        "cannot reflect an env pin, a /settier override or a missing key")
    assert "engine_analysis_as_admin" in block, (
        "the panel no longer resolves for the audience that spends the money "
        "shown beside it — the engine's autonomous analysis. A hardcoded "
        "is_admin is right only while that setting sits at its default")


def test_an_unreadable_source_is_not_serialised_as_an_empty_one():
    """`{}` is a readable answer meaning "no tiers", and it is truthy in JS —
    the panel painted zeros off it, or skipped the update and left the previous
    poll on screen."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "web" / "dashboard_server.py")
                    .read_text(encoding="utf-8"))
    for key in ('data["llm_tiers"] = {}', 'data["cost"] = {}'):
        assert key not in src, (
            f'{key} — a failed read is being serialised as an empty result')
    assert 'data["llm_tiers"] = None' in src
    assert 'data["cost"] = None' in src


def test_the_payload_and_the_card_cannot_drift():
    """THE ONE THAT MATTERS. Three renderings of one fact disagreed; the fix is
    that there is now one collector, and this fails the moment a second
    appears."""
    from tests.source_scan import code_only

    for rel in ("bot/web/dashboard_server.py", "bot/skills/telegram_handler.py"):
        src = code_only((ROOT / rel).read_text(encoding="utf-8"))
        assert "tier_report(" in src, f"{rel} stopped using the shared collector"

    handler = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                        .read_text(encoding="utf-8"))
    i = handler.index("def _llm_tier_card")
    body = handler[i:handler.index("def _cmd_dashboard", i)]
    assert "resolve_tier_config" not in body, (
        "the Telegram card resolves tiers itself again — a second collector "
        "that has to be kept in step with tier_report is how these three "
        "surfaces disagreed in the first place")


# ── the third surface, found by asking who else makes this claim ────────────

class TestLlmStatusAsksTheDisplayFunctionToo:
    """`/llmstatus` was the last surface still asking `is_configured()`.

    A keyless RUNECLAW config against a REMOTE tunnel printed

        Key: `NOT SET`
        Cost: zero
        Free tier: True

    with NO warning line, because `is_configured()` is True for RUNECLAW
    unconditionally. The catalog lines actively compound it: they read as
    confirmation that NOT SET is normal for this provider. It is not — the
    client is built with `api_key or "not-needed"` and every call 401s.

    /llmtiers was cured of this in the same commit that created `key_state()`,
    and this one was missed. `/portfolio` still had the defect
    `/open_positions` had just been cured of.
    """

    def _status(self, provider, key="", base_url=""):
        from bot.llm.provider import BYOK
        return BYOK.status(LLMConfig(provider=provider, api_key=key,
                                     base_url=base_url))

    def test_a_keyless_remote_endpoint_is_no_longer_silent(self):
        out = self._status(LLMProvider.RUNECLAW,
                           base_url="https://x.trycloudflare.com/v1")
        assert "NOT local" in out, (
            "a keyless config against a remote tunnel still reports nothing — "
            "beside 'Cost: zero' and 'Free tier: True', which read as "
            "confirmation that NOT SET is expected here")

    def test_it_does_not_claim_a_rule_based_fallback_that_is_not_happening(self):
        """THE TRAP IN THE OBVIOUS FIX. Flipping the condition to
        `key_state() != "key"` would print "using rule-based fallback", and for
        a keyless provider the client IS built and calls ARE attempted. That
        replaces a silent false claim with a loud one."""
        out = self._status(LLMProvider.RUNECLAW,
                           base_url="https://x.trycloudflare.com/v1")
        assert "rule-based fallback" not in out
        assert "attempted anyway" in out

    def test_a_genuinely_local_keyless_endpoint_stays_quiet(self):
        """CONTROL. Ollama on this machine needs no key and no warning; a
        warning there would train the reader to ignore the one that matters."""
        out = self._status(LLMProvider.OLLAMA,
                           base_url="http://localhost:11434/v1")
        assert "⚠️" not in out

    def test_a_hosted_provider_with_no_key_still_says_rule_based_fallback(self):
        """CONTROL. That sentence is TRUE for `missing` — create_llm_client
        returns None and the analyzer really does degrade to the rule engine."""
        assert "rule-based fallback" in self._status(LLMProvider.GEMINI)

    def test_a_configured_provider_gets_no_warning_at_all(self):
        out = self._status(LLMProvider.GEMINI, key="AIza-test")
        assert "⚠️" not in out and "❌" not in out

    def test_the_status_line_never_prints_the_key(self):
        out = self._status(LLMProvider.GEMINI, key="AIza-supersecret-value")
        assert "supersecret" not in out

    def test_all_three_surfaces_share_one_key_state_vocabulary(self):
        """The drift guard. Three surfaces report this state; two had already
        diverged. A private copy of the phrasing is how the next one silently
        gets a tick back."""
        from tests.source_scan import code_only

        card = code_only((ROOT / "bot" / "formatters" / "llm_tier_card.py")
                         .read_text(encoding="utf-8"))
        assert "KEY_STATE_TEXT" in card, (
            "the card declares its own key-state wording again")
        assert '"keyless_remote": (' not in card, (
            "a second copy of the key-state vocabulary is back in the card")
