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
  ['/api/market/tickers', { data: tickers, updated_at: new Date().toISOString() }],
  ['/api/reputation', { score: null, grade: null, unrated: true,
    subscores: { performance: null, risk_discipline: null, cost_efficiency: null, consistency: null },
    metrics: { trades: 0, win_rate: null, profit_factor: null, expectancy_r: null, max_drawdown_pct: null, fee_drag_pct: null },
    sample: { trades: 0, confidence: 0 }, flags: [{ key: 'no_trades', severity: 'info', label: 'No closed trades yet — reputation is unrated.' }], note: '' }],
];
