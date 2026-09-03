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
    closed_trades: [], recent: [] }],
  // The server always sends these (routes/arena.js builds positions and
  // limits before it answers); an empty body is a shape it never produces.
  ['/api/arena/account', { start_balance: 10000, balance: 10000, equity: 10000, return_pct: 0,
    limits: { min_margin: 5, max_leverage: 20, max_open: 5 }, positions: [], trades: [], follow: false }],
  ['/api/market/tickers', { data: tickers, updated_at: new Date().toISOString() }],
  ['/api/reputation', { score: null, grade: null, unrated: true,
    subscores: { performance: null, risk_discipline: null, cost_efficiency: null, consistency: null },
    metrics: { trades: 0, win_rate: null, profit_factor: null, expectancy_r: null, max_drawdown_pct: null, fee_drag_pct: null },
    sample: { trades: 0, confidence: 0 }, flags: [{ key: 'no_trades', severity: 'info', label: 'No closed trades yet — reputation is unrated.' }], note: '' }],
];
