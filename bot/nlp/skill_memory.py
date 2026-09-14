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

from bot.utils.logger import system_log

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


def web_answer_memory(intent: str, reply: object) -> str:
    """The assistant turn to record when the WEBSITE answered the question.

    app/routes/chat.js answers a dozen shapes of question with no bot
    round-trip — alerts, replay, the weekly letter, net worth, research — and
    POSTs the exchange to /chat/record so the next turn can build on it. That
    record took the ``[intent] result:`` shape above, and the shape is a
    CLAIM: both tool rules tell the model such a block "was written by the
    runtime after a tool really ran", and ``[networth] result:`` names a tool
    no surface holds. On Telegram the model was then told to call it again
    for a fresh figure — a tool it has never been offered.

    The website DID read something (its own records), so this is not the
    ``routed_answer_memory`` case either: "no tool ran" would undersell it.
    It is a third thing and gets its own words — who answered, and that no
    bot tool did. Same cap, same announced truncation, and the marker word
    is in the fabrication guard's vocabulary like every other record here.
    """
    body = _plain(reply)
    head = f"[{intent}] shown by the website (its own reading; no bot tool ran"
    if body is None:
        return (f"{head}; the reply carried no text, so nothing of it is "
                "recorded).")
    if len(body) > MEMORY_CAP:
        return (f"{head}; TRUNCATED — first {MEMORY_CAP} of {len(body)} "
                "characters; the rest is not recorded):\n" + body[:MEMORY_CAP])
    return f"{head}):\n{body}"


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


def routed_answer_memory(intent: str, answer: object) -> str:
    """The assistant turn to record for a reply the ROUTER produced itself.

    DISTINCT FROM ``skill_result_memory`` on purpose, and the distinction is
    the same one the three records above draw. A skill result is a
    MEASUREMENT — the tool went and looked. A routed answer is the bot
    speaking: a door ("nothing has been closed"), a refusal, a paywall, a
    stance explanation. Recording one as the other would tell the next turn
    that something was measured when nothing was, which is the shape this
    module exists to keep out of the history.

    It is recorded at all because the alternative is what every routed branch
    did: record NOTHING. A turn the user can see on their screen and the model
    cannot see at all is worse than a placeholder — the user says "why not?"
    and the history contains neither the request nor the refusal, so the model
    answers a question it has not been shown.
    """
    body = _plain(answer)
    if body is None:
        # A routed branch that replied with nothing is a defect in that
        # branch, and saying so is more useful to the next turn than silence.
        return (f"[{intent}] ANSWERED WITH NOTHING — the reply carried no "
                "text. Nothing was said to the user.")
    if len(body) > MEMORY_CAP:
        return (f"[{intent}] answered (no tool ran; TRUNCATED — first "
                f"{MEMORY_CAP} of {len(body)} characters; the rest is not "
                f"recorded):\n" + body[:MEMORY_CAP])
    return f"[{intent}] answered (no tool ran):\n{body}"


def card_shown_memory(card: str) -> str:
    """The assistant turn to record when a COMMAND answered and its text is
    not available where the branch returns.

    Several routed branches hand off to a guarded command handler
    (``_cmd_help``, ``_cmd_status``, ``_cmd_orders``, ``_cmd_open_positions``)
    which sends its own message — the card never passes through the caller, so
    there is nothing to record with ``skill_result_memory``.

    The honest record is not a placeholder. ``"[status] executed
    successfully"`` is the exact string this module was written to delete, and
    a marker that only NAMES the card is the same sentence in nicer clothes:
    both tell the model an answer exists and leave it to supply one. This one
    states the gap — the answer exists, its contents are NOT here — so the
    model's honest continuation is "I do not have that in front of me" rather
    than a reconstruction.

    Upgrading a call site to the real text is strictly better, and each of
    these commands is one text-returning seam away from it.
    """
    return (f"[{card}] SHOWN, CONTENTS NOT RECORDED — the {card} card was "
            "sent to the user and its text is not in this transcript. Nothing "
            "in it can be quoted, summarised or counted from here.")


def not_run_memory(skill: str, reason: str) -> str:
    """The assistant turn to record when a tool was REFUSED before it ran.

    A refusal is not a failure and it is not an absence of tooling: the tool
    exists, it was reachable, and a gate said no. The three read differently
    to the next turn — a failure invites a retry, an absent tool does not, and
    a refusal is answered by fixing the caller's tier or role — so they are
    three records, not one.

    The reason is written by the call site from its own gate, never taken from
    a driver message or an exception: memory feeds the model and the model
    writes to a user.
    """
    return (f"[{skill}] NOT RUN — {reason}. Nothing was measured, and no "
            "result from it exists for this turn.")


def record_routed_turn(store, user_id: str, text: str, intent: str,
                       record: str, *, surface: str,
                       skill: Optional[str] = None) -> None:
    """Write the question and what answered it into the conversation store.

    ONE implementation, called by both transports, because the alternative was
    tried on the skill-dispatch path and the two copies already differ: the
    web records `user` + result + recall, Telegram records `user` + result.
    A second copy of this is a second answer about what the model remembers.

    ``skill`` names the tool that actually RAN, when one did — ``scan_deep``
    is routed to the ``deepscan`` skill, and recording the router's name for
    it would attribute the output to a tool that was never called. It also
    decides ``Message.is_tool_record()``, which is what puts the age stamp on
    its own line instead of inline.

    Memory is context, never a dependency: a store that raises must not be the
    reason a reply the user already read fails to arrive.
    """
    meta: dict[str, object] = {"intent": intent, "surface": surface,
                               "routed": True}
    if skill:
        meta["skill"] = skill
    try:
        store.append(user_id, "user", text,
                     metadata={"intent": intent, "surface": surface})
        store.append(user_id, "assistant", record, metadata=meta)
    except Exception:
        system_log.debug("routed turn not recorded for %s", intent,
                         exc_info=True)
