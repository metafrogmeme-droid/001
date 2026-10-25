"""Per-user Claw Watch heuristics. Read-only, testnet or local fork.

The public reader is ``flag_on``. Scoring does not touch the network.
A poll does, and only when a testnet RPC URL is set and the weekly
budget still has room.
"""

from bot.claw_watch_heuristics.flag import FLAG, flag_on

__all__ = ["FLAG", "flag_on"]
