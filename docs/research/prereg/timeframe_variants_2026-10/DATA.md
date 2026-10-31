# Timeframe variants: data, frozen 2026-10-31 after the seal and before any run

Built by `python3 scripts/freeze_timeframe_variants.py` (candidates, then the snapshot tool, then assemble).

| dataset | dataset hash |
|---|---|
| benchmark/fresh10_1h | 449f02e383ec48d2c12fdef76766d71d10e7dd3a52a418797f08f70ec4233ee8 |
| benchmark/fresh10_15m | 7e318f69aa0c34035aeef56c8961fcaf8c7f3e79ab9d852ad885ddae48cb1b82 |
| benchmark/fresh10_5m | 358207feeaa7e8cf3305840d0188ee867c6300e2d9102e9ab0eb33e5a1c8ac47 |

The universe (rank order): IDOL, BEAM, Q, SOPH, LSK, PUMP, PARTI, OP, NIL, BGB. Passed over for want of
9,000 closed hourly bars: EVAA, BIRB, AKE, BTW, MAGMA (`universe.json`).

The snapshot tool drops the forming bar, so each series was fetched with a limit one above the
sealed count and kept only when it served the sealed count of closed bars.
