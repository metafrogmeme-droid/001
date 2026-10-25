"""Resting size for a name whose v2 book may have moved.

Bitget answers an R-prefix tokenized equity on the v2 order book with
code 40502 and serves that book on v3 (``params={"uta": True}``).
``bot.core.order_flow.order_book_moved_to_v3`` is that detector (PR #712).
It is not merged in this tree. The import is the guard: when the module
is there, its detector is the one reading; when it is not, the same
refusal text is matched here. An unread book is ``(None, None)``. It is
not a mid-scale score and it is not dropped out of the denominator.
"""
from typing import Any, Optional


def book_moved_to_v3(exc: BaseException) -> bool:
    """Whether this refusal is Bitget telling us the book lives on v3."""
    text = str(exc).lower()
    if "upgrade to access the v3" in text:
        return True
    return "40502" in text and "v3" in text


def _moved(exc: BaseException) -> bool:
    try:
        from bot.core.order_flow import order_book_moved_to_v3 as upstream
    except ImportError:
        upstream = None
    if upstream is not None:
        return bool(upstream(exc))
    return book_moved_to_v3(exc)


def _side_size(levels: Any) -> Optional[float]:
    if not isinstance(levels, (list, tuple)) or not levels:
        return None
    total = 0.0
    count = 0
    for row in list(levels)[:20]:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            size = float(row[1])
        except (TypeError, ValueError):
            continue
        if size > 0:
            total += size
            count += 1
    return total if count else None


def _sizes(book: Any) -> tuple:
    if not isinstance(book, dict):
        return None, None
    return _side_size(book.get("bids")), _side_size(book.get("asks"))


def read_levels(fetch, symbol: str) -> tuple:
    """``(bid_size, ask_size)`` from ``fetch(symbol, params)``.

    The first call is v2 (``params`` is None). A refusal that names the v3
    book is retried once with ``{"uta": True}``, the same retry PR #712
    makes. Any other failure, or a book with no resting size, is unread.
    """
    try:
        book = fetch(symbol, None)
    except TypeError:
        try:
            book = fetch(symbol)
        except Exception as exc:
            if not _moved(exc):
                return None, None
            return None, None
    except Exception as exc:
        if not _moved(exc):
            return None, None
        try:
            book = fetch(symbol, {"uta": True})
        except Exception:
            return None, None
    return _sizes(book)
