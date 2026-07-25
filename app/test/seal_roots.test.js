'use strict';
/**
 * Provable Calls v3 — daily seal roots. Every seal minted on a completed UTC
 * day folds into one Merkle root; receipts carry a membership proof the
 * browser replays; the roots feed is public so anyone can mirror it and hold
 * an independent timestamp. Roots are immutable once computed; today's day
 * is never committed early; empty days are omitted, never invented.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const { pool } = require('../db');
const { canonicalLeaves, merkleRoot, merkleProof, verifyProof, sha256hex } = require('../lib/sealroot');
const { sealCall } = require('../lib/callseal');
const roots = require('../lib/seal_roots');

const H = (s) => sha256hex(s);   // convenient 64-hex leaves from short names

let server, base;
test.before(async () => {
  const app = express();
  app.use('/api/call', require('../routes/call'));
  app.use('/api/roots', require('../routes/roots'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function get(p) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${p}`, (res) => {
      let d = ''; res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    }).on('error', reject);
  });
}

test('merkle math: canonical leaves, deterministic root, proofs for every member', () => {
  // Junk and duplicates never become leaves.
  assert.deepEqual(canonicalLeaves([H('b'), 'nope', H('a'), H('b'), null]), [H('a'), H('b')].sort());
  assert.equal(merkleRoot([]), null, 'no seals → no root, never an invented one');
  assert.equal(merkleRoot([H('x')]), H('x'), 'single leaf IS the root');
  // Order independence: shuffled input, same root.
  const seals = ['a', 'b', 'c', 'd', 'e'].map(H);
  const shuffled = [seals[3], seals[0], seals[4], seals[2], seals[1]];
  assert.equal(merkleRoot(seals), merkleRoot(shuffled));
  // Every member of every set size 1..7 proves membership; tampering fails.
  for (let n = 1; n <= 7; n++) {
    const set = Array.from({ length: n }, (_, i) => H('leaf' + i));
    const root = merkleRoot(set);
    for (const s of set) {
      const proof = merkleProof(set, s);
      assert.ok(verifyProof(s, proof, root), `member verifies (n=${n})`);
      assert.ok(!verifyProof(H('intruder'), proof, root), 'a different seal fails the same proof');
    }
    assert.equal(merkleProof(set, H('intruder')), null, 'non-members get no proof');
  }
  // A doctored root is caught.
  const set3 = ['p', 'q', 'r'].map(H);
  assert.ok(!verifyProof(set3[0], merkleProof(set3, set3[0]), H('fake-root')));
});

const yesterday = new Date(Date.now() - 24 * 3600 * 1000);
const yDay = roots.dayOf(yesterday);
let expectedLeaves;

test('a completed day folds all sealed surfaces into ONE immutable root', async () => {
  // Seed: two sealed signals yesterday, one sealed arena position yesterday,
  // one closed arena trade carrying ANOTHER seal from yesterday, one sealed
  // signal TODAY (must stay out), and one legacy unsealed row (ignored).
  const sig = (key) => {
    const s = { signal_key: key, symbol: 'BTCUSDT', direction: 'LONG', entry_price: 100,
      stop_loss: 95, take_profit: 110, confidence: 0.7, pattern: null, regime: null,
      created_at: yesterday.toISOString() };
    return { ...s, ...sealCall(s) };
  };
  const s1 = sig('sig:root:1'), s2 = sig('sig:root:2'), s3 = sig('sig:root:today');
  pool.signals.push(
    { ...s1, id: 9001, status: 'NEW', pnl: null, sealed_at: yesterday },
    { ...s2, id: 9002, status: 'NEW', pnl: null, sealed_at: yesterday },
    { ...s3, id: 9003, status: 'NEW', pnl: null, sealed_at: new Date() },
    { signal_key: 'sig:root:legacy', id: 9004, status: 'NEW', pnl: null, seal: null, created_at: yesterday });
  const posSeal = H('arena-open-seal');
  const tradeSeal = H('arena-closed-seal');
  pool.arenaPositions.push({ id: 9101, user_id: 91, symbol: 'ETHUSDT', direction: 'LONG',
    entry: 100, margin: 50, leverage: 3, trade_key: 'arena:aaaaaaaaaaaaaaaaaa',
    seal: posSeal, seal_payload: '{}', sealed_at: yesterday, opened_at: yesterday });
  pool.arenaTrades.push({ id: 9102, user_id: 91, symbol: 'SOLUSDT', direction: 'SHORT',
    entry: 100, exit_price: 90, margin: 50, leverage: 3, pnl: 15, reason: 'tp',
    trade_key: 'arena:bbbbbbbbbbbbbbbbbb', seal: tradeSeal, seal_payload: '{}',
    sealed_at: yesterday, opened_at: yesterday, closed_at: new Date() });

  expectedLeaves = canonicalLeaves([s1.seal, s2.seal, posSeal, tradeSeal]);
  const row = await roots.rootForDay(yDay);
  assert.ok(row, 'the completed day has a root');
  assert.equal(row.seal_count, 4, 'signals + arena position + arena trade; today and legacy excluded');
  assert.equal(row.root, merkleRoot(expectedLeaves));
  // Immutable: asking again returns the SAME stored row even if data changed.
  pool.signals.push({ ...sig('sig:root:late'), id: 9005, status: 'NEW', pnl: null, sealed_at: yesterday });
  const again = await roots.rootForDay(yDay);
  assert.equal(again.root, row.root, 'a computed root never moves');
  assert.equal(await roots.rootForDay(roots.dayOf(Date.now())), null, "today's day is still open — no early commitment");
  assert.equal(await roots.rootForDay('junk'), null);
});

test('/api/roots publishes the mirrorable feed with its construction contract', async () => {
  const r = await get('/api/roots');
  assert.equal(r.status, 200);
  assert.match(r.data.construction, /sha256\(utf8\(leftHex\+rightHex\)\)/);
  const row = r.data.roots.find((x) => x.day === yDay);
  assert.ok(row, 'the seeded day is in the feed');
  assert.equal(row.root, merkleRoot(expectedLeaves));
});

test('receipts from completed days carry a proof that reaches the day root', async () => {
  const r = await get('/api/call/' + encodeURIComponent('sig:root:1'));
  assert.equal(r.status, 200);
  const a = r.data.anchor;
  assert.ok(a && a.day === yDay && Array.isArray(a.proof), 'anchor served');
  assert.ok(verifyProof(r.data.seal, a.proof, a.root), 'the browser-replayed chain reaches the root');
  assert.equal(a.root, merkleRoot(expectedLeaves), 'anchored to the immutable stored root');
  // A seal minted TODAY has no anchor yet — the day is open, stated honestly.
  const today = await get('/api/call/' + encodeURIComponent('sig:root:today'));
  assert.equal(today.data.anchor, null);
  // A seal back-inserted into an already-published day gets NO anchor: proofs
  // come from the committed leaf set only — absence is the honest signal.
  const late = await get('/api/call/' + encodeURIComponent('sig:root:late'));
  assert.equal(late.data.anchor, null, 'no valid-looking proof for a backfilled row');
});

test('wiring: the verify page replays proofs and links the roots feed', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  assert.match(page, /appendAnchor/);
  assert.match(page, /ANCHOR MISMATCH/);
  assert.match(page, /\/api\/roots/);
  assert.match(page, /Daily seal roots \(live\)/);
  assert.match(page, /href="\/roots"/);
  const server_ = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_, /app\.use\('\/api\/roots'/);
});

test('wiring: the human-readable /roots mirror page', () => {
  const server_ = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_, /app\.get\('\/roots'/);
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'roots.html'), 'utf8');
  assert.match(page, /Daily Seal Roots/);
  assert.match(page, /fetch\('\/api\/roots'/);
  assert.match(page, /data-root=/, 'click-to-copy carries the hash');
  assert.match(page, /Honest ledgers start empty/, 'the empty state never invents a root');
  assert.match(page, /promoted unchanged/, 'the construction is documented for mirrors');
  const idx = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(idx, /href="\/roots">Seal Roots</, 'the front door links the trust surface');
});

test('wiring: /provable — the third-party verification contract page', () => {
  const server_ = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_, /app\.get\('\/provable'/);
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'provable.html'), 'utf8');
  // Both canonical payload contracts documented, field order included.
  assert.match(page, /"v":1,"signal_key"/);
  assert.match(page, /"v":2,"kind":"arena_trade"/);
  // Copy-paste verification: single-call hash and Merkle proof replay.
  assert.match(page, /hashlib\.sha256\(d\["seal_payload"\]/);
  assert.match(page, /d\["anchor"\]\["root"\]/);
  // Honest scope stays on the page.
  assert.match(page, /none of\s+it is investment advice/i);
  // The verify surfaces link the contract.
  const call = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  assert.match(call, /href="\/provable"/);
  const roots = fs.readFileSync(path.join(__dirname, '..', 'public', 'roots.html'), 'utf8');
  assert.match(roots, /href="\/provable"/);
});

test('landing: the Provable Calls trust section is real, linked, and honest', () => {
  const idx = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(idx, /id="provableTease"/);
  assert.match(idx, /Don't trust the screenshot\. Verify the call\./);
  // All three trust surfaces reachable from the front door.
  for (const href of ['/provable', '/roots', '/proof']) {
    assert.ok(idx.includes(`href="${href}"`), `landing must link ${href}`);
  }
  // The root strip shows a REAL root or nothing — it ships hidden and is only
  // revealed when the public feed actually returns one (no invented history).
  assert.match(idx, /id="rootStrip" hidden/);
  assert.match(idx, /if \(!row \|\| !row\.root\) return;/);
  assert.match(idx, /rootStrip'\)\.hidden = false/);
  // The bolded claim renders as markup in every language (textContent would
  // print literal tags), and the strings are dictionary-backed.
  assert.match(idx, /data-i18n-html="sec\.prov_p"/);
  const i18n = require('../public/js/i18n');
  for (const k of ['sec.prov_eyebrow', 'sec.prov_h', 'sec.prov_p', 'sec.prov_mirror', 'sec.prov_root_head']) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
  }
  assert.match(i18n.STRINGS['sec.prov_root_head'].es, /\{day\}[\s\S]*\{n\}/, 'placeholders survive translation');
});
