"""Exit-adjusted NAV for a Hyperliquid vault.

Off unless ``XNAV_ENABLED`` is set. Testnet signing only. A mainnet chain
id is refused before a key is read.

The website ranks with the JS walk. This package is the Python door for
the same haircut: the RXB node, the published snapshot, and the signer.
"""
from bot.xnav.config import l1_read_gas, placeholders
from bot.xnav.flag import FLAG, flag_on
from bot.xnav.graph import vault_exit_node
from bot.xnav.reading import load_snapshot
from bot.xnav.sign import digest, load_signing_key, parse_address, sign_update, typehash

__all__ = [
    "FLAG",
    "digest",
    "flag_on",
    "l1_read_gas",
    "load_signing_key",
    "load_snapshot",
    "parse_address",
    "placeholders",
    "sign_update",
    "typehash",
    "vault_exit_node",
]
