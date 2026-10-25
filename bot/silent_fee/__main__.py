"""stdin JSON, stdout JSON. The website route spawns this. No key is read."""

from __future__ import annotations

import json
import sys

from bot.silent_fee.check import read_account
from bot.silent_fee.flag import flag_on


def main() -> int:
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        json.dump({"available": False}, sys.stdout)
        return 0
    if not isinstance(body, dict) or not flag_on():
        json.dump({"available": False}, sys.stdout)
        return 0
    address = body.get("address")
    days = body.get("days")
    if not isinstance(address, str):
        address = ""
    if days is not None and days not in (30, 90):
        result = read_account(address, days=days if isinstance(days, int) else -1)
    else:
        result = read_account(address, days=days if isinstance(days, int) else 30)
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
