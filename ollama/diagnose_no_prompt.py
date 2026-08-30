#!/usr/bin/env python3
"""Which files hold the no-prompt rows, and what do those rows look like?

The v8 curation dropped 147,936 rows as no_prompt (instruction, input,
prompt, and question all empty). Per-file counts + one sample row each
settle whether that bucket is a further dialect worth normalizing or
genuine junk. Stdlib only.

Usage:  python diagnose_no_prompt.py [folder]   (default: training_data)
"""

import glob
import json
import os
import sys

PROMPT_KEYS = ("instruction", "input", "prompt", "question")


def has_prompt(row):
    if not isinstance(row, dict):
        return True  # not this bucket's problem
    if isinstance(row.get("messages"), list):
        return True
    return any(str(row.get(k) or "").strip() for k in PROMPT_KEYS)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "training_data"
    results = []
    for path in sorted(glob.glob(os.path.join(folder, "*.jsonl"))):
        count = 0
        sample = None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not has_prompt(row):
                    count += 1
                    if sample is None:
                        sample = line
        if count:
            results.append((count, path, sample))

    if not results:
        print("No no-prompt rows found in any file.")
        return

    results.sort(reverse=True)
    total = sum(c for c, _, _ in results)
    print(f"Total no-prompt rows: {total}\n")
    for count, path, sample in results:
        print(f"{count:>8}  {path}")
        print(f"          keys: {sorted(json.loads(sample).keys())}")
        print(f"          sample: {sample[:300]}")
        print()


if __name__ == "__main__":
    main()
