"""The Balance block of /livebalance, read and rendered as pure functions.

RC-2026-015. `LiveExecutor.fetch_balance()` answers a failed read with

    {"error": str(exc), "free": 0, "used": 0, "total": 0, "holdings": []}

and the card consumed it with `bal.get("total", 0)` and friends, so a rejected
API key, an IP allowlist miss, a bad nonce or a venue 5xx printed

    Cash $0.00 · Used $0.00 · Equity $0.00 · NET $0.00

— a complete account statement, with no error text, assembled from a read that
never happened. CLAUDE.md's first rule, on the card that states what the
account is worth.

WHY THE FIX IS HERE AND NOT IN `fetch_balance`. The obvious remedy — return
None instead of a zeros dict — is HARMFUL and was rejected on evidence.
`bot/main.py` classifies its startup credential preflight on exactly that
dict: `bal.get("error")` selects "STARTUP: exchange auth FAILED" and calls
`set_live_auth_status(False)`, which halts new live entries. None loses the
halt, trading an honest card for a safety regression. The error dict stays;
the CARD learns to read it.

OMIT, NOT GUARD, and that is the table in CLAUDE.md rather than a preference:
/livebalance is a COMPOSITE card. Realized PnL, fees, trade count and exposure
come from the local trade store and the executor's own book, and are perfectly
readable while the venue is unreachable. Throwing would blank all of it to
report one dead source.

`holdings` is None rather than [] when nothing was read. An empty list is a
measurement — "the venue answered and you hold no spot" — and rendering the
failed read as that claim is the same defect one section down.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bot.utils.logger import _redact_string

#: A bare URL can carry credentials in its query string and no key=value
#: redactor catches "?apiKey=..." once it is one token. Keep the host — which
#: venue is the diagnostic — and drop the rest. Same rule as
#: telegram_handler._safe_exc_text, applied to a string rather than an
#: exception, because `error` has already been through `str(exc)`.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")

UNKNOWN = "unknown"

#: Long enough to name a venue error code, short enough not to paste a body.
_REASON_LIMIT = 140


def scrub_reason(raw) -> str:
    """A venue error string, safe to show an operator.

    Never the raw text: a ccxt message carries the request URL, and on some
    venues the API key rides in that URL's query string. Routed through the
    same shared chokepoint the logger uses so there is one place to fix.
    """
    try:
        msg = str(raw or "")
    except Exception:
        return ""
    if not msg:
        return ""
    msg = _redact_string(msg)
    msg = _URL_QUERY_RE.sub(r"\1?***", msg)
    msg = " ".join(msg.split())
    return msg[:_REASON_LIMIT]


@dataclass(frozen=True)
class BalanceReading:
    """What the venue actually told us, with absent kept distinct from zero."""

    venue_answered: bool
    reason: str = ""
    free: float | None = None
    used: float | None = None
    total: float | None = None
    holdings: list | None = None

    @property
    def is_reading(self) -> bool:
        """True when there is a measurement to display at all."""
        return self.venue_answered


def read_balance(bal) -> BalanceReading:
    """Three-valued reading of `LiveExecutor.fetch_balance()`'s return.

    Three outcomes, not two: the venue answered; the venue failed and said
    why; or we were handed something that is not a balance at all.
    """
    if not isinstance(bal, dict):
        return BalanceReading(venue_answered=False,
                              reason="no balance was returned")
    err = bal.get("error")
    if err:
        return BalanceReading(venue_answered=False, reason=scrub_reason(err))

    def _num(key):
        # `.get(key, 0)` cannot tell an absent key from a reported 0.0, and
        # `free` is ALREADY three-valued upstream (RC-2026-017's
        # `_free_or_none`): the venue can answer without reporting the
        # balance coin at all. None survives; it is not coerced.
        v = bal.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    holdings = bal.get("holdings")
    return BalanceReading(
        venue_answered=True,
        free=_num("free"),
        used=_num("used"),
        total=_num("total"),
        holdings=holdings if isinstance(holdings, list) else None,
    )


def money(v: float | None) -> str:
    """`$1,234.50`, or the word — never a number standing in for no reading."""
    return UNKNOWN if v is None else f"${v:,.2f}"


def render_balance_block(reading: BalanceReading, *, exposure: float | None,
                         equity: float | None, sep: str) -> list[str]:
    """The Balance section's lines.

    `equity` is passed in rather than read off the reading because the card
    adds priced spot holdings to it; when the venue did not answer there is
    nothing to add and nothing to show.

    `exposure` is the executor's OWN book, not the venue's, so it survives a
    failed venue read and is labelled to say so.
    """
    lines = ["\U0001f4b3 <b>Balance</b>", sep]
    if not reading.is_reading:
        lines.append("- ⚠️ <i>Could not read this account from the "
                     "venue — the figures below are not available.</i>")
        if reading.reason:
            lines.append(f"- <i>Venue said:</i> <code>{reading.reason}</code>")
        lines.append(f"- Cash: <code>{UNKNOWN}</code>")
        lines.append(f"- Used: <code>{UNKNOWN}</code>")
        lines.append(f"- Equity: <code>{UNKNOWN}</code>")
        # Not from the venue. Real, and the only figure on this block that is.
        lines.append(f"- Exposure (bot-tracked): <code>{money(exposure)}</code>")
        return lines

    # `used` is the venue's; exposure is ours. The card has always shown the
    # higher of the two, which is only meaningful when both are readings.
    used_display = reading.used
    if used_display is not None and exposure is not None:
        used_display = max(used_display, exposure)
    lines.append(f"- Cash: <code>{money(reading.free)}</code>")
    lines.append(f"- Used: <code>{money(used_display)}</code>")
    lines.append(f"- Equity: <code>{money(equity)}</code>")
    lines.append(f"- Exposure: <code>{money(exposure)}</code>")
    return lines
