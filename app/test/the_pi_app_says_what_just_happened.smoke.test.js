'use strict';
/**
 * The Pi Browser app, driven in Chromium, against the owner's submission
 * review of 2026-10-20 (its list of bugs to fix before the Pi hackathon).
 *
 *   - "Position closed." on screen right after OPENING a trade (the recording
 *     at 0:45 and 1:02). A close's note on the arena tab outlived everything
 *     after it, including a trade opened from the Signals tab. A done action
 *     is now a toast that names the position, the next action's toast
 *     replaces it, and a refusal note goes with a change of tab.
 *   - A Pi user with no leaderboard name was sent "to the web". The name is
 *     chosen in the app's Leaders tab; the Pi username is offered, and saved
 *     only on a tap.
 *   - Each Duel receipt is checked in the app: the seal hashed and the daily
 *     root's proof replayed in the browser, against a real seal and a real
 *     proof here, with a tampered payload, a sealed round and an unreadable
 *     receipt each its own sentence.
 *   - Credits, not vUSDT; the season and balance only on the arena tab; a
 *     first screen with the owl; Privacy and Terms in a new tab.
 *
 * The stand-ins are the shared harness's (helpers/pi_app_harness.js).
 */
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');

const {
  pw, CHROMIUM, SKIP, ACCOUNT, DUEL_CARD, LIVE_CALL,
  serve, context, open, signInAndSettle, showTab,
} = require('./helpers/pi_app_harness');

const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const json = (route, status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const rootText = (page) => page.textContent('#root');
const arenaText = (page) => page.textContent('.p-panel[data-tab="arena"]');
/** What the arena tab shows: its header, its panel and any toast over it. */
const arenaScreen = (page) => page.evaluate(() => ['.p-top', '.p-panel[data-tab="arena"]', '.p-toast']
  .map((sel) => (document.querySelector(sel) || {}).textContent || '').join(' | '));

const HELD = { id: 7, symbol: 'BTCUSDT', direction: 'LONG', entry: 64000, mark: 65500, pnl: 46.88, margin: 200, leverage: 10 };

/**
 * An account that changes as the person acts: one position held, gone after
 * the close, and a new one after an open, as the server would answer.
 */
function liveAccount() {
  let positions = [HELD];
  return {
    routes: {
      '/api/arena/account': (route) => json(route, 200, { ...ACCOUNT, positions }),
      '/api/arena/close': (route) => {
        positions = [];
        return json(route, 200, { ok: true, closed: { symbol: 'BTCUSDT', pnl: 46.88, exit_price: 65500, liquidated: false } });
      },
      '/api/arena/open': (route, req) => {
        const b = JSON.parse(req.postData());
        positions = [{ id: 8, symbol: b.symbol + 'USDT', direction: b.direction, entry: 65010, mark: 65010, pnl: 0, margin: b.margin, leverage: b.leverage }];
        return json(route, 200, { ok: true, filled: { symbol: b.symbol + 'USDT', direction: b.direction, entry: 65010, margin: b.margin, leverage: b.leverage } });
      },
      '/api/arena/open-signal': (route) => {
        positions = [{ id: 9, symbol: 'BTCUSDT', direction: 'LONG', entry: 65010, mark: 65010, pnl: 0, margin: 200, leverage: 2 }];
        return json(route, 200, { ok: true, filled: { symbol: 'BTCUSDT', direction: 'LONG', entry: 65010, dropped: [] }, virtual: true });
      },
    },
  };
}

async function fillOpenForm(page, sym, margin, lev) {
  await page.fill('.m-sym', sym);
  await page.fill('.m-margin', String(margin));
  await page.fill('.m-lev', String(lev));
}

test('the Pi app says what just happened, and nothing it did before', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('a close, then a trade opened from Signals: nothing says "closed" over the new position', async () => {
      const live = liveAccount();
      const { ctx } = await context(browser, { routes: live.routes, signals: { status: 200, body: { signals: [LIVE_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);

      await page.click('.m-close[data-close-id="7"]');
      await page.waitForSelector('.p-toast');
      assert.match(await page.textContent('.p-toast'), /^Position closed: BTCUSDT LONG at 65500\. Result \+46\.88 credits\.$/);
      // The close is a toast, not a note that stays on the arena tab.
      assert.strictEqual(await page.$('.p-panel[data-tab="arena"] .m-problem'), null, 'the close left a note on the arena tab');

      // The submission recording: straight to Signals, follow a call, back.
      await showTab(page, 'signals');
      await page.waitForSelector('.p-sig-open');
      await page.click('.p-sig-open');
      await page.waitForFunction(() => /Opened BTCUSDT LONG at 65010/.test((document.querySelector('.p-toast') || {}).textContent || ''));
      await showTab(page, 'arena');
      await page.waitForFunction(() => document.querySelector('.m-pos[data-pos-id="9"]'));
      assert.doesNotMatch(await arenaScreen(page), /closed/i, 'a "closed" notice is on screen over the position just opened');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a trade opened from the form is a toast naming it; the form clears', async () => {
      const live = liveAccount();
      const { ctx, sent } = await context(browser, { routes: live.routes });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.click('.m-close[data-close-id="7"]');
      await page.waitForFunction(() => /Position closed/.test((document.querySelector('.p-toast') || {}).textContent || ''));
      await fillOpenForm(page, 'btc', 100, 2);
      await page.click('.m-long');
      await page.waitForFunction(() => /Position opened/.test((document.querySelector('.p-toast') || {}).textContent || ''));
      assert.strictEqual(await page.textContent('.p-toast'), 'Position opened: BTCUSDT LONG at 65010, 2×.');
      assert.doesNotMatch(await arenaScreen(page), /closed/i, 'the close outlived the open');
      assert.strictEqual(await page.inputValue('.m-sym'), '');
      const opened = sent.find((s) => s.path === '/api/arena/open');
      assert.deepStrictEqual(JSON.parse(opened.body), { symbol: 'BTC', direction: 'LONG', margin: 100, leverage: 2 });
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a refusal stays on the arena tab until the person moves on, in credits', async () => {
      const { ctx } = await context(browser, { routes: {
        '/api/arena/open': (route) => json(route, 400, { error: 'margin must be at least 10 vUSDT', code: 'min_margin' }),
        '/api/arena/close': (route) => json(route, 503, { error: 'No live mark for this symbol — try again shortly' }),
        '/api/arena/account': (route) => json(route, 200, { ...ACCOUNT, positions: [HELD] }),
      } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await fillOpenForm(page, 'btc', 5, 2);
      await page.click('.m-long');
      await page.waitForSelector('.p-panel[data-tab="arena"] .m-problem--warn');
      // The server's code and limit, in the app's unit: no vUSDT in a Pi app.
      assert.strictEqual(await page.textContent('.p-panel[data-tab="arena"] .m-problem--warn'), 'The amount must be at least 10 credits.');
      assert.strictEqual(await page.inputValue('.m-margin'), '5', 'a refused ticket lost what was typed');
      // Any other refusal is the server's own sentence.
      await page.click('.m-close[data-close-id="7"]');
      await page.waitForFunction(() => /No live mark for this symbol/.test(document.querySelector('.p-panel[data-tab="arena"]').textContent));
      // A change of tab is moving on.
      await showTab(page, 'duel');
      await showTab(page, 'arena');
      assert.strictEqual(await page.$('.p-panel[data-tab="arena"] .m-problem--warn'), null, 'a refusal outlived a change of tab');
      assert.doesNotMatch(await rootText(page), /vUSDT/, 'the Pi app printed vUSDT');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('the season and balance head the arena tab only', async () => {
      const { ctx } = await context(browser);
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      assert.ok(await page.$('.p-top .a-season'), 'the arena tab lost the season');
      assert.match(await page.textContent('.p-top .m-acct'), /credits/);
      for (const tab of ['duel', 'signals', 'leaders', 'about']) {
        await showTab(page, tab);
        assert.strictEqual(await page.$('.p-top .a-season'), null, `the season heads the ${tab} tab`);
        assert.strictEqual(await page.$('.p-top .m-acct'), null, `the balance heads the ${tab} tab`);
        assert.ok(await page.$('.p-top .m-refresh'), `Refresh is gone from the ${tab} tab`);
      }
      await ctx.close();
    });

    await t.test('the first screen: the owl, the promise, and the policies in a new tab', async () => {
      const { ctx } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      await page.waitForFunction(() => { const i = document.querySelector('.m-gate-pic'); return i && i.complete && i.naturalWidth > 0; });
      assert.strictEqual(await page.getAttribute('.m-gate-pic', 'src'), '/mascot/runeclaw_owl_round.webp?v=1');
      assert.strictEqual(await page.textContent('.m-gate-title'), 'Beat the Claw');
      assert.strictEqual((await page.$$('.m-gate-points li')).length, 4);
      assert.match(await page.textContent('.m-gate-points'), /Ask the Claw: an AI coach/);
      assert.match(await page.textContent('.m-signin'), /Sign in with Pi/);
      const legal = await page.$$eval('.p-legal a', (as) => as.map((a) => [new URL(a.href).pathname, a.target, a.rel]));
      assert.deepStrictEqual(legal, [['/privacy', '_blank', 'noopener noreferrer'], ['/terms', '_blank', 'noopener noreferrer']]);
      assert.doesNotMatch(await page.textContent('body'), /presale|RCLAW|smithii|vUSDT/i);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});

test('the DogeOS app with no wallet hands over its own link', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  async function noWallet(refuse) {
    const { ctx } = await context(browser);
    await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
    if (refuse) {
      await ctx.addInitScript(() => {
        Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) } });
      });
    }
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`${base}/doge`, { waitUntil: 'load' });
    await page.waitForSelector('.m-signin');
    await page.click('.m-signin');
    await page.waitForSelector('.m-copy-b');
    return { ctx, page, errors };
  }
  try {
    await t.test('X\'s browser has no wallet: the note carries the page\'s link and copies it', async () => {
      const { ctx, page, errors } = await noWallet(false);
      assert.match(await page.textContent('#root'), /No wallet found/);
      assert.strictEqual(await page.inputValue('.m-copy-url'), `${base}/doge`);
      await page.click('.m-copy-b');
      await page.waitForFunction(() => document.querySelector('.m-copy-b').textContent === 'Copied');
      assert.strictEqual(await page.evaluate(() => navigator.clipboard.readText()), `${base}/doge`);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
    await t.test('a clipboard the browser refuses: the link is selected, and the button says to copy it', async () => {
      const { ctx, page, errors } = await noWallet(true);
      await page.click('.m-copy-b');
      await page.waitForFunction(() => /Selected/.test(document.querySelector('.m-copy-b').textContent));
      assert.ok(await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('m-copy-url')));
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});

test('the leaderboard name is chosen in the app, and the Pi username is only offered', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  const SEASON_ROWS = { season: { name: 'October', starts_at: '2026-10-01T00:00:00Z', ends_at: '2026-11-01T00:00:00Z', status: 'live' },
    rows: [{ rank: 1, handle: 'moon_owl', return_pct: 12.5, trades: 4 }], closes_in_window: 4, ranked_total: 1, virtual: true };
  try {
    await t.test('offered, not saved: the box holds the username, and nothing is sent until Save', async () => {
      let handle = null;
      const { ctx, sent } = await context(browser, {
        signin: { status: 200, body: { token: 'session-jwt-1', handle: null, suggested_handle: 'pioneer' } },
        routes: {
          '/api/arena/season': (route) => json(route, 200, { ...SEASON_ROWS,
            rows: handle ? SEASON_ROWS.rows.concat([{ rank: 2, handle, return_pct: 1.5, trades: 1 }]) : SEASON_ROWS.rows }),
          '/api/leaderboard/opt-in': (route, req) => {
            if (req.headers().authorization !== 'Bearer session-jwt-1') return json(route, 401, { error: 'unauthorized' });
            handle = JSON.parse(req.postData()).handle;
            return json(route, 200, { ok: true, handle });
          },
        },
      });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      // The arena's footer sends the person to the tab, not to the web.
      assert.match(await arenaText(page), /No leaderboard name yet\. Choose one in Leaders/);
      assert.doesNotMatch(await rootText(page), /on the web/);
      await page.click('.p-goto[data-tab="leaders"]');
      await page.waitForSelector('.p-panel[data-tab="leaders"]:not([hidden]) .p-name-in');
      assert.strictEqual(await page.inputValue('.p-name-in'), 'pioneer');
      assert.match(await page.textContent('.p-name'), /This is your Pi username\. It is shown only if you save it/);
      assert.ok(!sent.some((s) => s.path === '/api/leaderboard/opt-in'), 'the username was saved without a tap');
      assert.match(await page.textContent('.p-board'), /moon_owl/);
      assert.match(await page.textContent('.p-board'), /\+12\.50%/);

      await page.fill('.p-name-in', 'claw_fan');
      await page.click('.p-name-save');
      await page.waitForFunction(() => /You appear as claw_fan/.test(document.querySelector('.p-name').textContent));
      const saved = sent.filter((s) => s.path === '/api/leaderboard/opt-in');
      assert.strictEqual(saved.length, 1);
      assert.deepStrictEqual(JSON.parse(saved[0].body), { handle: 'claw_fan' });
      await page.waitForFunction(() => /claw_fan/.test(document.querySelector('.p-board').textContent));
      await showTab(page, 'arena');
      assert.match(await arenaText(page), /Trading as claw_fan/);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a taken name is refused in the server\'s words, and what was typed stays', async () => {
      const { ctx } = await context(browser, { routes: {
        '/api/leaderboard/opt-in': (route) => json(route, 409, { error: 'That handle is taken — try another.' }),
      } });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'leaders');
      assert.strictEqual(await page.inputValue('.p-name-in'), '', 'a name was offered that Pi never verified');
      await page.fill('.p-name-in', 'moon_owl');
      await page.click('.p-name-save');
      await page.waitForFunction(() => /That handle is taken/.test(document.querySelector('.p-name').textContent));
      assert.strictEqual(await page.inputValue('.p-name-in'), 'moon_owl');
      await ctx.close();
    });

    await t.test('standings that could not be read are not "nobody yet"', async () => {
      const { ctx } = await context(browser, { routes: {
        '/api/arena/season': (route) => json(route, 500, { error: 'Season unavailable' }),
      } });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'leaders');
      const board = await page.textContent('.p-board');
      assert.match(board, /could not be read/);
      assert.doesNotMatch(board, /Nobody|No trades/);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});

test('a Duel receipt is checked in the app, in the person\'s own browser', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });

  // A real call receipt: the payload as stored, its SHA-256, and a one-step
  // proof to a real root (the sibling on the left).
  const payload = JSON.stringify({ v: 4, kind: 'duel_pick', round_id: 13, day: '2026-10-19', category: 'range',
    symbol: 'BNBUSDT', pick: 'hold', entry_price: 600, picked_at: '2026-10-19T09:00:00.000Z', resolves_at: '2026-10-20T09:00:00.000Z', handle: null });
  const seal = sha(payload);
  const sibling = 'b'.repeat(64);
  const root = sha(sibling + seal);
  const roundSeal = 'a'.repeat(64);
  const called = { ...DUEL_CARD, rounds: [
    { ...DUEL_CARD.rounds[2], callable: false, seal: roundSeal,
      my_call: { pick: 'hold', seal, entry_price: 600, resolves_at: '2026-10-20T09:00:00.000Z' } },
    DUEL_CARD.rounds[0],
  ] };
  const receipt = (over) => ({ seal, sealed_at: '2026-10-19T09:00:00.000Z', seal_payload: payload,
    anchor: { day: '2026-10-19', root, seal_count: 2, proof: [{ pos: 'L', hash: sibling }] }, ...over });

  async function openReceipt(routes, which = 'duel') {
    const { ctx, sent } = await context(browser, { duel: { status: 200, body: called }, routes });
    const { page, errors } = await open(ctx, `${base}/pi`);
    await signInAndSettle(page);
    await showTab(page, 'duel');
    const btn = which === 'duel' ? `.d-rc-v[data-receipt="duel:${seal}"]` : `.d-rc-v[data-receipt="round:${roundSeal}"]`;
    await page.waitForSelector(btn);
    const before = page.url();
    await page.click(btn);
    await page.waitForFunction(() => {
      const s = document.querySelector('.p-rc-sheet');
      return s && !/Checking in your browser/.test(s.textContent);
    });
    assert.strictEqual(page.url(), before, 'checking a receipt left the app');
    return { ctx, sent, page, errors, text: await page.textContent('.p-rc-sheet') };
  }

  try {
    await t.test('an intact receipt is verified and its proof reaches the day\'s root', async () => {
      const o = await openReceipt({ [`/api/call/duel:${seal}`]: (route) => json(route, 200, receipt()) });
      assert.match(o.text, /Verified in your browser/);
      assert.match(o.text, /Your call, as it was recorded/);
      assert.match(o.text, /HOLD on BNBUSDT/);
      assert.match(o.text, /Folded into the 2026-10-19 daily root, one of 2 seals/);
      assert.ok(o.text.includes(root), 'the root is not quoted');
      assert.ok(o.text.includes(seal), 'the seal is not quoted');
      const read = o.sent.find((s) => s.path === `/api/call/duel:${seal}`);
      assert.strictEqual(read.auth, 'Bearer session-jwt-1', 'the receipt was read without the session, so a called round stays sealed');
      // Closing the sheet leaves the card.
      await o.page.click('.p-rc-close');
      assert.strictEqual(await o.page.$('.p-rc-sheet'), null);
      assert.deepStrictEqual(o.errors, []);
      await o.ctx.close();
    });

    await t.test('a payload changed by one character does not match, and says so', async () => {
      const o = await openReceipt({ [`/api/call/duel:${seal}`]: (route) => json(route, 200,
        receipt({ seal_payload: payload.replace('"hold"', '"break"') })) });
      assert.match(o.text, /Does not match/);
      assert.doesNotMatch(o.text, /Verified/);
      await o.ctx.close();
    });

    await t.test('a proof that misses the root is said to', async () => {
      const o = await openReceipt({ [`/api/call/duel:${seal}`]: (route) => json(route, 200,
        receipt({ anchor: { day: '2026-10-19', root: 'c'.repeat(64), seal_count: 2, proof: [{ pos: 'L', hash: sibling }] } })) });
      assert.match(o.text, /Verified in your browser/);
      assert.match(o.text, /proof does not reach the day/);
      await o.ctx.close();
    });

    await t.test('a round still sealed claims nothing either way', async () => {
      const o = await openReceipt({ [`/api/call/round:${roundSeal}`]: (route) => json(route, 200,
        { locked: true, kind: 'duel_round', seal: roundSeal, sealed_at: '2026-10-19T00:00:00.000Z', opens_at: '2026-10-20T00:00:00.000Z' }) }, 'round');
      assert.match(o.text, /Still sealed/);
      assert.match(o.text, /2026-10-20T00:00:00\.000Z/);
      assert.doesNotMatch(o.text, /Verified|Does not match/);
      await o.ctx.close();
    });

    await t.test('a receipt that could not be read is ours, not a mismatch', async () => {
      const o = await openReceipt({ [`/api/call/duel:${seal}`]: (route) => json(route, 503, { error: 'Verify feed unavailable' }) });
      assert.match(o.text, /could not be read just now/);
      assert.doesNotMatch(o.text, /Verified|Does not match/);
      await o.ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
