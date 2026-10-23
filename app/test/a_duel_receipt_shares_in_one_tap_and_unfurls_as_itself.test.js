'use strict';
/**
 * Sharing a Daily Duel receipt from the Telegram Mini App, and what the shared
 * link shows.
 *
 *   the button      the receipt sheet offers Share only where the host can
 *                   share (the Mini App) and only for a receipt that checked
 *                   out or is honestly still sealed
 *   the transport   Telegram's own share sheet (t.me/share/url), with the
 *                   receipt's page on this site; the player picks the chat
 *   the preview     /api/frame/call/<duel key>/image draws the Duel's own card
 *                   (lib/duel_frame.js): the market and the pick from the
 *                   receipt card's model, green and red only for a verdict
 *                   read from the settlement's own seal, and a sealed round
 *                   with nothing to hash claims no match either way
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const express = require('express');

const F = require('../lib/duel_frame');
const { FONT } = require('../lib/pixel_card');

const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const PAY = {
  v: 4, kind: 'duel_pick', round_id: 7, day: '2026-10-22', category: 'range', symbol: 'BTCUSDT', symbol_b: null,
  threshold_pct: 1.5, handle: null, pick: 'hold', entry_price: 108000, entry_price_b: null,
  resolves_at: '2026-10-23T09:00:00.000Z', picked_at: '2026-10-22T09:14:00.000Z',
};
function pick(over = {}, pay = PAY) {
  const seal_payload = JSON.stringify(pay);
  return { kind: 'duel_pick', seal: sha(seal_payload), seal_payload, sealed_at: pay.picked_at, anchor: null, settlement: null, ...over };
}
function settlement(state, over = {}) {
  const seal_payload = JSON.stringify({ v: 7, kind: 'duel_settle', settle_state: state, disputed: null });
  return { kind: 'duel_settle', seal: sha(seal_payload), seal_payload, review: null, flagged: false, ...over };
}
const settledPick = (sr) => pick({ settlement: { key: 'settle:' + sr.seal, seal: sr.seal, state: 'x' } });
const texts = (drawn) => drawn.lines.map((l) => l.text);
const toned = (drawn, tone) => drawn.lines.filter((l) => l.tone === tone).map((l) => l.text);

test('a call\'s card names the market and the pick from the sealed payload, and claims no verdict before one is sealed', () => {
  const d = F.lines(pick(), null);
  assert.strictEqual(d.title, 'DAILY DUEL RECEIPT');
  assert.deepStrictEqual(texts(d), [
    'BTC', 'CALLED HOLDS +/-1.5%', 'SEAL ' + pick().seal.slice(0, 32), 'SERVER RECOMPUTED: MATCH',
    'SEALED 22 OCT 2026, 09:14 UTC, BEFORE THE RESULT.', 'SETTLES 23 OCT 2026, 09:00 UTC.',
    'OPEN TO RE-DERIVE THE HASH IN YOUR OWN BROWSER',
  ]);
  // Green is the server's match and nothing else: a pick is not a side.
  assert.deepStrictEqual(toned(d, 'up'), ['SERVER RECOMPUTED: MATCH']);
  assert.deepStrictEqual(toned(d, 'down'), []);
  assert.ok(!texts(d).join(' ').includes('108000'), 'a price rode onto a public preview');
  const mover = F.lines(pick({}, { ...PAY, category: 'mover', symbol_b: 'ETHUSDT', pick: 'second', threshold_pct: null }), null);
  assert.deepStrictEqual(texts(mover).slice(0, 2), ['BTC VS ETH', 'CALLED ETH MOVES MORE']);
  const match = F.lines(pick({}, { ...PAY, category: 'sports', symbol: 'FD:1', pick: 'home', entry_price: null }), null);
  assert.deepStrictEqual(texts(match).slice(0, 2), ['FOOTBALL MATCH', 'CALLED HOME WIN']);
});

test('the verdict is the settlement\'s own sealed payload, coloured only when it was measured', () => {
  const v = (sr) => {
    const d = F.lines(settledPick(sr), sr);
    return d.lines.find((l) => l.text.startsWith('SETTLED') || l.text.startsWith('THE RESULT')) || d.lines[5];
  };
  assert.deepStrictEqual(v(settlement('correct')), { text: 'SETTLED: CORRECT.', size: 2, tone: 'up' });
  assert.deepStrictEqual(v(settlement('wrong')), { text: 'SETTLED: WRONG.', size: 2, tone: 'down' });
  assert.deepStrictEqual(v(settlement('push')), { text: 'SETTLED: NO RESULT.', size: 2, tone: 'muted' });
  assert.deepStrictEqual(v(settlement('correct', { flagged: true })), { text: 'SETTLED: UNDER REVIEW.', size: 2, tone: 'muted' });
  // A settlement whose payload is not its seal is not read: the result is on the receipt.
  const broken = { ...settlement('correct'), seal_payload: JSON.stringify({ kind: 'duel_settle', settle_state: 'wrong' }) };
  assert.deepStrictEqual(F.lines(pick({ settlement: { key: 'settle:' + broken.seal } }), broken).lines[5],
    { text: 'SETTLED: THE RESULT IS ON THE RECEIPT.', size: 2, tone: 'muted' });
  // Settled, and the settlement could not be read at all: the same, never a verdict.
  assert.strictEqual(F.lines(settledPick(settlement('correct')), null).lines[5].tone, 'muted');
});

test('a payload that does not hash to its seal draws no market and no pick', () => {
  const bad = { ...pick(), seal_payload: JSON.stringify({ ...PAY, pick: 'break' }) };
  const d = F.lines(bad, null);
  assert.deepStrictEqual(texts(d).slice(0, 1), ['NOT INTACT']);
  assert.ok(texts(d).includes('SERVER RECOMPUTED: MISMATCH'));
  assert.ok(!texts(d).some((t) => /BREAK|HOLD|BTC/.test(t)), 'a broken payload was drawn as a call');
});

test('a round still sealed says when it opens and claims no match either way', () => {
  const d = F.lines({ kind: 'duel_round', seal: 'a'.repeat(64), locked: true, opens_at: '2026-10-23T00:00:00.000Z' }, null);
  assert.strictEqual(d.title, 'DAILY DUEL ROUND');
  assert.deepStrictEqual(texts(d), ['SEALED', 'SEAL ' + 'a'.repeat(32), 'OPENS 2026-10-23 00:00 UTC',
    'NOTHING TO RECOMPUTE UNTIL THEN - NOTHING CLAIMED']);
  assert.deepStrictEqual(toned(d, 'up').concat(toned(d, 'down')), []);
  // Opened, the same round's seal is recomputed like any other.
  const payload = JSON.stringify({ kind: 'duel_round', day: '2026-10-22' });
  const open = F.lines({ kind: 'duel_round', seal: sha(payload), seal_payload: payload }, null);
  assert.ok(texts(open).includes('SERVER RECOMPUTED: MATCH'));
});

test('everything drawn is in the card\'s font, so nothing is silently blanked', () => {
  const all = [F.lines(pick(), null), F.lines(settledPick(settlement('correct')), settlement('correct')),
    F.lines({ kind: 'duel_review', seal: 'b'.repeat(64), locked: true }, null)];
  for (const d of all) {
    // The card upper-cases as it draws (lib/pixel_card.js text), so a seal's hex is drawn too.
    for (const ch of [d.title, ...texts(d)].join('').toUpperCase()) assert.ok(ch in FONT, `'${ch}' is not in the font`);
  }
  assert.strictEqual(F.plain('±1.5% — it’s…'), '+/-1.5% - ITS...');
});

// ── the route ────────────────────────────────────────────────────────────────
test('the image route draws a Duel key through its own card, reading the settlement it names', async () => {
  const call = require('../routes/call');
  const sr = settlement('correct');
  const d = settledPick(sr);
  const asked = [];
  const real = call.lookupCall;
  call.lookupCall = async (key) => { asked.push(key); return { code: 200, body: key.startsWith('settle:') ? sr : d }; };
  const app = express();
  app.use('/api/frame', require('../routes/frame'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  try {
    const res = await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${server.address().port}/api/frame/call/duel:${d.seal}/image`, (r) => {
        const b = [];
        r.on('data', (c) => b.push(c));
        r.on('end', () => resolve({ status: r.statusCode, type: r.headers['content-type'], body: Buffer.concat(b) }));
      }).on('error', reject);
    });
    assert.strictEqual(res.status, 200);
    assert.match(res.type, /image\/png/);
    assert.deepStrictEqual(asked, ['duel:' + d.seal, 'settle:' + sr.seal]);
  } finally {
    call.lookupCall = real;
    server.close();
  }
});

// ── the Mini App ─────────────────────────────────────────────────────────────
global.window = global.window || {};
global.document = global.document || { hidden: true, addEventListener() {} };
global.window.RCDuel = require('../public/js/duel.js');
global.window.RCMiniView = require('../public/js/miniapp-view.js');
global.window.RCPiReceipt = require('../public/js/pi-receipt.js');
const R = global.window.RCPiReceipt;
const { create } = require('../public/js/pi-duel.js');

test('the sheet offers Share only where the host can share, and only for a receipt that held', () => {
  const share = /class="p-rc-share"/;
  for (const state of ['verified', 'sealed']) {
    assert.match(R.sheetHtml('Your call', { state, seal: 'a'.repeat(64) }, { share: true }), share, state);
    assert.doesNotMatch(R.sheetHtml('Your call', { state, seal: 'a'.repeat(64) }), share, `${state} without a host`);
  }
  for (const res of [null, { state: 'mismatch' }, { state: 'unreadable' }, { state: 'bad_key' }]) {
    assert.doesNotMatch(R.sheetHtml('Your call', res, { share: true }), share, JSON.stringify(res));
  }
});

test('Share hands the host this receipt\'s page, after the sheet checked it', async () => {
  const d = pick();
  const shared = [];
  const block = create({ share: (p, text) => shared.push([p, text]) });
  const ctx = {
    api: async (p) => (p === '/api/call/duel:' + d.seal ? { ok: true, status: 200, body: d } : { ok: false, status: 404, body: null }),
    render() {}, refresh: async () => {}, toast() {},
  };
  const el = (sel) => ({ closest: (s) => (s === sel ? { getAttribute: (a) => (a === 'data-receipt' ? 'duel:' + d.seal : 'Your call') } : null) });
  // No receipt open: the tap does nothing.
  assert.strictEqual(block.click(el('.p-duel .p-rc-share'), ctx), true);
  assert.deepStrictEqual(shared, []);
  block.click(el('.p-duel .d-rc-v'), ctx);
  await new Promise((r) => setTimeout(r, 50));
  assert.match(block.html(), /class="p-rc-share"/, 'a checked receipt offers Share in the Mini App');
  block.click(el('.p-duel .p-rc-share'), ctx);
  assert.deepStrictEqual(shared, [['/call/' + encodeURIComponent('duel:' + d.seal), 'Sealed before the result. Check it yourself:']]);
  // The Pi app passes no share: its sheet has no button.
  const pi = create({});
  pi.click(el('.p-duel .d-rc-v'), ctx);
  await new Promise((r) => setTimeout(r, 50));
  assert.doesNotMatch(pi.html(), /p-rc-share/);
});

test('the Mini App shares through Telegram\'s own share sheet, with the receipt on this site', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'tg-app.js'), 'utf8');
  const opened = [];
  let duelOpts = null;
  const win = {
    Telegram: { WebApp: { initData: '', ready() {}, expand() {}, openTelegramLink: (u) => opened.push(u) } },
    RCMiniView: { waitingFor: () => '' },
    RCPiDuel: { create: (o) => { duelOpts = o; return {}; } },
    RCPiProof: { create: () => ({}) },
    RCArenaSession: { start() {} },
    open: () => { throw new Error('the plain link was used while Telegram could open it'); },
  };
  vm.runInNewContext(src, { window: win, document: { documentElement: { getAttribute: () => 'en' }, getElementById: () => null },
    location: { origin: 'https://www.humanoid-traders.com' }, encodeURIComponent, Promise });
  assert.strictEqual(typeof duelOpts.share, 'function', 'the Duel block was given no way to share');
  duelOpts.share('/call/duel%3Aabc', 'Sealed before the result. Check it yourself:');
  assert.deepStrictEqual(opened, ['https://t.me/share/url?url=' + encodeURIComponent('https://www.humanoid-traders.com/call/duel%3Aabc')
    + '&text=' + encodeURIComponent('Sealed before the result. Check it yourself:')]);
});
