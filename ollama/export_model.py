r"""
RUNECLAW - Export Merged Model from Checkpoint
================================================
Uses transformers + peft (NOT unsloth).

THE FAILURE THIS GUARDS AGAINST

Four consecutive exports (30-7 and 9-8-2026) silently merged a stale June
8B adapter and shipped it under the 3B model's name — one copy reached the
public Ollama registry. The cause was checkpoint selection by a hardcoded
priority list: `runeclaw-8b-max-checkpoints` was first in the list, and the
folder the fresh run actually wrote (`runeclaw-3b-checkpoints`) was not in
the list at all. Selection is now by NEWEST adapter-weight mtime across
every `*checkpoints*` directory, the resolved choice and its base model are
printed and gated, and the merged parameter count is checked against the
base model's own name before anything is written.

Usage:
  python export_model.py                       # newest adapter wins
  python export_model.py --checkpoint PATH     # pin one explicitly
  python export_model.py --expect-base 3B      # hard-fail unless base matches

Output:
  ./runeclaw-model-merged/   (safetensors + tokenizer + EXPORT_MANIFEST.json)

The manifest records which checkpoint produced the merge; convert_to_gguf.py
refuses to convert a merge that predates the newest checkpoint on disk.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

ADAPTER_WEIGHT_NAMES = ("adapter_model.safetensors", "adapter_model.bin")

OUTPUT_DIR = "./runeclaw-model-merged"
MANIFEST_NAME = "EXPORT_MANIFEST.json"

# Quantized-base → full-precision equivalents. LoRA adapters carry only
# delta weights, so the full-precision twin is merge-compatible.
QUANT_TO_FULL = {
    "unsloth/Llama-3.2-3B-Instruct-bnb-4bit": "unsloth/Llama-3.2-3B-Instruct",
    "unsloth/Llama-3.2-1B-Instruct-bnb-4bit": "unsloth/Llama-3.2-1B-Instruct",
    "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit": "unsloth/Meta-Llama-3.1-8B-Instruct",
    "unsloth/Llama-3.1-8B-Instruct-bnb-4bit": "unsloth/Meta-Llama-3.1-8B-Instruct",
}


def adapter_weight_mtime(checkpoint_dir):
    """mtime of the adapter weights in a checkpoint dir, or None if absent."""
    newest = None
    for name in ADAPTER_WEIGHT_NAMES:
        path = os.path.join(checkpoint_dir, name)
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def read_adapter_base(checkpoint_dir):
    """Base model named by the adapter's own config, or None if unreadable.

    None stays None: a checkpoint without adapter_config.json has an UNKNOWN
    base, not a default one. The caller must refuse it, not guess.
    """
    config_path = os.path.join(checkpoint_dir, "adapter_config.json")
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return config.get("base_model_name_or_path") or None


def discover_checkpoints(root="."):
    """Every checkpoint dir under root that contains adapter weights,
    newest adapter first. Each entry: {path, mtime, base}."""
    patterns = [
        os.path.join(root, "*checkpoints*", "final-adapter"),
        os.path.join(root, "*checkpoints*", "checkpoint-*"),
        os.path.join(root, "runeclaw-model", "checkpoint-*"),
    ]
    seen = set()
    entries = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            path = os.path.normpath(path)
            if path in seen or not os.path.isdir(path):
                continue
            seen.add(path)
            mtime = adapter_weight_mtime(path)
            if mtime is None:
                continue  # no weights → not a usable checkpoint
            entries.append({"path": path, "mtime": mtime, "base": read_adapter_base(path)})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def base_matches(base, expect):
    """Does the adapter's base model name satisfy --expect-base?

    An unknown base (None) never matches: unreadable must not pass a gate.
    """
    if not base:
        return False
    return expect.lower() in base.lower()


def params_consistent_with_base(n_params, base):
    """The merged parameter count must agree with the size claimed in the
    base model's name (e.g. '3B', '8B'). A base whose name makes no size
    claim cannot be checked and passes."""
    if not base:
        return False
    match = re.search(r"(\d+(?:\.\d+)?)\s*B", base, re.IGNORECASE)
    if not match:
        return True
    claimed = float(match.group(1)) * 1e9
    return 0.6 * claimed <= n_params <= 1.4 * claimed


def resolve_full_precision(base):
    """Map a quantized base to its full-precision twin (4-bit weights can't
    be loaded as float16 for export)."""
    if base in QUANT_TO_FULL:
        return QUANT_TO_FULL[base]
    if "-bnb-4bit" in base:
        return base.replace("-bnb-4bit", "")
    return base


def _fmt_ts(mtime):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def main():
    parser = argparse.ArgumentParser(description="Merge the newest LoRA checkpoint into its base model")
    parser.add_argument("--checkpoint", help="checkpoint dir to export (default: newest adapter on disk)")
    parser.add_argument("--expect-base", help="fail unless the adapter's base model name contains this "
                                              "substring, e.g. '3B' or 'Llama-3.2'")
    args = parser.parse_args()

    print("=" * 60)
    print("RUNECLAW - Export Merged Model")
    print("=" * 60)

    if args.checkpoint:
        checkpoint = os.path.normpath(args.checkpoint)
        if adapter_weight_mtime(checkpoint) is None:
            print(f"\nERROR: {checkpoint} contains no adapter weights "
                  f"({' / '.join(ADAPTER_WEIGHT_NAMES)}).")
            sys.exit(1)
        candidates = [{"path": checkpoint, "mtime": adapter_weight_mtime(checkpoint),
                       "base": read_adapter_base(checkpoint)}]
    else:
        candidates = discover_checkpoints(".")
        if not candidates:
            print("\nERROR: No checkpoint with adapter weights found under ./*checkpoints*/")
            sys.exit(1)

    print("\nCheckpoints found (newest adapter first):")
    for i, entry in enumerate(candidates[:8]):
        marker = "→" if i == 0 else " "
        print(f"  {marker} {_fmt_ts(entry['mtime'])}  {entry['path']}")
        print(f"      base: {entry['base'] or 'UNKNOWN (no adapter_config.json)'}")

    chosen = candidates[0]
    checkpoint, base = chosen["path"], chosen["base"]

    if base is None:
        print(f"\nERROR: {checkpoint} has no readable adapter_config.json — its base model is "
              "unknown, and an unknown base must not be guessed. Pass --checkpoint to a "
              "checkpoint that carries its config.")
        sys.exit(1)

    if args.expect_base and not base_matches(base, args.expect_base):
        print(f"\nERROR: expected a base matching '{args.expect_base}' but the newest "
              f"checkpoint was trained on '{base}'.")
        print("If that older model is really what you want, pass it via --checkpoint explicitly.")
        sys.exit(1)

    base_model = resolve_full_precision(base)
    print(f"\nSelected: {checkpoint}")
    print(f"  Adapter trained on: {base}")
    if base_model != base:
        print(f"  Using full-precision: {base_model}")
        print("  (4-bit weights can't be exported — full-precision is compatible)")

    # ── Test torch first ──────────────────────────────────────
    print("\n[0/3] Testing PyTorch...")
    try:
        import torch
        print(f"  PyTorch {torch.__version__}")
        if torch.cuda.is_available():
            print(f"  CUDA: {torch.cuda.get_device_name(0)}")
        else:
            print("  CUDA not available (will use CPU for merge - slower but works)")
    except ImportError as e:
        print(f"\n  ERROR: Cannot import torch: {e}")
        sys.exit(1)

    # ── Step 1: Load base model + LoRA adapter ────────────────
    print("\n[1/3] Loading base model + LoRA adapter...")
    print("  Loading in float16 (NOT 4-bit) so weights can be saved.")

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"  Loading base: {base_model}")
    base_lm = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        device_map="cpu",  # CPU to avoid VRAM limits, uses RAM instead
    )

    print("  Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"  Loading LoRA from {checkpoint}...")
    model = PeftModel.from_pretrained(base_lm, checkpoint)
    print("  Loaded.")

    # ── Step 2: Merge and save ────────────────────────────────
    print("\n[2/3] Merging LoRA weights...")
    model = model.merge_and_unload()

    n_params = sum(p.numel() for p in model.parameters())
    print(f"  Merged parameters: {n_params / 1e9:.2f}B")
    if not params_consistent_with_base(n_params, base):
        print(f"\nERROR: merged model has {n_params / 1e9:.2f}B parameters but the adapter's "
              f"base is named '{base}'. Something merged the wrong weights — refusing to save.")
        sys.exit(1)

    # A stale merge with MORE shards than the new one would leave orphan
    # shards behind, and the converter would blend two models into one GGUF.
    # The output dir always starts empty.
    if os.path.isdir(OUTPUT_DIR):
        print(f"  Clearing previous contents of {OUTPUT_DIR}/ ...")
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)

    print(f"  Saving to {OUTPUT_DIR}/ ...")

    # Save state dict directly (bypasses broken revert_weight_conversion)
    from safetensors.torch import save_file
    state_dict = model.state_dict()

    # Split into shards if large (>2GB per shard)
    shard_size = 2 * 1024**3  # 2GB
    total_bytes = sum(v.numel() * v.element_size() for v in state_dict.values())

    if total_bytes > shard_size:
        # Multi-shard save
        current_shard = {}
        current_bytes = 0
        shard_idx = 1
        shard_files = []
        weight_map = {}

        for key, tensor in state_dict.items():
            tensor_bytes = tensor.numel() * tensor.element_size()
            if current_bytes + tensor_bytes > shard_size and current_shard:
                fname = f"model-{shard_idx:05d}-of-PLACEHOLDER.safetensors"
                shard_files.append(fname)
                save_file(current_shard, os.path.join(OUTPUT_DIR, fname))
                for k in current_shard:
                    weight_map[k] = fname
                print(f"    Saved shard {shard_idx} ({current_bytes / 1024**3:.1f} GB)")
                current_shard = {}
                current_bytes = 0
                shard_idx += 1
            current_shard[key] = tensor.contiguous().to(torch.float16)
            current_bytes += tensor_bytes

        if current_shard:
            fname = f"model-{shard_idx:05d}-of-PLACEHOLDER.safetensors"
            shard_files.append(fname)
            save_file(current_shard, os.path.join(OUTPUT_DIR, fname))
            for k in current_shard:
                weight_map[k] = fname
            print(f"    Saved shard {shard_idx} ({current_bytes / 1024**3:.1f} GB)")

        # Fix shard filenames (use os.replace for Windows compatibility)
        total_shards = len(shard_files)
        for i, old_name in enumerate(shard_files):
            new_name = old_name.replace("PLACEHOLDER", f"{total_shards:05d}")
            old_path = os.path.join(OUTPUT_DIR, old_name)
            new_path = os.path.join(OUTPUT_DIR, new_name)
            if os.path.exists(new_path):
                os.remove(new_path)
            os.replace(old_path, new_path)
            for k in weight_map:
                if weight_map[k] == old_name:
                    weight_map[k] = new_name

        # Write index
        index = {"metadata": {"total_size": total_bytes}, "weight_map": weight_map}
        with open(os.path.join(OUTPUT_DIR, "model.safetensors.index.json"), "w") as f:
            json.dump(index, f, indent=2)
    else:
        # Single file save
        clean_dict = {k: v.contiguous().to(torch.float16) for k, v in state_dict.items()}
        save_file(clean_dict, os.path.join(OUTPUT_DIR, "model.safetensors"))
        print(f"    Saved single file ({total_bytes / 1024**3:.1f} GB)")

    # Save config and tokenizer
    model.config.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # The manifest is what lets convert_to_gguf.py refuse a stale merge.
    manifest = {
        "checkpoint": checkpoint,
        "adapter_mtime": chosen["mtime"],
        "base_model": base_model,
        "adapter_base": base,
        "param_count": n_params,
        "created_at": time.time(),
    }
    with open(os.path.join(OUTPUT_DIR, MANIFEST_NAME), "w") as f:
        json.dump(manifest, f, indent=2)
    print("  Saved (weights + tokenizer + manifest).")

    # ── Step 3: Verify ────────────────────────────────────────
    print("\n[3/3] Verifying output...")

    total_size = 0
    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for fname in files:
            total_size += os.path.getsize(os.path.join(root, fname))
    size_gb = total_size / 1024**3
    expected_gb = n_params * 2 / 1024**3  # float16 = 2 bytes/param
    print(f"  Total size: {size_gb:.1f} GB (expected ~{expected_gb:.1f} GB for "
          f"{n_params / 1e9:.2f}B at fp16)")

    for fname in ["config.json", "tokenizer.json"]:
        path = os.path.join(OUTPUT_DIR, fname)
        status = "OK" if os.path.exists(path) else "MISSING"
        print(f"  {status}: {fname}")

    safetensors = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".safetensors")]
    print(f"  Safetensors files: {len(safetensors)}")

    print(f"\n{'=' * 60}")
    print(f"Merged model saved: {n_params / 1e9:.2f}B from {checkpoint}")
    print("\nNext step:")
    print("  python convert_to_gguf.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
