"""Print the published reading. Does not fetch and does not sign."""

from __future__ import annotations

import json
import sys

from bot.qch.config import active_env
from bot.qch.reading import custody_reading


def main() -> int:
    doc = custody_reading(env=active_env())
    if doc.get("readable") is not True:
        sys.stderr.write("unreadable\n")
        return 2
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
