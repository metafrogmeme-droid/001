"""agentWallet as of a block.

A MetadataSet of ``agentWallet`` opens a span. An NFT transfer to a non-zero
address closes it: the wallet is cleared, and the next span stores None.
A mint (transfer from the zero address) does not clear a wallet.

``wallet_at`` returns None for the wallet when the span is a clear. That is
a resolved empty wallet. It is not the zero address, and it is not a failed read.
"""
from __future__ import annotations

from typing import Optional

from bot.grounded_reputation.addrs import ZERO_ADDR, as_int, norm_addr

AGENT_WALLET_KEY = "agentWallet"


def build_wallet_spans(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Spans of ``[from_block, to_block)``. ``to_block`` None means still open."""
    ordered = sorted(events, key=_event_key)
    spans: list[dict[str, object]] = []
    open_span: Optional[dict[str, object]] = None
    for event in ordered:
        block = as_int(event.get("block"))
        if block is None:
            continue
        kind = event.get("kind")
        if kind not in ("set", "clear"):
            continue
        if open_span is not None:
            open_span["to_block"] = block
            when = as_int(event.get("time"))
            if when is not None and open_span.get("from_time") is not None:
                open_span["to_time"] = when
            spans.append(open_span)
        wallet = norm_addr(event.get("wallet")) if kind == "set" else None
        open_span = {
            "wallet": wallet,
            "from_block": block,
            "to_block": None,
            "from_time": as_int(event.get("time")),
            "to_time": None,
        }
    if open_span is not None:
        spans.append(open_span)
    return spans


def wallet_at(
    spans: list[dict[str, object]],
    block: object,
    *,
    at_time: object = None,
    same_chain: bool = True,
) -> dict[str, object]:
    """The wallet in force at ``block`` on the same chain, or at ``at_time`` across chains.

    ``readable`` False means the spans cannot be placed on this payment.
    ``unset`` True means a span was found and the wallet was clear.
    """
    if same_chain:
        number = as_int(block)
        if number is None:
            return {"readable": False, "wallet": None, "unset": False}
        chosen = _covering_block(spans, number)
        if chosen is None:
            return {"readable": True, "wallet": None, "unset": True}
        return {"readable": True, "wallet": chosen.get("wallet"), "unset": chosen.get("wallet") is None}
    when = as_int(at_time)
    if when is None:
        return {"readable": False, "wallet": None, "unset": False}
    chosen = _covering_time(spans, when)
    if chosen is None:
        return {"readable": False, "wallet": None, "unset": False}
    return {"readable": True, "wallet": chosen.get("wallet"), "unset": chosen.get("wallet") is None}


def is_clear_transfer(sender: object) -> bool:
    """An NFT transfer clears the wallet. A mint does not."""
    who = norm_addr(sender)
    return who is not None and who != ZERO_ADDR


def _event_key(event: dict[str, object]) -> tuple[int, int]:
    block = as_int(event.get("block"))
    log_index = as_int(event.get("log_index"))
    return (block if block is not None else 0, log_index if log_index is not None else 0)


def _covering_block(spans: list[dict[str, object]], block: int) -> Optional[dict[str, object]]:
    chosen = None
    for span in spans:
        start = as_int(span.get("from_block"))
        end = span.get("to_block")
        if start is None or start > block:
            continue
        if end is None or (isinstance(end, int) and not isinstance(end, bool) and block < end):
            chosen = span
    return chosen


def _covering_time(spans: list[dict[str, object]], when: int) -> Optional[dict[str, object]]:
    chosen = None
    for span in spans:
        start = as_int(span.get("from_time"))
        end = span.get("to_time")
        if start is None or start > when:
            continue
        if end is None or (isinstance(end, int) and not isinstance(end, bool) and when < end):
            chosen = span
    return chosen
