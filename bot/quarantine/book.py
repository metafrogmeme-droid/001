"""The local hide list. One file per owner, no network, no signature.

A missing file is an empty list: nothing has been hidden. A file that
cannot be parsed is unreadable, and that is not an empty list. Saving
refuses to replace an unreadable file.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import REPO_ROOT

_OWNER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def book_dir() -> Path:
    """The directory both this process and the website read.

    A relative ``RUNECLAW_STATE_DIR`` is anchored at the repository root,
    the same way ``app/lib/state_dir.js`` anchors it. Unset means
    ``<repo>/data``.
    """
    raw = (os.environ.get("RUNECLAW_STATE_DIR") or "").strip()
    if not raw:
        base = REPO_ROOT / "data"
    else:
        expanded = Path(raw).expanduser()
        base = expanded if expanded.is_absolute() else REPO_ROOT / expanded
    return base / "quarantine"


def owner_ok(owner: object) -> bool:
    return isinstance(owner, str) and _OWNER.fullmatch(owner) is not None


def contract_key(chain_id: object, contract: object, token_id: object = "") -> Optional[str]:
    """``chain:address`` for a whole contract, or ``chain:address:id`` for one token."""
    if isinstance(chain_id, bool) or not isinstance(chain_id, (int, str)):
        return None
    try:
        chain = int(chain_id)
    except ValueError:
        return None
    if chain < 0:
        return None
    if not isinstance(contract, str) or _ADDR.fullmatch(contract) is None:
        return None
    base = f"{chain}:{contract.lower()}"
    tid = "" if token_id is None else str(token_id).strip()
    if not tid:
        return base
    if len(tid) > 80 or any(c in tid for c in "\n\r/:\\"):
        return None
    return f"{base}:{tid}"


def _path(owner: str) -> Optional[Path]:
    if not owner_ok(owner):
        return None
    return book_dir() / f"{owner}.json"


def _addresses(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and _ADDR.fullmatch(item):
            out.append(item.lower())
    return out


def _assets(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = contract_key(item.get("chain_id"), item.get("contract"), item.get("token_id") or "")
        if key is None:
            continue
        out.append({
            "chain_id": int(item["chain_id"]),
            "contract": str(item["contract"]).lower(),
            "token_id": "" if not str(item.get("token_id") or "").strip() else str(item.get("token_id")).strip(),
        })
    return out


def empty_book() -> dict[str, Any]:
    return {"state": "empty", "assets": [], "own": [], "counterparties": [], "contracts": set()}


def _contracts(assets: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in assets:
        key = contract_key(row.get("chain_id"), row.get("contract"), row.get("token_id") or "")
        if key is not None:
            keys.add(key)
    return keys


def load_book(owner: object) -> dict[str, Any]:
    """Read one owner's list.

    ``state`` is ``empty`` (nothing saved), ``read`` (a list was read),
    ``unreadable`` (the file is there and could not be parsed, or the
    owner id is not a safe path segment), or ``invalid``.
    """
    if not isinstance(owner, str) or not owner:
        return {**empty_book(), "state": "invalid"}
    path = _path(owner)
    if path is None:
        return {**empty_book(), "state": "unreadable"}
    if not path.is_file():
        return empty_book()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {**empty_book(), "state": "unreadable"}
    if not isinstance(data, dict):
        return {**empty_book(), "state": "unreadable"}
    assets = _assets(data.get("assets"))
    book = {
        "state": "read",
        "assets": assets,
        "own": _addresses(data.get("own")),
        "counterparties": _addresses(data.get("counterparties")),
        "contracts": _contracts(assets),
    }
    return book


def save_book(owner: object, book: dict[str, Any]) -> str:
    """Write the list. Returns ``saved``, ``unreadable``, or ``invalid``.

    An unreadable file is left in place. A missing directory is created
    only for a save, never for a read.
    """
    if not isinstance(owner, str):
        return "invalid"
    path = _path(owner)
    if path is None:
        return "invalid"
    current = load_book(owner)
    if current["state"] == "unreadable":
        return "unreadable"
    body = {
        "assets": _assets(book.get("assets")),
        "own": _addresses(book.get("own")),
        "counterparties": _addresses(book.get("counterparties")),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, body)
    except OSError:
        return "unreadable"
    return "saved"
