# Solana Ecosystem Mode

RUNECLAW has a **Solana ecosystem mode** that puts a configured list of Solana tokens at the top
of the scanner's results. The list has 15 symbols (`SOLANA_ECOSYSTEM_SYMBOLS` in
`bot/config.py`); 12 of them were Bitget spot pairs on 2026-10-15. The full pre-trade risk gate
applies unchanged in this mode.

**Operator command:** `/mode solana` switches the scan universe. The universe is shared by
everyone using the bot, so only the operator can switch it; other users are refused.

---

## Activation

**Option 1 -- Environment variable:**
```bash
ASSET_UNIVERSE=solana # in .env (the code default is all_markets)
```

**Option 2 -- Switching at runtime via Telegram (operator only):**
```
/mode solana       → Solana ecosystem tokens first
/mode all          → all Bitget USDT spot pairs
/mode all_markets  → spot crypto and futures (the default universe)
```

No restart is needed: the next scan uses the new universe. A trader can send `/mode` with no
argument to see the current universe.

---

## Token universe (15 configured symbols)

Bitget's public spot symbol list, read on 2026-10-15, lists 12 of these as online. MOBILE, JITO
and TENSOR are not listed there (Tensor's token trades as TNSR), so the scanner finds no ticker
for them and they never appear.

| Token | Category | Bitget spot pair | Notes |
|-------|----------|------------------|-------|
| SOL | L1 | SOL/USDT | Solana's native token; correlation group ALT_L1 |
| JUP | DeFi | JUP/USDT | Jupiter DEX aggregator |
| JTO | Staking | JTO/USDT | Jito's token |
| BONK | Meme | BONK/USDT | MEME group: wider volatility threshold (10% ATR) |
| WIF | Meme | WIF/USDT | MEME group: wider volatility threshold (10% ATR) |
| PYTH | Oracle | PYTH/USDT | Pyth Network oracle |
| RAY | DeFi | RAY/USDT | Raydium AMM |
| ORCA | DeFi | ORCA/USDT | Orca DEX |
| RENDER | Compute | RENDER/USDT | GPU rendering network; correlation group AI |
| HNT | IoT | HNT/USDT | Helium network |
| MOBILE | IoT | not listed | Helium Mobile |
| W | Bridge | W/USDT | Wormhole cross-chain |
| JITO | Staking | not listed | Configured; Jito's token is JTO |
| TENSOR | NFT | not listed | Tensor NFT marketplace (trades as TNSR) |
| DRIFT | DeFi | DRIFT/USDT | Drift perpetuals DEX |

---

## Solana-specific risk behaviour

### Meme-coin volatility threshold

BONK and WIF are in the `MEME` correlation group, which the volatility guard allows a **wider**
ATR threshold: the larger of `VOLATILITY_GUARD_ATR_PCT` (default 7%) and 10% of price, so that
their ordinary volatility is not read as an extreme. They still share the MEME group's position
cap with DOGE, PEPE and the rest.

### Correlation groups

The risk gate limits positions in one correlation group (`MAX_CORRELATION_PER_GROUP`, default 2),
so it cannot stack bets on tokens that tend to move together.

| Group | Tokens from this list (+ others) | Max concurrent |
|-------|----------------------------------|----------------|
| ALT_L1 | SOL (+ AVAX, NEAR, etc.) | 2 |
| MEME | BONK, WIF (+ DOGE, PEPE, etc.) | 2 |
| AI | RENDER (+ FET, TAO, etc.) | 2 |
| SOLANA_ECO | JUP, JTO, PYTH, RAY, ORCA, JITO, TENSOR, DRIFT, HNT, MOBILE, W | 2 |

The group map is keyed by spot symbols. A perpetual's symbol (`JUP/USDT:USDT`) counts in its group
only when `CORRELATION_PERP_GROUP_MAPPING_ENABLED` is on (default off); otherwise it counts in the
shared bucket for unmapped symbols, capped at 3 (`MAX_UNMAPPED_CORRELATED`).

### Same risk gate

The full pre-trade risk gate applies unchanged in Solana mode: the circuit breaker, position and
exposure limits, the confirm path and the audit trail are the same as in every other mode. See
[Pre-Trade Risk Checks](risk-checks-18.md).

---

## How scanning works in Solana mode

```text
1. Fetch every Bitget USDT spot ticker
2. Drop inactive or delisted pairs and pairs below MIN_CRYPTO_VOLUME_USD of 24 h volume (default 1,500,000)
3. Score each remaining pair on momentum (24 h change and volume spike)
4. Sort by absolute momentum (descending)
5. SOLANA FILTER: move the configured Solana symbols to the top
6. Fill the remaining slots with the other top movers, up to TOP_MOVERS_COUNT (default 200)
```

You still see the strongest non-Solana movers, but the Solana symbols that pass the volume floor
come first. A Solana symbol below the volume floor is dropped like any other pair.

---

## Current limitations

- **Centralized execution** -- Solana mode trades on Bitget, not on chain.
- **No on-chain data in this mode** -- no wallet tracking, DEX volume or Solana RPC reads feed the
  Solana scan.
- **Static token list** -- the 15 symbols are fixed in `bot/config.py`; there is no dynamic
  discovery, and three of them were not Bitget spot pairs on 2026-10-15.
- **Separate from the swap page** -- the website's swap page can build an unsigned Jupiter swap
  for your own wallet to review and sign; RUNECLAW never signs it, and Solana mode does not use it.
