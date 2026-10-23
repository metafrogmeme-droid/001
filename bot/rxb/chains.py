"""Which chain ids this process may sign on.

Reading mainnet is a separate question and is allowed. Signing, and any
sentinel broadcast, is not. An anvil fork that keeps chain id 1 is refused:
the allowlist is the id the signature would be valid for, not the label
somebody put on the process.
"""

from __future__ import annotations

#: Local forks and the public testnets this deployment is willing to sign on.
ALLOWED_CHAIN_IDS = frozenset({31337, 1337, 11155111, 84532, 46630})

#: Named so a test can refuse them by id, not by a comment.
MAINNET_CHAIN_IDS = frozenset({1, 8453, 4663})

#: Indexer read order. Reading these ids is allowed. Signing on them is not.
READ_ORDER = (
    ("ethereum", 1),
    ("robinhood", 4663),
    ("base", 8453),
)


def signing_allowed(chain_id: object) -> bool:
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        return False
    return chain_id in ALLOWED_CHAIN_IDS
