"""What the conversation memory records after a skill runs.

Both surfaces used to write the same sentence no matter what happened:

    self.conversations.append(tg_id, "assistant",
                              f"[{intent.skill}] executed successfully", ...)

The Telegram call site's own comment read ``# Store skill result as assistant
message (truncated)``. It stored no result. The comment described the intent and
the code stored a placeholder, and nothing in between ever noticed.

WHY THIS PRODUCES FICTION. That string is the assistant's turn in the history
handed to the chat model. Ask a follow-up — "what did you find?" — and the model
is shown a user question, the words "executed successfully", and nothing else.
It has been told an answer exists and not what it was, which is the one prompt
shape most likely to be filled in with something plausible. That is the
UNIVERSE/USDT confabulation and the four-RSI-values-for-one-pair reply: not the
model being unreliable in general, but the model being handed a gap exactly
where the evidence should have been.

A skill that finds nothing says so in its output. A skill that fails says so.
Recording "executed successfully" over both is the same defect this repository
keeps finding one surface at a time — absent rendered as a measurement — moved
into the memory layer, where it is invisible on every screen and shows up as
invention several turns later.

THREE OUTCOMES, THREE RECORDS, and the distinction is the point:

    result text   ->  "[skill] result:\\n<what the tool actually said>"
    nothing        ->  "[skill] NO OUTPUT — the tool returned nothing."
    an exception   ->  "[skill] FAILED — the tool raised an error ..."

Truncation is ANNOUNCED rather than silent. A long scan card cut at a fixed
length and presented whole is a partial printed as a total — the model reads
seven of twelve rows as the complete set and describes twelve. The marker
carries both lengths so the gap is visible in the transcript itself.

The exception's own text is deliberately NOT recorded. Memory feeds the model,
the model writes to a user, and a driver message can carry a URL, a host or a
config value. `/readyz` answers with a coarse reason from a fixed vocabulary for
this reason; so does this.
"""

from __future__ import annotations

import html as _html
import re
from typing import Optional

#: The store persists `content[:2000]` (conversation_store.py:285, :351), so a
#: record longer than that is silently shortened again on the way to disk and a
#: restart quietly changes what the model remembers. The cap here leaves room
#: for the longest prefix below to stay inside that, and a test pins the
#: relation rather than the two numbers.
MEMORY_CAP = 1500

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def _plain(result: object) -> Optional[str]:
    """A skill's output as the model will read it, or None if it said nothing.

    Tags become a SPACE, not nothing: ``<b>LONG</b>XLM`` collapsing to
    ``LONGXLM`` invents a token the tool never emitted. Entities are unescaped
    for the same reason — a model reading ``&amp;`` sees a literal that was
    never on the user's screen.

    Line structure is kept. A scan card's newlines carry one row per symbol,
    and flattening them is how four RSI values end up attached to one pair.
    """
    if result is None:
        return None
    text = _html.unescape(_TAG.sub(" ", str(result)))
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return _BLANK_RUN.sub("\n\n", text).strip() or None


def skill_result_memory(skill: str, result: object) -> str:
    """The assistant turn to record after ``skill`` returned ``result``."""
    body = _plain(result)
    if body is None:
        # NOT "executed successfully". A skill that returned nothing is a fact
        # the model can relay; a skill that "succeeded" with no content is a
        # blank the model will fill.
        return f"[{skill}] NO OUTPUT — the tool returned nothing."
    if len(body) > MEMORY_CAP:
        return (f"[{skill}] result (TRUNCATED — first {MEMORY_CAP} of "
                f"{len(body)} characters; the rest is not recorded):\n"
                + body[:MEMORY_CAP])
    return f"[{skill}] result:\n{body}"


def skill_failure_memory(skill: str) -> str:
    """The assistant turn to record when a skill raised.

    Both surfaces used to return their apology to the user and record NOTHING,
    leaving the history with a question and no answer — a gap that reads, a few
    turns later, as an answer the model must reconstruct. Carries no detail from
    the exception: memory feeds the model and the model writes to a user.
    """
    return (f"[{skill}] FAILED — the tool raised an error and returned no "
            "result. Nothing was measured.")


def skill_unavailable_memory(skill: str) -> str:
    """The assistant turn to record when the named skill could not be run.

    DISTINCT FROM ``skill_failure_memory`` on purpose. A skill that raised was
    reached and returned nothing; a skill that is unavailable was never reached
    at all, and the difference is what a later turn needs to avoid inventing.
    "The tool errored" invites a retry. "There is no such tool here" does not.

    Like the failure memory, it names no internals: memory feeds the model and
    the model writes to a user.
    """
    return (f"[{skill}] UNAVAILABLE — this bot has no such tool wired up, so it "
            "was never run. Nothing was measured, and nothing about it can be "
            "answered from here.")
