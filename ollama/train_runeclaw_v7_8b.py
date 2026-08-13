#!/usr/bin/env python3
r"""
RUNECLAW v7 - Llama 3.1 8B QLoRA Fine-Tune (RTX 5090 Laptop, 24GB)
====================================================================
The 8B is the largest base that still serves FULLY in VRAM on the 8GB
deployment box (Q4_K_M = 4.9 GB), which is why it is the v7 target.

Lessons from the v6 run baked in:
- VRAM is READ, never defaulted: an unreadable GPU raises instead of
  printing "0.0 GB" and training on a profile nobody chose.
- The dataset is hashed and the hash saved with the checkpoints, so
  "what trained v7?" has a permanent answer (v6's answer took archaeology).
- Checkpoints go to ./runeclaw-8b-<run>-checkpoints/ (--run-name, default
  "v7") and a final-adapter/ is always written — the guarded export_model.py
  discovers it by mtime. Give every training generation a fresh --run-name.
- Per-epoch checkpoints + --resume, so a crash costs hours, not days.
- Trains on the CURATED set (run curate_training_data.py first) — an 8B on
  clean data beats a bigger model on the duplicated mega-merge.

Usage:
  venv\Scripts\activate
  python curate_training_data.py                 # once, before first run
  python train_runeclaw_v7_8b.py
  python train_runeclaw_v7_8b.py --resume        # continue after a crash
  python train_runeclaw_v7_8b.py --data path.jsonl --epochs 1
  python train_runeclaw_v7_8b.py --data training_data\curated_v8_final.jsonl --run-name v8

Then:
  python export_model.py --expect-base 8B
  python convert_to_gguf.py
"""

import argparse
import hashlib
import json
import os
import sys
import time

# ── Configuration ───────────────────────────────────────────────

BASE_MODEL = "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit"


def checkpoint_dir(run_name):
    """./runeclaw-8b-<run>-checkpoints — matches the '*checkpoints*' glob
    export_model.py discovers by mtime, so a v8 run cannot silently write
    into (or resume from) the v7 directory."""
    return f"./runeclaw-8b-{run_name}-checkpoints"

DATA_PATHS = [
    "./training_data/curated_v7.jsonl",
    "./v6_training_data/combined_training_v6.jsonl",  # fallback: uncurated
]

MAX_SEQ = 1024          # v6 measured avg 368 tokens, 2% truncated at 1024
LORA_RANK = 32
LORA_ALPHA = 32
LEARNING_RATE = 1e-4
WARMUP_STEPS = 50

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)


class VramUnreadable(RuntimeError):
    """The GPU's memory could not be measured. 0.0 is not a measurement."""


def read_vram_gb(torch):
    if not torch.cuda.is_available():
        raise VramUnreadable("CUDA not available — cannot train.")
    props = torch.cuda.get_device_properties(0)
    total = getattr(props, "total_memory", None)
    if not total:
        raise VramUnreadable(
            "torch reports no total_memory for this GPU. Refusing to guess "
            "a training profile — fix the driver/torch install first.")
    return total / 1024**3


def training_profile(vram_gb):
    """Batch/accum for an 8B 4-bit base + rank-32 LoRA at seq 1024.

    The dominant transient is the logits tensor
    (batch x 1024 x 128256 x 2 bytes, upcast to fp32 for the loss), on top
    of ~5.5 GB weights + ~1.5 GB LoRA/optimizer. Effective batch stays 16
    (v6's schedule) at every tier so runs are comparable.
    """
    if vram_gb >= 20:
        return {"batch": 4, "accum": 4}
    if vram_gb >= 12:
        return {"batch": 2, "accum": 8}
    raise VramUnreadable(
        f"{vram_gb:.1f} GB is not enough for an 8B QLoRA at seq {MAX_SEQ}. "
        "Use the 3B script on this machine, or train on the 24GB box.")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def vram_now(torch):
    return torch.cuda.memory_allocated() / 1024**3


def main():
    parser = argparse.ArgumentParser(description="RUNECLAW v7 8B QLoRA fine-tune")
    parser.add_argument("--data", help="training jsonl (default: curated_v7, then v6)")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--run-name", default="v7",
                        help="names the checkpoint dir runeclaw-8b-<run>-checkpoints; "
                             "use a fresh name per training generation (e.g. v8) so "
                             "runs never share or overwrite checkpoints")
    parser.add_argument("--resume", action="store_true",
                        help="resume from the latest checkpoint in the run's checkpoint dir")
    parser.add_argument("--safe", action="store_true",
                        help="micro-batch 2 x accum 8 (same effective 16) — several GB "
                             "lower peak VRAM so training survives sharing the GPU with "
                             "desktop apps; two daytime OOMs bought this flag")
    args = parser.parse_args()
    ckpt_dir = checkpoint_dir(args.run_name)

    print("=" * 60)
    print(f"RUNECLAW {args.run_name} - Llama 3.1 8B QLoRA Fine-Tune")
    print("=" * 60)
    print(f"  Checkpoints: {ckpt_dir}")

    # ── [1/6] GPU ─────────────────────────────────────────────
    print("\n[1/6] Reading GPU (honestly)...")
    import torch
    vram_gb = read_vram_gb(torch)
    profile = {"batch": 2, "accum": 8} if args.safe else training_profile(vram_gb)
    print(f"  GPU:  {torch.cuda.get_device_name(0)}")
    print(f"  VRAM: {vram_gb:.1f} GB -> batch {profile['batch']} x accum "
          f"{profile['accum']} (effective 16)"
          + ("  [--safe: lower peak for a shared GPU]" if args.safe else ""))
    print(f"  PyTorch {torch.__version__}, CUDA {torch.version.cuda}")

    # ── [2/6] Dataset (hashed BEFORE training) ────────────────
    print("\n[2/6] Loading training data...")
    data_file = args.data
    if data_file is None:
        for candidate in DATA_PATHS:
            if os.path.exists(candidate):
                data_file = candidate
                break
    if data_file is None or not os.path.exists(data_file):
        print("  ERROR: no training data found. Run curate_training_data.py "
              "or pass --data <file.jsonl>.")
        sys.exit(1)

    data_sha = sha256_file(data_file)
    rows = []
    with open(data_file, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"  {data_file}: {len(rows)} samples")
    print(f"  SHA256: {data_sha[:16]}...")
    if "curated" not in os.path.basename(data_file):
        print("  WARNING: this does not look like a curated set. Duplicated "
              "samples waste GPU-days — consider curate_training_data.py first.")

    # ── [3/6] Model + LoRA ────────────────────────────────────
    print("\n[3/6] Loading Llama 3.1 8B (4-bit) + LoRA rank 32...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ,
        dtype=None,
        load_in_4bit=True,
    )
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print(f"  VRAM after model load: {vram_now(torch):.2f} GB")

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    model.print_trainable_parameters()
    print(f"  VRAM after LoRA: {vram_now(torch):.2f} GB")

    # ── [4/6] Format + tokenize (dynamic length, no pad waste) ─
    print("\n[4/6] Formatting and tokenizing...")
    formatted = []
    for example in rows:
        user_msg = example["instruction"]
        if example.get("input") and example["input"].strip():
            user_msg += "\n\n" + example["input"]
        text = tokenizer.apply_chat_template(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_msg},
             {"role": "assistant", "content": example["output"]}],
            tokenize=False, add_generation_prompt=False,
        )
        formatted.append(text)

    sample_lengths = [len(tokenizer(t, truncation=False)["input_ids"])
                      for t in formatted[:500]]
    over = sum(1 for length in sample_lengths if length > MAX_SEQ)
    print(f"  Avg tokens: {sum(sample_lengths) / len(sample_lengths):.0f}, "
          f"max {max(sample_lengths)}, over {MAX_SEQ}: {over}/{len(sample_lengths)}")

    from torch.nn.utils.rnn import pad_sequence
    from torch.utils.data import Dataset as TorchDataset

    class DynamicDataset(TorchDataset):
        def __init__(self, texts):
            self.items = []
            print(f"  Tokenizing {len(texts)} samples...")
            for i, text in enumerate(texts):
                ids = tokenizer(text, truncation=True, max_length=MAX_SEQ,
                                padding=False, return_tensors="pt")["input_ids"].squeeze()
                self.items.append({"input_ids": ids,
                                   "attention_mask": torch.ones_like(ids),
                                   "labels": ids.clone()})
                if (i + 1) % 20000 == 0:
                    print(f"    {i + 1}/{len(texts)}...")

        def __len__(self):
            return len(self.items)

        def __getitem__(self, idx):
            return self.items[idx]

    class DynamicPadCollator:
        def __call__(self, batch):
            return {
                "input_ids": pad_sequence([b["input_ids"] for b in batch],
                                          batch_first=True,
                                          padding_value=tokenizer.pad_token_id),
                "labels": pad_sequence([b["labels"] for b in batch],
                                       batch_first=True, padding_value=-100),
                "attention_mask": pad_sequence([b["attention_mask"] for b in batch],
                                               batch_first=True, padding_value=0),
            }

    train_dataset = DynamicDataset(formatted)

    # ── [5/6] Train ───────────────────────────────────────────
    steps_per_epoch = len(train_dataset) // (profile["batch"] * profile["accum"])
    total_steps = steps_per_epoch * args.epochs
    print(f"\n[5/6] Training: {args.epochs} epochs = ~{total_steps} steps")
    print("  The first 20 logged steps give the real s/it — multiply by "
          f"{total_steps} for the true ETA before walking away.")

    from transformers import Trainer, TrainingArguments

    training_args = TrainingArguments(
        per_device_train_batch_size=profile["batch"],
        gradient_accumulation_steps=profile["accum"],
        warmup_steps=WARMUP_STEPS,
        num_train_epochs=args.epochs,
        learning_rate=LEARNING_RATE,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=42,
        output_dir=ckpt_dir,
        # Every-N-steps, not per-epoch: a 27h run OOM'd at 52% when another
        # process claimed VRAM, and per-epoch saves would have cost a full
        # day of epoch-2 progress. 1000 steps caps the loss at ~4h.
        save_strategy="steps",
        save_steps=1000,
        save_total_limit=3,
        report_to="none",
        dataloader_pin_memory=True,
        dataloader_num_workers=0,
    )

    trainer = Trainer(model=model, args=training_args,
                      train_dataset=train_dataset,
                      data_collator=DynamicPadCollator())

    # Provenance lands next to the checkpoints BEFORE training starts, so
    # even an aborted run says what it was training on.
    os.makedirs(ckpt_dir, exist_ok=True)
    with open(os.path.join(ckpt_dir, "TRAINING_MANIFEST.json"), "w") as f:
        json.dump({
            "run_name": args.run_name,
            "base_model": BASE_MODEL,
            "data_file": data_file,
            "data_sha256": data_sha,
            "n_samples": len(rows),
            "epochs": args.epochs,
            "max_seq": MAX_SEQ,
            "lora_rank": LORA_RANK,
            "lora_alpha": LORA_ALPHA,
            "learning_rate": LEARNING_RATE,
            "batch": profile["batch"],
            "grad_accum": profile["accum"],
            "started_at": time.time(),
        }, f, indent=2)

    stats = trainer.train(resume_from_checkpoint=True if args.resume else None)

    print("\n  Training complete!")
    print(f"  Final loss: {stats.training_loss:.4f}")
    print(f"  Runtime: {stats.metrics['train_runtime'] / 3600:.1f} h")

    # ── [6/6] Save the final adapter where export discovers it ─
    print("\n[6/6] Saving final adapter...")
    final_dir = os.path.join(ckpt_dir, "final-adapter")
    model.save_pretrained(final_dir)
    tokenizer.save_pretrained(final_dir)
    print(f"  {final_dir}/  (adapter + config)")

    print("\n" + "=" * 60)
    print("DONE. Next steps (each one gated):")
    print("  python export_model.py --expect-base 8B")
    print("  python convert_to_gguf.py        # expects ~4.9 GB for 8B")
    print("  ollama create pbdes2022/HUMANOID-TRADERS:v7-8b -f runeclaw-model/Modelfile")
    print("  ollama show pbdes2022/HUMANOID-TRADERS:v7-8b   # must read 8.0B")
    print("=" * 60)


if __name__ == "__main__":
    main()
