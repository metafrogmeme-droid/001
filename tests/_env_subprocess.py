"""Run a snippet in a FRESH interpreter with env overrides — the safe way to
test import-time env reads.

`importlib.reload` in-process is the alternative, and it is poison: reloading
a module re-executes it in the same namespace, replacing every class/function
object. Test modules that imported those objects at collection time keep the
old ones, so their monkeypatch.setattr targets silently stop matching what the
code under test resolves — which is exactly how a single reload-based test
broke 30 unrelated tests across the suite (2026-07 full-suite audit). A
subprocess exercises the real import-time path with zero INTERPRETER-state
damage -- which is the part that sentence used to over-claim. A subprocess
isolates objects; it inherits the FILESYSTEM, and this repo deliberately
persists secrets there so they survive a wiped .env. See run_py's body: the
state dir and the .env are both neutralised explicitly, because isolating the
interpreter and calling that isolation was how a removed variable came back.
"""
import os
import subprocess
import sys
import tempfile


def run_py(code: str, env_overrides: dict[str, str] | None = None,
           env_removals: tuple[str, ...] = ()) -> str:
    """Execute `code` with `sys.executable -c` and return stripped stdout.

    Raises on nonzero exit with stderr attached, so assertion failures inside
    the snippet surface as readable test failures.
    """
    env = dict(os.environ)
    for k in env_removals:
        env.pop(k, None)

    # A REMOVED VARIABLE MUST STAY REMOVED, AND TWO THINGS PUT IT BACK.
    #
    # This helper's contract is "a fresh interpreter with EXACTLY these
    # variables". `bot.config` reads two sources at import that are not in that
    # list, and both can reinstate a key `env_removals` just dropped:
    #
    #   the VAULT   bot/config.py:71 calls secrets_vault.seed_and_restore(),
    #               whose entire job is restoring BITGET_*, the Telegram token
    #               and the LLM keys that a wiped .env has lost. It reads
    #               $RUNECLAW_STATE_DIR (default "data"), and this subprocess
    #               ran with cwd=repo root -- so it read the vault the SUITE
    #               ITSELF had been writing.
    #
    #   the .env    resolved from bot/config.py's __file__, not from cwd
    #               (deliberately, so a bot started anywhere still finds it),
    #               and loaded with override=True by default. A developer with
    #               a populated .env gets the same defeat through a second door.
    #
    # The vault one is the confirmed cause of the intermittent
    # test_exchange_config_uses_alias failure. That test removes
    # BITGET_PASSPHRASE to prove the legacy alias is read instead; the leftover
    # data/secrets_vault.enc holds a BITGET_PASSPHRASE entry, so when it still
    # decrypts it is put straight back and the assertion fails on correct code.
    # It is intermittent because it turns on whether the master key has rotated
    # since whichever earlier test seeded it -- a stale key fails to decrypt,
    # the restore silently no-ops, and that is the run where the test passes.
    #
    # Both are closed with the knobs the code already provides, so no feature
    # is disabled and no behaviour is invented: point the state dir at an empty
    # scratch directory, and tell config the inherited environment wins.
    # Overrides are applied AFTER, so a caller that wants to exercise either
    # source can still say so explicitly.
    with tempfile.TemporaryDirectory() as _state:
        env["RUNECLAW_STATE_DIR"] = _state
        env["RUNECLAW_ENV_INHERIT"] = "1"
        env.update(env_overrides or {})
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env, timeout=120,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"subprocess check failed (exit {proc.returncode}):\n{proc.stderr}")
    return proc.stdout.strip()
