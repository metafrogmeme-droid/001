"""Refuse a reply in which the model claims a tool ran that never ran.

WHAT HAPPENED. `bot/nlp/skill_memory.py` records what a tool returned as an
assistant turn, so the model can refer back to it:

    [get_portfolio] result:
    <what the tool actually said>

That is the memory-grounding fix, and it works. But a conversation full of
turns in that shape is also a FORMAT, and next-token prediction copies
formats. Asked "Doji BTC" on 2026-08-31, v12 continued the pattern: it wrote
its own `[analyze_asset] result:` block, and a `[PENDING] scanning...` that
will never resolve because nothing is scanning.

WHY THIS IS WORSE THAN THE BUG IT REPLACED. The empty reply this fix
superseded was a failure the bot could see and report. A fabricated tool
result is indistinguishable from a real one — same prefix, same layout, same
authority — and it is reporting EXECUTION. On a trading bot, "I ran the
analysis" and "I did not run the analysis" are not stylistic variants.

WHY NOT JUST TELL THE MODEL NOT TO. The prompt does now say so, and that
lowers the rate. It cannot be the whole fix: an instruction is a request to
a sampler, and this exact failure already survived a training generation
aimed at it (see `rr_honesty`, same lesson one layer down). Structure holds
where instruction persuades.

REACHABILITY — WHY THIS CANNOT DELETE A TRUE STATEMENT. Checked before
writing it, because "not every match is a defect" has cost this repository
real time. Every `[name] <marker>` shape below is produced by a record
function in `skill_memory` (or the timeout record in `chat_tools.run_tool`),
and each of those strings is appended to the conversation HISTORY. None is
returned through `_chat_ret`, no canned fallback on either surface contains
one, and — the half that matters once the model can call tools — the text
`run_tool` hands BACK to the model as a tool's output never carries the
marker either (`tests/test_chat_guards_say_what_ran.py` drives every
outcome). A model that quotes its own evidence faithfully therefore never
trips this; a marker seen HERE was written by the model, about a record the
runtime did not make.

WHY TRUNCATE RATHER THAN EXCISE. Removing just the marker line leaves the
invented body behind as ordinary prose — the fabrication stripped of the one
label that made it recognisable. Everything after the marker is inside the
claim, so the claim is where the reply stops being answerable for.
"""

from __future__ import annotations

import re

#: The whole vocabulary the memory layer writes — a tool's result, its
#: absence, a failure, a timeout, a refusal, an unavailable tool, a routed
#: answer, a card shown, an answer the website gave — plus the `[PENDING]`
#: shape the model invented for itself. It was four words for a long time,
#: while `skill_memory` grew to nine records: a model copying
#: "[status] SHOWN, CONTENTS NOT RECORDED" was claiming a card it never sent,
#: in a shape nothing policed. Anchored to the start of a line (the marker
#: always begins one) so a user quoting "[analyze_asset] result:"
#: mid-sentence back at the bot is not what this fires on.
_MARKER = re.compile(
    r"^[ \t]*\[(?:"
    r"PENDING\]"                                  # [PENDING] scanning...
    r"|[A-Za-z_][\w.]*\]"                         # [skill] ...
    r"[ \t]*(?:result\b|NO OUTPUT\b|FAILED\b|UNAVAILABLE\b|TIMED OUT\b"
    r"|NOT RUN\b|ANSWERED WITH NOTHING\b|answered\b|SHOWN\b"
    r"|shown by the website\b)"
    r")",
    re.MULTILINE,
)

#: Said instead when nothing survives the truncation. Deliberately plain,
#: deliberately not offering a specific command (`_chat_ret` serves the
#: private, public and web surfaces, and "run /scan" is wrong on two of
#: them), and deliberately NOT claiming the bot cannot run tools: the first
#: version said "I cannot run one from this chat", which is true on the
#: public surface and false on the two where the model is offered tools —
#: there it had tools and did not call one, and telling the user otherwise
#: is the fabrication's cousin.
REFUSAL = (
    "I started to write a tool result that I do not have, so I have stopped: "
    "nothing ran for that claim and there is no result to report. Ask the "
    "question again in plain words and I will answer from what I can "
    "actually read."
)


def find_fabricated_marker(text: str):
    """Offset of the first fabricated tool-result marker, or ``None``.

    Separate from the stripping so a caller can ask the question without
    taking the action, and so the test suite can drive detection and
    replacement independently.
    """
    if not isinstance(text, str) or not text:
        return None
    m = _MARKER.search(text)
    return m.start() if m else None


def strip_fabricated_tool_results(text: str):
    """``(cleaned, n)`` — the reply up to the first fabricated claim.

    ``n`` is 0 when there was nothing to do and the text is returned
    unchanged, so a caller can distinguish "checked, clean" from "corrected".
    It is never a count of markers: once the first one lands the rest of the
    reply is inside it, and reporting three would suggest three separate
    salvageable regions.
    """
    at = find_fabricated_marker(text)
    if at is None:
        return text, 0
    kept = text[:at].rstrip()
    return (kept if kept else REFUSAL), 1
