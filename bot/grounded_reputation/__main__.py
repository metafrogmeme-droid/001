"""``python3 -m bot.grounded_reputation``.

``classify`` reads a JSON bundle on stdin and writes a reading. ``evidence``
reads the local index for one agent. Both refuse to run while the flag is
off. Neither signs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Optional

from bot.grounded_reputation.addrs import as_int
from bot.grounded_reputation.flag import flag_on
from bot.grounded_reputation.reading import reading_from_payload
from bot.grounded_reputation.store import load_payload, open_read
from bot.utils.paths import env_state_path


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv if argv is None else argv)
    command = args[1] if len(args) > 1 else ""
    if not flag_on():
        _emit({"ok": False, "readable": False, "counts": None, "error": "flag_off"})
        return 0
    if command == "classify":
        return _classify()
    if command == "evidence":
        return _evidence(args)
    _emit({"ok": False, "readable": False, "counts": None, "error": "usage"})
    return 2


def _classify() -> int:
    raw = sys.stdin.read()
    if raw.strip() == "":
        _emit({"ok": False, "readable": False, "counts": None, "error": "payload_unreadable"})
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _emit({"ok": False, "readable": False, "counts": None, "error": "JSONDecodeError"})
        return 0
    _emit(reading_from_payload(payload))
    return 0


def _evidence(args: list[str]) -> int:
    if len(args) < 4:
        _emit({"ok": False, "readable": False, "counts": None, "error": "usage"})
        return 2
    chain = as_int(args[2])
    agent = as_int(args[3])
    if chain is None or agent is None:
        _emit({"ok": False, "readable": False, "counts": None, "error": "usage"})
        return 2
    path = env_state_path("GROUNDED_REPUTATION_DB", "data/grounded_reputation.sqlite")
    if not path.is_file():
        _emit(reading_from_payload({"chain_id": chain, "agent_id": agent}))
        return 0
    try:
        conn = open_read(path)
    except sqlite3.Error:
        _emit({"ok": False, "readable": False, "counts": None, "error": "sqlite3.Error"})
        return 0
    if conn is None:
        _emit(reading_from_payload({"chain_id": chain, "agent_id": agent}))
        return 0
    try:
        payload = load_payload(conn, chain, agent)
    except sqlite3.Error:
        _emit({"ok": False, "readable": False, "counts": None, "error": "sqlite3.Error"})
        return 0
    finally:
        conn.close()
    if payload is None:
        _emit(reading_from_payload({"chain_id": chain, "agent_id": agent}))
        return 0
    _emit(reading_from_payload(payload))
    return 0


def _emit(body: object) -> None:
    sys.stdout.write(json.dumps(body, sort_keys=True) + "\n")


if __name__ == "__main__":
    sys.exit(main())
