'use strict';
/**
 * Market Clock. Admin-only. Flag off is 404 for anyone who is not an
 * operator. Times are UTC and Europe/Amsterdam. A docs convention is not
 * an issuer activationDateTime. No price field is served.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.MARKET_CLOCK_ENABLED;
delete process.env.MARKET_CLOCK_ACTIVATION_TOLERANCE_MIN;
delete process.env.MARKET_CLOCK_HALT_TOLERANCE_MIN;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/market_clock');
const flag = require('../lib/market_clock_flag');
const view = require('../lib/market_clock/view');

const ADMIN_ID = 95041;
const USER_ID = 95042;
pool.users.push({ id: ADMIN_ID, email: 'clock-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'clock-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'clock-admin@example.com');
const userToken = tokenFor(USER_ID, 'clock-user@example.com');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function hit(server, url, { bearer } = {}) {
  const headers = {};
  if (bearer) headers.authorization = 'Bearer ' + bearer;
  const res = await fetch('http://127.0.0.1:' + server.address().port + url, { headers });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed, cache: res.headers.get('cache-control') };
}

function app() {
  const server = express();
  server.use('/api/market-clock', route);
  return server;
}

function clockBody() {
  return view.calendar(undefined, {});
}

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ MARKET_CLOCK_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ MARKET_CLOCK_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ MARKET_CLOCK_ENABLED: '0' }), false);
  assert.equal(flag.flagOn({ MARKET_CLOCK_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ MARKET_CLOCK_ENABLED: 'TRUE' }), true);
  assert.equal(flag.FLAG, 'MARKET_CLOCK_ENABLED');
});

test('the route is authenticated and mounted', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'market_clock.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /accessFor/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|new Wallet\(|eth_sendRawTransaction/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/market-clock/);
  const preview = fs.readFileSync(path.join(__dirname, '..', 'lib', 'admin_preview.js'), 'utf8');
  assert.match(preview, /MARKET_CLOCK_ENABLED/);
});

test('flag off and flag on: a non-operator gets 404, an operator can read', async () => {
  delete process.env.MARKET_CLOCK_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, '/api/market-clock');
    assert.equal(anon.status, 404);
    const user = await hit(server, '/api/market-clock', { bearer: userToken });
    assert.equal(user.status, 404);
    const preview = await hit(server, '/api/market-clock', { bearer: adminToken });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.preview, true);
    assert.match(preview.body.banner, /Admin preview/);
    assert.equal(preview.cache, 'private, no-store');
    assert.equal(preview.body.product, 'market_clock');
    process.env.MARKET_CLOCK_ENABLED = '1';
    const stillOut = await hit(server, '/api/market-clock', { bearer: userToken });
    assert.equal(stillOut.status, 404);
    const open = await hit(server, '/api/market-clock', { bearer: adminToken });
    assert.equal(open.status, 200);
    assert.equal(open.body.preview, false);
    assert.equal(open.body.banner, null);
    assert.equal(view.priceKeyFound(open.body, '$'), null);
  } finally {
    delete process.env.MARKET_CLOCK_ENABLED;
    await close(server);
  }
});

test('issuer activationDateTime varies, and a convention is not that time', () => {
  const body = clockBody();
  assert.equal(body.readable, true);
  const aapl = body.events.filter((e) => e.event_id.indexOf('xstocks:AAPLx:') === 0);
  assert.equal(aapl.length, 4);
  const clocks = aapl.map((e) => e.resolved_value);
  assert.deepEqual(clocks.sort(), [
    '2025-11-13T23:55:00.000Z',
    '2026-02-12T23:55:00.000Z',
    '2026-05-09T02:30:00.000Z',
    '2026-08-08T00:30:00.000Z',
  ]);
  const clockOfDay = new Set(clocks.map((iso) => iso.slice(11, 16)));
  assert.equal(clockOfDay.size, 3);
  for (const event of aapl) {
    assert.equal(event.status, 'single_source');
    assert.equal(event.conflict, false);
    assert.equal(event.delta_s, null);
    assert.equal(event.action.kind, null);
    assert.equal(event.event_id.includes(':div'), false);
    assert.equal(event.venue_windows[0].absent_rank1.present, false);
    assert.equal(event.venue_windows[0].source_rank, 1);
  }
  const aug = aapl.find((e) => e.event_id === 'xstocks:AAPLx:2026-08-08');
  assert.equal(aug.venue_windows[0].time.amsterdam.clock, '02:30');
  assert.equal(aug.venue_windows[0].time.amsterdam.offset, 'GMT+02:00');
  const feb = aapl.find((e) => e.event_id === 'xstocks:AAPLx:2026-02-12');
  assert.equal(feb.venue_windows[0].time.amsterdam.date, '2026-02-13');
  assert.equal(feb.venue_windows[0].time.amsterdam.clock, '00:55');
  assert.equal(feb.venue_windows[0].time.amsterdam.offset, 'GMT+01:00');

  const pending = body.pending.find((row) => row.id === 'xstocks-tslax-pending');
  assert.equal(pending.activationDateTime, null);
  assert.equal(pending.activation_pending, false);
  assert.equal(JSON.stringify(pending).includes('"activationDateTime":0'), false);

  const convention = body.convention;
  assert.equal(convention.status, 'provisional');
  assert.equal(convention.conflict, true);
  assert.equal(convention.resolved_value, null);
  assert.equal(convention.selected_source, null);
  assert.equal(convention.alert_at_utc, null);
  assert.equal(convention.claims.length, 3);
  const stated = convention.claims.map((c) => c.stated).join('\n');
  assert.match(stated, /00:30 UTC/);
  assert.match(stated, /00:00 UTC/);
  assert.match(stated, /8:00 PM EST/);
  const kraken = convention.claims.find((c) => c.also_stated);
  assert.match(kraken.also_stated, /20:00 US Eastern/);
  assert.equal(kraken.normalizations[0].utc_clock, '01:00');
  assert.equal(kraken.normalizations[0].day_anchor, 'ex_date');
  assert.equal(kraken.normalizations[0].offset_label, 'EST');
  assert.equal(kraken.normalizations[1].utc_clock, '00:00');
  assert.equal(kraken.normalizations[1].day_anchor, 'ex_date');
  assert.equal(kraken.normalizations[1].offset_label, 'EDT');
  assert.equal(kraken.selected, null);
  const xstocks = convention.claims.find((c) => c.stated.indexOf('00:30 UTC') === 0);
  assert.equal(xstocks.amsterdam.standard.clock, '01:30');
  assert.equal(xstocks.amsterdam.standard.offset, 'GMT+01:00');
  assert.equal(xstocks.amsterdam.daylight.clock, '02:30');
  assert.equal(xstocks.amsterdam.daylight.offset, 'GMT+02:00');
});

test('SKHYNIX is 28 Jul 08:00 KST, which is 27 Jul 23:00 UTC', () => {
  const body = clockBody();
  const event = body.events.find((e) => e.event_id === 'tradexyz:xyz:SKHYNIX:2026-07-28:session_switch');
  assert.equal(event.status, 'confirmed');
  assert.equal(event.resolved_value, '2026-07-27T23:00:00.000Z');
  assert.equal(event.delta_s, 0);
  const window = event.venue_windows[0];
  assert.equal(window.start_utc, '2026-07-27T23:00:00.000Z');
  assert.equal(window.time.stated_clock, '08:00');
  assert.equal(window.time.stated_zone, 'KST');
  assert.equal(window.time.amsterdam.date, '2026-07-28');
  assert.equal(window.time.amsterdam.clock, '01:00');
  assert.equal(window.time.amsterdam.offset, 'GMT+02:00');
  assert.equal(window.thin_source, true);
  assert.equal(window.treatment, 'oracle_regime_switch');
  const regime = event.alerts.find((a) => a.type === 'regime_switch');
  assert.equal(regime.at_utc, '2026-07-27T22:45:00.000Z');
  assert.equal(regime.opt_in, true);
  assert.equal(regime.default_enabled, false);
  assert.match(event.cited_loss.quoted_as, /\$57-80M/);
  assert.match(event.cited_loss.quoted_as, /\$57\.4M/);
  assert.match(event.cited_loss.quoted_as, /Grafa/);
  assert.equal(event.cited_loss.single_source, true);
  assert.equal(event.cited_loss.status, 'single_source');
  assert.equal(event.cover.announced, true);
  assert.equal(event.cover.announced_on, '2026-07-29');
  assert.equal(event.cover.payout_completed, null);
  assert.notEqual(event.cover.payout_completed, false);
});

test('Kioxia is a 3-for-1 halt, and the old 15 minute pause is not stated', () => {
  const body = clockBody();
  const event = body.events.find((e) => e.event_id === 'tradexyz:Kioxia:2026-09-28:split');
  assert.equal(event.action.ratio, '3:1');
  assert.match(event.venue_windows[0].rules[0], /3-for-1/);
  assert.equal(event.venue_windows[0].start_utc, '2026-09-28T06:35:00.000Z');
  assert.equal(event.venue_windows[0].end_utc, '2026-09-28T07:30:00.000Z');
  assert.equal(event.venue_windows[0].end_time.amsterdam.clock, '09:30');
  assert.equal(event.venue_windows[0].time.amsterdam.clock, '08:35');
  assert.equal(event.venue_windows[0].time.amsterdam.offset, 'GMT+02:00');
  const leads = event.alerts.filter((a) => a.type === 'event_window' && a.lead_minutes > 0).map((a) => a.lead_minutes);
  assert.deepEqual(leads, [1440, 60, 5]);
  assert.equal(event.alerts.find((a) => a.phase === 'window_open').at_utc, '2026-09-28T06:35:00.000Z');
  const pause = body.events.find((e) => e.event_id === 'xstocks:mechanics:chains')
    .venue_windows.find((w) => w.treatment === 'pause_all');
  assert.equal(pause.minutes, null);
  assert.equal(pause.start_utc, null);
  assert.match(pause.rules.join(' '), /brief window/);
  assert.equal(JSON.stringify(body).includes('1-for-3'), false);
  assert.equal(JSON.stringify(body).toLowerCase().includes('either side'), false);
});

test('tolerances default to 15 and 5 minutes and a bad value is not zero', () => {
  const defaults = view.tolerances({});
  assert.equal(defaults.activation_minutes, 15);
  assert.equal(defaults.halt_minutes, 5);
  assert.equal(defaults.activation_source, 'default');
  assert.equal(defaults.halt_source, 'default');
  const set = view.tolerances({
    MARKET_CLOCK_ACTIVATION_TOLERANCE_MIN: '30',
    MARKET_CLOCK_HALT_TOLERANCE_MIN: '2',
  });
  assert.equal(set.activation_minutes, 30);
  assert.equal(set.halt_minutes, 2);
  assert.equal(set.activation_source, 'env');
  const bad = view.tolerances({ MARKET_CLOCK_ACTIVATION_TOLERANCE_MIN: 'nope' });
  assert.equal(bad.activation_readable, false);
  assert.equal(bad.activation_minutes, null);
  assert.equal(bad.readable, false);
  const body = view.calendar(undefined, { MARKET_CLOCK_HALT_TOLERANCE_MIN: 'soon' });
  assert.equal(body.readable, false);
  assert.equal(body.reason, 'tolerance_unreadable');
  assert.equal(body.tolerances.halt_minutes, null);
});

test('a conflict alerts at the earliest instant and does not pick a winner', () => {
  const far = view.resolveField([
    { rank: 1, utc: '2026-08-08T02:00:00Z', stated: 'rank 1' },
    { rank: 2, utc: '2026-08-08T00:30:00Z', stated: 'rank 2' },
    { rank: 2, utc: '2026-08-08T00:15:00Z', stated: 'rank 2 early' },
  ], 15 * 60);
  assert.equal(far.status, 'conflict');
  assert.equal(far.conflict, true);
  assert.equal(far.resolved_value, '2026-08-08T02:00:00Z');
  assert.equal(far.alert_at_utc, '2026-08-08T00:15:00.000Z');
  assert.equal(far.delta_s, 5400);
  assert.equal(far.selected_source, null);

  const near = view.resolveField([
    { rank: 1, utc: '2026-08-08T00:30:00Z', stated: 'a' },
    { rank: 2, utc: '2026-08-08T00:40:00Z', stated: 'b' },
  ], 15 * 60);
  assert.equal(near.status, 'confirmed');
  assert.equal(near.conflict, false);
  assert.equal(near.alert_at_utc, null);
  assert.equal(near.delta_s, 600);

  const one = view.resolveField([
    { rank: 1, utc: '2026-08-08T00:30:00Z', stated: 'only' },
  ], 15 * 60);
  assert.equal(one.status, 'single_source');
  assert.equal(one.delta_s, null);

  const agreed = view.resolveField([
    { rank: 2, stated: 'same rule', utc: null },
    { rank: 2, stated: 'same rule', utc: null },
  ], 15 * 60);
  assert.equal(agreed.status, 'confirmed');
  assert.equal(agreed.resolved_value, null);
  assert.equal(agreed.delta_s, null);
  assert.equal(agreed.alert_at_utc, null);

  const unlike = view.resolveField([
    { rank: 2, stated: 'one wording', utc: null },
    { rank: 2, stated: 'another wording', utc: null },
  ], 15 * 60);
  assert.equal(unlike.status, 'single_source');
  assert.equal(unlike.alert_at_utc, null);

  const halt = view.resolveField([
    { rank: 2, utc: '2026-09-28T06:35:00Z', stated: 'a' },
    { rank: 2, utc: '2026-09-28T06:50:00Z', stated: 'b' },
  ], 5 * 60);
  assert.equal(halt.status, 'conflict');
  assert.equal(halt.alert_at_utc, '2026-09-28T06:35:00.000Z');

  const stale = view.applyStale(
    { status: 'single_source' },
    '2020-01-01T00:00:00Z',
    24,
    Date.parse('2026-10-25T00:00:00Z'),
  );
  assert.equal(stale.status, 'stale');
  const fresh = view.applyStale(
    { status: 'single_source' },
    null,
    null,
    Date.parse('2026-10-25T00:00:00Z'),
  );
  assert.equal(fresh.status, 'single_source');
  assert.equal(fresh.stale_check, 'max_age_unset');
});

test('the calendar has the four alert types and no price or verdict', () => {
  const body = clockBody();
  assert.deepEqual(body.alert_types.map((a) => a.type), [
    'event_window', 'regime_switch', 'lockout', 'conflict',
  ]);
  assert.equal(body.ranks.find((r) => r.rank === 3).present, false);
  assert.equal(view.priceKeyFound(body, '$'), null);
  const dumped = JSON.stringify(body);
  assert.equal(dumped.includes('1128'), false);
  assert.equal(dumped.includes('917'), false);
  assert.equal(dumped.includes('1-for-3'), false);
  assert.equal(/\b(buy|sell|scam|safe)\b/i.test(dumped), false);
  assert.match(body.notice, /AI-written/);
  assert.match(body.notice, /Information, not advice/);
  assert.equal(body.refetched, false);
  assert.equal(body.seal, null);
  const jup = body.events.find((e) => e.event_id === 'jupiter:corporate-action-pause');
  assert.match(jup.venue_windows[0].rules[0], /repay/);
  assert.match(jup.venue_windows[0].rules[0], /add-collateral/);
  assert.equal(jup.alerts[0].at_utc, null);
  assert.equal(jup.alerts[0].type, 'lockout');
  const locked = body.events.find((e) => e.event_id === 'jupiter:oracle-locked');
  assert.equal(locked.venue_windows[0].start_utc, null);
  assert.match(locked.venue_windows[0].rules[0], /weekends and holidays/);
  const age = body.events.find((e) => e.event_id === 'jupiter:oracle-age');
  assert.equal(age.venue_windows[0].time.older_than_minutes, 10);
  assert.equal(age.venue_windows[0].start_utc, null);
  const gate = body.events.find((e) => e.event_id === 'gate:2025-11-25:nflx-tqqq');
  assert.equal(gate.venue_windows[0].start_utc, null);
  assert.equal(gate.venue_windows[0].time.kind, 'calendar_date');
});

test('Bitget rToken notices are official help pages, with status and no invented clock', () => {
  const body = clockBody();
  const ids = [
    'bitget:rtoken:cash-dividend',
    'bitget:rtoken:split',
    'bitget:rtoken:stock-dividend',
    'bitget:rtoken:session',
    'bitget:rtoken:session-pause',
    'bitget:rtoken:weekend-open',
    'bitget:rtoken:weekend-orders',
    'bitget:perps:corporate-action',
    'bitget:elite:copy-corporate-action',
  ];
  const rows = ids.map((id) => body.events.find((e) => e.event_id === id));
  assert.equal(rows.every(Boolean), true);

  const cash = rows[0];
  assert.equal(cash.status, 'confirmed');
  assert.equal(cash.resolved_value, null);
  assert.equal(cash.delta_s, null);
  assert.equal(cash.venue_windows[0].start_utc, null);
  assert.equal(cash.claims.length, 2);
  assert.match(cash.claims[0].source.url, /^https:\/\/www\.bitget\.com\/support\/articles\//);
  assert.equal(cash.claims[0].source.checked_on, '2026-10-25');
  assert.match(cash.venue_windows[0].rules.join(' '), /withholding rate is not confirmed/);
  assert.match(cash.claims[0].also_stated, /not recorded as the rate/);

  const split = rows[1];
  assert.equal(split.status, 'confirmed');
  assert.equal(split.venue_windows[0].start_utc, null);

  const shareDiv = rows[2];
  assert.equal(shareDiv.status, 'single_source');
  assert.equal(shareDiv.conflict, false);
  assert.match(shareDiv.venue_windows[0].rules.join(' '), /neither is selected/);

  const session = rows[3];
  assert.equal(session.status, 'confirmed');
  assert.equal(session.venue_windows[0].start_utc, null);
  assert.equal(session.venue_windows[0].treatment, 'session_hours');

  const pause = rows[4];
  assert.equal(pause.status, 'single_source');
  assert.equal(pause.venue_windows[0].minutes, null);
  assert.match(pause.venue_windows[0].rules.join(' '), /does not give a minute count/);

  const weekend = rows[5];
  assert.equal(weekend.status, 'single_source');
  assert.equal(weekend.conflict, false);
  assert.equal(weekend.resolved_value, null);
  assert.equal(weekend.alert_at_utc, null);
  const readings = weekend.venue_windows[0].time.readings;
  assert.equal(readings[0].weekday, 'saturday');
  assert.equal(readings[0].utc_clock, '00:00');
  assert.equal(readings[0].day_shift, 0);
  assert.equal(readings[0].amsterdam.standard.clock, '01:00');
  assert.equal(readings[0].amsterdam.daylight.clock, '02:00');
  assert.equal(readings[1].utc_clock, '01:00');
  assert.equal(readings[1].day_shift, 0);
  assert.equal(readings[1].amsterdam.standard.clock, '02:00');
  assert.equal(readings[1].amsterdam.daylight.clock, '03:00');
  assert.match(weekend.venue_windows[0].rules.join(' '), /single-source/);

  const orders = rows[6];
  assert.equal(orders.status, 'confirmed');
  assert.equal(orders.venue_windows[0].orders_effect, 'cancelled');
  assert.equal(orders.venue_windows[0].start_utc, null);

  const perps = rows[7];
  assert.equal(perps.status, 'single_source');
  assert.equal(perps.venue_windows[0].start_utc, null);
  assert.equal(perps.venue_windows[0].orders_effect, 'cancelled');
  assert.equal(perps.venue_windows[1].time.minutes, 5);
  assert.equal(perps.venue_windows[1].time.approximate, true);
  assert.equal(perps.venue_windows[1].start_utc, null);
  assert.equal(perps.alerts.length, 0);
  const perpText = perps.venue_windows.map((w) => w.rules.join(' ')).join(' ');
  assert.equal(perpText.includes('250'), false);
  assert.match(perpText, /one third/);
  assert.match(perpText, /five minutes before/);

  const elite = rows[8];
  assert.equal(elite.status, 'single_source');
  assert.equal(elite.venue_windows[0].start_utc, null);
  assert.match(elite.venue_windows[0].rules.join(' '), /spot elite/);

  const dumped = JSON.stringify(rows);
  assert.equal(/\b(buy|sell|scam|safe)\b/i.test(dumped), false);
  assert.equal(dumped.includes('1-for-3'), false);
  assert.equal(view.priceKeyFound({ events: rows }, '$'), null);
});

test('a symbol query returns that symbol and does not invent a clock for an unknown one', () => {
  const hitSk = view.calendar('xyz:SKHYNIX', {});
  assert.deepEqual(hitSk.symbol_event_ids, ['tradexyz:xyz:SKHYNIX:2026-07-28:session_switch']);
  assert.equal(hitSk.symbol_note, null);
  const miss = view.calendar('NOPE', {});
  assert.deepEqual(miss.symbol_event_ids, []);
  assert.match(miss.symbol_note, /not a measurement/);
  assert.equal(miss.events.length > 4, true);
});
