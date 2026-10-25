"""Block-range reads through the approval reader's explorer shape.

A failed call is ``None``. An empty list is a completed read that found
nothing. The two are not the same, and this module does not collapse them.
"""

from __future__ import annotations

from typing import Any, Optional

from bot.approval_reader import ApprovalReader

_PAGE = 100
_MAX_PAGES = 5


class RangeReader:
    """Logs and transactions between two blocks. The transport is injected."""

    def __init__(self, inner: Optional[ApprovalReader] = None, max_pages: int = _MAX_PAGES):
        self.inner = inner or ApprovalReader()
        self.max_pages = max_pages

    def fetch(self, chain_id: int, params: dict) -> tuple[str, Any]:
        """``('ok', result)`` or ``('unreadable', None)``."""
        try:
            url = self.inner._url(chain_id, params)
            data = self.inner.transport(url)
        except Exception:
            return "unreadable", None
        if not isinstance(data, dict) or data.get("error"):
            return "unreadable", None
        if "result" in data and "jsonrpc" in data:
            return "ok", data.get("result")
        status = str(data.get("status", ""))
        message = str(data.get("message") or "")
        if status == "1":
            return "ok", data.get("result")
        if "No records found" in message or "No transactions found" in message:
            return "ok", []
        return "unreadable", None

    def head(self, chain_id: int) -> Optional[int]:
        status, result = self.fetch(chain_id, {
            "module": "proxy", "action": "eth_blockNumber",
        })
        if status != "ok" or not isinstance(result, str) or not result.startswith("0x"):
            return None
        try:
            return int(result, 16)
        except ValueError:
            return None

    def logs(
        self, chain_id: int, owner: str, topic0: str, topic_pos: int,
        from_block: int, to_block: int,
    ) -> Optional[list[dict]]:
        """Every log in the range, or None when a page could not be read."""
        found: list[dict] = []
        topic_key = f"topic{topic_pos}"
        opr_key = "topic0_2_opr" if topic_pos == 2 else "topic0_1_opr"
        for page in range(1, self.max_pages + 1):
            status, result = self.fetch(chain_id, {
                "module": "logs",
                "action": "getLogs",
                "fromBlock": str(from_block),
                "toBlock": str(to_block),
                "topic0": topic0,
                opr_key: "and",
                topic_key: "0x" + owner.lower().removeprefix("0x").rjust(64, "0"),
                "page": str(page),
                "offset": str(_PAGE),
            })
            if status != "ok":
                return None
            if result is None:
                return found
            if not isinstance(result, list):
                return None
            rows = [row for row in result if isinstance(row, dict)]
            found.extend(rows)
            if len(rows) < _PAGE:
                return found
        return None

    def transactions(self, chain_id: int, owner: str, from_block: int, to_block: int) -> Optional[list]:
        status, result = self.fetch(chain_id, {
            "module": "account",
            "action": "txlist",
            "address": owner,
            "startblock": str(from_block),
            "endblock": str(to_block),
            "page": "1",
            "offset": str(_PAGE),
            "sort": "asc",
        })
        if status != "ok":
            return None
        if result is None:
            return []
        if not isinstance(result, list):
            return None
        if len(result) >= _PAGE:
            return None
        return result

    def tx_sender(self, chain_id: int, tx_hash: str) -> tuple[str, Optional[str]]:
        status, result = self.fetch(chain_id, {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": tx_hash,
        })
        if status != "ok" or not isinstance(result, dict):
            return "unreadable", None
        sender = result.get("from")
        if not isinstance(sender, str) or not sender.startswith("0x") or len(sender) != 42:
            return "unreadable", None
        return "ok", sender.lower()

    def account_delegation(self, chain_id: int, owner: str) -> tuple[str, Optional[str]]:
        try:
            return self.inner.delegation(chain_id, owner)
        except Exception:
            return "unreadable", None
