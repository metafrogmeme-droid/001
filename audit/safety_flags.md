# Safety toggles: boolean flags defaulting to ON

A flag that defaults True is a protection; setting it false removes that
protection. One absent from `.env.example` is a control an operator can
disable without the file that documents configuration ever naming it.

- default-True boolean flags: **110**
- of those, absent from `.env.example`: **90**

| flag | defined at | in .env.example |
|---|---|---|
| `ADAPTIVE_CONFIDENCE_ENABLED` | `bot/config.py:1642` | **no** |
| `ADAPTIVE_THRESHOLD_ENABLED` | `bot/config.py:1700` | **no** |
| `API_DEGRADE_REDUCE_ONLY` | `bot/config.py:1845` | **no** |
| `BACKTEST_LADDER_TRAILING` | `bot/backtest/engine.py:170` | **no** |
| `CANDLE_MTF_ENABLED` | `bot/config.py:1608` | **no** |
| `CANDLE_STRENGTH_VOTE_ENABLED` | `bot/config.py:1579` | **no** |
| `CANDLE_TREND_CONTEXT_ENABLED` | `bot/config.py:1578` | **no** |
| `CATALOG_WATCH_ENABLED` | `bot/config.py:2399` | **no** |
| `CONFLUENCE_FAMILY_CAP_ENABLED` | `bot/config.py:1896` | **no** |
| `DAILY_LOSS_BREAKER_AUTORESET` | `bot/config.py:360` | **no** |
| `DATA_QUALITY_PENALTY_ENABLED` | `bot/config.py:1567` | **no** |
| `DROP_UNCLOSED_CANDLE_ENABLED` | `bot/config.py:1306` | **no** |
| `ELLIOTT_FIB_TARGETS_ENABLED` | `bot/config.py:1383` | **no** |
| `ELLIOTT_MTF_ALIGNMENT_ENABLED` | `bot/config.py:1392` | **no** |
| `ELLIOTT_MTF_ENABLED` | `bot/config.py:1384` | **no** |
| `ELLIOTT_WAVE_ACTION_ENABLED` | `bot/config.py:1382` | **no** |
| `ELLIOTT_ZIGZAG_ENABLED` | `bot/config.py:1380` | **no** |
| `ENGINE_ANALYSIS_AS_ADMIN` | `bot/config.py:1294` | **no** |
| `ENV_NAME` | `tests/test_flag_prose_matches_default.py:54` | **no** |
| `EXCHANGE_MIN_ROUNDUP_ENABLED` | `bot/config.py:851` | **no** |
| `FIB_DIRECTION_AWARE_ENABLED` | `bot/config.py:1556` | **no** |
| `GUARDIAN_DIGITAL_TWIN_ENABLED` | `bot/config.py:633` | **no** |
| `GUARDIAN_ESCAPE_ENABLED` | `bot/config.py:651` | **no** |
| `GUARDIAN_FIREWALL_ENABLED` | `bot/config.py:620` | **no** |
| `GUARDIAN_RISK_SENTINEL_ENABLED` | `bot/config.py:642` | **no** |
| `INTRADAY_TRAILING_ENABLED` | `bot/config.py:2048` | **no** |
| `KELLY_SIZING_ENABLED` | `bot/config.py:657` | **no** |
| `LEADING_DIAGONAL_PRETREND_FIX` | `bot/core/chart_patterns.py:1220` | **no** |
| `LEARNING_READINESS_ALERT_ENABLED` | `bot/config.py:1272` | **no** |
| `LEARN_FROM_PAPER_CLOSES` | `bot/config.py:1658` | **no** |
| `LEVEL_AWARE_SLTP_ENABLED` | `bot/config.py:1413` | **no** |
| `LIMIT_DRIFT_MARKET_FALLBACK` | `bot/config.py:1995` | **no** |
| `LIMIT_ORDERS_ENABLED` | `bot/config.py:1982` | **no** |
| `LIMIT_POST_ONLY` | `bot/config.py:1990` | **no** |
| `LIQUIDITY_SWEEP_OWN_CLOSE` | `bot/core/chart_patterns.py:1377` | **no** |
| `LIVE_OPEN_TO_KEY_HOLDERS` | `bot/config.py:2304` | **no** |
| `LLM_BACKGROUND_SCANS` | `bot/config.py:1547` | **no** |
| `LLM_CACHE_SCOPED_KEY` | `bot/config.py:1229` | **no** |
| `LLM_DEGRADED_ALERT_ENABLED` | `bot/config.py:1280` | **no** |
| `LLM_DIRECTION_GUARD_ENABLED` | `bot/config.py:1187` | **no** |
| `LLM_FALLBACK_COST_ACCOUNTING` | `bot/config.py:1157` | **no** |
| `MTF_ALIGNMENT_GATE_ENABLED` | `bot/config.py:505` | **no** |
| `MTF_CONFLUENCE_ENABLED` | `bot/config.py:1400` | **no** |
| `OCO_BRACKET_ENABLED` | `bot/config.py:1842` | **no** |
| `OF_CROSS_VENUE_FUNDING` | `bot/core/order_flow.py:128` | **no** |
| `OF_FUNDING_VOTE_FIXED_SCALE` | `bot/core/order_flow.py:182` | **no** |
| `OF_GUARD_TOP_DEPTH_ENABLED` | `bot/core/order_flow.py:123` | **no** |
| `OF_RECORD_SNAPSHOTS` | `bot/core/flag_status.py:118` | **no** |
| `OF_REST_CVD_SERIES_DIVERGENCE` | `bot/core/order_flow.py:190` | **no** |
| `OF_TIME_BARS_ENABLED` | `bot/core/order_flow.py:162` | **no** |
| `ORDER_SPLIT_ENABLED` | `bot/config.py:1837` | **no** |
| `PAPER_AUTO_ACCEPT` | `bot/config.py:2292` | **no** |
| `PARTIAL_TP_ENABLED` | `bot/config.py:1685` | **no** |
| `PATTERN_ATR_TOLERANCES_ENABLED` | `bot/core/chart_patterns.py:135` | **no** |
| `PATTERN_DEDUP_ENABLED` | `bot/config.py:1561` | **no** |
| `PER_STRATEGY_NOTIONAL_CAP_ENABLED` | `bot/config.py:326` | **no** |
| `POSITION_TRAILING_ENABLED` | `bot/config.py:2064` | **no** |
| `PROACTIVE_AUTO_ENROLL_ADMIN` | `bot/config.py:2341` | **no** |
| `REGIME_SIZING_ENABLED` | `bot/config.py:713` | **no** |
| `RUNECLAW_TEST_SWITCH` | `tests/test_bg_scan_valve.py:49` | **no** |
| `SCALP_SESSION_VWAP_ENABLED` | `bot/config.py:1509` | **no** |
| `SCAN_CLASS_COMMODITIES` | `bot/config.py:2379` | **no** |
| `SCAN_CLASS_ETFS` | `bot/config.py:2387` | **no** |
| `SCAN_CLASS_STOCKS` | `bot/config.py:2380` | **no** |
| `SCAN_TRADFI_FULL_COVERAGE` | `bot/config.py:2523` | **no** |
| `SCAN_USE_ANALYZER_ENGINE` | `bot/config.py:1526` | **no** |
| `SCAN_VENUE_NATIVE_MARKETS` | `bot/config.py:2393` | **no** |
| `SLIPPAGE_GUARD_ENABLED` | `bot/config.py:1740` | **no** |
| `SMART_SCAN_ENABLED` | `bot/config.py:1707` | **no** |
| `STOCK_TRADING_ENABLED` | `bot/config.py:2160` | **no** |
| `STRUCTURE_ZIGZAG_ENABLED` | `bot/config.py:1406` | **no** |
| `SWING_TRAILING_ENABLED` | `bot/config.py:2056` | **no** |
| `SYMBOL_LOSS_STREAK_ENABLED` | `bot/config.py:429` | **no** |
| `THING_ENABLED` | `tests/test_flag_prose_matches_default.py:117` | **no** |
| `TIME_STOP_ENABLED` | `bot/config.py:2010` | **no** |
| `TRAILING_STOP_ENABLED` | `bot/config.py:1925` | **no** |
| `UNPROTECTED_ESCALATION_ENABLED` | `bot/config.py:1870` | **no** |
| `UNPROTECTED_GUARD_ENABLED` | `bot/config.py:1854` | **no** |
| `VALIDATION_GATE_ALLOW_UNTESTED` | `bot/config.py:605` | **no** |
| `VERIFY_CLASSIC_SLTP_ON_RESTART` | `bot/config.py:1835` | **no** |
| `VOL_TARGET_SIZING_ENABLED` | `bot/config.py:319` | **no** |
| `VOTER_SKIP_MISSING_ENABLED` | `bot/config.py:1615` | **no** |
| `VWAP_ANCHORED_PIVOT_ENABLED` | `bot/config.py:1503` | **no** |
| `VWAP_BANDS_VOTE_ENABLED` | `bot/config.py:1500` | **no** |
| `VWAP_SESSION_ANCHORED` | `bot/config.py:1198` | **no** |
| `VWAP_SETUP_ANCHORING_ENABLED` | `bot/config.py:1502` | **no** |
| `VWAP_SLOPE_VOTE_ENABLED` | `bot/config.py:1501` | **no** |
| `WAVE_TRAIL_ENABLED` | `bot/config.py:1975` | **no** |
| `WRAPPED_ENABLED` | `tests/test_flag_prose_matches_default.py:144` | **no** |
| `WS_CVD_ENABLED` | `bot/config.py:1815` | **no** |
