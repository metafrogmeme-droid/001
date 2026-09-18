"""The north star's own count, derived from the list it counts.

`docs/INCOME_MAP.md` is read FIRST to decide what to build next, so a number in
it is a claim a session is scoped from. Its "Capabilities this map has no leaf
for" section said NINETEEN in three places while the list under the heading had
grown to twenty-two — the trade co-pilot, trade costs, why a stop could not be
placed and the POC-retest setup each shipped as a capability the map still has
no leaf for, and each was appended under a count that did not move.

That is the `/setllm` ten-of-eleven shape pointed at a DOCUMENT, and this file
is the same answer the repo gives everywhere else: derive it, do not restate
it. `tests/test_claude_md_accuracy.py` does this for CLAUDE.md; the INCOME_MAP
had citation checks (the blank-line probe, the guarded-command derivation) and
nothing that read its counts.
"""

from __future__ import annotations

import pathlib
import re

MAP = pathlib.Path(__file__).resolve().parents[1] / "docs" / "INCOME_MAP.md"
SECTION = "## Capabilities this map has no leaf for"

#: Numerals as the prose writes them. Deliberately not a general number
#: parser: the document writes counts as WORDS, and a parser that also
#: accepted digits would pass against a sentence nobody would write.
WORDS = {
    18: "eighteen", 19: "nineteen", 20: "twenty", 21: "twenty-one",
    22: "TWENTY-TWO", 23: "twenty-three", 24: "twenty-four",
}


def _section_body(text: str) -> str:
    """The section, bounded by the NEXT top-level heading.

    Bounded by the heading rather than by "whatever happens to be next": a
    boundary that is the next thing this walk happens to recognise is the
    shape that manufactures accusations, which this repo records three times.
    """
    start = text.index(SECTION)
    rest = text[start + len(SECTION):]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def listed_capabilities() -> list[str]:
    """Every capability the section lists, as its own bold heading line."""
    return re.findall(r"^\*\*(.+?)\*\*$", _section_body(MAP.read_text(encoding="utf-8")), re.M)


def stale_numerals(body: str, n: int) -> list[str]:
    """Numeral words in `body` that are not the live count `n`.

    THE CURRENT NUMERAL IS REMOVED FIRST. Two drafts got this wrong in the
    same direction: `"twenty" in "twenty-two"` is True, and so is
    `re.search(r"\\btwenty\\b", "TWENTY-TWO")` — a hyphen IS a word boundary,
    so anchoring did not help. A guard against a stale count that accuses the
    LIVE one is the accusation-manufacturing shape, twice in one rule.
    """
    rest = re.sub(re.escape(WORDS[n]), "", body, flags=re.I)
    return [w for k, w in WORDS.items() if k != n
            and re.search(rf"\b{re.escape(w)}\b", rest, re.I)]


def test_the_section_counts_the_list_it_prints():
    rows = listed_capabilities()
    assert len(rows) >= 10, rows          # the walk found the list at all
    word = WORDS.get(len(rows))
    assert word, f"no numeral word for {len(rows)}; add one to WORDS"
    body = _section_body(MAP.read_text(encoding="utf-8"))
    assert f"The list is {word} now" in body, (
        f"the section lists {len(rows)} capabilities and its own sentence does "
        f"not say {word}. Move the count with the list — this document is read "
        "first to decide what to build, so a stale count here re-scopes work.")


def test_no_stale_count_survives_elsewhere_in_the_section():
    """The count was written THREE times, which is why it rotted.

    Two of the three said "the nineteen rows above" and "the nineteen unmapped
    capabilities above"; both now name the list without numbering it, so there
    is one count and it is derived. A second copy is a second answer.
    """
    body = _section_body(MAP.read_text(encoding="utf-8"))
    n = len(listed_capabilities())
    stale = stale_numerals(body, n)
    assert not stale, (
        f"the section lists {n} capabilities and still spells {stale} — a "
        "second copy of the count, which is how the first one went stale.")


def test_every_listed_capability_has_a_body():
    """A bold line with nothing under it is a heading, not a capability.

    Without this the count above could be inflated by a stray bold line, and
    the rule would pass while describing something that is not a row.
    """
    body = _section_body(MAP.read_text(encoding="utf-8"))
    empty = []
    for row in listed_capabilities():
        after = body.split(f"**{row}**", 1)[1].lstrip("\n")
        if not after.strip() or after.startswith("**"):
            empty.append(row)
    assert not empty, empty


def test_the_rule_can_actually_fail():
    """Driven on a planted section, because the real one passes.

    Every assertion above is satisfied by the document as it stands, so a
    mutation of one changes no verdict against it — the trap this repo records
    for `command_gates.py` and the candle ratchet alike.
    """
    planted = (
        f"{SECTION}\n\nThe list is nineteen now.\n\n"
        "**One**\n\nbody\n\n**Two**\n\nbody\n\n## Next\n\n**Three**\n\nbody\n"
    )
    rows = re.findall(r"^\*\*(.+?)\*\*$", _section_body(planted), re.M)
    # the boundary holds: "Three" is under the NEXT heading and is not a row
    assert rows == ["One", "Two"], rows
    # THE STALE RULE, driven. It passes vacuously against the real document,
    # so a mutation of it changes no verdict there.
    assert stale_numerals("The list is twenty-two now", 22) == []
    assert stale_numerals("The list is twenty-two now, was nineteen", 22) == ["nineteen"]
    # and the live numeral is never reported as its own stale prefix, which is
    # the false accusation two drafts of this rule made.
    assert "twenty" not in stale_numerals("twenty-two", 22)
