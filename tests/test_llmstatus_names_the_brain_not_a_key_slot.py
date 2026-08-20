"""`/llmstatus` reported a healthy keyless tier as a missing Anthropic key.

`tier_report` exists because the surfaces that report routing had drifted; its
own docstring says so and names the two:

    "`/llmtiers` resolved with is_admin=False while `/llmstatus` resolved with
     True, and the web dashboard's panel did not resolve at all ... Three
     renderings of one fact, three different answers, none of them labelled."

`/llmtiers` and the web panel were moved onto the collector. `/llmstatus` was
not — it kept a hand-rolled loop, so the surface the docstring names is the one
still drifting. What it printed:

    <b>Anthropic key slots</b>
    🟢 .env: sk-ant...abc12345 [valid]
    — engine uses → scan: NOT SET | thesis: NOT SET

Three defects in those two lines, all in the same direction — a working engine
described as a broken one:

1. THE HEADING CLAIMS ANTHROPIC. "engine uses →" closes a block listing
   Anthropic key candidates, so it reads as "of these slots, the engine picked
   this one". Once `LLM_TIER_*_PROVIDER` began binding, SCAN and THESIS resolve
   to the self-hosted model and the line is not about Anthropic at all.

2. "NOT SET" IS A FINGERPRINT'S IDEA OF KEYLESS. `key_fingerprint()` answers
   "NOT SET" whenever `api_key` is empty, which for a keyless provider is the
   correct configuration. So a healthy tier printed NOT SET immediately beneath
   a VALID key — the reading a person takes from that is "the key is not being
   picked up". `key_state()` was written to distinguish these four cases and
   this surface never called it.

3. TWO TIERS OF FOUR, unlabelled. LEARNING and CHAT were omitted, and they are
   the ones most likely to differ: pin SCAN/THESIS/LEARNING to the in-house
   model, leave CHAT on a hosted default, and CHAT is the one that can silently
   have no key.

THE RED HERRING, planted below: a tier that IS on Anthropic with a valid key.
It must still read as healthy — a fix that muted every tier would be the mirror
defect, and `keyless_remote` must stay a warning rather than becoming a pass.
"""

from __future__ import annotations

import re

import pytest

from bot.formatters.llm_tier_card import TierRow, render_engine_uses


def text(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def row(tier="scan", **over):
    base = dict(tier=tier, provider="runeclaw", model="v10-8b",
                source="env", key_state="keyless_remote",
                env_var=f"LLM_TIER_{tier.upper()}_PROVIDER", env_value="runeclaw")
    base.update(over)
    return TierRow(**base)


ANTHROPIC_OK = row("learning", provider="anthropic", model="claude-sonnet-5",
                   source="admin-table", key_state="key",
                   env_var="", env_value="")


# ── the defect ──────────────────────────────────────────────────────────────

class TestAKeylessTierIsNotAMissingKey:
    def test_it_does_not_say_not_set(self):
        out = text(render_engine_uses([row()]))
        assert "NOT SET" not in out, (
            "a correctly-configured keyless tier still reads as a missing key")

    def test_it_says_what_is_actually_known(self):
        out = text(render_engine_uses([row()]))
        assert "no key" in out and "NOT local" in out, out

    def test_a_local_keyless_tier_reads_as_fine(self):
        out = text(render_engine_uses([row(key_state="keyless_local")]))
        assert "endpoint is on this machine" in out
        assert "❌" not in out

    def test_a_genuinely_missing_key_still_reads_as_missing(self):
        out = render_engine_uses([row("chat", provider="groq",
                                      key_state="missing")])
        assert "❌" in out
        assert "no API key found" in text(out)


class TestItNamesTheModelNotAFingerprint:
    def test_the_provider_and_model_are_shown(self):
        out = text(render_engine_uses([row()]))
        assert "runeclaw/v10-8b" in out

    def test_a_fingerprint_never_appears(self):
        # The whole point: this line is about WHICH BRAIN answers, not which
        # Anthropic key slot it came from.
        out = text(render_engine_uses([row(), ANTHROPIC_OK]))
        assert not re.search(r"\.\.\.[0-9a-f]{8}", out), out

    def test_an_unknown_model_says_default_rather_than_blank(self):
        out = text(render_engine_uses([row(model="")]))
        assert "runeclaw/default" in out


class TestEveryTierAppears:
    def test_all_four_are_rendered(self):
        rows = [row("scan"), row("thesis"), ANTHROPIC_OK,
                row("chat", provider="groq", key_state="missing")]
        out = text(render_engine_uses(rows))
        for tier in ("SCAN", "THESIS", "LEARNING", "CHAT"):
            assert tier in out, f"{tier} is missing from the block"

    def test_the_routing_source_is_named(self):
        out = text(render_engine_uses([row(), ANTHROPIC_OK]))
        assert "pinned by env" in out
        assert "admin premium table" in out


# ── the red herring ─────────────────────────────────────────────────────────

class TestAHealthyTierStillReadsHealthy:
    def test_an_anthropic_tier_with_a_key_is_a_tick(self):
        out = render_engine_uses([ANTHROPIC_OK])
        assert "✅" in out
        assert "key set" in text(out)

    def test_keyless_remote_is_a_warning_not_a_pass(self):
        # It is an OBSERVATION, not a verdict — the endpoint may well be open.
        # But it must not wear the same tick as a checked credential.
        assert "⚠️" in render_engine_uses([row()])
        assert "✅" not in render_engine_uses([row()])

    def test_an_ignored_override_outranks_a_healthy_key(self):
        """The operator's instruction being dropped is the more surprising
        fact than a key being fine."""
        out = render_engine_uses([row("chat", provider="anthropic",
                                      key_state="key", source="admin-table",
                                      env_var="LLM_TIER_CHAT_PROVIDER",
                                      env_value="runeclaw")])
        assert "⚠️" in out
        assert "override not applied" in text(out)


# ── absence ─────────────────────────────────────────────────────────────────

class TestAFailedReadSaysSo:
    def test_no_rows_is_not_an_empty_block(self):
        out = text(render_engine_uses([]))
        assert "could not be read" in out
        assert "not an absence of routing" in out

    def test_an_unchecked_credential_is_not_a_pass(self):
        out = render_engine_uses([row(key_state="")])
        assert "credential not checked" in text(out)
        assert "✅" not in out


# ── it is wired, and it uses the one collector ──────────────────────────────

def test_llmstatus_routes_through_the_shared_collector():
    """`tier_report` was written because these surfaces drift. A private loop
    here is how the third rendering gets a different answer again."""
    from tests.source_scan import code_only
    src = code_only(open("bot/skills/telegram_handler.py", encoding="utf-8").read())
    assert "render_engine_uses(" in src, "the block is not built"
    assert "{engine_block}" in src, (
        "the block is built and never rendered — present is not reached")
    i = src.index("Anthropic key slots")
    window = src[max(0, i - 2000):i + 400]
    assert "key_fingerprint()" not in window, (
        "the hand-rolled per-tier fingerprint loop is back under the "
        "Anthropic heading")


def test_the_slots_block_no_longer_claims_the_engine_uses_one_of_them():
    from tests.source_scan import code_only
    src = code_only(open("bot/skills/telegram_handler.py", encoding="utf-8").read())
    assert "— engine uses → " not in src, (
        "a non-Anthropic resolution is being printed under an Anthropic heading")


@pytest.mark.parametrize("state", ["key", "keyless_local", "keyless_remote",
                                   "missing", ""])
def test_every_key_state_renders_within_the_shared_vocabulary(state):
    """The formatter imports KEY_STATE_TEXT rather than restating it, with a
    comment saying a private copy is how the next surface gets a tick back.
    This pins that none of the five states falls through to a blank."""
    out = text(render_engine_uses([row(key_state=state)]))
    assert "·" in out, out
    # No field is stated twice. The first draft printed the credential state
    # from `_KEY_STATE` AND `_icon_and_note`'s note, which for an unchecked
    # state are the same fact in two phrasings:
    #     "credential not checked · pinned by env · credential unknown"
    assert out.count("credential") <= 1, f"the credential is stated twice: {out}"
    # Every state resolves to a phrase, never to a blank segment.
    line = out.splitlines()[1]
    assert not any(seg.strip() == "" for seg in line.split("·")), line
