"""Every surface that promises human confirmation must name the flag that undoes it.

RC-2026-021. Six public surfaces stated an UNCONDITIONAL guarantee —

    "all trade executions require explicit human confirmation; the AI agent
     cannot autonomously place orders"                        (SECURITY.md)

— while `auto_confirm_live_enabled` defaults True in `bot/config.py`. The repo
already had this guard, applied to exactly ONE of the surfaces
(`tests/test_mcp_doc_matches_the_code.py`), which is an admission of the
defect rather than a fix for it.

WHY THIS ASSERTS A PRESENCE, NOT AN ABSENCE. The obvious guard — "the phrase
must not appear" — is wrong twice over. The claim is TRUE of the shipped
install: `.env.example` sets `AUTO_CONFIRM_LIVE_ENABLED=false` and
`AUTO_CONFIRM_THRESHOLD=1.0`, so `cp .env.example .env`, which four surfaces
document as the install, really does require a human press. Deleting the
sentence would remove a true statement, and this repo has now watched an
absence assertion misfire four times. What was wrong was the word "all".

So the rule is: state the claim, and name the condition beside it.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The flag an operator would search for. Present in the English and the
#: Chinese qualifier alike, so one token checks both.
QUALIFIER = "AUTO_CONFIRM_LIVE_ENABLED"

#: Ways the six surfaces phrase the promise, English and 正體中文.
CLAIM = re.compile(
    r"human[- ]confirm\w*|human-in-the-loop|the human decides|人工確認",
    re.IGNORECASE)

SURFACES = [
    "SECURITY.md",
    "README.md",
    "README.zh-TW.md",
    "docs/gitbook/README.md",
    # Campaign copy: "Every trade requires human confirmation" sat here
    # unqualified in two drafts meant to be pasted into X and Discord.
    "docs/SOCIAL_CAMPAIGN.md",
    "docs/x-thread-v5.md",
]


def _blocks(text: str):
    """Markdown paragraphs. A qualifier three paragraphs away is not a caveat."""
    out, cur = [], []
    for line in text.splitlines():
        if line.strip():
            cur.append(line)
        elif cur:
            out.append("\n".join(cur))
            cur = []
    if cur:
        out.append("\n".join(cur))
    return out


@pytest.mark.parametrize("rel", SURFACES)
def test_the_claim_never_stands_unqualified(rel):
    path = ROOT / rel
    assert path.exists(), f"{rel} is gone; this guard now watches nothing"
    bs = _blocks(path.read_text(encoding="utf-8"))
    # The block itself, or the one immediately after it. A table's caveat
    # belongs under the table and a diagram's under the diagram; requiring it
    # INSIDE would mean stuffing an env-var name into an ASCII box. One block
    # is still proximity — a qualifier three paragraphs away is not a caveat.
    unqualified = [
        b for i, b in enumerate(bs)
        if CLAIM.search(b) and QUALIFIER not in b
        and not (i + 1 < len(bs) and QUALIFIER in bs[i + 1])
    ]
    assert not unqualified, (
        f"{rel} promises human confirmation without naming the flag that "
        f"turns it off:\n\n" + "\n\n---\n\n".join(b[:300] for b in unqualified)
    )


#: The absolute quantifiers that were the actual defect. "all", "no trade",
#: "cannot" — the word that turns a true conditional guarantee into a false
#: unconditional one. Found by a mutation that survived the block-level rule:
#: restoring "all trade executions require explicit human confirmation" passed,
#: because the flag was still named LATER IN THE SAME BLOCK. A caveat in the
#: next paragraph does not repair the sentence a reader quotes.
ABSOLUTE = re.compile(
    r"(?:all trade executions?\s+require"
    r"|no trade\s+(?:executes?|is executed)\s+without"
    r"|cannot autonomously place"
    r"|every trade\s+requires"
    r"|所有交易[^。]*人工確認)",
    re.IGNORECASE)

#: In-sentence escape hatches. Any one of these makes the quantifier honest.
CONDITIONAL = re.compile(r"unless|except when|除非|subject to|" + QUALIFIER,
                         re.IGNORECASE)


def _sentences(block: str):
    """Rough sentence split. Table rows and list items count as sentences."""
    for line in block.splitlines():
        for part in re.split(r"(?<=[.。])\s+", line):
            if part.strip():
                yield part


@pytest.mark.parametrize("rel", SURFACES)
def test_an_absolute_quantifier_is_qualified_in_its_own_sentence(rel):
    """The strongest form of the claim needs the caveat where it is read.

    This is the rule the original finding was actually about: not that the
    guarantee is mentioned, but that it was stated WITHOUT EXCEPTION while
    `auto_confirm_live_enabled` defaults True.
    """
    bad = [
        sent for block in _blocks((ROOT / rel).read_text(encoding="utf-8"))
        for sent in _sentences(block)
        if ABSOLUTE.search(sent) and not CONDITIONAL.search(sent)
    ]
    assert not bad, (
        f"{rel} states the guarantee absolutely, with no exception in the "
        f"same sentence:\n  " + "\n  ".join(s.strip()[:200] for s in bad)
    )


@pytest.mark.parametrize("rel", SURFACES)
def test_the_claim_is_still_made_at_all(rel):
    """Qualifying must not become deleting.

    The guarantee is real when auto-confirm is off, and it is off as shipped.
    A reader deciding whether to trust this bot needs to read that it exists.
    """
    assert CLAIM.search((ROOT / rel).read_text(encoding="utf-8")), (
        f"{rel} no longer states the human-confirmation guarantee at all"
    )


class TestTheMachineReadableCard:
    """`agent_card.json` declares this to other agents as structured fact."""

    @staticmethod
    def _card():
        return json.loads((ROOT / "agent_card.json").read_text(encoding="utf-8"))

    def test_the_boolean_still_reflects_the_shipped_posture(self):
        """Deliberately still true.

        The audit's second pass rated flipping it to `false` "a safety
        declaration inverted toward danger on every standard install" — and it
        was right: the standard install DOES require confirmation.
        """
        card = self._card()
        assert card["requires_confirmation"] is True
        assert card["safety"]["human_in_the_loop"] is True

    @pytest.mark.parametrize("key,where", [
        ("requires_confirmation_note", "top level"),
        ("human_in_the_loop_note", "safety block"),
    ])
    def test_each_boolean_carries_its_condition(self, key, where):
        card = self._card()
        note = card.get(key) if where == "top level" else card["safety"].get(key)
        assert note, f"{key} is missing; the boolean reads as unconditional"
        assert QUALIFIER in note, (
            f"{key} does not name the flag that changes the answer"
        )


class TestTheGuardIsNotStale:
    """If the code default ever flips, this whole file should be revisited."""

    def test_the_code_default_really_is_auto_confirm_on(self):
        cfg = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
        assert re.search(
            r"auto_confirm_live_enabled[^\n]*_env_bool\([^\n]*True", cfg), (
            "auto_confirm_live_enabled no longer defaults True — the "
            "unconditional claim may now be accurate, and these qualifiers "
            "should be reconsidered rather than assumed"
        )

    def test_the_shipped_env_really_does_turn_it_off(self):
        """The qualifier's own factual claim, checked.

        Every surface now tells the reader `.env.example` ships it off. If that
        stopped being true, the caveat would be the new false statement.
        """
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert re.search(r"^AUTO_CONFIRM_LIVE_ENABLED=false\s*$", env, re.M)
        assert re.search(r"^AUTO_CONFIRM_THRESHOLD=1\.0\s*$", env, re.M)
