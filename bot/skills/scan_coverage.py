"""What a scan actually read, kept separate from what a scan concluded.

Every batch scan in RUNECLAW fetches OHLCV for the whole universe with
`asyncio.gather`, then does this:

    results = [r for r in raw if r is not None]

`_scan_symbol` returns None on a failed fetch and on a symbol with too few
candles. So that one line silently deletes every symbol the exchange would
not serve, and the survivors are then presented as the market. A pass that
read 27 of 67 symbols rendered identically to a pass that read all 67 —
same header, same ranking, same confident tone, no seam anywhere that could
tell the two apart.

The filtered scans made it worse by turning the gap into a verdict:

    if not results:
        await msg.edit_text("No swing setups found right now.")

That sentence is a claim about the market. When zero symbols were readable
it is a claim about the network, wearing the market's clothes. UNREADABLE IS
NOT ZERO — the distinction this codebase has had to relearn on the drawdown
peak, on the journal window, and on the LLM token counter.

Two rules, and this module exists so they can be asserted rather than
described:

  1. A partial read says so. Complete coverage says nothing — a note on
     every healthy scan is noise, and noise is how a real warning gets
     skipped.
  2. Zero readable is never phrased as a market verdict. "Nothing matched"
     and "nothing was read" are different facts and get different sentences.
"""
from __future__ import annotations


def coverage(total: int, readable: int) -> dict:
    """Counts for one scan pass. Never raises; clamps nonsense inputs.

    ``complete`` is the only thing callers should branch on for "may I speak
    with the market's authority?" — it is True only when every symbol in the
    universe produced data.
    """
    try:
        t = max(0, int(total or 0))
        r = max(0, int(readable or 0))
    except (TypeError, ValueError):
        t = r = 0
    r = min(r, t)
    return {
        "total": t,
        "readable": r,
        "unreadable": t - r,
        "complete": t > 0 and r == t,
        # Absent rather than zero when there is no universe to divide by:
        # 0/0 is not "0% coverage", it is "no question was asked".
        "pct": round(r / t * 100.0, 1) if t > 0 else None,
    }


def coverage_note(total: int, readable: int, *, html: bool = True) -> str:
    """A one-line disclosure for a partial pass, or "" when the pass was whole.

    Deliberately empty on complete coverage. A scan that read everything has
    nothing to disclose, and a banner printed on every healthy scan trains the
    reader to skip the one that matters.
    """
    c = coverage(total, readable)
    if c["total"] == 0 or c["complete"]:
        return ""
    if c["readable"] == 0:
        # No ranking exists to qualify — the caller should be using
        # unreadable_verdict() instead. Say the true thing anyway.
        body = (f"Read 0 of {c['total']} symbols this pass. "
                f"Nothing below is a reading of the market.")
    else:
        body = (f"Read {c['readable']} of {c['total']} symbols — "
                f"{c['unreadable']} did not return usable candles this pass. "
                f"The ranking covers what was read, not the whole universe.")
    return f"\n\n⚠️ <i>{body}</i>" if html else f"\n\n{body}"


def unreadable_verdict(label: str, total: int) -> str:
    """The message for a pass that read nothing at all.

    Never phrased as a market condition. On 2026-07-31 the filtered scans
    answered a total fetch failure with "No swing setups found right now",
    which is the shape of an answer and the substance of a silence.
    """
    lab = str(label or "scan").strip() or "scan"
    n = max(0, int(total or 0))
    return (f"🔴 <b>{lab} could not read the market.</b> "
            f"0 of {n} symbols returned usable candles this pass — that is a "
            f"data-availability failure, not a quiet tape. Nothing here says "
            f"whether setups exist. Try again in a moment; "
            f"<code>/status</code> carries the engine's own measured "
            f"fetch timing.")


def empty_result_line(label: str, total: int, readable: int) -> str:
    """Distinguish "nothing matched" from "nothing was read".

    Both produce an empty result list. Only one of them entitles the surface
    to speak about the market.
    """
    c = coverage(total, readable)
    if c["readable"] == 0:
        return unreadable_verdict(label, c["total"])
    lab = str(label or "scan").strip() or "scan"
    line = f"No {lab} setups matched across {c['readable']} readable symbols."
    if not c["complete"]:
        line += (f" {c['unreadable']} of {c['total']} were unreadable this "
                 f"pass, so this is not a statement about the full universe.")
    return line
