'use strict';
/**
 * /token shows the time locks as the record writes them, and invents nothing.
 *
 * The record's locks block (token/config/rclaw.mainnet.json; the website reads
 * its copy in app/content/) names, for each vested bucket, its schedule, every
 * unlock, the lock account, the wallet that claims and Smithii's certificate.
 * token/presale/the_record_states_the_locks.test.mjs holds those to the plan
 * and to a snapshot of the chain; this file holds the page to the record.
 * Three values again: a lock not created yet says so, a field of a shape the
 * record did not promise is unreadable, and a certificate link is printed only
 * when it is Smithii's (tests/fixtures/lock_certificate_cases.json, which the
 * bot's /rclaw card is held to as well).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const rclaw = require('../lib/rclaw_token');
const page = require('../public/js/token-page');
const { STRINGS, LANGS } = require('../public/js/i18n.js');

const APP = path.join(__dirname, '..');
const record = () => JSON.parse(JSON.stringify(rclaw.publicRecord(rclaw.readRecord())));
const CASES = JSON.parse(fs.readFileSync(path.join(APP, '..', 'tests', 'fixtures', 'lock_certificate_cases.json'), 'utf8'));
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** The section the page paints for the locks, bounded by its own tags. */
function locksSection(html) {
  const m = html.match(/<section class="tok-card tok-locks">[\s\S]*?<\/section>/);
  assert.ok(m, 'no locks section');
  return m[0];
}

/** One lock's part of the section: from its heading to the next heading or the section's prose. */
function lockPart(section, bucket) {
  const start = section.indexOf('<h3 class="tok-lock-h">' + esc(bucket) + '</h3>');
  assert.ok(start >= 0, `no heading for ${bucket}`);
  const end = section.indexOf('</dl>', start);
  return section.slice(start, end);
}

/** The value cell of the row labelled `label` inside a lock's part. */
function cell(part, label) {
  const m = part.match(new RegExp('<dt>' + label + '</dt><dd>([\\s\\S]*?)</dd>'));
  assert.ok(m, `no ${label} row`);
  return m[1];
}

test('the public route carries the locks without their editor note', () => {
  const t = record();
  assert.ok(t.locks && Array.isArray(t.locks.items), 'the locks block reaches the page');
  assert.ok(!('_comment' in t.locks), 'the editor note stays in the file');
  assert.ok(!/\$\s?\d/.test(JSON.stringify(t.locks)), 'a dollar amount on a public route');
});

test('every lock in the record is painted with its amount, schedule and every unlock', () => {
  const t = record();
  const section = locksSection(page.render(t));
  for (const item of t.locks.items) {
    const part = lockPart(section, item.bucket);
    assert.strictEqual(cell(part, 'Locked'), '<b>' + page.supplyText(item.tokens) + ' RCLAW</b>');
    assert.strictEqual(cell(part, 'Schedule'), esc(item.schedule));
    const unlocks = cell(part, 'Unlocks').split('<br>');
    assert.strictEqual(unlocks.length, item.unlocks.length, item.bucket);
    item.unlocks.forEach((u, i) => {
      assert.strictEqual(unlocks[i], u.at.slice(0, 10) + ' ' + u.at.slice(11, 16) + ' UTC · <b>' + u.percent + '%</b>');
    });
  }
  assert.ok(section.includes(esc(page.LOCKS_BODY)), 'the sentence saying what holds the locks');
  assert.ok(section.includes('Checked against the chain on ' + t.locks.verified_at + '.'));
});

test('a created lock links its account and its certificate; one not created yet says so', () => {
  const t = record();
  const section = locksSection(page.render(t));
  const created = t.locks.items.filter((i) => i.vesting_account !== null);
  assert.ok(created.length >= 2, 'the Community and Team locks exist');
  for (const item of t.locks.items) {
    const part = lockPart(section, item.bucket);
    if (item.vesting_account === null) {
      for (const label of ['Lock account', 'Claiming wallet', 'Certificate']) {
        assert.strictEqual(cell(part, label), '<span class="tok-na">not created yet</span>', `${item.bucket}: ${label}`);
      }
      continue;
    }
    assert.strictEqual(cell(part, 'Lock account'),
      '<a href="https://solscan.io/account/' + item.vesting_account + '" target="_blank" rel="noopener"><code>' + item.vesting_account + '</code></a>');
    assert.strictEqual(cell(part, 'Claiming wallet'), '<code>' + item.claim_wallet + '</code>');
    assert.strictEqual(cell(part, 'Certificate'),
      '<a href="' + item.certificate_url + '" target="_blank" rel="noopener noreferrer">' + item.certificate_url + '</a>');
  }
});

test('a certificate link is printed only when it is Smithii\'s vesting certificate', () => {
  for (const u of CASES.accept) {
    assert.ok(page.isCertificateUrl(u), `refused ${JSON.stringify(u)}`);
    assert.ok(page.certificateCell(u).startsWith('<a href="' + u + '"'), u);
  }
  for (const u of CASES.refuse) {
    assert.ok(!page.isCertificateUrl(u), `accepted ${JSON.stringify(u)}`);
    const html = page.certificateCell(u);
    assert.strictEqual(html, '<span class="tok-na">unreadable</span>', JSON.stringify(u));
  }
  // and every link the record holds is one the page prints
  for (const item of record().locks.items) {
    if (item.certificate_url !== null) assert.ok(page.isCertificateUrl(item.certificate_url), item.bucket);
  }
});

test('three values: no block is unreadable, a null block is "not created yet", a bad field is unreadable', () => {
  const none = record();
  delete none.locks;
  const s1 = locksSection(page.render(none));
  assert.ok(s1.includes('<span class="tok-na">unreadable</span>'));
  assert.ok(!s1.includes('not created yet'), 'an absent block is not a statement that nothing was created');

  const nul = record();
  nul.locks = null;
  const s2 = locksSection(page.render(nul));
  assert.ok(s2.includes('not created yet'));
  assert.ok(!s2.includes('unreadable'));

  const bad = record();
  const item = bad.locks.items.find((i) => i.vesting_account !== null);
  item.vesting_account = 'not-an-address';
  item.claim_wallet = 42;
  item.tokens = '250,000,000';
  item.unlocks[0].at = '2026-10-29';
  const part = lockPart(locksSection(page.render(bad)), item.bucket);
  for (const label of ['Lock account', 'Claiming wallet', 'Locked', 'Unlocks']) {
    assert.strictEqual(cell(part, label), '<span class="tok-na">unreadable</span>', label);
  }
  assert.ok(!part.includes('not-an-address'), 'a refused address is not printed');

  const missing = record();
  const m = missing.locks.items[0];
  delete m.certificate_url;
  delete m.unlocks;
  const mp = lockPart(locksSection(page.render(missing)), m.bucket);
  assert.strictEqual(cell(mp, 'Certificate'), '<span class="tok-na">unreadable</span>');
  assert.strictEqual(cell(mp, 'Unlocks'), '<span class="tok-na">unreadable</span>');
});

test('a percent or an instant of another shape makes the unlock list unreadable, never a partial list', () => {
  for (const bad of [
    [{ at: '2026-10-29T14:00:00Z', percent: 5 }, { at: '2027-07-29T14:00:30Z', percent: 20 }],
    [{ at: '2026-10-29T14:00:00Z', percent: 0 }],
    [{ at: '2026-10-29T14:00:00Z', percent: 101 }],
    [{ at: '2026-10-29T14:00:00Z', percent: '5' }],
    [{ at: '2026-10-29T14:00:00Z', percent: 5.5 }],
    [null],
    [],
    'soon',
  ]) {
    assert.strictEqual(page.unlocksCell(bad), '<span class="tok-na">unreadable</span>', JSON.stringify(bad));
  }
});

test('the lock strings exist in every language, and the English ones are the page\'s own', () => {
  const keys = ['tok.locks_h', 'tok.lock_amount', 'tok.lock_schedule', 'tok.lock_unlocks', 'tok.lock_account',
    'tok.lock_wallet', 'tok.lock_certificate', 'tok.lock_none', 'tok.locks_checked', 'tok.locks_body'];
  const src = fs.readFileSync(path.join(APP, 'public', 'js', 'token-page.js'), 'utf8');
  for (const k of keys) {
    for (const l of LANGS) assert.ok(STRINGS[k] && STRINGS[k][l.code], `${k} has no ${l.code}`);
    assert.ok(src.includes("'" + k + "'"), `${k} is not read by the page`);
  }
  assert.strictEqual(STRINGS['tok.locks_body'].en, page.LOCKS_BODY);
  assert.strictEqual(STRINGS['tok.lock_none'].en, page.LOCK_NONE);
});

test('a lock field still null says "not created yet" on its own, never "unreadable"', () => {
  // Held without the record's help: once every lock exists, no item in the record has a
  // null field, and this arm would go untested.
  const t = record();
  const item = t.locks.items.find((i) => i.vesting_account !== null);
  const labels = { vesting_account: 'Lock account', claim_wallet: 'Claiming wallet', certificate_url: 'Certificate' };
  for (const [key, label] of Object.entries(labels)) {
    const one = record();
    one.locks.items.find((i) => i.bucket === item.bucket)[key] = null;
    const part = lockPart(locksSection(page.render(one)), item.bucket);
    assert.strictEqual(cell(part, label), '<span class="tok-na">not created yet</span>', key);
  }
});

