"""One solver process for the admin page and the read API.

    python3 -m bot.rxb.cli page
    python3 -m bot.rxb.cli node xUSD
    python3 -m bot.rxb.cli backtest
    python3 -m bot.rxb.cli index

``index`` is the only command that opens a socket. A failed read is JSON
with readable false, not an empty book. Arguments are the subcommand and,
for ``node``, one symbol. No key is accepted on the command line.
"""

from __future__ import annotations

import json
import sys

from bot.rxb.backtest import run_backtest
from bot.rxb.indexer import fetch_all, live_transport
from bot.rxb.reading import node_view
from bot.rxb.report import admin_text, page_model


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "text":
        sys.stdout.write(admin_text(" ".join(args[1:])))
        return 0
    if args[0] == "page":
        json.dump(page_model(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args[0] == "node" and len(args) == 2:
        json.dump(node_view(args[1]), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args[0] == "backtest":
        json.dump(run_backtest(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args[0] == "index":
        json.dump(fetch_all(live_transport), sys.stdout)
        sys.stdout.write("\n")
        return 0
    json.dump({"readable": False, "reason": "unknown_command"}, sys.stdout)
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
