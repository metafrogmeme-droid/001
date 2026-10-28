# Addendum 1 to PREREGISTRATION.md (sha256 in PREREGISTRATION.sha256)

Written 2026-10-28, still before any price bar of this study was fetched or read. These clarify
implementation points the main file left open; they change no parameter and no pass bar.

1. Lookback availability. A coin is eligible on day t only if every lookback of the variant is
   available (it has a valid close at t-L for the longest L) in addition to the 150-bar and 60-return
   rules. So eligibility needs max(150, maxL+1) bars of own history: 201 for set A, 201 for set B.
2. N is the count of coins eligible on that day under item 1.
3. Return label. A return is labelled with the date of the later open (open_d / open_{d-1} - 1 is
   labelled d). The cost of a fill at open d is charged to label d. Dev = labels <= 2022-12-31,
   holdout = labels >= 2023-01-01. The dev run sees data up to the 2022-12-31 bar only.
4. Scoring starts at the first fill of the first rebalance (a Sunday close with at least one eligible
   coin), or 2018-01-01 if later; days before that are not scored. Days after it that are flat
   count as zero return.
5. Drift ignores costs and funding (they are linear and recomputed from recorded turnover and long
   notional), so the 0.20%/side and zero-funding sensitivities are exact re-costings.
6. BTC buy-and-hold uses BTCUSDT open-to-open over the same labels, with no cost.
7. Calendar-year rule counts years with >= 90 scored days, over the whole scored period.
