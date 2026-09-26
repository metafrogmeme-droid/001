'use strict';
/**
 * The anonymous scan names the operator's positions and not their size.
 *
 * Driven on 2026-09-26: `scanFor(false, scan)` scrubbed `notional`, `margin`
 * and `unrealized_pnl` out of every open-position row and kept `contracts`
 * beside `entry_price` and `leverage`. For the venue readout's BTC row
 * (0.0158 contracts at 63000, 5x) the public view gave back a notional of
 * $995.40 and a margin of $199.08, to the cent: the two figures the scrub
 * had just dropped, one multiplication away. GET /api/bot/sync/scan serves
 * that view to anyone.
 *
 * Only the size goes. The symbol, the side, the entry and the leverage stay,
 * because none of them says anything about the account's size on its own.
 * `quantity` and `qty` are the executor's and the Guardian's names for the
 * same field, and `filled_qty` stands for the compound spellings the executor
 * also writes. Each is planted here, because a key no fixture carries is a
 * rule nothing measures.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');

const { scanFor } = require('../routes/sync');

const SIZE = /(contracts|quantity|qty)/i;

function sizeKeys(value, path = '') {
  if (Array.isArray(value)) return value.flatMap((v, i) => sizeKeys(v, `${path}[${i}]`));
  if (!value || typeof value !== 'object') return [];
  return Object.keys(value).flatMap((k) => (SIZE.test(k) ? [`${path}.${k}`] : [])
    .concat(sizeKeys(value[k], `${path}.${k}`)));
}

// The two row shapes the bot publishes (scan_skill: the venue readout and the
// executor-book fallback), plus a row spelled the executor's way.
const SCAN = {
  regime: 'CHOP',
  symbols: { BTCUSDT: { price: 63500 } },
  circuit_breaker: {
    equity: 141.22, open_count: 3, live_mode: true,
    open_positions: [
      { symbol: 'BTCUSDT', direction: 'LONG', entry_price: 63000, contracts: 0.0158,
        notional: 995.4, unrealized_pnl: -12.3, margin: 199.08, leverage: 5 },
      { symbol: 'ETHUSDT', direction: 'SHORT', entry_price: 3000, contracts: 0.5,
        filled_qty: 0.5, notional: 1500, unrealized_pnl: null, margin: null, leverage: '' },
      { symbol: 'SOLUSDT', direction: 'LONG', entry_price: 150, quantity: 6, leverage: 3 },
    ],
    closed_trades: [
      { symbol: 'SOL/USDT', direction: 'LONG', entry_price: 150, exit_price: 147,
        pnl: -41.2, qty: 6, closed_at: '2026-09-25T10:00:00Z' },
    ],
  },
};

test('no size reaches the anonymous view, and the rest of each row does', () => {
  const anon = scanFor(false, SCAN);
  assert.deepEqual(sizeKeys(anon.circuit_breaker), [], 'a position size was served anonymously');
  assert.deepEqual(anon.circuit_breaker.open_positions, [
    { symbol: 'BTCUSDT', direction: 'LONG', entry_price: 63000, leverage: 5 },
    { symbol: 'ETHUSDT', direction: 'SHORT', entry_price: 3000, leverage: '' },
    { symbol: 'SOLUSDT', direction: 'LONG', entry_price: 150, leverage: 3 },
  ]);
  assert.deepEqual(anon.circuit_breaker.closed_trades, [
    { symbol: 'SOL/USDT', direction: 'LONG', entry_price: 150, exit_price: 147,
      closed_at: '2026-09-25T10:00:00Z' },
  ]);
  // Counts survive: a count is not a size.
  assert.equal(anon.circuit_breaker.open_count, 3);
  assert.match(anon.disclosure, /position sizes are shown to the operator only/);
});

test('the notional and the margin cannot be rebuilt from what is left', () => {
  const anon = scanFor(false, SCAN);
  for (const p of anon.circuit_breaker.open_positions) {
    const size = p.contracts ?? p.quantity ?? p.qty;
    assert.equal(size, undefined, `${p.symbol}: a size rides beside its entry`);
  }
});

test('the operator keeps the whole row, and the scan is not copied for them', () => {
  const op = scanFor(true, SCAN);
  assert.equal(op, SCAN);
  assert.equal(op.circuit_breaker.open_positions[0].contracts, 0.0158);
});
