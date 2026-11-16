"""Which API family a Bitget account answers: unified (``/api/v3``) or Classic
(``/api/v2/mix``).

Every Bitget client the bot built was marked ``options["uta"] = True``. An
account in Classic mode refuses each unified call with code 40084 ("You are
in Classic Account mode, and the Unified Account API is not supported"), so
its balance read, its position reads and the startup credential check all
failed, and the check reported the refusal as a rejected key.

The reading kept here is the venue's own statement and nothing else: an
account is Classic once Bitget has answered 40084 to it, and unified, as
before, until then. No probe decides it, so an account that never hears
40084 takes the path it always took. It is remembered per account for the
life of the process under a digest of the client's API key: the key is
neither stored nor logged, and a rotated key starts unknown again.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from bot.core.order_state import venue_code

#: Bitget's refusal of a unified-account call made by a Classic account.
CLASSIC_ACCOUNT_CODE = "40084"

#: Digests of the API keys whose account the venue has said is Classic.
_classic_accounts: set[str] = set()


def _slot(api_key: Any) -> Optional[str]:
    """The cache slot for an API key, or None when there is no key to name
    an account by (a test stand-in, an unconfigured client)."""
    if not isinstance(api_key, str) or not api_key:
        return None
    return hashlib.sha256(api_key.encode()).hexdigest()


def is_classic_refusal(err: Any) -> bool:
    """Whether ``err`` (an exception or its text) is Bitget's 40084.

    Read from the reply's ``code`` field (`venue_code`), never as a substring
    of the whole text: ``requestTime`` is thirteen digits, and five of them
    can spell any code.
    """
    return venue_code(err) == CLASSIC_ACCOUNT_CODE


def is_classic(api_key: Any) -> bool:
    """Whether the venue has said this key's account is in Classic mode."""
    slot = _slot(api_key)
    return slot is not None and slot in _classic_accounts


def uta_option(api_key: Any) -> bool:
    """The ``options["uta"]`` a new client for this key is built with: True,
    as every client was, until the venue has said the account is Classic."""
    return not is_classic(api_key)


def note_classic_refusal(client: Any, err: Any) -> bool:
    """Record a 40084 against ``client``'s account and make that client speak
    the classic family. True when ``err`` was that refusal and the client was
    switched, so the caller may ask once more.

    The account is the one the REFUSED client was built for (its own
    ``apiKey``), not the configuration's current key, which may already be a
    different one. A stand-in without a string key or a dict of options is
    neither recorded nor written.
    """
    if not is_classic_refusal(err):
        return False
    opts = getattr(client, "options", None)
    slot = _slot(getattr(client, "apiKey", None))
    if not isinstance(opts, dict) or slot is None:
        return False
    _classic_accounts.add(slot)
    opts["uta"] = False
    return True


async def balance_in_account_family(client: Any, params: dict) -> Any:
    """``client.fetch_balance(params)``, asked once more on the classic family
    when the venue refuses the unified one. Any other failure is raised as it
    was: a rejected key is still a rejected key."""
    try:
        return await client.fetch_balance(params)
    except Exception as exc:
        if not note_classic_refusal(client, exc):
            raise
        return await client.fetch_balance(params)


def _reset() -> None:
    """Forget every account. For the test harness (`tests/conftest.py`)."""
    _classic_accounts.clear()
