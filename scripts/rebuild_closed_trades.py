#!/usr/bin/env python3
"""Rebuild closed_trades.json from the audit chain — partially, and saying so.

On 2026-08-07 `data/closed_trades.json` was found missing on the operator's
box while `logs/audit_chain.jsonl` held 119 OUTCOME records. The test suite's
autouse fixture had deleted it through the `data/` symlink (see
tests/conftest.py's guard). No backup existed on any path.

The chain is the only surviving record, and it is NOT a superset of what was
lost. What it carries per close:

    decision_id (== trade_id)   symbol      direction
    pnl_usd                     entry_price close_reason      timestamp

What it does NOT carry, and what this script therefore leaves absent:

    quantity   cost_usd   leverage   stop_loss   take_profit
    gross_pnl  commission opened_at  fill_source strategy_type  signal_type

and `exit_price`, which is null on every record already written because
`outcome_event_payload` read a field name LivePosition does not have. Fixed
forward; unrecoverable backwards.

So this reconstruction can honestly answer *what closed* and *for how much*.
It cannot answer *at what price*, *how big*, or *when opened*. Every such
field is emitted as null and the record is stamped

    "source": "audit_chain_rebuild"

so no surface can mistake a rebuilt row for a recorded fill. Nothing is
inferred, averaged, or defaulted — a value the chain does not contain does not
appear here with a plausible number in its place.

SAFETY: writes to a temp path by default and refuses to overwrite an existing
file without --force. Reconstructed history replacing real history silently is
the failure this whole exercise is about.

Usage:
    python3 scripts/rebuild_closed_trades.py                    # dry run, prints a summary
    python3 scripts/rebuild_closed_trades.py --out /tmp/rebuilt.json
    python3 scripts/rebuild_closed_trades.py --out data/closed_trades.json --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

DEFAULT_CHAIN = "logs/audit_chain.jsonl"
SOURCE_TAG = "audit_chain_rebuild"

# Present on a real record, absent from the chain. Emitted as null rather than
# omitted, so a reader sees the field exist and be unknown instead of guessing
# whether the rebuild simply forgot it.
UNKNOWN_FIELDS = ("quantity", "cost_usd", "leverage", "stop_loss",
                  "take_profit", "gross_pnl", "commission", "opened_at",
                  "fill_source", "strategy_type", "signal_type")


def _num(value: Any) -> Optional[float]:
    """A finite float, or None. Never 0.0 as a stand-in for unreadable."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def read_outcomes(chain_path: str) -> tuple[list, list]:
    """(outcome_entries, problems). Never raises on a malformed line."""
    outcomes: list = []
    problems: list = []
    with open(chain_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"line {lineno}: unparseable ({exc})")
                continue
            if entry.get("event_type") == "OUTCOME":
                outcomes.append(entry)
    return outcomes, problems


def verify_chain(chain_path: str) -> tuple[bool, str]:
    """Refuse to rebuild from a chain that does not verify.

    A tampered or truncated chain is not evidence, and a reconstruction built
    on one would look exactly like a reconstruction built on a good one.
    """
    try:
        from bot.utils.audit_chain import AuditChain
        # Static, takes the path, returns (ok, [problem, ...]).
        ok, problems = AuditChain.verify(chain_path)
        return bool(ok), "; ".join(problems[:5]) if problems else ""
    except Exception as exc:               # noqa: BLE001 — advisory, not fatal
        return False, f"could not verify ({exc})"


def rebuild(outcomes: list) -> list:
    """Chain OUTCOME entries -> closed_trades.json-shaped records."""
    out: list = []
    for entry in outcomes:
        payload = entry.get("payload") or {}
        record = {
            "trade_id": payload.get("decision_id") or "",
            "symbol": payload.get("symbol") or "",
            "direction": payload.get("direction") or "",
            "entry_price": _num(payload.get("entry_price")),
            "close_price": _num(payload.get("exit_price")),
            "pnl_usd": _num(payload.get("pnl_usd")),
            "closed_at": entry.get("timestamp") or None,
            "close_reason": payload.get("close_reason") or None,
            "status": "closed",
            "origin": "executed",
            # Provenance. Without this a rebuilt row is indistinguishable from
            # a recorded one, and every surface downstream would present a
            # reconstruction as a measurement.
            "source": SOURCE_TAG,
            "chain_sequence": entry.get("sequence"),
        }
        for field in UNKNOWN_FIELDS:
            record[field] = None
        out.append(record)
    return out


def summarise(records: list) -> str:
    priced = [r for r in records if r["pnl_usd"] is not None]
    unpriced = len(records) - len(priced)
    with_exit = sum(1 for r in records if r["close_price"] is not None)
    total = sum(r["pnl_usd"] for r in priced) if priced else None
    lines = [
        f"  rebuilt records : {len(records)}",
        f"  with a P&L      : {len(priced)}" + (
            f"  (sum {total:+.4f})" if total is not None else ""),
        f"  without a P&L   : {unpriced}"
        + ("  — scored neither way" if unpriced else ""),
        f"  with exit price : {with_exit} of {len(records)}",
    ]
    if with_exit < len(records):
        lines.append(
            f"  NOTE: {len(records) - with_exit} record(s) carry no exit price, "
            "and the chain CANNOT say which of two reasons applies:\n"
            "        (a) the close genuinely could not be priced, or\n"
            "        (b) it predates the outcome_event_payload fix, which read\n"
            "            `exit_price` from a LivePosition that names the field\n"
            "            close_price — so a known price was stored as null.\n"
            "        Both look identical here. Records written after the fix\n"
            "        distinguish themselves; earlier ones are unrecoverable.")
    for field in ("quantity", "cost_usd", "opened_at"):
        lines.append(f"  {field:<15} : unknown for ALL records — not in the chain")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", default=DEFAULT_CHAIN, help=f"(default: {DEFAULT_CHAIN})")
    ap.add_argument("--out", default="", help="write here (default: dry run, prints only)")
    ap.add_argument("--force", action="store_true",
                    help="allow overwriting an existing --out file")
    ap.add_argument("--skip-verify", action="store_true",
                    help="rebuild even if the chain does not verify (says so in the output)")
    args = ap.parse_args()

    if not os.path.exists(args.chain):
        print(f"no audit chain at {args.chain} — nothing to rebuild from", file=sys.stderr)
        return 2

    ok, detail = verify_chain(args.chain)
    if not ok:
        msg = f"audit chain did not verify: {detail}"
        if not args.skip_verify:
            print(msg + "\nrefusing to rebuild from an unverified chain "
                  "(--skip-verify to override)", file=sys.stderr)
            return 3
        print(f"WARNING: {msg} — rebuilding anyway (--skip-verify)", file=sys.stderr)

    outcomes, problems = read_outcomes(args.chain)
    for p in problems:
        print(f"WARNING: {p}", file=sys.stderr)
    records = rebuild(outcomes)

    print(f"\nRebuilt from {args.chain} ({'verified' if ok else 'UNVERIFIED'}):")
    print(summarise(records))

    if not args.out:
        print("\ndry run — pass --out PATH to write. Review before installing "
              "anywhere the bot reads.\n")
        return 0

    if os.path.exists(args.out) and not args.force:
        print(f"\n{args.out} exists — refusing to overwrite without --force.\n"
              "A reconstruction silently replacing real history is the failure "
              "this script exists because of.", file=sys.stderr)
        return 4

    from bot.utils.atomic_write import atomic_write_json
    atomic_write_json(args.out, records, indent=2)
    print(f"\nwrote {len(records)} record(s) to {args.out}")
    print('Every row is stamped source="audit_chain_rebuild". '
          "quantity/cost_usd/opened_at are null and unknowable from the chain.")
    print("CAVEAT: _load_closed_trades coerces most absent numerics to 0.0 on\n"
          "read (`float(item.get(\"quantity\") or 0)`), so a null quantity or\n"
          "cost_usd comes back as a measured-looking zero. pnl_usd is exempt —\n"
          "it round-trips as None. Treat the `source` stamp, not the numbers,\n"
          "as the authority on which rows are reconstructions.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
