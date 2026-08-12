'use strict';
/**
 * A week the record could not price was published as a losing week.
 *
 * Both letter composers — the private one and the PUBLIC one — opened with
 *
 *     const pnls = trades.map(t => parseFloat(t.pnl) || 0);
 *     const wins = pnls.filter(p => p > 0).length;
 *     const wr   = trades.length ? Math.round(wins / trades.length * 100) : null;
 *     ...
 *     `${trades.length} closes (${wins}W/${trades.length - wins}L)`
 *
 * `trades.pnl` is `DECIMAL(14,2)`, nullable. So one unpriced close was three
 * wrong claims at once: summed as a break-even, scored as a loss, and left
 * eligible to be named the week's **best trade at $0.00**.
 *
 * At the limit — a week where nothing could be priced — `net` was 0 and `wr`
 * was 0, so the composition fell through to
 *
 *     "A losing week, plainly: 4 closed trades, 0% winners, $0.00 net.
 *      The numbers below are the honest post-mortem."
 *
 * A verdict manufactured out of a gap in the record, in a letter that calls
 * itself honest, on the public surface as well as the private one.
 *
 * Both composers now run the same reader, so two letters describing one week
 * cannot disagree about which closes were legible.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const letter = require('../lib/letter');

const WEEK = {
  key: '2026-W28',
  start: new Date('2026-07-06T00:00:00Z'),
  end: new Date('2026-07-13T00:00:00Z'),
};

const trade = (symbol, pnl, day) => ({
  symbol, pnl, size_usd: 1000, fees: 1,
  closed_at: `2026-07-0${day}T10:00:00Z`,
});

const base = { equity: { start: null, end: null }, signals: [], openCount: 0, reports: null };

const both = (trades) => [
  ['private', letter.composeLetter(WEEK, { ...base, trades })],
  ['public', letter.composePublicLetter(WEEK, { ...base, trades })],
];

const text = (L) => `${L.headline}\n` + L.sections.map(s => `${s.title}: ${s.html}`).join('\n');

test('a week nothing could price is not a losing week', () => {
  const trades = [trade('BTC/USDT', null, 7), trade('SOL/USDT', null, 8),
                  trade('ETH/USDT', undefined, 9), trade('TAO/USDT', null, 10)];
  for (const [which, L] of both(trades)) {
    const all = text(L);
    assert.ok(!/A losing week, plainly/.test(all),
      `${which}: a gap in the record was published as a verdict`);
    assert.ok(!/0% winners/.test(all), `${which}: fabricated 0% win rate`);
    assert.ok(/none of them carry a recorded P&amp;L/.test(all),
      `${which}: the letter did not say why there is no result — got:\n${all}`);
    assert.ok(!/\$0(\.00)?\b/.test(all.replace(/\$0\.00 net after fees/g, '')),
      `${which}: printed a $0 total for an unpriceable week`);
  }
});

test('an unpriced close is not a loss and not a zero in the total', () => {
  // +120, -40, unpriced. Old: net $80 over "1W/2L" at 33%.
  // New: net $80 over the 2 we could price, 1W/1L at 50%.
  const trades = [trade('BTC/USDT', 120, 7), trade('SOL/USDT', -40, 8),
                  trade('ETH/USDT', null, 9)];
  const [, priv] = both(trades)[0] ? [null, both(trades)[0][1]] : [];
  const all = text(priv);
  assert.match(all, /1W\/1L/, 'the unpriced close was filed as a defeat');
  assert.ok(!/33% winners/.test(all), 'the unpriced close stayed in the denominator');
  assert.match(all, /50% winners|50%/);
  assert.match(all, /2 of 3 closes carry a recorded P&amp;L/,
    'a partial window was printed as a whole one');
});

test('an unpriced close can never be the week\'s best trade', () => {
  // Every priced trade LOST. With `|| 0` the unpriced close scored 0.00 and
  // beat all of them, so the letter named it the best trade of the week.
  const trades = [trade('BTC/USDT', -50, 7), trade('SOL/USDT', -30, 8),
                  trade('GHOST/USDT', null, 9)];
  const all = text(both(trades)[0][1]);
  assert.ok(!/best: GHOST/.test(all), 'an unpriced close was named the week\'s best');
  assert.match(all, /best: SOL/, 'the best PRICED trade should be named');
});

test('the two composers agree about which closes were legible', () => {
  const trades = [trade('BTC/USDT', 120, 7), trade('SOL/USDT', -40, 8),
                  trade('ETH/USDT', null, 9)];
  const [[, priv], [, pub]] = both(trades);
  const wl = (L) => (text(L).match(/(\d+)W\/(\d+)L/) || []).slice(1).join('/');
  assert.strictEqual(wl(priv), wl(pub), 'private and public letters disagree');
  assert.strictEqual(wl(priv), '1/1');
});

test('a fully priced week is unchanged — no caveat, no hedging', () => {
  // The other half of the rule: the disclosure must not appear on a healthy
  // week, or a real one gets skipped.
  const trades = [trade('BTC/USDT', 120, 7), trade('SOL/USDT', -40, 8),
                  trade('ETH/USDT', 60, 9)];
  for (const [which, L] of both(trades)) {
    const all = text(L);
    assert.match(all, /2W\/1L/, which);
    assert.ok(!/carry a recorded P&amp;L/.test(all),
      `${which}: a coverage caveat printed on a complete week`);
    assert.ok(!/no result to report/.test(all), which);
  }
});

test('a genuinely flat close is scored, and is neither a win nor a loss', () => {
  // pnl exactly 0.00 is a measurement. It must stay in the denominator and
  // out of both columns.
  const trades = [trade('BTC/USDT', 100, 7), trade('SOL/USDT', 0, 8)];
  const all = text(both(trades)[0][1]);
  assert.match(all, /1W\/0L/, 'a break-even close was filed as a loss');
  assert.ok(!/carry a recorded P&amp;L/.test(all),
    'a break-even close is priced — no coverage caveat is due');
});

test('an empty week still says nothing closed, not "unreadable"', () => {
  for (const [which, L] of both([])) {
    const all = text(L);
    assert.match(all, /closed no positions this week/, which);
    assert.ok(!/carry a recorded P&amp;L/.test(all), which);
  }
});
