"""#97 fixed the gate and the gate was never reached.

It relaxed `use_table_directly` so an explicit `LLM_TIER_{N}_PROVIDER` would
bind for admins too. The block that actually answers admin calls sits above it
and RETURNS without consulting it:

    if is_admin and routing_override is None:
        if routing[tier]["provider"] == LLMProvider.ANTHROPIC:
            ... return LLMConfig(provider=ANTHROPIC, ...)

Every entry in ADMIN_TIER_ROUTING is Anthropic. So for any admin holding an
Anthropic key the block fired on all four tiers, and every tier pin was ignored
— which is the exact symptom #97 was written to cure, surviving the cure. Set
it, restart, watch the calls still go to the admin table.

WHAT MADE IT VISIBLE WAS BUILDING THE CARD. `/llmtiers` is the surface whose
whole job is to answer "did my override bind?", and it could not: it printed

    Source: tier-routed | <reason from DEFAULT_TIER_ROUTING>

where `tier-routed` meant `resolved != primary`, which is equally true of a
tier the operator pinned and a tier nobody has touched, because the default
table also differs from primary. Both read identically, so an operator who
pinned three tiers and forgot the fourth saw four identical lines. The first
render of the rebuilt card, against a real configuration, reported the pins as
NOT APPLIED — and they were not.

THE ORDERING IS THE FIX, not a second condition. `not _explicit_tier_provider`
on the admin block would bind the pin and lose the fallback: an admin whose pin
names a provider with no key would stop reaching key_health's condemned-key
auto-heal. Moving the env block above it keeps all three cases —
`test_a_pin_that_cannot_resolve_still_reaches_the_admin_key_health_path` is the
one that would fail under the tempting shortcut.

AND THE TICK WAS PAINTED FROM NOTHING. `is_configured()` returns True
unconditionally for RUNECLAW, from the days when keyless meant "on this
machine". `RUNECLAW_LLM_BASE_URL` now names a tunnel that answers 401 without a
key, and the client is built with `api_key or "not-needed"`, so the call fails
at the edge under a green tick. That is `integrity_veto.assess({})` returning
`clear` — fail-open is right for SCORING and wrong for DISPLAY, and they were
one function until a remote endpoint made them differ. `key_state()` is the
seam; `is_configured()` is deliberately untouched.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.formatters.llm_tier_card import TierRow, render_tier_card
from bot.llm.provider import (ADMIN_TIER_ROUTING, LLMConfig, LLMProvider,
                              LLMTier, resolve_tier_config,
                              tier_env_ignored_reason, unbound_tier_env)

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(monkeypatch):
    """No tier pins, a usable Anthropic key, a remote in-house endpoint."""
    for tier in LLMTier:
        for suffix in ("PROVIDER", "KEY", "MODEL"):
            monkeypatch.delenv(f"LLM_TIER_{tier.value.upper()}_{suffix}",
                               raising=False)
    monkeypatch.delenv("LLM_TIER_CHATS_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RUNECLAW_LLM_BASE_URL", "https://x.trycloudflare.com/v1")
    monkeypatch.setenv("RUNECLAW_LLM_API_KEY", "k" * 32)
    return monkeypatch


@pytest.fixture
def primary():
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="sk-ant-test")


# ── the routing bug ─────────────────────────────────────────────────────────

def test_the_admin_table_really_is_anthropic_on_every_tier():
    """The premise. If this ever stops being true the bug below changes shape,
    and a test that silently stops exercising it is worse than no test."""
    assert {ADMIN_TIER_ROUTING[t]["provider"] for t in LLMTier} == \
        {LLMProvider.ANTHROPIC}


def test_an_admin_tier_pin_actually_binds(clean_env, primary):
    """THE REGRESSION. Before the reorder this returned anthropic/admin-table
    for every tier, with the pin set, on every restart."""
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "runeclaw")
    cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    assert cfg.provider == LLMProvider.RUNECLAW, (
        "an admin's LLM_TIER_CHAT_PROVIDER is being ignored again — the admin "
        "premium path is returning before the env override is consulted")
    assert cfg.source == "env"


@pytest.mark.parametrize("tier", list(LLMTier))
def test_every_tier_can_be_pinned_by_an_admin(clean_env, primary, tier):
    """All four, because the defect was uniform across all four and a test of
    one tier would have passed while three stayed broken."""
    clean_env.setenv(f"LLM_TIER_{tier.value.upper()}_PROVIDER", "runeclaw")
    assert resolve_tier_config(tier, primary,
                               is_admin=True).provider == LLMProvider.RUNECLAW


def test_an_admin_with_no_pin_keeps_the_premium_table(clean_env, primary):
    """CONTROL. The fix must not cost admins the Anthropic routing they had."""
    cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    assert cfg.provider == LLMProvider.ANTHROPIC
    assert cfg.source == "admin-table"


def test_a_pin_that_cannot_resolve_still_reaches_the_admin_key_health_path(
        clean_env, primary):
    """THE ONE THAT KILLS THE SHORTCUT.

    Guarding the admin block with `not _explicit_tier_provider` would bind the
    pin and silently drop this case: an admin whose pin names a provider with
    no key would stop reaching key_health's candidate order and lose the
    auto-heal off a 401-condemned key. Ordering keeps it; a second condition
    does not.
    """
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "mistral")
    clean_env.delenv("MISTRAL_API_KEY", raising=False)
    cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    assert cfg.provider == LLMProvider.ANTHROPIC
    assert cfg.source == "admin-table", (
        "a pin that could not resolve no longer falls back through the admin "
        "key-health path — it is resolving Anthropic some other way")


def test_a_non_admin_is_still_never_handed_the_anthropic_key(clean_env, primary):
    """CONTROL, and the one that must never regress: the operator's Claude key
    is admin-only regardless of how routing is configured."""
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "anthropic")
    cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=False)
    assert not cfg.api_key, "a non-admin was handed an Anthropic key"


def test_a_per_user_routing_table_is_untouched(clean_env, primary):
    """CONTROL. An operator env var must not reach past the per-user tier
    table, which is a different mechanism with different owners."""
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "runeclaw")
    table = {LLMTier.CHAT: {"provider": LLMProvider.GROQ, "model": "m"}}
    clean_env.setenv("GROQ_API_KEY", "gsk-test")
    cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=False,
                              routing_override=table)
    assert cfg.provider == LLMProvider.GROQ
    assert cfg.source == "user-tier"


# ── the provenance stamp ────────────────────────────────────────────────────

def test_the_stamp_never_changes_config_equality():
    """LOAD-BEARING. `analyzer.py` decides whether a tier is separately routed
    with `self._scan_config != self._llm_config`, in four places. A provenance
    label that entered __eq__ would silently change which configs the analyzer
    considers distinct."""
    a = LLMConfig(provider=LLMProvider.OPENAI, api_key="k", model="m")
    b = LLMConfig(provider=LLMProvider.OPENAI, api_key="k", model="m",
                  source="env")
    assert a == b


def test_the_analyzer_still_compares_configs_the_same_way():
    """The reason the field is compare=False, pinned where it is relied on."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    assert "!= self._llm_config" in src, (
        "analyzer.py stopped comparing tier configs — the compare=False on "
        "LLMConfig.source was justified by this and the justification is gone")


@pytest.mark.parametrize("kind", ["env", "admin-table", "primary", "user-tier"])
def test_each_branch_stamps_where_it_came_from(clean_env, primary, kind):
    """Driven, not matched: every path a reader can land on names itself."""
    if kind == "env":
        clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "runeclaw")
        cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    elif kind == "admin-table":
        cfg = resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    elif kind == "user-tier":
        clean_env.setenv("GROQ_API_KEY", "gsk-test")
        cfg = resolve_tier_config(
            LLMTier.CHAT, primary, routing_override={
                LLMTier.CHAT: {"provider": LLMProvider.GROQ, "model": "m"}})
    else:
        # Nothing else can win: non-admin (no premium table), and no key for
        # the default table's provider, so the primary is all that is left.
        for env in ("XAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
            clean_env.delenv(env, raising=False)
        openai_primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="sk-o")
        cfg = resolve_tier_config(LLMTier.CHAT, openai_primary)
    assert cfg.source == kind, f"expected {kind}, got {cfg.source!r}"


def test_the_primary_fallback_does_not_relabel_the_callers_config(clean_env):
    """`primary_config` belongs to the caller. Stamping it in place would put a
    tier's provenance on the analyzer's own config object."""
    openai_primary = LLMConfig(provider=LLMProvider.OPENAI, api_key="sk-o")
    resolve_tier_config(LLMTier.CHAT, openai_primary, is_admin=True)
    assert openai_primary.source == "", (
        "resolve_tier_config mutated the config it was handed")


# ── key_state: the display seam ─────────────────────────────────────────────

def _rc(url, key=""):
    return LLMConfig(provider=LLMProvider.RUNECLAW, api_key=key, base_url=url)


def test_a_keyless_remote_endpoint_is_not_a_clean_bill_of_health():
    """THE TICK PAINTED FROM NOTHING. is_configured() says yes because RUNECLAW
    is on the keyless list; nothing checked the endpoint."""
    cfg = _rc("https://x.trycloudflare.com/v1")
    assert cfg.is_configured() is True          # scoring: unchanged, correct
    assert cfg.key_state() == "keyless_remote"  # display: says what it knows


@pytest.mark.parametrize("url", ["http://localhost:11434/v1",
                                 "http://127.0.0.1:8000/v1",
                                 "http://gpu.local:8000/v1"])
def test_a_genuinely_local_endpoint_still_reads_as_fine(url):
    assert _rc(url).key_state() == "keyless_local"


def test_a_key_beats_everything():
    assert _rc("https://x.trycloudflare.com/v1", "k" * 32).key_state() == "key"


def test_an_unreadable_endpoint_is_not_assumed_local():
    """Unknown must fall to the case that needs looking at. Defaulting an
    unreadable host to "local" is how the reassuring answer gets printed from
    no data.

    An EMPTY base_url is deliberately not one of these: it means "use the
    catalog default", which is a real, readable endpoint — not an unknown one.
    """
    assert _rc("not a url at all").key_state() == "keyless_remote"
    assert _rc("https:///v1").key_state() == "keyless_remote"


def test_a_hosted_provider_with_no_key_is_still_plainly_missing():
    assert LLMConfig(provider=LLMProvider.GEMINI,
                     api_key="").key_state() == "missing"


def test_is_configured_still_fails_open_for_scoring():
    """CONTROL. The scoring answer must not be tightened by the display fix —
    a keyless local server genuinely needs no key, and breaking that would stop
    Ollama users' clients being built at all."""
    assert _rc("http://localhost:11434/v1").is_configured() is True
    assert _rc("https://x.trycloudflare.com/v1").is_configured() is True


# ── variables that reach nothing ────────────────────────────────────────────

def test_a_misspelt_tier_variable_is_reported(clean_env):
    clean_env.setenv("LLM_TIER_CHATS_PROVIDER", "runeclaw")
    assert unbound_tier_env() == ["LLM_TIER_CHATS_PROVIDER"]


def test_the_four_real_tiers_are_never_reported(clean_env):
    for tier in LLMTier:
        clean_env.setenv(f"LLM_TIER_{tier.value.upper()}_PROVIDER", "groq")
    assert unbound_tier_env() == []


def test_unrelated_variables_are_left_alone(clean_env):
    clean_env.setenv("LLM_TIER_CHAT_MODEL", "x")
    clean_env.setenv("LLM_PROVIDER", "groq")
    assert unbound_tier_env() == []


# ── why an override did not take ────────────────────────────────────────────

def test_an_unknown_provider_name_is_named(clean_env, primary):
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "runclaw")
    why = tier_env_ignored_reason(LLMTier.CHAT, primary)
    assert "runclaw" in why and "not a provider" in why


def test_a_missing_key_names_the_variable_to_set(clean_env, primary):
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "mistral")
    clean_env.delenv("MISTRAL_API_KEY", raising=False)
    assert "MISTRAL_API_KEY" in tier_env_ignored_reason(LLMTier.CHAT, primary)


def test_nothing_set_explains_nothing(clean_env, primary):
    assert tier_env_ignored_reason(LLMTier.CHAT, primary) == ""


def test_an_unexplained_case_says_so_rather_than_inventing_one(clean_env, primary):
    """A diagnostic that always produces a cause is a diagnostic that will one
    day produce a wrong one."""
    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "groq")
    clean_env.setenv("GROQ_API_KEY", "gsk-test")
    assert tier_env_ignored_reason(LLMTier.CHAT, primary) == "reason unknown"


# ── the card ────────────────────────────────────────────────────────────────

def _row(**kw):
    base = dict(tier="chat", provider="groq", model="m", source="default-table",
                key_state="key")
    base.update(kw)
    # Derived, not defaulted — a helper that hardcodes CHAT's variable makes
    # every other tier's row quietly wrong.
    base.setdefault("env_var", f"LLM_TIER_{base['tier'].upper()}_PROVIDER")
    return TierRow(**base)


def test_an_unpinned_tier_says_it_is_unpinned():
    """THE GAP THIS WAS BUILT FOR. Three tiers pinned and the fourth on a
    default was indistinguishable from four pinned tiers."""
    out = render_tier_card([_row(tier="learning", env_value="")])
    assert "Not pinned" in out
    assert "LLM_TIER_LEARNING_PROVIDER" in out


def test_an_ignored_override_leads_the_card():
    """THE RED HERRING. Everything about this tier is healthy — a real
    provider, a real model, a key present — and the operator's instruction was
    dropped. The true-but-misleading signal is the green key."""
    out = render_tier_card([_row(source="admin-table", key_state="key",
                                 env_value="runeclaw",
                                 ignored_reason="no key for runeclaw")])
    assert "NOT" in out and "applied" in out
    assert "override not applied" in out, (
        "the headline still reads healthy — the reader has to find the ignored "
        "instruction in the body")
    assert out.index("NOT") < out.index("Key:"), (
        "the dropped instruction is reported below the key state, which is the "
        "part that looks fine")


def test_a_pinned_and_applied_tier_reads_clean():
    out = render_tier_card([_row(source="env", env_value="runeclaw",
                                 key_state="key")])
    assert "applied" in out
    assert "NOT" not in out
    assert "Not pinned" not in out


def test_a_table_rationale_is_not_printed_beside_a_route_it_did_not_choose():
    """The old card printed DEFAULT_TIER_ROUTING's reason unconditionally, so a
    tier pinned to the in-house model was justified with Groq's rationale — an
    argument for a route that is not in force, beside a value that is."""
    out = render_tier_card([_row(source="env", env_value="runeclaw",
                                 provider="runeclaw",
                                 table_reason="Groq is the fastest")])
    assert "Groq is the fastest" not in out


def test_a_table_rationale_is_printed_when_the_table_did_choose():
    """CONTROL — suppressing it everywhere would be the opposite error."""
    out = render_tier_card([_row(source="default-table",
                                 table_reason="Groq is the fastest")])
    assert "Groq is the fastest" in out


def test_a_keyless_remote_tier_is_not_given_a_green_tick():
    out = render_tier_card([_row(key_state="keyless_remote", env_value="",
                                 provider="runeclaw")])
    assert "⚠️" in out
    assert "NOT local" in out


def test_an_unknown_credential_state_reads_as_unknown_not_as_ok():
    out = render_tier_card([_row(key_state="", env_value="")])
    assert "❔" in out
    assert "✅" not in out, "a tier with no credential information got a tick"


def test_an_unknown_source_is_not_dressed_up_as_a_route():
    out = render_tier_card([_row(source="", env_value="")])
    assert "unknown" in out.lower()


def test_an_empty_card_does_not_read_as_an_empty_configuration():
    """Absent is never a measurement. A card that renders nothing when the
    routing could not be read says "no tiers configured" to a reader."""
    out = render_tier_card([])
    assert "could not be read" in out
    assert "not the same as there being none" in out


def test_unbound_variables_are_surfaced_on_the_card():
    out = render_tier_card([_row(env_value="")],
                           unbound_env=["LLM_TIER_CHATS_PROVIDER"])
    assert "LLM_TIER_CHATS_PROVIDER" in out
    assert "reach nothing" in out


def test_the_card_escapes_what_a_provider_name_could_carry():
    out = render_tier_card([_row(provider="<script>x</script>", env_value="")])
    assert "<script>" not in out


# ── wiring ──────────────────────────────────────────────────────────────────

def test_the_command_renders_through_the_card(clean_env, primary):
    """Every card test above calls the renderer directly and none of them prove
    the command uses it. #999 shipped a card that rendered zero times."""
    from bot.skills.telegram_handler import TelegramHandler

    clean_env.setenv("LLM_TIER_CHAT_PROVIDER", "runeclaw")
    h = TelegramHandler.__new__(TelegramHandler)
    out = h._llm_tier_card(primary, "en")
    assert "CHAT" in out and "LEARNING" in out
    assert "Not pinned" in out, "the unpinned LEARNING tier is not called out"
    assert "applied" in out


def test_the_command_resolves_the_route_the_reader_actually_gets(clean_env, primary):
    """/llmtiers is admin-gated, so an admin is its only possible reader, and
    it resolved with is_admin=False — presenting a non-admin's route as the
    routing. /llmstatus already passed is_admin=True for its fingerprints, so
    the two operator cards answered the same question differently."""
    from bot.skills.telegram_handler import TelegramHandler

    h = TelegramHandler.__new__(TelegramHandler)
    out = h._llm_tier_card(primary, "en")
    assert "anthropic" in out, (
        "the card is showing the non-admin route again — an admin reader is "
        "being told their scans run somewhere they do not")


def test_both_operator_cards_resolve_for_the_same_audience():
    """ASK WHICH OTHER SURFACE MAKES THE SAME CLAIM. Two commands report tier
    routing to the same admin; they must not disagree about whose route it is."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "skills" / "telegram_handler.py")
                    .read_text(encoding="utf-8"))
    # Sliced to the NEXT definition, not to a fixed character count — a count
    # that stops short of the call passes for the wrong reason.
    # End markers must be CODE: code_only() strips comments, so a comment
    # banner as a delimiter is not there to find.
    for marker, end in (("_cmd_llmstatus", "def _cmd_llmreset"),
                        ("_llm_tier_card", "def _cmd_dashboard")):
        i = src.index(f"def {marker}")
        body = src[i:src.index(end, i)]
        assert "resolve_tier_config" in body, f"{marker} no longer resolves"
        assert "is_admin=True" in body, (
            f"{marker} resolves tier routing without is_admin=True, so it "
            "reports a route its only possible reader never takes")


def test_the_title_is_not_double_escaped(clean_env, primary):
    """The i18n catalog ships the title with its own <b> wrapper; escaping it
    printed the tags at the reader."""
    from bot.skills.telegram_handler import TelegramHandler

    h = TelegramHandler.__new__(TelegramHandler)
    assert "&lt;b&gt;" not in h._llm_tier_card(primary, "en")
