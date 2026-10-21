#!/usr/bin/env python3
"""Write this week's public record to benchmark/record/<YYYY>-W<ww>.json.

    python3 scripts/weekly_record.py            # write this ISO week's file
    python3 scripts/weekly_record.py --print    # print it, write nothing
    python3 scripts/weekly_record.py --force    # replace a file already written

Run it on the bot's server, where `data/` holds the bot's records, then commit
the file it names: the website's /record page serves the committed weeks
(`app/scripts/sync_content.js` copies them into `app/content/record/`), so
each week is frozen in git the day it is written. What goes in it, and what is
held back, is `bot/core/public_record.py`: percent, ratio and count only.

It refuses to replace a week already written unless told to: a frozen record
that moves after it is committed is not frozen.

Exit codes: 0 written (or printed), 1 a section that could not be read (the
file is still written and says so), 2 the week's file exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RECORD_DIR = ROOT / "benchmark" / "record"


def closed_trades_path() -> Path:
    """The operator's live closed-trades file: the executor's own path, so
    the record reads the file the bot writes, not a second spelling of it."""
    from bot.core.live_executor import _CLOSED_TRADES_FILE
    p = Path(_CLOSED_TRADES_FILE)
    return p if p.is_absolute() else ROOT / p


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print the record and write nothing")
    ap.add_argument("--force", action="store_true", help="replace this week's file")
    ap.add_argument("--out-dir", default=str(RECORD_DIR), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from bot.core.public_record import UNREADABLE, build_public_record
    record = build_public_record(closed_trades_path=closed_trades_path())
    text = json.dumps(record, indent=1, sort_keys=True) + "\n"
    unreadable = [k for k in ("sleeve", "equity", "parity")
                  if record[k].get("state") == UNREADABLE]
    if args.print_only:
        sys.stdout.write(text)
        return 1 if unreadable else 0

    out = Path(args.out_dir) / f"{record['week']}.json"
    if out.exists() and not args.force:
        print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} already "
              "exists; a committed week does not move. --force replaces it.",
              file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"wrote {shown}")
    for k in unreadable:
        print(f"  {k}: could not be read ({record[k].get('exception')}); "
              "the file says so", file=sys.stderr)
    print("next: node app/scripts/sync_content.js, then commit both copies")
    return 1 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
