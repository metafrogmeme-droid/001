"""One report over a token nobody has traded yet.

Composes the two scorers — ``token_safety`` (the contract's mechanics) and
``deployer_history`` (the person who shipped it) — into a single answer, plus
the provenance of every section so a reader can tell a fact from a silence.

WHY COMPOSING IS THE HARD PART

Each scorer is already honest on its own. The danger is entirely in the join,
and it has three classic shapes, all of which this module refuses:

1. AVERAGING TWO VERDICTS. A `known_bad` deployer and a `safe` contract do not
   make a `caution` token. Mechanics and provenance are not commensurable
   quantities — a contract that is technically clean shipped by somebody with a
   prior rug is a stand-down, and averaging turns the strongest signal you have
   into a middling one. The dossier takes the STRONGEST stand-down present and
   names which section raised it.

2. AVERAGING TWO COVERAGES. "60% covered" over a dossier where the contract was
   fully read and the deployer was invisible describes neither. Coverage is
   reported PER SECTION and never merged: the question a reader has is not "how
   much do you know" but "which part are you blind to".

3. LETTING A CLEAN SECTION UPGRADE A DIRTY ONE. A `clean` deployer cannot lift a
   `danger` contract, and a `safe` contract cannot lift a `known_bad` deployer.
   Good news never cancels bad news here; it only fails to add to it.

THE VERDICT LADDER

    stand_down  — any hard finding, from either section
    caution     — soft flags, or one section unreadable while the other is fine
    unproven    — too little read anywhere to say anything
    watch       — both sections read, nothing against it

`watch` is the ceiling, and it is deliberately not called "safe" or "buy".
The best outcome of investigating a brand-new token is that nothing was found
against it in the short time it has existed — which is a reason to keep looking,
not a reason to act.
"""
from __future__ import annotations

from typing import Optional

from bot.core import deployer_history as dh
from bot.core import token_safety as ts

# Dossier verdicts. A fourth distinct vocabulary, for the same reason the other
# two differ from each other: this is a judgement about a whole token, and
# reusing a section's word would let a caller mistake one for the other.
WATCH = "watch"
UNPROVEN = "unproven"
CAUTION = "caution"
STAND_DOWN = "stand_down"

#: Worst-first. Position in this tuple IS the ordering; there is no numeric
#: severity to average by accident.
_LADDER = (STAND_DOWN, CAUTION, UNPROVEN, WATCH)

#: How each section's verdict maps into the dossier ladder. Written out rather
#: than derived from a shared severity number, because the two scorers use
#: different words on purpose and a shared scale would be the averaging this
#: module exists to refuse, one level down.
_TOKEN_MAP = {
    ts.DANGER: STAND_DOWN,
    ts.CAUTION: CAUTION,
    ts.SAFE: WATCH,
}
_DEPLOYER_MAP = {
    dh.KNOWN_BAD: STAND_DOWN,
    dh.SUSPECT: CAUTION,
    dh.UNPROVEN: UNPROVEN,
    dh.CLEAN: WATCH,
}


def _worst(*verdicts: str) -> str:
    """The strongest stand-down among the given verdicts."""
    for level in _LADDER:
        if level in verdicts:
            return level
    return UNPROVEN


def compose(token_report: Optional[dict] = None,
            deployer_report: Optional[dict] = None,
            sources: Optional[dict] = None) -> dict:
    """Assemble a dossier. Returns::

        {verdict, driven_by, sections:{name:{verdict,coverage,flags,source}},
         flags, blind_spots, unreadable}

    Every argument is optional and a missing section is recorded as unreadable
    rather than skipped — a dossier that silently omitted the deployer section
    would read as "we looked and there was nothing to say".
    """
    src = sources or {}
    sections: dict[str, dict] = {}
    blind: list[str] = []

    def add(name: str, report: Optional[dict], mapping: dict,
            missing_verdict: str) -> str:
        if not report or not isinstance(report, dict):
            # NOT skipped. An absent section is a hole in the dossier and has
            # to appear as one; leaving it out entirely makes a two-section
            # report look like a complete one-section report.
            sections[name] = {"verdict": None, "coverage": None, "flags": [],
                              "source": src.get(name), "read": False}
            blind.append(name)
            return missing_verdict
        cov = report.get("coverage") or {}
        sections[name] = {
            "verdict": report.get("verdict"),
            "coverage": cov,
            "flags": list(report.get("flags") or []),
            "source": src.get(name),
            "read": True,
        }
        mapped = mapping.get(report.get("verdict"), UNPROVEN)
        if cov.get("basis") in ("none", None):
            blind.append(name)
            # A SECTION THAT READ NOTHING CONTRIBUTES `unproven`, NOT ITS WORD.
            #
            # `token_safety` returns `caution` for a token it could not read at
            # all — correct there, because caution is its floor and it must
            # never say `safe` on no evidence. Mapped straight through, that
            # made a dossier over a brand-new token announce "CAUTION (on
            # contract)", which reads as "we looked and found something
            # concerning" when the truth is "nothing answered". The verdicts
            # were each honest and the JOIN was not.
            #
            # Except for a stand-down. A honeypot found on one readable check is
            # still a honeypot: coverage caps confidence in safety, never in
            # danger — the same rule the two scorers apply internally, applied
            # again where they meet.
            if mapped != STAND_DOWN:
                return UNPROVEN
        return mapped

    tok = add("contract", token_report, _TOKEN_MAP, UNPROVEN)
    dep = add("deployer", deployer_report, _DEPLOYER_MAP, UNPROVEN)

    verdict = _worst(tok, dep)

    # Which section is responsible, so a reader can act on it. A bare verdict
    # with two sections behind it sends somebody hunting through both.
    if verdict == UNPROVEN:
        driven_by = None
    else:
        driven_by = [name for name, mapped in (("contract", tok), ("deployer", dep))
                     if mapped == verdict]

    return {
        "verdict": verdict,
        "driven_by": driven_by,
        "sections": sections,
        # Flattened for display, each still attributed to its section.
        "flags": [f"{name}: {fl}"
                  for name, s in sections.items() for fl in s["flags"]],
        # The question a reader actually has: which part am I blind to?
        "blind_spots": blind,
        "unreadable": len(blind),
    }


def human_readable(dossier: Optional[dict]) -> str:
    """Plain-text render (no markup)."""
    if not dossier or not isinstance(dossier, dict):
        return "No dossier."
    v = dossier.get("verdict", UNPROVEN)
    icon = {WATCH: "○", UNPROVEN: "?", CAUTION: "⚠", STAND_DOWN: "⛔"}.get(v, "·")
    head = f"{icon} TOKEN DOSSIER: {v.upper().replace('_', ' ')}"
    if dossier.get("driven_by"):
        head += f" (on {', '.join(dossier['driven_by'])})"
    lines = [head]

    for name, s in (dossier.get("sections") or {}).items():
        if not s.get("read"):
            lines.append(f"   {name}: not read — no source answered")
            continue
        cov = s.get("coverage") or {}
        lines.append(f"   {name}: {s.get('verdict')} "
                     f"[{cov.get('basis')} — {cov.get('readable')}/{cov.get('total')}]"
                     + (f"  via {s['source']}" if s.get("source") else ""))
    for fl in dossier.get("flags", []):
        lines.append(f"   – {fl}")
    if v == WATCH:
        # The ceiling is not a recommendation, and the render says so rather
        # than leaving the absence of flags to imply it.
        lines.append("   nothing found against it yet — a new token is short on "
                     "history, not proven safe")
    return "\n".join(lines)
