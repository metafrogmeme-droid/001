'use strict';
/**
 * The anchors, watched (lib/anchor_watch.js): the roadmap's launch gate for
 * monitoring. Each finding is driven both ways at its boundary: a reader
 * disagreement and a recorded anchor that no longer checks out are alarms; a
 * Bitcoin proof stuck a day, or never sent, is a warning, and not before its
 * time; a day short of its quorum is an operator's to-do a day and a half
 * after it ended, and not before. A day whose check throws is named with its
 * class; roots that cannot be read are no findings at all (null), never an
 * empty list that reads as all clear.
 */
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const { pool } = require('../db');
const watch = require('../lib/anchor_watch');
const health = require('../lib/watch_health');

const H = 3600 * 1000;
const DAY = '2026-10-10';
const END = Date.parse(`${DAY}T00:00:00Z`) + 24 * H;           // the moment the day ended
const quorum = (over = {}) => ({
  day: DAY, status: 'multi_anchored', k: 2, groups: ['bitcoin', 'ethereum-l2'], unreadable: [], pending: [], mismatched: [],
  anchors: [{ chain: 'base', status: 'verified' }],
  bitcoin: { status: 'verified', submitted_at: new Date(END - 20 * H).toISOString() },
  ...over,
});
const kinds = (fs) => fs.map((f) => `${f.level}:${f.kind}${f.chain ? `:${f.chain}` : ''}`);

test('a day proven by two chains raises nothing', () => {
  assert.deepStrictEqual(watch.findingsFor(quorum(), END + 40 * H), []);
});

test('readers that contradict each other, and an anchor that no longer checks out, are alarms', () => {
  const q = quorum({
    anchors: [{ chain: 'base', status: 'verified' }, { chain: 'dogecoin', status: 'unknown', disagreement: true, reason: 'the Dogecoin readers disagree' }],
    mismatched: ['base-eas'],
  });
  q.anchors.push({ chain: 'base-eas', status: 'mismatch', reason: 'the transaction reverted' });
  const f = watch.findingsFor(q, END + 1 * H);
  assert.deepStrictEqual(kinds(f), ['alarm:reader_disagreement:dogecoin', 'alarm:anchor_mismatch:base-eas']);
  assert.strictEqual(f[1].detail, 'the transaction reverted');
  // Bitcoin's own readers disagreeing is the same alarm.
  const b = watch.findingsFor(quorum({ bitcoin: { status: 'unknown', disagreement: true, reason: 'the Bitcoin header sources disagree' } }), END + 1 * H);
  assert.deepStrictEqual(kinds(b), ['alarm:reader_disagreement:bitcoin']);
});

test('a Bitcoin proof never sent, or stuck a day, is a warning, and not before its time', () => {
  const unsent = quorum({ status: 'short', groups: ['ethereum-l2'], bitcoin: { status: 'not_submitted' } });
  assert.deepStrictEqual(kinds(watch.findingsFor(unsent, END + watch.OTS_MISSING_MS - 1)), []);
  assert.deepStrictEqual(kinds(watch.findingsFor(unsent, END + watch.OTS_MISSING_MS + 1)), ['warn:ots_missing:bitcoin']);
  const sent = END + 1 * H;
  const pending = quorum({ status: 'short', groups: ['ethereum-l2'], pending: ['bitcoin'],
    bitcoin: { status: 'pending', submitted_at: new Date(sent).toISOString() } });
  assert.deepStrictEqual(kinds(watch.findingsFor(pending, sent + watch.OTS_STUCK_MS - 1)), []);
  const stuck = watch.findingsFor(pending, sent + watch.OTS_STUCK_MS + 1);
  // 25 h after the day ended: stuck, and not yet the quorum's to-do.
  assert.deepStrictEqual(kinds(stuck), ['warn:ots_stuck:bitcoin']);
  assert.match(stuck[0].detail, /^sent 24 h ago/);
});

test('a day short of its quorum is a to-do a day and a half after it ended; unreadable is a warning', () => {
  const short = quorum({ status: 'short', groups: ['bitcoin'], anchors: [] });
  assert.deepStrictEqual(kinds(watch.findingsFor(short, END + watch.QUORUM_DUE_MS - 1)), []);
  const due = watch.findingsFor(short, END + watch.QUORUM_DUE_MS + 1);
  assert.deepStrictEqual(kinds(due), ['todo:quorum_short']);
  assert.strictEqual(due[0].detail, '1 of 2 independent chains');
  const unknown = watch.findingsFor(quorum({ status: 'unknown', groups: ['bitcoin'], unreadable: ['dogecoin'] }), END + 1 * H);
  assert.deepStrictEqual(kinds(unknown), ['warn:unreadable']);
  assert.match(unknown[0].detail, /dogecoin/);
});

test('a pass checks the newest days only, names a failed check by its class, and counts what it found', async () => {
  for (const d of ['2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10']) {
    await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
      [d, 'a'.repeat(64), 1, JSON.stringify(['a'.repeat(64)]), new Date()]);
  }
  const asked = [];
  const check = async (row) => {
    asked.push(row.day);
    if (row.day === '2026-10-09') { const e = new Error('connect ECONNREFUSED 10.0.0.1:3306'); e.name = 'ConnectionError'; throw e; }
    return quorum({ day: row.day, status: 'short', groups: ['bitcoin'], anchors: [] });
  };
  // 37 h after 10 October ended: both short days are past their due time.
  const now = Date.parse('2026-10-12T13:00:00Z');
  const out = await watch.runOnce({ now, check });
  assert.deepStrictEqual(asked, ['2026-10-10', '2026-10-09', '2026-10-08'], 'the newest three, newest first');
  assert.deepStrictEqual(out.days.map((d) => d.status), ['short', 'check_failed', 'short']);
  const failed = out.findings.find((f) => f.kind === 'check_failed');
  assert.strictEqual(failed.detail, 'ConnectionError');
  assert.ok(!JSON.stringify(out).includes('10.0.0.1'), 'the class, never the message');
  assert.deepStrictEqual((await watch.snapshot()).counts, { alarm: 0, warn: 1, todo: 2 });
  assert.strictEqual(health.snapshot()[watch.JOB].state, 'ok');
});

test('roots that cannot be read are no findings at all, never an empty all-clear', async () => {
  const real = pool.execute;
  pool.execute = async () => { const e = new Error('connect ECONNREFUSED'); e.name = 'ConnectionError'; throw e; };
  try {
    const out = await watch.runOnce({ now: Date.parse('2026-10-12T14:00:00Z') });
    assert.strictEqual(out.findings, null);
    assert.strictEqual(out.error, 'ConnectionError');
  } finally { pool.execute = real; }
  assert.strictEqual((await watch.snapshot()).counts, null);
  assert.strictEqual(health.snapshot()[watch.JOB].state, 'failed');
});
