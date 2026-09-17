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
from typing import Optional, Sequence

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


def plain_text(result: object) -> Optional[str]:
    """Any text as the model will read it, or None if it said nothing.

    PUBLIC because it is one reading with more than one reader: the records
    below, and `ConversationStore.note_alert`, which remembers what the bot
    said unprompted. A second copy of "what the model sees" would be a second
    answer about the same bytes.

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


def _headed(head: str, body: str) -> str:
    """Close the parenthesis ``head`` opened, announcing a truncation inside it.

    Three records share this tail — the router's answer, the website's, and
    a slash command's reply — and it was three byte-identical copies until a
    mutation driver's anchor matched two of them at once, which is the
    second-copy shape showing up in the instrument built to find it. One
    tail, so the announced truncation cannot drift between records.
    """
    if len(body) > MEMORY_CAP:
        return (f"{head}; TRUNCATED — first {MEMORY_CAP} of {len(body)} "
                "characters; the rest is not recorded):\n" + body[:MEMORY_CAP])
    return f"{head}):\n{body}"


def _captured(replies: Sequence[object]) -> Optional[str]:
    """What the send chokepoint DELIVERED, as the model will read it, or None.

    One reading for both doors that capture rather than ask — a slash command
    and a tapped button — because "was anything delivered" is one question and
    two answers to it would drift the day either joins its chunks differently.
    """
    joined = "\n".join(str(r) for r in replies if r is not None) if replies else ""
    return plain_text(joined) if joined else None


def _nothing_captured(tag: str, did: str) -> str:
    """The record for a door that answered and whose reply was not seen.

    Shared for the reason ``_headed`` is: the first draft of the button
    record was byte-identical to the command one, which is the second-copy
    shape appearing inside a slice about second copies. ``did`` is what the
    door did, because "/x ran" and "the user tapped that button" are the one
    thing the two records do not share.

    Nothing captured is NOT "it sent nothing": twenty commands reply through
    the bot object directly, six callback branches send by their own route,
    and a rate-limited ``/help`` returns in silence — from the chokepoint's
    side those are one absence. The record says what it knows and claims no
    send it did not see.
    """
    return (f"[{tag}] SHOWN, CONTENTS NOT RECORDED — {did} and no reply from "
            "it was captured in this transcript: it may reply by a route this "
            "transcript does not see, or it may have sent nothing. Nothing of "
            "its reply can be quoted, summarised or counted from here.")


def skill_result_memory(skill: str, result: object) -> str:
    """The assistant turn to record after ``skill`` returned ``result``."""
    body = plain_text(result)
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
    body = plain_text(reply)
    head = f"[{intent}] shown by the website (its own reading; no bot tool ran"
    if body is None:
        return (f"{head}; the reply carried no text, so nothing of it is "
                "recorded).")
    return _headed(head, body)


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
    body = plain_text(answer)
    if body is None:
        # A routed branch that replied with nothing is a defect in that
        # branch, and saying so is more useful to the next turn than silence.
        return (f"[{intent}] ANSWERED WITH NOTHING — the reply carried no "
                "text. Nothing was said to the user.")
    return _headed(f"[{intent}] answered (no tool ran", body)


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


def command_turn_text(command: str, n_args: int) -> str:
    """The USER turn to record for a slash command: the command, never its
    arguments.

    The routed free-text path records the message verbatim, and a slash
    command cannot: five commands take a SECRET as their argument
    (``/setexchange``, ``/setgateway``, ``/setsigner``, ``/setllm``,
    ``/connect``), and this store is both a file on disk and the model's
    prompt. A list of the commands whose arguments are safe to keep would be
    the ``/setllm`` ten-of-eleven shape — a command added later would leak
    by default — so the default is to withhold, and the count says that an
    argument existed. The reply captured beside it usually carries what the
    argument named: a dossier prints its symbol.
    """
    cmd = str(command).strip().lstrip("/")
    if n_args <= 0:
        return f"/{cmd}"
    plural = "s" if n_args != 1 else ""
    return f"/{cmd} ({n_args} argument{plural} not recorded)"


def command_reply_memory(command: str, replies: Sequence[object]) -> str:
    """The assistant turn to record after a SLASH COMMAND replied.

    A fifth record, and the distinction from the four above is WHO answered.
    A tool result is a measurement a tool the model holds made; a routed
    answer is the router speaking; a card shown is a command's reply the
    branch could not see; a refusal is a gate. This is a command's reply the
    runtime DID see — captured at the send chokepoint, as the user saw it —
    and it must not wear the ``[x] result:`` shape, because both tool rules
    tell the model such a block "was written by the runtime after a tool
    really ran" and ``/setexchange`` is no tool the model holds: the argument
    ``web_answer_memory`` makes for the website's intercepts, one transport
    over. The marker word is ``SHOWN``, which the fabrication guard already
    polices, so a model copying this shape is claiming a command it never ran.

    ``replies`` is what the chokepoint DELIVERED, in order — the chunks of one
    card, a refusal, or nothing. Nothing is not "the command sent nothing":
    twenty commands reply through the bot object directly and a rate-limited
    ``/help`` returns in silence, and from here those are one absence. The
    record says what it knows and claims no send it did not see.
    """
    cmd = str(command).strip().lstrip("/")
    body = _captured(replies)
    if body is None:
        return _nothing_captured(cmd, f"/{cmd} ran")
    head = (f"[{cmd}] SHOWN by the /{cmd} command (its own reply, as the user "
            "saw it; no chat tool ran")
    return _headed(head, body)


def button_turn_text(action: str) -> str:
    """The USER turn to record for a TAPPED BUTTON: what they did, not typed.

    The person typed nothing at all, so a turn shaped like a message would be
    the first false thing in the record. What they chose was a button, and
    ``action`` is the dispatcher's own name for it — an internal identifier
    rather than the label they read, which is why the turn says so instead of
    quoting it as words.

    The payload never appears, for the reason ``command_turn_text`` gives and
    one it does not have: ``confirm:<trade_id>:<uid>`` is machinery, and
    ``admit:<uid>`` is ANOTHER USER'S Telegram id. A slash argument is at
    worst the caller's own secret; this one would be somebody else's
    identifier, in this caller's prompt and in a file on disk.
    """
    act = str(action).strip() or "unnamed"
    if act == "unnamed":
        return ("(tapped a button — this build does not name that button, "
                "no text typed)")
    return f'(tapped a button — action "{act}", no text typed)'


def button_reply_memory(action: str, replies: Sequence[object]) -> str:
    """The assistant turn to record after a TAPPED BUTTON replied.

    A SEVENTH record, and the distinction is the same one the six make: WHO
    answered. Not ``skill_result_memory`` — both tool rules tell the model an
    ``[x] result:`` block "was written by the runtime after a tool really
    ran", and the Close button is no tool the model holds. Not
    ``command_reply_memory`` either, whose every sentence says ``/x``: a
    button is not a command, the model must never learn to offer one as if it
    were typeable, and reusing that record would teach it exactly that. Not
    ``card_shown_memory``, whose wording promises a send its callers watched
    happen — from here a send is CAPTURED, and six branches reply by a route
    the chokepoint never sees.

    The marker word is ``SHOWN``, which the fabrication guard already
    polices, so a model writing this shape is claiming a tap that never
    happened.
    """
    act = str(action).strip() or "unnamed"
    body = _captured(replies)
    if body is None:
        return _nothing_captured(act, "the user tapped that button")
    head = (f"[{act}] SHOWN by a button the user tapped (its own reply, as "
            "the user saw it; no chat tool ran")
    return _headed(head, body)


def record_routed_turn(store, user_id: str, text: str, intent: str,
                       record: str, *, surface: str,
                       skill: Optional[str] = None,
                       via: Optional[str] = None) -> None:
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

    ``via`` names the door the turn came through when it was not the intent
    router — ``"command"`` for a slash command — so a reader of the store can
    tell a typed ``/networth`` from the words "my net worth" without parsing
    the user turn.

    Memory is context, never a dependency: a store that raises must not be the
    reason a reply the user already read fails to arrive.
    """
    meta: dict[str, object] = {"intent": intent, "surface": surface,
                               "routed": True}
    if skill:
        meta["skill"] = skill
    if via:
        meta["via"] = via
    try:
        store.append(user_id, "user", text,
                     metadata={"intent": intent, "surface": surface})
        store.append(user_id, "assistant", record, metadata=meta)
    except Exception:
        system_log.debug("routed turn not recorded for %s", intent,
                         exc_info=True)
