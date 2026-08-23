'use strict';
/**
 * What the venue picker TELLS a user, with state planted underneath it.
 *
 * The web twin of tests/test_venue_card.py, against the same red herring: the
 * selection itself. Two ticked venues is a true fact about what somebody saved
 * and a lie about what the bot is doing, whenever the mode is off, shadow, or
 * unknown. `routes/controls.js` already carries that scar for pause-to-paper —
 * the site said "paused" while confirmed trades reached the exchange.
 *
 * Four states have to stay distinguishable and every test here is one of them:
 * proposed, applied, in force, and not-yet-reported.
 */

const test = require('node:test');
const assert = require('node:assert');

const { pickerState } = require('../public/js/venue-picker-model');

const CONN = [{ venue: 'bitget', connected: true }, { venue: 'bybit', connected: true }];


// ── the mode outranks the ticks ──────────────────────────────────────────

test('a saved selection with the mode OFF says the book is not spread', () => {
  const s = pickerState({ venues: ['bitget', 'bybit'], venues_pending: null,
    venues_mode: 'off' }, CONN);
  assert.equal(s.notice.tone, 'warn');
  assert.match(s.notice.text, /NOT spread/);
  assert.match(s.notice.text, /single default venue/);
});

test('shadow says nothing is spread yet', () => {
  const s = pickerState({ venues: ['bitget', 'bybit'], venues_pending: null,
    venues_mode: 'shadow' }, CONN);
  assert.equal(s.notice.tone, 'warn');
  assert.match(s.notice.text, /Nothing is spread yet/);
});

test('enforce says orders are routed AND that one trade goes to one venue', () => {
  const s = pickerState({ venues: ['bitget', 'bybit'], venues_pending: null,
    venues_mode: 'enforce' }, CONN);
  assert.equal(s.notice.tone, 'ok');
  assert.match(s.notice.text, /ONE of them/,
    'a reader could take "routed across two venues" as the trade being placed on both');
});

test('an unreported mode is NOT rendered as off', () => {
  // null means the bot has not acked. Calling that "off" is a confident claim
  // about a control nobody has reported on.
  const s = pickerState({ venues: ['bitget'], venues_pending: null,
    venues_mode: null }, CONN);
  assert.equal(s.notice.tone, 'unknown');
  assert.match(s.notice.text, /Waiting for the bot/);
  assert.ok(!/is OFF/.test(s.notice.text));
});


// ── proposed is not applied ──────────────────────────────────────────────

test('a pending change says the old venues are still the live ones', () => {
  const s = pickerState({ venues: ['bitget'], venues_pending: ['bitget', 'bybit'],
    venues_mode: 'enforce' }, CONN);
  assert.equal(s.dirty, true);
  assert.equal(s.notice.tone, 'pending');
  assert.match(s.notice.text, /still use the venues shown before this change/);
});

test('the checkboxes show the PROPOSAL while one is in flight', () => {
  // Showing the applied set while a change is queued makes the user think
  // their click was lost, and click again.
  const s = pickerState({ venues: ['bitget'], venues_pending: ['bitget', 'bybit'],
    venues_mode: 'enforce' }, CONN);
  assert.deepStrictEqual(s.rows.filter((r) => r.checked).map((r) => r.venue),
    ['bitget', 'bybit']);
});

test('a pending CLEAR is not the same as no pending change', () => {
  // [] in flight means "turn multi-venue off"; null means nothing queued.
  // Collapsing them shows a queued clear as though nothing had been asked for.
  const cleared = pickerState({ venues: ['bitget', 'bybit'], venues_pending: [],
    venues_mode: 'enforce' }, CONN);
  assert.equal(cleared.dirty, true);
  assert.equal(cleared.rows.filter((r) => r.checked).length, 0);

  const nothing = pickerState({ venues: ['bitget', 'bybit'], venues_pending: null,
    venues_mode: 'enforce' }, CONN);
  assert.equal(nothing.dirty, false);
});


// ── a venue that stopped being connected ─────────────────────────────────

test('a selected venue that is no longer connected still appears, flagged', () => {
  // Dropping it from the list reads as "I unticked that". The user cannot tell
  // that from "my keys stopped working", and only one of those is their doing.
  const s = pickerState({ venues: ['bitget', 'okx'], venues_pending: null,
    venues_mode: 'enforce' }, CONN);
  const okx = s.rows.find((r) => r.venue === 'okx');
  assert.ok(okx, 'a selected-but-disconnected venue vanished from the picker');
  assert.equal(okx.disconnected, true);
  assert.equal(okx.checked, true);
});

test('an unconnected venue nobody selected is not offered', () => {
  const s = pickerState({ venues: [], venues_pending: null, venues_mode: 'off' },
    [{ venue: 'bitget', connected: true }, { venue: 'okx', connected: false }]);
  assert.deepStrictEqual(s.rows.map((r) => r.venue), ['bitget']);
});


// ── shape ────────────────────────────────────────────────────────────────

test('no selection says single venue rather than looking broken', () => {
  const s = pickerState({ venues: [], venues_pending: null, venues_mode: 'enforce' },
    CONN);
  assert.equal(s.notice.tone, 'info');
  assert.match(s.notice.text, /single default venue/);
});

test('nothing connected disables saving instead of offering a button that fails', () => {
  const s = pickerState({ venues: [], venues_pending: null, venues_mode: 'off' }, []);
  assert.equal(s.canSave, false);
  assert.deepStrictEqual(s.rows, []);
});

test('the row order does not depend on the order venues arrive in', () => {
  const a = pickerState({ venues: [], venues_pending: null, venues_mode: 'off' },
    CONN).rows.map((r) => r.venue);
  const b = pickerState({ venues: [], venues_pending: null, venues_mode: 'off' },
    [...CONN].reverse()).rows.map((r) => r.venue);
  assert.deepStrictEqual(a, b);
});

test('a missing payload does not throw', () => {
  const s = pickerState(undefined, undefined);
  assert.deepStrictEqual(s.rows, []);
  assert.equal(s.canSave, false);
});


// ── it is reachable ──────────────────────────────────────────────────────

test('the dashboard renders the picker and ships the model that drives it', () => {
  // #999: a picker built and never reached renders zero times in production
  // while every test above passes. Both halves are needed — a caller, and a
  // script tag that defines what it calls.
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

  assert.match(dash, /venuePickerHtml\(c,/, 'the picker is defined and never called');
  assert.match(dash, /VenuePickerModel/, 'the panel does not use the model');
  assert.match(dash, /id === 'venSave'/, 'nothing saves the selection');
  assert.match(dash, /'\/api\/controls\/venues'/, 'the save does not reach the route');
  assert.match(html, /venue-picker-model\.js\?v=\d+/,
    'the browser never loads the model, so the picker renders nothing');

  const v = html.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(v && Number(v[1]) >= 151,
    'dashboard.js changed but its cache-buster did not — browsers keep the old one');
});

test('a missing model renders nothing rather than a broken picker', () => {
  // `if (!m) return ''`. Claiming nothing beats claiming wrongly: a picker
  // drawn without its model would show unticked boxes for venues the user has
  // actually selected.
  const fs = require('node:fs');
  const path = require('node:path');
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const i = dash.indexOf('function venuePickerHtml');
  assert.match(dash.slice(i, i + 300), /if \(!m\) return ''/);
});
