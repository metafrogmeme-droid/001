'use strict';
/**
 * The web half of venue selection.
 *
 * `routes/controls.js` already carries the scar this file is written against:
 * pause-to-paper stored a preference, acked it, and the website showed the user
 * as paused while every confirmed trade still went to the exchange. Believing
 * your trades are simulated when they are real is the worst direction for that
 * to fail in — and believing your book is spread across two venues when every
 * order goes to one is the same shape with the same cost.
 *
 * So: PROPOSED and APPLIED are two fields, never one, and the column that
 * carries a proposal has three states rather than two.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { parseSelection, serializeSelection, deserializeSelection, MAX_VENUES } =
  require('../lib/venue_selection_wire');


// ── the three states ─────────────────────────────────────────────────────

test('null and empty are DIFFERENT states, on the way out and back', () => {
  // null  = "no venue change proposed"
  // ''    = "clear my selection"
  // Collapsing them drops somebody's selection every time they change an
  // unrelated control, and the only symptom is their book concentrating.
  assert.strictEqual(deserializeSelection(null), null);
  assert.strictEqual(deserializeSelection(undefined), null);
  assert.deepStrictEqual(deserializeSelection(''), []);
  assert.strictEqual(serializeSelection([]), '', 'an empty selection became null');
});

test('a round trip preserves the selection', () => {
  const p = parseSelection(['bitget', 'bybit']);
  assert.ok(p.ok);
  assert.deepStrictEqual(deserializeSelection(serializeSelection(p.venues)),
    ['bitget', 'bybit']);
});


// ── parsing refuses rather than filtering ────────────────────────────────

test('an unsupported venue is REFUSED, not quietly dropped', () => {
  // Dropping it and saving the rest answers a request nobody made: the user
  // asked to trade on A and B, and silently storing only A leaves them
  // believing B is live.
  const r = parseSelection(['bitget', 'notavenue']);
  assert.equal(r.ok, false);
  assert.match(r.error, /notavenue/);
});

test('duplicates collapse and case is normalised', () => {
  const r = parseSelection([' BITGET ', 'bitget', 'Bybit']);
  assert.deepStrictEqual(r.venues, ['bitget', 'bybit']);
});

test('a comma string is accepted as well as a list', () => {
  assert.deepStrictEqual(parseSelection('bitget, bybit').venues, ['bitget', 'bybit']);
});

test('an empty selection is valid — it means single venue', () => {
  const r = parseSelection([]);
  assert.ok(r.ok);
  assert.deepStrictEqual(r.venues, []);
});

test('a missing field is refused rather than read as "clear"', () => {
  assert.equal(parseSelection(undefined).ok, false);
  assert.equal(parseSelection(null).ok, false);
});

test('a non-list is refused', () => {
  assert.equal(parseSelection({ bitget: true }).ok, false);
  assert.equal(parseSelection(42).ok, false);
});

test('there is a bound on how many venues one person can select', () => {
  const many = Array.from({ length: MAX_VENUES + 1 }, () => 'bitget');
  // Duplicates collapse, so build a genuinely long list of distinct junk.
  assert.equal(parseSelection(many).ok, true, 'duplicates should collapse first');
  const long = Array.from({ length: MAX_VENUES + 1 }, (_, i) => `venue${i}`);
  assert.equal(parseSelection(long).ok, false);
});


// ── proposed is not applied ──────────────────────────────────────────────

test('the status route reports applied and pending venues SEPARATELY', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'controls.js'), 'utf8');
  assert.match(src, /venues:\s*deserializeSelection\(c\.venues\)/,
    'applied venues are not read from user_controls (the bot\'s ack)');
  assert.match(src, /venues_pending:\s*deserializeSelection\(p\.venues\)/,
    'a proposal in flight is not distinguishable from an applied selection — '
    + 'the user sees a tick for venues the bot has never been told about');
});

test('the venues route answers pending, never live', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'controls.js'), 'utf8');
  const i = src.indexOf("router.post('/venues'");
  assert.ok(i > 0, '/venues route is missing');
  const body = src.slice(i, src.indexOf('router.post', i + 10));
  assert.match(body, /pending:\s*true/,
    'the route reports the selection as applied before the bot has seen it');
});

test('the venues route does not clobber other pending controls', () => {
  // The main controls UPSERT writes every column from VALUES. Folding venues
  // into it would mean changing a margin cap silently discards a venue
  // proposal made a moment earlier, and vice versa.
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'controls.js'), 'utf8');
  const i = src.indexOf("router.post('/venues'");
  const body = src.slice(i, src.indexOf('router.post', i + 10));
  assert.match(body, /INSERT INTO pending_controls \(user_id, telegram_id, venues\)/,
    'the venues write touches columns it has no business touching');
  assert.ok(!/live_enabled\s*=\s*VALUES/.test(body),
    'the venues write overwrites the live-enabled proposal');
});

test('you cannot select a venue you have not connected', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'controls.js'), 'utf8');
  const i = src.indexOf("router.post('/venues'");
  const body = src.slice(i, src.indexOf('router.post', i + 10));
  assert.match(body, /FROM exchange_status WHERE user_id = \? AND connected = 1/,
    'the route does not check which venues are actually connected');
  assert.match(body, /connect these before selecting them/);
});


// ── the channel carries it ───────────────────────────────────────────────

test('the sync channel serves and accepts the venues field', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  assert.match(src, /SELECT user_id, telegram_id, live_enabled, max_margin, paused, venues/,
    'the bot can never pull the venue selection');
  assert.match(src, /venues = VALUES\(venues\)/,
    'the bot ack does not write the applied venues back');
});

test('an ack that omits venues writes NULL, not an empty selection', () => {
  // An older bot that does not send the field has said NOTHING about venues.
  // Writing '' would claim it had cleared the user's selection.
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  assert.match(src, /\(a\.venues === null \|\| a\.venues === undefined\) \? null : String\(a\.venues\)/,
    'an omitted venues field is not distinguished from a cleared selection');
});

test('both tables can hold the column on an existing deployment', () => {
  const db = fs.readFileSync(path.join(__dirname, '..', 'db.js'), 'utf8');
  assert.match(db, /ALTER TABLE pending_controls ADD COLUMN venues/);
  assert.match(db, /ALTER TABLE user_controls ADD COLUMN venues/);
});
