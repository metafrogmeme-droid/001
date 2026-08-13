"""Contract Studio's dedicated smart-contract model routing (two-box split).

One 8GB box serves the trade LLM; the other serves a code-base fine-tune for
Solidity drafting. ``bot.llm.provider.sc_config`` is the switch, and these
tests lock its contract:

- INERT unless both RUNECLAW_SC_BASE_URL and RUNECLAW_SC_MODEL are set —
  half a config must not half-enable the route (the tier_gate convention).
- Read at CALL time, not import time — PROVIDER_CATALOG's import-time env
  reads are a documented restart trap; this switch must not add another.
- Keyless is allowed (the auth proxy adds the bearer, but a bare local
  Ollama needs nothing) and a set key is carried through.
- max_tokens must fit a full contract draft: the chat tier's 1024 default
  truncates a 1,500-2,500-token Solidity draft mid-body, and a model that
  looks like it "learned to emit half a contract" is really a config bug.

The gateway handler's fallback behavior (SC failure -> chat tier, provider
field naming whoever answered) is covered by the source-level assertions at
the bottom: the seam is env-driven and the failure path needs a live
endpoint to exercise, so the wiring is what can be pinned here.
"""

import os

import pytest

from bot.llm.provider import LLMProvider, sc_config


_ENV_KEYS = ("RUNECLAW_SC_BASE_URL", "RUNECLAW_SC_MODEL", "RUNECLAW_SC_API_KEY")


@pytest.fixture(autouse=True)
def _clean_sc_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_inert_when_unconfigured():
    assert sc_config() is None


def test_inert_when_only_half_configured(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SC_BASE_URL", "http://127.0.0.1:11435/v1")
    assert sc_config() is None
    monkeypatch.delenv("RUNECLAW_SC_BASE_URL")
    monkeypatch.setenv("RUNECLAW_SC_MODEL", "runeclaw-sc")
    assert sc_config() is None


def test_blank_values_do_not_enable(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SC_BASE_URL", "   ")
    monkeypatch.setenv("RUNECLAW_SC_MODEL", "runeclaw-sc")
    assert sc_config() is None


def test_configured_returns_runeclaw_keyless_config(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SC_BASE_URL", "http://127.0.0.1:11435/v1")
    monkeypatch.setenv("RUNECLAW_SC_MODEL", "runeclaw-sc")
    cfg = sc_config()
    assert cfg is not None
    assert cfg.provider is LLMProvider.RUNECLAW
    assert cfg.model == "runeclaw-sc"
    assert cfg.resolved_base_url() == "http://127.0.0.1:11435/v1"
    # Keyless local serving is legitimate; the config must count as usable.
    assert cfg.is_configured()


def test_api_key_is_carried_through(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SC_BASE_URL", "http://127.0.0.1:11435/v1")
    monkeypatch.setenv("RUNECLAW_SC_MODEL", "runeclaw-sc")
    monkeypatch.setenv("RUNECLAW_SC_API_KEY", "proxy-bearer-token-0123456789")
    cfg = sc_config()
    assert cfg is not None and cfg.api_key == "proxy-bearer-token-0123456789"


def test_env_read_at_call_time_not_import_time(monkeypatch):
    # sc_config was imported at module load with the env unset; setting the
    # env NOW must be enough. If this fails, the switch grew an import-time
    # snapshot and inherited the PROVIDER_CATALOG restart trap.
    assert sc_config() is None
    monkeypatch.setenv("RUNECLAW_SC_BASE_URL", "http://127.0.0.1:11435/v1")
    monkeypatch.setenv("RUNECLAW_SC_MODEL", "runeclaw-sc")
    assert sc_config() is not None


def test_max_tokens_fits_a_full_contract_draft():
    os.environ["RUNECLAW_SC_BASE_URL"] = "http://127.0.0.1:11435/v1"
    os.environ["RUNECLAW_SC_MODEL"] = "runeclaw-sc"
    try:
        cfg = sc_config()
        assert cfg is not None
        # v9 drafts measure up to ~1,600 tokens of code plus notes; 2048 is
        # the floor below which drafts truncate mid-contract.
        assert cfg.max_tokens >= 2048
        # An 8GB box streams a draft in minutes; the chat tier's 15s default
        # would abort every single one.
        assert cfg.timeout_seconds >= 60
    finally:
        for key in _ENV_KEYS:
            os.environ.pop(key, None)


def test_sc_modelfile_system_matches_training_prompt():
    """ollama/Modelfile.sc's SYSTEM block must be byte-identical to
    ollama/sc_system_prompt.txt — the file the trainer consumes via
    --system-prompt @sc_system_prompt.txt. If they diverge, the model
    serves under a prompt it was never trained with, which is exactly the
    silent drift this repo's render-don't-restate rule exists to prevent."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "ollama"
    trained = (root / "sc_system_prompt.txt").read_text(encoding="utf-8").strip()
    modelfile = (root / "Modelfile.sc").read_text(encoding="utf-8")
    m = re.search(r'SYSTEM\s+"""(.*?)"""', modelfile, re.DOTALL)
    assert m, "Modelfile.sc has no SYSTEM block"
    assert m.group(1).strip() == trained


def test_gateway_routes_contract_studio_through_sc_config():
    """Wiring, pinned at the source level: the handler must consult
    sc_config and must keep the chat-tier fallback. Behavior around a live
    endpoint can't run here; presence + order of the seam can."""
    import inspect

    from bot.web import user_gateway

    src = inspect.getsource(user_gateway.handle_contract_studio)
    assert "sc_config" in src, "Contract Studio no longer consults the SC model switch"
    assert "_llm_chat" in src, "the chat-tier fallback was removed"
    # Fallback must be honest: the SC path sets its own provider name so the
    # response never claims the SC model answered when the chat tier did.
    assert "runeclaw-sc" in src
