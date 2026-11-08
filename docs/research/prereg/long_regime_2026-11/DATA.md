# Long only behind BTC's trend: data, frozen 2026-11-08 after the seal and before any run

Built by `python3 scripts/freeze_long_regime.py` (candidates, the snapshot tool for the hourly
series, `btc` for the daily BTC series, then assemble).

| dataset | dataset hash |
|---|---|
| benchmark/fresh10b_1h | 2b87754aa931a3884bf8ba67538e0b0039e77b766c9dc69dcf64a75b5a1f09bb |
| benchmark/fresh10b_btc_1d | 7d64fdaf8e40af2330fd8dbc45ccadb4d13a3387aae73be8bdb4555e08236779 |

The universe (rank order): ONE, ICNT, LYN, CYBER, QNT, MET, ACT, ARIA, SIGN, FIL. Passed over for
want of 9,000 closed hourly bars: AKE, UAI, JCT, CAP, EVAA, CT, PONS, BTW, MEGA, XMR
(`universe.json`).

Two departures from the plan, neither touching a sealed number:
- The daily BTC series was not fetched with the snapshot tool: Bitget refuses a daily window wider
  than 90 days, which that tool asks for. `freeze_long_regime.py btc` pages the venue's daily
  candles 90 days a request and keeps closed bars only. Bitget's daily bars open at 16:00 UTC; the
  control reads each bar as closed 24 h after its open.
- The 40th candidate (a symbol named in Chinese characters) was left out of the fetch list. The
  first ten coins to serve in full were all ranked above it, so it could not have been chosen.
