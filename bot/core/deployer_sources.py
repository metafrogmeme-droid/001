"""Deployer facts from a block explorer — the source ``deployer_history`` was missing.

``bot/core/deployer_history.py`` has scored a deployer's provenance since it was
written, and nothing ever handed it any facts: ``token_research.investigate()``
passed ``deployer_report=None``, so every dossier recorded the section as **not
read** and capped at ``UNPROVEN``. That was the honest output, and it was also a
permanent one — a scorer no source feeds is a scorer that never runs. This is
the source.

WHAT AN EXPLORER CAN AND CANNOT ANSWER

Of the eight facts ``assess_deployer`` reads, a block explorer can supply five:

    contract_verified        getsourcecode → is there published source
    wallet_age_days          the deployer's first transaction
    prior_deployments        their contract-creation transactions
    concurrent_launches_24h  the same, within a day
    deployer_supply_pct      their token balance over total supply

It cannot supply ``prior_rugged`` / ``prior_alive`` — how their previous
contracts ENDED — and it cannot supply ``funded_by_mixer`` or
``reused_rug_bytecode``. Those need a price/liquidity history per contract, a
mixer address list and a rug-bytecode corpus respectively.

So this source deliberately leaves them absent, and that has a consequence
worth stating plainly: because ``_outcomes_resolved`` treats ``rugged is None``
as fatal, a dossier fed only by this source still cannot reach ``clean``. It
reaches ``unproven`` WITH CONTENT — a named deployer, a wallet age, a
deployment count, a supply share, and an explicit list of what stayed unread —
instead of ``unproven`` with nothing. Filling ``prior_rugged`` with 0 to unlock
a better-looking verdict would be the module's central defect committed by its
own supplier: "we could not determine any rugs" is not "there were none".

THE UNITS TRAP

``deployer_supply_pct`` is named ``_pct`` and is read as a FRACTION — its hard
threshold is ``0.5`` against the message "deployer holds ≥50% of supply", and
``tests/test_deployer_history.py`` passes ``0.03`` and ``0.6``. Emitting 60 for
60% would clear the hard threshold by 120× and mark every token a scam. The
docstring of ``token_research`` records the twin of this mistake — a fixture
supplying ``honeypot`` where the scorer reads ``honeypot_cannot_sell``, a
source that answered and contributed nothing. Same class, opposite direction:
this one contributes a value, and the value is wrong.

PARTIAL READS ARE OMITTED, NOT ZEROED

Each sub-request is caught on its own and contributes what it read. That is
CLAUDE.md's composite case: one dead sub-read must not blank the four that
worked. Two omissions matter more than the rest and are called out at their
sites — an unreadable transaction list must never become ``wallet_age_days: 0``
(a wallet younger than a week is a FLAG, so a failed read would manufacture
one), and a truncated list must never become a ``prior_deployments`` count (a
floor printed as a total is a partial sum presented as whole).

The one hard failure is the deployer address itself: every other fact here is a
fact ABOUT that address, so if it cannot be read this source returns nothing at
all and ``gather`` records an honest EMPTY rather than a report about nobody.

Pure-ish by construction: ``transport`` and ``now`` are injected, so the tests
drive fakes and never the network or the clock.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

#: Etherscan's V2 API is one host with a chain id, so every EVM chain the bot
#: trades is the same code path. Names match the chain strings used elsewhere
#: (``token_sources``/``meme_gate``), including the aliases.
CHAIN_IDS = {
    "eth": 1, "ethereum": 1, "mainnet": 1,
    "bsc": 56, "bnb": 56,
    "polygon": 137, "matic": 137,
    "base": 8453,
    "arbitrum": 42161, "arb": 42161,
    "optimism": 10,
    "avalanche": 43114, "avax": 43114,
}

_API = "https://api.etherscan.io/v2/api"

#: Etherscan caps `txlist` at this many records. Hitting it exactly means the
#: history is truncated and any count taken from it is a floor, not a total.
_TXLIST_MAX = 10000

_DAY_S = 86400.0


def _num(x: Any) -> Optional[float]:
    """float(x) or None — never 0.0 for a value that could not be read."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


class EtherscanDeployerSource:
    """Deployer provenance from the Etherscan V2 API.

    Requires a key. With none configured ``available()`` is False and ``gather``
    records ``unavailable`` — "we never asked" — which is a different row from
    "we asked and learned nothing", and the distinction is the reason
    ``_run_one`` checks availability before calling.
    """

    name = "etherscan"
    requires_key = True

    def __init__(self, api_key: Optional[str] = None,
                 transport: Optional[Callable] = None,
                 now: Optional[Callable[[], float]] = None) -> None:
        self._key = (api_key if api_key is not None
                     else os.getenv("ETHERSCAN_API_KEY", "")) or ""
        self._transport = transport
        self._now = now or time.time

    def available(self) -> bool:
        return bool(self._key)

    # ── transport ──────────────────────────────────────────────────────────

    async def _get(self, chain_id: int, params: dict) -> Any:
        """One API call. Returns the `result` field, or raises.

        Etherscan answers `status: "0"` for BOTH "no records" and "your key is
        rate-limited", so the message has to be read. Collapsing them would turn
        a throttled request into a confident empty history — the same shape as
        every 200-with-zeros this repo has already been bitten by.
        """
        q = dict(params)
        q["chainid"] = chain_id
        q["apikey"] = self._key
        url = f"{_API}?{urlencode(q)}"
        if self._transport is not None:
            data = await self._transport(url)
        else:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("malformed response")
        # The `proxy` module speaks JSON-RPC and carries no status/message at
        # all — it answers {jsonrpc, id, result} or {jsonrpc, id, error}. Read
        # before the status branch, or every eth_getCode looks like a failure
        # because the field it is checked on was never going to be there.
        if "jsonrpc" in data:
            if data.get("error"):
                raise RuntimeError(str(data["error"])[:120])
            return data.get("result")
        status = str(data.get("status", ""))
        if status == "1":
            return data.get("result")
        message = str(data.get("message") or "")
        # The only `status: 0` that is a READING rather than a failure.
        if "No transactions found" in message or "No records found" in message:
            return []
        raise RuntimeError(f"{message or 'explorer error'}"[:120])

    # ── the five facts ─────────────────────────────────────────────────────

    async def fetch(self, chain: str, address: str) -> dict:
        chain_id = CHAIN_IDS.get(str(chain or "").lower())
        if chain_id is None:
            # Not a refusal to answer — a chain this explorer does not cover.
            raise RuntimeError(f"unsupported chain {chain!r}")

        # The deployer address gates everything else, so it is the one read
        # allowed to fail the whole source.
        creation = await self._get(chain_id, {
            "module": "contract", "action": "getcontractcreation",
            "contractaddresses": address})
        row = (creation or [None])[0] if isinstance(creation, list) else None
        deployer = (row or {}).get("contractCreator") if isinstance(row, dict) else None
        if not deployer:
            return {}
        creation_hash = str((row or {}).get("txHash") or "").lower()

        out: dict[str, Any] = {"deployer_address": str(deployer)}

        # 1. Is the source published? A separate call, separately fallible: an
        #    unreadable verification status is not an unverified contract
        #    (which is a FLAG worth 1.0).
        try:
            src = await self._get(chain_id, {
                "module": "contract", "action": "getsourcecode",
                "address": address})
            entry = (src or [None])[0] if isinstance(src, list) else None
            if isinstance(entry, dict):
                out["contract_verified"] = bool(str(entry.get("SourceCode") or "").strip())
        except Exception as exc:                                  # noqa: BLE001
            logger.debug("getsourcecode failed for %s: %s", address, exc)

        # 2-4. The deployer's own transaction history.
        try:
            out.update(await self._history(chain_id, deployer, creation_hash))
        except Exception as exc:                                  # noqa: BLE001
            logger.debug("txlist failed for %s: %s", deployer, exc)

        # 4b. The contract's own runtime bytecode, hashed for the rug-template
        #     comparison. A separate call and separately fallible: an
        #     unreadable code blob must leave the HARD check unknown rather
        #     than clearing the contract against a corpus it never met.
        try:
            code = await self._get(chain_id, {
                "module": "proxy", "action": "eth_getCode",
                "address": address, "tag": "latest"})
            from bot.core.deployer_taint import bytecode_hash
            digest = bytecode_hash(code if isinstance(code, str) else "")
            if digest:
                out["runtime_bytecode_hash"] = digest
        except Exception as exc:                                  # noqa: BLE001
            logger.debug("eth_getCode failed for %s: %s", address, exc)

        # 5. How much of the supply the deployer still holds.
        try:
            share = await self._supply_share(chain_id, address, deployer)
            if share is not None:
                out["deployer_supply_pct"] = share
        except Exception as exc:                                  # noqa: BLE001
            logger.debug("supply share failed for %s: %s", address, exc)

        return out

    async def _history(self, chain_id: int, deployer: str,
                       creation_hash: str) -> dict:
        """Wallet age and deployment counts from the deployer's transactions."""
        txs = await self._get(chain_id, {
            "module": "account", "action": "txlist", "address": deployer,
            "startblock": 0, "endblock": 99999999, "page": 1,
            "offset": _TXLIST_MAX, "sort": "asc"})
        if not isinstance(txs, list):
            return {}
        out: dict[str, Any] = {}

        # An empty list for an address that demonstrably deployed a contract is
        # a visibility gap, not a brand-new wallet. Emitting `wallet_age_days:
        # 0` here would manufacture the "less than a week old" FLAG out of a
        # failed read — the exact inversion this module exists to avoid.
        if not txs:
            return {}

        # Who funded this wallet. Oldest first, so the head of this list is the
        # wallet's origin — the transfer a mixer would appear in. Deduplicated
        # but ORDER-PRESERVING: the first funder is the interesting one and a
        # set would lose which it was.
        me = str(deployer or "").lower()
        seen_funders: dict = {}
        for t in txs:
            if not isinstance(t, dict):
                continue
            if str(t.get("to") or "").lower() != me:
                continue                      # outbound: not a funding source
            src = str(t.get("from") or "").lower()
            if src and src != me:
                seen_funders.setdefault(src, True)
        if seen_funders:
            out["funding_sources"] = list(seen_funders)

        first_ts = _num((txs[0] or {}).get("timeStamp"))
        if first_ts is not None:
            age_days = (self._now() - first_ts) / _DAY_S
            # A future-dated first transaction means a clock or data problem,
            # not a negative-age wallet.
            if age_days >= 0:
                out["wallet_age_days"] = age_days

        # A contract creation is a transaction with no `to`.
        creations = [t for t in txs
                     if isinstance(t, dict) and not str(t.get("to") or "").strip()]

        # Truncation check. At exactly the cap the history is a prefix, so every
        # count below is a FLOOR. `prior_deployments` feeds `resolve_outcomes`
        # as the denominator of a record, and a denominator that is quietly too
        # small is a partial total printed as a whole one — omit both counts and
        # let the dossier report the section as unread.
        if len(txs) >= _TXLIST_MAX:
            out["deployments_truncated"] = True
            return out

        # This contract's own creation is not a PRIOR deployment.
        prior = [t for t in creations
                 if str(t.get("hash") or "").lower() != creation_hash]
        out["prior_deployments"] = float(len(prior))
        # The ADDRESSES too, newest first, so `deployer_fates` can go and ask
        # what became of each. A count alone is why that column stayed unread:
        # you cannot look up the fate of a number. Rows without a
        # contractAddress are still counted above — they happened — they simply
        # cannot be followed, and will land in `unresolved` rather than being
        # dropped from the total and quietly improving the ratio.
        out["prior_contracts"] = [
            str(t.get("contractAddress")) for t in reversed(prior)
            if t.get("contractAddress")]

        cutoff = self._now() - _DAY_S
        recent = 0
        for t in creations:
            ts = _num(t.get("timeStamp"))
            if ts is not None and ts >= cutoff:
                recent += 1
        out["concurrent_launches_24h"] = float(recent)
        return out

    async def _supply_share(self, chain_id: int, address: str,
                            deployer: str) -> Optional[float]:
        """The deployer's balance as a FRACTION of total supply.

        A fraction, not a percent, despite the field being named
        `deployer_supply_pct` — see this module's docstring. Both figures are
        raw token units, so the ratio needs no decimals and they cancel.
        """
        bal = _num(await self._get(chain_id, {
            "module": "account", "action": "tokenbalance",
            "contractaddress": address, "address": deployer, "tag": "latest"}))
        supply = _num(await self._get(chain_id, {
            "module": "stats", "action": "tokensupply",
            "contractaddress": address}))
        if bal is None or supply is None or supply <= 0:
            return None
        return bal / supply


def default_deployer_sources() -> list:
    """The deployer sources to ask by default.

    Returned even when unconfigured, deliberately. `gather` reports every source
    it was handed, so an absent key produces a visible `unavailable` row rather
    than a silently shorter list — "we never asked" is a fact about our
    diligence and belongs in the report.
    """
    return [EtherscanDeployerSource()]
