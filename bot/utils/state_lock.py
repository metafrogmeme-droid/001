"""Stop two processes silently overwriting each other's paper book.

`docker-compose.yml` runs `python -m bot.main` alongside
`uvicorn api_bridge:app`, and `api_bridge`'s lifespan constructs its own
`RuneClawEngine()` per worker — so with `--workers 2` there were THREE engines
mounting one `./data`. The paper portfolio is the record that feeds the
dashboards and the public track surfaces.

WHY A WRITE LOCK ALONE WOULD BE A FIX THAT DOES NOT FIX

`PortfolioTracker` does not read-modify-write. It loads the file once at
construction (`_load_state_on_init`) and then writes the WHOLE of its
in-memory state on every open and close. Nothing ever re-reads. So two writers
do not interleave edits to a shared document — the second one stamps its
entire, stale world over the first's. Serialising those writes with a mutex
makes the clobbering orderly; it does not make it stop.

WHAT THIS DOES INSTEAD

Every write carries a revision number. Before writing, under an exclusive
cross-process lock, we compare the revision on disk with the one this process
last loaded or wrote. If disk is ahead, somebody else has written since we last
looked and our state no longer describes reality — so we do NOT overwrite it.
The state we were about to write goes to a `.conflict-<pid>.json` sidecar and
the caller is told, loudly.

Nothing is lost and nothing is invented: both versions exist on disk and the
event is visible. Refusing to write is a real cost, which is the point — it is
the honest response to "I cannot safely merge these", and the alternative that
was running until now is a trade disappearing with no trace at all.

With the bridge reduced to one worker this should never fire. A guard that
fires routinely is a workflow; this one is meant to stay quiet and to be
believed when it does not.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

#: Bumped on every successful write. Absent in files written before this
#: existed, which read as revision 0 — a pre-existing file therefore never
#: looks "ahead" of a process that has just loaded it.
REVISION_KEY = "state_revision"

try:                                              # POSIX only; see _locked()
    import fcntl
    _HAVE_FLOCK = True
except ImportError:                               # pragma: no cover - non-POSIX
    fcntl = None                                  # type: ignore[assignment]
    _HAVE_FLOCK = False
    _log.warning(
        "fcntl is unavailable, so the portfolio state file cannot be locked "
        "across processes. Single-process deployments are unaffected; a "
        "multi-process one can lose writes.")


@contextmanager
def locked(path):
    """Hold an exclusive cross-process lock for `path` while the body runs.

    The lock lives on a SIDECAR `<path>.lock`, never on the state file itself:
    `atomic_write_json` publishes by renaming a temp file over the target, so
    the state file's inode changes on every write and a lock held against the
    old inode would guard nothing.

    Degrades to a no-op where `fcntl` is missing rather than refusing to run —
    but says so at import, once, instead of pretending to protect something.
    """
    if not _HAVE_FLOCK:
        yield False
        return
    lock_path = Path(f"{path}.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")                 # noqa: SIM115 - closed below
    except OSError as exc:                         # pragma: no cover
        _log.warning("could not open %s (%s) — proceeding unlocked", lock_path, exc)
        yield False
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield True
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def disk_revision(path) -> Optional[int]:
    """The revision recorded in the file, 0 if it has none, None if unreadable.

    None means "we could not read it", which is NOT the same as 0 and must not
    be treated as one: a file we cannot parse might be newer than ours, and
    guessing zero would license exactly the overwrite this module exists to
    prevent. Callers treat None as "do not risk it".
    """
    p = Path(path)
    if not p.exists():
        return 0                                   # nothing there to clobber
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        _log.warning("portfolio state at %s is unreadable (%s)", p, exc)
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get(REVISION_KEY, 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def conflict_path(path) -> Path:
    """Where a refused write is parked so the data still exists."""
    p = Path(path)
    return p.with_name(f"{p.stem}.conflict-{os.getpid()}{p.suffix}")
