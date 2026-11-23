'use strict';
/**
 * The one-time first-steps email: off unless switched on, once per member,
 * only about three days after sign-up, never to someone who already traded
 * in the Arena, opted out, or holds an address that cannot receive mail, and
 * never to the back-catalogue when it is first switched on.
 *
 * Driven through lib/onboarding_mail.js and scripts/email_onboarding_job.js
 * on the in-memory database, with the real account_mail.notify and the
 * mailer's send captured. Each test runs at its own moment, a month apart,
 * so one test's accounts are never inside another's window.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'o'.repeat(48);
process.env.APP_BASE_URL = 'https://app.test';
process.env.MAIL_FROM = 'RUNECLAW <info@example.com>';
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const mailer = require('../lib/mailer');
const sent = [];
let failSends = 0;
mailer.sendMail = async (msg) => {
  if (failSends > 0) { failSends -= 1; throw new Error('421 relay went away'); }
  sent.push(msg);
  return { skipped: false };
};
const realIsConfigured = mailer.isConfigured;
mailer.isConfigured = () => true;

const { pool } = require('../db');
const onboarding = require('../lib/onboarding_mail');
const prefs = require('../lib/email_prefs');
const { main } = require('../scripts/email_onboarding_job');

const DAY = 24 * 60 * 60 * 1000;
const ON = { EMAIL_ONBOARDING_JOB: '1' };
let moment = Date.parse('2030-01-01T12:00:00Z');
function nextMoment() { moment += 30 * DAY; return new Date(moment); }

let n = 0;
async function account(now, daysAgo, { verified = false, email } = {}) {
  n += 1;
  const addr = email || `onb${n}@example.com`;
  const [r] = await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [addr, 'x']);
  const u = pool.users.find((x) => x.id === r.insertId);
  u.created_at = new Date(now.getTime() - daysAgo * DAY);
  u.email_verified = verified;
  return { id: r.insertId, email: addr };
}

const claimedAt = (id) => pool.users.find((x) => x.id === id).onboarding_email_sent_at;
const mailsTo = (email, from) => sent.slice(from).filter((m) => m.to === email);

test('switched off, it reads nothing and sends nothing', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  for (const value of [undefined, '', '0', 'false', 'FALSE', 'no', 'off', 'yes', 'on', ' 2 ']) {
    const env = value === undefined ? {} : { EMAIL_ONBOARDING_JOB: value };
    const from = sent.length;
    let asked = 0;
    const counting = { execute: async (...args) => { asked += 1; return pool.execute(...args); } };
    const r = await onboarding.runOnce({ pool: counting, now, env });
    assert.deepEqual(r, { ran: false, reason: 'flag_off' }, JSON.stringify(value));
    assert.equal(asked, 0, `${JSON.stringify(value)}: the database was read`);
    assert.equal(sent.length, from);
  }
  assert.equal(claimedAt(a.id), null);
  // The script, too, stops before it touches a database.
  const lines = [];
  const code = await main([], {
    env: {}, out: (l) => lines.push(l),
    db: { backend: () => { throw new Error('the database was asked for'); } },
  });
  assert.equal(code, 0);
  assert.match(lines[0], /off; nothing read, nothing sent/);
});

test('switched on, a day-3 member gets one mail, and a second run is a no-op', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  let from = sent.length;
  let r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.ran, true, JSON.stringify(r));
  assert.equal(r.counts.sent, 1, JSON.stringify(r.counts));
  const got = mailsTo(a.email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'A few first steps on RUNECLAW');
  const t = prefs.tokenFor(a.id);
  assert.ok(got[0].text.includes(`you can unsubscribe: https://app.test/email-preferences?token=${t}`));
  assert.equal(got[0].listUnsubscribe, `https://app.test/api/email-preferences/unsubscribe?token=${t}`);
  assert.ok(claimedAt(a.id) instanceof Date);

  from = sent.length;
  r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.counts.sent, 0, JSON.stringify(r.counts));
  assert.deepEqual(mailsTo(a.email, from), [], 'the same member was mailed twice');
  // And a day later, still nothing.
  r = await onboarding.runOnce({ pool, now: new Date(now.getTime() + DAY / 2), env: ON });
  assert.deepEqual(mailsTo(a.email, from), []);
});

test('only accounts at least 3 and under 4 days old: never the back-catalogue', async () => {
  const now = nextMoment();
  const inside = [await account(now, 3), await account(now, 3.5), await account(now, 3.999)];
  const outside = [
    await account(now, 2.999), await account(now, 2), await account(now, 4),
    await account(now, 5), await account(now, 30), await account(now, 400),
  ];
  const from = sent.length;
  const r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.counts.sent, inside.length, JSON.stringify(r.counts));
  for (const a of inside) assert.equal(mailsTo(a.email, from).length, 1, `${a.email} was not mailed`);
  for (const a of outside) {
    assert.deepEqual(mailsTo(a.email, from), [], `${a.email} is outside the window and was mailed`);
    assert.equal(claimedAt(a.id), null);
  }
  assert.deepEqual(r.window, {
    lo: Math.floor((now.getTime() - 4 * DAY) / 1000),
    hi: Math.floor((now.getTime() - 3 * DAY) / 1000),
  });
});

test('skipped: an Arena trade, an Arena position, an opt-out, an address that cannot receive mail', async () => {
  const now = nextMoment();
  const traded = await account(now, 3.5);
  pool.arenaTrades.push({ id: 900000 + traded.id, user_id: traded.id, closed_at: now });
  const holding = await account(now, 3.5);
  pool.arenaPositions.push({ id: 900000 + holding.id, user_id: holding.id });
  const optedOut = await account(now, 3.5);
  await prefs.setOptOut(optedOut.id, true);
  const placeholder = await account(now, 3.5, { email: `telegram-${n + 1}@telegram.runeclaw.local` });
  const eligible = await account(now, 3.5);
  const from = sent.length;
  const r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.counts.traded, 2, JSON.stringify(r.counts));
  assert.equal(r.counts.undeliverable, 1, JSON.stringify(r.counts));
  assert.equal(r.counts.sent, 1, JSON.stringify(r.counts));
  for (const a of [traded, holding, optedOut, placeholder]) {
    assert.deepEqual(mailsTo(a.email, from), [], `${a.email} was mailed`);
    assert.equal(claimedAt(a.id), null, `${a.email} was claimed`);
  }
  assert.equal(mailsTo(eligible.email, from).length, 1, 'the other arm: the eligible member was mailed');
});

test('an Arena read that fails skips the member; it is never taken as "has not traded"', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  const broken = {
    execute: async (sql, params) => {
      if (/FROM arena_/i.test(sql)) throw new Error('arena table unreadable');
      return pool.execute(sql, params);
    },
  };
  const from = sent.length;
  const r = await onboarding.runOnce({ pool: broken, now, env: ON });
  assert.equal(r.counts.arena_unreadable, 1, JSON.stringify(r.counts));
  assert.equal(r.counts.sent, 0);
  assert.deepEqual(mailsTo(a.email, from), []);
  assert.equal(claimedAt(a.id), null, 'a member whose trades could not be read was claimed');
  // The script says so with exit 1.
  const code = await main([], { env: ON, now, out: () => {}, db: { backend: () => 'mysql', pool: broken } });
  assert.equal(code, 1);
});

test('a run that cannot send claims nobody', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  const cases = [
    ['mailer_not_configured', () => { mailer.isConfigured = () => false; }, () => { mailer.isConfigured = () => true; }],
    ['mailer_has_no_from', () => { process.env.MAIL_FROM = ''; }, () => { process.env.MAIL_FROM = 'RUNECLAW <info@example.com>'; }],
    ['public_origin_not_https', () => { process.env.APP_BASE_URL = 'http://app.test'; }, () => { process.env.APP_BASE_URL = 'https://app.test'; }],
    ['no_unsubscribe_key', () => { process.env.BOT_SYNC_SECRET = 'short'; }, () => { process.env.BOT_SYNC_SECRET = 'o'.repeat(48); }],
  ];
  for (const [problem, arrange, restore] of cases) {
    arrange();
    try {
      const from = sent.length;
      const r = await onboarding.runOnce({ pool, now, env: ON });
      assert.equal(r.ran, false, problem);
      assert.ok(r.problems.includes(problem), `${problem}: ${JSON.stringify(r)}`);
      assert.equal(sent.length, from);
      assert.equal(claimedAt(a.id), null, `${problem}: a member was claimed by a run that could not send`);
      const code = await main([], { env: ON, now, out: () => {}, db: { backend: () => 'mysql', pool } });
      assert.equal(code, 2, problem);
    } finally {
      restore();
    }
  }
  // The other arm: with everything in place the same member is mailed.
  const r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.counts.sent, 1);
});

test('a delivery failure stops the run; the claimed member is not retried, the rest stay unclaimed', async () => {
  const now = nextMoment();
  const first = await account(now, 3.5);
  const second = await account(now, 3.5);
  failSends = 1;
  let from = sent.length;
  let r = await onboarding.runOnce({ pool, now, env: ON });
  assert.equal(r.stopped, 'send_failed', JSON.stringify(r));
  assert.deepEqual(r.counts.not_sent, { failed: 1 });
  assert.ok(claimedAt(first.id) instanceof Date, 'the member whose send failed should stay claimed');
  assert.equal(claimedAt(second.id), null, 'a member after the failure was claimed');
  assert.equal(sent.length, from);
  from = sent.length;
  r = await onboarding.runOnce({ pool, now, env: ON });
  assert.deepEqual(mailsTo(first.email, from), [], 'a claimed member was mailed on the retry');
  assert.equal(mailsTo(second.email, from).length, 1, 'the unclaimed member was not mailed on the next run');
});

test('a claim already taken is a no-op, even between the read and the claim', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  // Another run claims the row after this run read its candidates.
  const racing = {
    execute: async (sql, params) => {
      if (/^UPDATE users SET onboarding_email_sent_at/.test(sql)) {
        await pool.execute(sql, params);
      }
      return pool.execute(sql, params);
    },
  };
  const from = sent.length;
  const r = await onboarding.runOnce({ pool: racing, now, env: ON });
  assert.equal(r.counts.already_claimed, 1, JSON.stringify(r.counts));
  assert.equal(r.counts.sent, 0);
  assert.deepEqual(mailsTo(a.email, from), []);
});

test('the unverified get the one quiet reminder line, and no new token', async () => {
  const now = nextMoment();
  const unverified = await account(now, 3.5, { verified: false });
  const verified = await account(now, 3.5, { verified: true });
  const from = sent.length;
  await onboarding.runOnce({ pool, now, env: ON });
  const line = 'If you haven’t confirmed your email yet, you can resend the link from your account.';
  const u = mailsTo(unverified.email, from)[0];
  const v = mailsTo(verified.email, from)[0];
  assert.ok(u.text.includes(line));
  assert.ok(!v.text.includes(line));
  for (const m of [u, v]) assert.doesNotMatch(m.text + m.html, /verify\?token=/, 'the first-steps mail carried a verify link');
  assert.equal(pool.users.find((x) => x.id === unverified.id).verify_token, null);
});

test('a dry run counts and claims and sends nothing', async () => {
  const now = nextMoment();
  const a = await account(now, 3.5);
  const from = sent.length;
  const r = await onboarding.runOnce({ pool, now, env: ON, dryRun: true });
  assert.equal(r.counts.would_send, 1, JSON.stringify(r.counts));
  assert.equal(sent.length, from);
  assert.equal(claimedAt(a.id), null);
  const lines = [];
  const code = await main(['--dry-run'], { env: ON, now, out: (l) => lines.push(l), db: { backend: () => 'mysql', pool } });
  assert.equal(code, 0);
  assert.match(lines[0], /\(dry run\)/);
  assert.doesNotMatch(lines.join('\n'), /@/, 'the job printed an address');
  assert.equal(claimedAt(a.id), null);
});

test('without a MySQL database the script cannot run and says so', async () => {
  const lines = [];
  const code = await main([], { env: ON, out: (l) => lines.push(l), db: { backend: () => 'memory', pool } });
  assert.equal(code, 2);
  assert.match(lines[0], /no MySQL database/);
});

test('nothing in the web process runs the job: it starts only from its script', () => {
  const { codeOnly } = require('./helpers/code_only');
  const APP = path.join(__dirname, '..');
  const SKIP = new Set(['node_modules', 'test', 'public', 'content', 'data']);
  const callers = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { if (!SKIP.has(e.name)) walk(full); continue; }
      if (!e.name.endsWith('.js')) continue;
      if (/onboarding_mail|email_onboarding_job/.test(codeOnly(fs.readFileSync(full, 'utf8')))) {
        callers.push(path.relative(APP, full));
      }
    }
  }(APP));
  assert.deepEqual(callers, [path.join('scripts', 'email_onboarding_job.js')]);
});

test.after(() => { mailer.isConfigured = realIsConfigured; });
