"""Grounded Reputation v0.1.

Counts of ERC-8004 ratings whose cited payment can be checked, and counts of
those that cannot. Admin-only, off unless GROUNDED_REPUTATION_ENABLED is set.
No verdict is produced.
"""
from bot.grounded_reputation.chains import chain_allowed, default_escrows, usdc_token
from bot.grounded_reputation.decode import authorization_topic, decode_receipt, transfer_topic
from bot.grounded_reputation.files import inspect_file
from bot.grounded_reputation.flag import FLAG, flag_on, mainnet_read_on
from bot.grounded_reputation.indexer import apply_logs, fetch_logs, index_chain
from bot.grounded_reputation.match import ESCROW_LABEL, RULE_VERSION, classify_feedbacks
from bot.grounded_reputation.reading import FOOTER, LIMITS, build_reading, reading_from_payload
from bot.grounded_reputation.signals import graph_signals
from bot.grounded_reputation.store import connect, ensure, load_payload, open_read, postgres_ddl, save_snapshot
from bot.grounded_reputation.wallet import build_wallet_spans, wallet_at

__all__ = [
    "ESCROW_LABEL",
    "FLAG",
    "FOOTER",
    "LIMITS",
    "RULE_VERSION",
    "apply_logs",
    "authorization_topic",
    "build_reading",
    "build_wallet_spans",
    "chain_allowed",
    "classify_feedbacks",
    "connect",
    "decode_receipt",
    "default_escrows",
    "ensure",
    "fetch_logs",
    "flag_on",
    "graph_signals",
    "index_chain",
    "inspect_file",
    "load_payload",
    "mainnet_read_on",
    "open_read",
    "postgres_ddl",
    "reading_from_payload",
    "save_snapshot",
    "transfer_topic",
    "usdc_token",
    "wallet_at",
]
