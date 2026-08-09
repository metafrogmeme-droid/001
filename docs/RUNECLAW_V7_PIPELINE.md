# RUNECLAW v7 — train on the RTX 5090, serve on the 8GB box

The complete pipeline for the recommended configuration: **Llama 3.1 8B
QLoRA**, fine-tuned on the ProArt (RTX 5090 Laptop, 24 GB) on a **curated**
dataset, served **fully in VRAM** on the 8GB RTX machine (64 GB DDR). The 8B
is the largest model that still fits the deployment card at 100% GPU — that
constraint, not the training card, picks the size.

Chart analysis note: RUNECLAW "reads charts" as **numbers** — the confluence
engine computes RSI/MACD/ADX/Fib/etc. and the LLM reasons over that payload.
That is exactly what the training data contains, so a text model is the right
tool. Image-based chart reading is a serving-only vision add-on (appendix C);
it needs no training.

## The files

| file | role |
|---|---|
| `curate_training_data.py` | dedup + validate + manifest the dataset |
| `train_runeclaw_v7_8b.py` | the 8B QLoRA run (VRAM-honest, resumable) |
| `export_model.py` | LoRA→fp16 merge — **newest checkpoint by mtime**, gated |
| `convert_to_gguf.py` | GGUF + Q4_K_M — refuses stale merges, size-gated |
| `runeclaw_eval.py` | scores the model against the real risk thresholds |

Every stage refuses bad input instead of proceeding — the v6 postmortem
(an 8B shipped under the 3B's name, four times) is why.

---

## Phase 1 — Curate (ProArt, ~2 min)

```powershell
cd C:\runeclaw-training
python curate_training_data.py
```

Reads the v6 305K set, drops exact duplicates, conflicting duplicate prompts,
empty/oversized samples; writes `training_data\curated_v7.jsonl` plus a
manifest with SHA256 hashes and per-reason drop counts.

Read the printed numbers. If curation drops a large fraction, that is the
duplication the mega-merges carried — GPU-days you did not spend re-learning
the same rows. To cap training time, add `--max-samples 100000`
(deterministic even subsample, reproducible).

## Phase 2 — Train (ProArt, expect days — measure, don't guess)

```powershell
venv\Scripts\activate
python train_runeclaw_v7_8b.py
python keep_awake.py        # separate window, box must not sleep
```

What the script enforces:
- **VRAM is measured or the run refuses** — no `0.0 GB` guessing. 24 GB →
  batch 4 × accum 4 (effective 16, same schedule as v6, so loss curves are
  comparable).
- **The dataset hash is written into `runeclaw-8b-v7-checkpoints\TRAINING_MANIFEST.json`
  before step 1** — provenance exists even if the run dies.
- Per-epoch checkpoints, `--resume` to continue after a crash.

ETA arithmetic (estimates until the log prints real s/it): v6 measured
8.45 s/it for the 3B at effective batch 16; an 8B is roughly 2.5× the
compute, call it ~20 s/it on this card. 100K curated samples × 2 epochs
≈ 12.5K steps ≈ **~3 days**. Full 305K uncurated ≈ 9+ days — curation is
the difference. **Multiply the real s/it from the first 20 logged steps by
the printed total steps before walking away.**

Health checks during the run (from v6 experience):
- loss descending through ~0.4s in epoch 1: normal. Collapapse toward <0.1
  early: duplication got through — stop, re-curate.
- Task Manager → GPU: dedicated <24 GB with headroom, "Shared GPU memory"
  flat. Shared climbing = sysmem spill = 10–20× slowdown (keep the NVIDIA
  "Prefer No Sysmem Fallback" setting on so it OOMs honestly instead).
- No Ollama serving on this GPU until training ends.

## Phase 3 — Export + convert (ProArt, ~40 min, every step gated)

```powershell
python export_model.py --expect-base 8B
```

- Prints ALL checkpoint candidates, newest adapter first, and selects by
  **mtime** — folder names cannot lie it into the wrong adapter again.
- `--expect-base 8B` hard-fails if the newest adapter is not the 8B.
- Console must show `runeclaw-8b-v7-checkpoints\...` and
  `Meta-Llama-3.1-8B-Instruct`; expect `Merged parameters: 8.03B`,
  ~8 shards, **~16 GB** total, and an `EXPORT_MANIFEST.json`.

```powershell
python convert_to_gguf.py
```

- Refuses a merge with no manifest, older than the newest checkpoint, or
  inconsistent with the weights on disk.
- Expected output: **`unsloth.Q4_K_M.gguf` ≈ 4.9 GB** — for THIS model that
  size is correct (it was only wrong when a 3B was claimed). The script's
  size gate checks against the manifest's 8.03B automatically.

## Phase 4 — Verify locally, then publish (ProArt)

```powershell
cd runeclaw-model
ollama create pbdes2022/HUMANOID-TRADERS:v7-8b -f Modelfile
ollama show pbdes2022/HUMANOID-TRADERS:v7-8b     # MUST read: parameters 8.0B
ollama run  pbdes2022/HUMANOID-TRADERS:v7-8b "Analyze BTC/USDT. RSI 28, MACD histogram positive, price at 61.8% Fib, ADX 32 with +DI > -DI."
```

The reply must carry the `TRADE IDEA [TI-...]` block, a Risk Check verdict,
and `Status: PENDING — type CONFIRM`. Then score it before it goes anywhere:

```powershell
python runeclaw_eval.py --model pbdes2022/HUMANOID-TRADERS:v7-8b --output v7.json
```

Compare against the same eval on the v6 3B (and the prompt-only baseline).
**v7 has to win the eval, not just exist.** Only then:

```powershell
ollama push pbdes2022/HUMANOID-TRADERS:v7-8b
```

Repoint `:latest` only after Phase 6's shadow A/B also agrees.
`ollama show` between `create` and `push`, every time, forever.

## Phase 5 — Serve on the 8GB box

One-time setup on that machine:
1. NVIDIA Control Panel → Manage 3D Settings → *CUDA - Sysmem Fallback
   Policy* → **Prefer No Sysmem Fallback**.
2. System environment variables, then restart the Ollama tray app:
   ```
   OLLAMA_NUM_PARALLEL=1        (each slot allocates its OWN KV cache)
   OLLAMA_KEEP_ALIVE=-1         (SCAN fires often; never unload)
   OLLAMA_FLASH_ATTENTION=1
   OLLAMA_KV_CACHE_TYPE=q8_0    (halves KV cost — the 8B needs the room)
   ```

Then:

```powershell
ollama pull pbdes2022/HUMANOID-TRADERS:v7-8b
ollama run  pbdes2022/HUMANOID-TRADERS:v7-8b --verbose "sanity check"
ollama ps
```

**`ollama ps` must read `100% GPU`.** The 8B at 4.9 GB + 4K context fits an
8 GB card with a light desktop; a heavy browser session can push it into
partial offload, which *works* but runs ~10× slower — that column is the
deploy verification, not `create`'s exit code. If it won't reach 100%:
close GPU-using apps first; if still short, this is the fallback ladder:
lower `num_ctx` to 3072 → serve the v6 3B instead (2.0 GB, roomy) and keep
the 8B on the ProArt.

Budget sketch (estimates; `--verbose`'s eval rate is the measurement):
4.9 GB weights + ~0.7 GB CUDA/graph + 0.5 GB KV (4K ctx at q8_0) + desktop
≈ 6.5–7.5 GB. Expect roughly 15–25 tok/s.

## Phase 6 — Wire the bot + promotion gates

`.env` on the bot host (restart the bot after — these are read at import):

```bash
RUNECLAW_LLM_BASE_URL=http://localhost:11434/v1
RUNECLAW_LLM_MODEL=pbdes2022/HUMANOID-TRADERS:v7-8b   # byte-for-byte from `ollama list`
```

Rollout, cheap tiers first (docs/RUNECLAW_LLM.md §3–4 is the full gate):

```
/settier chat runeclaw
/settier scan runeclaw
/llmstatus                      # confirm + watch degradation streaks
```

Shadow A/B before THESIS ever routes to it:

```bash
LLM_SHADOW_ENABLED=true
LLM_SHADOW_PROVIDER=runeclaw
```

After a few days of scans, `/llmab` reports directional hit rate on the same
realized trades. Promote THESIS only on a win; LEARNING stays on a hosted
large-context model. The 23-check risk gate and every circuit breaker stay
on regardless — the LLM proposes, the risk engine disposes.

---

## Appendix A — Why 8B and not bigger

The 24 GB card can *train* up to ~27–32B QLoRA, but the 8 GB card can only
*serve* ~8B dense fully in VRAM. A model that cannot run at 100% GPU on the
deployment box fails the latency budget for the SCAN tier. Bigger ambitions
have two honest paths: serve a 14B+ on the ProArt itself (making it a second
inference host), or use the 8GB box's 64 GB DDR for MoE models with CPU
expert offload (Qwen3-30B-A3B class at ~10–20 tok/s) — good for CHAT,
unproven for SCAN latency.

## Appendix B — If the v7 eval does NOT beat v6

Do not ship it out of sunk-cost. The likely causes, in order: curation was
too aggressive (check the drop counts), 2 epochs overfit the smaller set
(retrain 1 epoch — with the manifest, the rerun is reproducible), or the 8B
needs the fuller dataset (retrain on 200K+). The eval and `/llmab` decide;
GPU-days spent are not evidence.

## Appendix C — Chart *images* (optional, later, serving-only)

To read candlestick screenshots (e.g. user-uploaded charts in Telegram):
run a vision model as a sidecar — `ollama run llama3.2-vision:11b` (~8 GB,
fits the ProArt easily; tight on the 8GB box) or a Qwen-VL class model —
and pipe its text description into the normal RUNECLAW pipeline. No
fine-tune needed to start; collect real usage first and only consider
vision fine-tuning if the sidecar's descriptions are the weak link.
