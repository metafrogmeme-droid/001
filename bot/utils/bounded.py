"""One eviction rule for the process-lifetime maps that would otherwise grow.

The bot process runs for days. A dict keyed by something open-ended (an idea
id, a user, a symbol from a churning universe) and written for the life of the
process grows until the unit's memory limit ends it, and nothing in a single
test run can show that. Several such maps are capped here with the same rule,
so the rule lives in one place: the oldest key in insertion order goes first,
and a caller can name keys that must never go (a pending idea's ATR, say).

LRU, where a caller wants it, is `touch`: re-inserting a key moves it to the
newest end. This module keeps no order of its own: a Python dict already is
the insertion order.
"""
from __future__ import annotations

from typing import Callable, MutableMapping, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


def touch(mapping: MutableMapping[K, V], key: K) -> None:
    """Move ``key`` to the newest end of ``mapping`` (no-op when absent), so
    `evict_oldest` reaches it last: least recently used goes first."""
    try:
        value = mapping.pop(key)
    except KeyError:
        return
    mapping[key] = value


def evict_oldest(mapping: MutableMapping[K, V], cap: int, *,
                 keep: Optional[Callable[[K], bool]] = None) -> list[tuple[K, V]]:
    """Pop the oldest keys of ``mapping`` until ``len(mapping) <= cap``.

    ``keep(key)`` true means that key is never evicted, so a map whose kept
    keys alone exceed ``cap`` stays above it: the cap bounds what may be
    dropped, it never drops what the caller still needs. Returns the evicted
    ``(key, value)`` pairs, oldest first, so a caller holding resources (an
    exchange client) can release them.

    Safe against a concurrent writer on another thread (the scan lane runs on
    its own loop): a key removed meanwhile is skipped, never raised.
    """
    if cap < 0:
        raise ValueError(f"cap must be >= 0, got {cap}")
    evicted: list[tuple[K, V]] = []
    if len(mapping) <= cap:
        return evicted
    if keep is None:
        while len(mapping) > cap:
            try:
                oldest = next(iter(mapping))
            except (StopIteration, RuntimeError):
                break       # emptied, or resized mid-read, by another writer
            try:
                evicted.append((oldest, mapping.pop(oldest)))
            except KeyError:
                continue    # removed meanwhile by another writer
        return evicted
    excess = len(mapping) - cap
    for key in list(mapping):
        if excess <= 0:
            break
        if keep(key):
            continue
        try:
            evicted.append((key, mapping.pop(key)))
        except KeyError:
            continue        # removed meanwhile by another writer
        excess -= 1
    return evicted
