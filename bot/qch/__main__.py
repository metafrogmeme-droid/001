"""Print the published reading. Does not fetch and does not sign."""

from __future__ import annotations

import json
import sys

from bot.qch.reading import publish


def main() -> int:
    doc = publish()
    if doc.get("readable") is not True:
        sys.stderr.write("unreadable\n")
        return 2
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
