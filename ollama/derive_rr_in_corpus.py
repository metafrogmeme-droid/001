#!/usr/bin/env python3
"""Rewrite asserted risk:reward lines in an existing corpus as derived ones.

WHY THIS EXISTS. v10 approved a trade whose levels were a 1:1 while its own
report called it 1.5:1 — the levels were right, the ratio was the only wrong
number, and it was the one the verdict rested on. The cause was in the data:
the reject path always showed its arithmetic ("risk = A - B = C, reward =
..., R:R = ... < 1.2") and the approve path printed a bare "- RISK_REWARD:
1.87 >= 1.2 minimum". Refusals modelled derivation; approvals modelled
assertion.

`generate_v8_training_data.py` now derives on both paths. That fixes newly
generated rows only. The curated corpus a training run merges alongside them
is several times larger and still asserts, so without this pass the habit
being trained is still assertion by roughly four to one — a fix that ships
and does nothing, which is the failure mode this repo spends most of its
guard tests preventing.

WHAT IT WILL NOT DO. A row whose stated ratio disagrees with its own levels
is NOT repaired here. That is a real defect (audit_training_corpus.py's
`rr_mismatch`), and silently rewriting the number to whatever the levels say
would erase the evidence of it while reporting a repair. Those rows are
counted and left exactly as they are. Same for rows whose levels cannot be
parsed: absent is never a measurement, so an unreadable row is reported as
unreadable rather than guessed at.

Usage:
    python derive_rr_in_corpus.py --input corpus.jsonl --output corpus_derived.jsonl
    python derive_rr_in_corpus.py --input corpus.jsonl --dry-run
"""

import argparse
import hashlib
import json
import re

RR_TOLERANCE = 0.05

_NUM = r"\$?([\d][\d,]*\.?\d*)"
RE_ENTRY = re.compile(r"(?:entry[_\s]?price|Entry)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_SL = re.compile(r"(?:stop[_\s]?loss|Stop Loss|SL)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_TP = re.compile(r"(?:take[_\s]?profit|Take Profit|TP1?)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_DIR = re.compile(r"(?:direction|Direction)\s*[:\s]\s*(LONG|SHORT)", re.IGNORECASE)

#: An ASSERTED risk:reward check line — a bare ratio against the floor, with
#: no operands. The negative lookahead is what keeps this pass idempotent:
#: a line that already reads "risk = ..." is left alone, so running the
#: script twice cannot double-derive.
RE_ASSERTED = re.compile(
    r"^(?P<indent>\s*[-*]\s*)(?P<label>RISK[_ ]REWARD)\s*:\s*"
    r"(?!risk\s*=)(?P<ratio>\d+\.?\d*)\s*(?P<op>>=|<=|<|>)\s*(?P<floor>\d+\.?\d*)"
    r"(?P<tail>.*)$",
    re.IGNORECASE | re.MULTILINE)


def _num(s):
    try:
        return float(s.replace(",", "").replace("$", ""))
    except (ValueError, AttributeError):
        return None


def _decimals_of(*strings):
    """How many decimal places the levels in this row are written with.

    Derived numbers must print at the row's own precision. A risk of 0.0404
    rendered as "0.04" does not divide back into the ratio it sits next to,
    and a derivation whose arithmetic does not check out teaches worse than
    an assertion does.
    """
    best = 0
    for s in strings:
        if s and "." in s:
            best = max(best, len(s.split(".")[-1]))
    return best


def _fmt(value, decimals):
    return f"{value:,.{decimals}f}" if decimals else f"{value:,.0f}"


def levels_of(out):
    """(entry, sl, tp, direction, decimals) or None if the row cannot be read."""
    m_e, m_s, m_t = RE_ENTRY.search(out), RE_SL.search(out), RE_TP.search(out)
    if not (m_e and m_s and m_t):
        return None
    entry, sl, tp = _num(m_e.group(1)), _num(m_s.group(1)), _num(m_t.group(1))
    if entry is None or sl is None or tp is None:
        return None
    m_dir = RE_DIR.search(out)
    direction = m_dir.group(1).upper() if m_dir else None
    if direction is None:
        # Infer only when unambiguous — TP and SL on opposite sides of entry.
        if tp > entry > sl:
            direction = "LONG"
        elif tp < entry < sl:
            direction = "SHORT"
        else:
            return None
    decimals = _decimals_of(m_e.group(1), m_s.group(1), m_t.group(1))
    return entry, sl, tp, direction, decimals


def derive_output(out, stats):
    """Rewrite every asserted RISK_REWARD line in `out`. Returns the new text."""
    if not RE_ASSERTED.search(out):
        return out

    parsed = levels_of(out)
    if parsed is None:
        stats["unreadable_levels"] += 1
        return out
    entry, sl, tp, direction, decimals = parsed

    if direction == "LONG":
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    if risk <= 0 or reward <= 0:
        stats["degenerate_geometry"] += 1
        return out
    computed = round(reward / risk, 2)

    def _repl(m):
        stated = _num(m.group("ratio"))
        if stated is None or abs(stated - computed) > RR_TOLERANCE:
            # A genuine contradiction. Leave it for the auditor to flag;
            # repairing it here would delete the evidence and report a fix.
            stats["ratio_mismatch_left_alone"] += 1
            return m.group(0)
        stats["derived"] += 1
        return (f"{m.group('indent')}{m.group('label')}: "
                f"risk = {_fmt(risk, decimals)}, "
                f"reward = {_fmt(reward, decimals)}, "
                f"R:R = {_fmt(reward, decimals)} / {_fmt(risk, decimals)} "
                f"= {stated:.2f} {m.group('op')} {m.group('floor')}"
                f"{m.group('tail')}")

    return RE_ASSERTED.sub(_repl, out)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Rewrite asserted risk:reward lines as derived ones")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", help="where to write the repaired corpus "
                                         "(omit with --dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would change, write nothing")
    args = parser.parse_args()

    if not args.dry_run and not args.output:
        parser.error("--output is required unless --dry-run is given")

    stats = {"derived": 0, "ratio_mismatch_left_alone": 0,
             "unreadable_levels": 0, "degenerate_geometry": 0}
    rows_total = rows_changed = 0
    out_fh = open(args.output, "w", encoding="utf-8") if not args.dry_run else None

    with open(args.input, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows_total += 1
            row = json.loads(line)
            before = row.get("output", "")
            after = derive_output(before, stats)
            if after != before:
                rows_changed += 1
                row["output"] = after
            if out_fh:
                out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if out_fh:
        out_fh.close()

    print()
    print(f"  rows read                 : {rows_total:,}")
    print(f"  rows rewritten            : {rows_changed:,}")
    print(f"  RISK_REWARD lines derived : {stats['derived']:,}")
    print()
    print("  Left alone (reported, never guessed at):")
    print(f"    stated ratio contradicts levels : {stats['ratio_mismatch_left_alone']:,}"
          "   <- audit_training_corpus.py's rr_mismatch; fix there, not here")
    print(f"    levels unreadable               : {stats['unreadable_levels']:,}")
    print(f"    degenerate geometry             : {stats['degenerate_geometry']:,}")
    if args.dry_run:
        print("\n  DRY RUN - nothing written.")
    else:
        print(f"\n  Output: {args.output}")
        print(f"  SHA256: {sha256_file(args.output)[:16]}...")
    print()


if __name__ == "__main__":
    main()
