"""The in-house-model card diagnosed perfectly and named no remedy.

Live, 2026-09-10:

    ⚠️ IN-HOUSE MODEL UNREACHABLE
    - Host: llm.humanoid-traders.com
    - Routed tier: scan
    - Failed checks: 2

    The endpoint answers and the key works, but the model v14-real-14b is not
    served there. It offers: control-clean-14b:latest, deepseek-v3.1:671b-cloud,
    llama3.2:latest, pbdes2022/humanoid-traders:latest, ...:v10-8b, ...:v11-8b.
    Every call to this tier will 404 while the endpoint looks healthy.

Everything about that is right, and it is a card an operator opens BECAUSE
something is wrong. Its two sibling branches both hand over a next step — the
unreachable one points at `scripts/cloudflared/README.md`, the forbidden one
names both causes of a refused key — and this one, the one that actually
fired, stopped at the diagnosis. The setting is `LLM_TIER_SCAN_MODEL` and the
card never said so.

`/vault`'s fix-hint work is the precedent, and so is its trap: the hint was
picked by GUESSING from a key's name rather than reading what any command
does. So the env var is built by ONE function that the probe and the card both
call — a second hand-written copy is how `_PROVIDER_KEY_ENV` ended up written
three times, one of them wrong for exactly one provider.
"""

import os

import pytest

from bot.core.proactive_monitor import tier_model_env


class TestTheEnvVarHasOneDefinition:
    @pytest.mark.parametrize("tier,expected", [
        ("scan", "LLM_TIER_SCAN_MODEL"),
        ("SCAN", "LLM_TIER_SCAN_MODEL"),
        ("deep", "LLM_TIER_DEEP_MODEL"),
        ("chat", "LLM_TIER_CHAT_MODEL"),
    ])
    def test_it_builds_the_name_the_probe_reads(self, tier, expected):
        assert tier_model_env(tier) == expected

    @pytest.mark.parametrize("tier", ["", None])
    def test_an_absent_tier_does_not_raise(self, tier):
        """The probe runs on a timer; a card must not be what crashes it."""
        assert tier_model_env(tier) == "LLM_TIER__MODEL"

    def test_the_probe_reads_what_this_returns(self):
        """One definition, or the card names a variable nothing consults.

        THIS ONE IS A SCAN, and saying so matters in a file about claims that
        outrun their evidence — an earlier draft of this docstring said
        "driven rather than grepped" about these two greps. The probe builds
        its lookup inside a 40-line coroutine that needs aiohttp and a live
        endpoint to reach, so wiring is the one property available here; the
        BEHAVIOUR of the name is driven by the parametrised cases above and by
        the end-to-end case at the bottom of this file.
        """
        import inspect

        from bot.core import proactive_monitor as pm
        src = inspect.getsource(pm.ProactiveMonitor)
        assert "tier_model_env(tier)" in src, (
            "the probe went back to building the env var itself; the card and "
            "the probe can now disagree about which setting is wrong")
        # And the literal it replaced must be gone, or both spellings survive.
        assert 'f"LLM_TIER_{tier.upper()}_MODEL"' not in src


class TestTheCardHandsOverANextStep:
    """Driven through the real alert builder with a planted probe."""

    def _card(self, monkeypatch, served, model="v14-real-14b", tier="scan"):
        from bot.core.proactive_monitor import ProactiveMonitor
        mon = ProactiveMonitor.__new__(ProactiveMonitor)
        mon._llm_probe = {
            "state": "model_missing", "status": 200, "tier": tier,
            "model": model, "host": "llm.humanoid-traders.com",
            "served": served, "consecutive_failures": 2,
        }
        mon._llm_alerted_state = None
        mon.LLM_PROBE_ALERT_AT = 2
        alerts = mon._check_llm_endpoint()
        assert alerts, "the model_missing state produced no alert at all"
        return alerts[0].body

    SERVED = ["control-clean-14b:latest", "llama3.2:latest",
              "pbdes2022/humanoid-traders:v11-8b"]

    def test_it_names_the_setting_to_change(self, monkeypatch):
        body = self._card(monkeypatch, self.SERVED)
        assert "LLM_TIER_SCAN_MODEL" in body, (
            "the card still says what is wrong without saying what to change")

    def test_the_setting_matches_the_tier_that_broke(self, monkeypatch):
        body = self._card(monkeypatch, self.SERVED, tier="deep")
        assert "LLM_TIER_DEEP_MODEL" in body
        assert "LLM_TIER_SCAN_MODEL" not in body

    def test_it_still_lists_what_is_actually_served(self, monkeypatch):
        """The remedy is only actionable beside the menu."""
        body = self._card(monkeypatch, self.SERVED)
        for m in self.SERVED:
            assert m in body

    def test_it_still_names_the_model_that_is_missing(self, monkeypatch):
        assert "v14-real-14b" in self._card(monkeypatch, self.SERVED)

    def test_it_offers_the_other_remedy_too(self, monkeypatch):
        """Only the operator knows whether the model was meant to be there."""
        body = self._card(monkeypatch, self.SERVED)
        assert "serve that model on the host" in body

    def test_it_still_says_decisions_are_unaffected(self, monkeypatch):
        """The most important sentence on the card, and the easiest to lose.

        Without it the operator reads an LLM fault as a trading fault and goes
        looking in the wrong subsystem — the 37-timed-out-ticks lesson.
        """
        body = self._card(monkeypatch, self.SERVED)
        assert "Decisions are UNAFFECTED" in body

    def test_but_it_does_not_claim_the_fault_is_free(self, monkeypatch):
        """It said "Trading is UNAFFECTED", full stop, and that was too broad.

        A 404 is not an auth error, so the analyzer's handler condemns nothing
        (only `looks_like_auth_error` marks a key) and every call to this tier
        pays the failed round trip before the fallback one. The operator who
        reads "unaffected" and closes the card is the one whose scans are
        timing out on a per-symbol dead hop.
        """
        body = self._card(monkeypatch, self.SERVED)
        assert "failed round trip" in body
        assert "candidate" in body

    def test_and_it_does_not_claim_to_be_the_cause_either(self, monkeypatch):
        """A heuristic is never a verdict — including a plausible one.

        Nothing here measured that this hop is why a scan was slow. Naming it
        as the cause would be the same over-broad claim in the other
        direction, on the card an operator uses to decide where to look.
        """
        body = self._card(monkeypatch, self.SERVED)
        assert "not a verdict" in body
        for overclaim in ("is why scans", "causes the", "the cause of"):
            assert overclaim not in body

    def test_an_empty_served_list_does_not_invent_a_menu(self, monkeypatch):
        """The venue answered with nothing; that is a reading, not an absence
        of one, and the card already had the right words for it."""
        body = self._card(monkeypatch, [])
        assert "none listed" in body
        # The remedy still applies — repointing is exactly what you cannot do
        # here, so the host half has to survive.
        assert "serve that model on the host" in body


class TestTheSiblingBranchesKeepTheirHints:
    """This change must not be the reason another branch loses its next step."""

    def _card(self, state, **extra):
        from bot.core.proactive_monitor import ProactiveMonitor
        mon = ProactiveMonitor.__new__(ProactiveMonitor)
        mon._llm_probe = {"state": state, "tier": "scan", "model": "m",
                          "host": "h", "consecutive_failures": 2, **extra}
        mon._llm_alerted_state = None
        mon.LLM_PROBE_ALERT_AT = 2
        alerts = mon._check_llm_endpoint()
        return alerts[0].body if alerts else ""

    def test_unreachable_still_points_at_the_tunnel_doc(self):
        assert "cloudflared" in self._card("unreachable")

    def test_forbidden_still_names_both_causes(self):
        body = self._card("forbidden", status=403)
        assert "key is wrong" in body and "access policy" in body


def test_the_env_var_is_the_one_the_runtime_would_read(monkeypatch):
    """End to end on the NAME: plant a value under it and read it back the way
    the probe does, so a rename of either side fails here."""
    monkeypatch.setenv(tier_model_env("scan"), "pbdes2022/humanoid-traders:v11-8b")
    assert os.environ.get("LLM_TIER_SCAN_MODEL") == "pbdes2022/humanoid-traders:v11-8b"
