"""`python3 -m bot.hashkey_seal status|read|publish`.

Stdout is one JSON object. A crash writes the exception class, never the
message, and never the signer key.
"""
from __future__ import annotations

import json
import os
import sys

from bot.hashkey_seal.publish import publish_anchor, read_anchor, status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ["status"]:
            body = status(_env())
        elif len(args) == 2 and args[0] == "read":
            body = read_anchor(_env(), args[1])
        elif len(args) == 3 and args[0] == "publish":
            body = publish_anchor(_env(), args[1], args[2])
        else:
            body = {"ok": False, "reason": "usage"}
            json.dump(body, sys.stdout)
            sys.stdout.write("\n")
            return 2
        json.dump(body, sys.stdout)
        sys.stdout.write("\n")
    except Exception as exc:  # noqa: BLE001 — the caller is told the class only
        sys.stderr.write(type(exc).__name__ + "\n")
        return 1
    return 0


def _env() -> dict[str, str]:
    return {str(k): str(v) for k, v in os.environ.items()}


if __name__ == "__main__":
    raise SystemExit(main())
