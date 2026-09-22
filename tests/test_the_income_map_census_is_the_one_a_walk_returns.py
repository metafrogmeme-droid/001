r"""The census the plan is sequenced off is derived from the leaf tables.

docs/INCOME_MAP.md opens with a three-row table -- shipped 5, partial 33, and
52 with nothing behind them -- and a sentence saying "Fifteen categories,
ninety leaves". That is the figure a reader uses to decide what to build next,
and NO TEST READ IT. The map already has a family of derived guards (its doors
are re-resolved against the command catalogue, its admin-only sentences
against the real decorators, its `path:line` citations against blank lines),
and the numbers at the very top were the part nothing checked.

Driven when this was written, the census reproduces to the digit. That is
worth recording as a RETRACTION: the note that sent me here said the figure
"must be re-driven before anything is sequenced off it", which was right to
demand the drive and wrong to imply it would not survive one. It survives.
What it lacked was a reason to keep surviving.

WHAT THIS DELIBERATELY DOES NOT CLAIM. It checks that the map's own numbers
describe the map's own tables -- an internal consistency, which is the part
that rots when a leaf is added. Whether each leaf's VERDICT is true of the
code is a different question, and the map states its own three limits for it
(code-reading not execution, citations unverified, only doors checked). Those
limits are pinned by `tests/test_income_map_doors_exist.py`, not here.
"""

from __future__ import annotations

import pathlib
import re

MAP = pathlib.Path(__file__).resolve().parents[1] / "docs/INCOME_MAP.md"
SRC = MAP.read_text(encoding="utf-8")

_HEAD = re.compile(r"^###\s+(.+?)\s*$", re.M)


def _census() -> tuple[list[str], list[tuple[str, str, str]]]:
    """`(categories, [(category, leaf, verdict)])` from the leaf tables.

    Bounded by the map's own section headings rather than by a character
    count -- "everything within N characters" is a boundary that manufactures
    accusations, which this repo records three times.
    """
    start = SRC.index("## The map")
    end = SRC.index("## Capabilities this map has no leaf for")
    body = SRC[start:end]

    cats: list[str] = []
    leaves: list[tuple[str, str, str]] = []
    current = ""
    for line in body.splitlines():
        head = _HEAD.match(line)
        if head:
            current = head.group(1)
            cats.append(current)
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        if set(cells[0]) <= set("-: ") or cells[0].lower().strip("*") == "leaf":
            continue
        leaves.append((current, cells[0], cells[1]))
    return cats, leaves


def _state(verdict: str) -> str:
    low = verdict.lower()
    if "shipped" in low:
        return "shipped"
    if "partial" in low:
        return "partial"
    # `—` and `— ⟲` are both "nothing in the tree serves this"; the glyph is a
    # note about re-visiting, not a fourth state.
    if verdict.replace("⟲", "").strip() in ("—", "-", ""):
        return "none"
    return f"UNRECOGNISED:{verdict!r}"


def test_every_verdict_is_one_the_legend_defines():
    """An unreadable verdict must not be silently counted as anything."""
    bad = sorted({v for _, _, v in _census()[1]
                  if _state(v).startswith("UNRECOGNISED")})
    assert not bad, (
        f"these leaf verdicts match none of shipped/partial/—: {bad}")


def test_the_headline_sentence_counts_the_tables():
    cats, leaves = _census()
    assert cats and leaves, "the walk found no tables — it would pass vacuously"
    words = {15: "Fifteen", 90: "ninety"}
    assert f"{words[len(cats)]} categories, {words[len(leaves)]} leaves" in SRC, (
        f"the opening sentence does not describe the tables: the walk finds "
        f"{len(cats)} categories and {len(leaves)} leaves")


def test_the_summary_table_counts_the_tables():
    _, leaves = _census()
    counted: dict[str, int] = {}
    for _, _, verdict in leaves:
        s = _state(verdict)
        counted[s] = counted.get(s, 0) + 1

    for label, key in (("shipped", "shipped"), ("partial", "partial")):
        m = re.search(rf"\|\s*\*\*{label}\*\*[^|]*\|\s*(\d+)\s*\|", SRC)
        assert m, f"the summary table has no **{label}** row"
        assert int(m.group(1)) == counted.get(key, 0), (
            f"the summary says {m.group(1)} {label}; the tables hold "
            f"{counted.get(key, 0)}")

    m = re.search(r"\|\s*\*\*—\*\*[^|]*\|\s*(\d+)\s*\|", SRC)
    assert m, "the summary table has no **—** row"
    assert int(m.group(1)) == counted.get("none", 0), (
        f"the summary says {m.group(1)} unserved; the tables hold "
        f"{counted.get('none', 0)}")

    assert sum(counted.values()) == len(leaves), (
        "the three states must partition the leaves, or the summary is a "
        "partial total printed as whole")


def test_the_active_trading_sentence_is_the_one_a_walk_returns():
    """"Four of the five shipped leaves are in Active Trading" is a claim
    about the tables, made in prose, so it is checked."""
    _, leaves = _census()
    shipped = [(cat, leaf) for cat, leaf, v in leaves if _state(v) == "shipped"]
    in_active = [leaf for cat, leaf in shipped if cat.strip() == "Active Trading"]
    words = {4: "Four", 5: "five"}
    assert (f"{words[len(in_active)]} of the {words[len(shipped)]} shipped "
            f"leaves are in Active Trading") in SRC, (
        f"the walk finds {len(in_active)} of {len(shipped)} shipped leaves in "
        f"Active Trading: {shipped}")


def test_no_leaf_is_listed_twice_in_a_category():
    _, leaves = _census()
    seen: dict[tuple[str, str], int] = {}
    for cat, leaf, _ in leaves:
        seen[(cat, leaf)] = seen.get((cat, leaf), 0) + 1
    dupes = sorted(k for k, n in seen.items() if n > 1)
    assert not dupes, (
        f"a leaf counted twice inflates every number above it: {dupes}")


# ---------------------------------------------------------------------------
# A SECOND DOCUMENT COUNT, FOUND THE SAME WAY AND STALE.
#
# docs/ROADMAP.md's shipped list claimed "Twelve languages" and named twelve
# codes. Driven, both sources carry FOURTEEN (`it` and `hi` were added), and
# agent_card.json already published `"interface": 14` -- so the product's own
# machine-readable surface contradicted its roadmap, and nothing compared them.
#
# This is the whole of what "Layer 0" needs to mean here: not a checklist of
# claims to re-audit by hand, but the count DERIVED from the thing it counts.
# ---------------------------------------------------------------------------

ROADMAP = pathlib.Path(__file__).resolve().parents[1] / "docs/ROADMAP.md"

_NUMBER_WORDS = {
    10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen",
    15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
}


def _supported_langs() -> list[str]:
    from bot.utils.i18n import SUPPORTED_LANGS
    return sorted(SUPPORTED_LANGS)


def test_the_roadmap_language_count_is_the_one_the_code_ships():
    langs = _supported_langs()
    word = _NUMBER_WORDS.get(len(langs))
    assert word, f"no number word for {len(langs)} — widen _NUMBER_WORDS"
    text = ROADMAP.read_text(encoding="utf-8")
    assert f"**{word} languages**" in text, (
        f"the roadmap does not say **{word} languages**, and "
        f"bot/utils/i18n.SUPPORTED_LANGS carries {len(langs)}: {langs}")


def test_the_roadmap_names_every_language_it_counts():
    """A count can be right while the list is wrong, and naming every code and
    no other cannot be. This is what the twelve-code list got wrong."""
    langs = _supported_langs()
    text = ROADMAP.read_text(encoding="utf-8")
    start = text.index("languages** — the web UI ships fully translated in")
    listing = text[start:start + 260]
    listed = set(re.findall(r"\b([a-z]{2})\b(?=[,\s])", listing))
    missing = sorted(set(langs) - listed)
    assert not missing, (
        f"the roadmap counts them and does not name them: {missing}")


def test_the_agent_card_and_the_roadmap_agree():
    """They disagreed, and the card was right. Nothing compared them."""
    import json
    card = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "agent_card.json")
        .read_text(encoding="utf-8"))

    def _interface(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "interface" and isinstance(value, int):
                    return value
                found = _interface(value)
                if found is not None:
                    return found
        if isinstance(obj, list):
            for value in obj:
                found = _interface(value)
                if found is not None:
                    return found
        return None

    published = _interface(card)
    assert published is not None, "agent_card.json publishes no interface count"
    assert published == len(_supported_langs()), (
        f"agent_card.json says {published} interface languages and the code "
        f"ships {len(_supported_langs())}")
