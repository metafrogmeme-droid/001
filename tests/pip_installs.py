"""Every `pip install` a build file runs, and whether pip checks what it downloads.

A helper, not a test file. Two tests read it
(`test_the_lock_is_hash_pinned.py`, `test_dependencies_are_pinned.py`), so
there is one answer to "is this install hashed".

An install is one of three things:

    wheel      the official pip wheel, by URL, with its sha256 in the URL.
               pip checks that hash itself.
    locked     `--require-hashes -r <a generated lock>`, and nothing else on
               the line. pip checks every file against the lock and refuses a
               package the lock does not name. A requirement added to that
               line would be the one thing on it with no hash, so a line that
               names one is not `locked`.
    unhashed   anything else.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import compile_locks  # noqa: E402

#: The files pip may be pointed at under --require-hashes: the ones the
#: compile script writes, read from the script.
HASHED = tuple(target for _, target in compile_locks.LOCKS)

_PIP_INSTALL = re.compile(r"\bpip3?\s+install\b(.*)")
_WHEEL = re.compile(
    r'^"?pip @ https://files\.pythonhosted\.org/\S+/pip-([0-9][0-9.]*)-py3-none-any\.whl'
    r'#sha256=([0-9a-f]{64})"?$')
_FLAG = r"(?:^|\s)(-{1,2}[A-Za-z][\w-]*(?:=\S+)?)"
_FILE = r"(?:^|\s)-r\s+(\S+)"


def build_files() -> list[str]:
    """The files that build an environment: every workflow, the GitLab
    pipeline, the Makefile and the Dockerfile."""
    found = sorted(str(p.relative_to(ROOT)) for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    found += [name for name in (".gitlab-ci.yml", "Makefile", "Dockerfile") if (ROOT / name).exists()]
    return found


def invocations(text: str) -> list[str]:
    """The arguments of every `pip install` in `text`.

    Whole-line comments are dropped first, because a comment that quotes the
    command it explains is indistinguishable from the command. A `#` inside a
    line is left alone: the pip wheel's URL carries its hash after one.
    Backslash continuations are joined.
    """
    kept = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    out = []
    for line in re.sub(r"\\\n", " ", "\n".join(kept)).splitlines():
        found = _PIP_INSTALL.search(line)
        if found:
            out.append(" ".join(found.group(1).split()))
    return out


def classify(arguments: str) -> str:
    """`wheel`, `locked` or `unhashed` for one install's arguments."""
    files = re.findall(_FILE, arguments)
    rest = re.sub(_FILE, " ", arguments)
    flags = re.findall(_FLAG, rest)
    rest = re.sub(_FLAG, " ", rest).strip()
    if not files and _WHEEL.match(rest):
        return "wheel"
    if files and not rest and "--require-hashes" in flags and all(name in HASHED for name in files):
        return "locked"
    return "unhashed"


def installs(rel: str) -> list[tuple[str, str]]:
    """(arguments, classification) for every `pip install` in one file."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    return [(arguments, classify(arguments)) for arguments in invocations(text)]


def wheels(rel: str) -> list[tuple[str, str]]:
    """(pip version, sha256) of every pip wheel URL a file installs."""
    out = []
    for arguments, kind in installs(rel):
        if kind == "wheel":
            found = _WHEEL.match(re.sub(_FLAG, " ", arguments).strip())
            assert found is not None
            out.append((found.group(1), found.group(2)))
    return out
