"""Refuse to start when runtime state would be written somewhere a redeploy deletes.

Every state path in this codebase is RELATIVE and resolved against the process
working directory:

    live_executor.py   os.environ.get("RUNECLAW_STATE_DIR", "data")
    logger.py          LOG_DIR = Path("logs")          (and mkdir at import)
    engine.py          AuditChain("logs/audit_chain.jsonl")

They are correct today only because the bot happens to be launched from the repo
root. Launch it from anywhere else and all three silently create fresh
directories at the new CWD — bypassing the persistent-store symlinks entirely.
The bot keeps trading, writes its state somewhere the next redeploy wipes, and
nothing anywhere says so. That is the 2026-08-02 shape again: a success message
printed over a data loss.

The audit chain makes it worse than ordinary data loss. It is hash-chained from
a genesis record, so a second chain started in a different directory does not
merely lose history — it produces a chain that cannot be reconciled with the
first. Discontinuity is exactly what the chain exists to make visible, and this
would manufacture it accidentally.

So: check before anything writes. Two questions, in order of how badly they end.

  1. Are we at the repo root? If not, every relative path above is wrong.
  2. Does a persistent store exist, and if so are data/ and logs/ actually
     linked into it? A real directory where a symlink belongs means deploy.sh
     has not run (or half-ran), and everything written there dies at the next
     redeploy.

Check 2 is conditional on the store existing, because a developer checkout with
no store is a legitimate configuration — nothing is going to wipe it. Check 1 is
unconditional: a wrong CWD is never correct.

Fail CLOSED and LOUD. An operator who sees this refuse has a five-second fix; an
operator who does not see it loses the vault and finds out days later.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Files that identify the repo root. Both must be present: `bot/` alone also
# matches a parent directory that happens to contain a bot/ folder.
_ROOT_MARKERS = ("bot", "deploy.sh")

# Paths deploy.sh links into the persistent store. Keep in sync with deploy.sh —
# tests/test_state_guard.py asserts they match, so adding one there without
# adding it here fails the suite rather than silently going unguarded.
_LINKED = ("data", "logs")

_SKIP_ENV = "RUNECLAW_SKIP_STATE_GUARD"


class StateGuardError(RuntimeError):
    """Runtime state would be written somewhere unsafe. Never caught internally."""


def persist_dir(env: Optional[dict] = None) -> Path:
    """Where deploy.sh keeps the persistent store. Mirrors its PERSIST_DIR."""
    e = env if env is not None else os.environ
    explicit = str(e.get("PERSIST_DIR", "") or "").strip()
    if explicit:
        return Path(explicit)
    return Path(e.get("HOME", str(Path.home()))) / "runeclaw-persist"


def check_state_paths(cwd: Optional[Path] = None,
                      env: Optional[dict] = None) -> list:
    """Return a list of problems. Empty means safe. Pure — never raises, never
    writes, so it is usable from a health check as well as from startup."""
    e = env if env is not None else os.environ
    here = Path(cwd) if cwd is not None else Path.cwd()
    problems: list = []

    missing = [m for m in _ROOT_MARKERS if not (here / m).exists()]
    if missing:
        problems.append(
            f"not at the repo root: {here} is missing {', '.join(missing)}. "
            f"Every state path is relative to the working directory, so state "
            f"would be written here instead of the repo. Start the bot with "
            f"the repo root as its working directory.")
        # No point checking the links — we are not in the tree they live in.
        return problems

    store = persist_dir(e)
    if not store.exists():
        # A checkout with no persistent store is a legitimate developer setup:
        # nothing redeploys over it, so relative paths are not a hazard.
        return problems

    for name in _LINKED:
        p = here / name
        if not p.exists() and not p.is_symlink():
            problems.append(
                f"{name}/ does not exist. deploy.sh creates and links it; run "
                f"./deploy.sh before starting.")
            continue
        if not p.is_symlink():
            problems.append(
                f"{name}/ is a real directory, not a symlink into {store}. "
                f"A persistent store exists, so this directory is inside the "
                f"redeploy path and everything written to it is destroyed on "
                f"the next deploy. Run ./deploy.sh.")
            continue
        try:
            target = p.resolve()
        except OSError:
            problems.append(f"{name}/ is a symlink that cannot be resolved (dangling).")
            continue
        expected = (store / name).resolve() if (store / name).exists() else (store / name)
        if target != expected:
            problems.append(
                f"{name}/ points at {target}, not {expected}. State would be "
                f"written outside the persistent store.")
    return problems


def assert_state_paths_are_safe(cwd: Optional[Path] = None,
                                env: Optional[dict] = None) -> None:
    """Raise StateGuardError if state would be written somewhere unsafe.

    Set RUNECLAW_SKIP_STATE_GUARD=1 to bypass. That exists for CI and for an
    operator who genuinely knows better — it is deliberately not the default,
    and it prints what it is skipping so a bypass left on by accident is
    visible in the log rather than silent.
    """
    e = env if env is not None else os.environ
    problems = check_state_paths(cwd=cwd, env=e)
    if not problems:
        return
    if str(e.get(_SKIP_ENV, "")).strip().lower() in ("1", "true", "yes", "on"):
        print(f"WARNING: {_SKIP_ENV} is set — starting despite unsafe state paths:")
        for p in problems:
            print(f"  - {p}")
        return
    raise StateGuardError(
        "refusing to start — runtime state would be written where a redeploy "
        "deletes it:\n" + "\n".join(f"  - {p}" for p in problems)
        + f"\n\nFix the above, or set {_SKIP_ENV}=1 to override deliberately."
    )
