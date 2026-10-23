"""Read open ERC-20, ERC-721 and Permit2 approvals on the launch chains.

The explorer is Etherscan's V2 API when ``ETHERSCAN_API_KEY`` is set (one key,
``chainid`` selects the chain). With no key, the same call shape goes to the
public Routescan explorer. Either way the bot only reads.

Logs discover approvals. A later ``eth_call`` confirms the allowance is still
open and, for fungible tokens, reads total supply so "unlimited" can follow
revoke.cash. A failed confirm is not a zero allowance. A log query that stops
early is not a complete list: that chain is left out of the counts.

The public exploit list is Revoke.cash's approval-exploit-list. Only chain id
and spender address are kept from it. A partial download is not a list.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from bot.approval_hygiene import CHAINS, ChainRead, OpenApproval

log = logging.getLogger("runeclaw.approval_digest")

EXPLORER = "https://api.etherscan.io/v2/api"
ROUTESCAN = "https://api.routescan.io/v2/network/mainnet/evm/{chain_id}/etherscan/api"
EXPLOIT_INDEX = "https://raw.githubusercontent.com/RevokeCash/approval-exploit-list/main/index.json"
EXPLOIT_FILE = "https://raw.githubusercontent.com/RevokeCash/approval-exploit-list/main/exploits/{slug}.json"

# Approval(address,address,uint256) — ERC-20 amount and ERC-721 token id share this topic.
TOPIC_APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
# ApprovalForAll(address,address,bool)
TOPIC_FOR_ALL = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
# Permit2 Approval(address,address,address,uint160,uint48)
TOPIC_PERMIT2 = "0xda9fa7c1b00402c17d0161b249b1ab8bbec047c5a52207b9c112deffd817036b"

SEL_SUPPORTS = "0x01ffc9a7"
SEL_SUPPLY = "0x18160ddd"
SEL_ALLOWANCE = "0xdd62ed3e"
SEL_IS_APPROVED_FOR_ALL = "0xe985e9c5"
SEL_GET_APPROVED = "0x081812fc"
SEL_PERMIT2_ALLOWANCE = "0x927da105"
ERC721_INTERFACE = "80ac58cd"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
ZERO = "0x" + "0" * 40

_SLUG = re.compile(r"^[a-z0-9-]{1,80}$")
_PAGE = 100
_MAX_PAGES = 20
_MAX_CALLS = 120
_LIST_TTL = 24 * 3600

Transport = Callable[[str], Any]


def _word(n: int) -> str:
    return f"{n:064x}"


def _addr_word(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def _topic_addr(address: str) -> str:
    return "0x" + _addr_word(address)


def _hex_int(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value.startswith("0x"):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _address_from_word(word: str) -> Optional[str]:
    raw = word.lower().removeprefix("0x")
    if len(raw) < 40:
        return None
    return "0x" + raw[-40:]


@dataclass
class _Event:
    token: str
    spender: str
    kind: str
    amount: int
    expiration: Optional[int]
    block: int
    log_index: int
    updated: Optional[int]


class ApprovalReader:
    def __init__(
        self,
        api_key: Optional[str] = None,
        transport: Optional[Transport] = None,
        now: Optional[Callable[[], int]] = None,
        max_pages: int = _MAX_PAGES,
        page_size: int = _PAGE,
        max_calls: int = _MAX_CALLS,
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("ETHERSCAN_API_KEY", "")
        self.api_key = (api_key or "").strip()
        self.transport = transport or _urlopen_json
        self.now = now or (lambda: int(time.time()))
        self.max_pages = max_pages
        self.page_size = page_size
        self.max_calls = max_calls

    def read(self, address: str) -> tuple[ChainRead, ...]:
        owner = address.lower()
        out: list[ChainRead] = []
        for chain_id, name in CHAINS:
            try:
                out.append(self._chain(chain_id, name, owner))
            except Exception as exc:
                log.info("approval read failed on %s: %s", name, type(exc).__name__)
                out.append(ChainRead(chain_id=chain_id, chain_name=name, status="unreadable"))
        return tuple(out)

    def _chain(self, chain_id: int, name: str, owner: str) -> ChainRead:
        events: list[_Event] = []
        for topic in (TOPIC_APPROVAL, TOPIC_FOR_ALL, TOPIC_PERMIT2):
            page = self._logs(chain_id, owner, topic)
            if page is None:
                return ChainRead(chain_id=chain_id, chain_name=name, status="truncated")
            events.extend(_decode(page, topic, owner))
        latest = _latest(events)
        if _call_budget(latest) > self.max_calls:
            return ChainRead(chain_id=chain_id, chain_name=name, status="truncated")
        approvals: list[OpenApproval] = []
        unresolved = 0
        for ev in latest:
            row, unknown = self._confirm(chain_id, name, owner, ev)
            if unknown:
                unresolved += 1
            if row is not None:
                approvals.append(row)
        return ChainRead(
            chain_id=chain_id, chain_name=name, status="ok",
            approvals=tuple(approvals), unresolved=unresolved,
        )

    def _logs(self, chain_id: int, owner: str, topic0: str) -> Optional[list[dict]]:
        """Every log, or None when the pages ran out before a short page."""
        found: list[dict] = []
        for page in range(1, self.max_pages + 1):
            batch = self._log_page(chain_id, owner, topic0, page)
            found.extend(batch)
            if len(batch) < self.page_size:
                return found
        return None

    def _log_page(self, chain_id: int, owner: str, topic0: str, page: int) -> list[dict]:
        params = {
            "module": "logs",
            "action": "getLogs",
            "fromBlock": "0",
            "toBlock": "latest",
            "topic0": topic0,
            "topic0_1_opr": "and",
            "topic1": _topic_addr(owner),
            "page": str(page),
            "offset": str(self.page_size),
        }
        data = self._get(chain_id, params)
        if data is None:
            return []
        if not isinstance(data, list):
            raise RuntimeError("logs")
        return [row for row in data if isinstance(row, dict)]

    def _get(self, chain_id: int, params: dict) -> Any:
        url = self._url(chain_id, params)
        try:
            data = self.transport(url)
        except Exception as exc:
            raise RuntimeError(type(exc).__name__) from None
        if not isinstance(data, dict):
            raise RuntimeError("malformed")
        if data.get("error"):
            raise RuntimeError("explorer")
        if "result" in data and "jsonrpc" in data:
            result = data.get("result")
            if not isinstance(result, str):
                raise RuntimeError("explorer")
            return result
        status = str(data.get("status", ""))
        message = str(data.get("message") or "")
        if status == "1":
            return data.get("result")
        if "No records found" in message or "No transactions found" in message:
            return None
        raise RuntimeError("explorer")

    def _url(self, chain_id: int, params: dict) -> str:
        query = dict(params)
        if self.api_key:
            query["chainid"] = str(chain_id)
            query["apikey"] = self.api_key
            return f"{EXPLORER}?{urlencode(query)}"
        base = ROUTESCAN.format(chain_id=chain_id)
        return f"{base}?{urlencode(query)}"

    def _eth_call(self, chain_id: int, to: str, data: str) -> Optional[str]:
        try:
            result = self._get(chain_id, {
                "module": "proxy",
                "action": "eth_call",
                "to": to,
                "data": data,
                "tag": "latest",
            })
        except RuntimeError:
            return None
        if not isinstance(result, str) or not result.startswith("0x") or len(result) < 3:
            return None
        return result

    def _confirm(self, chain_id: int, name: str, owner: str, ev: _Event) -> tuple[Optional[OpenApproval], bool]:
        """``(approval or None, unresolved)``. Closed approvals are neither."""
        if ev.spender == ZERO or ev.token == ZERO:
            return None, False
        now = self.now()
        if ev.kind == "forall":
            approved = self._bool_call(
                chain_id, ev.token,
                SEL_IS_APPROVED_FOR_ALL + _addr_word(owner) + _addr_word(ev.spender),
            )
            open_now = ev.amount == 1 if approved is None else approved
            if not open_now:
                return None, False
            return OpenApproval(
                chain_id, name, ev.token, ev.spender, "erc721_all",
                None, None, False, ev.updated,
            ), False
        if ev.kind == "permit2":
            return self._confirm_permit2(chain_id, name, owner, ev, now)
        standard = self._standard(chain_id, ev.token)
        if standard == "erc721_single":
            approved_for = self._address_call(chain_id, ev.token, SEL_GET_APPROVED + _word(ev.amount))
            if approved_for is None:
                open_now = True
            else:
                open_now = approved_for == ev.spender
            if not open_now:
                return None, False
            return OpenApproval(
                chain_id, name, ev.token, ev.spender, "erc721_single",
                None, None, False, ev.updated,
            ), False
        if standard != "erc20":
            return None, True
        supply, known = self._supply(chain_id, ev.token)
        allowance_now = self._uint_call(
            chain_id, ev.token, SEL_ALLOWANCE + _addr_word(owner) + _addr_word(ev.spender),
        )
        amount = ev.amount if allowance_now is None else allowance_now
        if amount <= 0:
            return None, False
        return OpenApproval(
            chain_id, name, ev.token, ev.spender, "erc20",
            amount, supply, known, ev.updated,
        ), False

    def _confirm_permit2(
        self, chain_id: int, name: str, owner: str, ev: _Event, now: int,
    ) -> tuple[Optional[OpenApproval], bool]:
        data = SEL_PERMIT2_ALLOWANCE + _addr_word(owner) + _addr_word(ev.token) + _addr_word(ev.spender)
        result = self._eth_call(chain_id, PERMIT2, data)
        amount = ev.amount
        expiration = ev.expiration
        if result is not None and len(result) >= 2 + 64 * 2:
            parsed_amount = _hex_int("0x" + result[2:66])
            parsed_exp = _hex_int("0x" + result[66:130])
            if parsed_amount is None or parsed_exp is None:
                amount = ev.amount
                expiration = ev.expiration
            else:
                amount = parsed_amount
                expiration = parsed_exp
        if amount <= 0:
            return None, False
        if expiration is not None and expiration <= now:
            return None, False
        supply, known = self._supply(chain_id, ev.token)
        return OpenApproval(
            chain_id, name, ev.token, ev.spender, "permit2",
            amount, supply, known, ev.updated,
        ), False

    def _standard(self, chain_id: int, token: str) -> Optional[str]:
        data = SEL_SUPPORTS + ERC721_INTERFACE.ljust(64, "0")
        result = self._eth_call(chain_id, token, data)
        if result is not None:
            flag = _hex_int(result)
            if flag == 1:
                return "erc721_single"
        supply, known = self._supply(chain_id, token)
        if known and supply is not None:
            return "erc20"
        return None

    def _supply(self, chain_id: int, token: str) -> tuple[Optional[int], bool]:
        result = self._eth_call(chain_id, token, SEL_SUPPLY)
        if result is None:
            return None, False
        value = _hex_int(result)
        if value is None:
            return None, False
        return value, True

    def _uint_call(self, chain_id: int, to: str, data: str) -> Optional[int]:
        result = self._eth_call(chain_id, to, data)
        if result is None:
            return None
        return _hex_int(result)

    def _bool_call(self, chain_id: int, to: str, data: str) -> Optional[bool]:
        value = self._uint_call(chain_id, to, data)
        if value is None:
            return None
        return value == 1

    def _address_call(self, chain_id: int, to: str, data: str) -> Optional[str]:
        result = self._eth_call(chain_id, to, data)
        if result is None:
            return None
        return _address_from_word(result)


def _decode(rows: list[dict], topic0: str, owner: str) -> list[_Event]:
    out: list[_Event] = []
    want = _topic_addr(owner)
    for row in rows:
        topics = row.get("topics")
        if not isinstance(topics, list) or len(topics) < 2 or str(topics[0]).lower() != topic0:
            continue
        if str(topics[1]).lower() != want:
            continue
        block = _hex_int(str(row.get("blockNumber") or ""))
        index = _hex_int(str(row.get("logIndex") or ""))
        updated = _hex_int(str(row.get("timeStamp") or ""))
        if block is None or index is None:
            continue
        data = str(row.get("data") or "0x")
        words = _data_words(data)
        if topic0 == TOPIC_FOR_ALL and len(topics) >= 3 and words:
            spender = _address_from_word(str(topics[2]))
            token = str(row.get("address") or "").lower()
            if spender:
                out.append(_Event(token, spender, "forall", 1 if words[0] == 1 else 0, None, block, index, updated))
        elif topic0 == TOPIC_PERMIT2 and len(topics) >= 4 and len(words) >= 2:
            permit_token = _address_from_word(str(topics[2]))
            permit_spender = _address_from_word(str(topics[3]))
            if permit_token and permit_spender:
                out.append(_Event(
                    permit_token, permit_spender, "permit2", words[0], words[1], block, index, updated,
                ))
        elif topic0 == TOPIC_APPROVAL:
            token = str(row.get("address") or "").lower()
            if len(topics) >= 3 and words:
                spender = _address_from_word(str(topics[2]))
                amount = words[0]
            elif len(words) >= 2:
                spender = _address_from_word(_word(words[0]))
                amount = words[1]
            else:
                continue
            if spender:
                out.append(_Event(token, spender, "approval", amount, None, block, index, updated))
    return out


def _data_words(data: str) -> list[int]:
    raw = data.lower().removeprefix("0x")
    if len(raw) < 64 or len(raw) % 64 != 0:
        return []
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]


def _latest(events: list[_Event]) -> list[_Event]:
    best: dict[tuple[str, str, str], _Event] = {}
    for ev in events:
        key = (ev.kind, ev.token, ev.spender)
        held = best.get(key)
        if held is None or (ev.block, ev.log_index) >= (held.block, held.log_index):
            best[key] = ev
    return list(best.values())


def _call_budget(events: list[_Event]) -> int:
    # supportsInterface + supply + allowance, or one confirm. An over-budget
    # chain is left out whole rather than counted from the first few.
    return len(events) * 3


class ExploitDirectory:
    """Spender addresses from the public approval-exploit list.

    ``known`` is a finished read, including an empty set. ``None`` means the
    directory must fetch. A failed fetch stays ``None`` so a digest does not
    report a clean scam check.
    """

    def __init__(
        self,
        known: Optional[set[tuple[int, str]]] = None,
        transport: Optional[Transport] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._known = known
        self.transport = transport or _urlopen_json
        self.now = now or time.time
        self._loaded: Optional[set[tuple[int, str]]] = None
        self._at = 0.0

    def get(self) -> Optional[set[tuple[int, str]]]:
        if self._known is not None:
            return set(self._known)
        if self._loaded is not None and (self.now() - self._at) < _LIST_TTL:
            return set(self._loaded)
        loaded = self._fetch()
        if loaded is None:
            self._loaded = None
            return None
        self._loaded = loaded
        self._at = self.now()
        return set(loaded)

    def _fetch(self) -> Optional[set[tuple[int, str]]]:
        try:
            index = self.transport(EXPLOIT_INDEX)
        except Exception as exc:
            log.info("exploit list index failed: %s", type(exc).__name__)
            return None
        if not isinstance(index, list) or not index or len(index) > 400:
            return None
        found: set[tuple[int, str]] = set()
        for slug in index:
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                return None
            try:
                body = self.transport(EXPLOIT_FILE.format(slug=slug))
            except Exception as exc:
                log.info("exploit list file failed: %s", type(exc).__name__)
                return None
            if not isinstance(body, dict):
                return None
            rows = body.get("addresses")
            if not isinstance(rows, list):
                return None
            for row in rows:
                if not isinstance(row, dict):
                    return None
                chain = row.get("chainId")
                addr = row.get("address")
                if isinstance(chain, bool) or not isinstance(chain, int):
                    return None
                if not isinstance(addr, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr, re.IGNORECASE):
                    return None
                found.add((chain, addr.lower()))
        return found


def _urlopen_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "runeclaw-approval-digest"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))
