"""One Claw Watch note. The advisory lines are on every alert."""

from __future__ import annotations

from bot.approval_hygiene import short_address
from bot.drain_watch.cancel import cancel_link
from bot.drain_watch.copy import ADVISORY, CANCEL_STUB, NEVER_ASK, OFFLINE_TX, PREVIEW, page_url
from bot.drain_watch.tx import format_tx

# Telegram's own cap is 4096. Stay under it without cutting the transaction
# bytes: a truncated revoke is a revoke that does not revoke.
ALERT_MAX = 3500

def _cancel_line(event) -> str:
    """The stub, unless a later module returns a page on our own host."""
    link = cancel_link(event)
    url = link.get("url") if link.get("available") is True else None
    if isinstance(url, str) and url.startswith("https://www.humanoid-traders.com/"):
        return url
    return CANCEL_STUB


_SIGNAL_LINES = (
    ("exploit_list", "exploit list"),
    ("approval_age_days", "approval age in days"),
    ("unlimited", "unlimited"),
    ("delegate_family", "delegate code family"),
    ("claw_guard", "Claw Guard"),
)


def render_alert(event, *, preview: bool = False, env=None) -> list[str]:
    """One or two messages. The unsigned transaction is never sliced."""
    head = [ADVISORY, NEVER_ASK]
    if preview:
        head.append(PREVIEW)
    body = [event.summary]
    if event.token:
        body.append(f"token {short_address(event.token)}")
    if event.spender:
        body.append(f"spender {short_address(event.spender)}")
    if event.counterparty:
        body.append(f"counterparty {short_address(event.counterparty)}")
    for key, label in _SIGNAL_LINES:
        value = event.signals.get(key, "unread")
        body.append(f"{label}: {value}")
    tail = [_cancel_line(event), page_url(env)]
    tx_lines = []
    if event.tx is not None:
        tx_lines = [OFFLINE_TX, format_tx(event.tx)]
    text = "\n\n".join(head + body + tx_lines + tail)
    if len(text) <= ALERT_MAX:
        return [text]
    first = "\n\n".join(head + body + tail)
    second = "\n\n".join(head + tx_lines) if tx_lines else first
    return [first[:ALERT_MAX], second] if tx_lines else [first[:ALERT_MAX]]


def render_watch_status(addresses: list[str], *, preview: bool, watching: bool) -> str:
    lines = [ADVISORY, NEVER_ASK]
    if preview:
        lines.append(PREVIEW)
    if not addresses:
        lines.append("No address is stored for this chat.")
    else:
        shown = ", ".join(short_address(a) for a in addresses)
        lines.append(f"Stored addresses: {shown}.")
    if not watching:
        lines.append("The public flag is off. This reply is the admin preview.")
    lines.append("Send an address to store it. Send stop to delete every row for this chat.")
    lines.append(CANCEL_STUB)
    return "\n\n".join(lines)
