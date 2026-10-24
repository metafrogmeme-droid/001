"""Print the shadow report for the packaged recorded window.

The page calls this. It writes one JSON object to stdout and the exception
class, never the message, to stderr.
"""
from __future__ import annotations

import json
import sys

from bot.srb.shadow import demo_report


def main() -> int:
    try:
        json.dump(demo_report(), sys.stdout)
        sys.stdout.write("\n")
    except Exception as exc:  # noqa: BLE001 — the caller is told the class only
        sys.stderr.write(type(exc).__name__ + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
