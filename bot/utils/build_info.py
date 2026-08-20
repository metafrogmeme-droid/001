"""Which commit is this process actually running?

THE INCIDENT (2026-08-20). A deploy on the box ran

    git fetch origin && git reset --hard origin/main

and reported "Deploy-pull completed successfully". `origin` there is a GitLab
mirror 255 commits stale; the real repository is a remote named `backup`. Every
check the operator ran passed, because each was true of the stale tree — the
pull worked, the symlinks were right, the user store loaded, eighteen users
were present. Seven merged fixes were one restart away from being reported as
deployed.

`scripts/verify_deploy_source.sh` now refuses to let that deploy start. This
module answers the half no pre-launch gate can: AFTER a restart, what is
running right now? The bot's only self-report was

    ⚔️ RUNECLAW v0.1.0

`__version__` is a hand-maintained string in `bot/__init__.py` that has never
changed and reads identically before and after every deploy this repo will ever
do. It is a claim about intent. A commit id is a fact about the bytes on disk.

THE PYTHON HALF NEVER JOINED A CHAIN THAT ALREADY EXISTED. `app/lib/version.js`
solved this in July, and `Dockerfile:35` writes `/app/build-info.json` — which
is the repo root in the image — precisely so the web app can read it without a
`.git`. That stamp is not unused; it is used by the *other* runtime. The bot
runs on the same box, behind the same deploys, and had none of it. So the order
below is inherited rather than rediscovered, scars included: an image with no
`.git`, a host with no `git` binary, a bundle with neither.

    1. BUILD_SHA / SOURCE_COMMIT env  — set by a container build ARG
    2. build-info.json at the repo root — the Dockerfile stamp, now read here too
    3. `git rev-parse`                — the re-clone deploy path has a real .git
    4. .git plumbing read directly    — .git present, `git` not on PATH
    5. UNKNOWN                        — never a fabricated value

WHAT IS NOT CLAIMED. `dirty` is tri-state on purpose. A working tree can only
be described when the sha was resolved FROM that tree; when the stamp came from
a build arg the tree is not ours to characterise, and printing a bare sha there
would assert "clean" without having looked. `None` means not checked, and the
rendered label says so. Absent is never a measurement — including this one.

It matters here specifically because patching a box by hand is a thing that
happens on this project, and a bot reporting `0449bc7` with three hand-edited
files is false provenance dressed as precision.

§F-15: reads ref plumbing and environment variables named above, nothing else.
Never object contents, never .env, never data/. A commit id is public metadata
and is the only thing that leaves this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bot.utils.paths import REPO_ROOT

#: A hex object name, full or abbreviated. Anything else is discarded rather
#: than displayed, because a stamp that is not a commit id is worse than no
#: stamp — it looks like one. Two real values this rejects:
#:
#:   "dev"          — `ARG BUILD_SHA=dev` in our own Dockerfile, so an image
#:                    built without --build-arg carries it by default
#:   "${BUILD_SHA}" — an unexpanded build arg, which a shell will happily write
#:
#: Both fall through to the next source instead, and `unknown` is the floor.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: What a reader sees when nothing resolved. A word, never an empty string: a
#: blank beside the label "Build" reads as "no build problem", and this module
#: exists because an absent fact was read as a reassuring one.
UNKNOWN = "unknown"

#: How the sha was obtained, in the words a reader needs. Not decoration — it
#: is the difference between "the tree says so" and "the build said so", which
#: is what makes the `tree unchecked` caveat legible rather than alarming.
_SOURCE_TEXT = {
    "env": "build stamp",
    "image": "build stamp",
    "git": "git",
    "gitdir": "git refs",
}

_GIT_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class BuildInfo:
    """sha is None when nothing resolved. dirty is None when nothing looked."""

    sha: str | None
    source: str          # "env" | "image" | "git" | "gitdir" | "none"
    dirty: bool | None


def _git(args: list[str], root: Path) -> str | None:
    """Run git ANCHORED TO THE REPO — never to the caller's working directory.

    `scripts/verify_deploy_source.sh` shipped with a bare `git rev-parse HEAD`
    and so answered from whatever directory the launcher happened to be
    standing in. For that script's own documented usage — a launcher living
    OUTSIDE the repo, which is where a launcher belongs — that is not this
    repository at all; for a caller sitting in some other checkout it is a
    confident answer about the wrong code. Same trap, same fix: `-C`.

    Returns None when git could not answer, and the raw (stripped) output
    otherwise — INCLUDING the empty string, which is a real answer for
    `status --porcelain` and must not be confused with failure.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None                      # no git binary, or it hung
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _clean_sha(raw: object) -> str | None:
    """A sha, or None. Anything unparseable is absent, not a default."""
    if not isinstance(raw, str):
        return None
    val = raw.strip().lower()
    return val if _SHA_RE.match(val) else None


def _from_env() -> str | None:
    for var in ("BUILD_SHA", "SOURCE_COMMIT"):
        sha = _clean_sha(os.environ.get(var))
        if sha is not None:
            return sha
    return None


def _from_build_file(root: Path) -> str | None:
    """The stamp `Dockerfile:35` writes, read from the Python half as well.

    The Dockerfile writes it to `/app/build-info.json`, and `/app` is WORKDIR
    and therefore REPO_ROOT inside the image, so the repo root is the only
    place that needs checking.
    """
    try:
        data = json.loads((root / "build-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _clean_sha(data.get("sha")) if isinstance(data, dict) else None


def _from_git_plumbing(root: Path) -> str | None:
    """HEAD → loose ref → packed-refs, with no `git` binary involved.

    A re-clone deploy has a real `.git` even when the image has no git on PATH.
    Handles a `.git` FILE (worktree/submodule pointer) as well as a directory,
    and a detached HEAD, because a deploy that reset to a SHA rather than a
    branch is exactly the situation this is most needed in.
    """
    try:
        git_dir = root / ".git"
        if git_dir.is_file():
            match = re.search(r"^gitdir:\s*(.+?)\s*$",
                              git_dir.read_text(encoding="utf-8"), re.M)
            if match is None:
                return None
            git_dir = (root / match.group(1)).resolve()

        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        ref = re.match(r"^ref:\s*(.+)$", head)
        if ref is None:
            return _clean_sha(head)      # detached: HEAD holds the sha itself

        ref_name = ref.group(1).strip()
        try:
            loose = (git_dir / ref_name).read_text(encoding="utf-8").strip()
            sha = _clean_sha(loose)
            if sha is not None:
                return sha
        except OSError:
            pass                         # not a loose ref — try packed-refs

        try:
            packed = (git_dir / "packed-refs").read_text(encoding="utf-8")
        except OSError:
            return None
        for line in packed.splitlines():
            if not line or line[0] in "#^":
                continue
            sha_part, _, name = line.partition(" ")
            if name.strip() == ref_name:
                return _clean_sha(sha_part)
        return None
    except (OSError, ValueError):
        return None


def _working_tree_dirty(root: Path) -> bool | None:
    """True / False / None — and the None arm is the whole point.

    `git status --porcelain` prints NOTHING for a clean tree and exits 0. That
    is the same empty output a failed invocation would produce if failure were
    folded into "". Clean and could-not-tell are different facts, and this repo
    has been bitten more than once by collapsing exactly that distinction — so
    `_git` reports failure as None and success-with-no-output as "".

    Untracked files are excluded deliberately. `deploy.sh` symlinks data/,
    logs/ and .env into the tree and an operator's `nohup.out` lands beside
    them; none of that is a difference in the CODE, which is the only question
    being asked here. A guard that cries wolf gets ignored, and then it is not
    a guard.
    """
    out = _git(["status", "--porcelain", "--untracked-files=no"], root)
    if out is None:
        return None
    return out != ""


def resolve(root: Path | None = None) -> BuildInfo:
    """Work out the running commit. Uncached — see `build_info()`."""
    root = REPO_ROOT if root is None else root

    sha = _from_env()
    if sha is not None:
        return BuildInfo(sha=sha, source="env", dirty=None)

    sha = _from_build_file(root)
    if sha is not None:
        return BuildInfo(sha=sha, source="image", dirty=None)

    sha = _clean_sha(_git(["rev-parse", "HEAD"], root))
    if sha is not None:
        return BuildInfo(sha=sha, source="git", dirty=_working_tree_dirty(root))

    sha = _from_git_plumbing(root)
    if sha is not None:
        # `.git` was readable but git(1) was not usable, so the tree's state is
        # not knowable by the route that produced the sha.
        return BuildInfo(sha=sha, source="gitdir", dirty=None)

    return BuildInfo(sha=None, source="none", dirty=None)


_CACHED: BuildInfo | None = None


def build_info(*, refresh: bool = False) -> BuildInfo:
    """The running build, resolved once and memoised.

    Memoising is not just a cost saving: the question is "what did this process
    START with", and a long-lived bot whose checkout is updated underneath it
    should keep reporting what it is actually executing, not what is on disk
    now.
    """
    global _CACHED
    if _CACHED is None or refresh:
        _CACHED = resolve()
    return _CACHED


def short(info: BuildInfo | None = None) -> str:
    """The one label every surface prints. One renderer, so surfaces cannot
    disagree with each other about the same fact — which they have, repeatedly.

        unknown                              nothing resolved
        0449bc7 (git)                        from the tree, tracked files clean
        0449bc7-dirty (git)                  from the tree, tracked files modified
        0449bc7 (build stamp, tree unchecked)  from a build arg or the image stamp
        0449bc7 (git refs, tree unchecked)     read from .git without git(1)
    """
    if info is None:
        info = build_info()
    if info.sha is None:
        return UNKNOWN

    label = info.sha[:7]
    if info.dirty is True:
        label += "-dirty"

    # An unrecognised source falls back to its own name: neutral and honest.
    # It must never fall through to a reassuring one — that is the mistake
    # `brain_state` and the entry-gate icon map were each written to prevent.
    note = _SOURCE_TEXT.get(info.source, info.source)
    if info.dirty is None:
        note += ", tree unchecked"
    return f"{label} ({note})"
