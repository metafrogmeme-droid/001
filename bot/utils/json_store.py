"""A JSON file read whole and written whole, with a third answer between
"empty" and "read".

THE DEFECT THIS EXISTS FOR. A store read its file with

    try:
        data = json.load(fh)
    except Exception:
        data = {}

and wrote ``data`` back whole on its next change. A MISSING file is an empty
store. A file that is there and will not read is not one: it holds every other
user's row, and the next write -- somebody's ``/leverage set``, the next
envelope bind, the next peak observed -- replaced it with the one row the
writer knew about. Driven: a leverage file holding three users, one failed
read, one ``set_pref`` for a fourth, and the file held the fourth alone. The
authority ledger did it to every user's 24h spend; the equity-peak store did
it to every person's high-water mark.

THREE STATES, one reading for all of them:

  ``fresh``       no file, or an EMPTY one (nothing in it for a write to
                  erase). A fresh start, and the only state a first write
                  may create the file from.
  ``read``        the file parsed and is the shape the store expects.
  ``unreadable``  anything else: an OSError other than a missing file, a parse
                  error, a file of the wrong shape. NEVER written over.

``update_json_store`` is the one write this shape allows. It reads the file
again, refuses to write over a file that will not read, applies ONE change to
what it read, and replaces the file atomically. A write that starts from the
file cannot erase what the file held, whatever a copy in memory believes -- so
a store that keeps one adopts what was written rather than its own map.

What a READER gets from an unreadable store is not decided here, because it
depends on what the store means: an unreadable spending ledger is not $0
spent, an unreadable high-water mark is not "no drawdown", and an unreadable
tighten-only preference is not "none". Each store says, beside its reader.

The precedents this generalises are in the tree and each covered one store:
``exchange_credentials._load`` (a failed read blocks every save),
``shadow_ledger`` (the same three states, for rows), and the executor's
closed-trade record (keeps what it could not read). The rule that keeps the
old shape from coming back is ``tests/test_no_json_store_writes_over_a_failed_read.py``.

WHAT THIS DOES NOT DO. The in-process lock is the caller's. Two PROCESSES can
still interleave a read-modify-write, and the later one wins for the key they
both changed; what is gone is the loss of every OTHER key, which is what the
old shape cost.
"""

from __future__ import annotations

import json
from typing import Any, Callable, NamedTuple, Optional, TypeVar, overload

from bot.utils.atomic_write import StrPath, atomic_write_json

FRESH = "fresh"            # no file on disk yet, or an empty one
READ = "read"              # the file parsed, and is the shape expected
UNREADABLE = "unreadable"  # a file is there and is not this store; never overwritten


class StoreRead(NamedTuple):
    """One reading of a store's file. ``data`` is the parsed value when
    ``state`` is ``read`` and ``None`` otherwise; ``detail`` says why a file
    could not be read (an exception's CLASS, never its text)."""

    state: str
    data: Any = None
    detail: str = ""


class StoreUnreadable(RuntimeError):
    """The file is there and could not be read. Not an empty store."""

    def __init__(self, path: StrPath, detail: str) -> None:
        self.path = str(path)
        self.detail = detail
        super().__init__(f"{self.path} could not be read ({detail})")


def read_json_store(path: StrPath, *, shape: type = dict,
                    check: Optional[Callable[[Any], str]] = None) -> StoreRead:
    """Read ``path`` into one of the three states. Nothing the FILE holds
    makes this raise.

    ``check(data)`` answers ``""`` for a file that is this store and a short
    reason for one that parses and is not (a ledger whose ``book`` is a list).
    A file of the wrong shape is unreadable, never empty: it is somebody's
    file, and an empty map written over it would erase it just the same.
    ``check`` is the store's own code and is handed a value already of
    ``shape``; one that raises is a defect in that store and propagates.
    Every check in the tree reads only ``dict.get`` and ``isinstance``, so
    none can, and a ``try`` here would be a line no input reaches.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return StoreRead(FRESH)
    except Exception as exc:
        return StoreRead(UNREADABLE, None, type(exc).__name__)
    if not text.strip():
        # An EMPTY file holds no rows, so no write can erase anything from it:
        # a fresh start, the answer `RiskEngine._load_state` gives it too.
        return StoreRead(FRESH)
    try:
        data = json.loads(text)
    except Exception as exc:
        return StoreRead(UNREADABLE, None, type(exc).__name__)
    if not isinstance(data, shape):
        return StoreRead(UNREADABLE, None, f"not a JSON {shape.__name__}")
    if check is not None:
        why = check(data)
        if why:
            return StoreRead(UNREADABLE, None, why)
    return StoreRead(READ, data)


_T = TypeVar("_T")


@overload
def load_json_store(path: StrPath, *,
                    check: Optional[Callable[[Any], str]] = None) -> dict: ...


@overload
def load_json_store(path: StrPath, *, shape: type[_T],
                    check: Optional[Callable[[Any], str]] = None) -> _T: ...


def load_json_store(path: StrPath, *, shape: type = dict,
                    check: Optional[Callable[[Any], str]] = None) -> Any:
    """The store's data -- an empty ``shape()`` for a fresh start -- or
    :class:`StoreUnreadable` for a file that is there and will not read."""
    r = read_json_store(path, shape=shape, check=check)
    if r.state == UNREADABLE:
        raise StoreUnreadable(path, r.detail)
    return r.data if r.state == READ else shape()


def update_json_store(path: StrPath, change: Callable[[Any], Any], *,
                      shape: type = dict,
                      check: Optional[Callable[[Any], str]] = None,
                      **json_kwargs: Any) -> tuple[Any, bool]:
    """Read-modify-write ``path``. Returns ``(data, written)``.

    Reads the file again; an unreadable one raises :class:`StoreUnreadable`
    and NOTHING is written. A fresh start begins from ``shape()``.
    ``change(data)`` edits the data in place and answers ``False`` when there
    was nothing to change, in which case nothing is written either. The rest
    is written atomically through :func:`atomic_write_json`, whose OSError is
    the caller's: a write that did not land is a different fact from a file
    that could not be read, and the caller says which.
    """
    data: Any = load_json_store(path, shape=shape, check=check)
    if change(data) is False:
        return data, False
    atomic_write_json(path, data, **json_kwargs)
    return data, True
