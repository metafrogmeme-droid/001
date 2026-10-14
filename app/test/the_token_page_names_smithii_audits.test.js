'use strict';
/**
 * /token names Smithii's audits as the record lists them, links only to the auditors' own reports,
 * and says what the audits do not cover.
 *
 * The record's smithii_audits block names each audit's auditor, subject, date and report link;
 * token/presale/the_record_names_smithii_audits.test.mjs holds the record and the sentence beside
 * the links to the deploy dates on record. This file holds the page to the record: every audit is
 * printed, a link is printed only when it is Halborn's or CoinFabrik's own report (an "audit" link
 * is a claim a reader acts on), and a block or an item of another shape is unreadable, never blank.
 */

const test = require('node:test');
const assert = require('node:assert');

const rclaw = require('../lib/rclaw_token');
const page = require('../public/js/token-page');

const record = () => JSON.parse(JSON.stringify(rclaw.publicRecord(rclaw.readRecord())));
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const UNREADABLE = '<span class="tok-na">unreadable</span>';

function section(html) {
  const m = html.match(/<section class="tok-card tok-audits">[\s\S]*?<\/section>/g);
  assert.ok(m && m.length === 1, `${m ? m.length : 0} audits sections`);
  return m[0];
}

test('the public route carries the audits without their editor note', () => {
  const t = record();
  assert.ok(t.smithii_audits && Array.isArray(t.smithii_audits.items));
  assert.ok(!('_comment' in t.smithii_audits));
});

test('every audit in the record is printed, linked to its report, with the sentence on what it covers', () => {
  const t = record();
  const s = section(page.render(t));
  for (const it of t.smithii_audits.items) {
    const label = esc(it.auditor) + ' · ' + esc(it.subject) + ' · ' + esc(it.date);
    assert.ok(s.includes('<li><a href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a></li>'), it.url);
  }
  assert.ok(s.includes('<p class="tok-prose">' + esc(page.AUDITS_BODY) + '</p>'));
  const html = page.render(t);
  assert.ok(html.indexOf('tok-multisig') < html.indexOf('tok-audits') && html.indexOf('tok-audits') < html.indexOf('tok-bot'));
});

test('a report link is printed only when it is the auditor\'s own', () => {
  for (const it of record().smithii_audits.items) assert.ok(page.isAuditUrl(it.url), it.url);
  for (const u of [
    'http://www.halborn.com/audits/smithii/vesting-1ae001',
    'https://halborn.com.evil.io/audits/smithii/vesting-1ae001',
    'https://www.halborn.com/audits/other/vesting-1ae001',
    'https://www.halborn.com/audits/smithii/vesting-1ae001?x=1',
    'https://github.com/C0inFabrik/coinfabrik-audit-reports/blob/main/Mantis/06-2024.pdf',
    'https://github.com/CoinFabrik/coinfabrik-audit-reports/blob/main/Mantis/06-2024.pdf.exe',
    'https://raw.githubusercontent.com/CoinFabrik/coinfabrik-audit-reports/main/Mantis/06-2024.pdf',
    'javascript:alert(1)', '', null, 42,
  ]) {
    assert.ok(!page.isAuditUrl(u), JSON.stringify(u));
  }
  const t = record();
  t.smithii_audits.items[0].url = 'https://www.halborn.com.evil.io/audits/smithii/x';
  const s = section(page.render(t));
  assert.ok(!s.includes('evil.io'), 'a refused link is not printed');
  assert.ok(s.includes(esc(t.smithii_audits.items[0].auditor) + ' · ' + esc(t.smithii_audits.items[0].subject)
    + ' · ' + esc(t.smithii_audits.items[0].date) + ' · ' + UNREADABLE), 'the audit is still named, its link unreadable');
});

test('three values: no block or no items is unreadable; an item of another shape is unreadable on its own line', () => {
  const none = record();
  delete none.smithii_audits;
  assert.ok(section(page.render(none)).includes(UNREADABLE));
  const empty = record();
  empty.smithii_audits.items = [];
  assert.ok(section(page.render(empty)).includes(UNREADABLE));
  for (const bad of [null, 'Halborn', { auditor: 'Halborn', subject: '', date: '2025-04', url: 'x' },
    { auditor: 'Halborn', subject: 'Smithii Token Vesting', date: 'April 2025', url: 'x' }]) {
    assert.strictEqual(page.auditItem(bad), '<li>' + UNREADABLE + '</li>', JSON.stringify(bad));
  }
  const one = record();
  one.smithii_audits.items[1] = null;
  const s = section(page.render(one));
  assert.strictEqual((s.match(/<li>/g) || []).length, one.smithii_audits.items.length);
  assert.ok(s.includes('<li>' + UNREADABLE + '</li>'));
  assert.ok(s.includes(esc(one.smithii_audits.items[0].url)), 'the other audits still print');
});
