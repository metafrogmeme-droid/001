"""Sentences the Hyperliquid watch sends. Advisory, and not a verdict."""

from __future__ import annotations

NOT_FOUND = "Not found."
NOT_AVAILABLE = "not available yet"
NO_CHAT = "This chat has no id, so nothing was stored."
ADVISORY = (
    "Claw Watch, Hyperliquid. Advisory only. "
    "This note does not stop a transfer and does not revoke anything."
)
NEVER_ASK = (
    "This bot does not ask for a seed phrase or a private key, and it does not sign."
)
NO_CHANGE = "No change since the last read."
BASELINE_QUIET = "Baseline read. Nothing further was listed."
HOST_TESTNET = "Info host: Hyperliquid testnet."
HOST_MAINNET = "Info host: Hyperliquid mainnet, read-only."
CLAW_UNREAD = "Claw Watch addresses could not be read, so they were not included."
STOPPED = "Removed. No Hyperliquid watch address or snapshot is stored for this chat."
STOPPED_EMPTY = "Nothing was stored for this chat."
HOW = "Send an address to store it. Send stop to delete every row for this chat. Send now to read once."
