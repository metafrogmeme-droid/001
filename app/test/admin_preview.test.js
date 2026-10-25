'use strict';
/**
 * Admin preview. Public flag on: everyone. Public flag off: today's answer
 * for a non-admin, and the feature for a user whose plan is admin
 * (lib/operator_view.js isOperator). No second allowlist.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.RWA_PRECHECK_ENABLED;
delete process.env.CLAW_GUARD_V01;
delete process.env.SCAM_OR_SAFE_V0;
delete process.env.DUEL_CONFIDENCE_ENABLED;
delete process.env.DUEL_CHIPS_ENABLED;
delete process.env.AVALANCHE_ANCHOR_ENABLED;
delete process.env.SAR_ORACLE_ENABLED;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const preview = require('../lib/admin_preview');
const { pool } = require('../db');
const sos = require('../lib/scam_or_safe');
const gate = require('../lib/daily_duel_gate');
const svc = require('../lib/duel_service');
const chips = require('../lib/duel_chips');
const { setTickerFetcher } = require('../lib/tickers');

const ADMIN_ID = 91001;
const USER_ID = 91002;
pool.users.push({ id: ADMIN_ID, email: 'preview-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'preview-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'preview-admin@example.com');
const userToken = tokenFor(USER_ID, 'preview-user@example.com');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function hit(server, path, { token, method, body } = {}) {
  const headers = {};
  if (token) headers.authorization = 'Bearer ' + token;
  let payload;
  if (body != null) {
    payload = JSON.stringify(body);
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(`http://127.0.0.1:${server.address().port}${path}`, {
    method: method || 'GET', headers, body: payload,
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (e) { parsed = raw; }
  return { status: res.status, raw, body: parsed, cache: res.headers.get('cache-control') };
}

test('an unknown flag is off, and the public flag does not ask who the caller is', async () => {
  assert.equal(await preview.flag_on_for(null, 'NOT_A_PRODUCT_FLAG'), false);
  assert.equal(preview.publicOn('NOT_A_PRODUCT_FLAG'), false);
  process.env.RWA_PRECHECK_ENABLED = 'true';
  try {
    assert.equal(await preview.flag_on_for(null, 'RWA_PRECHECK_ENABLED'), true);
    const flags = await preview.previewFlags({ user: { user_id: ADMIN_ID } });
    assert.equal(flags.includes('RWA_PRECHECK_ENABLED'), false);
    assert.equal(flags.includes('CLAW_GUARD_V01'), true);
    assert.deepEqual(await preview.previewFlags(null), []);
  } finally {
    delete process.env.RWA_PRECHECK_ENABLED;
  }
});

test('RWA, Claw Guard, and Scam or Safe: off hides, an admin previews, on is public', async () => {
  const app = express();
  app.use(express.json());
  const rwa = require('../lib/rwa_flag');
  app.use(rwa.pageGate);
  app.get('/rwa', (req, res) => { rwa.sendPage(req, res); });
  app.use('/v1/rwa', require('../routes/rwa_precheck'));
  app.get('/guard', (req, res) => { require('../lib/claw_guard_v01').sendPage(req, res); });
  app.use('/api/guard/poison', require('../routes/guard_v01'));
  app.use('/v1/receipts', require('../routes/receipts_v01'));
  app.use('/scam', require('../routes/scam_page'));
  app.use('/api/scam-or-safe', require('../routes/scam_or_safe'));
  const server = await listen(app);
  const poison = {
    destination: '0x' + 'ab'.repeat(2) + '22'.repeat(16) + 'cd'.repeat(2),
    counterparties: ['0x' + 'ab'.repeat(2) + '11'.repeat(16) + 'cd'.repeat(2)],
    chain_id: 8453,
  };
  try {
    const rwaOff = await hit(server, '/rwa');
    assert.equal(rwaOff.status, 404);
    const rwaUser = await hit(server, '/rwa', { token: userToken });
    assert.equal(rwaUser.status, 404);
    const rwaAdmin = await hit(server, '/rwa', { token: adminToken });
    assert.equal(rwaAdmin.status, 200);
    assert.ok(rwaAdmin.raw.includes(preview.BANNER));
    assert.equal(rwaAdmin.cache, 'private, no-store');
    const chainsUser = await hit(server, '/v1/rwa/chains', { token: userToken });
    assert.equal(chainsUser.status, 404);
    const chainsAdmin = await hit(server, '/v1/rwa/chains', { token: adminToken });
    assert.equal(chainsAdmin.status, 200);
    assert.ok(Array.isArray(chainsAdmin.body.chains));

    const guardUser = await hit(server, '/guard', { token: userToken });
    assert.equal(guardUser.raw.includes('/js/guard-v01.js'), false);
    const guardAdmin = await hit(server, '/guard', { token: adminToken });
    assert.ok(guardAdmin.raw.includes('/js/guard-v01.js'));
    assert.ok(guardAdmin.raw.includes(preview.BANNER));
    const poisonUser = await hit(server, '/api/guard/poison', { token: userToken, method: 'POST', body: poison });
    assert.equal(poisonUser.status, 404);
    assert.equal(poisonUser.body.error, 'Not found');
    const poisonAdmin = await hit(server, '/api/guard/poison', { token: adminToken, method: 'POST', body: poison });
    assert.equal(poisonAdmin.status, 200);
    const receiptUser = await hit(server, '/v1/receipts/nope?format=aat', { token: userToken });
    assert.equal(receiptUser.body.error, 'Not found');
    const receiptAdmin = await hit(server, '/v1/receipts/nope?format=aat', { token: adminToken });
    assert.notEqual(receiptAdmin.body.error, 'Not found');

    const scamUser = await hit(server, '/scam', { token: userToken });
    assert.equal(scamUser.status, 404);
    assert.match(scamUser.raw, /not open/);
    const scamAdmin = await hit(server, '/scam', { token: adminToken });
    assert.equal(scamAdmin.status, 200);
    assert.ok(scamAdmin.raw.includes(preview.BANNER));
    assert.equal((scamAdmin.raw.match(/<script\b(?![^>]*\bsrc=)/g) || []).length, 0);
    assert.doesNotMatch(scamAdmin.raw, /(^|[{,\s])['"`]?margin['"`]?\s*:/);
    const apiUser = await hit(server, '/api/scam-or-safe/today', { token: userToken });
    assert.equal(apiUser.status, 404);
    assert.equal(apiUser.body.error, 'This game is not open.');
    const apiAdmin = await hit(server, '/api/scam-or-safe/today', { token: adminToken });
    assert.equal(apiAdmin.status, 200);
    assert.equal(apiAdmin.body.ok, true);
    assert.equal(apiAdmin.raw.includes('caller_is_admin'), false);

    process.env.RWA_PRECHECK_ENABLED = 'true';
    process.env.CLAW_GUARD_V01 = '1';
    process.env.SCAM_OR_SAFE_V0 = 'yes';
    const rwaPublic = await hit(server, '/rwa');
    assert.equal(rwaPublic.status, 200);
    assert.equal(rwaPublic.raw.includes(preview.BANNER), false);
    const guardPublic = await hit(server, '/guard');
    assert.ok(guardPublic.raw.includes('/js/guard-v01.js'));
    assert.equal(guardPublic.raw.includes(preview.BANNER), false);
    const scamPublic = await hit(server, '/scam');
    assert.equal(scamPublic.status, 200);
    assert.equal(scamPublic.raw.includes(preview.BANNER), false);
    const poisonPublic = await hit(server, '/api/guard/poison', { method: 'POST', body: poison });
    assert.equal(poisonPublic.status, 200);
  } finally {
    delete process.env.RWA_PRECHECK_ENABLED;
    delete process.env.CLAW_GUARD_V01;
    delete process.env.SCAM_OR_SAFE_V0;
    await close(server);
  }
});

test('the seal drops caller_is_admin, and a non-admin is still off', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-preview-'));
  process.env.SCAM_OR_SAFE_STATE_DIR = dir;
  const off = sos.offer({ now: '2026-10-23T12:00:00.000Z', player_key: 'ab'.repeat(32) });
  assert.equal(off.error, 'off');
  const opened = sos.offer({
    now: '2026-10-23T12:00:00.000Z', player_key: 'ab'.repeat(32), caller_is_admin: true,
  });
  assert.equal(opened.ok, true);
  assert.equal(opened.verdict, undefined);
  const sealed = sos.sealPick({
    now: '2026-10-23T12:00:00.000Z', player_key: 'cd'.repeat(32),
    pick: 'safe', caller_is_admin: true,
  });
  assert.equal(sealed.ok, true);
  assert.equal(JSON.parse(sealed.seal_payload).caller_is_admin, undefined);

  const rated = await gate.sealCall({
    now: '2026-10-23T12:00:00.000Z', pick: 'up', confidence: 'high',
    marks: { ETHUSDT: { price: 50, volume: 1 } }, caller_is_admin: true,
  });
  assert.equal(rated.ok, true);
  const body = JSON.parse(rated.seal_payload);
  assert.equal(body.confidence, 'high');
  assert.equal(body.caller_is_admin, undefined);
  const dropped = await gate.sealCall({
    now: '2026-10-23T12:00:00.000Z', pick: 'down', confidence: 'high',
    marks: { ETHUSDT: { price: 50, volume: 1 } },
  });
  assert.equal(JSON.parse(dropped.seal_payload).confidence, undefined);
  const rules = gate.rules();
  assert.equal(rules.confidence, false);
  delete process.env.SCAM_OR_SAFE_STATE_DIR;
});

test('confidence and chips preview for an admin, and the shared round stays undealt', async () => {
  delete process.env.DUEL_CONFIDENCE_ENABLED;
  delete process.env.DUEL_CHIPS_ENABLED;
  const prices = {
    BTCUSDT: { price: 60000, volume: 900 }, ETHUSDT: { price: 3000, volume: 800 },
    SOLUSDT: { price: 150, volume: 700 }, BNBUSDT: { price: 600, volume: 600 },
    XRPUSDT: { price: 0.5, volume: 500 }, ARBUSDT: { price: 1.2, volume: 50 },
  };
  setTickerFetcher(async () => prices);
  const now = new Date('2026-10-23T09:30:00.000Z');
  try {
    const rounds = await svc.ensureRounds('2026-10-23', now);
    assert.ok(rounds.every((r) => r.odds == null));
    const stranger = await svc.cardFor(USER_ID, now);
    assert.ok(!('confidence' in stranger));
    assert.equal(stranger.chips, null);
    assert.ok(!stranger.admin_preview);
    assert.ok(stranger.rounds.every((r) => !('odds' in r)));
    const admin = await svc.cardFor(ADMIN_ID, now);
    assert.equal(admin.confidence, true);
    assert.equal(admin.admin_preview, true);
    assert.equal(admin.chips.enabled, true);
    const direction = admin.rounds.find((r) => r.kind === 'direction');
    assert.deepEqual(direction.odds, chips.oddsFor('direction').odds);
    assert.equal(chips.oddsFor('sports').odds, null);
    const stored = await svc.loadRoundsForDay('2026-10-23');
    assert.ok(stored.every((r) => r.odds == null));

    const refused = await svc.placePick(USER_ID, direction.id, 'long', now, { chips: 40, confidence: 'high' });
    assert.equal(refused.reason, 'chips_off');
    const plain = await svc.placePick(USER_ID, direction.id, 'long', now, { confidence: 'high' });
    assert.equal(plain.ok, true);
    assert.equal(JSON.parse(pool.duelPicks.find((p) => p.seal === plain.seal).seal_payload).confidence, undefined);

    const put = await svc.placePick(ADMIN_ID, admin.rounds.find((r) => r.kind === 'range').id, 'hold', now, {
      chips: 40, confidence: 'low',
    });
    assert.equal(put.ok, true);
    assert.equal(put.admin_preview, true);
    const payload = JSON.parse(pool.duelPicks.find((p) => p.seal === put.seal).seal_payload);
    assert.equal(payload.confidence, 'low');
    assert.equal(payload.chips, 40);
    const after = await svc.loadRoundsForDay('2026-10-23');
    assert.ok(after.every((r) => r.odds == null));

    process.env.DUEL_CONFIDENCE_ENABLED = '1';
    process.env.DUEL_CHIPS_ENABLED = 'yes';
    const everyone = await svc.cardFor(USER_ID, now);
    assert.equal(everyone.confidence, true);
    assert.ok(!everyone.admin_preview);
  } finally {
    setTickerFetcher(null);
    delete process.env.DUEL_CONFIDENCE_ENABLED;
    delete process.env.DUEL_CHIPS_ENABLED;
  }
});

test('Avalanche is listed for an admin and no signable mainnet plan is issued', async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/roots', require('../routes/roots'));
  const server = await listen(app);
  try {
    const anon = await hit(server, '/api/roots/chains');
    assert.equal(anon.body.chains.some((c) => c.key === 'avalanche'), false);
    const user = await hit(server, '/api/roots/chains', { token: userToken });
    assert.equal(user.body.chains.some((c) => c.key === 'avalanche'), false);
    const admin = await hit(server, '/api/roots/chains', { token: adminToken });
    const avax = admin.body.chains.find((c) => c.key === 'avalanche');
    assert.equal(avax.admin_preview, true);
    assert.equal(avax.signing, false);
    assert.equal(avax.testnet, false);
    assert.equal(admin.cache, 'private, no-store');
    const again = await hit(server, '/api/roots/chains');
    assert.equal(again.body.chains.some((c) => c.key === 'avalanche'), false);
    assert.equal(again.cache, 'public, max-age=300');

    const planUser = await hit(server, '/api/roots/anchor-plan/2026-10-22?chain=avalanche', { token: userToken });
    assert.equal(planUser.status, 400);
    assert.equal(planUser.body.error, 'Unknown chain');
    const planAdmin = await hit(server, '/api/roots/anchor-plan/2026-10-22?chain=avalanche', { token: adminToken });
    assert.equal(planAdmin.status, 403);
    assert.equal(planAdmin.body.admin_preview, true);
    assert.equal(planAdmin.body.data, undefined);
    assert.equal(JSON.stringify(planAdmin.body).includes('0x'), false);
    const postAdmin = await hit(server, '/api/roots/anchor', {
      token: adminToken, method: 'POST', body: { day: '2026-10-22', chain: 'avalanche', tx_hash: 'abc' },
    });
    assert.equal(postAdmin.status, 403);
    assert.equal(JSON.stringify(postAdmin.body).includes('0x'), false);

    process.env.AVALANCHE_ANCHOR_ENABLED = 'on';
    const pub = await hit(server, '/api/roots/chains');
    const listed = pub.body.chains.find((c) => c.key === 'avalanche');
    assert.ok(listed);
    assert.equal(listed.admin_preview, undefined);
    assert.equal(pub.cache, 'public, max-age=300');
  } finally {
    delete process.env.AVALANCHE_ANCHOR_ENABLED;
    await close(server);
  }
});

test('SaR stays off in public, and an admin previews the tools page and the admin routes', async () => {
  delete process.env.SAR_ORACLE_ENABLED;
  const app = express();
  const sarFlag = require('../lib/sar_oracle_flag');
  const sarRouter = require('../routes/sar_oracle');
  let seam = 0;
  sarRouter.setOperatorCheckForTests(() => { seam += 1; return true; });
  app.use(sarFlag.pageGate);
  app.use(express.static(path.join(__dirname, '..', 'public')));
  app.get('/tools', (req, res) => { sarFlag.sendPage(req, res); });
  app.use('/api/sar-oracle', sarRouter);
  const server = await listen(app);
  try {
    const anonPage = await hit(server, '/tools');
    assert.equal(anonPage.status, 404);
    assert.equal(anonPage.raw.includes(preview.BANNER), false);
    const userPage = await hit(server, '/tools', { token: userToken });
    assert.equal(userPage.status, 404);
    const anonHtml = await hit(server, '/tools.html');
    assert.equal(anonHtml.status, 404);
    const anonScript = await hit(server, '/js/tools-page.js');
    assert.equal(anonScript.status, 404);
    const anonFeed = await hit(server, '/api/sar-oracle/packets');
    assert.equal(anonFeed.status, 404);
    const adminFeed = await hit(server, '/api/sar-oracle/packets', { token: adminToken });
    assert.equal(adminFeed.status, 404);
    assert.equal(adminFeed.raw.includes('_usd_'), false);
    const userAdmin = await hit(server, '/api/sar-oracle/admin/packets', { token: userToken });
    assert.equal(userAdmin.status, 404);
    assert.equal(userAdmin.raw.includes('_usd_'), false);
    assert.equal(seam, 0);

    const adminPage = await hit(server, '/tools', { token: adminToken });
    assert.equal(adminPage.status, 200);
    assert.ok(adminPage.raw.includes(preview.BANNER));
    assert.match(adminPage.raw, /href="\/api\/sar-oracle\/admin\/packets"/);
    assert.match(adminPage.raw, /Admin JSON API/);
    assert.equal(adminPage.raw.includes('_usd_'), false);
    assert.equal(adminPage.raw.includes('$'), false);
    assert.equal(adminPage.cache, 'private, no-store');
    const adminHtml = await hit(server, '/tools.html', { token: adminToken });
    assert.equal(adminHtml.status, 200);
    assert.ok(adminHtml.raw.includes(preview.BANNER));
    const adminScript = await hit(server, '/js/tools-page.js', { token: adminToken });
    assert.equal(adminScript.status, 200);
    assert.match(adminScript.raw, /visibleIds/);

    const adminPkt = await hit(server, '/api/sar-oracle/admin/packets', { token: adminToken });
    assert.equal(adminPkt.status, 200);
    assert.equal(adminPkt.cache, 'private, no-store');
    assert.equal(adminPkt.body.markets[0].operator_amounts.oi_usd_e6, null);
    const userAdopt = await hit(server, '/api/sar-oracle/admin/adoption', { token: userToken });
    assert.equal(userAdopt.status, 404);
    const adminAdopt = await hit(server, '/api/sar-oracle/admin/adoption', { token: adminToken });
    assert.equal(adminAdopt.status, 200);
    assert.equal(adminAdopt.raw.includes('_usd_'), false);
    assert.equal(adminAdopt.body.alerts.status, 'not_read');
    assert.equal(adminAdopt.body.replay.status, 'not_read');
    assert.equal(adminAdopt.body.replay.reason, 'dataset_absent');
    assert.equal(adminAdopt.body.hip3.signs, false);
    assert.equal(adminAdopt.body.hip3.submits, false);
    assert.equal(seam, 0);

    sarRouter.setOperatorCheckForTests(null);
    process.env.SAR_ORACLE_ENABLED = '1';
    const publicPage = await hit(server, '/tools');
    assert.equal(publicPage.status, 404);
    const openPage = await hit(server, '/tools', { token: adminToken });
    assert.equal(openPage.status, 200);
    assert.equal(openPage.raw.includes(preview.BANNER), false);
    assert.match(openPage.raw, /Admin JSON API/);
    assert.equal(openPage.cache, 'no-store');
    const openFeed = await hit(server, '/api/sar-oracle/packets');
    assert.equal(openFeed.status, 200);
    assert.equal(openFeed.raw.includes('_usd_'), false);
    const closedAdmin = await hit(server, '/api/sar-oracle/admin/packets');
    assert.equal(closedAdmin.status, 404);
    const openAdmin = await hit(server, '/api/sar-oracle/admin/packets', { token: adminToken });
    assert.equal(openAdmin.status, 200);
    assert.equal(openAdmin.cache, 'no-store');
  } finally {
    sarRouter.setOperatorCheckForTests(null);
    delete process.env.SAR_ORACLE_ENABLED;
    await close(server);
  }
});

test('the Pi page stays unchanged unless the caller is previewing', async () => {
  const pi = require('../routes/piapp');
  const plain = pi._page('en', {});
  assert.equal(plain.includes(preview.BANNER), false);
  assert.equal(plain.includes('scam-or-safe.js'), false);
  const shown = pi._page('en', {}, { scam: true, banner: true });
  assert.ok(shown.includes(preview.BANNER));
  assert.ok(shown.includes('/js/scam-or-safe.js?v=2'));
  assert.equal((shown.match(/<script\b(?![^>]*\bsrc=)/g) || []).length, 0);
});
