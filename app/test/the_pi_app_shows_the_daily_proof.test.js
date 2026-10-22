'use strict';
/**
 * The daily proof in the Pi app (public/js/pi-proof.js): read-only, the
 * latest finished days with each day's Anchor Quorum badge.
 *
 *   the badge     three values as the quorum has them (held, short, unknown)
 *                 and a fourth of the page's own (not read), which claims
 *                 nothing; a chain whose record does not match is named; only
 *                 "held" takes a colour
 *   the block     an unreadable feed is said to be unreadable, never "no
 *                 days"; badges are asked one after another, never seven at
 *                 once; a settled (held) day is not asked again on Refresh,
 *                 an unsettled one is; nothing links anywhere
 *   the real app  the header's Proof button opens it (smoke test beside this)
 */
const test = require('node:test');
const assert = require('node:assert');

global.window = global.window || {};
global.document = global.document || { hidden: true, addEventListener() {} };
const { create, badge, heldBy, DAYS } = require('../public/js/pi-proof.js');

const ROOT = 'a'.repeat(64);
const quorum = (over) => ({ day: '2026-10-20', root: ROOT, status: 'short', k: 2, groups: [], unreadable: [], pending: [],
  mismatched: [], meaning: 'time-proven, not performance', anchors: [], bitcoin: { status: 'not_submitted' }, ...over });
const anchor = (chain, chain_name, status, counts = true) => ({ chain, chain_name, status, counts_toward_quorum: counts });

test('the badge says what the quorum says, in three values, and "not read" claims nothing', () => {
  const held = badge(quorum({ status: 'multi_anchored', groups: ['bitcoin', 'ethereum'],
    anchors: [anchor('base', 'Base', 'verified')], bitcoin: { status: 'verified' } }));
  assert.deepStrictEqual([held.text, held.tone], ['Held by 2 independent chains', 'up']);
  const one = badge(quorum({ groups: ['ethereum'], pending: ['bitcoin'] }));
  assert.deepStrictEqual([one.text, one.tone, one.sub], ['1 of 2 independent chains so far', 'muted', 'Waiting for: Bitcoin']);
  const none = badge(quorum({}));
  assert.deepStrictEqual([none.text, none.tone], ['Not anchored yet', 'muted']);
  const unknown = badge(quorum({ status: 'unknown', groups: ['ethereum'], unreadable: ['dogecoin'],
    anchors: [anchor('dogecoin', 'Dogecoin', 'unknown')] }));
  assert.deepStrictEqual([unknown.text, unknown.tone, unknown.sub],
    ['Could not be checked just now', 'muted', 'Could not read: Dogecoin'], 'unknown is not short');
  for (const q of [null, undefined, {}, { status: 42 }]) {
    const b = badge(q);
    assert.deepStrictEqual([b.text, b.tone], ['Not checked yet', 'muted'], 'a badge not read is never "not anchored"');
  }
  const bad = badge(quorum({ status: 'multi_anchored', groups: ['bitcoin', 'ethereum'], mismatched: ['dogeos-testnet'],
    anchors: [anchor('dogeos-testnet', 'DogeOS testnet', 'mismatch', false)] }));
  assert.strictEqual(bad.sub, 'Does not match the root: DogeOS testnet', 'a record that does not match is named');
});

test('"On:" names only the chains that count and verified, and Bitcoin when its proof is in a block', () => {
  const q = quorum({ anchors: [anchor('base', 'Base', 'verified'), anchor('dogeos-testnet', 'DogeOS testnet', 'verified', false),
    anchor('dogecoin', 'Dogecoin', 'pending')], bitcoin: { status: 'verified' } });
  assert.deepStrictEqual(heldBy(q), ['Base', 'Bitcoin'], 'a testnet is shown elsewhere and never counted');
  assert.deepStrictEqual(heldBy(null), []);
});

function session(rootsAnswer, quorumFor) {
  const calls = { asked: [], inFlight: 0, maxInFlight: 0, renders: 0 };
  const ctx = {
    api(path) {
      if (path === '/api/roots') return Promise.resolve(rootsAnswer);
      const m = /^\/api\/roots\/quorum\/(.+)$/.exec(path);
      if (m) {
        const day = decodeURIComponent(m[1]);
        calls.asked.push(day);
        calls.inFlight++; calls.maxInFlight = Math.max(calls.maxInFlight, calls.inFlight);
        return new Promise((r) => setTimeout(() => { calls.inFlight--; r(quorumFor(day)); }, 5));
      }
      return Promise.resolve({ ok: false, status: 404, body: null });
    },
    render() { calls.renders++; },
  };
  return { ctx, calls };
}
const days = (n) => Array.from({ length: n }, (_, i) => ({ day: `2026-10-${String(21 - i).padStart(2, '0')}`, root: ROOT, seal_count: 10 + i }));
const settle = (ms = 150) => new Promise((r) => setTimeout(r, ms));

test('the block lists the finished days with their badges, asks one at a time, and links nowhere', async () => {
  const roots = days(9);
  const { ctx, calls } = session({ ok: true, status: 200, body: { roots, chains: [] } }, (day) => (day === '2026-10-21'
    ? { ok: true, status: 200, body: quorum({ day, status: 'multi_anchored', groups: ['bitcoin', 'ethereum'],
      anchors: [anchor('base', 'Base', 'verified')], bitcoin: { status: 'verified' } }) }
    : day === '2026-10-20' ? { ok: false, status: 429, body: { error: 'Too many requests' } }
      // A failed answer is not read, whatever its body claims.
      : day === '2026-10-18' ? { ok: false, status: 500, body: { status: 'multi_anchored', groups: ['a', 'b'], k: 2 } }
      : { ok: true, status: 200, body: quorum({ day }) }));
  const block = create();
  assert.strictEqual(block.bar, false, 'behind the header, like About: the bar keeps its five');
  block.signedIn(ctx);
  await settle();
  assert.deepStrictEqual(calls.asked, roots.slice(0, DAYS).map((r) => r.day), 'the shown days, and only those');
  assert.strictEqual(calls.maxInFlight, 1, 'one badge at a time');
  const html = block.html();
  assert.strictEqual((html.match(/class="p-proof-row"/g) || []).length, DAYS);
  assert.match(html, /data-day="2026-10-21"[\s\S]*?tone-up">Held by 2 independent chains<\/div>[\s\S]*?On: Base, Bitcoin/);
  assert.match(html, /data-day="2026-10-20"[\s\S]*?tone-muted">Not checked yet</, 'a refused ask is not read, never "not anchored"');
  assert.match(html, /data-day="2026-10-19"[\s\S]*?Not anchored yet/);
  assert.match(html, /data-day="2026-10-18"[\s\S]*?tone-muted">Not checked yet</, 'a failed answer is trusted for nothing');
  assert.match(html, /10 seals/);
  assert.match(html, /never how good it was/);
  assert.match(html, /<code title="a{64}">aaaaaaaaaa…aaaaaa<\/code>/, 'the root is quoted, shortened, whole in the title');
  assert.doesNotMatch(html, /<a\b|href=/, 'the Pi app links nowhere from here');
  // Refresh: the held day is settled and not asked again; the others are.
  calls.asked.length = 0;
  await block.reload();
  await settle();
  assert.ok(!calls.asked.includes('2026-10-21'), 'a held day is not asked again');
  assert.ok(calls.asked.includes('2026-10-20') && calls.asked.includes('2026-10-19'));
});

test('an unreadable feed is said to be unreadable, and an empty one says no day is finished yet', async () => {
  for (const answer of [{ ok: false, status: 500, body: { error: 'x' } }, { ok: true, status: 200, body: { roots: 'nope' } }]) {
    const { ctx, calls } = session(answer, () => ({ ok: true, body: quorum({}) }));
    const block = create();
    block.signedIn(ctx);
    await settle(30);
    assert.match(block.html(), /The daily roots could not be read just now\./);
    assert.doesNotMatch(block.html(), /No finished day/);
    assert.deepStrictEqual(calls.asked, []);
  }
  const { ctx } = session({ ok: true, status: 200, body: { roots: [] } }, () => null);
  const block = create();
  assert.match(block.html(), /Reading the daily roots…/, 'before the read: reading, not empty');
  block.signedIn(ctx);
  await settle(30);
  assert.match(block.html(), /No finished day has sealed calls yet\./);
});
