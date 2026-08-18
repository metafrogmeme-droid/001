"""Tell a model's reasoning apart from the tag stapled in front of it.

`analyzer.analyze()` builds every idea's ``reasoning`` by prefixing a machine
provenance tag to whatever the model said::

    f"[{source}|{regime}|{mode}|{strategy}|C={confluence:.2f}{mtf}{sm}] "
    f"{thesis.get('reasoning', '')}"

The model does not always say anything. `_parse_llm_response` returns
``reasoning=""`` whenever a response carries a direction and a confidence but no
reasoning — JSON without the key, or plain text with DIRECTION and CONFIDENCE
lines and no REASONING line. Both of those set ``_parsed=True``, both survive
the C-07 invalid-direction guard, and what reaches the display is::

    "[gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up] "

A non-empty, truthy string containing no reasoning at all. Every guard on the
way is spelled ``if idea.reasoning:`` and every one of them passes.

SEVEN SURFACES PRINTED IT UNDER A HEADING THAT CLAIMED OTHERWISE: the idea
card's blockquote, the verdict card's blockquote, the ``/explain`` card — a
command whose entire subject is the reason — the scan push to the website
dashboard, the signal-card text fallback, Guardian's ``Thesis:`` line in a fill
explanation, and the Reasoning row on the sealed public receipt, which exists
specifically so the reason would not have to be taken on trust.

That is this repository's own rule, one field over from where it usually bites:
absent is never a measurement. An unreadable price must not render as
``0.00%``, and a model that gave no reason must not render as a model that
reasoned. `thesis_prose()` returns ``None`` for that string so callers can omit
the block instead of dressing the tag up as an explanation.

The tag itself is true, and nothing here strips it from what is STORED — it
stays in the audit record, in the Flight Recorder, and inside the v4 seal,
where a commitment should be maximal. It is only the label that lies.
"""

from __future__ import annotations

import re
from typing import Optional

# Matched by SHAPE, not by naming the fields, so adding a segment to the tag in
# analyzer.py cannot quietly stop the strip from matching. The pipe-separated
# interior is required: a model that opens with "[worth noting] the trend is..."
# has no pipe inside the brackets and is left exactly as it wrote it.
_PROVENANCE = re.compile(r"^\s*\[[^\[\]|]*\|[^\[\]]*\]\s*")


def thesis_prose(reasoning: object) -> Optional[str]:
    """The model's own words, or ``None`` when the string is provenance only.

    ``None`` rather than ``""`` on purpose. A caller has to be able to tell
    "no reason was recorded" from a reason it happens to render as empty, and
    ``if prose:`` agreeing with ``if prose is not None:`` here is an accident
    of this particular return value, not a property callers may lean on.
    Test ``is None``.
    """
    if reasoning is None:
        return None
    body = _PROVENANCE.sub("", str(reasoning), count=1).strip()
    return body or None


def provenance_tag(reasoning: object) -> Optional[str]:
    """The bracketed tag's interior — ``gpt-4o|TREND_UP|swing|…`` — or ``None``.

    Kept beside the prose rather than thrown away: on a card whose subject is
    disclosure, the source and confluence behind a call are worth showing. They
    are simply not an explanation, so they get their own label.
    """
    if reasoning is None:
        return None
    m = _PROVENANCE.match(str(reasoning))
    if not m:
        return None
    return m.group(0).strip().strip("[]").strip() or None
