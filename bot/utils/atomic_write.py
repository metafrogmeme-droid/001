"""Atomic file replacement that stays atomic when there are two processes.

Twenty-seven stores wrote through a scratch name derived only from the
destination — `path + ".tmp"`, or `path.with_suffix(".tmp")`. `os.replace` is
atomic, and that is the part everyone checked. Filling the scratch file is
not, and its name was the same for every writer:

    process A: open("positions.json.tmp", "w")   truncates
    process B: open("positions.json.tmp", "w")   truncates again
    process A: writes 40 KB
    process B: writes 12 KB, os.replace -> positions.json    (B's, short)
    process A: os.replace -> positions.json                  (A's, interleaved)

The survivor can hold two serializations spliced together, or one truncated
mid-object. Both read back as corrupt JSON, and for `secrets_vault.enc` or
`live_positions.json` that is unrecoverable state, not a lost cache entry.

Latent today only because `api_bridge` happens to run single-process.
`docker-compose.yml:58` and `Dockerfile:47` both specify `--workers 2`, so
the property holding this together is a deployment detail that is already
contradicted by the deployment files.

`tempfile.mkstemp` in the destination's own directory fixes it: the name is
unique per call, and staying in the same directory keeps the rename on one
filesystem, which is what makes `os.replace` atomic in the first place. A
temp file in `/tmp` would cross a device boundary and silently degrade to a
copy — non-atomic again, by a different route.

Two details that are easy to get wrong and are handled here:

  * **Cleanup.** A fixed name self-overwrites, so a crashed write left one
    piece of litter forever. Unique names do not, so a failure that skips
    the rename leaks a file per attempt. Every exit path unlinks.
  * **Mode.** `mkstemp` creates 0600, and `os.replace` carries the temp
    file's mode onto the destination — so a naive migration would silently
    make every store private and break any reader running as another user.
    The destination's existing mode is preserved; a new file gets 0644
    unless the caller asks for something tighter.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

StrPath = Union[str, "os.PathLike[str]"]

# Matches what `open(path, "w")` produces under a conventional 022 umask, so
# migrating a call site does not change who can read the file.
_DEFAULT_MODE = 0o644


def _target_mode(path: Path, requested: Optional[int]) -> int:
    if requested is not None:
        return requested
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return _DEFAULT_MODE


def atomic_write_bytes(path: StrPath, data: bytes, *,
                       mode: Optional[int] = None,
                       fsync: bool = True) -> None:
    """Write `data` to `path` via a uniquely-named temp file in the same dir.

    `fsync` defaults to True: without it `os.replace` can publish a rename
    the data has not landed behind, so a power loss leaves an empty file
    where the old contents used to be — a durable-looking write that isn't.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    final_mode = _target_mode(p, mode)
    # prefix ties the scratch file to its destination so anything left by a
    # hard kill is identifiable; the dot keeps it out of casual listings.
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        os.chmod(tmp, final_mode)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: StrPath, text: str, *, encoding: str = "utf-8",
                      mode: Optional[int] = None, fsync: bool = True) -> None:
    """Text form of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode, fsync=fsync)


def atomic_write_json(path: StrPath, obj: Any, *, encoding: str = "utf-8",
                      mode: Optional[int] = None, fsync: bool = True,
                      **json_kwargs: Any) -> None:
    """JSON form. `json_kwargs` pass straight through to `json.dumps`.

    `indent=2` is applied only when the caller specified neither `indent` nor
    `separators`, so a compact store stays compact. Nothing else is defaulted
    — in particular `default=str` is NOT, because it turns a write that used
    to raise on an unserializable object into one that silently stores its
    repr. Call sites that had it keep passing it; the rest keep failing loudly.
    """
    if "indent" not in json_kwargs and "separators" not in json_kwargs:
        json_kwargs["indent"] = 2
    atomic_write_text(path, json.dumps(obj, **json_kwargs),
                      encoding=encoding, mode=mode, fsync=fsync)
