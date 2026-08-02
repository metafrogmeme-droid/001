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


def _is_price_line(line: str) -> bool:
    return bool(_PRICE_LABEL.match(line))


def _strip_line(line: str) -> Optional[str]:
    """The line with money removed, or None when only a label is left.

    "Net PnL: <code>$412.90</code>" has nothing to say once the figure is
    gone, and posting the bare label would be its own small dishonesty — it
    announces a number the reader cannot see. "PnL: +$12.34 (+1.23%)" still
    has the percent and survives.
    """
    line = _MONEY.sub("", line)
    line = _EMPTY_TAG.sub("", line)
    line = _EMPTY_PAREN.sub("", line)
    # `Trades: 4 |  | Risk: Healthy` — collapse the gap a removal opened.
    line = re.sub(r"\s{2,}", " ", line)
    line = re.sub(r"(?:\|\s*){2,}", "| ", line).rstrip()
    # Test the VALUE side of the label, before trailing punctuation is
    # trimmed — trimming first turns "Net PnL:" into "Net PnL", which reads
    # as content and kept every emptied line.
    tail = line.split(":", 1)[1] if ":" in line else line
    if not re.search(r"[0-9A-Za-z]", tail):
        return None
    return re.sub(r"[|,;:\-]\s*$", "", line).rstrip()


def scrub_money(text: str) -> tuple[str, int]:
    """Return the text with non-price dollar amounts removed, and how many.

    The count is the point of the return tuple: zero means the caller already
    composed public text, and anything else is a caller to fix, not a success.
    """
    if not text:
        return text or "", 0
    removed = 0
    out: list[str] = []
    for line in text.split("\n"):
        if _is_price_line(line):
            out.append(line)
            continue
        hits = len(_MONEY.findall(line))
        if not hits:
            out.append(line)
            continue
        removed += hits
        cleaned = _strip_line(line)
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


def public_close_line(close_data: Optional[Mapping[str, Any]]) -> Optional[str]:
    """A close, told in percent — or None when the record cannot tell it.

    Returning None hands the forwarder the private text and lets the scrubber
    above handle it, which is worse-looking but never wrong. Inventing a 0.00%
    for a close whose percentage was never computed is the failure mode this
    repo's doctrine opens with.
    """
    if not close_data:
        return None
    sym = str(close_data.get("symbol") or "").replace("/", "").replace(":USDT", "")
    if not sym:
        return None
    pct = _num(close_data, "pnl_pct")
    if pct is None:
        return None
    direction = str(close_data.get("direction") or "").upper()
    reason = str(close_data.get("reason") or "").replace("_", " ").strip()
    icon = "\U0001f7e2" if pct > 0 else "\U0001f534" if pct < 0 else "⚪"

    head = f"{icon} <b>{sym}</b> {direction} closed".rstrip()
    if reason:
        head += f" ({reason[:48]})"

    parts = [f"Move: <code>{pct:+.2f}%</code>"]
    lev = _num(close_data, "pnl_pct_margin")
    if lev is not None and abs(lev - pct) > 0.005:
        parts.append(f"on margin <code>{lev:+.2f}%</code>")
    hold = str(close_data.get("hold_time") or "").strip()
    if hold:
        parts.append(f"Hold: <code>{hold}</code>")
    return head + "\n" + " | ".join(parts)
