'use strict';
/**
 * The /guard page draws the route's answer (public/js/guard-page.js render),
 * and claims nothing the answer does not.
 *
 * Colour is a claim: red for 'stop', amber for 'check', and 'clear' muted,
 * never green, because no warning in what could be read is not a measurement
 * that a request is safe. A check that could not run is said; it is never an
 * empty verdict. Every sentence goes through the translator by a stable key
 * and falls back to the English whole.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const P = require('../public/js/guard-page.js');
const G = require('../public/js/guard-model.js');
const X = require('../public/js/txray-model.js');
const SIM = require('../public/js/txsim-model.js');

const fixture = (name) => JSON.parse(fs.readFileSync(
  path.join(__dirname, 'fixtures', 'claw_guard', `sim_${name}.json`), 'utf8'));

/** What the route answers for a fixture, built from the same models. */
function answer(name, opts) {
  const f = fixture(name);
  const sim = SIM.read(f.answer, f.tx.from);
  const v = G.verdict(X.decodeTx({ data: f.tx.data || '0x', value: f.tx.value || '0' }), sim);
  return { request: 'tx', chain_id: 8453, ...v,
    simulation: { ran: true, status: sim.status, block: sim.block, other_events: sim.other_events },
    receipt: null, ...(opts || {}) };
}

/** A translator that marks every key it was asked for. */
function marking() {
  const asked = [];
  const T = (k, en) => { asked.push(k); return `«${k}»`; };
  return { T, asked };
}

test('stop is red, check amber, and clear is muted, never green', () => {
  assert.match(P.render(answer('unlimited_approve')), /class="verdict v-danger"/);
  assert.match(P.render(answer('reverted')), /class="verdict v-caution"/);
  const clear = P.render(answer('token_send'));
  assert.match(clear, /class="verdict v-muted"/);
  assert.doesNotMatch(clear, /v-clear|--up|✅/, 'a clear verdict drew a green claim');
});

test('a check that could not run says so and draws no verdict', () => {
  for (const body of [null, undefined, {}, { level: 7 }]) {
    const html = P.render(body);
    assert.match(html, /could not run just now/);
    assert.doesNotMatch(html, /class="verdict/);
  }
});

test('every sentence is asked for by its key', () => {
  const { T, asked } = marking();
  P.render(answer('unlimited_approve'), T);
  for (const k of ['grd.v_stop', 'grd.r_unlimited_approval', 'grd.reasons_h', 'txr.a_approve', 'grd.actions_h',
    'grd.changes_h', 'grd.c_approval', 'grd.sim_at', 'grd.signin']) {
    assert.ok(asked.includes(k), `${k} was not asked for`);
  }
});

test('the simulation line names the chain and the block; none, and a failed one, are said apart', () => {
  const ran = P.render(answer('token_send'));
  assert.match(ran, new RegExp(`Simulated on Base against block ${fixture('token_send').answer.result[0].number ? '\\d+' : ''}`));
  const none = P.render({ ...answer('token_send'), simulation: null, changes: [] });
  assert.match(none, /Not simulated: add your address/);
  assert.doesNotMatch(none, /What changes for your account/, 'no simulation, no changes heading');
  const failed = P.render({ ...answer('token_send'), simulation: { ran: false, reason: 'unreachable' } });
  assert.match(failed, /could not run: Base.s endpoints did not answer/);
  // A signature is never simulated, and says nothing about it.
  assert.doesNotMatch(P.render({ ...answer('token_send'), request: 'typed_data', simulation: null, reasons: [], changes: [] }), /simulat/i);
});

test('an empty change list is a sentence, not a blank', () => {
  const html = P.render({ ...answer('token_send'), changes: [] });
  assert.match(html, /nothing leaving or arriving/);
});

test('a receipt is a link to its verify path, and without one the page says how to get one', () => {
  const sealed = P.render(answer('token_send', { receipt: { verify: '/call/sc_abc', scan_key: 'sc_abc' } }));
  assert.match(sealed, /<a href="\/call\/sc_abc">Open the receipt<\/a>/);
  assert.match(P.render(answer('token_send')), /Sign in to get a sealed receipt/);
});

test('deadlines and expiries are dates or words, never raw seconds or sentinels', () => {
  assert.strictEqual(P.when('NEVER'), 'no end');
  assert.strictEqual(P.when('BLOCK'), 'the block it is used in');
  assert.strictEqual(P.when('1792800000'), '2026-10-24 00:00 UTC');
  assert.strictEqual(P.when('nonsense'), 'nonsense');
  const html = P.render({ request: 'typed_data', level: 'check', headline: 'x', reasons: [], simulation: null, changes: [],
    actions: [{ tid: 'sgr.a_permit', en: 'Until {deadline}, {amount}.', params: { deadline: 'NEVER', amount: 'UNLIMITED', unlimited: true } }] });
  assert.match(html, /Until no end, UNLIMITED\./);
});

test('what the page sends is built per mode, and an empty form sends nothing', () => {
  assert.strictEqual(P.requestFor('tx', {}), null);
  assert.deepStrictEqual(P.requestFor('tx', { from: '0xa', to: '0xb', data: '0x01', value: '5', chain: '84532' }),
    { request: 'tx', tx: { to: '0xb', data: '0x01', from: '0xa', value: '5' }, chain_id: 84532 });
  assert.deepStrictEqual(P.requestFor('sig', { text: '{"a":1}' }), { request: 'typed_data', typed_data: { a: 1 }, chain_id: 8453 });
  assert.deepStrictEqual(P.requestFor('auth', { text: 'not json' }), { request: 'authorization', authorization: 'not json', chain_id: 8453 });
  assert.strictEqual(P.requestFor('sig', { text: '   ' }), null);
});

test('what the reader pasted is escaped, never drawn as markup', () => {
  const html = P.render({ request: 'typed_data', level: 'unknown', headline: 'x', reasons: [], simulation: null, changes: [],
    actions: [{ tid: 'sgr.a_unknown', en: 'Signs a {type} message', params: { type: '<img src=x onerror=1>' } }] });
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

test('the page loads its script, has no inline handler, and says what leaves it', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'guard.html'), 'utf8');
  assert.match(html, /<script src="\/js\/guard-page\.js\?v=\d+"><\/script>/);
  assert.doesNotMatch(html, /\son[a-z]+=/i, 'an inline event handler');
  assert.match(html, /data-i18n="grd\.privacy">What leaves this page/);
});
