"""Why a stop-loss could not be placed — one reading, one sentence.

THE ABORT THAT NAMES NO CAUSE. When `_place_sl_tp` comes back empty the
executor flattens the position it has just opened and tells the operator:

    ⚠️ EXECUTION ABORTED — ARB/USDT
    Position opened but the stop-loss could not be placed, so it was CLOSED
    for safety.

— and its two siblings (URGENT, when the safety flatten also failed; KEPT
OPEN, when the close did not complete) say as little. None of the three names
WHY, though `_note_sltp_error` recorded it seconds earlier and five other
surfaces already read it back. That is `_leverage_field_phrase`'s lesson one
control over: *"Exchange stuck at 20x" does not say what to go and change*.
A venue refusing the trigger price, a reduce-only leg under the minimum size,
an API key without futures permission and a network blip are four different
remedies, and the card that announces the abort gave the operator one
sentence for all of them.

THREE READERS, THREE SENTENCES, AND ONE OF THEM UNESCAPED. The fact already
had three renderings, disagreeing about everything a rendering can disagree
about:

    /positions              venue said: <code>{why[:120]}</code>   escaped
    the unprotected card    Venue reason: <code>{why}</code>       NOT escaped
    the proactive monitor   Venue rejected the stop: <code>…[:160]</code>  escaped

The unescaped one is the `app/lib/esc.js` failure in Python: a rejection
carrying `<` makes Telegram refuse the WHOLE message, and the send
chokepoint's fallback then strips every tag — so the card arrives without its
markup, or not at all, on the one message that says a real position is naked.

AND "THE VENUE SAID" IS FALSE FOR MOST OF WHAT IS RECORDED. Driven over every
`_note_sltp_error` call site, the store holds `str(exc)` from a ccxt
`create_order` (a NetworkError is nobody saying anything), the v3 client's
`{code}: {msg}` (the venue), `"success code but no order id returned"` (the
BOT's own reading of a venue response) and `f"exception: {exc}"` (ours). Three
of the four are not the venue speaking, and three surfaces stated it as fact.
So the sentence here is source-neutral — *stop placement was refused* is true
of all of them — and it is one sentence rather than three.

The reading is THREE-VALUED for the reason everything here is: a refusal
nobody recorded a reason for is not a refusal with no reason, and neither is
a stop that was placed. `unrecorded` gets its own words on the abort cards,
because an abort card that simply says nothing about the cause reads as an
abort that had none.
"""
from __future__ import annotations

import html
from typing import Optional

#: One truncation limit, where there were three (120 / none / 160). Long
#: enough for a venue error code plus its message; short enough that a driver
#: echoing a request body cannot fill a card with it.
REASON_MAX = 160


def venue_reason(reason: object) -> Optional[str]:
    """The recorded refusal, escaped and truncated once, or None.

    None means NOTHING WAS RECORDED — never "there was no reason". The escape
    happens here so no caller has to remember it; the one caller that forgot
    published a venue string as markup.
    """
    if reason is None:
        return None
    try:
        text = str(reason).strip()
    except Exception:                     # pragma: no cover - str() on a mock
        return None
    if not text:
        return None
    return html.escape(text[:REASON_MAX])


def refusal_line(reason: object) -> str:
    """One line naming the refusal, or naming that nobody recorded one.

    Source-NEUTRAL: `_note_sltp_error` holds the venue's words for some
    refusals and the bot's own for others, and a line that says "the venue
    said" about `success code but no order id returned` is a confident wrong
    attribution on a card an operator reads to decide what to change.

    Always a sentence, never "" — the empty string is what let three cards
    announce an abort and say nothing whatever about its cause.
    """
    safe = venue_reason(reason)
    if safe is None:
        return ("Stop placement was refused and no reason was recorded — "
                "check the venue's order history for this symbol.")
    return f"Stop placement was refused: <code>{safe}</code>"


def refusal_suffix(reason: object) -> str:
    """`refusal_line` as a trailing line for a card that already has a head.

    Separate from `refusal_line` only in that it carries its own newline, so
    a caller composing an f-string cannot produce a blank line when there is
    nothing to say — there is always something to say, which is the point.
    """
    return "\n" + refusal_line(reason)
