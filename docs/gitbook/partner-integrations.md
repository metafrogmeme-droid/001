# Partner Integrations

This page lists the model providers and Solana services RUNECLAW uses, and how each is configured today.

---

## Model Providers

The rule engine decides each trade's direction and confidence. `LLM_DECIDES` is off by default (`bot/config.py::AnalyzerConfig`), so a configured model writes a read that rides along on each idea, marked as not used to decide, and answers chat. With no model configured, the rule engine works the same and the model's read is simply absent.

### Choosing a provider

`LLM_PROVIDER` picks the primary provider. The code default is `openai`; the shipped `.env.example` sets `anthropic`. `LLM_API_KEY` is its key, `LLM_MODEL` its model, and `LLM_BASE_URL` its endpoint; an empty model or endpoint takes the provider's entry in the catalogue (`bot/llm/provider.py::PROVIDER_CATALOG`). An admin can switch provider at runtime with `/setllm`, and `/llmstatus` shows the provider, model and per-tier key state in use.

| `LLM_PROVIDER` | Provider | Default model in the catalogue | Notes |
|---|---|---|---|
| `openai` | OpenAI | `gpt-4o` | |
| `anthropic` | Anthropic Claude | `claude-sonnet-5` | Native SDK; `LLM_BASE_URL` is not used |
| `gemini` | Google Gemini | `gemini-3.5-flash` | OpenAI-compatible endpoint |
| `groq` | Groq | `openai/gpt-oss-20b` | |
| `mistral` | Mistral | `mistral-large-latest` | |
| `deepseek` | DeepSeek | `deepseek-chat` | |
| `together` | Together AI | `meta-llama/Llama-3-70b-chat-hf` | |
| `openrouter` | OpenRouter | `anthropic/claude-sonnet-5` | |
| `alibaba` | Alibaba Cloud (Qwen) | `qwen3.6-plus` | Set `LLM_BASE_URL`; see below |
| `grok` | xAI Grok | `grok-4.5` | |
| `ollama` | Local Ollama | `llama3` | No key needed |
| `runeclaw` | Self-hosted RUNECLAW model | `runeclaw-v6` when `RUNECLAW_LLM_MODEL` is unset | Endpoint from `RUNECLAW_LLM_BASE_URL` |

The operator setting is `RUNECLAW_LLM_MODEL`. Copy the tag exactly as `ollama list` prints it, including case. The tag recorded for this tree is `pbdes2022/humanoid-traders:v10-8b`. `runeclaw-v6` is only the catalogue default above. `pbdes2022/HUMANOID-TRADERS` and `:v7-8b` are different strings; the server answers 404 for a tag it does not have.
| `custom` | Any OpenAI-compatible endpoint | none | Set `LLM_BASE_URL`, `LLM_MODEL` and a non-empty `LLM_API_KEY` |

Every provider except `anthropic` is called through the OpenAI SDK (`bot/llm/provider.py::create_llm_client`). A provider other than `ollama` and `runeclaw` counts as configured only when it has a key.

### Task tiers

Calls are routed per task tier: `scan`, `thesis`, `learning` and `chat` (`bot/llm/provider.py::resolve_tier_config`). For each tier, the first of these that applies wins:

1. An admin's runtime override (`/settier`).
2. `LLM_TIER_<TIER>_PROVIDER`, with `LLM_TIER_<TIER>_MODEL` and `LLM_TIER_<TIER>_KEY`. With no tier key, the provider's own key variable is used (`GEMINI_API_KEY`, `GROQ_API_KEY`, `ALIBABA_API_KEY`, `XAI_API_KEY`, …), or `LLM_API_KEY` when the tier names the primary provider. This step never takes `anthropic`.
3. For an admin's calls, the admin table: Claude on every tier, when an Anthropic key is set.
4. The default table, when that provider's key is set: Groq for `scan`, Gemini for `thesis` and `learning`, Grok for `chat`.
5. The primary `LLM_PROVIDER`.

A tier routed by steps 1 to 4 uses the catalogue's endpoint for its provider, not `LLM_BASE_URL`. A caller who is not an admin is never routed to Anthropic. Two per-user options change the routing for a user's own `/analyze`, and both are off by default: the user's own provider key (`PER_USER_LLM_ENABLED`) and routing by the user's tier (`PER_USER_LLM_TIERS_ENABLED`). The shipped `.env.example` pins `scan`, `learning` and `chat` to Gemini; its `thesis` pin names Anthropic, which step 2 never takes, so an admin's thesis reaches Claude through step 3, and anyone else's through step 4 when that provider's key is set, and otherwise gets no model.

### Qwen through Alibaba Cloud (DashScope)

```bash
# .env
LLM_PROVIDER=alibaba
LLM_API_KEY=your-dashscope-api-key
LLM_BASE_URL=your-dashscope-openai-compatible-endpoint
LLM_MODEL=qwen3.6-plus   # or qwen3.6-flash; empty means qwen3.6-plus
```

Set `LLM_BASE_URL` to the OpenAI-compatible endpoint in your DashScope console. An empty value falls back to the catalogue's endpoint for `alibaba`, which is not DashScope. Only the primary provider reads `LLM_BASE_URL`, so a tier pinned with `LLM_TIER_<TIER>_PROVIDER=alibaba` also uses the catalogue's endpoint. To keep every tier on DashScope, leave the `LLM_TIER_*_PROVIDER` pins empty and leave the default table's keys (`GROQ_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`) unset, so each tier falls back to the primary provider.

### Qwen through other hosts

```bash
# OpenRouter or Together AI: the catalogue supplies the endpoint
LLM_PROVIDER=openrouter        # or together
LLM_API_KEY=your-key
LLM_MODEL=the-host's-current-qwen-model-id

# Any other OpenAI-compatible host
LLM_PROVIDER=custom
LLM_API_KEY=your-key
LLM_BASE_URL=the-host's-openai-compatible-endpoint
LLM_MODEL=the-host's-current-qwen-model-id
```

### Local models

```bash
# Ollama: no key; the catalogue points at Ollama's default local endpoint
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5              # any model you have pulled

# vLLM or another local OpenAI-compatible server
LLM_PROVIDER=custom
LLM_BASE_URL=http://localhost:<port>/v1
LLM_MODEL=the-served-model-id
LLM_API_KEY=any-non-empty-value
```

For a local server, pick a port other than 8000 (`api_bridge.py`, `API_BRIDGE_PORT`) and 8080 (the bot's gateway).

### Cost

Prices are the provider's. RUNECLAW books an estimated cost per call (`bot/core/cost.py`) against `LLM_DAILY_BUDGET_USD` (code default 1.0; the shipped `.env.example` sets 5.0). Past it, the model is not asked again until the UTC day rolls over.

---

## Solana Ecosystem

### Solana-focused scanning

`ASSET_UNIVERSE=solana` (default `all_markets`; an operator can switch at runtime with `/mode solana`) scans Bitget's USDT spot pairs and moves the pairs listed in `SOLANA_ECOSYSTEM_SYMBOLS` (`bot/config.py`) to the top of the results. The remaining slots go to the highest-momentum other pairs. A symbol the venue does not list, or that trades under the volume floor (`MIN_CRYPTO_VOLUME_USD`), does not appear in a scan. Every idea from these pairs passes the same pre-trade risk gate as any other.

The list today: `SOL/USDT`, `JUP/USDT`, `JTO/USDT`, `BONK/USDT`, `WIF/USDT`, `PYTH/USDT`, `RAY/USDT`, `ORCA/USDT`, `RENDER/USDT`, `HNT/USDT`, `MOBILE/USDT`, `W/USDT`, `JITO/USDT`, `TENSOR/USDT`, `DRIFT/USDT`.

```bash
# .env
ASSET_UNIVERSE=solana
```

### Solana services

| Service | What RUNECLAW does with it | Status |
|---|---|---|
| Jupiter | Builds an unsigned swap transaction that the user signs in their own wallet (`bot/core/meme_swap.py`). Nothing in RUNECLAW signs or holds a key. Builds default to simulation; a mainnet transaction needs `MEME_EXECUTION_NETWORK=mainnet`. | Built |
| DexScreener | DEX pair data for the meme radar, Solana among other chains (`app/lib/meme.js`, MCP tool `get_meme_radar`) | Built |
| Solana JSON-RPC | Read-only balances for a Solana watch address a member links (`app/lib/solana.js`, endpoint `WEB3_RPC_URL_SOLANA`), and the $RCLAW token-tier gate (`bot/token/tier_gate.py`, off by default) | Built |
| Birdeye, Helius DAS, Alchemy | None | Not built |

None of these places an order on its own: a Jupiter swap is signed by the user in their own wallet.

---

## How They Fit Together

```text
 Model providers (per task tier)            Bitget (market data, orders)
               │                                        │
               v                                        v
 RUNECLAW engine: rule-engine decision, model read rides along, pre-trade risk gate
               │
      ┌────────┼─────────────────┐
      v        v                 v
  Telegram   Website        MCP (POST /mcp)

 Solana services, beside the engine:
   Jupiter (unsigned swaps) · DexScreener (meme radar) · Solana RPC (read-only balances, $RCLAW tier gate)
```

Every trade idea, whatever the model provider, passes the same pre-trade risk gate. Trades need a human confirm unless the operator enables auto-confirm (`AUTO_CONFIRM_LIVE_ENABLED` with `AUTO_CONFIRM_THRESHOLD`), which is off in the shipped `.env.example` and on in the code default when no `.env` sets it. Even then, a live order nobody confirmed is placed only when a committed eligibility record exists for the running strategy, and none ships.
