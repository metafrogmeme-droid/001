#!/usr/bin/env python3
r"""
Balance the curated SC training set.
====================================
The v9 generator's draft builder has a huge parameter space (names x supplies
x schedules x phrasings) while reviews/refusals/explains saturate early — so
after the curator's dedup the mix skews to ~88% drafts, starving the
compliance categories (flags-not-verdicts, safety refusals) that are the
entire reason the SC model exists. The 3,000th ERC-20 variant teaches
nothing; the refusal posture needs its gradient share.

This caps drafts at --max-drafts (deterministic selection, --seed) and keeps
every unique row of the other categories. No duplication is introduced —
balancing by capping the oversized class, never by padding the small ones.

Usage:
  python balance_sc_data.py --input training_data\curated_sc1.jsonl ^
      --output training_data\curated_sc1_balanced.jsonl

Writes the output jsonl plus SC_BALANCE_MANIFEST.json (input/output SHA256,
per-class counts before and after) next to it.
"""

import argparse
import hashlib
import json
import os
import random


def classify(output):
    if output.startswith("```solidity"):
        return "draft"
    if output.startswith("CONTRACT REVIEW"):
        return "review"
    if output.startswith("I can flag"):
        return "refusal"
    return "explain"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Cap the draft class of a curated SC set")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-drafts", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=9)
    args = parser.parse_args()

    rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    by_class = {}
    for row in rows:
        by_class.setdefault(classify(row["output"]), []).append(row)
    before = {k: len(v) for k, v in by_class.items()}

    rng = random.Random(args.seed)
    drafts = by_class.get("draft", [])
    if len(drafts) > args.max_drafts:
        drafts = rng.sample(drafts, args.max_drafts)
    kept = drafts + [r for k, v in by_class.items() if k != "draft" for r in v]
    rng.shuffle(kept)
    after = {}
    for row in kept:
        key = classify(row["output"])
        after[key] = after.get(key, 0) + 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "input": args.input, "input_sha256": sha256_file(args.input),
        "output": args.output, "output_sha256": sha256_file(args.output),
        "seed": args.seed, "max_drafts": args.max_drafts,
        "class_counts_before": before, "class_counts_after": after,
        "total": len(kept),
    }
    manifest_path = os.path.join(os.path.dirname(args.output) or ".",
                                 "SC_BALANCE_MANIFEST.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  before: {before}")
    print(f"  after:  {after}  (total {len(kept)})")
    print(f"  Output:   {args.output}")
    print(f"  Manifest: {manifest_path}")
    print(f"  SHA256:   {manifest['output_sha256'][:16]}...")


if __name__ == "__main__":
    main()
