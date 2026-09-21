"""Which default a comment CLAIMS, and which default the flag HAS.

CLAUDE.md records this shape for the Guardian firewall: *"The comment over that
scan named the wrong half as off ... A comment that misdescribes which half of a
security gate is disabled is how the gate goes unexamined."* That instance was
written down and the comment was never fixed, so the lesson sat in the document
while the misleading line sat in the code. This is the reading that closes it.

TWO CLAIM SITES, and they need different rules.

  DECLARATION — the comment block directly above `x: bool = _env_bool("X", D)`
  in `bot/config.py`. The block is about that declaration, so the pairing is
  certain, and a block that claims BOTH defaults is its own finding.

  READER — a comment above a USE of the flag, anywhere in the tree. The pairing
  is a guess, and the first draft of this reading guessed wrong.

THE BLIND SPOT, FOUND IN THE INSTRUMENT ON ITS FIRST RUN. The reader rule first
paired each comment with the nearest `CONFIG.<section>.<attr>` below it. That
MANUFACTURED FOUR FALSE ACCUSATIONS — `backtest/engine.py` twice and
`order_flow.py` twice — where the comment says "Own env flag, default OFF" about
a LOCAL `_env_bool(..., False)` sitting right there, and the `CONFIG.` reference
on the next line is a different flag entirely. *A checker with a blind spot
manufactures exactly the accusation it exists to prevent.* A local `_env_bool`
in the window therefore WINS: the comment is about the flag being declared
beside it, not about one merely mentioned nearby. That correction also found one
the first draft MISSED (`chart_patterns.py`), because a local declaration is not
a `CONFIG.` reference at all.

WHAT IT DELIBERATELY DOES NOT CLAIM. A comment with neither a local `_env_bool`
nor a resolvable `CONFIG.` attribute in its window is not a finding — the flag
it means cannot be established, and naming one would be the guess this reading
was corrected for. A comment that names flag A while flag B is nearer is also
out of reach; that needs a reader who knows what the comment MEANT.
"""

from __future__ import annotations

import pathlib
import re
from typing import NamedTuple, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PY = ROOT / "bot" / "config.py"

#: How far below a comment its flag may sit. A comment block plus the statement
#: it introduces; beyond that the association is not a reading.
WINDOW = 13

#: A claim about the DEFAULT, not a description of a STATE. The word "default"
#: (or "by default") must be there. A bare "opt-in" was the first trigger and it
#: MANUFACTURED AN ACCUSATION against `LIVE_OPEN_TO_KEY_HOLDERS`, whose comment
#: reads "OFF restores the staged rollout (opt-in allowlist)" — that sentence
#: says what the OFF state DOES and claims nothing about which state ships.
#: Every real finding in the tree also spells "default OFF", so requiring the
#: word costs no coverage and removes a whole class of false accusation.
_OFF = re.compile(r"(default:?\s*off|defaults?\s+off|off by default|"
                  r"disabled by default)", re.I)
_ON = re.compile(r"(default:?\s*on|defaults?\s+on|on by default|"
                 r"enabled by default)", re.I)
_LOCAL = re.compile(r'_env_bool\(\s*"([A-Z0-9_]+)"\s*,\s*(True|False)\s*\)')
_DECL = re.compile(r'(\w+)\s*:\s*bool\s*=\s*_env_bool\(\s*"([A-Z0-9_]+)"\s*,'
                   r'\s*(True|False)\s*\)')
_CONFIG_REF = re.compile(r"CONFIG\.(\w+)\.(\w+)")


class Finding(NamedTuple):
    path: str
    line: int          # 1-indexed
    env: str           # the env var the flag reads
    claimed: str       # "on" | "off" | "both"
    actual: bool       # the declared default
    how: str           # which rule resolved the flag
    text: str          # the comment, for the message

    @property
    def key(self) -> str:
        """Stable across line moves: a baseline keyed by line churns."""
        return f"{self.path}::{self.env}::{self.claimed}"


def declared_defaults(src: Optional[str] = None) -> dict[str, tuple[str, bool]]:
    """`attr -> (ENV_NAME, default)` for every bool flag `bot/config.py` declares."""
    text = CONFIG_PY.read_text() if src is None else src
    return {m.group(1): (m.group(2), m.group(3) == "True")
            for m in _DECL.finditer(text)}


def _claim(text: str) -> Optional[str]:
    off, on = bool(_OFF.search(text)), bool(_ON.search(text))
    if off and on:
        return "both"
    if off:
        return "off"
    if on:
        return "on"
    return None


def declaration_findings(src: Optional[str] = None,
                         path: str = "bot/config.py") -> list[Finding]:
    """Comment blocks in `bot/config.py` that misdescribe the line below them."""
    text = CONFIG_PY.read_text() if src is None else src
    lines = text.splitlines()
    out: list[Finding] = []
    for i, line in enumerate(lines):
        m = _DECL.search(line)
        if not m:
            continue
        env, actual = m.group(2), m.group(3) == "True"
        j, block = i - 1, []
        while j >= 0 and lines[j].strip().startswith("#"):
            block.append(lines[j].strip("# ").strip())
            j -= 1
        if not block:
            continue
        joined = " ".join(reversed(block))
        claim = _claim(joined)
        if claim is None:
            continue
        # "both" is a finding whatever the flag is: one block, two answers.
        if claim == "both" or (claim == "off") is not (not actual):
            out.append(Finding(path, i + 1, env, claim, actual,
                               "declaration", joined[:160]))
    return out


def _resolve(window: str, decl: dict[str, tuple[str, bool]]
             ) -> Optional[tuple[str, bool, str]]:
    """The flag a comment is about: a LOCAL declaration first, then a CONFIG read.

    The order is the whole correction. A local `_env_bool` IS the flag being
    described; a `CONFIG.` reference is only the nearest one mentioned.
    """
    local = _LOCAL.search(window)
    if local:
        return local.group(1), local.group(2) == "True", "local _env_bool"
    for m in _CONFIG_REF.finditer(window):
        got = decl.get(m.group(2))
        if got:
            return got[0], got[1], "CONFIG attr"
    return None


def reader_sources() -> dict[str, str]:
    """Every Python file the reader rule walks, by repo-relative path.

    Split out so the WALK is assertable. *A scan that matches nothing passes
    every assertion while checking none* — the sentence
    `test_flag_prose_matches_default` opened with, and the reason it carried
    `test_the_scan_actually_reaches_the_flags`. With the tree at zero, a
    narrowing of this walk changes no verdict and would survive the round
    silently; the floor and the named files are what make it die.
    """
    out: dict[str, str] = {}
    for base in ("bot", "scripts"):
        for p in sorted((ROOT / base).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel == "bot/config.py":
                continue              # its own declarations are the other rule
            try:
                out[rel] = p.read_text()
            except OSError:
                continue
    return out


def reader_findings(files: Optional[dict[str, str]] = None,
                    decl: Optional[dict[str, tuple[str, bool]]] = None
                    ) -> list[Finding]:
    """Comments above a USE of a flag that name a default the flag does not have."""
    decl = declared_defaults() if decl is None else decl
    if files is None:
        files = reader_sources()
    out: list[Finding] = []
    for rel, text in files.items():
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            if not lines[i].strip().startswith("#"):
                i += 1
                continue
            # THE WHOLE CONTIGUOUS BLOCK, not the line. The claim that started
            # `test_flag_prose_matches_default` was WRAPPED — "(opt-in, default"
            # ended one line and "OFF; deep-audit medium)" began the next — so a
            # per-line match saw neither half. That guard joins its block for
            # exactly this reason; reading it is what found the same gap here.
            start = i
            while i < len(lines) and lines[i].strip().startswith("#"):
                i += 1
            block = " ".join(ln.strip().lstrip("#").strip()
                             for ln in lines[start:i])
            claim = _claim(block)
            if claim is None:
                continue
            # The window is measured from the END of the block, where the flag
            # is — not from its start. Measured from the start, an 18-line
            # explanation pushes its own `CONFIG.` reference out of reach and
            # the rule goes SILENT on exactly the longest comments: the Kelly
            # block is 18 lines and the mutation restoring its false claim
            # survived a whole round because of it.
            got = _resolve("\n".join(lines[i:i + WINDOW]), decl)
            if got is None:
                continue              # no flag in reach: not a reading
            env, actual, how = got
            if claim == "both" or (claim == "off") is not (not actual):
                out.append(Finding(rel, start + 1, env, claim, actual, how,
                                   block[:120]))
    return out


def all_findings() -> list[Finding]:
    return sorted(declaration_findings() + reader_findings(),
                  key=lambda f: (f.path, f.line))


def describe(f: Finding) -> str:
    says = {"off": "OFF", "on": "ON", "both": "both ON and OFF"}[f.claimed]
    return (f"{f.path}:{f.line}  [{f.how}]  {f.env}\n"
            f"      comment says {says}; the flag is declared "
            f"{'True' if f.actual else 'False'}\n"
            f"      {f.text}")
