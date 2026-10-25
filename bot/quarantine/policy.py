"""Holdings do not change what an agent may do.

Agent Leash (``bot.guardian.authority.authorize``) drops these fields
before it reads the action. A received token or NFT is not a permission,
an allow-list entry, or a mode switch. The product flag does not turn
this off: a switch that put it back would be the bug.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# Fields a holding can arrive on. ``asset`` is the market symbol the
# envelope already checks, and is not one of these.
_HOLDING_KEYS = frozenset({
    "holdings", "nfts", "nft", "tokens", "token_metadata",
    "metadata", "collection",
})


def without_holdings(action: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """A copy of ``action`` with holding fields removed.

    A missing action is an empty dict, which the leash then refuses.
    The caller's mapping is not modified.
    """
    if not isinstance(action, Mapping):
        return {}
    return {k: v for k, v in action.items() if k not in _HOLDING_KEYS}
