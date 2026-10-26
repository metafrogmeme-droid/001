"""Cancel links belong to the on-chain module. That module is not built.

The interface is the whole of the stub: callers ask, and the answer is that
there is no transaction to sign. A later module can replace this function.
It must not grow a relay or a stored signature.
"""

from __future__ import annotations

from typing import Any, Optional


def cancel_link(event: Optional[object] = None) -> dict[str, Any]:
    """No cancel transaction. ``event`` is accepted so the later module has a place to look."""
    del event
    return {
        "available": False,
        "reason": "module_not_installed",
        "tx": None,
        "url": None,
    }
