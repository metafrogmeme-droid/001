"""Score one JSON window from stdin. No network.

    python3 -m bot.claw_watch_heuristics
"""

from __future__ import annotations

import json
import sys

from bot.claw_watch_heuristics.score import score_request


def main() -> None:
    raw = json.load(sys.stdin)
    json.dump(score_request(raw if isinstance(raw, dict) else {}), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
