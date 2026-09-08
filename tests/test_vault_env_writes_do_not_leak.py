"""A vault write reaches `os.environ`, and monkeypatch cannot take it back.

`secrets_vault.store_secrets` writes each secret straight into the process
environment — deliberately, and its own comment says why:

    # Always update the live environment first — recovery of the running
    # process
    os.environ[k] = v

`seed_and_restore` does the same at boot. Both are correct. Neither goes
through `monkeypatch`, so monkeypatch's teardown has no record of the write
and restores nothing, and the value outlives the test that made it.

WHAT THAT COST, MEASURED. `test_vault_keeps_what_it_cannot_read.py` (added
2026-09-08) calls `store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})` to prove
the recovery path does not erase the rest of the vault. It does prove that.
It also left `WEB_GATEWAY_SECRET` set for every test that ran afterwards, and
`user_gateway._secret()` reads the environment FIRST:

    return os.environ.get("WEB_GATEWAY_SECRET", "") or _GATEWAY_SECRET

so every later test that plants a secret by monkeypatching the module
attribute was silently overridden by a 48-character `ggg…`. **40 of
`tests/test_web_gateway.py`'s 48 tests failed 403 in a full run and all 48
passed alone** — confirm, the live-mode gate, the portfolio snapshot,
authority apply / revoke / enforce. `ci_test_gate`'s flake filter re-ran each
failure alone, saw it pass, filed it order-dependent, and counted none of
them. The money-facing HTTP surface was in CI and gating nothing.

Nothing about that is visible from a green suite, which is the whole problem:
the gate reported success, the tests reported success individually, and the
only artefact was a count of "flaky" nobody reads. It was found by watching
`os.environ` after every test in one full run and printing the first change.

The containment is an autouse fixture in `tests/conftest.py`, sibling to the
one that hands back the analyzer's lookahead flags for the same reason. This
file is what stops it silently coming back.
"""

import os

import pytest

import bot.core.secrets_vault as sv
import bot.web.user_gateway as ug
from tests.dep_policy import require

require("cryptography", "the vault seals with Fernet")

KEY = "WEB_GATEWAY_SECRET"
PLANTED = "g" * 48


class TestThePrecedenceThatMadeItFatal:
    """Why one stray environment variable took out 40 tests in another file."""

    def test_the_environment_wins_over_the_module_attribute(self, monkeypatch):
        monkeypatch.setattr(ug, "_GATEWAY_SECRET", "m" * 48)
        monkeypatch.setenv(KEY, "e" * 48)
        assert ug._secret() == "e" * 48, (
            "the module attribute wins — which would be a different bug, but "
            "the tests that plant a secret rely on the documented fallback")

    def test_the_module_attribute_is_the_fallback_when_env_is_unset(
            self, monkeypatch):
        monkeypatch.setattr(ug, "_GATEWAY_SECRET", "m" * 48)
        monkeypatch.delenv(KEY, raising=False)
        assert ug._secret() == "m" * 48, (
            "this fallback is what every test in test_web_gateway.py plants "
            "its secret through; without it they cannot authenticate at all")

    def test_a_leaked_value_silently_overrides_a_planted_one(self, monkeypatch):
        # The exact failure, in one test: a test plants its secret the
        # supported way, an earlier write is still in the environment, and the
        # request authenticates against the wrong one.
        monkeypatch.setenv(KEY, PLANTED)          # the leak
        monkeypatch.setattr(ug, "_GATEWAY_SECRET", "m" * 48)   # the plant
        assert ug._secret() != "m" * 48
        assert ug._secret() == PLANTED, (
            "a stale environment value no longer shadows the planted secret — "
            "if this changed deliberately, the conftest containment can go")


class TestTheWriteReachesTheEnvironmentAndIsContained:
    """Both halves, and the order of the two tests below is the assertion."""

    def test_store_secrets_really_does_write_the_environment(
            self, tmp_path, monkeypatch):
        # Asserted WITHIN one test, so the containment fixture cannot make
        # this vacuous — the same argument `_restore_lookahead_flags` makes.
        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", _fresh_key())
        monkeypatch.delenv(KEY, raising=False)

        sv.store_secrets({KEY: PLANTED})
        assert os.environ.get(KEY) == PLANTED, (
            "store_secrets no longer reaches the running process — that is "
            "the recovery behaviour its own comment promises")

    def test_the_next_test_does_not_inherit_it(self):
        # ORDERING IS THE POINT and pytest runs a file in source order: this
        # runs immediately after the write above. Before the conftest fixture
        # existed, this is where the value was still sitting — and it kept
        # sitting there for the remaining ~9,000 tests in the session.
        assert os.environ.get(KEY) is None, (
            "a vault write escaped its test. Every later test that reads "
            "WEB_GATEWAY_SECRET is now authenticating against it")

    def test_the_managed_keys_are_all_held_not_just_the_one_that_bit(self):
        # Containment scoped to the key that happened to hurt would be the
        # same defect waiting on a different variable — BOT_SYNC_SECRET breaks
        # dashboard sync, the Bitget triple is every linked account.
        from bot.core.secrets_vault import _managed_keys
        held = set(_managed_keys())
        for k in ("WEB_GATEWAY_SECRET", "BOT_SYNC_SECRET", "BITGET_API_KEY",
                  "TELEGRAM_BOT_TOKEN"):
            assert k in held, f"{k} is not vault-managed, so nothing holds it"


def _fresh_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


@pytest.mark.parametrize("name", ["BOT_SYNC_SECRET", "BITGET_API_SECRET"])
def test_any_managed_key_is_handed_back(name, tmp_path, monkeypatch):
    """One key proves the fixture runs; the parametrisation proves its scope
    is the managed set rather than a special case for the one that bit."""
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RUNECLAW_SECRETS_KEY", _fresh_key())
    monkeypatch.delenv(name, raising=False)
    sv.store_secrets({name: "z" * 48})
    assert os.environ.get(name) == "z" * 48


def test_the_parametrised_writes_above_are_gone():
    for name in ("BOT_SYNC_SECRET", "BITGET_API_SECRET"):
        assert os.environ.get(name) is None, f"{name} escaped its test"
