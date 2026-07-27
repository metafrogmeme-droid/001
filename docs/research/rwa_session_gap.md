# RWA session-gap reversion — tested, and the data said no

**RUNECLAW Research Note 001 · July 2026**

We test a live hypothesis about tokenized-equity perpetuals and publish the
result regardless of direction. This note reports a **negative result**: the
thesis, as stated, is not supported by the data. We publish it because a
marketplace that only ever shows winners is advertising, not research —
and because a refuted thesis narrows the search space for everyone.

---

## The thesis

Tokenized equity perps trade 24/7. Their underlying stocks trade ~6.5 hours a
day, weekdays only. For ~17.5 hours a day and all weekend there is no
arbitrage anchor — nobody can hedge the perp against the real stock, because
the real stock isn't trading. During those hours the price is set purely by
crypto-native flow. When the cash market reopens, arbitrageurs can hedge
again and the perp gets pulled back to the real stock's price.

**Prediction:** returns accumulated while the cash market is closed should
systematically revert during the following open session.

The instrument class this requires — a 24/7 derivative on a non-24/7
underlying — only became widely tradeable in 2025, so the hypothesis is
young and worth testing honestly.

## The venue and the data

Verified live against Bitget's public API at the time of the study:

- Bitget lists USDT-margined perpetuals on tokenized equities:
  TSLAUSDT, NVDAUSDT, AAPLUSDT, MSFTUSDT, AMZNUSDT, GOOGLUSDT, METAUSDT,
  QQQUSDT, SP500USDT and others. Contract metadata `openTime` shows the
  basket studied here listed on **2026-02-02**.
- Prices are real equity levels (not homonymous meme tokens — several
  symbols like `SPXUSDT` and `OPENUSDT` collide with crypto tokens and were
  excluded by price-level and listing-date checks).
- Data: **30-minute candles for every session since listing** (~8,500 per
  symbol), fetched from the public candles API. Nothing simulated, nothing
  back-filled.

Basket: `TSLA, NVDA, AAPL, MSFT, AMZN, GOOGL, META, QQQ` (8 symbols,
119 usable NYSE sessions each after the first, **952 pooled observations**).

## Method

For each NYSE trading day D (DST-correct `America/New_York` sessions,
2026 exchange holidays excluded):

```
closed_ret = open(D, 09:30 ET) / close(D-1, 16:00 ET) - 1   # unanchored hours
open_ret   = close(D, 16:00 ET) / open(D, 09:30 ET) - 1     # anchored session
```

Prices are the traded price **at** the boundary (the open of the 09:30 and
16:00 candles), never a price from inside the closed window. Two tests:

1. Correlation / OLS slope of `open_ret` on `closed_ret` — the thesis
   predicts **negative**.
2. A fade-the-gap rule: hold `-sign(closed_ret)` through the session when
   `|closed_ret|` clears a filter, net of Bitget taker fees (0.06% × 2).

## Results — price

| symbol | sessions | corr | gross fade mean/day | net fade @\|gap\|≥0.5% |
|---|---|---|---|---|
| TSLA | 119 | +0.134 | −0.267% (t −1.18) | −0.445% (t −1.63) |
| NVDA | 119 | +0.020 | +0.102% (t +0.54) | −0.116% (t −0.50) |
| AAPL | 119 | +0.017 | −0.003% (t −0.02) | −0.227% (t −1.15) |
| MSFT | 119 | +0.015 | −0.153% (t −1.07) | −0.295% (t −1.65) |
| AMZN | 119 | −0.135 | −0.012% (t −0.08) | −0.172% (t −0.88) |
| GOOGL | 119 | −0.076 | +0.091% (t +0.59) | +0.051% (t +0.22) |
| META | 119 | +0.126 | −0.251% (t −1.47) | −0.321% (t −1.42) |
| QQQ | 119 | +0.013 | −0.068% (t −0.74) | −0.221% (t −1.68) |

Pooled (n = 952):

- **Correlation of closed-hours return with open-session return: +0.026** —
  indistinguishable from zero, and the *wrong sign* for reversion.
- Weekend gaps (Mondays, n = 192) — the strongest form of the thesis —
  show nothing: gross fade +0.018%, t = 0.14.
- Fading the gap **loses money at every filter tested**, significantly:
  net −0.167% (t −2.41) at ≥0.3%, **−0.227% (t −2.91) at ≥0.5%**,
  −0.255% (t −2.24) at ≥1.0%.

The significantly *negative* fade means the gap mildly **continues** rather
than reverts — but the continuation (~+0.11% gross at the 0.5% filter) is
smaller than a taker round trip, so it is not tradeable either at these
costs.

## Results — funding

If arbitrageurs can't hedge overnight, perhaps the anchor expresses in the
funding rate instead. All 8h funding prints since listing (00:00 / 08:00 /
16:00 UTC; the 16:00 print lands inside the NYSE session):

- **2,160 prints, 67% exactly zero.** Nonzero prints are clamped at ±0.10%.
- **No session structure**: mean funding at the in-session print is
  statistically the same as at the two closed-hours prints, on every symbol.
- There **is** a persistent small positive carry: mean +0.0125% per 8h
  (≈ +13.7%/yr, longs pay shorts) — real, but capped by the clamp and small
  against tokenized-equity volatility. We state its existence; we make no
  claim it survives execution.

## Caveats — what this study cannot say

- One venue, ~6 months, ~120 sessions. These markets are young; the regime
  can change as depth grows. A quarterly re-run is cheap (scripts below).
- The gap definition is close-to-open only; intraday windows (e.g. the
  first 30–60 minutes after the open) were not tested separately.
- Fee assumptions are taker-only (0.12% round trip); maker execution would
  roughly halve costs and change the marginal arithmetic, not the sign of
  the correlation.
- Funding P&L was studied separately from price P&L, not jointly.

## Conclusions

1. **The session-gap reversion thesis is not supported** on this venue and
   window. RUNECLAW will not ship a strategy built on it.
2. The failure mode is informative: unanchored-hours returns mildly
   *continue*, which is consistent with trend-following also underperforming
   on these names intraday — both observations point to gap moves that
   drift, not snap back.
3. The missing anchor does not express in funding on this venue either —
   funding is mostly pinned at zero and clamped. The most interesting
   residual is the persistent long-pays carry.
4. These instruments remain interesting for the platform: 24/7 equity
   exposure with session-aware risk is a real product surface. Any strategy
   we ship on them will follow this same protocol: state the thesis, fetch
   the data, publish the result — **in either direction**.

## Reproduce it

Both studies are in-repo and run against public endpoints (no keys):

```
python3 scripts/research/rwa_session_gap.py    # price study
python3 scripts/research/rwa_funding.py        # funding study
```

Education and research, not investment advice. No performance is promised;
a negative result on one venue and window is not proof the effect can never
exist elsewhere.
