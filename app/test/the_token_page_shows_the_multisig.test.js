'use strict';
/**
 * /token shows the treasury multisig as the record writes it, and invents nothing.
 *
 * The record's multisig block (token/config/rclaw.mainnet.json; the website
 * reads its copy in app/content/) names the vault that holds the treasury, the
 * partnerships bucket and the rest of the reserve, the multisig account, how
 * many members must approve, the time lock, and what the vault holds.
 * token/presale/the_record_states_the_multisig.test.mjs holds those to a
 * snapshot of the chain; this file holds the page to the record, in three
 * values: no block is unreadable, a null block is "not created yet", and a
 * field of a shape the record did not promise is unreadable on its own row.
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
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const UNREADABLE = '<span class="tok-na">unreadable</span>';

/** The section the page paints for the multisig, bounded by its own tags. */
function section(html) {
  const m = html.match(/<section class="tok-card tok-multisig">[\s\S]*?<\/section>/g);
  assert.ok(m && m.length === 1, `${m ? m.length : 0} multisig sections`);
  return m[0];
}

/** The value cell of the row labelled `label`. */
function cell(part, label) {
  const m = part.match(new RegExp('<dt>' + label.replace(/[()]/g, '\\$&') + '</dt><dd>([\\s\\S]*?)</dd>'));
  assert.ok(m, `no ${label} row`);
  return m[1];
}

const link = (a) => '<a href="https://solscan.io/account/' + a + '" target="_blank" rel="noopener"><code>' + a + '</code></a>';

test('the public route carries the multisig without its editor note, and no dollar amount', () => {
  const t = record();
  assert.ok(t.multisig && typeof t.multisig === 'object', 'the multisig block reaches the page');
  assert.ok(!('_comment' in t.multisig), 'the editor note stays in the file');
  assert.ok(!/\$\s?\d/.test(JSON.stringify(t.multisig)), 'a dollar amount on a public route');
});

test('the page paints every field of the record\'s multisig', () => {
  const t = record();
  const ms = t.multisig;
  const s = section(page.render(t));
  const holds = ['<b>' + page.supplyText(ms.holds_tokens) + ' RCLAW</b>']
    .concat(ms.buckets.map((b) => esc(b.bucket) + ' · ' + page.supplyText(b.tokens)));
  assert.strictEqual(cell(s, 'Holds'), holds.join('<br>'));
  assert.strictEqual(cell(s, 'Approvals'), '<b>' + ms.threshold + ' of ' + ms.members.length + ' members</b>');
  assert.strictEqual(ms.time_lock_seconds % 3600, 0, 'the record\'s time lock is whole hours');
  assert.strictEqual(cell(s, 'Time lock'), '<b>' + (ms.time_lock_seconds / 3600) + ' hours</b>');
  assert.strictEqual(cell(s, 'Vault (holds the tokens)'), link(ms.vault));
  assert.strictEqual(cell(s, 'Multisig account'), link(ms.account));
  assert.ok(s.includes('<p class="tok-prose">' + esc(page.MULTISIG_BODY) + '</p>'));
  assert.ok(s.includes('Checked against the chain on ' + ms.verified_at + '.'));
  // The members' wallets are in the record for the verifier; the page names the vault and the account.
  for (const m of ms.members) assert.ok(!s.includes(m), 'a member wallet painted');
});

test('the multisig follows the locks and comes before the bot hint', () => {
  const html = page.render(record());
  const locks = html.indexOf('<section class="tok-card tok-locks">');
  const ms = html.indexOf('<section class="tok-card tok-multisig">');
  const hint = html.indexOf('<p class="tok-bot">');
  assert.ok(locks >= 0 && ms > locks && hint > ms, `${locks} ${ms} ${hint}`);
});

test('three values: no block is unreadable, a null block is "not created yet"', () => {
  const none = record();
  delete none.multisig;
  const s1 = section(page.render(none));
  assert.ok(s1.includes(UNREADABLE));
  assert.ok(!s1.includes('not created yet'), 'an absent block is not a statement that nothing was created');

  const nul = record();
  nul.multisig = null;
  const s2 = section(page.render(nul));
  assert.ok(s2.includes('not created yet'));
  assert.ok(!s2.includes('unreadable'));
});

test('a field of another shape is unreadable on its own row, and the others still print', () => {
  const cases = [
    ['threshold', 4, 'Approvals'], ['threshold', 0, 'Approvals'], ['threshold', '2', 'Approvals'],
    ['members', ['not-an-address'], 'Approvals'], ['members', '3', 'Approvals'],
    ['holds_tokens', '309,997,298', 'Holds'], ['buckets', [], 'Holds'],
    ['buckets', [{ bucket: 'Treasury / DAO', tokens: '200,000,000' }], 'Holds'], ['buckets', [null], 'Holds'],
    ['time_lock_seconds', '48h', 'Time lock'], ['time_lock_seconds', -1, 'Time lock'], ['time_lock_seconds', 1.5, 'Time lock'],
    ['vault', 'not-an-address', 'Vault (holds the tokens)'], ['account', 42, 'Multisig account'],
  ];
  for (const [field, value, label] of cases) {
    const t = record();
    t.multisig[field] = value;
    const s = section(page.render(t));
    assert.strictEqual(cell(s, label), UNREADABLE, `${field}=${JSON.stringify(value)}`);
    assert.ok(!s.includes('not-an-address'), 'a refused address is not printed');
    const other = label === 'Holds' ? 'Multisig account' : 'Holds';
    assert.notStrictEqual(cell(s, other), UNREADABLE, `${field} took ${other} down with it`);
  }
  const missing = record();
  delete missing.multisig.vault;
  assert.strictEqual(cell(section(page.render(missing)), 'Vault (holds the tokens)'), UNREADABLE);
  const notYet = record();
  notYet.multisig.vault = null;
  assert.strictEqual(cell(section(page.render(notYet)), 'Vault (holds the tokens)'), '<span class="tok-na">not created yet</span>');
});

test('the time lock: hours when whole, seconds otherwise, and 0 is a real "none"', () => {
  assert.strictEqual(page.timeLockCell(172800), '<b>48 hours</b>');
  assert.strictEqual(page.timeLockCell(5400), '<b>5400 seconds</b>');
  assert.strictEqual(page.timeLockCell(0), '<span class="tok-na">none: an approved transfer executes at once</span>');
  assert.strictEqual(page.timeLockCell(undefined), UNREADABLE);
});

test('the multisig strings exist in every language, keep their placeholders, and the English ones are the page\'s own', () => {
  const keys = ['tok.ms_h', 'tok.ms_holds', 'tok.ms_approvals', 'tok.ms_quorum', 'tok.ms_timelock', 'tok.ms_hours',
    'tok.ms_seconds', 'tok.ms_no_delay', 'tok.ms_vault', 'tok.ms_account', 'tok.ms_body'];
  const src = fs.readFileSync(path.join(APP, 'public', 'js', 'token-page.js'), 'utf8');
  const holes = (t) => (t.match(/\{[a-z]\}/g) || []).sort().join('');
  for (const k of keys) {
    assert.ok(src.includes("'" + k + "'"), `${k} is not read by the page`);
    for (const l of LANGS) {
      assert.ok(STRINGS[k] && STRINGS[k][l.code], `${k} has no ${l.code}`);
      assert.strictEqual(holes(STRINGS[k][l.code]), holes(STRINGS[k].en), `${k} ${l.code}: placeholders`);
    }
  }
  assert.strictEqual(STRINGS['tok.ms_body'].en, page.MULTISIG_BODY);
  // The English fallbacks the page carries are the English strings.
  for (const k of keys) {
    const m = src.match(new RegExp("say\\('" + k.replace('.', '\\.') + "', '([^']*)'\\)"));
    if (m) assert.strictEqual(m[1], STRINGS[k].en, k);
  }
});
