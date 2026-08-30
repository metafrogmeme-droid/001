"""`run_py` promised "exactly these variables" and delivered "these, plus disk".

THE FLAKE THIS CLOSES

Two consecutive full-suite runs failed on two DIFFERENT subprocess tests, each
passing in isolation. One was
test_passphrase_env_alias::test_exchange_config_uses_alias, which removes
BITGET_PASSPHRASE to prove `_env_secret_any` falls back to the legacy
BITGET_API_PASSPHRASE spelling.

`run_py` popped the variable and `bot.config` put it back. Its import calls
secrets_vault.seed_and_restore(), whose entire job is restoring BITGET_*, the
Telegram token and the LLM keys that a wiped .env has lost -- reading
$RUNECLAW_STATE_DIR, default "data", relative to a cwd that was the repo root.
So it read the vault THE SUITE ITSELF had been writing, and handed back the
variable the test had just removed.

Reproduced by seeding a decryptable vault holding BITGET_PASSPHRASE: the old
helper returned the vaulted value instead of the legacy one, which is the CI
failure exactly. It was intermittent because it depended on whether the master
key had rotated since some earlier test seeded it -- a stale key fails to
decrypt, the restore silently no-ops, and that is the run where it passes. A
1-in-8760 failure whose real trigger was "which tests ran first".

WHY THE FIX IS AT THIS BOUNDARY AND NOT IN conftest

The tempting fix is adding data/secrets_vault.enc to conftest's cleanup list.
That list is DELETED between tests, and on a developer machine the vault holds
real Bitget credentials -- which is the destroy-the-operator's-secrets defect
this audit already fixed once, reintroduced through a different door. So the
isolation goes where the contract is: run_py points the state dir at an empty
scratch directory and tells config the inherited environment wins.
"""
from __future__ import annotations

import os
import subprocess
import sys

from tests._env_subprocess import run_py


def test_a_removed_variable_stays_removed_even_with_a_vault_present(tmp_path):
    """The regression, driven against a real seeded vault.

    Builds the exact condition the suite creates -- a decryptable vault holding
    BITGET_PASSPHRASE -- then asks run_py to remove that variable. If the vault
    can reach the subprocess, the value comes back and this fails.
    """
    state = tmp_path / "state"
    state.mkdir()
    seed = subprocess.run(
        [sys.executable, "-c",
         "from bot.core.secrets_vault import seed_and_restore; seed_and_restore()"],
        capture_output=True, text=True, timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ,
             "RUNECLAW_STATE_DIR": str(state),
             "SECRETS_VAULT_ENABLED": "true",
             "BITGET_API_KEY": "k",
             "BITGET_API_SECRET": "s",
             "BITGET_PASSPHRASE": "VAULTED_PASSPHRASE"})
    assert seed.returncode == 0, f"could not seed a test vault: {seed.stderr[:400]}"
    assert (state / "secrets_vault.enc").exists(), (
        "the vault was not written, so this test would pass without proving "
        "anything about vault interference")

    # Point the AMBIENT environment at that vault, exactly as a suite run does
    # by leaving one in ./data, then ask run_py to remove the key it holds.
    os.environ["RUNECLAW_STATE_DIR"] = str(state)
    try:
        out = run_py(
            "from bot.config import ExchangeConfig\n"
            "print(ExchangeConfig().passphrase)",
            env_overrides={"BITGET_API_PASSPHRASE": "legacy-pass"},
            env_removals=("BITGET_PASSPHRASE",))
    finally:
        os.environ.pop("RUNECLAW_STATE_DIR", None)

    assert out == "legacy-pass", (
        f"run_py returned {out!r}. A variable passed in env_removals came back "
        f"from the secrets vault, so the helper's contract -- a fresh "
        f"interpreter with EXACTLY these variables -- does not hold, and every "
        f"test built on it is only passing when the vault happens not to "
        f"decrypt.")


def test_the_subprocess_cannot_see_the_repo_state_dir():
    """The property directly: whatever ./data holds is not what it reads."""
    out = run_py(
        "import os\n"
        "from bot.core.secrets_vault import _vault_file\n"
        "print(os.path.abspath(_vault_file()))")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not out.startswith(os.path.join(repo, "data")), (
        f"the subprocess resolves its vault to {out}, inside the repo. It will "
        f"read whatever the suite has been writing there.")


def test_a_caller_can_still_opt_back_in():
    """Isolation must be a default, not a wall.

    A test that genuinely wants to exercise the vault or the repo .env has to
    be able to say so, or the next person works around the helper instead of
    with it.
    """
    out = run_py("import os; print(os.environ['RUNECLAW_STATE_DIR'])",
                 env_overrides={"RUNECLAW_STATE_DIR": "/tmp/explicitly-chosen"})
    assert out == "/tmp/explicitly-chosen", (
        f"an explicit env_override was overwritten by the isolation defaults "
        f"(got {out!r}); overrides must be applied last")


def test_overrides_and_removals_still_work_together():
    """The helper's original contract, unbroken by the isolation."""
    out = run_py("import os; print(os.environ.get('SOME_VAR', 'ABSENT'))",
                 env_overrides={"SOME_VAR": "present"})
    assert out == "present"
    os.environ["SOME_VAR_TO_DROP"] = "leaked"
    try:
        out = run_py("import os; print(os.environ.get('SOME_VAR_TO_DROP', 'ABSENT'))",
                     env_removals=("SOME_VAR_TO_DROP",))
    finally:
        os.environ.pop("SOME_VAR_TO_DROP", None)
    assert out == "ABSENT", f"env_removals did not remove the variable (got {out!r})"
