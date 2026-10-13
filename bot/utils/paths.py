"""Where durable state lives, decided once and not by whoever ran the process.

THE INCIDENT (2026-08-19). `/users` reported two registered accounts. Nothing
had been deleted: `bot/db/models.py` held

    DB_PATH = Path(os.getenv("DB_PATH", "data/runeclaw.db"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

and a relative path is resolved against the process's WORKING DIRECTORY. So
which database the bot opened was decided by whoever launched it. `deploy.sh`
symlinks ./data at the repo root to a persistent store that redeploys never
touch, which means a bot started from the repo found the real users and a bot
started from anywhere else — $HOME, /, a unit file with no WorkingDirectory —
found nothing.

AND IT DID NOT FAIL. `mkdir(exist_ok=True)` and `sqlite3.connect` both create,
so a wrong directory produced a brand-new empty database in silence, and every
surface counted it and reported the total with complete confidence. That is
this repository's central rule broken at the storage layer: absent rendered as
a measurement, with no way for any reader above it to tell.

`bot/utils/backup.py` had it too — `critical_paths(root=".")` — so the same
wrong-cwd process backed up an empty set and returned an archive and a
success, which is how the recovery path was lost at the same moment as the
data.

WHY A MODULE AND NOT `parents[2]` IN EACH FILE. Six modules needed anchoring,
and every copy of `Path(__file__).resolve().parents[N]` is a hardcoded count of
how deep that particular file sits. All six happen to be `bot/<pkg>/<mod>.py`
today, so all six happen to want 2 — and the first module that moves, or that
lives one level deeper, gets a root pointing at `bot/` and re-creates the bug
in a place nobody is looking. The depth is computed once, here, where the
answer is a property of this file alone.

Use `state_path("data/thing.json")` for anything that must survive a restart.
An absolute value is returned unchanged, so an operator's explicit choice is
always honoured.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The repo root. `bot/utils/paths.py` → parents[2]. Stated in exactly one
#: place so no other module has to know its own depth.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def state_path(relative: str) -> Path:
    """Absolute path to a durable-state file, independent of the cwd."""
    p = Path(relative).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


def env_state_path(env_var: str, default_relative: str) -> Path:
    """`state_path` for a value an operator may override by environment.

    A RELATIVE override is anchored too. It has exactly the failure the
    relative default had, and an operator who writes `DB_PATH=data/x.db` is
    not asking for "wherever this process happens to be standing" — they are
    naming a file. An absolute value is used as given.
    """
    raw = (os.getenv(env_var) or "").strip()
    return state_path(raw or default_relative)


def repo_path(relative: str) -> Path:
    """Absolute path to a file the repository itself ships, independent of the cwd.

    Today this resolves exactly as `state_path` does. They are two functions
    because they answer two questions: `state_path` is where the bot's own
    durable state lives, which a test-only state root may one day relocate,
    and `repo_path` is where a committed input lives (the macro calendar
    seed, an eligibility record, a config list), which nothing relocates. A
    seed read through `state_path` would vanish from under a test state root
    and read as "no calendar", which fails every entry closed.

    THE INCIDENT SHAPE. `MacroEventProvider(seed_path=Path("config/…"))`
    resolved against the working directory. An engine built from any other
    directory found no seed, marked the calendar blind and returned
    BLOCK_NEW_ENTRIES for every entry, with nothing saying the file was
    simply looked for in the wrong place.
    """
    p = Path(relative).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


def env_repo_path(env_var: str, default_relative: str) -> Path:
    """`repo_path` for a value an operator may override by environment. A
    relative override is anchored too, and a blank one reads as unset, as in
    `env_state_path`."""
    raw = (os.getenv(env_var) or "").strip()
    return repo_path(raw or default_relative)
