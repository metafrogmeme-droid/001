"""Three patched files on the live bot box, folded back into the tree.

Carrying a 3,349-line override of `proactive_monitor.py` to change one
integer is a file that will drift, and it did: it fell three methods behind
main and crashed on startup calling `set_anomaly_prefs_fn`, a method the
override itself had deleted. `build` read `-dirty` the whole time.

The headline defect it was carrying a fix for had been WRONG 227 CONSECUTIVE
TIMES. `want not in served` compared `v14-real-14b` against Ollama's
`v14-real-14b:latest`, which always differ as strings and never differ in
practice — a permanent, unfalsifiable `model_missing` beside an LLM status
card reading "Brain: healthy — LLM answering" over 24 served calls. Two
cards, one endpoint, opposite claims, and the wrong one is the one that
pages.
"""
import os
import subprocess
import sys

import pytest

from bot.core.proactive_monitor import ProactiveMonitor
from bot.llm.provider import PROVIDER_CATALOG, LLMProvider, provider_key_env

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheModelNameComparison:
    @pytest.mark.parametrize("want,served", [
        ("v14-real-14b", "v14-real-14b:latest"),      # the 227-alarm case
        ("v14-real-14b:latest", "v14-real-14b"),      # and reversed
        ("v14-real-14b", "v14-real-14b"),
        ("pbdes2022/humanoid-traders", "pbdes2022/humanoid-traders:latest"),
        ("pbdes2022/humanoid-traders:v13-14b",
         "pbdes2022/humanoid-traders:v13-14b"),
    ])
    def test_a_bare_name_and_its_latest_tag_are_the_same_model(self, want, served):
        assert ProactiveMonitor._same_model(want, served)

    @pytest.mark.parametrize("want,served", [
        ("v14-real-14b", "v14-real-7b:latest"),       # different model
        ("v14-real-14b", "v14-real-14b:v2"),          # different TAG
        ("", "v14-real-14b:latest"),                  # nothing asked for
        ("v14-real-14b", ""),
    ])
    def test_it_does_not_over_match(self, want, served):
        # The fix must not trade a false alarm for a silent one: a genuinely
        # absent model still has to page.
        assert not ProactiveMonitor._same_model(want, served)

    def test_the_repo_path_is_not_mistaken_for_a_tag(self):
        # The colon that matters is in the FINAL path segment. A registry
        # path with no tag still gains `:latest`, and one with a tag keeps it.
        assert ProactiveMonitor._same_model("a/b", "a/b:latest")
        assert not ProactiveMonitor._same_model("a/b:v1", "a/b:v2")


class TestTheOfferedListSaysWhenItIsCut:
    def _card(self, probe):
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m._llm_probe = probe
        m._llm_alerted_state = None
        return m._check_llm_endpoint()

    @staticmethod
    def _probe(served, total=None):
        p = {"state": "model_missing", "model": "v14-real-14b",
             "provider": "ollama", "tier": "chat", "host": "gw.example",
             "status": 200, "consecutive_failures": 2, "served": served}
        if total is not None:
            p["served_total"] = total
        return p

    def test_a_truncated_list_names_both_numbers(self):
        alerts = self._card(self._probe([f"m{i}" for i in range(6)], total=13))
        body = " ".join(getattr(a, "message", "") or str(a) for a in alerts)
        assert "first 6 of 13" in body, (
            "six of thirteen tags read as the endpoint's whole store and "
            "sent a morning chasing a registry that did not exist")

    def test_a_complete_list_makes_no_such_claim(self):
        alerts = self._card(self._probe(["m1", "m2"], total=2))
        body = " ".join(getattr(a, "message", "") or str(a) for a in alerts)
        assert "first" not in body

    def test_an_absent_total_does_not_invent_one(self):
        # Older probe payloads carry no served_total; the card must fall back
        # to the length it has rather than claim a cut it cannot see.
        alerts = self._card(self._probe(["m1", "m2"]))
        body = " ".join(getattr(a, "message", "") or str(a) for a in alerts)
        assert "first" not in body


class TestOllamaCanCarryAKeyAndAnAddress:
    def test_the_key_is_read_WITHOUT_joining_the_vault_contract(self, monkeypatch):
        """The first draft put OLLAMA in `_PROVIDER_KEY_ENV` and broke
        `test_a_keyless_provider_stores_nothing`. That map is not a key
        lookup — it is the `/setllm` -> vault contract, and a row there means
        `/vault` audits a slot the command promises to fill. `/setllm ollama`
        takes no key by design. The key is an .env read instead.
        """
        from bot.llm.provider import _PROVIDER_KEY_ENV, optional_provider_key
        assert LLMProvider.OLLAMA not in _PROVIDER_KEY_ENV
        # ...and not as a SECOND map either: four copies of the
        # provider-to-key table once existed carrying 11, 8, 6 and 7 rows,
        # and the short ones failed silently for the providers they forgot.
        # `test_there_is_exactly_one_provider_to_key_map` forbids a second.
        assert provider_key_env("ollama") == ""

        monkeypatch.setenv("OLLAMA_API_KEY", "sk-proxy")
        assert optional_provider_key(LLMProvider.OLLAMA) == "sk-proxy"
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        assert optional_provider_key(LLMProvider.OLLAMA) == ""

    def test_a_provider_with_no_optional_key_reads_nothing(self):
        from bot.llm.provider import optional_provider_key
        assert optional_provider_key(LLMProvider.OPENAI) == ""

    def test_the_client_attaches_it_instead_of_the_placeholder(self, monkeypatch):
        # `api_key or "not-needed"` sent the placeholder to an endpoint behind
        # an auth proxy, and every call 401'd while it was healthy.
        import inspect

        from bot.llm.provider import create_llm_client
        src = inspect.getsource(create_llm_client)
        assert "optional_provider_key(config.provider)" in src
        assert 'config.api_key or "not-needed"' not in src

    @staticmethod
    def _base_url(env_value):
        env = dict(os.environ)
        env.pop("OLLAMA_BASE_URL", None)
        if env_value is not None:
            env["OLLAMA_BASE_URL"] = env_value
        out = subprocess.run(
            [sys.executable, "-c",
             "from bot.llm.provider import PROVIDER_CATALOG, LLMProvider;"
             " print(PROVIDER_CATALOG[LLMProvider.OLLAMA]['base_url'])"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        return out.stdout.strip().splitlines()[-1]

    def test_unset_keeps_localhost(self):
        assert self._base_url(None) == "http://localhost:11434/v1"

    def test_set_points_elsewhere(self):
        # "Self-hosted" stopped meaning "on this machine". The workaround was
        # to point RUNECLAW_LLM_BASE_URL at Ollama and let it masquerade.
        assert self._base_url("https://gw.example/v1") == "https://gw.example/v1"

    def test_the_catalog_entry_is_still_openai_shaped(self):
        assert PROVIDER_CATALOG[LLMProvider.OLLAMA]["sdk"] == "openai"


class TestTheSevereCapIsTunableWithoutPatchingTheFile:
    @staticmethod
    def _cap(env_value):
        env = dict(os.environ)
        env.pop("SEVERE_CARDS_PER_HOUR", None)
        if env_value is not None:
            env["SEVERE_CARDS_PER_HOUR"] = env_value
        out = subprocess.run(
            [sys.executable, "-c",
             "from bot.core.proactive_monitor import ProactiveMonitor as P;"
             " print(P._SEVERE_CARDS_PER_HOUR)"],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        return int(out.stdout.strip().splitlines()[-1])

    def test_unset_keeps_the_documented_default(self):
        assert self._cap(None) == 6

    def test_set_is_read(self):
        # The whole reason a 3,349-line file was being carried on the box.
        assert self._cap("8") == 8


class TestPreScopeBehaviourNeedsNoCodeChange:
    """The override's remaining delta was a REVERT of the anomaly scope
    feature. It did not need to be: the feature already exposes dials that
    reproduce pre-scope behaviour exactly, which is why reverting it cost a
    startup crash for nothing."""

    def test_scope_all_is_a_no_op(self):
        from bot.core.anomaly_scope import SCOPE_ALL, scoped

        class _A:
            def __init__(self, s): self.symbol = s
        items = [_A("BTC/USDT"), _A("ETH/USDT")]
        kept, dropped, note = scoped(items, held=set(), scope=SCOPE_ALL)
        assert kept == items and dropped == [] and note == ""

    def test_a_zero_interval_is_always_due(self):
        from bot.core.anomaly_scope import is_due
        assert is_due(last_sent=1000.0, now=1000.0, interval=0)

    def test_an_unwired_monitor_still_has_working_defaults(self):
        # `set_anomaly_prefs_fn`'s docstring: "Unset is not an error." The
        # override deleted the method anyway, and the caller crashed.
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        dials = m._anomaly_dials()
        assert set(dials) == {"scope", "interval"}
        assert dials["interval"] > 0

    def test_the_injection_point_still_exists(self):
        assert callable(getattr(ProactiveMonitor, "set_anomaly_prefs_fn", None))
