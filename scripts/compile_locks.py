#!/usr/bin/env python3
"""Compile the hashed dependency locks, or say whether the committed ones are current.

    python3 scripts/compile_locks.py                     write every lock from its *.in
    python3 scripts/compile_locks.py --check             compile again, compare, write nothing
    python3 scripts/compile_locks.py --installed FILE    compare this interpreter with a lock

THE COMPILE COMMAND LIVES HERE AND NOWHERE ELSE. `make lock`, the CI job and
the header of every generated file all point at this script, so there is one
answer to "how was this file made".

WHY THE INPUTS PIN EVERYTHING. Each `*.in` names every package of its lock at
an exact version, transitives included. pip-compile therefore has nothing to
choose: a release published tomorrow cannot move a pin, and the only things a
fresh compile can change are the hashes. A package the compile needs that no
input pins is refused here, with the line to add, rather than written into a
lock at whatever version was newest that day.

WHY --check COMPILES INTO AN EMPTY DIRECTORY. Given an existing output file,
pip-compile keeps its pins and copies its hashes, so a compile on top of the
committed lock would agree with it whatever PyPI now publishes. The inputs
are copied to a temporary directory and compiled there.

--check answers with three states, not two:

    0  every committed lock is what a fresh compile produces
    1  a lock is stale, and the message says which way (see `Finding`)
    2  COULD NOT CHECK: wrong platform, wrong Python, wrong pip-tools or pip,
       or PyPI could not be reached. Not a pass.

One way to go stale needs no change in this repository: a project uploads
another file to a release that is already pinned. The version did not move;
re-run the compile and commit the longer hash list.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.metadata
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: (human-edited input, generated output), compiled in this order.
LOCKS: Tuple[Tuple[str, str], ...] = (
    ("requirements.in", "requirements.lock"),
    ("requirements-ci.in", "requirements-ci.txt"),
    ("requirements-xdist.in", "requirements-xdist.txt"),
    ("requirements-piptools.in", "requirements-piptools.txt"),
)

#: The input that pins the compiler itself.
TOOLS_INPUT = "requirements-piptools.in"

#: Handed to `python -m piptools compile`. `--no-emit-*` and `--no-config` keep
#: one machine's pip configuration out of a committed file.
COMPILE_FLAGS: Tuple[str, ...] = (
    "--generate-hashes", "--allow-unsafe", "--strip-extras", "--no-header",
    "--no-emit-index-url", "--no-emit-trusted-host", "--no-emit-find-links",
    "--no-config", "--newline", "lf", "--quiet",
)

#: What each generated file is for, printed in its header.
PURPOSE: Dict[str, Tuple[str, ...]] = {
    "requirements.lock": (
        "What `make install`, the deploy docs and the Dockerfile install, and the",
        "file the SCA gate reads (`pip-audit`, .github/workflows/ci.yml). Every",
        "package that ships is named here with its hashes, transitives included.",
    ),
    "requirements-ci.txt": (
        "The CI test environment: everything requirements.lock pins, plus the",
        "test and quality tools. tests/test_ci_env_matches_lock.py requires the",
        "first half.",
    ),
    "requirements-xdist.txt": (
        "pytest-xdist, for the parallel shadow job only. Installed after",
        "requirements-ci.txt; the serial gate's environment does not have it.",
    ),
    "requirements-piptools.txt": (
        "pip-tools, which compiles this file and the other three. Installed by",
        "the lock job in ci.yml and by whoever regenerates a lock.",
    ),
}

OK, STALE, CANNOT_CHECK = 0, 1, 2
#: A mistyped flag. Not 2: that code is taken.
USAGE = 64

#: What pip and pip-compile print when the index could not be reached. A
#: compile that fails for any other reason is a real failure.
_NETWORK_SIGNS = (
    "connectionerror", "connection refused", "connection reset", "connect timeout",
    "read timed out", "readtimeout", "max retries exceeded", "proxyerror",
    "temporary failure in name resolution", "name or service not known",
    "could not fetch url", "network is unreachable", "sslerror", "tunnel connection failed",
)


def canon(name: str) -> str:
    """PEP 503: `Typing_Extensions`, `typing.extensions` and `typing-extensions` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


class Pin(NamedTuple):
    """One requirement of a generated lock."""
    version: str
    hashes: frozenset
    marker: str          # "" when the requirement is unconditional
    via: Tuple[str, ...]  # who requires it, as pip-compile annotated it


_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;\\]+)\s*(?:;\s*(.*?))?\s*\\?\s*$")
_HASH = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


def parse_lock(text: str) -> Dict[str, Pin]:
    """The pins of a pip-compile `--generate-hashes` file, by canonical name.

    A requirement is a `name==version` line, then indented `--hash=` lines,
    then an indented `# via` comment. Anything else at column 0 (the header,
    pip-compile's "unsafe packages" banner) belongs to no requirement.
    """
    pins: Dict[str, Pin] = {}
    name: Optional[str] = None
    version, marker = "", ""
    hashes: Set[str] = set()
    via: List[str] = []

    def close() -> None:
        if name is not None:
            pins[name] = Pin(version, frozenset(hashes), marker, tuple(via))

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.startswith((" ", "\t")):
            close()
            name, version, marker, hashes, via = None, "", "", set(), []
            found = _REQ.match(line)
            if found:
                name, version, marker = canon(found.group(1)), found.group(2), (found.group(3) or "").strip()
            continue
        if name is None:
            continue
        body = line.strip()
        hashes.update(_HASH.findall(body))
        if body.startswith("#"):
            note = body.lstrip("#").strip()
            if note.startswith("via"):
                note = note[len("via"):].strip()
            if note:
                via.extend(part.strip() for part in note.split(",") if part.strip())
    close()
    return pins


def parse_input(name: str, root: Path = ROOT) -> Dict[str, str]:
    """Every `==` pin a `*.in` file states, following its `-r` includes."""
    pins: Dict[str, str] = {}
    for raw in (root / name).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        include = re.match(r"^-r\s+(\S+)$", line)
        if include:
            pins.update(parse_input(include.group(1), root))
            continue
        found = _REQ.match(line)
        if found:
            pins[canon(found.group(1))] = found.group(2)
    return pins


class Finding(NamedTuple):
    """One way a committed lock differs from a fresh compile.

    `kind` is one of:
      unpinned         the compile needs a package no input pins
      pin              a version, or the set of packages, differs
      hash-withdrawn   a committed hash is no longer published for that version
      hash-added       more files are published for an unchanged version
      text             same pins and hashes, different text (header, annotation)
    """
    lock: str
    kind: str
    detail: str


def unpinned(compiled: Dict[str, Pin], declared: Dict[str, str]) -> List[Tuple[str, str]]:
    """(name, version) of every compiled package the input does not pin."""
    return sorted((name, pin.version) for name, pin in compiled.items() if name not in declared)


def compare(lock: str, committed_text: str, fresh_text: str, declared: Dict[str, str]) -> List[Finding]:
    """Every way `committed_text` differs from `fresh_text`, most serious first."""
    committed, fresh = parse_lock(committed_text), parse_lock(fresh_text)
    out: List[Finding] = []
    for name, version in unpinned(fresh, declared):
        out.append(Finding(lock, "unpinned", f"add `{name}=={version}` to the input: the compile needs it "
                                             "and nothing pins it"))
    for name in sorted(set(committed) | set(fresh)):
        old, new = committed.get(name), fresh.get(name)
        if old is None and new is not None:
            out.append(Finding(lock, "pin", f"{name}=={new.version} is compiled and not committed"))
        elif new is None and old is not None:
            out.append(Finding(lock, "pin", f"{name}=={old.version} is committed and no longer compiled"))
        elif old is not None and new is not None and old.version != new.version:
            out.append(Finding(lock, "pin", f"{name}: {old.version} committed, {new.version} compiled"))
        elif old is not None and new is not None:
            gone, added = old.hashes - new.hashes, new.hashes - old.hashes
            if gone:
                out.append(Finding(lock, "hash-withdrawn", f"{name}=={old.version}: {len(gone)} committed "
                                                           "hash(es) are not published for that version"))
            if added:
                out.append(Finding(lock, "hash-added", f"{name}=={old.version}: {len(added)} more file(s) "
                                                       "published for the same version"))
    if not out and committed_text != fresh_text:
        diff = "".join(difflib.unified_diff(
            committed_text.splitlines(keepends=True), fresh_text.splitlines(keepends=True),
            f"{lock} (committed)", f"{lock} (compiled)", n=1))
        out.append(Finding(lock, "text", "same pins and hashes, different text:\n" + diff))
    return out


def header(source: str, target: str) -> str:
    lines = [
        f"GENERATED by scripts/compile_locks.py from {source}. Do not edit.",
        "",
        f"To change a pin, edit {source} and run:",
        "    python3 scripts/compile_locks.py",
        "CI compiles again and fails when this file differs",
        '(.github/workflows/ci.yml, "The committed lock is the compiled lock").',
        "",
        f"Install on Python {wanted_python()}. pip refuses a file whose hash does not match, and",
        "refuses a package this file does not name:",
        f"    pip install --require-hashes -r {target}",
        "",
        *PURPOSE[target],
    ]
    return "".join(f"# {line}".rstrip() + "\n" for line in lines)


def wanted_python(root: Path = ROOT) -> str:
    return (root / ".python-version").read_text(encoding="utf-8").strip()


def environment_refusal(root: Path = ROOT) -> Optional[str]:
    """Why this interpreter cannot produce the committed files, or None.

    pip-compile resolves for the interpreter and platform it runs on, and its
    output is the output of one pip-tools and one pip. Anything else produces
    a different file, and a difference from that says nothing about the lock.
    """
    if not sys.platform.startswith("linux"):
        return f"the locks are compiled on Linux, and this is {sys.platform}"
    want = wanted_python(root)
    have = "%d.%d" % sys.version_info[:2]
    if have != want:
        return f"the locks are compiled on Python {want} (.python-version), and this is {have}"
    pinned = parse_input(TOOLS_INPUT, root)
    for dist in ("pip-tools", "pip"):
        expected = pinned.get(dist)
        if expected is None:
            return f"{TOOLS_INPUT} does not pin {dist}"
        try:
            running = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            return (f"{dist} is not installed in this interpreter. Build the tools environment "
                    f"described in {TOOLS_INPUT} and run this script with its python")
        if running != expected:
            return f"{dist} {running} is running and {TOOLS_INPUT} pins {expected}"
    return None


class CompileFailure(Exception):
    def __init__(self, source: str, output: str) -> None:
        super().__init__(f"pip-compile failed on {source}")
        self.source, self.output = source, output

    @property
    def network(self) -> bool:
        low = self.output.lower()
        return any(sign in low for sign in _NETWORK_SIGNS)


def compile_all(root: Path = ROOT) -> Dict[str, str]:
    """{output name: full text it should have}, compiled into an empty directory."""
    work = Path(tempfile.mkdtemp(prefix="runeclaw-locks-"))
    try:
        for source, _ in LOCKS:
            shutil.copy2(root / source, work / source)
        out: Dict[str, str] = {}
        for source, target in LOCKS:
            run = subprocess.run(
                [sys.executable, "-m", "piptools", "compile", *COMPILE_FLAGS,
                 "--output-file", target, source],
                cwd=work, capture_output=True, text=True)
            if run.returncode != 0:
                raise CompileFailure(source, run.stdout + run.stderr)
            out[target] = header(source, target) + (work / target).read_text(encoding="utf-8")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _compiled_or_exit_code(root: Path) -> Tuple[Optional[Dict[str, str]], int]:
    refusal = environment_refusal(root)
    if refusal is not None:
        print(f"COULD NOT CHECK: {refusal}.", file=sys.stderr)
        return None, CANNOT_CHECK
    try:
        return compile_all(root), OK
    except CompileFailure as failure:
        print(failure.output, file=sys.stderr)
        if failure.network:
            print(f"COULD NOT CHECK: the package index could not be reached while compiling "
                  f"{failure.source}.", file=sys.stderr)
            return None, CANNOT_CHECK
        print(f"pip-compile refused {failure.source}. Nothing was written.", file=sys.stderr)
        return None, STALE


_ADVICE = {
    "unpinned": "Pin it in the input, then run this script without --check.",
    "pin": "Run `python3 scripts/compile_locks.py` and commit the result, or undo the hand edit.",
    "hash-withdrawn": "A file the lock trusts is gone from the index. Find out why before recompiling.",
    "hash-added": "No version moved. Run `python3 scripts/compile_locks.py` and commit the longer hash list.",
    "text": "Run `python3 scripts/compile_locks.py` and commit the result.",
}


def check(root: Path = ROOT) -> int:
    compiled, code = _compiled_or_exit_code(root)
    if compiled is None:
        return code
    findings: List[Finding] = []
    for source, target in LOCKS:
        path = root / target
        committed = path.read_text(encoding="utf-8") if path.exists() else ""
        findings.extend(compare(target, committed, compiled[target], parse_input(source, root)))
    if not findings:
        print(f"{len(LOCKS)} locks are what a fresh compile produces.")
        return OK
    for finding in findings:
        print(f"STALE {finding.lock} [{finding.kind}] {finding.detail}")
    for kind in sorted({finding.kind for finding in findings}):
        print(f"  [{kind}] {_ADVICE[kind]}")
    return STALE


def write(root: Path = ROOT) -> int:
    compiled, code = _compiled_or_exit_code(root)
    if compiled is None:
        return code
    missing = [(target, name, version)
               for source, target in LOCKS
               for name, version in unpinned(parse_lock(compiled[target]), parse_input(source, root))]
    if missing:
        for target, name, version in missing:
            print(f"{target}: the compile needs {name}=={version} and its input does not pin it",
                  file=sys.stderr)
        print("Nothing was written. Pin each one in the input and run this again.", file=sys.stderr)
        return STALE
    for _, target in LOCKS:
        (root / target).write_text(compiled[target], encoding="utf-8")
        print(f"wrote {target}: {len(parse_lock(compiled[target]))} pins")
    return OK


def installed_versions() -> Dict[str, List[str]]:
    """{canonical name: every version this interpreter has metadata for}.

    A list, because an environment assembled by copying one site-packages over
    another can hold two versions of one distribution, and reporting either
    one alone would be reporting half of it.
    """
    found: Dict[str, List[str]] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        versions = found.setdefault(canon(name), [])
        if dist.version not in versions:
            versions.append(dist.version)
    return found


def differences(pins: Dict[str, Pin], have: Dict[str, List[str]]) -> Tuple[List[str], List[str], List[str]]:
    """(wrong, conditional, unlisted) as printable lines.

    `wrong` is a pin this interpreter does not satisfy, or any distribution
    present in two versions, pinned or not: that environment was assembled
    from two installs and holds files of both. `conditional` is a pin with an
    environment marker that is not installed, which a marker may explain and
    this function does not evaluate. `unlisted` is installed, once, and not in
    the lock.
    """
    wrong: List[str] = []
    conditional: List[str] = []
    for name, pin in sorted(pins.items()):
        versions = have.get(name, [])
        if versions == [pin.version]:
            continue
        if not versions and pin.marker:
            conditional.append(f"{name}=={pin.version} is not installed ({pin.marker})")
        elif not versions:
            wrong.append(f"{name}: the lock pins {pin.version}, nothing is installed")
        else:
            wrong.append(f"{name}: the lock pins {pin.version}, installed {' and '.join(versions)}")
    unlisted: List[str] = []
    for name, versions in sorted(have.items()):
        if name in pins:
            continue
        if len(versions) > 1:
            wrong.append(f"{name}: installed {' and '.join(versions)}, two versions in one environment")
        else:
            unlisted.append(f"{name} {versions[0]}")
    return wrong, conditional, unlisted


def installed(lock: str) -> int:
    path = Path(lock)
    if not path.exists():
        print(f"COULD NOT CHECK: {lock} does not exist.", file=sys.stderr)
        return CANNOT_CHECK
    pins = parse_lock(path.read_text(encoding="utf-8"))
    if not pins:
        print(f"COULD NOT CHECK: {lock} holds no pinned requirement.", file=sys.stderr)
        return CANNOT_CHECK
    wrong, conditional, unlisted = differences(pins, installed_versions())
    print(f"{sys.executable} against {lock} ({len(pins)} pins). Nothing was installed or changed.")
    for line in wrong:
        print(f"  DIFFERS  {line}")
    for line in conditional:
        print(f"  skipped  {line}")
    for line in unlisted:
        print(f"  unlisted {line}")
    if wrong:
        print(f"{len(wrong)} pin(s) differ from this environment.")
        return STALE
    print("Every unconditional pin is installed at the pinned version.")
    return OK


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which here means "could not check"."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(USAGE, f"{self.prog}: error: {message}\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _Parser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="compile again and compare with the committed locks; write nothing")
    mode.add_argument("--installed", metavar="FILE",
                      help="compare this interpreter's packages with a lock; install nothing")
    args = parser.parse_args(argv)
    if args.installed:
        return installed(args.installed)
    return check() if args.check else write()


if __name__ == "__main__":
    sys.exit(main())
