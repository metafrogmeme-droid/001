"""The War Room menu, the legacy dashboard keyboard and the dashboard link — a leaf out of the handler.

Three module-level objects the entry commands and the callback handler
share: `_KB_WARROOM`, the inline menu under /start and /status;
`_KB_DASH`, the pane keyboard /dashboard keeps for compatibility; and
`_dashboard_url`, the web deep-link /start surfaces, built on the same
WEBSITE_URL the rest of the bot uses so the bot and the web stay pointed at
one origin.

A leaf, not a mixin: nothing here reads `self`. They moved here because the
start-here group left the handler for a mixin while `_handle_callback`
stayed, both read them, and a mixin must not import from the handler.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.site_url import site_url

_KB_WARROOM = InlineKeyboardMarkup([
    [InlineKeyboardButton("Scan Market", callback_data="open_warroom"),
     InlineKeyboardButton("Latest Signal", callback_data="latest_signal")],
    [InlineKeyboardButton("Positions", callback_data="positions"),
     InlineKeyboardButton("Performance", callback_data="performance")],
    [InlineKeyboardButton("Orders", callback_data="orders"),
     InlineKeyboardButton("Risk", callback_data="risk_control")],
    [InlineKeyboardButton("Stop Bot", callback_data="risk_emergency_stop")],
])


_KB_DASH = InlineKeyboardMarkup([
    [InlineKeyboardButton("Status", callback_data="pane:status"),
     InlineKeyboardButton("Risk", callback_data="pane:risk")],
    [InlineKeyboardButton("Portfolio", callback_data="pane:portfolio"),
     InlineKeyboardButton("Scan", callback_data="pane:scan")],
])


def _dashboard_url() -> str:
    """The web dashboard deep-link surfaced in /start. Reuses the same
    WEBSITE_URL env + default the rest of the bot uses (user_middleware,
    website_sync) so the bot and web stay pointed at one origin."""
    base = site_url()
    return f"{base}/dashboard#home"
