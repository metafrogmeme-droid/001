'use strict';
/**
 * The Pi app's Daily Duel block (public/js/pi-duel.js), driven in Node with
 * a stand-in session: what the Duel tab's badge counts, what the progress
 * line says, and where a recorded call and a refusal are shown.
 *
 * A recorded call is a toast (it is good news, and the round itself now
 * shows the call); a refusal stays in the block until it is read, because
 * a notice that vanishes is a refusal the player may never see.
 */
const test = require('node:test');
const assert = require('node:assert');

global.window = { RCDuel: require('../public/js/duel.js'), RCMiniView: require('../public/js/miniapp-view.js') };
// `hidden`: the block's two-minute re-read timer would otherwise keep the process alive.
global.document = { hidden: true, addEventListener() {} };
const { create, recordedText } = require('../public/js/pi-duel.js');

const CARD = { day: '2026-10-19', horizon_hours: 24, open: 1, rounds: [
  { id: 1, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true },
  { id: 2, kind: 'range', symbol: 'BNBUSDT', options: ['break', 'hold', 'pass'], callable: true, threshold_pct: 2,
    my_call: { pick: 'hold', pending: true }, my_result: { state: 'unresolved', marks: 0, beat_agent: false } },
  { id: 3, kind: 'sports', symbol: 'FD:1', options: ['home', 'draw', 'away', 'pass'], callable: false,
    match: { home: 'A', away: 'B', kickoff: '2026-10-19T10:00:00.000Z' } },
] };
const ME = { accuracy: { pct: null, scored: 0, unresolved: 1 }, marks: { marks: 0, pending: 1 }, streak: { current: 1 } };

function session(pickAnswer) {
  const calls = { toasts: [], renders: 0, posts: [] };
  const ctx = {
    api(path, o) {
      if (path === '/api/duel/today') return Promise.resolve({ ok: true, status: 200, body: CARD });
      if (path === '/api/duel/me') return Promise.resolve({ ok: true, status: 200, body: ME });
      if (path === '/api/duel/pick') { calls.posts.push(o.body); return Promise.resolve(pickAnswer); }
      return Promise.resolve({ ok: false, status: 404, body: null });
    },
    render() { calls.renders++; },
    refresh() { return Promise.resolve(); },
    toast(kind, text) { calls.toasts.push([kind, text]); },
  };
  return { ctx, calls };
}
const settle = () => new Promise((r) => setTimeout(r, 30));
const tap = (block, ctx, roundId, pick) => {
  const wrap = { getAttribute: () => String(roundId) };
  const btn = { closest: () => wrap, getAttribute: () => pick };
  return block.click({ closest: (sel) => (sel.indexOf('d-btn') >= 0 ? btn : null) }, ctx);
};

test('the badge counts the rounds still open to a call: not the called one, not the closed one', async () => {
  const block = create();
  const { ctx } = session();
  block.signedIn(ctx);
  await settle();
  assert.strictEqual(block.badge(), 1);
  const html = block.html();
  assert.match(html, /1 of 3 called/);
  assert.match(html, /1 open/);
  assert.match(html, /aria-valuenow="1"/);
  assert.strictEqual(block.tab, 'duel');
});

test('a recorded call is a toast and leaves no notice in the block', async () => {
  const block = create();
  const { ctx, calls } = session({ ok: true, status: 200, body: { ok: true, kind: 'direction', symbol: 'BTCUSDT', pick: 'long',
    entry_price: 100, agent_direction: 'short' } });
  block.signedIn(ctx);
  await settle();
  assert.strictEqual(tap(block, ctx, 1, 'long'), true);
  await settle();
  assert.deepStrictEqual(calls.posts, [{ round_id: 1, pick: 'long' }]);
  assert.strictEqual(calls.toasts.length, 1);
  assert.strictEqual(calls.toasts[0][0], 'ok');
  assert.match(calls.toasts[0][1], /Call recorded: LONG on BTC at 100.*The Claw called SHORT/);
  assert.doesNotMatch(block.html(), /m-problem--ok/, 'the toast is the notice; the block repeats nothing');
});

test('a refusal stays in the block, in the server’s words, and is never a toast', async () => {
  const block = create();
  const { ctx, calls } = session({ ok: false, status: 409, body: { error: 'This round has closed.' } });
  block.signedIn(ctx);
  await settle();
  tap(block, ctx, 1, 'short');
  await settle();
  assert.deepStrictEqual(calls.toasts, []);
  assert.match(block.html(), /m-problem--warn[^>]*>This round has closed\./);
});

test('without a toast on the session, a recorded call is shown in the block', async () => {
  const block = create();
  const { ctx, calls } = session({ ok: true, status: 200, body: { ok: true, kind: 'range', symbol: 'BNBUSDT', pick: 'hold', entry_price: 600, threshold_pct: 2 } });
  delete ctx.toast;
  block.signedIn(ctx);
  await settle();
  tap(block, ctx, 2, 'hold');
  await settle();
  assert.deepStrictEqual(calls.toasts, []);
  assert.match(block.html(), /m-problem--ok[^>]*>Call recorded: HOLDS ±2% on BNB at 600/);
});

test('the recorded-call sentence quotes a price for a market call and the match for a football call, never a dash', () => {
  assert.strictEqual(recordedText({ kind: 'direction', symbol: 'BTCUSDT', pick: 'long', entry_price: 82024.5, agent_direction: 'short' }),
    'Call recorded: LONG on BTC at 82024.50, snapped for you just now. The Claw called SHORT.');
  assert.strictEqual(recordedText({ kind: 'sports', symbol: 'FD:441', pick: 'draw', entry_price: null,
    match: { home: 'Arsenal FC', away: 'Chelsea FC', home_short: 'Arsenal', away_short: 'Chelsea' } }),
    'Call recorded: DRAW on Arsenal vs Chelsea. Settled on the final score.');
  assert.strictEqual(recordedText({ kind: 'sports', symbol: 'FD:441', pick: 'home', entry_price: null, match: { home: 'Arsenal FC', away: 'Chelsea FC' } }),
    'Call recorded: ARSENAL FC on Arsenal FC vs Chelsea FC. Settled on the final score.');
});
