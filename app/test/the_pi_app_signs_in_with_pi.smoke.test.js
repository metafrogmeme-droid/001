'use strict';
/**
 * The Pi Browser app, driven in Chromium: sign in with Pi, see the arena.
 *
 * Pi's SDK is the one thing a test cannot load (it lives on sdk.minepi.com and
 * talks to the Pi Browser), so its script is answered by a stand-in exposing
 * the two calls the page makes, Pi.init and Pi.authenticate, and recording
 * what it was asked. Our own API is answered by fixtures that record what the
 * page sent. Everything the page itself does is real: the router's HTML, the
 * scripts it loads, and the shared session (arena-session.js).
 *
 * Every outcome is a different sentence, and each is asserted: signed in, Pi
 * refusing the token, Pi unreachable, and no Pi Browser at all. The Farcaster
 * Mini App, which shares the session, is rendered too, so the refactor that
 * made it shared cannot break it unseen.
 */
const test = require('node:test');
const assert = require('node:assert');

const {
  pw, CHROMIUM, SKIP, ACCOUNT, NOW, LIVE_CALL, LOCKED_CALL,
  serve, context, open, signInAndSettle, showTab, readAgain,
} = require('./helpers/pi_app_harness');

test('the Pi app signs in with Pi and shows the arena, and says why when it cannot', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('signed out: the gate names Pi, and there is no presale anywhere on it', async () => {
      const { ctx } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      assert.match(await page.textContent('.m-signin'), /Sign in with Pi/);
      assert.strictEqual(await page.$('#presale-bar'), null);
      assert.doesNotMatch(await page.textContent('body'), /presale|RCLAW|smithii/i);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('signing in sends Pi\'s token, not a uid, and the arena opens on the session', async () => {
      const { ctx, sent } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      const text = await signInAndSettle(page);
      const signin = sent.find((s) => s.path === '/api/pi/signin');
      assert.ok(signin, 'the page never asked our server');
      assert.deepStrictEqual(JSON.parse(signin.body), { accessToken: 'piAccessToken_fromTheStandInSdk123' });
      const acct = sent.find((s) => s.path === '/api/arena/account');
      assert.strictEqual(acct && acct.auth, 'Bearer session-jwt-1');
      assert.match(text, /No leaderboard name yet\. Choose one in Leaders/);
      const pi = await page.evaluate(() => window.__pi);
      assert.deepStrictEqual(pi.inits, [{ version: '2.0' }], 'init got more than its version');
      assert.strictEqual(pi.authBeforeInit, false, 'authenticate ran before init had finished');
      assert.deepStrictEqual(pi.scopes, ['username', 'payments'], 'more was asked of the person than the app uses');
      assert.strictEqual(pi.hasCallback, true);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a refused close says why, and an answer with no reason still names its status', async () => {
      // 2026-10-17: the live Arena could read no price, refused every close,
      // and the app said only "The position could not be closed."
      const holding = { ...ACCOUNT, positions: [{ id: 7, symbol: 'ETHUSDT', direction: 'LONG', entry: 2433.92,
        mark: null, margin: 1000, leverage: 10, pnl: null }] };
      const cases = [
        [{ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'No live mark for this symbol — try again shortly' }) },
          /No live mark for this symbol — try again shortly/],
        [{ status: 502, contentType: 'text/html', body: '<html><body>Bad gateway</body></html>' },
          /The position could not be closed \(the server answered 502\)\./],
      ];
      for (const [close, want] of cases) {
        const { ctx, sent } = await context(browser, { account: holding, close });
        const { page, errors } = await open(ctx, `${base}/pi`);
        assert.match(await signInAndSettle(page), /mark unavailable/);
        await page.click('.m-close[data-close-id="7"]');
        await page.waitForFunction((re) => new RegExp(re).test(document.querySelector('#root').textContent), want.source);
        const asked = sent.find((x) => x.path === '/api/arena/close');
        assert.deepStrictEqual(JSON.parse(asked.body), { position_id: 7 });
        assert.deepStrictEqual(errors, []);
        await ctx.close();
      }
    });

    await t.test('a token Pi refused is named as that', async () => {
      const { ctx } = await context(browser, { signin: { status: 401, body: { error: 'pi_rejected', reason: 'token_rejected' } } });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /Pi did not confirm that sign-in/);
      await ctx.close();
    });

    await t.test('Pi unreachable is ours, not the person\'s', async () => {
      const { ctx } = await context(browser, { signin: { status: 503, body: { error: 'verifier_unavailable' } } });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /unavailable right now — this is on our side/);
      await ctx.close();
    });

    await t.test('outside the Pi Browser, it says where to open it', async () => {
      const { ctx, sent } = await context(browser, { sdk: false });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /Open this in the Pi Browser to sign in/);
      assert.ok(!sent.some((s) => s.path === '/api/pi/signin'), 'a sign-in was attempted with no SDK');
      await ctx.close();
    });

    await t.test('a tip pays exactly the order the server issued, and is settled by id', async () => {
      const { ctx, sent } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      // The tip lives in About, off the trading screen.
      assert.strictEqual(await page.isVisible('.p-tip'), false, 'the tip is on the trading screen again');
      await showTab(page, 'about');
      await page.waitForSelector('.p-tip');
      const labels = await page.$$eval('.p-tip', (bs) => bs.map((b) => b.textContent));
      assert.deepStrictEqual(labels, ['1 π', '3 π', '5 π']);
      await page.click('.p-tip[data-tip="3"]');
      await page.waitForFunction(() => /your tip arrived/.test(document.querySelector('#root').textContent));
      const order = sent.find((s) => s.path === '/api/pi/payments/tips' && s.method === 'POST');
      assert.deepStrictEqual(JSON.parse(order.body), { amount: 3 });
      assert.strictEqual(order.auth, 'Bearer session-jwt-1');
      const pi = await page.evaluate(() => window.__pi);
      assert.deepStrictEqual(pi.payments, [{ amount: 3, memo: 'Support RUNECLAW', metadata: { orderId: 'order-from-server', kind: 'tip' } }],
        'createPayment got something other than the server\'s order');
      const approve = sent.find((s) => s.path === '/api/pi/payments/approve');
      assert.deepStrictEqual(JSON.parse(approve.body), { paymentId: 'pay-123' });
      const done = sent.find((s) => s.path === '/api/pi/payments/complete');
      assert.deepStrictEqual(JSON.parse(done.body), { paymentId: 'pay-123', txid: 'abc123txid' });
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a payment left unfinished is finished after sign-in, by its id alone', async () => {
      const { ctx, sent } = await context(browser, { unfinished: 'pay-old' });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForFunction(() => /earlier tip was completed/.test(document.querySelector('#root').textContent));
      const inc = sent.find((s) => s.path === '/api/pi/payments/incomplete');
      assert.deepStrictEqual(JSON.parse(inc.body), { paymentId: 'pay-old' }, 'only the id; the server reads the rest from Pi');
      assert.strictEqual(inc.auth, 'Bearer session-jwt-1');
      await ctx.close();
    });

    await t.test('a cancelled tip says nothing was paid, and tips switched off show nothing', async () => {
      const { ctx } = await context(browser, { cancel: true });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'about');
      await page.waitForSelector('.p-tip');
      await page.click('.p-tip[data-tip="1"]');
      await page.waitForFunction(() => /Nothing was paid/.test(document.querySelector('#root').textContent));
      await ctx.close();

      const off = await context(browser, { tips: { enabled: false } });
      const o = await open(off.ctx, `${base}/pi`);
      const text = await signInAndSettle(o.page);
      await o.page.waitForTimeout(200);
      assert.strictEqual(await o.page.$('.p-tip'), null);
      assert.doesNotMatch(await o.page.textContent('#root'), /Support RUNECLAW|Tips could not/);
      assert.match(text, /No leaderboard name yet\. Choose one in Leaders/);
      await off.ctx.close();
    });

    await t.test('signed in, the engine\'s calls show with the site\'s chart, and one opens on paper', async () => {
      const { ctx, sent } = await context(browser, { signals: { status: 200, body: { signals: [LIVE_CALL, LOCKED_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .e-row');
      assert.match(await page.textContent('.p-signals .e-row--locked'), /Sign in again to see its side and levels/);
      const card = await page.textContent('.p-signals .e-row:not(.e-row--locked)');
      assert.match(card, /LONG/);
      assert.match(card, /BTC/);
      // The site's TradingView chart, drawn from our own candle relay.
      await page.waitForSelector('.p-signals .e-chart canvas', { timeout: 5000 });
      const sig = sent.find((s) => s.path === '/api/signals');
      assert.strictEqual(sig.auth, 'Bearer session-jwt-1', 'signals were read anonymously, so live calls would be locked');
      assert.ok(sent.some((s) => s.path.startsWith('/api/market/candles/BTCUSDT')), 'the chart asked about the wrong market');

      await page.click('.p-sig-open');
      await page.waitForFunction(() => /Opened BTCUSDT LONG at 65010/.test(document.querySelector('#root').textContent));
      const opened = sent.find((s) => s.path === '/api/arena/open-signal');
      assert.deepStrictEqual(JSON.parse(opened.body), { signal_key: 'k-live-1', margin: 200, leverage: 2 });
      assert.strictEqual(opened.auth, 'Bearer session-jwt-1');

      // Nothing on the screen links back to the site, which carries the
      // presale, except our two policy pages (built from site/, no presale and
      // no script: the_pi_app_is_the_arena_and_nothing_else.test.js), each in
      // a new tab so the in-memory session survives.
      const own = await page.$$eval('#root a[href]', (as) => as
        .filter((a) => new URL(a.href, location.href).origin === location.origin)
        .map((a) => new URL(a.href).pathname + ' ' + a.target));
      assert.deepStrictEqual(own.sort(), ['/privacy _blank', '/terms _blank']);
      assert.doesNotMatch(await page.textContent('body'), /presale|RCLAW|smithii/i);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    // ── the signal cards: one door per call ─────────────────────────────

    const CLOSED_CALL = { ...LIVE_CALL, signal_key: 'k-closed', symbol: 'SOL/USDT:USDT', entry_price: 150,
      stop_loss: 140, take_profit: 170, created_at: new Date(NOW - 30 * 60000).toISOString(),
      expires_at: new Date(NOW - 25 * 60000).toISOString() };
    const ENDED_CALL = { ...LIVE_CALL, signal_key: 'k-ended', symbol: 'XRP/USDT:USDT', status: 'TARGET',
      created_at: new Date(NOW - 90 * 60000).toISOString(), expires_at: new Date(NOW - 85 * 60000).toISOString() };
    const cardOf = (page, key) => page.evaluate((k) => {
      const act = document.querySelector('.p-sig-act[data-key="' + k + '"]');
      const card = act && act.closest('.e-row');
      return act && { act: act.textContent, card: card && card.textContent,
        follow: !!act.querySelector('.p-sig-open'), own: !!act.querySelector('.p-sig-own'),
        tone: (act.querySelector('.p-sig-out') || {}).className || null };
    }, key);

    await t.test('a live call is followed, a closed window is your own trade, an ended call offers nothing', async () => {
      const { ctx, sent } = await context(browser, {
        signals: { status: 200, body: { signals: [LIVE_CALL, CLOSED_CALL, ENDED_CALL] } },
        openSignal: { status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, filled: {
          symbol: 'SOLUSDT', direction: 'LONG', entry: 153.75, own: true, dropped: [],
          signal_entry: 150, drift_pct: 2.5, drift_warn: true }, virtual: true }) },
      });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-sig-act[data-key="k-ended"]');

      const live = await cardOf(page, 'k-live-1');
      assert.deepStrictEqual([live.follow, live.own], [true, false]);
      assert.match(live.act, /^Live call · follow it within \d+:\d\dPaper 200 · 2×$/);
      const closed = await cardOf(page, 'k-closed');
      assert.deepStrictEqual([closed.follow, closed.own], [false, true]);
      assert.match(closed.act, /^Its window closed at \d\d:\d\d\. Taking it now is your own trade at the live price, not credited to the call\.Trade it yourself · 200 · 2×$/);
      const ended = await cardOf(page, 'k-ended');
      assert.deepStrictEqual([ended.follow, ended.own], [false, false]);
      assert.strictEqual(ended.act, '✓ TARGET This call has ended.');
      assert.strictEqual(ended.tone, 'p-sig-out p-sig-out--up');

      await page.click('.p-sig-own');
      await page.waitForFunction(() => /as your own trade/.test(document.querySelector('#root').textContent));
      const opened = sent.find((s) => s.path === '/api/arena/open-signal');
      assert.deepStrictEqual(JSON.parse(opened.body), { signal_key: 'k-closed', margin: 200, leverage: 2, own: true });
      assert.match(await page.textContent('#root'),
        /Opened SOLUSDT LONG at 153\.75, the live mark, as your own trade\. It is not credited to the call\. That is \+2\.5% from the call’s entry of 150\./);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a follow refused because the window just closed reads the calls again and offers the own trade', async () => {
      let closedNow = false;
      let reads = 0;
      const { ctx } = await context(browser, {
        signals: { status: 200, body: { signals: [LIVE_CALL] } },
        openSignal: { status: 400, contentType: 'application/json', body: JSON.stringify({ code: 'expired',
          error: 'That call is no longer live — its window closed at 12:05 UTC, and the engine no longer stands behind it. Open a current one.' }) },
      });
      // The server's clock says the window closed; the next read of the calls agrees.
      // (Registered after context()'s, so it answers /api/signals instead: it counts its own reads.)
      await ctx.route('**/api/signals*', (route) => {
        reads += 1;
        return route.fulfill({ status: 200, contentType: 'application/json',
          body: JSON.stringify({ signals: [closedNow ? { ...LIVE_CALL, expires_at: new Date(Date.now() - 1000).toISOString() } : LIVE_CALL] }) });
      });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-sig-open');
      closedNow = true;
      await page.click('.p-sig-open');
      await page.waitForSelector('.p-sig-own', { timeout: 5000 });
      assert.match(await page.textContent('#root'), /no longer live — its window closed at 12:05 UTC/);
      assert.strictEqual(reads, 2, 'the calls were not read again after the refusal');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a window closing on screen turns its card, and redraws neither the chart nor the ticket', async () => {
      const soon = { ...LIVE_CALL, signal_key: 'k-soon', expires_at: new Date(Date.now() + 6000).toISOString() };
      const { ctx } = await context(browser, { signals: { status: 200, body: { signals: [soon] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .sc-tv canvas', { timeout: 5000 });
      const first = (await cardOf(page, 'k-soon')).act;
      // The ticket is on the Arena tab; the chart stays drawn on the Signals
      // tab behind it, and the window closing must turn the card in place.
      await showTab(page, 'arena');
      await page.evaluate(() => { document.querySelector('.p-signals .sc-tv').dataset.before = '1'; });
      await page.fill('.m-sym', 'eth');
      await page.fill('.m-margin', '250');
      await page.waitForTimeout(1300);
      assert.notStrictEqual((await cardOf(page, 'k-soon')).act, first, 'the countdown did not tick');

      await page.waitForSelector('.p-sig-own', { timeout: 9000, state: 'attached' });
      assert.match((await cardOf(page, 'k-soon')).act, /Its window closed at/);
      assert.ok(await page.$('.p-signals .sc-tv[data-before="1"]'), 'the chart was redrawn to turn a button');
      assert.strictEqual(await page.inputValue('.m-sym'), 'eth');
      assert.strictEqual(await page.inputValue('.m-margin'), '250');
      assert.strictEqual(await page.evaluate(() => document.activeElement && document.activeElement.className), 'm-margin',
        'the keyboard was taken away');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a refused ticket keeps what was typed; a filled one clears it', async () => {
      const { ctx } = await context(browser, { signals: { status: 200, body: { signals: [] } } });
      await ctx.route('**/api/arena/open', (route) => route.fulfill(opened
        ? { status: 200, contentType: 'application/json', body: '{"ok":true,"filled":{}}' }
        : { status: 400, contentType: 'application/json', body: '{"error":"Margin must be at least 10 vUSDT"}' }));
      let opened = false;
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.fill('.m-sym', 'btc');
      await page.fill('.m-margin', '5');
      await page.fill('.m-lev', '3');
      await page.click('.m-long');
      await page.waitForFunction(() => /Margin must be at least 10 vUSDT/.test(document.querySelector('#root').textContent));
      assert.deepStrictEqual(await page.$$eval('.m-sym, .m-margin, .m-lev', (els) => els.map((e) => e.value)), ['btc', '5', '3'],
        'a refusal wiped the ticket it was explaining');
      opened = true;
      await page.fill('.m-margin', '50');
      await page.click('.m-long');
      await page.waitForFunction(() => /Position opened\./.test(document.querySelector('#root').textContent));
      assert.deepStrictEqual(await page.$$eval('.m-sym, .m-margin, .m-lev', (els) => els.map((e) => e.value)), ['', '', '']);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('the charts take the website\'s timeframes, pinch and drag, and keep a reader\'s zoom', async () => {
      const { ctx, sent } = await context(browser, { signals: { status: 200, body: { signals: [LIVE_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      // Record what each chart was built with, and the charts themselves.
      // The library's exports are read-only getters, so the stand-in is a copy
      // of the library with a recording createChart; tv-chart.js reads
      // window.LightweightCharts when it draws.
      await page.evaluate(() => {
        window.__charts = [];
        const L = window.LightweightCharts;
        window.LightweightCharts = Object.assign({}, L, {
          createChart(el, o) { const c = L.createChart(el, o); window.__charts.push({ o, c }); return c; },
        });
      });
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .sc-tv canvas', { timeout: 5000 });
      assert.deepStrictEqual(await page.$$eval('.p-tf-b', (bs) => bs.map((b) => [b.textContent, b.getAttribute('aria-pressed')])),
        [['15m', 'false'], ['1H', 'true'], ['4H', 'false'], ['1D', 'false']]);
      const first = sent.filter((s) => s.path.startsWith('/api/market/candles/BTCUSDT'));
      assert.deepStrictEqual(first.map((s) => s.query), ['?granularity=1h&limit=200']);

      const o = await page.evaluate(() => {
        const x = window.__charts[window.__charts.length - 1].o;
        return { scroll: x.handleScroll, scale: x.handleScale, time: x.timeScale.visible };
      });
      assert.deepStrictEqual(o.scroll, { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        'a vertical drag on a chart must scroll the app, a horizontal one the chart');
      assert.strictEqual(o.scale.pinch, true);
      assert.strictEqual(o.time, true);
      const range = () => page.evaluate(() => {
        const r = window.__charts[window.__charts.length - 1].c.timeScale().getVisibleLogicalRange();
        return [Math.round(r.from), Math.round(r.to)];
      });
      assert.deepStrictEqual(await range(), [140, 201], 'the first view is the newest 60 bars of 200');

      // A reader zooms in; a render of the whole screen must not undo it.
      await page.evaluate(() => window.__charts[window.__charts.length - 1].c.timeScale().setVisibleLogicalRange({ from: 170, to: 190 }));
      await page.waitForTimeout(150);
      const before = await page.evaluate(() => window.__charts.length);
      await showTab(page, 'arena');
      await page.click('.m-long');            // an empty ticket: refused, and the screen is rendered again
      await page.waitForFunction((n) => window.__charts.length > n, before);
      await showTab(page, 'signals');
      await page.waitForTimeout(150);
      assert.deepStrictEqual(await range(), [170, 190], 'a redraw threw the reader\'s zoom away');

      await page.click('.p-tf-b[data-tf="4h"]');
      await page.waitForFunction(() => document.querySelector('.p-tf-b[data-tf="4h"]').getAttribute('aria-pressed') === 'true');
      await page.waitForFunction(() => /granularity=4h/.test(performance.getEntriesByType('resource').map((e) => e.name).join(' ')));
      assert.ok(sent.some((s) => s.path.startsWith('/api/market/candles/BTCUSDT') && s.query === '?granularity=4h&limit=200'));
      await page.waitForTimeout(150);
      assert.deepStrictEqual(await range(), [140, 201], 'a new timeframe opens on its own newest bars');

      // The choice is the reader's next time too.
      await page.reload({ waitUntil: 'load' });
      await page.waitForSelector('.m-signin');
      const at = sent.length;
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .sc-tv canvas', { timeout: 5000 });
      assert.deepStrictEqual(sent.slice(at).filter((s) => s.path.startsWith('/api/market/candles/')).map((s) => s.query),
        ['?granularity=4h&limit=200']);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    // ── the Refresh button ───────────────────────────────────────────────

    await t.test('Refresh reads the account, the calls and their bars again, and says when', async () => {
      // A short on a coin under a dollar, whose mark moves between two reads.
      let mark = 0.1481;
      let accountReads = 0;
      const { ctx, sent } = await context(browser, { signals: { status: 200, body: { signals: [LIVE_CALL] } } });
      await ctx.route('**/api/arena/account', (route) => {
        accountReads += 1;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...ACCOUNT,
          positions: [{ id: 3, symbol: 'FARTCOINUSDT', direction: 'SHORT', entry: 0.14945, mark,
            margin: 200, leverage: 2, pnl: 1.94 }] }) });
      });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .sc-tv canvas', { timeout: 5000 });
      assert.match(await page.textContent('.m-updated'), /^Updated \d\d:\d\d:\d\d$/);
      assert.strictEqual((await page.textContent('.m-refresh')).trim(), '↻ Refresh');
      assert.match(await page.textContent('.m-pos'), /entry 0\.149450.*mark 0\.148100/s,
        'a coin under a dollar was printed to two decimals');
      await showTab(page, 'arena');
      await page.fill('.m-sym', 'eth');

      const before = { account: accountReads, signals: sent.filter((x) => x.path === '/api/signals').length,
        candles: sent.filter((x) => x.path.startsWith('/api/market/candles/')).length };
      mark = 0.1470;
      await page.click('.m-refresh');
      await page.waitForFunction(() => /mark 0\.147000/.test(document.querySelector('.m-pos').textContent));
      await page.waitForFunction(() => {
        const b = document.querySelector('.m-refresh');
        return b && !b.disabled && b.textContent.trim() === '↻ Refresh';
      });
      assert.strictEqual(accountReads, before.account + 1, 'the account was not read again');
      assert.ok(sent.filter((x) => x.path === '/api/signals').length > before.signals, 'the calls were not read again');
      // The charts are drawn again by the render that brings the button back,
      // and Playwright hands their requests to this process a moment after the
      // page has them: wait for the bars, bounded, rather than racing the event.
      const barsReadAgain = async () => {
        for (const t0 = Date.now(); Date.now() - t0 < 5000; await new Promise((r) => setTimeout(r, 25))) {
          if (sent.filter((x) => x.path.startsWith('/api/market/candles/')).length > before.candles) return true;
        }
        return false;
      };
      assert.ok(await barsReadAgain(), 'the charts kept their old bars');
      assert.strictEqual(await page.inputValue('.m-sym'), 'eth', 'Refresh wiped the ticket');

      // Back in front after being put away, the account is read again by itself.
      const n = accountReads;
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
        document.dispatchEvent(new Event('visibilitychange'));
      });
      for (let i = 0; i < 60 && accountReads === n; i++) await page.waitForTimeout(50);
      assert.strictEqual(accountReads, n + 1, 'coming back to the app did not read the account');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('an account that could not be read offers Refresh, and Refresh recovers it', async () => {
      let reads = 0;
      const { ctx } = await context(browser, { signals: { status: 200, body: { signals: [] } } });
      await ctx.route('**/api/arena/account', (route) => {
        reads += 1;
        return reads === 1
          ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Arena unavailable"}' })
          : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ACCOUNT) });
      });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForFunction(() => /could not be loaded/.test(document.querySelector('#root').textContent));
      await page.click('.m-refresh');
      await page.waitForFunction(() => /No leaderboard name yet/.test(document.querySelector('#root').textContent));
      assert.doesNotMatch(await page.textContent('#root'), /could not be loaded/);
      assert.strictEqual(reads, 2);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('signals that cannot be read say so, and none is not an error', async () => {
      const down = await context(browser, { signals: { status: 503, body: { error: 'signal_stream_unavailable' } } });
      const d = await open(down.ctx, `${base}/pi`);
      await signInAndSettle(d.page);
      await d.page.waitForFunction(() => /Signals could not be loaded/.test(document.querySelector('#root').textContent));
      await down.ctx.close();

      const none = await context(browser, { signals: { status: 200, body: { signals: [] } } });
      const n = await open(none.ctx, `${base}/pi`);
      await signInAndSettle(n.page);
      await n.page.waitForFunction(() => /No signals right now/.test(document.querySelector('#root').textContent));
      assert.doesNotMatch(await n.page.textContent('#root'), /could not be loaded/);
      await none.ctx.close();
    });

    await t.test('signed in, the Daily Duel shows every category with its own buttons, and a tap records a call', async () => {
      const { ctx, sent } = await context(browser, { signals: { status: 200, body: { signals: [] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      // One tab at a time: the duel waits behind its tab, which counts the open rounds.
      assert.strictEqual(await page.$eval('.p-panel[data-tab="duel"]', (e) => e.hidden), true);
      assert.match(await page.textContent('.p-tab-b[data-tab="duel"]'), /Duel5$/, 'the badge is the rounds still open to a call');
      await showTab(page, 'duel');
      assert.strictEqual(await page.$eval('.p-panel[data-tab="arena"]', (e) => e.hidden), true);
      await page.waitForSelector('.p-duel .d-round');
      assert.match(await page.textContent('.p-duel-prog'), /0 of 5 called.*5 open/s, 'the progress line counts calls, not rounds');
      const today = sent.find((s) => s.path === '/api/duel/today');
      assert.strictEqual(today && today.auth, 'Bearer session-jwt-1', 'the card was read without the session');
      const kinds = await page.$$eval('.p-duel .d-round .d-kind', (els) => els.map((e) => e.textContent.replace(/^[^A-Za-z]+/, '')));
      assert.deepStrictEqual(kinds, ['Direction', 'Back or fade', 'Range', 'Bigger mover', 'Football']);
      const text = await page.textContent('.p-duel');
      assert.match(text, /Arsenal vs Chelsea — who wins\? Premier League/);
      assert.match(text, /Higher or lower in 24h\?/);
      assert.match(text, /The Claw called SHORT on SOL\. Back it, or fade it\?/);
      assert.match(text, /Does BNB move more than ±2% either way in 24h\?/);
      assert.match(text, /XRP or ARB — which moves more in 24h, either way\?/);
      // Each round offers its own words, in its own order.
      const picks = await page.$$eval('.p-duel .d-actions', (els) => els.map((e) => e.getAttribute('data-round') + ':'
        + [...e.querySelectorAll('.d-btn')].map((b) => b.getAttribute('data-pick')).join(',')));
      assert.deepStrictEqual(picks, ['11:long,short,pass', '12:back,fade,pass', '13:break,hold,pass', '14:first,second,pass',
        '15:home,draw,away,pass']);
      // The record: a dash before anything has settled, never 0%, with what is still running.
      const rec = await page.textContent('.p-duel-rec');
      assert.match(rec, /Accuracy/);
      assert.match(rec, /—/);
      assert.doesNotMatch(rec, /0%/);
      assert.match(rec, /2 still running/);
      assert.match(rec, /🔥 2/);
      // A tap posts the round and the word, with the session, and the reply is shown as sent.
      await page.click('.p-duel .d-actions[data-round="13"] .d-btn[data-pick="hold"]');
      await page.waitForFunction(() => /Call recorded: HOLDS ±2% on BNB at 600/.test(document.querySelector('#root').textContent));
      const pick = sent.find((s) => s.path === '/api/duel/pick');
      assert.deepStrictEqual(JSON.parse(pick.body), { round_id: 13, pick: 'hold' });
      assert.strictEqual(pick.auth, 'Bearer session-jwt-1');
      await readAgain(sent, '/api/duel/today', 2, 'the card was not read again after the call');
      // Nothing in the block links back to the site.
      assert.strictEqual(await page.$('.p-duel a[href]'), null);
      assert.match(text, /Dealt sealed aaaaaaaa…/, 'the round’s commitment is quoted');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
    await t.test('a duel refusal is shown in the server\'s words, and an unreadable card is said to be', async () => {
      const refused = await context(browser, { signals: { status: 200, body: { signals: [] } },
        duelPick: { status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'You have already called this round.' }) } });
      const r = await open(refused.ctx, `${base}/pi`);
      await signInAndSettle(r.page);
      await showTab(r.page, 'duel');
      await r.page.waitForSelector('.p-duel .d-actions[data-round="11"]');
      await r.page.click('.p-duel .d-actions[data-round="11"] .d-btn[data-pick="short"]');
      await r.page.waitForFunction(() => /You have already called this round\./.test(document.querySelector('#root').textContent));
      assert.doesNotMatch(await r.page.textContent('.p-duel'), /Call recorded/);
      await refused.ctx.close();
      const down = await context(browser, { signals: { status: 200, body: { signals: [] } },
        duel: { status: 503, body: { error: 'The day\'s rounds are unavailable', reason: 'prices_unreadable' } } });
      const d = await open(down.ctx, `${base}/pi`);
      await signInAndSettle(d.page);
      await showTab(d.page, 'duel');
      await d.page.waitForFunction(() => /Today’s card could not be loaded/.test(document.querySelector('#root').textContent));
      const text = await d.page.textContent('.p-duel');
      assert.doesNotMatch(text, /No rounds/, 'an unreadable card must not read as an empty one');
      assert.strictEqual(await d.page.$('.p-duel-rec'), null, 'no record is drawn beside an unreadable card');
      await down.ctx.close();
      // The card read, the record not: the card stands, the record is said to be unread.
      const norec = await context(browser, { signals: { status: 200, body: { signals: [] } },
        duelMe: { status: 500, body: { error: 'Your record is unavailable' } } });
      const n = await open(norec.ctx, `${base}/pi`);
      await signInAndSettle(n.page);
      await showTab(n.page, 'duel');
      await n.page.waitForSelector('.p-duel .d-round');
      await n.page.waitForFunction(() => /Your record could not be read just now/.test(document.querySelector('#root').textContent));
      assert.strictEqual(await n.page.$('.p-duel-rec'), null);
      await norec.ctx.close();
    });
    await t.test('the Farcaster Mini App, which shares the session, still renders its own gate', async () => {
      const { ctx } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/miniapp/arena`);
      assert.match(await page.textContent('.m-signin'), /Sign in with Farcaster/);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
