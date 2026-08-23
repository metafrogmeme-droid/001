"""The background-sweep LLM valve: what it throttles, and what it must not.

A full autonomous sweep runs SCAN_ANALYSIS_CONCURRENCY (12) analyses in
flight, each wanting an LLM thesis. Against one serving GPU that is more
volume than the provider absorbs: requests queue, each blows
ANALYSIS_TIMEOUT_SEC (90s), the tick's TICK_PHASE_TIMEOUT_SEC (300s) analyze
phase cancels the rest, and the all-providers-exhausted path raises
``_llm_degraded_streak`` and flaps the brain OFFLINE/online.

``LLM_BACKGROUND_SCANS=off`` sends that sweep to the rule engine.

TWO THINGS CAN GO WRONG AND BOTH ARE SILENT, so both are pinned here.

1. THE VALVE CANNOT BE TURNED ON. The operator writes ``off``; ``_env_bool``'s
   false-vocabulary is ("", "false", "0", "no") and does not contain it, so
   the flag would read True, log "unrecognised", and the sweep would keep
   hammering the GPU while the operator believed it was throttled. This repo
   has shipped that shape before — a multi-venue flag on the wrong dataclass
   that could never be enabled.

2. THE VALVE THROTTLES TOO MUCH. The first draft keyed on ``user_id is None``.
   Four USER-INVOKED paths reach the analyzer with no user_id:

       bot/skills/scan_skill.py:_scan_single        the Telegram /scan handler
       bot/skills/skill_registry.py:_run_symbol_scan
       bot/skills/skill_registry.py  (two skill execute() bodies)

   Every one of them would have been downgraded to the rule engine, while the
   valve's own comment promised it kept user-invoked analyses on the LLM, and
   nothing in the reply would have said otherwise. The user asks for a scan,
   gets a thesis, and cannot tell it came from somewhere else.
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import _env_switch


# ── 1. the flag can actually be turned on ────────────────────────────────

class TestTheSwitchCanBeThrown:
    @pytest.mark.parametrize("raw", ["off", "OFF", " Off ", "false", "0", "no",
                                     "disabled"])
    def test_off_spellings_are_false(self, monkeypatch, raw):
        monkeypatch.setenv("RUNECLAW_TEST_SWITCH", raw)
        assert _env_switch("RUNECLAW_TEST_SWITCH", True) is False, (
            f"{raw!r} did not turn the switch off — an operator who writes it "
            f"and restarts gets no error and no change")

    @pytest.mark.parametrize("raw", ["on", "ON", "true", "1", "yes", "enabled"])
    def test_on_spellings_are_true(self, monkeypatch, raw):
        monkeypatch.setenv("RUNECLAW_TEST_SWITCH", raw)
        assert _env_switch("RUNECLAW_TEST_SWITCH", False) is True

    def test_off_is_the_spelling_env_bool_gets_wrong(self, monkeypatch):
        # The reason _env_switch exists, pinned as behaviour rather than prose.
        # If _env_bool ever learns "off", this test fails and the helper can be
        # retired — until then, using _env_bool here would be a silent no-op.
        from bot.config import _env_bool
        monkeypatch.setenv("RUNECLAW_TEST_SWITCH", "off")
        assert _env_bool("RUNECLAW_TEST_SWITCH", True) is True, (
            "_env_bool now understands 'off' — _env_switch may be redundant")
        assert _env_switch("RUNECLAW_TEST_SWITCH", True) is False

    def test_unset_and_empty_keep_the_default(self, monkeypatch):
        monkeypatch.delenv("RUNECLAW_TEST_SWITCH", raising=False)
        assert _env_switch("RUNECLAW_TEST_SWITCH", True) is True
        assert _env_switch("RUNECLAW_TEST_SWITCH", False) is False
        monkeypatch.setenv("RUNECLAW_TEST_SWITCH", "   ")
        assert _env_switch("RUNECLAW_TEST_SWITCH", True) is True

    def test_garbage_falls_back_rather_than_guessing(self, monkeypatch):
        monkeypatch.setenv("RUNECLAW_TEST_SWITCH", "maybe")
        assert _env_switch("RUNECLAW_TEST_SWITCH", True) is True
        assert _env_switch("RUNECLAW_TEST_SWITCH", False) is False

    @pytest.mark.parametrize("value,want", [("off", "False"), ("on", "True"),
                                            (None, "True")])
    def test_the_real_flag_reads_from_the_real_env_var(self, value, want):
        """The FIELD is wired to the NAME the deploy runbook sets.

        A flag nothing reads is the defect this whole file exists about, so
        this cannot be a unit test of the helper — it has to go through
        CONFIG.

        IN A SUBPROCESS, deliberately. The first version used
        importlib.reload(bot.config), which builds a NEW CONFIG object while
        bot.core.analyzer keeps the one it bound with `from bot.config import
        CONFIG` at import. The reload left every later test in this file
        patching an object the analyzer no longer reads: they passed alone and
        failed in the full suite. A fresh interpreter is the only honest way
        to observe an import-time default.
        """
        import subprocess
        import sys
        env = dict(os.environ)
        env.pop("LLM_BACKGROUND_SCANS", None)
        if value is not None:
            env["LLM_BACKGROUND_SCANS"] = value
        out = subprocess.run(
            [sys.executable, "-c",
             "from bot.config import CONFIG;"
             "print(CONFIG.analyzer.llm_background_scans)"],
            capture_output=True, text=True, env=env, cwd=os.getcwd(), timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.strip().splitlines()[-1] == want, (
            f"LLM_BACKGROUND_SCANS={value!r} produced "
            f"{out.stdout.strip()!r}, wanted {want} — "
            f"the operator sets this and restarts, and nothing tells them if "
            f"it did not take")


# ── 2. the valve throttles the sweep and nothing else ────────────────────

def _set_valve(value: bool):
    """Flip CONFIG.analyzer.llm_background_scans on a FROZEN dataclass."""
    from bot.config import CONFIG
    prev = CONFIG.analyzer.llm_background_scans
    object.__setattr__(CONFIG.analyzer, "llm_background_scans", value)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.analyzer, "llm_background_scans", prev)


def _analyzer():
    """A bare Analyzer with only what _llm_thesis touches before the valve.

    Built with __new__ rather than a real constructor so the test drives the
    ACTUAL method, not a reimplementation of it. `_llm` must be non-None: the
    first guard in _llm_thesis returns the rule engine when there is no LLM at
    all, and a fixture that tripped it would pass whether the valve worked or
    not.
    """
    from bot.core.analyzer import Analyzer
    a = Analyzer.__new__(Analyzer)
    a._opt_stats = MagicMock()
    a._llm = MagicMock()
    a._llm_cache = MagicMock()
    a._llm_cache.get.return_value = None      # a cache HIT would skip the valve
    a._llm_cache_scope = MagicMock(return_value="test")
    return a


def _signal():
    s = MagicMock()
    s.symbol = "BTC/USDT"
    return s


class TestTheValveThrottlesOnlyTheSweep:
    @pytest.fixture(autouse=True)
    def _valve_off(self):
        # AnalyzerConfig is a FROZEN dataclass, so monkeypatch.setattr raises
        # FrozenInstanceError. object.__setattr__ is the way in, and the
        # restore has to be explicit because monkeypatch never saw the write.
        yield from _set_valve(False)

    @pytest.mark.asyncio
    async def test_background_true_takes_the_rule_engine(self, monkeypatch):
        a = _analyzer()
        monkeypatch.setattr(a, "_rule_based_thesis",
                            lambda sig, ind: {"direction": "LONG", "confidence": 0.6},
                            raising=False)
        out = await a._llm_thesis(_signal(), {}, background=True)
        assert out["source"] == "RULE_ENGINE_BG_THROTTLE", (
            "the sweep still reached the LLM with the valve off")

    @pytest.mark.asyncio
    async def test_a_user_invoked_analysis_with_NO_user_id_keeps_the_llm(self, monkeypatch):
        # THE REGRESSION. background defaults False, so /scan — which passes no
        # user_id — must sail straight past the valve. Keying on `user_id is
        # None` made this return RULE_ENGINE_BG_THROTTLE.
        a = _analyzer()
        reached = {"llm": False}

        def _boom(sig, ind):
            reached["llm"] = False
            return {"direction": "LONG", "confidence": 0.6}

        monkeypatch.setattr(a, "_rule_based_thesis", _boom, raising=False)
        monkeypatch.setattr(
            "bot.core.analyzer.AdaptiveFrequency.should_use_llm",
            staticmethod(lambda sig, ind: (_ for _ in ()).throw(
                AssertionError("REACHED_THE_LLM_PATH"))),
            raising=False)

        with pytest.raises(AssertionError, match="REACHED_THE_LLM_PATH"):
            await a._llm_thesis(_signal(), {}, user_id=None)

    @pytest.mark.asyncio
    async def test_a_rule_engine_with_no_signal_returns_None_not_a_neutral_thesis(
            self, monkeypatch):
        # _rule_based_thesis returns None for "ambiguous confluence + neutral
        # RSI + no MACD" — a real absence of signal. It must stay None: a
        # manufactured 0.5-confidence NEUTRAL would be an invented reading,
        # which is the defect the valve is meant to avoid causing.
        a = _analyzer()
        monkeypatch.setattr(a, "_rule_based_thesis", lambda sig, ind: None,
                            raising=False)
        assert await a._llm_thesis(_signal(), {}, background=True) is None


class TestTheValveIsInertByDefault:
    @pytest.fixture(autouse=True)
    def _valve_on(self):
        yield from _set_valve(True)

    @pytest.mark.asyncio
    async def test_valve_on_lets_the_sweep_reach_the_llm(self, monkeypatch):
        a = _analyzer()
        monkeypatch.setattr(
            "bot.core.analyzer.AdaptiveFrequency.should_use_llm",
            staticmethod(lambda sig, ind: (_ for _ in ()).throw(
                AssertionError("REACHED_THE_LLM_PATH"))),
            raising=False)
        with pytest.raises(AssertionError, match="REACHED_THE_LLM_PATH"):
            await a._llm_thesis(_signal(), {}, background=True)


# ── 3. reachability: `background=True` is set, and set in ONE place ──────

class TestTheFlagIsActuallyReached:
    """A valve nothing sets is indistinguishable from one that does not work.

    #58 in CLAUDE.md, and the reason this repo ratchets unreachable modules.
    Source-scanned deliberately: the property is about the CALLERS, which is
    not visible from inside the analyzer at all.
    """

    def _code_only(self, path):
        import io
        import tokenize
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(io.BytesIO(fh.read()).readline):
                if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                    out.append(tok.string)
        return " ".join(out)

    def test_the_autonomous_tick_sets_background_true(self):
        code = self._code_only("bot/core/engine.py")
        assert "background = True" in code.replace("background=True", "background = True"), (
            "nothing sets background=True — the valve can never fire, and every "
            "test above passes anyway because they call _llm_thesis directly")

    def test_exactly_one_call_site_sets_it(self):
        # More than one means the meaning of `background` has drifted from
        # "the autonomous tick" to "whatever each caller thought", which is how
        # `user_id is None` became a discriminator for something it did not
        # discriminate.
        code = self._code_only("bot/core/engine.py")
        assert code.count("background = True") + code.count("background=True") == 1, (
            "background=True is set in more than one place — say which sweeps "
            "are throttled, in one place, on purpose")

    def test_force_scan_is_not_throttled(self):
        # A person triggered it and is reading the answer. Pinned so that
        # sending it to the rule engine has to be a decision someone makes and
        # writes down, not a default that arrives by accident.
        import inspect

        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine._force_scan_locked)
        assert "background=True" not in src.replace(" ", ""), (
            "force_scan now takes the rule engine — user-visible downgrade")

    def test_the_four_user_paths_still_pass_no_background_flag(self):
        # These are the call sites the first draft would have downgraded. If
        # one ever starts passing background=True, that is a user-visible
        # change and it should fail here first.
        for path in ("bot/skills/scan_skill.py", "bot/skills/skill_registry.py"):
            code = self._code_only(path)
            assert "background = True" not in code.replace("background=True",
                                                           "background = True"), (
                f"{path} throttles a user-invoked analysis to the rule engine")
