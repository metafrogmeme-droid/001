#!/usr/bin/env python3
r"""
RUNECLAW - Convert Merged Model to GGUF (Fully Offline)
=========================================================
Uses the gguf pip package (v0.19+) for correct GGUF encoding.

THE FAILURE THIS GUARDS AGAINST

This script converts whatever ./runeclaw-model-merged/ holds — and four
times (30-7 and 9-8-2026) that folder held a stale June 8B merge while a
freshly trained 3B sat unread in its checkpoint dir. The 8B GGUF shipped
under the 3B's name, once to the public registry. Conversion now refuses
to run when the merge's manifest is missing, older than the newest
checkpoint on disk, or inconsistent with the weights actually present —
and the finished GGUF's size must match the parameter count before the
script will call it done.

Usage:
  python convert_to_gguf.py               # gated; refuses stale input
  python convert_to_gguf.py --no-quant    # keep F16, skip Q4_K_M
  python convert_to_gguf.py --force       # bypass gates (prints why loudly)

Requires:
  - ./runeclaw-model-merged/  (from export_model.py, which writes the manifest)
  - pip packages: gguf>=0.19, safetensors, numpy

Output:
  ./runeclaw-model/unsloth.Q4_K_M.gguf  (or model-f16.gguf with --no-quant)
  ./runeclaw-model/Modelfile
"""

import argparse
import glob
import json
import os
import platform
import subprocess
import sys

import numpy as np

MODEL_DIR = "./runeclaw-model-merged"
OUTPUT_DIR = "./runeclaw-model"
LLAMA_CPP_DIR = "./llama-cpp-tools"
MANIFEST_NAME = "EXPORT_MANIFEST.json"

ADAPTER_WEIGHT_NAMES = ("adapter_model.safetensors", "adapter_model.bin")

# Empirical GGUF bytes per parameter: Q4_K_M mixes q4/q6 tensors,
# F16 is exactly 2. Used only for the ±tolerance size gate.
BYTES_PER_PARAM = {"q4_k_m": 0.62, "f16": 2.0}

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)

# Llama HF → GGUF tensor name mapping
TENSOR_MAP = {
    "model.embed_tokens.weight": "token_embd.weight",
    "model.norm.weight": "output_norm.weight",
    "lm_head.weight": "output.weight",
}

LAYER_TENSOR_MAP = {
    "model.layers.{}.self_attn.q_proj.weight": "blk.{}.attn_q.weight",
    "model.layers.{}.self_attn.k_proj.weight": "blk.{}.attn_k.weight",
    "model.layers.{}.self_attn.v_proj.weight": "blk.{}.attn_v.weight",
    "model.layers.{}.self_attn.o_proj.weight": "blk.{}.attn_output.weight",
    "model.layers.{}.mlp.gate_proj.weight": "blk.{}.ffn_gate.weight",
    "model.layers.{}.mlp.up_proj.weight": "blk.{}.ffn_up.weight",
    "model.layers.{}.mlp.down_proj.weight": "blk.{}.ffn_down.weight",
    "model.layers.{}.input_layernorm.weight": "blk.{}.attn_norm.weight",
    "model.layers.{}.post_attention_layernorm.weight": "blk.{}.ffn_norm.weight",
}


# ── Freshness / identity gates ────────────────────────────────────


def load_manifest(model_dir):
    """The EXPORT_MANIFEST.json written by export_model.py, or None."""
    path = os.path.join(model_dir, MANIFEST_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def newest_adapter_mtime(root="."):
    """mtime of the newest adapter weight file under any *checkpoints* dir,
    or None when no checkpoints exist. (Mirrors export_model.py's discovery;
    kept inline so this script stays copyable as a single file.)"""
    patterns = [
        os.path.join(root, "*checkpoints*", "final-adapter"),
        os.path.join(root, "*checkpoints*", "checkpoint-*"),
        os.path.join(root, "runeclaw-model", "checkpoint-*"),
    ]
    newest = None
    for pattern in patterns:
        for ckpt_dir in glob.glob(pattern):
            for name in ADAPTER_WEIGHT_NAMES:
                path = os.path.join(ckpt_dir, name)
                if os.path.exists(path):
                    mtime = os.path.getmtime(path)
                    if newest is None or mtime > newest:
                        newest = mtime
    return newest


def detect_params_from_merged(model_dir):
    """Parameter count implied by the merged weights on disk (fp16 = 2
    bytes/param), from the shard index or the file sizes. None if unreadable
    — and None must stay None; it is not zero."""
    index_path = os.path.join(model_dir, "model.safetensors.index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, "r") as f:
                total = json.load(f).get("metadata", {}).get("total_size")
            if total:
                return int(total // 2)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        st_bytes = sum(
            os.path.getsize(os.path.join(model_dir, f))
            for f in os.listdir(model_dir)
            if f.endswith(".safetensors")
        )
    except OSError:
        return None
    return int(st_bytes // 2) if st_bytes else None


def stale_reason(model_dir, root="."):
    """Why this merge must NOT be converted, or None if it is safe.

    Fail-closed: no manifest is itself a refusal — an unverifiable merge is
    treated as stale, not trusted.
    """
    manifest = load_manifest(model_dir)
    if manifest is None:
        return (f"{model_dir} has no {MANIFEST_NAME} — this merge predates the guarded "
                "export and cannot prove which checkpoint it came from. Re-run the "
                "updated export_model.py.")

    newest = newest_adapter_mtime(root)
    if newest is not None and newest > manifest.get("adapter_mtime", 0) + 1.0:
        return ("a checkpoint NEWER than this merge exists on disk — the merge is stale. "
                "Re-run export_model.py so the newest training result is what gets converted.")

    detected = detect_params_from_merged(model_dir)
    declared = manifest.get("param_count")
    if detected and declared and abs(detected - declared) / declared > 0.2:
        return (f"the weights on disk (~{detected / 1e9:.2f}B) do not match the manifest "
                f"({declared / 1e9:.2f}B) — the folder was modified after export. "
                "Re-run export_model.py.")
    return None


def gguf_size_ok(n_params, size_bytes, quantized):
    """Is the finished GGUF the size this parameter count demands?"""
    expected = n_params * BYTES_PER_PARAM["q4_k_m" if quantized else "f16"]
    return 0.75 * expected <= size_bytes <= 1.3 * expected


def map_tensor_name(hf_name):
    """Map HuggingFace tensor name to GGUF name."""
    if hf_name in TENSOR_MAP:
        return TENSOR_MAP[hf_name]
    for hf_pattern, gguf_pattern in LAYER_TENSOR_MAP.items():
        parts = hf_name.split(".")
        try:
            layer_idx = None
            for i, p in enumerate(parts):
                if p == "layers" and i + 1 < len(parts):
                    layer_idx = int(parts[i + 1])
                    break
            if layer_idx is not None:
                if hf_pattern.format(layer_idx) == hf_name:
                    return gguf_pattern.format(layer_idx)
        except (ValueError, IndexError):
            continue
    return None


def convert_hf_to_gguf(model_name):
    """Convert HuggingFace safetensors model to F16 GGUF using gguf package."""
    from gguf import GGMLQuantizationType, GGUFWriter
    from safetensors import safe_open

    print("\n[1/2] Reading model config...")

    config_path = os.path.join(MODEL_DIR, "config.json")
    with open(config_path) as f:
        config = json.load(f)

    arch = "llama"
    vocab_size = config.get("vocab_size", 128256)
    hidden_size = config.get("hidden_size", 3072)
    intermediate_size = config.get("intermediate_size", 8192)
    num_layers = config.get("num_hidden_layers", 28)
    num_heads = config.get("num_attention_heads", 24)
    num_kv_heads = config.get("num_key_value_heads", 8)
    rms_eps = config.get("rms_norm_eps", 1e-5)
    rope_theta = config.get("rope_theta", 500000.0)
    max_pos = config.get("max_position_embeddings", 131072)
    bos_id = config.get("bos_token_id", 128000)
    eos_id = config.get("eos_token_id", 128001)

    print(f"  Architecture: {arch}")
    print(f"  Layers: {num_layers}, Hidden: {hidden_size}")
    print(f"  Heads: {num_heads}, KV Heads: {num_kv_heads}")
    print(f"  Vocab: {vocab_size}")

    # ── Set up GGUF writer ────────────────────────────────
    print("\n[2/2] Creating GGUF file...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    # Remove old files
    for old in ["model-f16.gguf", "unsloth.Q4_K_M.gguf"]:
        old_path = os.path.join(OUTPUT_DIR, old)
        if os.path.exists(old_path):
            os.remove(old_path)
            print(f"  Removed old {old}")

    writer = GGUFWriter(f16_path, arch)

    # Write metadata
    writer.add_name(model_name)
    writer.add_block_count(num_layers)
    writer.add_context_length(max_pos)
    writer.add_embedding_length(hidden_size)
    writer.add_feed_forward_length(intermediate_size)
    writer.add_head_count(num_heads)
    writer.add_head_count_kv(num_kv_heads)
    writer.add_layer_norm_rms_eps(rms_eps)
    writer.add_rope_freq_base(rope_theta)
    writer.add_file_type(GGMLQuantizationType.F16)

    # ── Tokenizer ─────────────────────────────────────────
    print("  Loading tokenizer...")
    tokenizer_path = os.path.join(MODEL_DIR, "tokenizer.json")
    if os.path.exists(tokenizer_path):
        with open(tokenizer_path, "r", encoding="utf-8") as f:
            tokenizer_data = json.load(f)

        model_data = tokenizer_data.get("model", {})
        vocab = model_data.get("vocab", {})

        if vocab:
            tokens = [b""] * vocab_size
            scores = [0.0] * vocab_size
            token_types = [0] * vocab_size

            for token_str, token_id in vocab.items():
                if token_id < vocab_size:
                    tokens[token_id] = token_str.encode("utf-8", errors="replace")
                    if token_str.startswith("<|") or token_str.startswith("<s>") or token_str == "</s>":
                        token_types[token_id] = 2

            # BPE merges
            merges = model_data.get("merges", [])
            # Ensure merges are strings (not lists)
            clean_merges = []
            for m in merges:
                if isinstance(m, list):
                    clean_merges.append(" ".join(m))
                elif isinstance(m, str):
                    clean_merges.append(m)
                else:
                    clean_merges.append(str(m))

            writer.add_tokenizer_model("gpt2")
            writer.add_token_list(tokens)
            writer.add_token_scores(scores)
            writer.add_token_types(token_types)

            if clean_merges:
                writer.add_token_merges(clean_merges)
                print(f"  BPE merges: {len(clean_merges)}")

            writer.add_bos_token_id(bos_id)
            writer.add_eos_token_id(eos_id)

            # Chat template
            tc_path = os.path.join(MODEL_DIR, "tokenizer_config.json")
            if os.path.exists(tc_path):
                with open(tc_path, "r", encoding="utf-8") as tc:
                    tc_data = json.load(tc)
                chat_tmpl = tc_data.get("chat_template", "")
                if chat_tmpl:
                    writer.add_chat_template(chat_tmpl)
                    print("  Chat template: OK")

            print(f"  Tokenizer: {len(vocab)} tokens")
        else:
            print("  WARNING: No vocab in tokenizer.json")
            writer.add_tokenizer_model("gpt2")
    else:
        print("  WARNING: tokenizer.json not found")
        writer.add_tokenizer_model("gpt2")

    # ── Load tensors ──────────────────────────────────────
    print("  Loading tensors from safetensors...")
    st_files = sorted([f for f in os.listdir(MODEL_DIR) if f.endswith(".safetensors")])
    print(f"  Found {len(st_files)} safetensors files")

    tensor_count = 0
    skipped = []
    seen = set()

    for st_file in st_files:
        st_path = os.path.join(MODEL_DIR, st_file)
        print(f"  Processing {st_file}...")

        with safe_open(st_path, framework="numpy") as sf:
            for hf_name in sf.keys():
                gguf_name = map_tensor_name(hf_name)
                if gguf_name is None:
                    skipped.append(hf_name)
                    continue
                if gguf_name in seen:
                    continue
                seen.add(gguf_name)

                tensor = sf.get_tensor(hf_name)
                if tensor.dtype != np.float16:
                    tensor = tensor.astype(np.float16)

                writer.add_tensor(gguf_name, tensor)
                tensor_count += 1

                if tensor_count % 20 == 0:
                    print(f"    {tensor_count} tensors...")

    if skipped:
        print(f"  Skipped {len(skipped)} unmapped tensors")
    print(f"  Total tensors: {tensor_count}")

    # ── Finalize ──────────────────────────────────────────
    print("  Writing GGUF file (this takes a minute)...")
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    size_gb = os.path.getsize(f16_path) / 1024**3
    print(f"  F16 GGUF: {f16_path} ({size_gb:.1f} GB)")

    # Verify
    with open(f16_path, "rb") as vf:
        magic = vf.read(4)
        print(f"  Verify magic: {magic} {'OK' if magic == b'GGUF' else 'FAIL'}")

    return f16_path


def quantize(f16_path):
    """Quantize F16 → Q4_K_M using pre-built binary."""
    print("\n[3/3] Quantizing to Q4_K_M...")

    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_bin = None

    for root, _dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == quantize_name:
                quantize_bin = os.path.join(root, f)
                break
        if quantize_bin:
            break

    q4_path = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")

    if not quantize_bin or not os.path.exists(quantize_bin):
        print(f"  WARNING: {quantize_name} not found in {LLAMA_CPP_DIR}/")
        print("  Using F16 GGUF directly.")
        return f16_path

    if platform.system() != "Windows":
        os.chmod(quantize_bin, 0o755)

    print(f"  Using: {quantize_bin}")
    print(f"  {f16_path} -> {q4_path}...")

    result = subprocess.run(
        [quantize_bin, f16_path, q4_path, "Q4_K_M"],
        text=True,
    )

    if result.returncode == 0 and os.path.exists(q4_path):
        size_gb = os.path.getsize(q4_path) / 1024**3
        print(f"  Q4_K_M: {size_gb:.1f} GB")
        print("  Removing intermediate F16...")
        os.remove(f16_path)
        return q4_path

    print("  Quantization failed, using F16 directly.")
    return f16_path


def create_modelfile(gguf_filename):
    """Create Ollama Modelfile."""
    path = os.path.join(OUTPUT_DIR, "Modelfile")
    with open(path, "w") as f:
        f.write(f'FROM ./{gguf_filename}\n\n')
        f.write('PARAMETER temperature 0.3\n')
        f.write('PARAMETER top_p 0.9\n')
        f.write('PARAMETER num_ctx 4096\n')
        f.write('PARAMETER stop "<|eot_id|>"\n')
        f.write('PARAMETER stop "<|end|>"\n\n')
        f.write(f'SYSTEM """{SYSTEM_PROMPT}"""\n')
    print(f"  Modelfile: {path}")


def main():
    parser = argparse.ArgumentParser(description="Convert the merged model to GGUF, refusing stale input")
    parser.add_argument("--force", action="store_true",
                        help="bypass the freshness/manifest gates (announces itself loudly)")
    parser.add_argument("--no-quant", action="store_true",
                        help="keep the F16 GGUF instead of quantizing to Q4_K_M")
    args = parser.parse_args()

    print("=" * 60)
    print("RUNECLAW - Convert to GGUF")
    print("=" * 60)

    if not os.path.exists(MODEL_DIR):
        print(f"\nERROR: {MODEL_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    # ── The gate that four exports needed ──────────────────
    reason = stale_reason(MODEL_DIR, ".")
    if reason is not None:
        if args.force:
            print(f"\nWARNING (bypassed with --force): {reason}")
        else:
            print(f"\nERROR: refusing to convert — {reason}")
            sys.exit(1)

    manifest = load_manifest(MODEL_DIR)
    n_params = manifest.get("param_count") if manifest else None
    if n_params is None:
        n_params = detect_params_from_merged(MODEL_DIR)
    if n_params is None:
        print("\nERROR: cannot determine the merged model's parameter count — "
              "an unverifiable model must not be shipped.")
        sys.exit(1)

    if manifest:
        print(f"\nConverting: {manifest.get('checkpoint', '?')}")
        print(f"  Base:       {manifest.get('base_model', '?')}")
    print(f"  Parameters: {n_params / 1e9:.2f}B")

    # Install/upgrade gguf to known-good version
    print("\nChecking dependencies...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "gguf>=0.19", "--upgrade", "-q"],
        check=False,
    )

    try:
        import gguf
        print(f"  gguf: {gguf.__version__ if hasattr(gguf, '__version__') else 'OK'}")
    except ImportError:
        print("  ERROR: Failed to install gguf!")
        sys.exit(1)

    try:
        from safetensors import safe_open  # noqa: F401
        print("  safetensors: OK")
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "safetensors", "-q"])

    print("  numpy: OK")

    print(f"\nInput:  {MODEL_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    # Clean old Ollama model
    print("\nCleaning old Ollama model...")
    subprocess.run(["ollama", "rm", "runeclaw"], capture_output=True, text=True)

    model_name = f"runeclaw-{n_params / 1e9:.1f}b"
    f16_path = convert_hf_to_gguf(model_name)

    final_path = f16_path if args.no_quant else quantize(f16_path)
    final_name = os.path.basename(final_path)
    quantized = "Q4_K_M" in final_name

    # ── Final size gate: the GGUF must weigh what its params demand ──
    size_bytes = os.path.getsize(final_path)
    size_gb = size_bytes / 1024**3
    expected_gb = n_params * BYTES_PER_PARAM["q4_k_m" if quantized else "f16"] / 1024**3
    if not gguf_size_ok(n_params, size_bytes, quantized):
        print(f"\nERROR: the finished GGUF is {size_gb:.1f} GB but a {n_params / 1e9:.2f}B model "
              f"at {'Q4_K_M' if quantized else 'F16'} should be ~{expected_gb:.1f} GB.")
        print("The wrong weights were converted. Do NOT create or push this file.")
        sys.exit(1)
    print(f"\n  Size check OK: {size_gb:.1f} GB (~{expected_gb:.1f} GB expected "
          f"for {n_params / 1e9:.2f}B)")

    # Modelfile
    create_modelfile(final_name)

    print(f"\n{'=' * 60}")
    print(f"DONE! GGUF conversion complete: {n_params / 1e9:.2f}B, {size_gb:.1f} GB.")
    print(f"{'=' * 60}")
    print(f"""
Next steps (run `ollama show` BEFORE any push — it reads the GGUF's own
metadata and is the check that catches a wrong model):

  cd {OUTPUT_DIR}
  ollama create runeclaw -f Modelfile
  ollama show runeclaw            <-- parameters must read ~{n_params / 1e9:.1f}B
  ollama run runeclaw "Scan BTC/USDT for trade setups"

Then, and only then:
  ollama cp runeclaw pbdes2022/humanoid-traders
  ollama push pbdes2022/humanoid-traders
""")


if __name__ == "__main__":
    main()
