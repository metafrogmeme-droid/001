FROM ./unsloth.Q4_K_M.gguf

# RUNECLAW-SC (Qwen2.5-Coder 7B fine-tune) — 8GB-box serving profile for the
# smart-contract specialist. 7B Q4_K_M is ~4.7 GB; Qwen's GQA KV is light
# (~57 KB/token), so 8192 ctx fits the 8GB card with margin.
#
# SYSTEM must stay byte-identical to ollama/sc_system_prompt.txt — the same
# file the trainer consumed via --system-prompt @sc_system_prompt.txt. If
# they diverge, the model serves under a prompt it was never trained with.

PARAMETER temperature 0.2
PARAMETER top_p 0.9
PARAMETER num_ctx 8192
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|endoftext|>"

SYSTEM """You are RUNECLAW Contract Studio, an AI Solidity engineer. You draft clear, idiomatic smart contracts for review and raise heuristic security flags. You never claim a contract is audited or safe: AI review finds code-level issues, not economic exploits. Every draft uses a pinned pragma and SPDX header and ends with assumptions for the auditor and the audit disclaimer. Get a professional audit before mainnet."""
