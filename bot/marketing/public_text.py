"""The marketing forwarder's own docstring says "no sensitive data. Public-
facing marketing content only." Nothing enforced it, and two of its five post
methods carried dollar amounts to public Telegram groups.

    post_trade_closed(msg)      # "PnL: +$12.3456 (+1.23%)"  — every close
    post_daily_report(summary)  # "Net PnL: $+412.90", "Best: BTC (+$88.10)"

§4: public surfaces carry percent / ratio / count only. Prices are a public
market fact and stay — which is the whole difficulty, because `post_signal`
and `post_trade_opened` legitimately publish `Entry: $63,500.0000` and a
scrubber that cannot tell a price from a P&L would break the two posts that
were already correct.

A dollar amount alone does not say which it is. Its LABEL does. So the rule
here is label-driven and strict in the direction that costs nothing to be
wrong about:

    a $-amount survives only on a line whose label is a known PRICE label;
    every other $-amount is removed.

An unrecognized label means we do not know it is a price, and on a public
surface "we don't know" resolves to "don't publish it". A future post method
that invents a new money field is therefore scrubbed by default rather than
leaking until someone notices.

Removal is not the fix, though — it is the backstop. `_post` logs CRITICAL
with the count whenever it removes anything, because a scrubber that silently
cleans up after its callers just relocates the bug. The callers compose public
text; this makes sure they did.
"""
from __future__ import annotations

import html
import re
from typing import Any, Mapping, Optional

# `$1,234.56`, `-$0.5`, `$+412.90`, `$ 12` — the sign sits on either side of
# the symbol depending on which f-string built it.
_MONEY = re.compile(r"[-+]?\$\s?[-+]?\d[\d,]*(?:\.\d+)?")

# A line whose value is a price. Leading HTML tags and emoji are skipped so
# `<b>Entry</b>: <code>$63,500</code>` still reads as a price line.
_PRICE_LABEL = re.compile(
    r"^[^A-Za-z]*(?:<[^>]*>[^A-Za-z<]*)*"
    r"(entry|exit|stop\s*loss|stop|take\s*profit|target|price|mark|last|"
    r"liq(?:uidation)?|sl|tp|avg|average)\b",
    re.IGNORECASE,
)

# Left behind by removal: `<code></code>`, `()`, `( | )`.
_EMPTY_TAG = re.compile(r"<(\w+)[^>]*>\s*</\1>")
_EMPTY_PAREN = re.compile(r"\(\s*[|,;:/\-]*\s*\)")

# THE UNIT IS A FIELD, NOT A LINE, AND THE CARDS ARE WHY.
#
# Every rule above is about ONE label and ONE value, and it was applied to a
# whole LINE — while `live_executor`'s close card puts three labelled fields on
# one of them:
#
#     PnL: -$0.1354 (-1.55% margin / -0.31% notional, 5x) | Fees: $0.14 | Hold: 0m
#
# Driven, that published as `PnL: (-1.55% …) | Fees: | Hold: 0m` — the bare
# `Fees:` label this module's own `_strip_line` docstring calls "its own small
# dishonesty", because the drop-the-line rule only fires when the WHOLE line
# empties and here one field of three did.
#
# The other direction is worse and was silent. `_is_price_line` anchors at the
# start, so a price label acquitted every figure after it:
#
#     Exit: $0.4198 | PnL: -$0.1354      ->  0 removed, published verbatim
#
# `scrub_money`'s own docstring says a count of zero means "the caller already
# composed public text", so the CRITICAL log that exists to name a caller
# composing private text does not fire either — the backstop reporting success
# over the leak. `/broadcast` reaches it with arbitrary admin text today, and
# this module's header promises that "a future post method that invents a new
# money field is therefore scrubbed by default rather than leaking until
# someone notices", which was false for any field following a price label.
#
# So a line is cut into the fields the cards actually compose, each field is
# judged by ITS OWN label, and a field that loses its value loses its label
# with it.
_FIELD_SEP = re.compile(r"(\s*(?:\||→|->)\s*)")


def _is_price_line(line: str) -> bool:
    """True when this field's own label says its value is a price.

    Named for the line it used to take because every caller still reads it
    that way for a single-field line, which is most of them.
    """
    return bool(_PRICE_LABEL.match(line))


def _strip_field(field: str) -> Optional[str]:
    """The field with money removed, or None when only a label is left.

    "Net PnL: <code>$412.90</code>" has nothing to say once the figure is
    gone, and posting the bare label would be its own small dishonesty — it
    announces a number the reader cannot see. "PnL: +$12.34 (+1.23%)" still
    has the percent and survives.
    """
    field = _MONEY.sub("", field)
    field = _EMPTY_TAG.sub("", field)
    field = _EMPTY_PAREN.sub("", field)
    # `Trades: 4 |  | Risk: Healthy` — collapse the gap a removal opened.
    field = re.sub(r"\s{2,}", " ", field).rstrip()
    # Test the VALUE side of the label, before trailing punctuation is
    # trimmed — trimming first turns "Net PnL:" into "Net PnL", which reads
    # as content and kept every emptied line.
    tail = field.split(":", 1)[1] if ":" in field else field
    if not re.search(r"[0-9A-Za-z]", tail):
        return None
    return re.sub(r"[|,;:\-]\s*$", "", field).rstrip()


def _strip_line(line: str) -> Optional[str]:
    """The line with every non-price field scrubbed, or None when none survive.

    A field whose own label is a price label is kept VERBATIM, which is what
    makes `post_signal`'s `Entry:`/`Stop Loss:`/`Take Profit:` lines survive.
    A field is delimited by the separators the cards compose with — `|` and
    the entry-to-exit arrow — and its label is what stands before its first
    colon; a SECOND label nested inside one field is not parsed, and no
    producer in this tree writes one, so that bound is stated rather than
    guessed at.
    """
    parts = _FIELD_SEP.split(line)
    # `split` on a capturing group alternates value, separator, value, ...
    fields, seps = parts[0::2], parts[1::2]
    kept: list[str] = []
    for i, field in enumerate(fields):
        if _is_price_line(field) or not _MONEY.search(field):
            kept.append((seps[i - 1] if i and kept else "") + field)
            continue
        cleaned = _strip_field(field)
        if cleaned is not None:
            kept.append((seps[i - 1] if i and kept else "") + cleaned)
    joined = "".join(kept)
    return joined or None


def scrub_money(text: str) -> tuple[str, int]:
    """Return the text with non-price dollar amounts removed, and how many.

    The count is the point of the return tuple: zero means the caller already
    composed public text, and anything else is a caller to fix, not a success.
    It counts what was REMOVED, so a figure a price field keeps is not in it —
    which is what lets a `post_signal` line of three prices stay a zero.
    """
    if not text:
        return text or "", 0
    removed = 0
    out: list[str] = []
    for line in text.split("\n"):
        if not _MONEY.search(line):
            out.append(line)
            continue
        cleaned = _strip_line(line)
        removed += len(_MONEY.findall(line)) - len(_MONEY.findall(cleaned or ""))
        if cleaned is not None:
            out.append(cleaned)
    return "\n".join(out), removed


def _num(d: Mapping[str, Any], key: str) -> Optional[float]:
    try:
        v = d.get(key)
        if v is None:
            return None
        f = float(v)
    except (AttributeError, TypeError, ValueError):
        return None
    return None if (f != f or f in (float("inf"), float("-inf"))) else f


def close_outcome(close_data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """``"win"`` / ``"loss"`` / ``"flat"`` by the close's NET result, or None.

    The one reading of a close's sign for every public icon: the line's dot
    below and the forwarder's headline (``post_trade_closed``), which read a
    regex for any ``+N%`` in the text and put a trophy on a 20x close that
    moved +0.10% and lost 0.40% on margin after fees. ``pnl_usd`` is net and
    its SIGN is readable even when the margin was never recorded; the net
    return on margin says the same thing when the dollars were not read. Both
    absent is a close nobody priced, and that is not a win or a loss. Read,
    never printed (§4).
    """
    if not close_data:
        return None
    net = _num(close_data, "pnl_usd")
    if net is None:
        net = _num(close_data, "pnl_pct_margin_net")
    if net is None:
        return None
    return "win" if net > 0 else "loss" if net < 0 else "flat"


def public_close_line(close_data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """A close, told in percent — or None when the record cannot tell it.

    Returning None hands the forwarder the private text and lets the scrubber
    above handle it, which is worse-looking but never wrong. Inventing a 0.00%
    for a close whose percentage was never computed is the failure mode this
    repo's doctrine opens with.

    THIS FUNCTION BUILDS HTML, SO IT ESCAPES WHAT IT INTERPOLATES. It did not,
    and the forwarder escaped the whole finished string instead — turning the
    `<b>` and `<code>` below into `&lt;b&gt;` and printing the tags to a public
    channel:

        🔴 <b>CLUSDT</b> LONG closed (leverage overshoot)
        Move: <code>-0.08%</code> | on margin <code>-1.67%</code>

    The escape was guarding something real (`sym`, `direction` and `reason` all
    arrive from outside and none were escaped), it was just doing it a layer
    too late — where it could no longer tell a tag this function wrote from a
    bracket a venue supplied. Escaping the fields here lets the forwarder stop
    escaping the message, which is the fix.
    """
    if not close_data:
        return None
    sym = str(close_data.get("symbol") or "").replace("/", "").replace(":USDT", "")
    if not sym:
        return None
    pct = _num(close_data, "pnl_pct")
    if pct is None:
        return None
    sym = html.escape(sym)
    direction = html.escape(str(close_data.get("direction") or "").upper())
    # TRUNCATE, THEN ESCAPE. The other order cuts an entity in half — a reason
    # carrying `&` becomes `&amp;`, and a 48-character slice through that
    # leaves `&am`, which is malformed markup rather than a shortened word.
    reason = html.escape(
        str(close_data.get("reason") or "").replace("_", " ").strip()[:48])
    # COLOUR IS A CLAIM, AND THE CLAIM IS ABOUT THE ACCOUNT, NOT THE CHART.
    # Keyed on the price move, a close whose move was positive but whose fees
    # ate it renders green on a public channel — and at 20x the fees are 0.12%
    # of notional, so any move smaller than that flips the sign. The NET
    # decides (`close_outcome`), and a close whose net nobody measured is
    # neither colour: it used to fall back to the gross move here, which is
    # the chart again.
    icon = {"win": "\U0001f7e2", "loss": "\U0001f534"}.get(
        close_outcome(close_data) or "", "⚪")

    head = f"{icon} <b>{sym}</b> {direction} closed".rstrip()
    if reason:
        head += f" ({reason})"

    parts = [f"Move: <code>{pct:+.2f}%</code>"]
    # THE MARGIN FIGURE IS THE NET ONE OR IT IS NOT PUBLISHED.
    # This read `pnl_pct_margin`, which `close_pct` builds as price-move x
    # leverage and which therefore contains no fees at all. The omission is a
    # constant — fees/margin is 2 * fee_pct * leverage, so 1.6-2.4% of margin
    # at 20x — and it runs the flattering way every time. The live CLUSDT card
    # published `on margin -1.67%` on a limit entry whose fee drag alone was
    # about 1.6%: roughly half the loss, missing, on a public channel.
    lev = _num(close_data, "pnl_pct_margin_net")
    if lev is not None and abs(lev - pct) > 0.005:
        parts.append(f"on margin <code>{lev:+.2f}%</code>")
    else:
        # OMIT, not substitute. A composite line where one dead source must not
        # blank the rest — so the leverage still goes out, because that is a
        # FACT about the position rather than a measurement of its return, and
        # without it a 20x close reads as a 0.08% nothing.
        _lv = _num(close_data, "leverage")
        if _lv is not None and _lv > 1:
            parts.append(f"at <code>{_lv:.0f}×</code>")
    hold = str(close_data.get("hold_time") or "").strip()
    if hold:
        parts.append(f"Hold: <code>{hold}</code>")
    return head + "\n" + " | ".join(parts)
