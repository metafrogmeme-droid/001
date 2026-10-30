"""The only user-facing verdict sentences.

Three strings, built here and nowhere else:

* ``ALLOW_TEXT`` is exact.
* ``BLOCK_PREFIX`` plus a reason.
* ``CONFIRM_PREFIX`` plus a reason. ``CONFIRM_PREFIX`` is the one confirm
  sentence. Swap it here and every confirm follows.

Nothing in this module says a project is safe or a scam. A reason is the
user's own limit.
"""

from __future__ import annotations

ALLOW_TEXT = "Allowed: within your limits"
BLOCK_PREFIX = "Blocked by your rule: "
CONFIRM_PREFIX = "Needs your OK: "


def blocked(reason: str) -> str:
    """A block sentence. The reason does not repeat the prefix."""
    return BLOCK_PREFIX + reason


def needs_ok(reason: str) -> str:
    """A confirm sentence. The reason does not repeat the prefix."""
    return CONFIRM_PREFIX + reason
