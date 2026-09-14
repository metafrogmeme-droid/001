'use strict';
/**
 * The swap page's failed-parse path printed a MARKET VERDICT.
 *
 * `swap-page.js` parsed the planner's reply with `catch (_) { data = null; }`
 * and then, on a 200 with no `build`, printed "The plan did not pass —
 * nothing was built." in the warn voice. A 200 whose body did not parse — a
 * proxy interstitial, a truncated body — took exactly that path: a statement
 * about the route's viability, manufactured from a read that failed, five
 * lines under the comment saying an unreachable planner is not an empty plan,
 * and `routes/meme.js` names this exact rendering as the shape the repo guards
 * against. The page has its own copy of the fetch wrapper because it does not
 * load app.js; the copy kept the parse and lost the outcome.
 *
 * `SwapSignModel.readBuildReply` is the reading now — pure, driven here — and
 * the page only paints what it answers.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');
const { readBuildReply } = require('../public/js/swap-sign-model');

const PAGE = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'swap-page.js'), 'utf8'));

test('a 2xx whose body did not parse is a failed read, never "the plan did not pass"', () => {
  for (const r of [{ ok: true, data: null, unreadable: true },
                   { ok: true, data: null, unreadable: false },   // the JSON literal null is not a plan reply
                   { ok: true, data: 'edge html', unreadable: false },
                   { ok: true, data: 42, unreadable: false }]) {
    const v = readBuildReply(r);
    assert.strictEqual(v.kind, 'unreadable', JSON.stringify(r));
    assert.strictEqual(v.tone, 'err');
    assert.strictEqual(v.build, null);
    assert.ok(!/did not pass/.test(v.text), `a verdict from a failed read: ${v.text}`);
    assert.match(v.text, /could not be read/);
    assert.match(v.text, /fault on our side/);
  }
});

test('a readable reply with no build is the planner\'s own verdict, in its own words when it gave them', () => {
  assert.deepStrictEqual(readBuildReply({ ok: true, data: { reason: 'No route at this size.' }, unreadable: false }),
    { kind: 'no_plan', tone: 'warn', build: null, human: '', text: 'No route at this size.' });
  const bare = readBuildReply({ ok: true, data: { human: 'Buy 1 BONK' }, unreadable: false });
  assert.strictEqual(bare.kind, 'no_plan');
  assert.strictEqual(bare.human, 'Buy 1 BONK');
  assert.match(bare.text, /did not pass/);
});

test('a build is a plan, and a refusal keeps the server\'s sentence', () => {
  const plan = readBuildReply({ ok: true, data: { build: { intent_id: 'i1' }, human: 'Buy 1 BONK' }, unreadable: false });
  assert.deepStrictEqual(plan, { kind: 'plan', tone: '', build: { intent_id: 'i1' }, human: 'Buy 1 BONK', text: '' });
  assert.deepStrictEqual(readBuildReply({ ok: false, status: 503, data: { error: 'build_unavailable', detail: 'Could not reach the planner.' }, unreadable: false }),
    { kind: 'refused', tone: 'err', build: null, human: '', text: 'Could not reach the planner.' });
  // A refusal whose body did not parse is still a refusal, with the generic sentence.
  const junk = readBuildReply({ ok: false, status: 502, data: null, unreadable: true });
  assert.strictEqual(junk.kind, 'refused');
  assert.match(junk.text, /nothing was built or signed/);
});

test('the page records the parse outcome and paints only what the model answers', () => {
  assert.match(PAGE, /catch \(_\) \{ unreadable = true; \}/, 'the parse outcome is dropped again');
  assert.match(PAGE, /M\.readBuildReply\(\{ ok: r\.ok, status: r\.status, data, unreadable \}\)/, 'the page decides for itself');
  assert.ok(!/did not pass/.test(PAGE), 'the verdict sentence is back in the page');
  assert.ok(!/data && data\.build/.test(PAGE), 'the page reads build off the raw payload again');
  assert.match(PAGE, /build = reply\.build;/);
  assert.match(PAGE, /setStatus\(reply\.text, reply\.tone\);/);
});
