"""``python -m bot.sigdecoder`` reads one request from a file or stdin and prints JSON.

Exit 0 when no attention signal fired. Exit 2 when one did. That code is a
count of attention signals, not a verdict. A request the decoder could not
read exits 1 and names the exception class only.
"""
from __future__ import annotations

import argparse
import json
import sys

from bot.sigdecoder.core import DISCLAIMER, InputTooLarge, decode_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bot.sigdecoder",
        description="Decode a signing request into public signals.",
    )
    parser.add_argument("input", nargs="?", default="-")
    parser.add_argument("--kind", default="auto")
    parser.add_argument("--now", type=int, default=None)
    args = parser.parse_args(argv)
    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        result = decode_text(raw, args.kind, args.now)
    except InputTooLarge:
        print(json.dumps({
            "ok": False,
            "error": "too_large",
            "signals": None,
            "disclaimer": DISCLAIMER,
            "assisted": "ai",
            "reads_network": False,
        }))
        return 0
    except Exception as exc:  # noqa: BLE001 — the caller is told the class only
        print(json.dumps({
            "ok": False,
            "error": type(exc).__name__,
            "signals": None,
            "disclaimer": DISCLAIMER,
            "assisted": "ai",
            "reads_network": False,
        }))
        return 1
    result["ok"] = True
    print(json.dumps(result))
    return 0 if result["attention_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
