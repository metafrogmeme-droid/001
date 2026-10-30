"""Per-order checks. Testnet only. Off unless CLAW_GUARD_ENABLED is set.

The Guard holds the trade-only key and signs the payload it has just
checked. The bot sends an intent. Main-key and wallet-level actions are
not this package.
"""

from bot.claw_guard.flag import FLAG, flag_on
from bot.claw_guard.verdicts import ALLOW_TEXT, BLOCK_PREFIX, CONFIRM_PREFIX

__all__ = ["ALLOW_TEXT", "BLOCK_PREFIX", "CONFIRM_PREFIX", "FLAG", "flag_on"]
