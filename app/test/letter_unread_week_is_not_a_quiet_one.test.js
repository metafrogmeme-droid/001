'use strict';
/**
 * The weekly letter invented a reason for a week it could not read.
 *
 * `loadWeekData` caught the trades query with `catch (e) { /* section reports
 * no data *\/ }` and left `trades = []`. Both composers render that as:
 *
 *   "The desk closed no positions this week — patience is a position too,
 *    and the risk gate saw nothing worth paying fees for."
 *
 * That is not a wrong number. It attributes a CAUSE: it tells the reader the
 * risk gate declined to trade, on a week the database simply did not answer.
 * The module header promises "nothing is invented — no data for a section
 * means the section says so", and one branch below, the unpriced-closes case
 * carries a comment about a verdict "manufactured from" absent data. The same
 * defect one level up, on the read itself, was missed.
 *
 * It was also PERMANENT. `getLetter` composes once and inserts with
 * `ON DUPLICATE KEY UPDATE week_key = week_key`, so the first write wins and
 * is never revised — a transient failure baked that week's letter in forever,
 * and every later reader was served it from cache. The comment beside the
 * insert reasons that concurrent callers "produced the same text", which is
 * true only while the load cannot fail.
 *
 * `openCount` was the same shape with a smaller blast radius: `/* stays 0 *\/`
 * rendered as "the desk carries nothing into the new week".
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const letter = require('../lib/letter');

const WEEK = {
  key: '2026-W35',
  start: new Date('2026-08-24T00:00:00Z'),
  end: new Date('2026-08-31T00:00:00Z'),
};

/** Make one SELECT fail, the way a deadlock or a dropped socket would. */
function breakQuery(re) {
  const real = pool.execute;
  pool.execute = async (sql, ...rest) => {
    if (re.test(String(sql))) throw new Error('ER_LOCK_WAIT_TIMEOUT');
    return real.call(pool, sql, ...rest);
  };
  return () => { pool.execute = real; };
}

const FABRICATED = /risk gate saw nothing worth paying fees for/;

function allText(letterObj) {
  return JSON.stringify(letterObj);
}


test('a failed trades read is reported as a failed read', async () => {
  const restore = breakQuery(/FROM trades[\s\S]*status = 'CLOSED'/);
  try {
    const data = await letter.loadWeekData(WEEK.start, WEEK.end);
    assert.equal(data.reads.trades, false, 'the failed read must be reported');
  } finally { restore(); }
});

test('an unread week never claims the risk gate declined to trade', async () => {
  // The whole point. A cause invented for a week nobody could read.
  const restore = breakQuery(/FROM trades[\s\S]*status = 'CLOSED'/);
  try {
    const data = await letter.loadWeekData(WEEK.start, WEEK.end);
    for (const compose of [letter.composeLetter, letter.composePublicLetter]) {
      const out = allText(compose(WEEK, data));
      assert.ok(!FABRICATED.test(out),
        `${compose.name}: a failed read still renders the invented cause`);
      assert.match(out, /could not be read/,
        `${compose.name}: it must say the record could not be read`);
      assert.match(out, /not a\\nquiet week|not a quiet week/,
        `${compose.name}: it must deny being a quiet week explicitly`);
    }
  } finally { restore(); }
});

test('a genuinely quiet week still reads as one', async () => {
  // The honest zero must survive: this branch is what the sentence was
  // written for, and removing it would trade one false claim for another.
  const data = await letter.loadWeekData(WEEK.start, WEEK.end);
  assert.equal(data.reads.trades, true);
  assert.equal(data.trades.length, 0, 'no trades seeded for this week');
  const out = allText(letter.composeLetter(WEEK, data));
  assert.match(out, FABRICATED, 'a real quiet week must keep its plain sentence');
});

test('an unread open count is not a flat book', async () => {
  const restore = breakQuery(/COUNT\(\*\) AS open_count/);
  try {
    const data = await letter.loadWeekData(WEEK.start, WEEK.end);
    assert.equal(data.openCount, null, 'unknown, not zero');
    assert.equal(data.reads.open, false);
    for (const compose of [letter.composeLetter, letter.composePublicLetter]) {
      const out = allText(compose(WEEK, data));
      assert.match(out, /open positions carried into the new week could not be read/,
        `${compose.name}: an unread count must say so`);
      assert.ok(!/carries <b>0<\/b>/.test(out),
        `${compose.name}: it must not print a measured zero`);
    }
  } finally { restore(); }
});

test('a letter built on a failed read is never stored', async () => {
  // getLetter's insert is write-once, so persisting here would make the
  // invented week permanent and serve it from cache forever after.
  const restore = breakQuery(/FROM trades[\s\S]*status = 'CLOSED'/);
  let res;
  try {
    res = await letter.getLetter(WEEK);
  } finally { restore(); }

  assert.equal(res.provisional, true, 'it must be marked provisional');
  assert.equal(res.created, false, 'nothing was created, so it must not claim it was');

  const [rows] = await pool.execute(
    'SELECT week_key, generated_at, letter_json FROM agent_letters WHERE week_key = ?',
    [WEEK.key]);
  assert.equal(rows.length, 0,
    'THE DEFECT: the unreadable week was written to agent_letters, where the '
    + 'write-once insert makes it permanent');
});

test('the week is composed again once the database answers', async () => {
  // The corollary of not storing it: a later call must not be served a cached
  // failure, and must be free to write the real letter.
  const res = await letter.getLetter(WEEK);
  assert.ok(!res.provisional, 'a readable week must not be provisional');
  const out = allText(res.letter);
  assert.ok(!/could not be read/.test(out),
    'the failed-read wording leaked into a week that read fine');
});

test('equity and reports were already honest and stay that way', async () => {
  // Not every catch in this file was a defect. These two set null, which the
  // composers omit — the omit strategy, correctly applied. Pinned so a later
  // sweep does not "fix" them into saying something.
  const r1 = breakQuery(/FROM equity_snapshots/);
  let data;
  try { data = await letter.loadWeekData(WEEK.start, WEEK.end); } finally { r1(); }
  assert.equal(data.reads.equity, false);
  assert.equal(data.equity.start, null);
  assert.equal(data.equity.end, null);
});
