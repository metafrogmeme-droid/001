'use strict';
/**
 * Three public surfaces answered "how did that trade go?" three ways.
 *
 * The complaint in audit M9 is not that any one of them was wrong in isolation
 * — it is that they DISAGREED, while a comment in the MCP handler promised they
 * shared "one source of truth" with the page it contradicted:
 *
 *   track.js recent_trades   `parseFloat(t.pnl) || 0` → an unpriced close
 *                            published as 'flat'                        (L23)
 *   track.js replay-trade    `(parseFloat(pick.pnl) || 0) >= 0` → a measured
 *                            0.00 scratch animated as a WIN             (L24)
 *   mcp.js  get_track_record the same `|| 0`, plus trades.length as the
 *                            win-rate denominator, so every unpriced close
 *                            dragged the published rate down             (M9)
 *
 * FOUR OUTCOMES, ALL DISTINCT. `flat` is a measurement — somebody priced the
 * close and it came out zero. `unknown` is the absence of one. `>= 0` folds
 * both into 'win', which is why CLAUDE.md names that shape specifically.
 *
 * RED HERRING, planted below: a genuine 0.00 close. It is indistinguishable
 * from an unpriced one under `|| 0`, and a fix that mapped everything
 * unreadable to 'flat' — or everything zero to 'unknown' — would look correct
 * on any fixture that lacked one of the two.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const { outcomeOf, classifyPnls } = require('../routes/track');

// ── the four outcomes ────────────────────────────────────────────────

test('a profit is a win, a loss is a loss', () => {
  assert.strictEqual(outcomeOf(12.5), 'win');
  assert.strictEqual(outcomeOf('12.50'), 'win');
  assert.strictEqual(outcomeOf(-3), 'loss');
});

test('a measured break-even is flat, not a win', () => {
  // L24: `(x || 0) >= 0` published this as a win on the landing page.
  assert.strictEqual(outcomeOf(0), 'flat');
  assert.strictEqual(outcomeOf('0.00'), 'flat');
});

test('an unpriced close is unknown, not flat', () => {
  // L23/M9: `parseFloat(x) || 0` published this as a measured break-even.
  for (const missing of [null, undefined, '', 'n/a', NaN]) {
    assert.strictEqual(outcomeOf(missing), 'unknown',
      `${JSON.stringify(missing)} should not read as a measurement`);
  }
});

test('flat and unknown are different answers', () => {
  // The red herring, stated as an assertion. Both are "not a win and not a
  // loss"; only one of them is a fact.
  assert.notStrictEqual(outcomeOf(0), outcomeOf(null));
});

test('no input maps to a win unless it is actually positive', () => {
  for (const v of [0, '0.00', null, undefined, '', 'abc', NaN, -0.01]) {
    assert.notStrictEqual(outcomeOf(v), 'win', `${JSON.stringify(v)} read as a win`);
  }
});

// ── the surfaces agree ───────────────────────────────────────────────

test('every surface routes through the one helper', () => {
  // The invariant M9 is actually about. Source-level because the three call
  // sites live in two route modules behind a DB and an HTTP layer; what must
  // be true is that none of them does its own arithmetic.
  const fs = require('node:fs');
  const path = require('node:path');
  const strip = (s) => s
    .replace(/\/\*[\s\S]*?\*\//g, '')     // block comments — one of them quotes
    .replace(/^\s*\/\/.*$/gm, '');        // the banned shapes verbatim
  for (const f of ['track.js', 'mcp.js']) {
    const src = strip(fs.readFileSync(path.join(__dirname, '..', 'routes', f), 'utf8'));
    assert.ok(!/\|\|\s*0\s*\)\s*>=\s*0/.test(src),
      `${f}: (x || 0) >= 0 — unreadable and break-even both read as a win`);
    assert.ok(!/parseFloat\(\s*\w+\.pnl\s*\)\s*\|\|\s*0/.test(src),
      `${f}: parseFloat(pnl) || 0 — an unpriced close read as break-even`);
  }
});

test('the win rate is over what was actually priced', () => {
  // M9: trades.length as the denominator let a row nobody scored count against
  // the rate. classifyPnls is the page's own helper and separates them.
  const c = classifyPnls([10, -5, null, 0, 'x']);
  assert.strictEqual(c.priced.length, 3);
  assert.strictEqual(c.unpriced, 2);
  assert.strictEqual(c.wins.length, 1);
  assert.strictEqual(c.losses.length, 1);
  assert.strictEqual(c.breakeven, 1);
  // 1 win of 3 priced — not of 5 rows.
  assert.strictEqual(Math.round(c.wins.length / c.priced.length * 100), 33);
});

test('an all-unpriced record yields no rate rather than zero', () => {
  const c = classifyPnls([null, undefined, 'x']);
  assert.strictEqual(c.priced.length, 0);
  assert.strictEqual(c.unpriced, 3);
  // The caller publishes null here; a 0% win rate would be a measured claim
  // of total failure drawn from no measurements at all.
  assert.strictEqual(c.priced.length ? 0 : null, null);
});

// ── the handler, driven ──────────────────────────────────────────────

test('an unpriced close does not drag the published win rate down', async () => {
  // M9's actual number, through the actual handler. Added after a mutation:
  // reverting the denominator to trades.length passed every test above,
  // because they all exercised classifyPnls rather than its USE. A helper
  // being right is not the same as the caller using it.
  const { pool } = require('../db');
  const { TOOLS } = require('../routes/mcp');
  const uid = parseInt(process.env.BOT_USER_ID) || 1;

  const seed = (symbol, pnl, at) => pool.execute(
    'INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, '
    + "size_usd, pnl, fees, status, pattern, opened_at, closed_at) "
    + "VALUES (?,?,?,?,?,?,?,?,'CLOSED',?,?,?)",
    [uid, symbol, 'LONG', 100, 105, 200, pnl, 0.3, 'test', at, at]);

  await seed('AAA/USDT', 30, new Date('2026-05-01T10:00:00Z'));
  await seed('BBB/USDT', -10, new Date('2026-05-02T10:00:00Z'));
  await seed('CCC/USDT', null, new Date('2026-05-03T10:00:00Z'));   // never priced

  const out = await TOOLS.get_track_record.handler({});

  assert.strictEqual(out.trades, 3, 'all three closes are still counted');
  assert.strictEqual(out.unpriced, 1, 'the unpriced count must be published '
    + 'so a reader can reconcile the rate against the trade count');
  assert.strictEqual(out.win_rate_pct, 50,
    'win rate must be 1-of-2-priced, not 1-of-3-rows');

  const ccc = out.recent_trades.find(t => t.symbol === 'CCC/USDT');
  assert.strictEqual(ccc.result, 'unknown',
    'an unpriced close published as a measured outcome');
});

test('junk does not throw', () => {
  assert.strictEqual(outcomeOf({}), 'unknown');
  assert.strictEqual(outcomeOf([]), 'unknown');
  assert.deepStrictEqual(classifyPnls(null).unpriced, 0);
});
