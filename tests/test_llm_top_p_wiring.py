"""LLM_TOP_P reaches the model, and an unset LLM_TOP_P changes nothing.

The knob was set in `.env` and read by nothing — a dead env var, which is
worse than an absent one because it looks configured. This pins both halves.

The DEFAULT is the load-bearing part. `None` omits the parameter, so an
install that has never heard of LLM_TOP_P sends byte-identical requests. A
numeric default would have changed sampling on every deployment at once, on
the component that writes trade theses.

NO `importlib.reload` IN HERE, and that is not a style preference. The first
draft reloaded `bot.config` and `bot.llm.provider` to re-evaluate their
module-level defaults. It worked, and it broke **39 tests in six other
files** — every module holding a reference to the old CONFIG object or the
old classes was silently talking to a replaced module. The suite reported
them as "flaky/order-dependent", which is exactly how a self-inflicted wound
disguises itself as weather. Env-dependent module-level state gets a
SUBPROCESS; everything else is read at call time and needs no reload at all.
"""
import io
import json
import os
import subprocess
import sys
import types

import pytest

from bot.config import _env_float_opt
from bot.llm.provider import LLMConfig, _env_top_p, sampling_kwargs
from tests.source_scan import code_only

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_top_p(value):
    """CONFIG.llm.top_p as a FRESH interpreter would compute it.

    A subprocess because the field's default is evaluated at import; reloading
    the module in-process is what wrecked the suite.
    """
    env = dict(os.environ)
    if value is None:
        env.pop("LLM_TOP_P", None)
    else:
        env["LLM_TOP_P"] = value
    out = subprocess.run(
        [sys.executable, "-c",
         "from bot.config import CONFIG; import json;"
         " print(json.dumps({'top_p': CONFIG.llm.top_p,"
         " 'max_tokens': CONFIG.llm.max_tokens}))"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestTheEnvReaders:
    """`_env_float_opt` and `_env_top_p` are the same rule in two modules.

    Two copies on purpose: provider.py is imported BY config.py, so building
    an LLMConfig must not depend on bot.config being importable.
    """

    @pytest.mark.parametrize("read", [_env_float_opt, _env_top_p])
    def test_unset_is_none_not_one(self, read, monkeypatch):
        monkeypatch.delenv("LLM_TOP_P", raising=False)
        assert (read("LLM_TOP_P") if read is _env_float_opt else read()) is None

    @pytest.mark.parametrize("read", [_env_float_opt, _env_top_p])
    def test_a_deliberate_zero_survives(self, read, monkeypatch):
        # 0.0 is a MEANING here (greedy, top token only). `_env_float(key,
        # default)` cannot tell it from unset; that is why these exist.
        monkeypatch.setenv("LLM_TOP_P", "0")
        got = read("LLM_TOP_P") if read is _env_float_opt else read()
        assert got == 0.0 and got is not None

    @pytest.mark.parametrize("read", [_env_float_opt, _env_top_p])
    @pytest.mark.parametrize("junk", ["nine tenths", "", "   ", "0.9.1"])
    def test_junk_reads_as_unset_rather_than_crashing_at_import(
            self, read, junk, monkeypatch):
        monkeypatch.setenv("LLM_TOP_P", junk)
        assert (read("LLM_TOP_P") if read is _env_float_opt else read()) is None


class TestSamplingKwargs:
    """One function decides what sampling goes on the wire."""

    @staticmethod
    def _with_top_p(monkeypatch, top_p, temperature=0.3):
        # sampling_kwargs does a CALL-TIME `from bot.config import CONFIG`,
        # so replacing the module attribute is enough — no reload, no
        # mutation of a frozen dataclass, nothing left behind.
        stub = types.SimpleNamespace(
            llm=types.SimpleNamespace(temperature=temperature, top_p=top_p))
        monkeypatch.setattr("bot.config.CONFIG", stub)

    def test_unset_sends_no_top_p_at_all(self, monkeypatch):
        self._with_top_p(monkeypatch, None)
        assert "top_p" not in sampling_kwargs("gpt-4o")

    def test_set_sends_it(self, monkeypatch):
        self._with_top_p(monkeypatch, 0.9)
        assert sampling_kwargs("gpt-4o")["top_p"] == 0.9

    def test_zero_is_sent_not_swallowed(self, monkeypatch):
        self._with_top_p(monkeypatch, 0.0)
        assert sampling_kwargs("gpt-4o")["top_p"] == 0.0

    def test_claude_five_still_gets_no_temperature(self, monkeypatch):
        # The 2026-07-16 incident: an explicit temperature 400s on that family
        # and took the brain down to the rule engine. Adding a second sampling
        # parameter must not reintroduce the first.
        self._with_top_p(monkeypatch, 0.9)
        kw = sampling_kwargs("claude-opus-5")
        assert "temperature" not in kw
        assert kw["top_p"] == 0.9

    def test_an_ordinary_model_still_gets_its_temperature(self, monkeypatch):
        self._with_top_p(monkeypatch, None, temperature=0.42)
        assert sampling_kwargs("gpt-4o") == {"temperature": 0.42}

    @pytest.mark.parametrize("model", ["gpt-4o", "claude-opus-5",
                                       "llama-3.1-70b", ""])
    def test_unset_top_p_is_byte_identical_to_before(self, model, monkeypatch):
        """The safety property: no LLM_TOP_P, no new parameter, any model."""
        self._with_top_p(monkeypatch, None)
        assert "top_p" not in sampling_kwargs(model)


class TestLLMConfigCarriesIt:
    """The field's default_factory reads the env at INSTANTIATION."""

    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("LLM_TOP_P", raising=False)
        assert LLMConfig().top_p is None

    def test_set_is_read(self, monkeypatch):
        monkeypatch.setenv("LLM_TOP_P", "0.9")
        assert LLMConfig().top_p == 0.9

    def test_an_explicit_argument_still_wins(self):
        assert LLMConfig(top_p=0.5).top_p == 0.5


class TestConfigLLM:
    """CONFIG.llm's default is evaluated at import — so, a subprocess."""

    def test_unset_is_none(self):
        assert _config_top_p(None)["top_p"] is None

    def test_set_is_read(self):
        assert _config_top_p("0.85")["top_p"] == 0.85

    def test_max_tokens_survived_the_edit(self):
        # It did not, the first time: the field was dropped by the very
        # replacement that added top_p, and only the mypy ratchet noticed.
        assert _config_top_p(None)["max_tokens"] > 0


class TestTheAnalyzerWiring:
    def _code(self):
        return code_only(io.open("bot/core/analyzer.py", encoding="utf-8").read())

    def test_no_api_call_hardcodes_its_own_temperature_any_more(self):
        # Four sites each remembering the Claude-5 rule is four chances to
        # forget it. ONE use survives on purpose and the count pins it:
        # `_resolve_llm_config` populating an LLMConfig field, which is
        # storing the setting rather than choosing what to put on the wire.
        code = self._code()
        assert code.count("temperature=CONFIG.llm.temperature") == 1
        i = code.index("temperature=CONFIG.llm.temperature")
        assert "LLMConfig(" in code[max(0, i - 700):i]

    def test_adaptive_thinking_drops_top_p_too(self):
        # Extended thinking requires DEFAULT sampling. Dropping only the
        # parameter we already knew about would have turned LLM_TOP_P into a
        # 400 on every thesis-tier Opus call.
        code = self._code()
        i = code.index('create_kwargs["thinking"] = {"type": "adaptive"}')
        window = code[i:i + 1400]
        assert 'create_kwargs.pop("temperature", None)' in window
        assert 'create_kwargs.pop("top_p", None)' in window

    def test_the_reject_and_retry_net_covers_top_p(self):
        assert '("temperature", "top_p")' in self._code()
