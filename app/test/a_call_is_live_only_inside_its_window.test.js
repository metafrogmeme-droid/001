'use strict';
/**
 * A call may be acted on only inside the window the bot stated for it.
 *
 * The bot states each signal's window (`expires_at`: its creation plus its
 * own idea TTL, the time it would still confirm the idea itself), and the site
 * had three answers to "is this call still live":
 *   - the copy picks and their push read that window;
 *   - the Arena's picker, its open route and practice-follow used a
 *     hand-written 6 hours;
 *   - the Signals stream and the Home panel read neither, so Trade and Paper
 *     stayed on a call for as long as nothing had ended it.
 * `SignalStatusModel.live` is the one reading now, and every door asks it.
 * The public payload carries the window; each live call shows a countdown,
 * and its buttons retire when it runs out and are asked again at the click.
 *
 * Practice-follow promises to mirror each call while it is live, which is
 * minutes. It ran only on the follower's own account reads, so it now also
 * runs once a minute for every follower, and a compare-and-set cursor keeps
 * the two from opening one call twice.
 *
 * The plan's F6, the "one definition of live" half.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const vm = require('node:vm');
const express = require('express');

const SS = require('../public/js/signal-status-model.js');
const { publicSignal } = require('../lib/public_signal');
const { codeOnly } = require('./helpers/code_only');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const NOW = Date.parse('2026-10-13T10:00:00Z');
const UNTIL = NOW + 4 * 60000 + 5000;                     // 4:05 left at NOW

// ── the reading ─────────────────────────────────────────────────────────────

test('the window reads from a Date, an ISO string or epoch ms, and nothing else', () => {
  for (const v of [new Date(UNTIL), new Date(UNTIL).toISOString(), UNTIL]) {
    assert.equal(SS.liveUntil({ expires_at: v }), UNTIL, String(v));
  }
  for (const v of [null, undefined, '', true, false, 'soon', NaN]) {
    assert.equal(SS.liveUntil({ expires_at: v }), null, String(v));
  }
  assert.equal(SS.liveUntil(null), null);
});

test('a call is live up to and including the window\'s last instant, as it is for the bot', () => {
  const s = { status: 'NEW', expires_at: UNTIL };
  assert.equal(SS.live(s, NOW), true);
  assert.equal(SS.live(s, UNTIL), true, 'the bot drops an idea only once it is OLDER than its TTL');
  assert.equal(SS.live(s, UNTIL + 1), false);
  assert.equal(SS.live({ status: 'NEW' }, NOW), false, 'a row that states no window is not live');
  assert.equal(SS.live(s, new Date(UNTIL + 1)), false, 'a Date clock reads the same');
});

test('actionable is pending, unscored and live: each condition alone refuses', () => {
  const live = { status: 'NEW', expires_at: UNTIL };
  assert.equal(SS.actionable(live, NOW), true);
  assert.equal(SS.actionable({ ...live, status: 'OPEN' }, NOW), true);
  assert.equal(SS.actionable({ ...live, status: 'STOP' }, NOW), false, 'ended');
  assert.equal(SS.actionable({ ...live, outcome: 'WIN' }, NOW), false, 'scored');
  assert.equal(SS.actionable(live, UNTIL + 1), false, 'past its window');
  assert.equal(SS.actionable({ status: 'NEW' }, NOW), false, 'no window stated');
});

test('the countdown is m:ss while live, 0:00 in the last second, and nothing after', () => {
  const s = { expires_at: UNTIL };
  assert.equal(SS.leftText(s, NOW), '4:05');
  assert.equal(SS.leftText(s, UNTIL - 61000), '1:01');
  assert.equal(SS.leftText(s, UNTIL - 999), '0:00');
  assert.equal(SS.leftText(s, UNTIL), '0:00');
  assert.equal(SS.leftText(s, UNTIL + 1), null, 'never a negative time');
  assert.equal(SS.leftText({}, NOW), null);
});

// ── the tick and the click ──────────────────────────────────────────────────

function el(attrs) {
  return {
    attrs: { ...attrs }, textContent: 'start', hidden: false, disabled: false,
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; },
  };
}
const root = (els) => ({ querySelectorAll: (q) => (q === '[data-live-until]' ? els : []) });

test('the tick counts a countdown down and retires it, and hides a button whose window closed', () => {
  const clock = el({ 'data-live-until': String(UNTIL), 'data-live-word': 'live for {t}',
    'data-closed-word': 'window closed' });
  const button = el({ 'data-live-until': String(UNTIL), 'data-ptrade': '{}' });
  const other = el({ 'data-live-until': String(UNTIL + 3600e3), 'data-parena': 'k' });

  assert.equal(SS.tick(root([clock, button, other]), NOW), 0);
  assert.equal(clock.textContent, 'live for 4:05');
  assert.equal(button.hidden, false);
  assert.equal(button.textContent, 'start', 'a button keeps its own label');

  assert.equal(SS.tick(root([clock, button, other]), UNTIL + 1), 2);
  assert.equal(clock.textContent, 'window closed');
  assert.equal(button.hidden, true);
  assert.equal(button.disabled, true);
  assert.equal(other.hidden, false, 'a call with time left keeps its button');

  // An empty or missing window is closed, never live.
  const blank = el({ 'data-live-until': '' });
  assert.equal(SS.tick(root([blank]), NOW), 1);
  assert.equal(blank.hidden, true);
  assert.equal(SS.tick(null, NOW), 0);
  assert.equal(SS.tick({}, NOW), 0);
});

test('a button is asked again at the click', () => {
  assert.equal(SS.elementLive(el({ 'data-live-until': String(UNTIL) }), NOW), true);
  assert.equal(SS.elementLive(el({ 'data-live-until': String(UNTIL) }), UNTIL + 1), false);
  assert.equal(SS.elementLive(el({ 'data-live-until': '' }), NOW), false);
  assert.equal(SS.elementLive(el({}), NOW), false);
  assert.equal(SS.elementLive(null, NOW), false);
});

// ── the dashboard ───────────────────────────────────────────────────────────

const DASH = codeOnly(read('public', 'js', 'dashboard.js'));

/** A function or arrow body, by brace depth from its head. */
function lift(src, head) {
  const a = src.indexOf(head);
  assert.ok(a >= 0, `${head} moved`);
  assert.equal(src.indexOf(head, a + 1), -1, `${head} matches twice`);
  let depth = 0; let i = src.indexOf('{', a);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') { depth -= 1; if (depth === 0) break; }
  }
  return src.slice(a, i + 1);
}

const WORDS = { 'dd.sig_live': 'live for {t}', 'dd.sig_closed': 'window closed',
  'dd.sig_nowin': 'no window stated' };

function dashCtx(extra = {}) {
  const ctx = vm.createContext({ self: { SignalStatusModel: SS }, esc, JSON, Date,
    T: (k, d) => WORDS[k] || d, ...extra });
  vm.runInContext(lift(DASH, 'function liveLeft(s)'), ctx);
  return ctx;
}

test('beside a pending call the stream says one of three things, and nothing beside an ended one', () => {
  const ctx = dashCtx();
  const soon = Date.now() + 4 * 60000 + 30000;
  const live = ctx.liveLeft({ status: 'NEW', expires_at: soon });
  assert.match(live, /data-live-until="\d+"/);
  assert.match(live, />live for 4:(29|30)</);
  assert.match(live, /data-closed-word="window closed"/);
  assert.match(ctx.liveLeft({ status: 'OPEN', expires_at: Date.now() - 1 }), />window closed</);
  assert.doesNotMatch(ctx.liveLeft({ status: 'OPEN', expires_at: Date.now() - 1 }), /data-live-until/);
  assert.match(ctx.liveLeft({ status: 'NEW' }), />no window stated</);
  assert.equal(ctx.liveLeft({ status: 'STOP', expires_at: soon }), '');
});

function streamRows(rows) {
  const body = lift(DASH, "renderPanel(C('stream'), async () =>").replace(/^renderPanel\(C\('stream'\), /, '');
  const ctx = dashCtx({
    fetchJSON: async () => ({ ok: true, data: { signals: rows } }), mustRead: () => {},
    dirChip: (d) => `[${d}]`, fmtPrice: (p) => String(p), fmtAgo: () => 'now', rrCell: () => '',
    dsBase: (s) => String(s), dsContract: (s) => String(s),
  });
  ctx.self.RCConfidenceScore = { text: () => '70' };
  return vm.runInContext(`(${body})`, ctx)();
}

const call = (over) => Object.assign({ signal_key: 'k1', symbol: 'BTC/USDT', direction: 'LONG',
  status: 'NEW', entry_price: 100, stop_loss: 95, take_profit: 110,
  created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 4 * 60000).toISOString() }, over);

test('the stream offers Trade and Paper only inside the window, each carrying it', async () => {
  const html = await streamRows([
    call({ signal_key: 'live', symbol: 'AAA' }),
    call({ signal_key: 'closed', symbol: 'BBB', expires_at: new Date(Date.now() - 1).toISOString() }),
    call({ signal_key: 'none', symbol: 'CCC', expires_at: null }),
  ]);
  const unesc = (v) => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  const trades = [...html.matchAll(/data-ptrade='([^']*)'/g)].map((m) => JSON.parse(unesc(m[1])).sy);
  const papers = [...html.matchAll(/data-parena="([^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(trades, ['AAA'], 'a call past its window, or with none, was offered');
  assert.deepEqual(papers, ['live']);
  const until = String(Date.parse(call({}).expires_at)).slice(0, 8);
  for (const b of html.match(/<button[^>]*data-p(trade|arena)[^>]*>/g)) {
    assert.match(b, new RegExp(`data-live-until="${until}\\d+"`), b);
  }
  assert.match(html, />window closed</);
  assert.match(html, />no window stated</);
});

test('the click handlers ask the window again before they act', () => {
  const said = [];
  const ctx = dashCtx({ toast: (m) => said.push(m) });
  vm.runInContext(lift(DASH, 'function windowStillOpen(btn)'), ctx);
  const btn = (v) => ({ hasAttribute: (k) => k === 'data-live-until' && v !== undefined,
    getAttribute: (k) => (k === 'data-live-until' ? v : null) });
  assert.equal(ctx.windowStillOpen(btn(String(Date.now() + 60000))), true);
  assert.deepEqual(said, []);
  assert.equal(ctx.windowStillOpen(btn(String(Date.now() - 1))), false);
  assert.match(said[0], /window has closed/);
  assert.equal(ctx.windowStillOpen(btn(undefined)), true, 'a button that is not a signal\'s is not asked');
  // Each door that acts on a call asks before it acts.
  for (const head of ["const abtn = e.target.closest('[data-parena]');",
    "const pbtn = e.target.closest('[data-ptrade]');", "const b = e.target.closest('[data-pcopy]');"]) {
    const at = DASH.indexOf(head);
    assert.ok(at > 0, head);
    assert.match(DASH.slice(at, at + 400), /if \(!windowStillOpen\((abtn|pbtn|b)\)\) return;/, head);
  }
  assert.match(DASH, /setInterval\(\(\) => \{\s*const SS = self\.SignalStatusModel;\s*if \(SS && document\.body\) SS\.tick\(document\.body\);/);
});

// ── the Arena page ──────────────────────────────────────────────────────────

test('the Arena picker counts a live call down and names a closed one', () => {
  const page = read('public', 'arena.html');
  const ctx = vm.createContext({ window: { SignalStatusModel: SS }, esc,
    T: (k, d) => d, fill: (t, m) => String(t).replace(/\{(\w+)\}/g, (_, k) => m[k]),
    fmtP: (p) => String(p), sigAgo: () => '' });
  vm.runInContext(lift(page, 'var SIG_BLOCK = {').replace(/^var /, 'this.') + ';', ctx);
  vm.runInContext(`var SIG_BLOCK = this.SIG_BLOCK;\n${lift(page, 'function sigRowHtml(s)')}`, ctx);
  const row = (over) => ctx.sigRowHtml(Object.assign({ id: 3, symbol: 'BTCUSDT', direction: 'LONG',
    tradeable: true, blocked_reason: null, expires_at: Date.now() + 2 * 60000 }, over));

  const live = row({});
  assert.match(live, /data-sig="3"/);
  assert.match(live, /data-live-until="\d+" data-sig="3"/);
  assert.match(live, />live for [12]:\d\d</);
  const lapsed = row({ expires_at: Date.now() - 1 });
  assert.doesNotMatch(lapsed, /data-sig=/, 'a call whose window closed since the list was read offered an open');
  assert.match(lapsed, />Window closed</);
  const refused = row({ tradeable: false, blocked_reason: 'expired' });
  assert.match(refused, />Window closed</);
  assert.doesNotMatch(page, /sig_b_stale|stale: \[/, 'the 6-hour reason is gone');
  const at = page.indexOf("var btn = e.target.closest ? e.target.closest('[data-sig]') : null;");
  assert.match(page.slice(at, at + 500), /window\.SignalStatusModel\.elementLive\(btn\)/,
    'the open is asked again at the click');
  assert.ok(page.indexOf('/js/signal-status-model.js') > 0
    && page.indexOf('/js/signal-status-model.js') < page.indexOf('function sigRowHtml'),
  'the model loads before the picker renders');
});

// ── the payload and the routes, on the in-memory database ──────────────────

test('the public payload carries the window', () => {
  const out = publicSignal({ signal_key: 'k', status: 'NEW', expires_at: '2026-10-13T10:05:00.000Z',
    pnl: 12.5, seal_payload: 'x' });
  assert.equal(out.expires_at, '2026-10-13T10:05:00.000Z');
  assert.equal(out.pnl, undefined, 'still no amount');
});

const authModule = require('../auth');
const { setTickerFetcher, FILL_MAX_AGE_MS } = require('../lib/tickers');
const { pool } = require('../db');
const arenaRoutes = require('../routes/arena');

const PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 }, ETHUSDT: { price: 50, change: 0, volume: 1 },
  SOLUSDT: { price: 20, change: 0, volume: 1 }, XRPUSDT: { price: 2, change: 0, volume: 1 } };
let server, base;
test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', arenaRoutes);
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}
let n = 0;
async function register() {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: `window${Date.now()}${++n}@x.io`, password: 'longenough1' } });
  assert.ok(reg.data.token, 'registered');
  return { token: reg.data.token, id: reg.data.user_id };
}
async function user() { return (await register()).token; }
/** A call as the bot pushes it; `leftMs` from now to the end of its window. */
async function push(key, symbol, leftMs, status = 'NEW') {
  const at = new Date();
  await pool.execute(
    'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [key, symbol, 'LONG', 0.7, 1, 't', 'trend', 100, 95, 110, 2, 't', status, null, at, null,
      leftMs === null ? null : new Date(at.getTime() + leftMs)]);
  const [rows] = await pool.execute('SELECT * FROM signals WHERE signal_key = ?', [key]);
  return rows[0];
}

test('/api/signals sends each call its window', async () => {
  await push('win-feed-1', 'BTC/USDT', 4 * 60000);
  const r = await req('GET', '/api/signals?limit=200');
  assert.equal(r.status, 200);
  const row = r.data.signals.find((s) => s.signal_key === 'win-feed-1');
  assert.ok(row, 'listed');
  assert.ok(SS.live(row), `the stream row is not live: ${JSON.stringify(row.expires_at)}`);
});

test('/open-signal refuses a call past its window and opens nothing; a live one opens', async () => {
  const token = await user();
  const old = await push('win-open-old', 'ETH/USDT', -1000);
  // Both doors: the stream names a call by key, the Arena panel by id.
  for (const name of [{ signal_key: 'win-open-old' }, { signal_id: old.id }]) {
    const r = await req('POST', '/api/arena/open-signal',
      { token, body: { ...name, margin: 200, leverage: 2 } });
    assert.equal(r.status, 400, JSON.stringify(r.data));
    assert.equal(r.data.code, 'expired');
    assert.match(r.data.error, /window closed at \d\d:\d\d UTC/);
  }
  let a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0);

  const fresh = await push('win-open-new', 'SOL/USDT', 4 * 60000);
  const ok = await req('POST', '/api/arena/open-signal',
    { token, body: { signal_id: fresh.id, margin: 200, leverage: 2 } });
  assert.equal(ok.status, 200, JSON.stringify(ok.data));
  a = await req('GET', '/api/arena/account', { token });
  assert.deepEqual(a.data.positions.map((p) => p.symbol), ['SOLUSDT']);
});

test('the picker sends the window and refuses by it', async () => {
  const token = await user();
  await push('win-pick-old', 'XRP/USDT', -1000);
  await push('win-pick-new', 'BTC/USDT', 4 * 60000);
  const r = await req('GET', '/api/arena/signals', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.max_age_ms, undefined, 'the 6-hour figure is gone');
  const rows = r.data.signals;
  const pick = (sym) => rows.find((s) => s.symbol === sym);
  assert.equal(pick('XRPUSDT').blocked_reason, 'expired');
  assert.equal(pick('BTCUSDT').tradeable, true);
  assert.ok(Number.isFinite(pick('BTCUSDT').expires_at) && pick('BTCUSDT').expires_at > Date.now());
});

async function follower() {
  const u = await register();
  assert.ok(u.id, 'the register response names the user');
  const on = await req('POST', '/api/arena/follow', { token: u.token, body: { enabled: true, margin: 200, leverage: 2 } });
  assert.equal(on.status, 200, JSON.stringify(on.data));
  return u;
}

test('the minute sweep mirrors a live call for a follower who is not looking', async () => {
  const f = await follower();
  await push('win-sweep-live', 'ETH/USDT', 4 * 60000);
  await push('win-sweep-old', 'XRP/USDT', -1000);
  const opened = await arenaRoutes.sweepAllFollows();
  assert.ok(opened >= 1, 'the sweep opened nothing');
  const [rows] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC',
    [f.id]);
  assert.deepEqual(rows.map((p) => p.symbol), ['ETHUSDT'], 'the expired call was mirrored, or the live one was not');
});

test('the sweep fills nothing off a stale mark and keeps its place', async () => {
  const f = await follower();
  await push('win-stale-mark', 'BTC/USDT', 4 * 60000);
  const [[before]] = await pool.execute(
    'SELECT user_id, enabled, margin, leverage, last_signal_id FROM arena_follows WHERE user_id = ?', [f.id]);
  const opened = await arenaRoutes.sweepAllFollows({
    getTickersWithin: async () => ({ map: PRICES, ageMs: FILL_MAX_AGE_MS + 1 }) });
  assert.equal(opened, 0);
  const [[after]] = await pool.execute(
    'SELECT user_id, enabled, margin, leverage, last_signal_id FROM arena_follows WHERE user_id = ?', [f.id]);
  assert.equal(after.last_signal_id, before.last_signal_id, 'the cursor moved past a call nothing was filled for');
});

test('an account read and the sweep at once open a call once', async () => {
  const f = await follower();
  await push('win-race', 'SOL/USDT', 4 * 60000);
  await Promise.all([arenaRoutes.sweepAllFollows(), req('GET', '/api/arena/account', { token: f.token }),
    arenaRoutes.sweepAllFollows()]);
  const a = await req('GET', '/api/arena/account', { token: f.token });
  assert.deepEqual(a.data.positions.map((p) => p.symbol), ['SOLUSDT'], 'one call was opened twice');
});

test('a claim lands only on the cursor it read', async () => {
  const f = await follower();
  const [[row]] = await pool.execute(
    'SELECT user_id, enabled, margin, leverage, last_signal_id FROM arena_follows WHERE user_id = ?', [f.id]);
  const from = Number(row.last_signal_id);
  const [won] = await pool.execute(
    'UPDATE arena_follows SET last_signal_id = ? WHERE user_id = ? AND last_signal_id = ?', [from + 5, f.id, from]);
  const [lost] = await pool.execute(
    'UPDATE arena_follows SET last_signal_id = ? WHERE user_id = ? AND last_signal_id = ?', [from + 9, f.id, from]);
  assert.equal(won.affectedRows, 1);
  assert.equal(lost.affectedRows, 0);
});

test('the server starts the minute sweep, and every follow read selects the window', () => {
  assert.match(codeOnly(read('server.js')), /require\('\.\/routes\/arena'\)\.startFollowSweep\(\);/);
  const src = codeOnly(read('routes', 'copy.js')) + codeOnly(read('lib', 'copy_watch.js'));
  assert.equal((src.match(/SignalStatus\.actionable\(s, now\)/g) || []).length, 2,
    'the copy picks and the copy push read live through the model');
});

// ── the copy push reads the same window ─────────────────────────────────────

test('the copy push pushes a live pick, not one that ended inside its window or outlived it', async () => {
  const cw = require('../lib/copy_watch');
  const CAT = [{ id: 'dip', name: 'Dip', icon: 'x', copy_follow: true,
    scorecard: { gates: { confidence_threshold: 0.5 } } }];
  const sig = (k, over) => Object.assign({ signal_key: k, symbol: 'BTC/USDT', confidence: 0.8,
    regime: 'TREND', direction: 'LONG', status: 'NEW', expires_at: new Date(NOW + 60000) }, over);
  const signals = [sig('live'), sig('stopped', { status: 'STOP' }), sig('late', { expires_at: new Date(NOW - 1) })];
  const sent = [];
  const deps = (saved) => ({
    now: new Date(NOW),
    loadFollowedAgentIds: async () => ['dip'], loadSignals: async () => signals,
    loadCatalogue: async () => CAT, loadFollowers: async () => [1], loadOptIns: async () => new Set([1]),
    saved,
  });
  const jobState = require('../lib/job_state');
  await jobState.save('copy_watch', []);
  await cw.sweepCopy(deps(), async (payload, ids) => { sent.push(payload.body); return ids.length; });
  assert.equal(sent.length, 1);
  assert.doesNotMatch(sent[0], /more/, 'a pick that ended or outlived its window was counted');
});

test('the copy push reads the window through its own query', async () => {
  // No `loadSignals` stand-in: the sweep reads the in-memory database through
  // its own SELECT, which must name the window for the reading to see it.
  const cw = require('../lib/copy_watch');
  const jobState = require('../lib/job_state');
  const at = new Date();
  for (const [key, status] of [['cw-live', 'NEW'], ['cw-ended', 'STOP']]) {
    await pool.execute(
      'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      [key, 'DOGE/USDT', 'LONG', 0.9, 1, 't', 'TREND', 1, 0.9, 1.2, 2, 't', status, null, at, null,
        new Date(at.getTime() + 4 * 60000)]);
  }
  await jobState.save('copy_watch', []);
  const CAT = [{ id: 'dip', name: 'Dip', icon: 'x', copy_follow: true,
    scorecard: { gates: { confidence_threshold: 0.85 } } }];
  await cw.sweepCopy({ loadFollowedAgentIds: async () => ['dip'], loadCatalogue: async () => CAT,
    loadFollowers: async () => [1], loadOptIns: async () => new Set([1]) }, async (p, ids) => ids.length);
  const seen = (await jobState.load('copy_watch')).value || [];
  assert.ok(seen.includes('dip|cw-live'), `the live call was not picked: ${JSON.stringify(seen)}`);
  assert.ok(!seen.includes('dip|cw-ended'), 'a call that ended inside its window was picked');
});
