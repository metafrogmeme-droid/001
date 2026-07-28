'use strict';

/**
 * A statistic over zero samples is absent, not zero.
 *
 * Production, 2026-07-28. The engine telemetry card read:
 *
 *     ENGINE EQUITY $378.88   NET PNL +0.00   WIN RATE 0.0%   OPEN 0
 *
 * while the operator's Telegram /portfolio for the same account reported a
 * realized PnL of -$10.74 over 95 trades at 57%. The sync payload was:
 *
 *     {"equity":378.88,"net_pnl":0,"win_rate":0,"total_trades":0}
 *
 * total_trades: 0. So net_pnl and win_rate were not measurements of zero —
 * there was nothing to measure. Rendering them as "+0.00" and "0.0%" states
 * a result the engine never produced, and states it in the same typeface as
 * the equity figure beside it, which IS real.
 *
 * The shared formatters were already correct: fmt/signed/fmtPrice all render
 * null as "—". The zeros simply never reached them. This is the same rule the
 * venue line one panel down already follows — "balance unreadable, the bot
 * did not answer" — applied to the card that sits above it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the guard exists and keys off the sample count', () => {
  assert.match(SRC, /const _haveTrades = \(cb\) => Number\(cb && cb\.total_trades\) > 0;/,
    'the question is whether any trade was recorded, not whether a number is truthy');
});

test('it is declared before every use', () => {
  const decl = SRC.indexOf('const _haveTrades');
  assert.ok(decl > 0);
  const uses = [...SRC.matchAll(/_haveTrades\(cb\)/g)].map((m) => m.index);
  assert.ok(uses.length >= 2, 'both statistics must be guarded');
  for (const u of uses) {
    assert.ok(u > decl, 'a helper declared after its use is a ReferenceError at runtime');
  }
});

test('both derived statistics are guarded, and equity is NOT', () => {
  // Slice FORWARD from the block: "Open" appears elsewhere earlier in the
  // file, so an indexOf-to-indexOf range inverts and silently yields ''.
  const start = SRC.indexOf('<div class="k">Engine equity</div>');
  assert.ok(start > 0, 'the engine card must still exist');
  const row = SRC.slice(start, start + 900);
  assert.match(row, /Net PnL[\s\S]*_haveTrades\(cb\)/);
  assert.match(row, /Win rate[\s\S]*_haveTrades\(cb\)/);
  // Equity is read from the exchange and is real even with no trades — it has
  // its own null check and must not be gated on the trade count.
  const equity = row.slice(0, row.indexOf('Net PnL'));
  assert.ok(!equity.includes('_haveTrades'),
    'equity is a live balance, not a statistic over trades');
  assert.match(equity, /cb\.equity != null/);
});

test('the absent rendering is a dash, never a zero', () => {
  // Anchor on the ENGINE card: the first "Net PnL" in the file belongs to the
  // signals panel (s.net_pnl), a different statistic entirely.
  const start = SRC.indexOf('<div class="k">Engine equity</div>');
  assert.ok(start > 0);
  const row = SRC.slice(start, start + 900);
  assert.ok(!/: '0/.test(row) && !/\? '0/.test(row),
    'the fallback must not be a zero in any form');
  // Assert the two guarded fallbacks EXACTLY, rather than counting dashes —
  // equity has its own independent dash in this slice, which is correct and
  // would make a count assertion pass or fail for the wrong reason.
  assert.match(row, /_haveTrades\(cb\) \? signed\(cb\.net_pnl\) : '—'/);
  assert.match(row, /_haveTrades\(cb\) \? fmt\(cb\.win_rate, 1\) \+ '%' : '—'/);
});

test('an absent PnL carries no colour', () => {
  const start = SRC.indexOf('<div class="k">Engine equity</div>');
  assert.ok(start > 0);
  const row = SRC.slice(start, start + 620);
  assert.match(row, /_haveTrades\(cb\) \? pnlClass\(cb\.net_pnl\) : ''/,
    'green or red on a number that does not exist reads as a real result');
});
