#!/usr/bin/env python3
r"""
RUNECLAW - Training Data Curator
==================================
Dedup + validate + manifest a raw training jsonl before a fine-tune run.

WHY THIS EXISTS

The v6 run trained on 305,203 samples assembled by merging merges
(merged_all_training.jsonl was 427 MB and contained earlier combined files
several times over). Duplicated samples inflate step counts, overfit the
duplicated voice first, and make two "identical" runs incomparable because
nobody can say what was actually in the pot. A curated set with a manifest
makes every run reproducible and typically trains BETTER per GPU-hour: a
14B on duplicates loses to an 8B on clean data.

Pure stdlib — runs on any Python 3.8+, no venv needed.

Usage:
  python curate_training_data.py                          # default in/out
  python curate_training_data.py --input a.jsonl b.jsonl  # merge sources
  python curate_training_data.py --max-samples 100000     # cap (even subsample)

Output:
  training_data/curated_v7.jsonl
  training_data/CURATION_MANIFEST.json   (hashes, counts, drop reasons)
"""

import argparse
import hashlib
import json
import os
import re
import sys

DEFAULT_INPUTS = [
    "v6_training_data/combined_training_v6.jsonl",
    "training_data/combined_training.jsonl",
]
DEFAULT_OUTPUT = "training_data/curated_v7.jsonl"

# ~4 chars/token; MAX_SEQ 1024 in the trainer. Samples far beyond it are
# truncated mid-answer at train time, which teaches the model to stop early.
MAX_CHARS = 1024 * 5
MIN_OUTPUT_CHARS = 20

_WS = re.compile(r"\s+")


def _norm(text):
    return _WS.sub(" ", (text or "").strip().lower())


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def curate(rows):
    """Filter + dedup. Returns (kept_rows, drop_counts).

    Two dedup layers:
    - exact: normalized (instruction, input, output) seen before
    - prompt: same normalized (instruction, input) with a DIFFERENT output —
      conflicting labels for one prompt; the first (usually oldest/cleanest
      source) wins.
    """
    drops = {"malformed": 0, "empty_output": 0, "too_long": 0,
             "exact_dup": 0, "conflicting_dup": 0}
    seen_exact = set()
    seen_prompt = set()
    kept = []

    for row in rows:
        if not isinstance(row, dict):
            drops["malformed"] += 1
            continue
        instruction = row.get("instruction") or ""
        inp = row.get("input") or ""
        output = row.get("output") or ""

        if not instruction.strip():
            drops["malformed"] += 1
            continue
        if len(output.strip()) < MIN_OUTPUT_CHARS:
            drops["empty_output"] += 1
            continue
        if len(instruction) + len(inp) + len(output) > MAX_CHARS:
            drops["too_long"] += 1
            continue

        prompt_key = hashlib.sha256(
            (_norm(instruction) + "\x00" + _norm(inp)).encode()).digest()
        exact_key = hashlib.sha256(
            prompt_key + _norm(output).encode()).digest()

        if exact_key in seen_exact:
            drops["exact_dup"] += 1
            continue
        if prompt_key in seen_prompt:
            drops["conflicting_dup"] += 1
            continue

        seen_exact.add(exact_key)
        seen_prompt.add(prompt_key)
        kept.append({"instruction": instruction, "input": inp, "output": output})

    return kept, drops


def subsample(rows, max_samples):
    """Deterministic even subsample — every k-th row, no RNG, reproducible."""
    if max_samples is None or len(rows) <= max_samples:
        return rows
    step = len(rows) / max_samples
    return [rows[int(i * step)] for i in range(max_samples)]


def main():
    parser = argparse.ArgumentParser(description="Dedup + manifest training data")
    parser.add_argument("--input", nargs="+", help="input jsonl file(s)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-samples", type=int, default=None,
                        help="cap the curated set (deterministic even subsample)")
    args = parser.parse_args()

    inputs = args.input or [p for p in DEFAULT_INPUTS if os.path.exists(p)][:1]
    if not inputs:
        print("ERROR: no input file found. Pass --input <file.jsonl>.")
        sys.exit(1)

    print("=" * 60)
    print("RUNECLAW - Training Data Curator")
    print("=" * 60)

    rows = []
    input_meta = []
    for path in inputs:
        if not os.path.exists(path):
            print(f"ERROR: {path} not found.")
            sys.exit(1)
        n_before = len(rows)
        bad_lines = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1
        n_read = len(rows) - n_before
        input_meta.append({"path": path, "sha256": _sha256_file(path),
                           "lines_read": n_read, "unparseable_lines": bad_lines})
        print(f"  {path}: {n_read} rows read"
              + (f", {bad_lines} unparseable lines skipped" if bad_lines else ""))

    kept, drops = curate(rows)
    print(f"\n  After validation + dedup: {len(kept)} of {len(rows)}")
    for reason, count in drops.items():
        if count:
            print(f"    dropped {reason}: {count}")

    capped = subsample(kept, args.max_samples)
    if len(capped) < len(kept):
        print(f"  Subsampled to {len(capped)} (--max-samples)")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in capped:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "inputs": input_meta,
        "output": args.output,
        "output_sha256": _sha256_file(args.output),
        "rows_in": len(rows),
        "rows_out": len(capped),
        "dropped": drops,
        "max_samples": args.max_samples,
    }
    manifest_path = os.path.join(
        os.path.dirname(args.output) or ".", "CURATION_MANIFEST.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n  Curated set: {args.output} ({len(capped)} samples)")
    print(f"  Manifest:    {manifest_path}")
    print(f"  SHA256:      {manifest['output_sha256'][:16]}...")
    print("\nNext step:")
    print("  python train_runeclaw_v7_8b.py")


if __name__ == "__main__":
    main()
