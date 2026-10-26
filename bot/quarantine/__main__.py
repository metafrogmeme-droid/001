"""Read-only stdin JSON in, JSON out. No network and no signature.

The website route spawns this. It does not read a key and it does not
open a socket. Ops: ``sanitize``, ``poison``, ``touch``, ``filter``.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping, cast

from bot.guardian.firewall import asset_for_model
from bot.quarantine.hide import touch_decision, visible_assets
from bot.quarantine.poison import destination_warning


def handle(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"ok": False, "error": "request"}
    op = str(body.get("op") or "")
    if op == "sanitize":
        return asset_for_model(body.get("asset"))
    if op == "poison":
        return destination_warning(
            body.get("destination"),
            counterparties=body.get("counterparties") or [],
            own=body.get("own") or [],
        )
    if op == "touch":
        asset = body.get("asset")
        book = body.get("book") if isinstance(body.get("book"), dict) else {"state": "empty", "contracts": set()}
        if isinstance(book, dict) and not isinstance(book.get("contracts"), set):
            from bot.quarantine.book import contract_key
            keys = set()
            for row in book.get("assets") or []:
                if isinstance(row, dict):
                    key = contract_key(row.get("chain_id"), row.get("contract"), row.get("token_id") or "")
                    if key:
                        keys.add(key)
            book = {**book, "contracts": keys, "state": book.get("state") or "read"}
        if not isinstance(asset, dict):
            return {"ok": False, "error": "asset"}
        decided = touch_decision(body.get("action"), asset, cast(Mapping[str, Any], book))
        decided["ok"] = True
        return decided
    if op == "filter":
        book = body.get("book") if isinstance(body.get("book"), dict) else {"state": "empty", "contracts": set()}
        if isinstance(book, dict) and not isinstance(book.get("contracts"), set):
            from bot.quarantine.book import contract_key
            keys = set()
            for row in book.get("assets") or []:
                if isinstance(row, dict):
                    key = contract_key(row.get("chain_id"), row.get("contract"), row.get("token_id") or "")
                    if key:
                        keys.add(key)
            book = {**book, "contracts": keys}
        part = visible_assets(body.get("rows"), cast(Mapping[str, Any], book))
        # Sets are not JSON. The count is the fact the caller needs.
        return {
            "ok": part["state"] != "unreadable",
            "state": part["state"],
            "hidden": part["hidden"],
            "unread_rows": part["unread_rows"],
            "visible": part["visible"],
        }
    return {"ok": False, "error": "op"}


def main() -> int:
    try:
        raw = sys.stdin.read()
        body = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, UnicodeError):
        json.dump({"ok": False, "error": "request"}, sys.stdout)
        return 0
    try:
        json.dump(handle(body), sys.stdout)
    except Exception as exc:
        json.dump({"ok": False, "error": type(exc).__name__}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
