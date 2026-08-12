'use strict';
/**
 * The dossier's "Agent track record here" section, coloured green off a
 * number nobody measured.
 *
 *     const pnls = mine.map(t => parseFloat(t.pnl) || 0);
 *     const wins = pnls.filter(p => p > 0).length;
 *     const net  = round2(pnls.reduce((a, b) => a + b, 0));
 *     ...
 *     `${wins}W/${mine.length - wins}L, net <b class="${net >= 0 ? 'up' : 'down'}">`
 *
 * `trades.pnl` is nullable. So an unpriced close was summed as break-even,
 * filed as a loss, and — because `0 >= 0` is true — the fabricated total was
 * painted **green**. That is `(x || 0) >= 0` from CLAUDE.md's table: the
 * shape whose named failure is "unreadable **won**".
 *
 * This section is research a member reads before deciding to copy a pick, so
 * a wrong record here is advice.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const research = require('../lib/research');

const OPERATOR = parseInt(process.env.BOT_USER_ID) || 1;

async function closeTrade(symbol, pnl) {
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
    [OPERATOR, symbol, 'LONG', 3.0, 3.3, 500, pnl, 1, null,
     new Date(Date.now() - 86400000), new Date()]);
}

const trackSection = (d) =>
  (d?.sections || []).find((s) => s.title === 'Agent track record here');

test.before(async () => {
  research.setTickerFetcher(async () => ({
    ZZZUSDT: { price: 1, change: 0, volume: 1e6 },
    YYYUSDT: { price: 1, change: 0, volume: 1e6 },
  }));
  require('../lib/token_safety').setPairSearcher(async () => null);
});

test('an unpriced close is not a loss and not a zero in the total', async () => {
  // +40, -10, unpriced. Old: "1W/2L, net +$30.00" — the null summed as 0 and
  // scored as a defeat.
  await closeTrade('ZZZ/USDT', 40);
  await closeTrade('ZZZ/USDT', -10);
  await closeTrade('ZZZ/USDT', null);

  const s = trackSection(await research.buildDossier('ZZZ'));
  assert.ok(s, 'the track-record section did not render');
  assert.match(s.html, /closed <b>3<\/b> trade/, 'the close COUNT is 3 and stays 3');
  assert.match(s.html, /1W\/1L/, 'the unpriced close was filed as a defeat');
  assert.match(s.html, /over the 2 with a recorded P&amp;L/,
    'a partial window was printed as a whole one');
  assert.match(s.html, /\+\$30\.00/);
});

test('a record nothing can price says so, and carries no colour', async () => {
  await closeTrade('YYY/USDT', null);
  await closeTrade('YYY/USDT', null);

  const s = trackSection(await research.buildDossier('YYY'));
  assert.ok(s, 'the section did not render');
  assert.match(s.html, /none of them carry a recorded P&amp;L/);
  assert.ok(!/\$0\.00/.test(s.html), 'a fabricated $0.00 total');
  assert.ok(!/2W\/0L|0W\/2L/.test(s.html), 'a fabricated W/L line');
  assert.ok(!/class="up"|class="down"/.test(s.html),
    'green says "not down" as loudly as the digits do — `(x || 0) >= 0` '
    + 'painted an unreadable total green');
});
