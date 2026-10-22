'use strict';
/**
 * The receipt card (public/js/receipt-card.js, lib/receipt_qr.js, and the
 * "Make a card" block on /call): one Daily Duel call as a picture, with a QR
 * code that opens its receipt.
 *
 *   made from the seal   no card unless this browser found the payload intact,
 *                        and the market, the call and the times are the
 *                        payload's
 *   three marks          SEALED, SETTLED, ANCHORED, each done, not yet, or
 *                        (the anchor) could not be checked; ANCHORED only on
 *                        the quorum's multi_anchored
 *   the verdict          read from the settlement's own sealed payload, the
 *                        review's when one exists, "under review" when flagged
 *   colour               the accent for a done mark, green and red only for a
 *                        measured verdict
 *   the QR               the receipt page on the public origin, a matrix with
 *                        no margin, drawn with the spec's four-module quiet
 *                        zone; an origin the server cannot vouch for is refused
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const Card = require('../public/js/receipt-card.js');
const Duel = require('../public/js/duel.js');
const { qrFor, receiptUrl } = require('../lib/receipt_qr');
const { codeOnly } = require('./helpers/code_only');

const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const PAY = {
  v: 4, kind: 'duel_pick', round_id: 7, day: '2026-10-22', category: 'direction', symbol: 'BTCUSDT', symbol_b: null,
  threshold_pct: null, handle: null, pick: 'long', entry_price: 108000, entry_price_b: null,
  resolves_at: '2026-10-23T09:00:00.000Z', picked_at: '2026-10-22T09:14:00.000Z',
};
function receipt(over = {}, pay = PAY) {
  const seal_payload = JSON.stringify(pay);
  return { kind: 'duel_pick', seal: sha(seal_payload), seal_payload, sealed_at: pay.picked_at, anchor: null, settlement: null, ...over };
}
const ok = (d, o = {}) => Card.model(d, JSON.parse(d.seal_payload), { intact: true, duel: Duel, ...o });
const text = (parts) => parts.map((x) => x.text).join('');

test('no card unless this browser found the seal intact, and only for a Duel call', () => {
  const d = receipt();
  const p = JSON.parse(d.seal_payload);
  for (const intact of [false, null, undefined, 'true']) assert.strictEqual(Card.model(d, p, { intact, duel: Duel }), null, String(intact));
  assert.strictEqual(Card.model({ ...d, kind: 'duel_settle' }, p, { intact: true, duel: Duel }), null);
  assert.strictEqual(Card.model(d, { ...p, kind: 'duel_round' }, { intact: true, duel: Duel }), null);
  assert.strictEqual(Card.model(d, p, { intact: true }), null, 'no Duel words, no card');
  assert.strictEqual(Card.model({ ...d, seal: 'nothex' }, p, { intact: true, duel: Duel }), null);
  assert.notStrictEqual(ok(d), null);
});

test('the market, the call and the times are the sealed payload\'s', () => {
  const m = ok(receipt());
  assert.deepStrictEqual([m.market, m.glyph, m.pick, m.seal], ['BTC', '▲', 'LONG', `${receipt().seal.slice(0, 4)} ${receipt().seal.slice(4, 8)} … ${receipt().seal.slice(56, 60)} ${receipt().seal.slice(60)}`]);
  assert.strictEqual(text(m.lines[0]), 'Sealed 22 Oct 2026, 09:14 UTC, before the result.');
  assert.strictEqual(text(m.lines[1]), 'Settles 23 Oct 2026, 09:00 UTC.');
  assert.deepStrictEqual(m.lines[0].filter((x) => x.strong).map((x) => x.text), ['22 Oct 2026, 09:14 UTC']);
  // A mover names both markets; a football call's seal names no team, so the card names none.
  assert.strictEqual(ok(receipt({}, { ...PAY, category: 'mover', symbol_b: 'ETHUSDT', pick: 'second' })).market, 'BTC vs ETH');
  const f = ok(receipt({}, { ...PAY, category: 'sports', symbol: 'EPL', pick: 'home', entry_price: null }));
  assert.deepStrictEqual([f.market, f.pick], ['Football match', 'HOME WIN']);
  assert.strictEqual(ok(receipt({}, { ...PAY, category: 'sports', pick: 'away' })).pick, 'AWAY WIN');
  assert.strictEqual(ok(receipt({}, { ...PAY, category: 'sports', pick: 'draw' })).pick, 'DRAW');
  // A time that does not read is a dash, never a made-up date.
  assert.strictEqual(text(ok(receipt({}, { ...PAY, resolves_at: 'soon' })).lines[1]), 'Settles —.');
  // Every word goes through the translator.
  const de = ok(receipt(), { t: (k, en) => (k === 'rc.st_sealed' ? 'VERSIEGELT' : k === 'duel.b_long' ? 'LONG-DE' : en), lang: 'de' });
  assert.deepStrictEqual([de.steps[0].label, de.pick], ['VERSIEGELT', 'LONG-DE']);
  assert.match(text(de.lines[0]), /22\. Okt\. 2026/);
});

test('SETTLED and its verdict: the settlement\'s seal, then its review, then its flag', () => {
  const settled = receipt({ settlement: { key: 'settle:' + 'a'.repeat(64), seal: 'a'.repeat(64), state: 'correct' } });
  const s = (state, extra = {}) => ({ current: { settle_state: state, disputed: null }, review: null, flagged: false, ...extra });
  const v = (settle) => {
    const m = ok(settled, { settle });
    assert.strictEqual(m.steps[1].state, 'done');
    return [m.verdict.text, m.verdict.tone, text(m.lines[1])];
  };
  assert.deepStrictEqual(v(s('correct')), ['Correct', 'up', 'Settled: Correct.']);
  assert.deepStrictEqual(v(s('wrong')), ['Wrong', 'down', 'Settled: Wrong.']);
  assert.deepStrictEqual(v(s('push')).slice(0, 2), ['No result', 'muted']);
  assert.deepStrictEqual(v(s('unresolved')).slice(0, 2), ['Unresolved — no price', 'muted']);
  assert.deepStrictEqual(v(s('correct', { current: { settle_state: 'correct', disputed: true } })).slice(0, 2), ['Correct · disputed', 'up']);
  assert.deepStrictEqual(v(s('wrong', { review: { decision: 'stands' } })).slice(0, 2), ['Wrong · reviewed', 'down']);
  assert.deepStrictEqual(v(s('wrong', { review: { decision: 'push' } })).slice(0, 2), ['No result', 'muted']);
  assert.deepStrictEqual(v(s('wrong', { review: { decision: 'corrected' } })).slice(0, 2), ['corrected on review', 'muted']);
  assert.deepStrictEqual(v(s('correct', { flagged: true })).slice(0, 2), ['under review', 'muted'], 'a flagged verdict may still change');
  assert.deepStrictEqual(v(null).slice(0, 2), ['the result is on the receipt', 'muted'], 'a settlement not read is not a verdict');
  // Not settled: the mark is not done and nothing is green.
  const m = ok(receipt());
  assert.deepStrictEqual([m.steps[1].state, m.verdict], ['pending', null]);
});

test('the verdict is read from the settlement\'s sealed payload, and only when its hash is its seal', () => {
  const payload = JSON.stringify({ v: 9, kind: 'duel_settle', settle_state: 'correct', disputed: false });
  const sr = { kind: 'duel_settle', seal: sha(payload), seal_payload: payload, review: { decision: 'stands' }, flagged: false,
    current: { settle_state: 'wrong' } };
  assert.deepStrictEqual(Card.settleFrom(sr, sha(payload)),
    { current: { settle_state: 'correct', disputed: false }, review: { decision: 'stands' }, flagged: false }, 'the payload, not the row');
  assert.strictEqual(Card.settleFrom(sr, sha(payload + ' ')), null, 'a hash that is not the seal');
  // A payload that hashes to its own seal but is not a settlement (a call's, here) gives no verdict.
  const callPay = JSON.stringify({ kind: 'duel_pick', settle_state: 'correct' });
  assert.strictEqual(Card.settleFrom({ ...sr, seal: sha(callPay), seal_payload: callPay }, sha(callPay)), null);
  assert.strictEqual(Card.settleFrom({ ...sr, seal_payload: '{' }, sha('{')), null);
  assert.strictEqual(Card.settleFrom(null, 'x'), null);
  assert.strictEqual(Card.settleFrom({ ...sr, flagged: 'yes' }, sha(payload)).flagged, false, 'only a true flag is a flag');
});

test('ANCHORED is done only when the day is held by independent chains; not read is not "not anchored"', () => {
  const a = (anchor, quorum) => { const st = ok(receipt({ anchor }), { quorum }).steps[2]; return [st.state, st.note]; };
  const day = { day: '2026-10-22', root: 'r' };
  assert.deepStrictEqual(a(null, null), ['pending', 'Not in a day’s root yet']);
  assert.deepStrictEqual(a(day, null), ['unknown', 'Not checked yet']);
  assert.deepStrictEqual(a(day, { status: 'multi_anchored', groups: [['base'], ['bitcoin']], k: 2 }), ['done', 'Held by 2 independent chains']);
  assert.deepStrictEqual(a(day, { status: 'short', groups: [['base']], k: 2 }), ['pending', '1 of 2 independent chains so far']);
  assert.deepStrictEqual(a(day, { status: 'short', groups: [], k: 2 }), ['pending', 'Not anchored yet']);
  assert.deepStrictEqual(a(day, { status: 'unknown', groups: [['base']] }), ['unknown', 'Could not be checked just now']);
  assert.deepStrictEqual(a(day, { error: 'x' }), ['unknown', 'Not checked yet']);
});

/** A stand-in for the canvas's measureText: the font's pixel size times 0.55 per character. */
const measure = (font, s) => Number(/(\d+)px/.exec(font)[1]) * 0.55 * String(s).length;
const QR = qrFor('https://www.humanoid-traders.com', 'duel:' + 'c'.repeat(64));

test('the drawing: the accent for a done mark, green and red only for a measured verdict, no money', () => {
  const colours = (list) => list.flatMap((o) => [o.color, o.fill, o.stroke].concat((o.runs || []).map((r) => r.color))).filter(Boolean);
  let list = Card.ops(ok(receipt()), QR, measure);
  assert.ok(!colours(list).includes(Card.C.up) && !colours(list).includes(Card.C.down), 'nothing measured, nothing green or red');
  const pills = list.filter((o) => o.op === 'pill');
  assert.deepStrictEqual(pills.map((o) => o.done), [true, false, false]);
  assert.deepStrictEqual(pills.map((o) => o.stroke), ['rgba(63,182,255,0.45)', Card.C.line, Card.C.line]);
  const won = receipt({ settlement: { key: 'settle:x', seal: 'x' }, anchor: { day: '2026-10-22' } });
  list = Card.ops(ok(won, { settle: { current: { settle_state: 'correct' } }, quorum: { status: 'multi_anchored', groups: [[1], [2]] } }), QR, measure);
  const verdictRun = list.filter((o) => o.op === 'runs').flatMap((o) => o.runs).find((r) => r.text === 'Correct');
  assert.deepStrictEqual([verdictRun.color, verdictRun.bold], [Card.C.up, true]);
  assert.deepStrictEqual(list.filter((o) => o.op === 'pill').map((o) => o.done), [true, true, true]);
  list = Card.ops(ok(won, { settle: { current: { settle_state: 'wrong' } } }), QR, measure);
  assert.strictEqual(list.filter((o) => o.op === 'runs').flatMap((o) => o.runs).find((r) => r.text === 'Wrong').color, Card.C.down);
  const words = list.flatMap((o) => [o.text].concat((o.runs || []).map((r) => r.text))).filter(Boolean).join(' ');
  assert.ok(!/[$€£]|\bUSD\b|108000/.test(words), 'no money and no price on the card: ' + words);
  assert.ok(words.includes('RUNECLAW') && words.includes('DAILY DUEL RECEIPT') && words.includes('www.humanoid-traders.com'));
});

test('the QR: the receipt page on the public origin, with the spec\'s quiet zone on its tile', () => {
  assert.strictEqual(QR.url, 'https://www.humanoid-traders.com/call/duel%3A' + 'c'.repeat(64));
  assert.strictEqual(receiptUrl('https://x.example/', 'k1'), 'https://x.example/call/k1');
  assert.strictEqual(QR.rows.length, QR.size);
  assert.ok(QR.rows.every((r) => r.length === QR.size && /^[01]+$/.test(r)));
  // No margin in the matrix: each finder pattern starts at its corner.
  const finder = ['1111111', '1000001', '1011101', '1011101', '1011101', '1000001', '1111111'];
  const n = QR.size;
  for (let i = 0; i < 7; i += 1) {
    assert.strictEqual(QR.rows[i].slice(0, 7), finder[i]);
    assert.strictEqual(QR.rows[i].slice(n - 7), finder[i]);
    assert.strictEqual(QR.rows[n - 7 + i].slice(0, 7), finder[i]);
  }
  // Row by row, top row first, as the encoder laid it out at level M: a transposed matrix keeps its
  // three corners and scans as nothing.
  const lib = require('qrcode').create(QR.url, { errorCorrectionLevel: 'M' }).modules;
  assert.strictEqual(lib.size, n);
  for (let y = 0; y < n; y += 1) for (let x = 0; x < n; x += 1) assert.strictEqual(QR.rows[y][x], lib.data[y * n + x] ? '1' : '0', `${y},${x}`);
  const op = Card.ops(ok(receipt()), QR, measure).find((o) => o.op === 'qr');
  const side = op.cell * QR.size;
  assert.ok(op.cell >= 4, 'modules at least four pixels on the 1080-wide card');
  assert.ok(op.x - op.tile.x >= 4 * op.cell && op.tile.x + op.tile.w - (op.x + side) >= 4 * op.cell, 'four light modules each side');
  assert.ok(op.y - op.tile.y >= 4 * op.cell && op.tile.y + op.tile.h - (op.y + side) >= 4 * op.cell);
  assert.deepStrictEqual([op.tile.fill, op.color], [Card.C.tile, Card.C.bg], 'dark modules on a light tile');
  assert.ok(!Card.ops(ok(receipt()), { url: 'x', size: 0, rows: [] }, measure).some((o) => o.op === 'qr'), 'no matrix, no QR drawn');
});

test('the sentences fit the card in the longest language, and the seal\'s word is translated', () => {
  const words = { 'rc.sealed': 'Versiegelt am {time}, vor dem Ergebnis.', 'rc.seal': 'Siegel' };
  const list = Card.ops(ok(receipt(), { t: (k, en) => words[k] || en, lang: 'de' }), QR, measure);
  const runs = list.filter((o) => o.op === 'runs' && o.boldFont);
  for (const o of runs) {
    const w = o.runs.reduce((a, r) => a + measure(r.bold ? o.boldFont : o.font, r.text), 0);
    assert.ok(o.x + w <= Card.W - 88, `${w} wide at ${o.font}`);
  }
  assert.ok(Number(/(\d+)px/.exec(runs[0].font)[1]) < 34, 'the long line made the size smaller');
  assert.ok(list.some((o) => o.op === 'text' && /^Siegel [0-9a-f]{4} /.test(o.text)));
  const wide = Card.ops(ok(receipt({}, { ...PAY, category: 'mover', symbol: '1000000BABYDOGEUSDT', symbol_b: '1000000MOGCOINUSDT' })), QR, measure)
    .filter((o) => o.op === 'text' && /vs/.test(o.text))[0];
  assert.ok(measure(wide.font, wide.text) <= Card.W - 2 * 88, 'a long market is set smaller, not off the card');
  const narrow = (font, x) => Number(/(\d+)px/.exec(font)[1]) * 0.3 * String(x).length;
  const en = Card.ops(ok(receipt()), QR, narrow).filter((o) => o.op === 'runs' && o.boldFont);
  assert.strictEqual(en[0].font, '34px InterVar, Inter, system-ui, sans-serif', 'a line that fits keeps the full size');
});

test('every mark fits one row, in the longest labels the card is translated into', () => {
  const longest = { 'rc.st_sealed': 'ЗАПЕЧАТАНО', 'rc.st_settled': 'РАССЧИТАНО', 'rc.st_anchored': 'ЗАКРЕПЛЕНО' };
  for (const words of [longest, { 'rc.st_sealed': 'VERSIEGELT', 'rc.st_settled': 'ABGERECHNET', 'rc.st_anchored': 'VERANKERT' }]) {
    const list = Card.ops(ok(receipt(), { t: (k, en) => words[k] || en }), QR, measure);
    const pills = list.filter((o) => o.op === 'pill');
    assert.ok(pills[2].x + pills[2].w <= Card.W - 88, JSON.stringify(pills.map((p) => [p.x, p.w])));
  }
  // Labels too long for one row at full size (16 characters; the longest translation is 11) are
  // set smaller until they fit.
  const long = (k, en) => (/^rc\.st_/.test(k) ? 'X'.repeat(16) : en);
  const list = Card.ops(ok(receipt(), { t: long }), QR, measure);
  const pills = list.filter((o) => o.op === 'pill');
  assert.ok(pills[2].x + pills[2].w <= Card.W - 88, 'three long labels still fit');
  assert.ok(list.some((o) => o.op === 'text' && /X{16}/.test(o.text) && !/^24px/.test(o.font)), 'by a smaller size');
});

test('the route: our own receipt URL on the configured origin, never cached, a private origin refused', async () => {
  const app = express();
  app.use('/api/call', require('../routes/call'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  const get = (p, headers = {}) => new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: server.address().port, path: p, headers }, (res) => {
      let b = ''; res.on('data', (c) => { b += c; }); res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: JSON.parse(b) }));
    }).on('error', reject);
  });
  const saved = { PUBLIC_ORIGIN: process.env.PUBLIC_ORIGIN, APP_BASE_URL: process.env.APP_BASE_URL, WEBSITE_URL: process.env.WEBSITE_URL };
  try {
    process.env.PUBLIC_ORIGIN = 'https://www.humanoid-traders.com';
    const key = 'duel:' + 'c'.repeat(64);
    let r = await get('/api/call/' + encodeURIComponent(key) + '/qr', { Host: 'attacker.example' });
    assert.strictEqual(r.status, 200);
    assert.deepStrictEqual(r.body, QR, 'the configured origin, whatever Host the request carried');
    assert.strictEqual(r.headers['cache-control'], 'no-store');
    r = await get('/api/call/' + encodeURIComponent('bad key!') + '/qr');
    assert.strictEqual(r.status, 400);
    for (const k of Object.keys(saved)) delete process.env[k];
    r = await get('/api/call/' + encodeURIComponent(key) + '/qr', { Host: '10.0.0.5:8080' });
    assert.deepStrictEqual([r.status, r.body.error], [503, 'no_public_origin'], 'an origin nobody outside can reach is not encoded');
  } finally {
    for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
    server.close();
  }
});

test('the page offers a card only under a Duel call it found intact, at the versions the manifest records', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'asset_versions.json'), 'utf8'));
  for (const f of ['receipt-card.js', 'duel.js']) assert.ok(page.includes(`/js/${f}?v=${manifest['js/' + f].v}"`), f);
  const code = codeOnly(page);
  assert.match(code, /if \(intact && p\.kind === 'duel_pick' && d\.kind === 'duel_pick'\) \{\s*html \+= '<div class="rc-card"/);
  assert.match(code, /if \(intact && p\.kind === 'duel_pick'\) wireCard\(d, p\);/);
  assert.match(code, /sha256hex\(sr\.seal_payload\)\.then\(function \(hex\) \{ return C\.settleFrom\(sr, hex\); \}\)/, 'the settlement is hashed in the page');
});
