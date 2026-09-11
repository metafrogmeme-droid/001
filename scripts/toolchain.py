"""One reading of "is this analyser the one the baselines were recorded with".

``ruff_gate`` and ``mypy_gate`` each carried a byte-identical copy of `_pinned`
and `_running`, and this slice needed the same reading a THIRD time — in
`preflight.py`, so it can say up front that the whole-tree ratchets cannot check
on this box rather than leaving each to discover it. A second copy of a reading
is a second answer; a third is how a convention that holds twice stops being
checked. This is the reading.

WHY THE READING MATTERS AT ALL is written out in `ruff_gate.check_version`: a
baseline recorded under mypy 1.19.1 and checked under 1.15.0 named eleven grown
classes and not one was a code change. A count is only comparable to a count
from the same tool version.

WHAT THIS MODULE DOES NOT DO is decide what to do about a mismatch. The gates
refuse (exit 2); the preflight names the condition and carries on so one stale
analyser does not hide the state of everything else. Same fact, two policies,
and keeping the policy out of here is what lets both hold it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 — runs `<tool> --version`, no shell, fixed argv
import sys
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements-ci.txt"

#: The analysers whose baselines are only comparable within one version. Read
#: by the preflight to report a mismatch BEFORE the gates run. Adding a tool
#: here without it calling `check_version` would overstate the check, so
#: `tests/test_preflight_names_what_it_could_not_check.py` pins the two lists
#: against each other.
VERSION_PINNED_TOOLS = ("ruff", "mypy")


class Comparability(NamedTuple):
    """Whether counts from this tool can be compared to the recorded baseline.

    Three values, and the third is the point: `unknown` is not `comparable`.
    A version that could not be read is an absent measurement, and answering
    "fine" for it would be this repo's whole defect in the one module written
    to prevent it.
    """
    tool: str
    pinned: Optional[str]
    running: Optional[str]
    #: Which binary `running` actually asked, and where a correctly-pinned copy
    #: sits if one exists further down PATH. Both are None when unknown.
    resolved: Optional[str] = None
    shadowed_pinned: Optional[str] = None

    @property
    def comparable(self) -> bool:
        """True only when both versions were read AND they match."""
        return (self.pinned is not None
                and self.running is not None
                and self.pinned == self.running)

    @property
    def unknown(self) -> bool:
        """A version nobody could read — neither a match nor a mismatch."""
        return self.pinned is None or self.running is None

    def describe(self) -> str:
        if self.unknown:
            return (f"{self.tool}: could not determine the version to compare "
                    f"(requirements-ci.txt={self.pinned}, running={self.running})")
        if self.comparable:
            return f"{self.tool} {self.running} matches requirements-ci.txt"
        where = f" ({self.resolved})" if self.resolved else ""
        line = (f"{self.tool}: requirements-ci.txt pins {self.pinned}, "
                f"this is {self.running}{where}")
        if self.shadowed_pinned:
            # The case that cost an hour: `pip install ruff==0.11.13` reported
            # success and the version did not move, because the pinned binary
            # was already installed and a different one came first on PATH.
            # "Install it" is the wrong instruction when it is already there.
            line += (f"\n      the pinned {self.pinned} IS installed at "
                     f"{self.shadowed_pinned} — it is SHADOWED on PATH, not "
                     f"missing. Put its directory first rather than "
                     f"reinstalling.")
        return line


def pinned(tool: str) -> Optional[str]:
    """The version CI installs, read from requirements-ci.txt."""
    if not REQUIREMENTS.exists():
        return None
    m = re.search(rf"^{re.escape(tool)}==([0-9][^\s#]*)",
                  REQUIREMENTS.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def running(tool: str) -> Optional[str]:
    """The version that would actually run, or None if it cannot be asked.

    A tool that is not installed, or whose `--version` says nothing a version
    regex recognises, answers None rather than a string: absent is not a
    measurement here either.
    """
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            [tool, "--version"], capture_output=True, text=True, cwd=ROOT)
    except (OSError, ValueError):
        return None
    m = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)",
                  (proc.stdout or "") + (proc.stderr or ""))
    return m.group(1) if m else None


def _pinned_copy_elsewhere(tool: str, want: Optional[str],
                           resolved: Optional[str]) -> Optional[str]:
    """A binary further down PATH whose version IS the pinned one.

    Answers None when there is no such copy — including when it cannot be
    determined — because "no shadowed copy found" and "did not look" lead to
    the same instruction here, and inventing one would send an operator to the
    wrong fix.
    """
    if not want:
        return None
    seen = set()
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry or entry in seen:
            continue
        seen.add(entry)
        cand = shutil.which(tool, path=entry)
        # `cand == resolved` is a short-circuit, not a rule: this is only
        # reached when the running version already differs from the pinned one,
        # so the running binary would fail the version test below anyway.
        # Removing it survives the mutation round for that reason — an
        # equivalent mutation, recorded rather than chased. It saves one
        # subprocess per PATH entry that resolves to the binary we just asked.
        if not cand or cand == resolved:
            continue
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell
                [cand, "--version"], capture_output=True, text=True, cwd=ROOT)
        except (OSError, ValueError):
            continue
        m = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)",
                      (proc.stdout or "") + (proc.stderr or ""))
        if m and m.group(1) == want:
            return cand
    return None


def comparability(tool: str) -> Comparability:
    want, have = pinned(tool), running(tool)
    resolved = shutil.which(tool)
    shadowed = (None if (want is None or have is None or want == have)
                else _pinned_copy_elsewhere(tool, want, resolved))
    return Comparability(tool, want, have, resolved, shadowed)


def install_hint(rows: list[Comparability]) -> str:
    """The command that makes the counts comparable again.

    Quoted per-spec because a bare `ruff==0.11.13` is a shell redirect waiting
    to happen, and printed rather than run: a preflight that installs a
    toolchain behind your back is not a preflight — the same rule that keeps
    token tooling out of LOCAL_JOBS.
    """
    need_install = [r for r in rows if r.pinned and not r.shadowed_pinned]
    specs = " ".join(f"'{r.tool}=={r.pinned}'" for r in need_install)
    return f"python3 -m pip install {specs}" if specs else ""


def require_comparable(tool: str) -> None:
    """Refuse to compare counts produced by a different analyser.

    THE FIRST CI RUN OF THE MYPY GATE FAILED HERE, AND THE FAILURE WAS A LIE.
    The baseline was recorded with mypy 1.19.1; CI pins 1.15.0. It reported 654
    errors against a baseline of 390 and named eleven classes as having grown --
    union-attr 44 -> 195, no-any-return 58 -> 105, plus a `list-item` class that
    does not exist in the other version at all. Not one of those was a code
    change. Every number came from a different analyser looking at an identical
    tree.

    A count is only comparable to a count from the same tool version, so a
    mismatch is NOT a verdict -- it is the absence of one, and this repo has a
    rule about reporting those as verdicts. Exit 2, distinct from the 1 that
    means "something really did grow", so a launcher reading truthiness still
    fails closed while a human reading the message learns which of the two
    happened -- and `scripts/preflight.py` is that launcher, reading the 2.

    `ruff_gate` and `mypy_gate` held byte-identical copies of this. They call it.
    """
    c = comparability(tool)
    if c.unknown:
        print(f"WARNING: could not determine the {tool} version to compare "
              f"(pinned={c.pinned}, running={c.running}); counts may not be "
              f"comparable")
        return
    if c.comparable:
        return
    print(f"CANNOT CHECK: baseline counts were recorded with {tool} {c.pinned} "
          f"(the version requirements-ci.txt installs) and this is {tool} "
          f"{c.running}.", file=sys.stderr)
    print(f"  Different {tool} versions report different counts on identical "
          f"code, so any growth or shrinkage reported here would be an "
          f"artefact of the toolchain rather than a fact about the tree.",
          file=sys.stderr)
    if c.shadowed_pinned:
        # "Install the pinned version" was the old advice and it is WRONG here:
        # the pinned build is already installed, so a pip install reports
        # success and changes nothing. That round trip is why this branch exists.
        print(f"  The pinned {tool} {c.pinned} IS installed, at "
              f"{c.shadowed_pinned}. {c.resolved} comes first on PATH and is "
              f"what ran. Put the pinned one's directory first; reinstalling "
              f"will not move it.", file=sys.stderr)
    else:
        print(f"  Install the pinned version ({install_hint([c])}), or "
              f"re-record deliberately with --update once requirements-ci.txt "
              f"moves.", file=sys.stderr)
    raise SystemExit(2)
