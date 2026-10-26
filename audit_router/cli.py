"""CLI. ``python -m audit_router contracts/hashkey``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from audit_router.flag import FLAG, enabled
from audit_router.run import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit_router",
        description="Route a Solidity tree to a Claude model and write a review.",
    )
    parser.add_argument("path", nargs="?", help="contract folder or .sol file inside the repo")
    parser.add_argument("--model", default=None, help="model id override; must be one the client supports")
    parser.add_argument("--config", default=None, help="tier mapping JSON (default: audit_router/models.json)")
    parser.add_argument("--out", default=None, help="report directory (default: docs/audits)")
    parser.add_argument("--date", default=None, help="report date YYYY-MM-DD (default: today, UTC)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not enabled():
        print(f"{FLAG} is off. The audit router does not run.", file=sys.stderr)
        return 2
    if not args.path:
        print("a contract folder or .sol path is required", file=sys.stderr)
        return 3
    day = None
    if args.date:
        try:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("--date must be YYYY-MM-DD", file=sys.stderr)
            return 3
    result = run(
        args.path,
        model=args.model,
        config_path=Path(args.config) if args.config else None,
        out_dir=Path(args.out) if args.out else None,
        today=day,
    )
    if result.report_path is not None:
        print(result.report_path)
    if result.verdict:
        print(f"verdict: {result.verdict}")
    if result.models:
        print("models: " + ", ".join(result.models))
    if result.stopped and result.exit_code != 0:
        print(result.stopped, file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
