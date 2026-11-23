'use strict';
/**
 * Every email the website sends is rendered here and checked for what it
 * must and must not say, from the rendered output rather than the copy file.
 *
 * The renders come from app/scripts/email_preview.js, the same list a
 * reviewer reads, so a template added to the copy without a preview fails
 * the first test below rather than shipping unread. They are rendered under
 * a stand-in origin that is NOT the production host, so a link that does
 * not come from the configured origin shows up as one.
 *
 * Structural assertions, not snapshots. The snapshot is the committed
 * preview set, which email_previews_are_current.test.js compares, so the
 * copy a reviewer approved is the copy that ships.
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const T = require('../lib/email_templates');
const preview = require('../scripts/email_preview');

const ORIGIN = 'https://mail-origin.test';
const ALL = preview.renders(ORIGIN);

const CREDIT = 'HUMANOID-TRADERS · RUNECLAW';

const both = (r) => `${r.subject}\n${r.preheader}\n${r.text}\n${r.html}`;

test('every template has a preview, and every preview a template', () => {
  assert.deepEqual([...new Set(ALL.map((r) => r.id))].sort(), [...T.TEMPLATE_IDS].sort());
  assert.ok(ALL.length >= 17, `only ${ALL.length} renders`);
});

test('every render has a subject, a text part, an html part and a hidden preheader', () => {
  for (const { name, rendered: r } of ALL) {
    assert.ok(r.subject.trim(), `${name}: no subject`);
    assert.ok(r.text.trim().length > 80, `${name}: text part is empty`);
    assert.match(r.html, /^<!DOCTYPE html>/, name);
    assert.ok(r.preheader.trim(), `${name}: no preheader`);
    const hidden = /<div style="display:none;[^"]*">([^<]*)<\/div>/.exec(r.html);
    assert.ok(hidden, `${name}: no hidden preheader block`);
    assert.ok(hidden[1].startsWith(T.escapeHtml(r.preheader)), `${name}: preheader is not the hidden text`);
    assert.doesNotMatch(both(r), /\{\{|\}\}/, `${name}: a placeholder was left unfilled`);
  }
});

test('the credit line is exact, in both parts, on every mail', () => {
  for (const { name, rendered: r } of ALL) {
    assert.ok(r.text.includes(CREDIT), `${name}: text lacks the credit line`);
    assert.ok(r.html.includes(T.escapeHtml(CREDIT)), `${name}: html lacks the credit line`);
    assert.doesNotMatch(both(r), /Humanoid Traders · RUNECLAW/, `${name}: the old credit line`);
  }
});

test('an unsubscribe link is on every non-security mail and on no security mail', () => {
  let security = 0;
  let notices = 0;
  for (const { name, id, rendered: r } of ALL) {
    assert.equal(r.security, T.isSecurity(id));
    if (r.security) {
      security += 1;
      assert.doesNotMatch(both(r), /unsubscribe|email-preferences/i, `${name}: a security mail offers an opt-out`);
      assert.ok(r.text.includes(T.COPY.SECURITY_NOTE), `${name}: no security footer`);
    } else {
      notices += 1;
      const url = `${ORIGIN}/email-preferences?token=preview-unsubscribe-token`;
      assert.ok(r.text.includes(`you can unsubscribe: ${url}`), `${name}: text lacks the unsubscribe line`);
      assert.ok(r.html.includes(`href="${url}"`), `${name}: html lacks the unsubscribe link`);
      assert.ok(!r.text.includes(T.COPY.SECURITY_NOTE), `${name}: a notice calls itself a security notice`);
    }
  }
  // Both arms were exercised, so neither branch above passed vacuously.
  assert.ok(security >= 9 && notices >= 3, `${security} security, ${notices} notices`);
});

test('the security mails are exactly the ones the brief names', () => {
  const sec = T.TEMPLATE_IDS.filter((id) => T.isSecurity(id)).sort();
  assert.deepEqual(sec, ['account_deleted', 'password_changed', 'password_reset', 'password_reset_link',
    'password_set', 'totp_disabled', 'totp_enabled', 'verify', 'welcome_verify']);
  assert.deepEqual(T.TEMPLATE_IDS.filter((id) => !T.isSecurity(id)).sort(),
    ['onboarding', 'tier_downgrade', 'tier_upgrade']);
});

test('the footer notes go where the copy says', () => {
  const by = Object.fromEntries(ALL.map((r) => [r.name, r.rendered]));
  assert.ok(by.welcome_verify.text.includes(T.COPY.ADVICE), 'the welcome says it is not advice');
  assert.ok(!by.verify.text.includes(T.COPY.ADVICE));
  assert.ok(by.tier_upgrade_pro.text.includes(T.COPY.NOTICE_NOTE));
  assert.ok(by.tier_downgrade_basic.text.includes(T.COPY.NOTICE_NOTE));
  // The credit line comes after the notes, the unsubscribe line after it.
  const t = by.tier_upgrade_pro.text;
  assert.ok(t.indexOf(T.COPY.NOTICE_NOTE) < t.indexOf(CREDIT));
  assert.ok(t.indexOf(CREDIT) < t.indexOf('you can unsubscribe'));
  const w = by.welcome_verify.text;
  assert.ok(w.indexOf(T.COPY.ADVICE) < w.indexOf(CREDIT));
});

test('every link starts with the configured origin', () => {
  let textLinks = 0;
  for (const { name, rendered: r } of ALL) {
    const attrs = [...r.html.matchAll(/\s(?:href|src)="([^"]*)"/g)].map((m) => m[1]);
    const inText = [...r.text.matchAll(/https?:\/\/[^\s]+/g)].map((m) => m[0]);
    // The logo at least; totp_enabled and account_deleted carry no link.
    assert.ok(attrs.length > 0, `${name}: no links found`);
    textLinks += inText.length;
    for (const u of [...attrs, ...inText]) {
      // The HTML namespace is a name, not a link.
      if (u === 'http://www.w3.org/1999/xhtml') continue;
      assert.ok(u === ORIGIN || u.startsWith(`${ORIGIN}/`), `${name}: ${u} is not under the origin`);
    }
    assert.doesNotMatch(both(r), /humanoid-traders\.com/i, `${name}: names the production host`);
  }
  assert.ok(textLinks > 30, `only ${textLinks} links in the text parts: the scan matched nothing`);
});

test('nothing promises money, names the bot, sells a token, or puts live trading a tap away', () => {
  const FORBIDDEN = [
    [/\$\s?\d/, 'a dollar amount'],
    [/\b\d[\d,.]*\s?(USD|USDT|USDC|EUR|vUSDT)\b/i, 'a currency amount'],
    [/\bprofit/i, 'profit'],
    [/\breturns?\b/i, 'returns'],
    [/\btargets?\b/i, 'a target'],
    [/\b(ROI|APY|APR)\b/, 'a yield figure'],
    [/guarantee/i, 'a guarantee'],
    [/win rate/i, 'a win rate'],
    [/@\w*bot\b/i, 'a bot handle'],
    [/t\.me\//i, 'a t.me link'],
    [/telegram\.me/i, 'a telegram.me link'],
    [/HTRUNECLAW/i, 'the bot handle'],
    [/\$?RCLAW\b/, 'the token'],
    [/\bstak(e|ing)\b/i, 'staking'],
    [/presale|pre-sale|airdrop|token sale/i, 'a token sale'],
    [/one[- ]tap|single tap|instant(ly)? live/i, 'live trading a tap away'],
  ];
  for (const { name, rendered: r } of ALL) {
    const all = both(r);
    for (const [re, what] of FORBIDDEN) {
      assert.doesNotMatch(all, re, `${name} mentions ${what}`);
    }
  }
  // A plan notice says nothing about live trading at all: no plan unlocks it.
  for (const { name, id, rendered: r } of ALL) {
    if (id.startsWith('tier_')) assert.doesNotMatch(both(r), /\blive\b/i, `${name}: a plan notice mentions live`);
  }
});

test('the greeting is "Hello," without a usable name, and names a name when there is one', () => {
  const vars = { base: ORIGIN, verify_url: `${ORIGIN}/verify?token=t`, expires_in: '24 hours' };
  for (const name of [undefined, null, '', '   ']) {
    const r = T.render('verify', { ...vars, name });
    assert.match(r.text, /^Hello,\n/, JSON.stringify(name));
    assert.ok(r.html.includes('>Hello,</p>'));
  }
  const r = T.render('verify', { ...vars, name: 'Ada' });
  assert.match(r.text, /^Hello Ada,\n/);
  assert.ok(r.html.includes('>Hello Ada,</p>'));
});

test('a name, a link or a plan feature can never become markup', () => {
  const evil = '<script>alert("x")</script>\'><img src=x onerror=alert(1)>';
  const r = T.render('tier_upgrade', {
    base: ORIGIN,
    name: evil,
    tier_name: 'Pro',
    features: [evil, 'AI chat: <b>50</b> & "more"'],
    unsubscribe_url: `${ORIGIN}/email-preferences?token="><script>x</script>`,
  });
  assert.doesNotMatch(r.html, /<script|<img src=x|<b>50/i, 'a value reached the HTML unescaped');
  assert.ok(r.html.includes('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&#39;&gt;'));
  assert.ok(r.html.includes('AI chat: &lt;b&gt;50&lt;/b&gt; &amp; &quot;more&quot;'));
  assert.ok(!r.html.includes('token="><'), 'an attribute was broken out of');
  // The text part is plain text: not escaped, so the reader sees the words.
  assert.ok(r.text.includes('- AI chat: <b>50</b> & "more"'));
  assert.ok(r.text.includes(`Hello ${evil},`));
});

test('a missing value throws instead of sending an empty link', () => {
  assert.throws(() => T.render('verify', { base: ORIGIN, expires_in: '24 hours' }), /verify_url/);
  assert.throws(() => T.render('password_changed', { base: ORIGIN }), /when/);
  assert.throws(() => T.render('verify', { verify_url: 'x', expires_in: 'y' }), /origin/);
  assert.throws(() => T.render('tier_upgrade', { base: ORIGIN, tier_name: 'Pro', unsubscribe_url: 'u' }), /features/);
  assert.throws(() => T.render('tier_upgrade', { base: ORIGIN, tier_name: 'Pro', features: [], unsubscribe_url: 'u' }), /features/);
  assert.throws(() => T.render('nope', { base: ORIGIN }), /unknown/);
  assert.throws(() => T.isSecurity('nope'), /unknown/);
  // The other arm: the same calls with the value present render.
  assert.ok(T.render('verify', { base: ORIGIN, verify_url: `${ORIGIN}/v`, expires_in: '24 hours' }).html);
});

test('a non-security mail cannot be rendered without its unsubscribe link', () => {
  for (const id of ['tier_upgrade', 'tier_downgrade', 'onboarding']) {
    assert.throws(() => T.render(id, { base: ORIGIN, tier_name: 'Pro', features: ['x'] }), /unsubscribe/, id);
    assert.throws(() => T.render(id, { base: ORIGIN, tier_name: 'Pro', features: ['x'], unsubscribe_url: '  ' }), /unsubscribe/, id);
  }
  // A security mail ignores one passed to it: it never offers an opt-out.
  const r = T.render('password_changed', { base: ORIGIN, when: 'now', unsubscribe_url: `${ORIGIN}/email-preferences?token=x` });
  assert.doesNotMatch(both(r), /unsubscribe|email-preferences/i);
});

test('a line break can never reach the subject header', () => {
  assert.throws(() => T.render('tier_upgrade', {
    base: ORIGIN, tier_name: 'Pro\r\nBcc: someone@example.com', features: ['x'], unsubscribe_url: 'u',
  }), /line break/);
  assert.throws(() => T.render('verify', { base: ORIGIN, verify_url: 'v', expires_in: '1\nday' }), /line break/);
});

test('inbox HTML: tables and inline styles, no style block, no script, an image with size and alt', () => {
  for (const { name, rendered: r } of ALL) {
    assert.doesNotMatch(r.html, /<style|<script|<link\s|@font-face|<svg/i, name);
    assert.match(r.html, /<table role="presentation"/, name);
    assert.match(r.html, /max-width:600px/, name);
    assert.match(r.html, /<meta name="color-scheme" content="dark light">/, name);
    assert.match(r.html, /<img src="https:\/\/mail-origin\.test\/app_icon_256\.png" width="40" height="40" alt="RUNECLAW"/, name);
    assert.match(r.html, /letter-spacing:0\.12em;[^"]*">RUNECLAW<\/td>/, `${name}: no text wordmark beside the image`);
    // One primary button at most.
    const buttons = (r.html.match(/<td align="center" bgcolor="#3fb6ff"/g) || []).length;
    assert.ok(buttons <= 1, `${name}: ${buttons} primary buttons`);
  }
  // No origin, no image: a relative src is a broken image in an inbox.
  const r = T.render('verify', { base: '', verify_url: '/verify?token=t', expires_in: '24 hours' });
  assert.doesNotMatch(r.html, /<img/);
  assert.match(r.html, />RUNECLAW<\/td>/);
});

test('the time and lifetime sentences come from the values the routes use', () => {
  assert.equal(T.durationText(24 * 60 * 60 * 1000), '24 hours');
  assert.equal(T.durationText(30 * 60 * 1000), '30 minutes');
  assert.equal(T.durationText(60 * 60 * 1000), '1 hour');
  assert.throws(() => T.durationText(0));
  assert.throws(() => T.durationText(1500));
  assert.equal(T.whenText(new Date('2026-10-14T15:32:59Z')), '2026-10-14 15:32 UTC');
  assert.throws(() => T.whenText('not a date'));
});

test('every send in the app goes through auth.js or lib/account_mail.js, and so through the templates', () => {
  // A guard at every call site, which no behaviour test can reach: a new
  // sendMail call elsewhere would skip the templates, the deliverable gate
  // and the opt-out. Comments are stripped, so prose naming the function
  // does not count.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const APP = path.join(__dirname, '..');
  const SKIP_DIRS = new Set(['node_modules', 'test', 'public', 'content', 'data']);
  const callers = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) walk(full); continue; }
      if (!e.name.endsWith('.js')) continue;
      const rel = path.relative(APP, full);
      if (rel === path.join('lib', 'mailer.js')) continue;
      if (/\bsendMail\b/.test(codeOnly(fs.readFileSync(full, 'utf8')))) callers.push(rel);
    }
  }(APP));
  assert.deepEqual(callers.sort(), ['auth.js', path.join('lib', 'account_mail.js')]);
});
