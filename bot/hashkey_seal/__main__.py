"""`python3 -m bot.hashkey_seal status|read|publish`.

Stdout is one JSON object. A crash writes the exception class, never the
message, and never the signer key. Before anything else the process asks
the same startup guard the bot and the bridge ask: a signer key aimed at a
chain other than HashKey testnet, or any server-wide mainnet refusal, stops
this process too, because this is the process that holds the key.
"""
from __future__ import annotations

import json
import os
import sys

from bot.hashkey_seal.publish import publish_anchor, read_anchor, status
from bot.web.mainnet_guard import startup_refusal


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = _env()
    refusal = startup_refusal(env)
    if refusal:
        sys.stderr.write("startup refused\n")
        return 1
    try:
        if args == ["status"]:
            body = status(env)
        elif len(args) == 2 and args[0] == "read":
            body = read_anchor(env, args[1])
        elif len(args) == 3 and args[0] == "publish":
            body = publish_anchor(env, args[1], args[2])
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
