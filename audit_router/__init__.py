"""Contract Audit Router.

Picks a Claude model for a Solidity tree from a risk score, runs the static
tools that are installed, and writes a markdown review. The model call goes
through ``bot.llm.provider.llm_complete`` and the dollars through
``bot.core.cost``. This package does not deploy, sign, or send a transaction.
"""

from audit_router.flag import enabled
from audit_router.run import run

__all__ = ["enabled", "run"]
