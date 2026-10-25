"""Silent Fee Check, a read-only module of Panic Button.

Public Hyperliquid info reads for one address: approved builder caps, the
referrer, the role, sub-accounts, the fee schedule, and builder-fee totals
from fills. Per-builder attribution uses the explorer RPC and stays off
unless its own flag is on. Nothing here signs, approves, or holds a key.
"""
