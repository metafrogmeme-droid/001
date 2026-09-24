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

import ast
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
#: The same read without a section. Tried after the sectioned one — see
#: `_resolve`, where the ORDER is load-bearing.
_CONFIG_FLAT = re.compile(r"CONFIG\.(\w+)(?!\s*\.)")


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


# A RETRACTION HAS TO NAME THE SENTENCE IT CORRECTS, and nobody writes a LIVE
# claim inside quotation marks. So a quoted span is not a claim -- which is
# the generic form of the occurrence-counting `test_flag_prose_matches_default`
# already does for one literal, and of `_unquoted` in the MCP doc guard.
#
# POST-JOIN IS THE WHOLE MECHANISM. `"[^"\n]*"` refuses newlines, and the
# retraction this rule was widened onto wraps its quote across two comment
# lines (`... is "disabled by default (threshold` / `1.0)" ...`). Applied to
# the raw lines the strip changes nothing and the block still claims "off";
# applied after the block is joined it acquits exactly. Reusing the sibling
# helper at the wrong point in the pipeline would ship a fix that fixes
# nothing, and a green tree would not say so.
_QUOTED = re.compile(r'"[^"\n]*"')


def _claim(text: str) -> Optional[str]:
    text = _QUOTED.sub(" ", text)
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
    # FLAT ATTRIBUTES, TRIED LAST. `_CONFIG_REF` demands a SECTIONED two-dot
    # reference, and a body of flags is read flat -- `CONFIG.auto_confirm_
    # live_enabled` has no section -- so those comments resolved to nothing
    # and their claims were dropped in silence. That narrowness was written
    # down NOWHERE: the module's own account of what it deliberately does not
    # claim lists five limits and this is not among them, which is why it
    # survived.
    #
    # LAST is not cosmetic. A flat pattern tried first reads
    # `CONFIG.risk.flag_a` as the section name `risk` and loses every
    # sectioned resolution, and `TestTheTwoFalseAccusationsItAlreadyMade`
    # pins that a local `_env_bool` wins ahead of both.
    for m in _CONFIG_FLAT.finditer(window):
        got = decl.get(m.group(1))
        if got:
            return got[0], got[1], "CONFIG flat attr"
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


ENV_EXAMPLE = ROOT / ".env.example"

#: An assignment line in `.env.example`, commented (`# X=false`) or live
#: (`X=false`). It ENDS a prose block rather than joining it: the file's
#: examples are `#` lines too, so the Python rule's "every contiguous `#` line"
#: would read an example value as prose and pair the block with nothing.
_ENV_ASSIGN = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=(\S*)")

#: What a LIVE example line does, said in the block above it. `cp .env.example
#: .env` is the documented install, so a live line that sets a flag opposite to
#: its declared default is the value that install RUNS, and a block that only
#: names the default describes a different install.
_EXAMPLE_SAYS = {
    False: re.compile(r"lines?\s+below\s+(sets?\s+it\s+off|disables?\s+it)", re.I),
    True: re.compile(r"lines?\s+below\s+(sets?\s+it\s+on|enables?\s+it)", re.I),
}
_TRUE_WORDS = {"true", "1", "yes", "on"}
_FALSE_WORDS = {"false", "0", "no", "off"}


class EnvBlock(NamedTuple):
    line: int                      # 1-indexed first line of the prose block
    block: str                     # the prose, joined
    run: list[tuple[str, int, Optional[bool]]]
    # each assignment below it: (ENV, line, value a LIVE line sets or None)


def env_example_blocks(src: Optional[str] = None) -> list[EnvBlock]:
    """Every prose block in `.env.example` with the assignment run below it."""
    text = ENV_EXAMPLE.read_text() if src is None else src
    lines = text.splitlines()
    out: list[EnvBlock] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        # A run with NO prose above it is still read, with empty prose: an
        # override there is the silent case at its plainest, and a walk that
        # starts only at a `#` line never visits it.
        if not s.startswith("#") and not _ENV_ASSIGN.match(s):
            i += 1
            continue
        start = i
        while (i < len(lines) and lines[i].strip().startswith("#")
               and not _ENV_ASSIGN.match(lines[i].strip())):
            i += 1
        block = " ".join(ln.strip().lstrip("#").strip() for ln in lines[start:i])
        run: list[tuple[str, int, Optional[bool]]] = []
        while i < len(lines):
            raw = lines[i].strip()
            m = _ENV_ASSIGN.match(raw)
            if not m:
                break
            val: Optional[bool] = None
            if not raw.startswith("#"):
                word = m.group(2).lower()
                val = True if word in _TRUE_WORDS else False if word in _FALSE_WORDS else None
            run.append((m.group(1), i + 1, val))
            i += 1
        out.append(EnvBlock(start + 1, block, run))
    return out


def env_example_findings(src: Optional[str] = None,
                         decl: Optional[dict[str, tuple[str, bool]]] = None
                         ) -> list[Finding]:
    """Prose in `.env.example` that names a default its flag does not have.

    THE THIRD CLAIM SITE, and the one an operator reads. The two rules above
    walk Python; this file is where somebody deciding whether a live control
    is running looks first, and on 2026-09-23 twelve of its blocks said OFF
    over a flag that ships ON — the live auto-close, the live-performance
    governor, correlation sizing, live risk hardening and the regime hard
    gates among them. Each was flipped to default ON in 2026-07 (the runbook's
    stage table says so) and the prose above its example line never moved.

    The pairing is CERTAIN or it is not made. A prose block pairs with the run
    of assignment lines directly below it, and only when that run holds
    exactly ONE declared bool flag: a block over two flags cannot say which
    one its sentence is about, and guessing is the false-accusation shape the
    reader rule was corrected for. A blank line between the prose and the
    example also breaks the pairing -- a miss, stated rather than guessed at.
    """
    decl = declared_defaults() if decl is None else decl
    by_env = {env: default for env, default in decl.values()}
    out: list[Finding] = []
    for b in env_example_blocks(src):
        claim = _claim(b.block)
        if claim is None:
            continue
        flags = [env for env, _ln, _v in b.run if env in by_env]
        if len(flags) != 1:
            continue                  # no flag, or two: the pairing is a guess
        env, actual = flags[0], by_env[flags[0]]
        if claim == "both" or (claim == "off") is not (not actual):
            out.append(Finding(".env.example", b.line, env, claim, actual,
                               "env-example", b.block[:120]))
    return out


def env_example_silent_overrides(src: Optional[str] = None,
                                 decl: Optional[dict[str, tuple[str, bool]]] = None
                                 ) -> list[Finding]:
    """LIVE example lines that set a flag opposite to its default, unsaid.

    Keyed on the LINE, not the pairing: whatever sits above a live override
    has to say what the line does, including when nothing does. Six lines did
    this on 2026-09-23, and five had prose calling the flag default OFF, so the
    file read as consistent while `cp .env.example .env` switched off five
    controls the runbook lists as default ON.
    """
    decl = declared_defaults() if decl is None else decl
    by_env = {env: default for env, default in decl.values()}
    out: list[Finding] = []
    for b in env_example_blocks(src):
        for env, ln, val in b.run:
            if val is None or env not in by_env or val == by_env[env]:
                continue
            if not _EXAMPLE_SAYS[val].search(b.block):
                out.append(Finding(".env.example", ln, env,
                                   "on" if val else "off", by_env[env],
                                   "env-example-override", b.block[:120]))
    return out


#: The value-returning readers `bot/config.py` declares a knob through, whose
#: second argument is the code's default. `_env_bool` is the flag rule's.
_VALUE_READERS = {"_env", "_env_float", "_env_int",
                  "_env_float_bounded", "_env_int_bounded"}

#: What a live line that departs from its code default says above itself:
#: "this line raises it", "the two lines below disable it", "the line below
#: picks anthropic", or "departs from the code default".
_SAYS_DEPARTS = re.compile(
    r"\b(?:this|the(?:\s+two)?)\s+lines?\b[^.]*?"
    r"\b(?:raises?|lowers?|sets?|disables?|picks?|pins?)\b"
    r"|\bdeparts?\s+from\s+the\s+code(?:'s)?\s+default", re.I)


def declared_values(src: Optional[str] = None) -> dict[str, tuple[str, object]]:
    """`ENV -> (reader, default)` for every non-bool knob declared ONCE.

    Read off the AST of `bot/config.py`. A name declared twice with two
    readers or two defaults has no single default to compare against, so it
    is left out rather than guessed; so is a default that is not a literal.
    """
    tree = ast.parse(CONFIG_PY.read_text() if src is None else src)
    seen: dict[str, set] = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in _VALUE_READERS and len(n.args) >= 2):
            continue
        name, dflt = n.args[0], n.args[1]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            continue
        if isinstance(dflt, ast.UnaryOp) and isinstance(dflt.op, ast.USub) \
                and isinstance(dflt.operand, ast.Constant):
            value: object = -dflt.operand.value
        elif isinstance(dflt, ast.Constant) and not isinstance(dflt.value, bool):
            value = dflt.value
        else:
            continue
        seen.setdefault(name.value, set()).add((n.func.id, value))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


class ValueDeparture(NamedTuple):
    line: int          # 1-indexed, in `.env.example`
    env: str
    value: str         # what the live line sets
    default: object    # what `bot/config.py` declares
    text: str          # the prose above it, for the message


def env_example_value_departures(src: Optional[str] = None,
                                 decl: Optional[dict[str, tuple[str, object]]] = None
                                 ) -> list[ValueDeparture]:
    """LIVE `.env.example` values that differ from their code default, unsaid.

    The flag rule above covers on/off switches; this is the same claim for
    every other knob, because `cp .env.example .env` runs these values too. On
    2026-09-24 three did it with nothing said: `COMMISSION_PCT=0.1` over a code
    default of 0.06 (the Bitget standard taker rate, so the modelled fee was
    overstated by two thirds), `ENTRY_TIMING_REGIMES=` under the words
    "Default empty" while the code default is `TREND_DOWN` (a gate switched
    off in silence), and a pinned `LLM_MODEL` the routing tests forbid. A
    departure is allowed; an unsaid one is not.
    """
    decl = declared_values() if decl is None else decl
    text = ENV_EXAMPLE.read_text() if src is None else src
    lines = text.splitlines()
    out: list[ValueDeparture] = []
    for b in env_example_blocks(text):
        for env, ln, _v in b.run:
            raw = lines[ln - 1].strip()
            if raw.startswith("#") or env not in decl:
                continue
            reader, default = decl[env]
            value = raw.split("=", 1)[1].strip()
            if reader == "_env":
                same = value == str(default)
            else:
                try:
                    same = float(value) == float(default)  # type: ignore[arg-type]
                except ValueError:
                    same = False
            if not same and not _SAYS_DEPARTS.search(b.block):
                out.append(ValueDeparture(ln, env, value, default, b.block[:120]))
    return out


def all_findings() -> list[Finding]:
    return sorted(declaration_findings() + reader_findings()
                  + env_example_findings(),
                  key=lambda f: (f.path, f.line))


def describe(f: Finding) -> str:
    says = {"off": "OFF", "on": "ON", "both": "both ON and OFF"}[f.claimed]
    return (f"{f.path}:{f.line}  [{f.how}]  {f.env}\n"
            f"      comment says {says}; the flag is declared "
            f"{'True' if f.actual else 'False'}\n"
            f"      {f.text}")
