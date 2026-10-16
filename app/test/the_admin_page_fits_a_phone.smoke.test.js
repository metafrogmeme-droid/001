'use strict';
/**
 * The Admin page fits a phone. Driven in Chromium, because the claim is
 * rendered geometry and no source scan can see it.
 *
 * 2026-10-16, reported from the live site with a screenshot: on a phone the
 * "Safety flags" table ran off the right edge of the screen. Its first two
 * columns are monospace flag names and values, which do not wrap, so the
 * "What it does" column was left a sliver at the edge: one word per line,
 * and the ends of the words cut off by the viewport ("entry's AT", "is
 * refused", "setting, no"). The recent-changes table has six columns and
 * would have done the same.
 *
 * Three claims, each stated against the viewport or the panel rather than a
 * pixel count somebody has to maintain:
 *   1. nothing on the page is wider than the screen;
 *   2. a flag's description reads as a paragraph, not a column;
 *   3. a collapsed cell still names its column.
 * And the other arm: on a desktop the tables stay tables.
 *
 * Needs a Chromium binary. Where there is none it SKIPS and says so — a skip is
 * not a pass, and preflight on a box with the browser runs it for real.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');
const PHONE = { width: 412, height: 900 };            // the reporting phone's CSS width
const NARROW = { width: 360, height: 800 };           // the narrowest common phone
const DESKTOP = { width: 1200, height: 900 };

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM, process.env.PLAYWRIGHT_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    for (const d of fs.readdirSync(root)) {
      if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome'));
    }
  } catch (e) { /* no browsers dir */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}

let pw = null;
try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed'
  : !CHROMIUM ? 'no Chromium binary found (set PLAYWRIGHT_BROWSERS_PATH or RC_SMOKE_CHROMIUM)' : null;

// The flags are the real longest names and the real description from the
// screenshot (bot/core/self_audit.py FLAG_MEANINGS), so the wrap is exercised.
const FLAGS = [
  { name: 'SYMBOL_LOSS_STREAK_THRESHOLD', value: '3', up: 'loosens',
    means: 'consecutive losses on one symbol before that symbol is benched for a long cooldown; raising it benches later (loosens)' },
  { name: 'VOLATILITY_GUARD_ATR_PCT', value: '6.0', up: 'loosens',
    means: "the threshold, in percent of price, above which an entry's ATR is refused; it is a setting, not a reading of current volatility; raising it admits more volatile entries (loosens)" },
  { name: 'REENTRY_COOLDOWN_SECONDS', value: '900', up: 'tightens',
    means: "seconds after a symbol's last real fill before a fresh entry on that symbol is allowed; raising it tightens, 0 turns it off" },
];

const SETTINGS = {
  two_factor: true,
  bot: {
    saved_readable: true,
    settings: [{
      key: 'live_drawdown_override_pct', label: 'Live drawdown cap', kind: 'number',
      means: 'The drawdown from peak at which live entries stop; raising it lets the book fall further before the gate closes (loosens).',
      door: '/drawdownlimit <pct> | reset', up: 'loosens', choices: [], lo: 1, hi: 50, store: 'risk_state',
      value: 12.5, value_error: null, default: null, effective: '12.5% in force', source: 'set',
    }],
    locked: [{ label: 'Live trading', value: 'off', why: 'Turned on and off from Telegram only, so a stolen web session cannot start live trading.' }],
    changes: [{
      at: '2026-10-16T06:40:00Z', key: 'live_drawdown_override_pct', before: 10, after: 12.5,
      effect: 'loosens', via: 'website', actor: 'operator@example.com', persisted: true,
    }],
    flags: FLAGS,
  },
  web: {
    gateway_configured: true,
    findings: [{ level: 'warn', key: 'SMTP',
      msg: 'SMTP_HOST/MAIL_FROM unset — verification and password-reset emails are NOT sent (flows no-op).' }],
  },
};

// Members: the longest shapes the roster holds — a 20-digit id, a long
// display name, a website-only member, every control a row can carry.
const MEMBERS = {
  two_factor: true,
  bot_error: null,
  bot: {
    you: '1001',
    members_total: 640,
    store_started_empty: true,
    web_roles: ['trader', 'paper', 'viewer'],
    web_tiers: ['basic', 'pro', 'elite'],
    telegram_only: ['grant_live', 'grant_admin'],
    members: [
      { id: '1001', name: 'Admin', role: 'admin', tier: 'admin', live: true, authorized: true, admitted_by: null },
      { id: '12345678901234567890', name: 'Maximiliana Konstantinopoulou-Wetherington', role: 'trader',
        tier: 'pro', live: true, authorized: true, admitted_by: '1001' },
      { id: 'web:4242', name: 'w4242', role: 'paper', tier: 'basic', live: null, authorized: true, admitted_by: null },
    ],
    changes: [{
      at: '2026-10-16T06:40:00Z', action: 'set_tier', target: '12345678901234567890',
      before: { role: 'trader', tier: 'basic', live: true }, after: { role: 'trader', tier: 'pro', live: true },
      via: 'website', actor: '1001', held: false,
    }],
  },
};

async function serve() {
  const app = express();
  app.get('/admin', (req, res) => res.sendFile(path.join(PUBLIC, 'admin.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function measure(viewport) {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2 });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      const body = u.pathname === '/api/admin/settings' ? SETTINGS
        : u.pathname === '/api/admin/members' ? MEMBERS : { ok: true, data: {}, rows: [], items: [] };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
    const page = await ctx.newPage();
    await page.goto(`${base}/admin`, { waitUntil: 'load' });
    await page.waitForSelector('#admFlags table.rows', { timeout: 10000 });
    await page.waitForSelector('#admWeb table.rows', { timeout: 10000 });
    await page.waitForSelector('#admMembers table.rows', { timeout: 10000 });
    await page.waitForSelector('#admMemberChanges table.rows', { timeout: 10000 });
    return await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      // Every element's right edge, not only the page's scroll width: a panel
      // with `overflow: hidden` would cut the text off without widening the
      // page, which is the screenshot's failure seen from the other side.
      let widest = null;
      for (const el of document.querySelectorAll('.ad-wrap *')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0) continue;
        if (!widest || r.right > widest.right) {
          widest = { right: r.right, tag: el.tagName, cls: el.className, text: (el.textContent || '').slice(0, 40) };
        }
      }
      const flagPanel = document.querySelector('#admFlags');
      const means = [...document.querySelectorAll('#admFlags tbody td')]
        .filter((td) => /threshold, in percent of price/.test(td.textContent))[0];
      const cells = [...document.querySelectorAll('.ad-wrap table.rows tbody td')];
      const thead = document.querySelector('#admFlags thead');
      return {
        vw,
        scrollW: document.documentElement.scrollWidth,
        widest,
        panelW: flagPanel.getBoundingClientRect().width,
        meansW: means ? means.getBoundingClientRect().width : 0,
        meansRight: means ? means.getBoundingClientRect().right : 0,
        unlabelled: cells.filter((td) => !td.getAttribute('data-label')).map((td) => td.textContent.trim().slice(0, 30)),
        labelShown: means ? getComputedStyle(means, '::before').content : '',
        theadShown: thead ? getComputedStyle(thead).display !== 'none' : false,
        cellDisplay: means ? getComputedStyle(means).display : '',
        tierOpen: (document.querySelector('[data-mtier="12345678901234567890"]') || {}).value || null,
        memberButtons: [...document.querySelectorAll('#admMembers button[data-mact]')]
          .map((b) => `${b.dataset.mid}:${b.dataset.mact}`),
        memberNotes: (document.querySelector('#admMembers') || {}).textContent || '',
        adminRow: ([...document.querySelectorAll('#admMembers tbody tr')]
          .find((tr) => tr.querySelector('td').textContent.trim() === '1001') || {}).textContent || '',
        liveUnread: [...document.querySelectorAll('#admMembers td[data-label="Trading"]')]
          .map((td) => td.textContent.trim()),
      };
    });
  } finally {
    await browser.close();
    server.close();
  }
}

for (const [name, vp] of [['a phone', PHONE], ['a narrow phone', NARROW]]) {
  test(`on ${name} (${vp.width}px) the Admin page fits the screen`, SKIP ? { skip: SKIP } : {}, async (t) => {
    const m = await measure(vp);
    t.diagnostic(`viewport ${m.vw} · scroll ${m.scrollW} · widest ${Math.round(m.widest.right)} `
      + `(${m.widest.tag}.${m.widest.cls}) · panel ${Math.round(m.panelW)} · means ${Math.round(m.meansW)}`);

    // 1. Nothing is wider than the screen.
    assert.ok(m.scrollW <= m.vw + 1, `the page scrolls sideways: ${m.scrollW}px of content in a ${m.vw}px screen`);
    assert.ok(m.widest.right <= m.vw + 1,
      `${m.widest.tag}.${m.widest.cls} ("${m.widest.text}") ends at ${Math.round(m.widest.right)}px, past the ${m.vw}px screen`);

    // 2. The description is a paragraph. In the screenshot it was the last
    //    sliver of a three-column row and wrapped a word per line.
    assert.ok(m.meansW > m.panelW * 0.7,
      `the flag's description got ${Math.round(m.meansW)}px of the panel's ${Math.round(m.panelW)}px — it will wrap a word per line`);

    // 3. A collapsed cell says which column it was.
    assert.deepEqual(m.unlabelled, [], 'a table cell carries no data-label, so on a phone it would be a value with no name');
    assert.match(m.labelShown, /What it does/, 'the collapsed description does not name its column');
  });
}

test('the members panel opens each select on the member\'s value and offers only what the website may do',
  SKIP ? { skip: SKIP } : {}, async () => {
    const m = await measure(PHONE);
    assert.equal(m.tierOpen, 'pro', 'the tier select opened on another tier, so "Set tier" would change it');
    assert.deepEqual(m.memberButtons.sort(), [
      '12345678901234567890:approve', '12345678901234567890:revoke', '12345678901234567890:revoke_live',
      '12345678901234567890:set_tier',
      'web:4242:set_tier',
    ].sort(), 'a row offers a change it should not (an admin account, a web-only id approved) or misses one');
    assert.match(m.adminRow, /Telegram only/, 'the admin row does not say where it is changed');
    assert.match(m.memberNotes, /Showing the newest 3 of 640 members/, 'a cut roster reads as the whole roster');
    assert.match(m.memberNotes, /started empty on this run/, 'an empty start reads as a confident roster');
    assert.deepEqual(m.liveUnread, ['live', 'live', 'not read'], 'an unread live flag rendered as paper');
  });

test('on a desktop the Admin tables stay tables', SKIP ? { skip: SKIP } : {}, async () => {
  const m = await measure(DESKTOP);
  assert.ok(m.theadShown, 'the column headings are hidden on a desktop');
  assert.equal(m.cellDisplay, 'table-cell', 'a desktop cell is laid out as a block');
  assert.ok(m.scrollW <= m.vw + 1, 'the desktop page scrolls sideways');
});
