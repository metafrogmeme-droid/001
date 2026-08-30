#!/usr/bin/env python3
r"""
RUNECLAW training-corpus auditor
================================
Finds training samples that teach the OPPOSITE of the rules. The eval keeps
catching the model approving sub-1.2 R:R and quoting ratios its own numbers
contradict — and the most likely teacher is the corpus itself: ~115K of the
current samples are inherited from old merges nobody has audited. A model
trained on rule-breaking exemplars will break rules no matter how good the
new targeted data is.

Checks (all on the sample's OUTPUT text; a check only fires when the numbers
it needs are actually present — absent is never a defect):

  rr_below_min        APPROVED with computed R:R < 1.2 (from entry/SL/TP)
  rr_mismatch         stated R:R disagrees with computed by > 5%
  geometry            LONG without SL < entry < TP (or SHORT mirrored)
  confidence_percent  "Confidence: 61%" — trained format is 0-1 decimal
  confidence_word     "Confidence: high/low/..." — words are not calibration
  fabrication_tell    "all 23 checks passed" — the invented-verdict pattern
  verdict_conflict    APPROVED together with "NO TRADE" (or REJECTED with
                      "Status: PENDING — type CONFIRM")

Report first, clean second: the default writes CORPUS_AUDIT.json (counts +
up to 20 example line numbers per check) and touches nothing. --clean writes
a filtered jsonl that drops flagged rows, plus a manifest with both SHAs.

--fix (with --clean) REPAIRS instead of dropping where repair is safe:
a row whose ONLY defect is percent confidence is mostly a good trade in the
wrong format — the 2026-08 audit found 17,084 of them, 15% of the corpus —
so it is rewritten ("Confidence: 61%" -> "Confidence: 0.61") and kept,
re-audited after the rewrite, and dropped if anything still flags. Bad
arithmetic, geometry, word confidences and verdict conflicts are never
repaired: guessing what a broken sample meant would launder it.

Usage:
  python audit_training_corpus.py --input training_data\curated_v8_all.jsonl
  python audit_training_corpus.py --input training_data\curated_v8_all.jsonl ^
      --clean training_data\curated_v8_all_clean.jsonl --fix
"""

import argparse
import hashlib
import json
import re

MIN_RR = 1.2
RR_TOLERANCE = 0.05

_NUM = r"\$?([\d][\d,]*\.?\d*)"
RE_ENTRY = re.compile(r"(?:entry[_\s]?price|Entry)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_SL = re.compile(r"(?:stop[_\s]?loss|Stop Loss|SL)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_TP = re.compile(r"(?:take[_\s]?profit|Take Profit|TP1?)\s*[:\s]\s*" + _NUM, re.IGNORECASE)
RE_RR = re.compile(r"(?:Risk:Reward|risk[_\s]?reward(?:[_\s]?ratio)?|R:R)\s*[:\s]\s*(?:1\s*:\s*)?([\d.]+)",
                   re.IGNORECASE)
RE_DIR = re.compile(r"(?:direction|Direction)\s*[:\s]\s*(LONG|SHORT)", re.IGNORECASE)
RE_CONF_PCT = re.compile(r"Confidence\s*[:\s]\s*[\d.]+\s*%", re.IGNORECASE)
RE_CONF_WORD = re.compile(r"Confidence\s*[:\s]\s*(?:very\s+)?(high|low|medium|strong|weak)\b",
                          re.IGNORECASE)
RE_ALL_CHECKS = re.compile(r"all\s+23\s+(?:risk\s+)?checks\s+passed", re.IGNORECASE)
RE_APPROVED = re.compile(r"\bAPPROVED\b")
RE_REJECTED = re.compile(r"\bREJECTED\b")
RE_NO_TRADE = re.compile(r"\bNO TRADE\b", re.IGNORECASE)
RE_PENDING = re.compile(r"Status:\s*PENDING\s*[—-]\s*type\s+CONFIRM", re.IGNORECASE)


def _num(s):
    try:
        return float(s.replace(",", "").replace("$", ""))
    except (ValueError, AttributeError):
        return None


def audit_output(out):
    """Return the list of check names this output violates. Only checks whose
    inputs are present may fire — a scan without numbers reports nothing."""
    flags = []
    approved = bool(RE_APPROVED.search(out))
    rejected = bool(RE_REJECTED.search(out))

    m_e, m_s, m_t = RE_ENTRY.search(out), RE_SL.search(out), RE_TP.search(out)
    entry = _num(m_e.group(1)) if m_e else None
    sl = _num(m_s.group(1)) if m_s else None
    tp = _num(m_t.group(1)) if m_t else None
    m_dir = RE_DIR.search(out)
    direction = m_dir.group(1).upper() if m_dir else None
    if direction is None and entry is not None and sl is not None and tp is not None:
        # Infer only when unambiguous: TP and SL on opposite sides of entry.
        if tp > entry > sl:
            direction = "LONG"
        elif tp < entry < sl:
            direction = "SHORT"

    computed = None
    if entry is not None and sl is not None and tp is not None and direction:
        if direction == "LONG":
            risk, reward = entry - sl, tp - entry
        else:
            risk, reward = sl - entry, entry - tp
        if risk <= 0 or reward <= 0:
            flags.append("geometry")
        else:
            computed = reward / risk
            if approved and computed < MIN_RR:
                flags.append("rr_below_min")

    m_rr = RE_RR.search(out)
    if m_rr and computed is not None:
        stated = _num(m_rr.group(1))
        if stated is not None and stated > 0 and \
                abs(stated - computed) / computed > RR_TOLERANCE:
            flags.append("rr_mismatch")

    if RE_CONF_PCT.search(out):
        flags.append("confidence_percent")
    if RE_CONF_WORD.search(out):
        flags.append("confidence_word")
    if RE_ALL_CHECKS.search(out):
        flags.append("fabrication_tell")

    if approved and RE_NO_TRADE.search(out):
        flags.append("verdict_conflict")
    if rejected and not approved and RE_PENDING.search(out):
        flags.append("verdict_conflict")

    return flags


RE_CONF_PCT_FIX = re.compile(r"(Confidence\s*[:\s]\s*)([\d.]+)\s*%", re.IGNORECASE)


def fix_percent_confidence(out):
    """Rewrite every 'Confidence: NN%' to its 0-1 decimal. Only called for
    rows whose sole defect is the percent format; the caller re-audits the
    result and drops the row if anything still flags."""
    def _repl(m):
        try:
            value = float(m.group(2))
        except ValueError:
            return m.group(0)
        if value > 1.0:
            value = value / 100.0
        return f"{m.group(1)}{value:.2f}"
    return RE_CONF_PCT_FIX.sub(_repl, out)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Audit a training corpus for rule-contradicting samples")
    parser.add_argument("--input", required=True)
    parser.add_argument("--clean", help="also write a cleaned jsonl (flagged rows dropped) to this path")
    parser.add_argument("--fix", action="store_true",
                        help="with --clean: repair rows whose ONLY defect is percent "
                             "confidence instead of dropping them (rewritten, re-audited, "
                             "dropped if anything still flags)")
    parser.add_argument("--report", default=None,
                        help="report path (default: CORPUS_AUDIT.json next to the input)")
    args = parser.parse_args()

    rows = []
    with open(args.input, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    counts = {}
    examples = {}
    flagged_lines = set()
    for i, row in enumerate(rows, 1):
        flags = audit_output(str(row.get("output", "")))
        for flag in flags:
            counts[flag] = counts.get(flag, 0) + 1
            examples.setdefault(flag, [])
            if len(examples[flag]) < 20:
                examples[flag].append(i)
        if flags:
            flagged_lines.add(i)

    report_path = args.report or (args.input.rsplit(".", 1)[0] + "_CORPUS_AUDIT.json")
    report = {
        "input": args.input, "input_sha256": sha256_file(args.input),
        "total_rows": len(rows), "flagged_rows": len(flagged_lines),
        "flagged_pct": round(100 * len(flagged_lines) / max(1, len(rows)), 2),
        "by_check": counts, "example_lines": examples,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print("=" * 60)
    print("CORPUS AUDIT")
    print("=" * 60)
    print(f"  {args.input}: {len(rows)} rows")
    print(f"  Flagged: {len(flagged_lines)} ({report['flagged_pct']}%)")
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>7}  {name}  (e.g. lines {examples[name][:5]})")
    if not counts:
        print("    no rule-contradicting samples found")
    print(f"  Report: {report_path}")

    if args.clean:
        kept, fixed, dropped = [], 0, 0
        for i, row in enumerate(rows, 1):
            if i not in flagged_lines:
                kept.append(row)
                continue
            flags = set(audit_output(str(row.get("output", ""))))
            if args.fix and flags == {"confidence_percent"}:
                repaired = dict(row)
                repaired["output"] = fix_percent_confidence(str(row["output"]))
                if not audit_output(repaired["output"]):
                    kept.append(repaired)
                    fixed += 1
                    continue
            dropped += 1
        with open(args.clean, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {"input": args.input, "input_sha256": report["input_sha256"],
                    "output": args.clean, "output_sha256": sha256_file(args.clean),
                    "rows_in": len(rows), "rows_out": len(kept),
                    "rows_fixed": fixed, "rows_dropped": dropped,
                    "fix_mode": bool(args.fix)}
        mpath = args.clean.rsplit(".", 1)[0] + "_MANIFEST.json"
        with open(mpath, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  Cleaned: {args.clean} ({len(kept)} rows kept, "
              f"{fixed} repaired, {dropped} dropped)  manifest: {mpath}")
    elif counts:
        print("\n  Review the report, then re-run with --clean <out.jsonl> to filter.")


if __name__ == "__main__":
    main()
