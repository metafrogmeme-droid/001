'use strict';
/**
 * The anchor watch tells the owner's Discord channel what changed
 * (lib/anchor_watch.js `alert`, lib/discord_alerts.js), and never its URL.
 *
 * Each rule is driven both ways: a new finding is said at once and not again
 * on the next pass; one still open is said again after its level's wait, and
 * not before; one that cleared is said once; one gone only because its check
 * could not be read this time has NOT cleared and is not said to have; a day
 * that left the window is said to be no longer watched. A send that fails is
 * said again on the next pass. With no hook nothing is posted and nothing is
 * recorded as said. What was said is shared state, so a new instance does not
 * repeat it. The hook's URL carries its token, so it is never in a message, an
 * error, the health record or /diagz.
 */
delete process.env.DATABASE_URL;
delete process.env.ANCHOR_ALERTS_DISCORD_WEBHOOK;
delete process.env.APP_BASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const { pool } = require('../db');
const discord = require('../lib/discord_alerts');
const jobState = require('../lib/job_state');
const health = require('../lib/watch_health');
const watch = require('../lib/anchor_watch');

const H = 3600 * 1000;
// Built here, never spelled out: a whole hook URL in a source file reads as a leak.
const TOKEN = 'token'.repeat(8);
const HOOK = `https://discord.com/api/webhooks/123456789012345678/${TOKEN}`;
const ENV = { ANCHOR_ALERTS_DISCORD_WEBHOOK: HOOK, APP_BASE_URL: 'https://humanoid-traders.com/' };

/** A Discord stand-in that records what it was handed. */
function hookStub(status = 204) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    return { status };
  };
  return { calls, fetchImpl };
}

const day = (d, over = {}) => ({ day: d, status: 'multi_anchored', groups: ['bitcoin', 'ethereum-l2'], unread: [], ...over });
const pass = (days, findings) => ({ checked_at: '2026-10-12T00:00:00Z', days, findings, error: null });
const F = {
  disagree: { day: '2026-10-10', kind: 'reader_disagreement', level: 'alarm', chain: 'dogecoin', detail: 'the Dogecoin readers disagree' },
  short: { day: '2026-10-10', kind: 'quorum_short', level: 'todo', detail: '1 of 2 independent chains' },
  stuck: { day: '2026-10-10', kind: 'ots_stuck', level: 'warn', chain: 'bitcoin', detail: 'sent 25 h ago, not yet in a Bitcoin block' },
  failed: { day: '2026-10-10', kind: 'check_failed', level: 'warn', detail: 'ConnectionError' },
};
const keys = (xs) => xs.map((f) => `${f.kind}:${f.day || ''}`);

// ── the hook ────────────────────────────────────────────────────────────────

test('only a Discord webhook URL is a hook, and its state never carries the URL', () => {
  assert.deepStrictEqual(discord.hookOf({}), { configured: false, reason: 'not set' });
  for (const bad of [
    `https://discord.example/api/webhooks/123456789012345678/${TOKEN}`,   // another host
    `http://discord.com/api/webhooks/123456789012345678/${TOKEN}`,        // not https
    `https://discord.com/api/webhooks/123456789012345678/${TOKEN}?wait=1`, // anything after the token
    'https://discord.com/channels/1/2',
  ]) {
    assert.deepStrictEqual(discord.hookOf({ ANCHOR_ALERTS_DISCORD_WEBHOOK: bad }),
      { configured: false, reason: 'not a Discord webhook URL' }, bad);
  }
  assert.deepStrictEqual(discord.hookOf(ENV), { configured: true, url: HOOK });
  assert.deepStrictEqual(discord.hookOf({ ANCHOR_ALERTS_DISCORD_WEBHOOK: ` ${HOOK}\n` }), { configured: true, url: HOOK });
  assert.deepStrictEqual(discord.describe(ENV), { configured: true });
});

test('a post goes to the hook with mentions off, and the text it carries is scrubbed', async () => {
  const { calls, fetchImpl } = hookStub();
  await discord.post(`a line that quotes ${HOOK} by mistake`, { env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, HOOK);
  assert.strictEqual(calls[0].init.method, 'POST');
  assert.deepStrictEqual(calls[0].body.allowed_mentions, { parse: [] }, 'no finding can ping the channel');
  assert.strictEqual(calls[0].body.username, discord.USERNAME);
  assert.ok(!calls[0].body.content.includes(TOKEN), calls[0].body.content);
  assert.match(calls[0].body.content, /webhooks\/123456789012345678\//, 'the hook id survives, to say which hook');
});

test('a failed post names Discord\'s status or the failure\'s class, never the URL', async () => {
  await assert.rejects(discord.post('x', { env: ENV, fetchImpl: hookStub(429).fetchImpl }),
    (e) => e.name === 'DiscordHttpError' && e.status === 429 && e.message === 'Discord answered 429');
  const leaky = async (url) => { const e = new TypeError(`fetch failed for ${url}`); throw e; };
  await assert.rejects(discord.post('x', { env: ENV, fetchImpl: leaky }), (e) => {
    assert.strictEqual(e.name, 'TypeError');
    assert.ok(!`${e.message}\n${e.stack}`.includes(TOKEN), e.message);
    return true;
  });
  await assert.rejects(discord.post('x', { env: {}, fetchImpl: hookStub().fetchImpl }), (e) => e.name === 'NotConfigured');
});

test('a post Discord never answers is given up after its time limit', async () => {
  const hangs = (url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => { const e = new Error(`aborted ${url}`); e.name = 'AbortError'; reject(e); });
  });
  await assert.rejects(discord.post('x', { env: ENV, fetchImpl: hangs, timeoutMs: 20 }),
    (e) => e.name === 'AbortError' && !e.message.includes(TOKEN));
});

test('a long message keeps whole lines under Discord\'s limit and counts the ones left out', () => {
  // Several widths: whether the counted tail still fits depends on where the last whole line ends.
  for (const width of [60, 99, 100, 150, 499]) {
    const lines = Array.from({ length: 80 }, (_, i) => `line ${i} `.padEnd(width, '.'));
    const out = discord.compose(lines);
    assert.ok(out.length <= discord.MAX_CHARS, `width ${width}: ${out.length} chars`);
    const kept = out.split('\n');
    const tail = kept.pop();
    assert.match(tail, /^… and (\d+) more lines \(see \/diagz\)$/);
    assert.strictEqual(kept.length + Number(tail.match(/(\d+)/)[1]), lines.length, 'kept + left out = all');
    assert.deepStrictEqual(kept, lines.slice(0, kept.length), 'whole lines, in order');
  }
  // A message that fits is sent whole, up to the last character Discord takes.
  const exact = ['x'.repeat(999), 'y'.repeat(1000)];
  assert.strictEqual(discord.compose(exact), exact.join('\n'));
  assert.strictEqual(discord.compose(exact).length, discord.MAX_CHARS);
  const over = discord.compose(['x'.repeat(999), 'y'.repeat(1001)]);
  assert.strictEqual(over, `${'x'.repeat(999)}\n… and 1 more line (see /diagz)`);
});

// ── what to say (plan) ──────────────────────────────────────────────────────

test('a new finding is said at once, and not again before its level\'s wait', () => {
  const t0 = Date.parse('2026-10-12T00:00:00Z');
  const p = pass([day('2026-10-10', { status: 'unknown', unread: ['dogecoin'] })], [F.disagree, F.short]);
  const first = watch.plan(p, {}, t0);
  assert.deepStrictEqual(keys(first.fresh), ['reader_disagreement:2026-10-10', 'quorum_short:2026-10-10'], 'alarms first');
  const soon = watch.plan(p, first.next, t0 + 1 * H);
  assert.deepStrictEqual([soon.fresh, soon.again, soon.cleared, soon.aged], [[], [], [], []]);
  assert.deepStrictEqual(soon.next, first.next);
  // An alarm waits six hours, a to-do a day: at six hours only the alarm is said again.
  const six = watch.plan(p, first.next, t0 + watch.REPEAT_MS.alarm);
  assert.deepStrictEqual(keys(six.again), ['reader_disagreement:2026-10-10']);
  assert.strictEqual(six.again[0].first, t0, 'said again with when it was first said');
  assert.deepStrictEqual(keys(watch.plan(p, first.next, t0 + watch.REPEAT_MS.alarm - 1).again), []);
  // A day in: the to-do is due, and so is the alarm, said last at six hours.
  const dayLater = watch.plan(p, six.next, t0 + watch.REPEAT_MS.todo);
  assert.deepStrictEqual(keys(dayLater.again), ['reader_disagreement:2026-10-10', 'quorum_short:2026-10-10']);
  assert.deepStrictEqual(keys(watch.plan(p, six.next, t0 + watch.REPEAT_MS.alarm + 1 * H).again), [],
    'an hour after the alarm was said again, nothing is due');
});

test('a finding is cleared only when its check was read; gone because unreadable, it stays open unsaid', () => {
  const t0 = Date.parse('2026-10-12T00:00:00Z');
  const sent = watch.plan(pass([day('2026-10-10', { status: 'short' })], [F.stuck, F.short]), {}, t0).next;

  // Bitcoin could not be read this time: the stuck proof is not known to be unstuck.
  const unread = watch.plan(pass([day('2026-10-10', { status: 'short', unread: ['bitcoin'] })], [F.short]), sent, t0 + H);
  assert.deepStrictEqual(unread.cleared, []);
  assert.ok(unread.next[`ots_stuck|2026-10-10|bitcoin`], 'still open');
  // The day itself is unknown: a quorum to-do is not known to be done.
  const unknown = watch.plan(pass([day('2026-10-10', { status: 'unknown', unread: ['dogecoin'] })],
    [{ day: '2026-10-10', kind: 'unreadable', level: 'warn', detail: 'could not be checked: dogecoin' }, F.stuck]), sent, t0 + H);
  assert.deepStrictEqual(unknown.cleared, []);
  assert.deepStrictEqual(keys(unknown.fresh), ['unreadable:2026-10-10']);
  // The day's check threw: nothing about it can be told.
  const threw = watch.plan(pass([day('2026-10-10', { status: 'check_failed', groups: null, unread: null })], [F.failed]), sent, t0 + H);
  assert.deepStrictEqual(threw.cleared, []);
  assert.deepStrictEqual(Object.keys(threw.next).sort(), ['check_failed|2026-10-10|', 'ots_stuck|2026-10-10|bitcoin', 'quorum_short|2026-10-10|']);

  // Read, and gone: cleared, once.
  const read = watch.plan(pass([day('2026-10-10')], []), sent, t0 + H);
  assert.deepStrictEqual(keys(read.cleared).sort(), ['ots_stuck:2026-10-10', 'quorum_short:2026-10-10']);
  assert.deepStrictEqual(read.next, {});
  // A check that stopped throwing has cleared its failure, even while the day is unknown.
  const after = watch.plan(pass([day('2026-10-10', { status: 'unknown', unread: ['dogecoin'] })], []),
    watch.plan(pass([day('2026-10-10', { status: 'check_failed', unread: null })], [F.failed]), {}, t0).next, t0 + H);
  assert.deepStrictEqual(keys(after.cleared), ['check_failed:2026-10-10']);
});

test('a day that left the window is said to be no longer watched, not cleared', () => {
  const t0 = Date.parse('2026-10-12T00:00:00Z');
  const sent = watch.plan(pass([day('2026-10-10', { status: 'short' })], [F.short]), {}, t0).next;
  const moved = watch.plan(pass([day('2026-10-13'), day('2026-10-12'), day('2026-10-11')], []), sent, t0 + 72 * H);
  assert.deepStrictEqual(moved.cleared, []);
  assert.deepStrictEqual(keys(moved.aged), ['quorum_short:2026-10-10']);
  assert.deepStrictEqual(moved.next, {});
});

test('a pass that could not read is said, clears nothing, and is cleared when the roots read again', () => {
  const t0 = Date.parse('2026-10-12T00:00:00Z');
  const sent = watch.plan(pass([day('2026-10-10', { status: 'short' })], [F.short]), {}, t0).next;
  const blind = watch.plan({ checked_at: null, days: [], findings: null, error: 'ConnectionError' }, sent, t0 + H);
  assert.deepStrictEqual(keys(blind.fresh), ['watch_unreadable:']);
  assert.match(blind.fresh[0].detail, /ConnectionError/);
  assert.deepStrictEqual([blind.cleared, blind.aged], [[], []], 'an unread pass cannot tell anything has cleared');
  assert.ok(blind.next['quorum_short|2026-10-10|']);
  const back = watch.plan(pass([day('2026-10-10', { status: 'short' })], [F.short]), blind.next, t0 + 2 * H);
  assert.deepStrictEqual(keys(back.cleared), ['watch_unreadable:']);
  assert.deepStrictEqual(back.fresh, []);
  assert.match(watch.messageOf(back, {}), /the anchor watch reads the roots again/);
});

test('the message counts what changed, names each line and links the site, with no URL of the hook', () => {
  const t0 = Date.parse('2026-10-12T00:00:00Z');
  const sent = watch.plan(pass([day('2026-10-10', { status: 'short' })], [F.short]), {}, t0).next;
  const planned = watch.plan(pass([day('2026-10-10', { status: 'unknown', unread: ['dogecoin'] })], [F.disagree, F.short]), sent, t0 + watch.REPEAT_MS.todo);
  const text = watch.messageOf(planned, ENV);
  const lines = text.split('\n');
  assert.strictEqual(lines[0], '**RUNECLAW anchor watch** · 1 new, 1 still open');
  assert.match(lines[1], /\*\*alarm\*\* · reader_disagreement · 2026-10-10 · dogecoin: the Dogecoin readers disagree$/);
  assert.match(lines[2], /still open since 2026-10-12T00:00Z · todo · quorum_short · 2026-10-10/);
  assert.strictEqual(lines[3], 'https://humanoid-traders.com/roots · the full pass is on /diagz');
  assert.ok(!text.includes(TOKEN) && !text.includes('webhooks'));
  assert.strictEqual(watch.messageOf(planned, {}).split('\n').pop(), 'The full pass is on /diagz.', 'no site link without a base URL');
});

// ── the watch, end to end ───────────────────────────────────────────────────

const ROOT = 'b'.repeat(64);
let seeded = false;
async function seed() {
  if (seeded) return;
  seeded = true;
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    ['2026-10-10', ROOT, 1, JSON.stringify([ROOT]), new Date()]);
}
const shortDay = async (row) => ({ day: row.day, status: 'short', k: 2, groups: ['bitcoin'], unreadable: [], pending: [], mismatched: [],
  anchors: [], bitcoin: { status: 'verified' } });
const provenDay = async (row) => ({ day: row.day, status: 'multi_anchored', k: 2, groups: ['bitcoin', 'ethereum-l2'], unreadable: [], pending: [],
  mismatched: [], anchors: [{ chain: 'base', status: 'verified' }], bitcoin: { status: 'verified' } });
// 40 h after 10 October ended: its short quorum is a to-do.
const T = Date.parse('2026-10-12T16:00:00Z');

test('with no hook nothing is posted and nothing is recorded as said', async () => {
  await seed();
  const { calls, fetchImpl } = hookStub();
  await watch.runOnce({ now: T, check: shortDay, env: {}, fetchImpl });
  assert.strictEqual(calls.length, 0);
  assert.deepStrictEqual(await jobState.load(watch.ALERTS), { found: false });
  assert.strictEqual(health.snapshot()[watch.ALERTS].state, 'skipped');
  assert.strictEqual(health.snapshot()[watch.ALERTS].skipped_reason, 'no Discord webhook: not set');
  const snap = await watch.snapshot({ env: {} });
  assert.deepStrictEqual(snap.alerts, { channel: 'discord', configured: false, reason: 'not set', open: null, last_sent_at: null, last_error: null });
});

test('a failed send is said again on the next pass, and the failure is named without the URL', async () => {
  await seed();
  const leaky = async (url) => { throw new TypeError(`request to ${url} failed`); };
  const r = await watch.alert(await watch.runOnce({ now: T, check: shortDay, env: {} }), { now: T, env: ENV, fetchImpl: leaky });
  assert.deepStrictEqual(r, { said: 0, error: 'TypeError' });
  const saved = await jobState.load(watch.ALERTS);
  assert.deepStrictEqual(saved.value.sent, {}, 'nothing recorded as said');
  assert.strictEqual(saved.value.last_error, 'TypeError');
  assert.strictEqual(health.snapshot()[watch.ALERTS].state, 'failed');
  const snap = await watch.snapshot({ env: ENV });
  assert.strictEqual(snap.alerts.last_error, 'TypeError');
  for (const where of [JSON.stringify(saved), JSON.stringify(snap), JSON.stringify(health.snapshot())]) {
    assert.ok(!where.includes(TOKEN), where);
  }
});

test('the first configured pass says everything open; the next says nothing; a cleared finding is said once', async () => {
  await seed();
  const { calls, fetchImpl } = hookStub();
  await watch.runOnce({ now: T + H, check: shortDay, env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 1, 'the open to-do, said after the failed send');
  assert.match(calls[0].body.content, /1 new/);
  assert.match(calls[0].body.content, /\*\*todo\*\* · quorum_short · 2026-10-10: 1 of 2 independent chains/);
  assert.deepStrictEqual(calls[0].body.allowed_mentions, { parse: [] });
  const saved = await jobState.load(watch.ALERTS);
  assert.deepStrictEqual(Object.keys(saved.value.sent), ['quorum_short|2026-10-10|']);
  assert.strictEqual(saved.value.last_error, null, 'a send that worked clears the last failure');

  await watch.runOnce({ now: T + 2 * H, check: shortDay, env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 1, 'still open, not yet due again: nothing said');

  // A new instance: its module memory is gone, the shared state is not.
  jobState._clearMemory();
  delete require.cache[require.resolve('../lib/anchor_watch')];
  const fresh = require('../lib/anchor_watch');
  const seen = await fresh.snapshot({ env: ENV });
  assert.strictEqual(seen.source, 'shared', 'the last pass is read from the shared state');
  assert.strictEqual(seen.checked_at, new Date(T + 2 * H).toISOString());
  assert.deepStrictEqual(seen.counts, { alarm: 0, warn: 0, todo: 1 });
  assert.strictEqual(seen.alerts.open, 1);
  await fresh.runOnce({ now: T + 3 * H, check: shortDay, env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 1, 'a new instance does not repeat what was said');

  await fresh.runOnce({ now: T + 4 * H, check: provenDay, env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 2);
  assert.match(calls[1].body.content, /1 cleared/);
  assert.match(calls[1].body.content, /cleared · quorum_short · 2026-10-10/);
  await fresh.runOnce({ now: T + 5 * H, check: provenDay, env: ENV, fetchImpl });
  assert.strictEqual(calls.length, 2, 'said once');

  const snap = await fresh.snapshot({ env: ENV });
  assert.strictEqual(snap.alerts.configured, true);
  assert.strictEqual(snap.alerts.open, 0);
  assert.strictEqual(snap.alerts.last_sent_at, new Date(T + 4 * H).toISOString());
  assert.ok(!JSON.stringify(snap).includes(TOKEN));
  for (const c of calls) assert.ok(!c.body.content.includes(TOKEN));
});

test('readers that go silent after disagreeing do not post the alarm as cleared', async () => {
  await seed();
  const w = require('../lib/anchor_watch'); // the instance the test above left
  const { calls, fetchImpl } = hookStub();
  const dogecoin = (over) => async (row) => ({ day: row.day, k: 2, groups: ['bitcoin'], pending: [], mismatched: [],
    status: 'unknown', unreadable: ['dogecoin'], bitcoin: { status: 'verified' }, ...over });
  await w.runOnce({ now: T + 6 * H, env: ENV, fetchImpl,
    check: dogecoin({ anchors: [{ chain: 'dogecoin', status: 'unknown', disagreement: true, reason: 'the Dogecoin readers disagree' }] }) });
  assert.strictEqual(calls.length, 1);
  assert.match(calls[0].body.content, /\*\*alarm\*\* · reader_disagreement · 2026-10-10 · dogecoin/);
  // The readers stop answering: no disagreement, and no reading either.
  await w.runOnce({ now: T + 7 * H, env: ENV, fetchImpl,
    check: dogecoin({ anchors: [{ chain: 'dogecoin', status: 'unknown', reason: 'the Dogecoin readers could not be read' }] }) });
  assert.strictEqual(calls.length, 1, 'an alarm whose check went dark is not cleared');
  assert.strictEqual((await w.snapshot({ env: ENV })).alerts.open, 2, 'the alarm and the unreadable day, both still open');
  // The readers answer and agree: now it has cleared.
  await w.runOnce({ now: T + 8 * H, env: ENV, fetchImpl,
    check: dogecoin({ status: 'multi_anchored', groups: ['bitcoin', 'dogecoin'], unreadable: [], anchors: [{ chain: 'dogecoin', status: 'verified' }] }) });
  assert.strictEqual(calls.length, 2);
  assert.match(calls[1].body.content, /2 cleared/);
  assert.match(calls[1].body.content, /cleared · reader_disagreement · 2026-10-10 · dogecoin/);
});
