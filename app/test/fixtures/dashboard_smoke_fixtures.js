'use strict';
/**
 * Shaped stub BODIES for the dashboard smoke. `fetchJSON` returns
 * `{ ok, status, data: <body> }`, and the panels read the body's own keys
 * (`r.data?.yield`, `r.data?.reports`, `meR?.data?.plan`), so these are raw
 * response bodies, not `{ ok, data }` envelopes. Minimal, but populated
 * where a panel has a populated branch worth rendering: the shipped defect
 * this smoke exists for lived in the yield panel's populated branch, which
 * an empty body never reaches. First matching prefix wins.
 */
const me = { id: 1, email: 'op@example.com', plan: 'admin', is_admin: true, tier: 'admin', telegram_linked: true, wallet: null };
const yieldRow = { coin: 'USDT', idle_amount: 40, idle_usd: 40, stakeable_usd: 28, apy_flexible: 4.2, apy_fixed: null, fixed_terms: [], est_year_usd: 1.18, source: 'futures free', product_id: '', alt_note: '' };
const tickers = [
  { symbol: 'BTC/USDT', last: 60000, percentage: 1.2, quoteVolume: 2.5e9, baseVolume: 41000 },
  { symbol: 'ETH/USDT', last: 2400, percentage: -0.6, quoteVolume: 1.1e9, baseVolume: 450000 },
];
module.exports = [
  ['/api/auth/me', me],
  // The engine's scan, as the sync route serves it to a signed-in reader:
  // every subject the home view's context row reads is READ here — a stamp
  // this site put on it at ingest, a BTC anchor beside the regime, a venue,
  // a calendar that says it loaded, and the entry gate's three-valued
  // answer — so the row renders five chips and no NOT REPORTED.
  ['/api/bot/sync/scan', { scan: {
    received_at: new Date(Date.now() - 60e3).toISOString(), timestamp: '2026-09-14 11:00 UTC',
    regime: { label: 'BULLISH', score: 0.4, gate: 60000, long_short: '', funding: '' },
    features: { venue: { id: 'bitget', name: 'Bitget' } },
    macro: { state: 'NORMAL', stale: false, unreadable: false, has_events: true, reading: 'Normal', next_event: null, active_event: null, seconds_until_next: 86400, evaluated_at: new Date().toISOString() },
    circuit_breaker: { rules: [], gate: { blocked: false, unknown: false, reasons: [] }, equity: null, net_pnl: null, win_rate: null, record_unreadable: false,
      total_trades: 0, open_count: 0, open_positions: [], closed_trades: [], live_mode: true, live_unavailable: false, strategy_mode: 'balanced',
      // The risk backstop as bot/formatters/risk_backstop.py publishes it:
      // every field read, so the Engine view's panel renders four rows.
      backstop: { drawdown_pct: 3.2, limit_pct: 7.0, source: 'live', verdict: 'Healthy', override_pct: null, default_limit_pct: 7.0, hardening: true,
        slots_used: 2, slots_cap: 5, slots_floor: false, slots_note: '', slots_person: 'unset', gate: { blocked: false, unknown: false, reasons: [] } } },
    symbols: {}, entry_cards: [], key_call: 'No scan data available.',
  } }],
  ['/api/reports/yield', { yield: { rows: [yieldRow], total_idle_usd: 40, total_est_year_usd: 1.18, incomplete: '' } }],
  ['/api/reports', { reports: {
    parity: { trades: 18, excluded_non_fills: 7, unscored_pnl: 0, win_rate: 0.61, net_pnl: 4.51, pf: 2.24, fees_read: 18, total_fees: 1.2, realized_fee_rate: 0.001, modeled_fee_rate: 0.002, fee_vs_model: 0.48, inferred_fills: 14 },
    funding: { rows: [] }, arb: { carries: [], notional_usd: 1000, snapshots: 0 }, has_yield: true, generated_at: new Date().toISOString(),
  } }],
  ['/api/staking/fixed', { available: false, rows: [] }],
  // One armed tripwire and a healthy engine, so the Tripwires panel renders
  // its populated branch: the engine line plus a green badge that is TRUE.
  ['/api/alerts', { alerts: [{ id: 1, symbol: 'BTCUSDT', metric: 'price', op: '<', threshold: 50000, label: 'BTC below $50,000',
    active: true, trigger_price: null, created_at: new Date(Date.now() - 3600e3).toISOString(), triggered_at: null }],
    max_active: 20,
    engine: { running: true, last_run_at: new Date(Date.now() - 20e3).toISOString(), last_ok_at: new Date(Date.now() - 20e3).toISOString(),
      consecutive_failures: 0, last_error: null } }],
  // The server always sends open_positions (routes/portfolio.js); a body
  // without it is a shape it never produces, and reads as an empty book.
  ['/api/portfolio', { mode: 'PAPER', equity: 10000, start_equity: 10000, total_pnl: 12.5, total_pnl_pct: 0.125,
    daily_pnl: 1.5, daily_pnl_pct: 0.015, win_rate: 0.55, trades: 20, live_unavailable: false,
    open_positions: [{ symbol: 'BTC/USDT', direction: 'LONG', entry_price: 60000, current_price: 60500, size_usd: 100, pnl: 0.83, pnl_pct: 0.83, stop_loss: 59000, take_profit: 63000, opened_at: '2026-09-02T00:00:00Z' }],
    closed_trades: [], recent: [],
    // Per-figure provenance and the bot's own stamp, exactly as the gateway
    // branch of routes/portfolio.js publishes them. The metric cluster
    // THROWS on a payload that names no source (a labelled dash would claim
    // we looked), so a fixture without these lands in the generic error
    // state and the smoke fails — which is the fixture drifting from the
    // route, and the right answer.
    updated_at: new Date().toISOString(),
    provenance: { equity: 'gateway', open_positions: 'gateway', daily_pnl: 'gateway' },
    as_of: { equity: new Date().toISOString(), open_positions: new Date().toISOString(), daily_pnl: new Date().toISOString() } }],
  // The server always sends these (routes/arena.js builds positions and
  // limits before it answers); an empty body is a shape it never produces.
  ['/api/arena/account', { start_balance: 10000, balance: 10000, equity: 10000, return_pct: 0,
    limits: { min_margin: 5, max_leverage: 20, max_open: 5 }, positions: [], trades: [], follow: false }],
  ['/api/market/tickers', { data: tickers, updated_at: new Date().toISOString() }],
  // The Guardian flight ledger and incident stream, shaped as routes/guardian.js
  // relays them: one approved-and-executed record with a priced close, one
  // rejection, one record whose gate the recorder could not read (the sealed
  // literal UNKNOWN — muted on the decision log, never red), and three
  // incidents of the three kinds. Populated so the decision log, guardianBlock
  // and incidentsCard render their populated branches in a real browser.
  ['/api/guardian/flight', { records: [
    { decision_id: 'd-1', symbol: 'BTC/USDT:USDT', timestamp: new Date(Date.now() - 3600e3).toISOString(), outcome: 'EXECUTED_LIVE', is_paper: false,
      idea: { direction: 'LONG', confidence: 0.71, entry: 59000, sl: 57000, tp: 64000, rr: 2.5, reasoning: 'Higher-timeframe trend up, pullback to the 4h demand zone with a bullish engulfing close.', signals_used: ['trend', 'engulfing'], provenance: { model_provider: 'grok', prompt_hash: 'abcdef0123456789', analysis_version: 'v9', data_bars: 240, data_thin: false }, votes: [] },
      risk: { verdict: 'APPROVED', passed: 11, failed: 0, size_usd: 100, position_pct: 0.02, drawdown_pct: 0.01, reason: '', checks_failed: [] },
      result: { pnl_usd: 12.5, exit_price: 60100, entry_price: 59000, close_reason: 'tp' },
      chain: { sequence: 41, entry_hash: 'a'.repeat(64), prev_hash: 'b'.repeat(64) } },
    { decision_id: 'd-2', symbol: 'ETH/USDT:USDT', timestamp: new Date(Date.now() - 7200e3).toISOString(), outcome: 'REJECTED', is_paper: false,
      idea: { direction: 'SHORT', confidence: 0.55, entry: 2400, sl: 2500, tp: 2200, rr: 2.0, reasoning: 'Lower high into resistance.', signals_used: ['resistance'], provenance: {}, votes: [] },
      risk: { verdict: 'REJECTED', passed: 9, failed: 2, size_usd: 100, reason: 'daily loss cap reached', checks_failed: ['DAILY_LOSS', 'MAX_OPEN'] },
      result: null, chain: { sequence: 40, entry_hash: 'c'.repeat(64), prev_hash: 'a'.repeat(64) } },
    { decision_id: 'd-3', symbol: 'SOL/USDT:USDT', timestamp: '', outcome: 'EXECUTION_FAILED', is_paper: false,
      idea: { direction: 'LONG', confidence: 0.6 }, risk: { verdict: 'UNKNOWN' }, result: null,
      chain: { sequence: 39, entry_hash: 'd'.repeat(64), prev_hash: 'c'.repeat(64) } },
  ], chain: { ok: true, length: 41, tip_hash: 'a'.repeat(64), problems: [] }, policy: null, guardian_status: null,
    window: { ok: true, checked: 3, problems: [] }, updated_at: new Date(Date.now() - 3600e3).toISOString() }],
  ['/api/guardian/incidents', { read_only: true, incidents: [
    { id: 'i-1', ts: new Date(Date.now() - 1800e3).toISOString(), kind: 'block', category: 'Firewall', severity: 'high', symbol: '', detail: 'prompt injection pattern in a message', chain: { sequence: 42, entry_hash: 'e'.repeat(64) } },
    { id: 'i-2', ts: new Date(Date.now() - 5400e3).toISOString(), kind: 'recovery', category: 'Escape plan', severity: 'medium', symbol: 'BTC/USDT:USDT', detail: '2 unwind step(s)', chain: { sequence: 38, entry_hash: 'f'.repeat(64) } },
    { id: 'i-3', ts: new Date(Date.now() - 9000e3).toISOString(), kind: 'flag', category: 'Sentinel', severity: 'low', symbol: 'ETH/USDT:USDT', detail: 'funding spike', chain: { sequence: 37, entry_hash: '1'.repeat(64) } },
  ], counts: { block: 1, recovery: 1, flag: 1 }, derived: false, guardian_status: null, updated_at: new Date(Date.now() - 1800e3).toISOString() }],
  // The positions payload as routes/positions.js relays it from the bot:
  // `book_read` says the book was READ (the instrument row's empty state is
  // reachable only then), and one live row with a stop on the exchange so
  // the row's populated branch — mark, move, R, sparkline — renders in a
  // real browser rather than only its empty state.
  ['/api/positions', { live: true, read_only: true, book_read: true, count: 1, protected_count: 1, unprotected_count: 0, unknown_count: 0,
    updated_at: new Date().toISOString(),
    positions: [{ symbol: 'BTC/USDT:USDT', pair: 'BTC', direction: 'LONG', entry_price: 59000, stop_loss: 57000, take_profit: 64000,
      sl_dist_pct: 3.39, tp_dist_pct: 8.47, size_usd: 100, leverage: 5, quantity: 0.01, sl_order: 'exchange', tp_order: 'exchange',
      sl_protected: true, tp_protected: true, unprotected: false, sl_unknown: false, strategy_type: 'swing', opened_at: '2026-09-02T00:00:00Z' }] }],
  // Bitget candle rows ([ts, open, high, low, close, ...]) for the sparkline:
  // 24 hourly closes that move, so a line is drawn rather than words.
  ['/api/market/candles', { code: '00000', data: Array.from({ length: 24 }, (_, i) => {
    const c = 59000 + Math.round(400 * Math.sin(i / 3));
    return [String(1757800000000 + i * 3600000), String(c - 50), String(c + 120), String(c - 130), String(c), '10', '600000'];
  }) }],
  ['/api/reputation', { score: null, grade: null, unrated: true,
    subscores: { performance: null, risk_discipline: null, cost_efficiency: null, consistency: null },
    metrics: { trades: 0, win_rate: null, profit_factor: null, expectancy_r: null, max_drawdown_pct: null, fee_drag_pct: null },
    sample: { trades: 0, confidence: 0 }, flags: [{ key: 'no_trades', severity: 'info', label: 'No closed trades yet — reputation is unrated.' }], note: '' }],
];
