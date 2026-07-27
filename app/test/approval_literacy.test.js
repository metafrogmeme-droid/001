'use strict';
/**
 * Approval literacy — lesson 05 pairs with the Allowance X-ray the way
 * lesson 04 paired with the Stress Lab, and the firewall's decode points at
 * the after-the-fact half exactly when the calldata grants spending power.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

test('lesson 05 is on the shelf with the canonical footer', () => {
  const { listLessons, getLesson } = require('../lib/learn_lessons');
  const all = listLessons();
  assert.ok(all.some((l) => l.slug === 'approvals-and-delegates'), 'the shelf picked it up');
  const l = getLesson('approvals-and-delegates');
  assert.match(l.title, /Approvals and delegates/);
  assert.match(l.html, /not investment advice/i, 'the canonical footer rides');
  // the honest-limits doctrine is IN the lesson, not just the tools
  assert.match(l.html, /nothing found where we looked/);
  assert.match(l.html, /revoke on exit/);
});

test('the firewall points at /approvals only for approval-family actions', () => {
  const fw = read('public', 'firewall.html');
  assert.match(fw, /APPROVALISH = \{ 'txr\.a_approve': 1, 'txr\.a_increase': 1, 'txr\.a_permit': 1, 'txr\.a_forall_on': 1 \}/,
    'the family is explicit — transfers and unknowns never trigger it');
  assert.match(fw, /if \(hasApproval\) \{/);
  assert.match(fw, /href="\/approvals"/);
  assert.match(fw, /fw\.x_approvals/);
  // forall_off (a REVOKE) is deliberately not in the family
  assert.ok(!fw.includes("'txr.a_forall_off': 1"), 'revokes do not nag');
});

test('fw.x_approvals ships all 14 locales', () => {
  const line = read('public', 'js', 'i18n.js').split('\n')
    .find((l) => l.includes("'fw.x_approvals':"));
  assert.ok(line);
  for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
    'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
    assert.ok(line.includes(loc), `fw.x_approvals has ${loc}`);
  }
  const m = read('public', 'firewall.html').match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 90, 'buster >= 90');
});
