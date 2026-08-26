'use strict';
/**
 * Paper Trading Arena — /api/arena. Every registered user gets a virtual
 * account with the same starting stake the moment they touch the Arena: no
 * exchange API keys, no bot gateway, no setup. Fills and marks come from the
 * public Bitget ticker feed (lib/tickers) and positions live in the app DB, so
 * the Arena works even when the trading engine is offline — the zero-friction
 * on-ramp and the substrate for paper-trading competitions.
 *
 * Mechanics are lib/arena.js (pure, tested): isolated margin, pnl clamped at
 * -margin, liquidation at -(1-MMR) return on margin. Liquidations settle
 * lazily whenever the account is read or traded against.
 *
 * §4: virtual funds only — nothing here can move real money. The PUBLIC
 * leaderboard shows opt-in anonymous handles + percent return only (the same
 * privacy model as the main leaderboard); virtual balances appear solely on
 * the owner's private account view.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey, ipKey } = require('../lib/rate_limit');
const { pool } = require('../db');
const { getTickers, getTickersWithin, FILL_MAX_AGE_MS } = require('../lib/tickers');
const arena = require('../lib/arena');
const { sealArenaTrade, newTradeKey } = require('../lib/callseal');

const router = express.Router();

// ── Error diagnostic: surface WHY, not just THAT. ──
// Same philosophy as wallet.js error_detail — the operator cannot tell a
// schema mismatch from a timeout from a connection refusal without the reason.
// Sanitise hard: URIs → <uri>, file paths → <path>, long hex → <hex>,
// passwords/secrets → stripped. Tests drive this sanitiser directly.
function safeReason(err) {
  if (!err) return null;
  let m = String(err.code ? `${err.code}: ${err.message}` : err.message || err).slice(0, 200);
  // Strip connection strings: mysql://user:pass@host/db → <uri>
  m = m.replace(/\b[a-z+]+:\/\/[^\s'")]+/gi, '<uri>');
  // Strip file paths
  m = m.replace(/(?:\/[\w.-]+){3,}/g, '<path>');
  // Strip long hex tokens (32+ chars)
  m = m.replace(/\b[0-9a-f]{32,}\b/gi, '<hex>');
  // Strip anything that looks like a password param
  m = m.replace(/password[=:]\S+/gi, '<redacted>');
  return m || null;
}

// Provable Calls v2 — every open is sealed with the trader's opted-in handle
// AT THAT MOMENT (null = anonymous receipt; still verifiable by key).
async function handleFor(userId) {
  try {
    const [rows] = await pool.execute('SELECT id, leaderboard_handle FROM users WHERE id = ?', [userId]);
    return (rows[0] && rows[0].leaderboard_handle) || null;
  } catch (e) { return null; }
}

// { trade_key, seal, seal_payload, sealed_at } for one open. §4: the payload
// carries prices/leverage/times only — margin (vUSDT) never enters it.
function sealedOpen(handle, { symbol, direction, entry, leverage, tp, sl, opened_at }) {
  const trade_key = newTradeKey();
  const receipt = sealArenaTrade({ trade_key, handle, symbol, direction, entry, leverage, tp, sl, opened_at });
  return { trade_key, seal: receipt.seal, seal_payload: receipt.seal_payload, sealed_at: opened_at };
}

const tradeLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });

/**
 * The five PUBLIC arena routes had no limiter of any kind.
 *
 * That was survivable while their only caller was a logged-in dashboard tab.
 * It stops being survivable the moment a Mini App points at them: /embed
 * refreshes itself every 30 seconds, and a board people leave open on a phone
 * is a poller, not a visitor. `routes/embed.js` has capped its own traffic at
 * 120/min per IP since it was written; these are the endpoints that board would
 * be reading, and they were the uncapped half of the same path.
 *
 * Bucketed by IP rather than user because there is no user here — that is the
 * whole point of these routes. Generous, because a legitimate board polling
 * every 30s uses four of these per minute and a shared NAT holds many phones.
 */
const publicBoardLimit = rateLimit({ windowMs: 60000, max: 120, key: ipKey });

/*
 * THE QUERY COST HERE IS KNOWN AND DELIBERATELY NOT CACHED. Recorded because
 * the obvious two fixes are both wrong, and the second one was written, tested
 * and reverted rather than guessed at.
 *
 * `computeLeaderboard` runs five unbounded queries per request: every arena
 * account, every open position, every opted-in handle, and two GROUP BYs over
 * the whole trade table.
 *
 * A `LIMIT` is the wrong fix. A leaderboard ranks by comparing everybody, so
 * capping the input silently drops traders and returns a ranking that is
 * confidently incorrect — nobody reading rank 3 would know it came from a
 * truncated field.
 *
 * A TTL CACHE IS ALSO THE WRONG FIX, which is less obvious. It was implemented
 * with a 20s window and `test/arena.test.js` failed immediately, on the one
 * sequence that matters most: opt in with a handle, then look at the board.
 * The cached copy predates the opt-in, so a person who has just joined is told
 * they are not on the leaderboard — at the exact moment a competition is
 * trying to recruit them. Twenty seconds of that is not a stale read, it is a
 * wrong answer to "did it work". Invalidating properly needs the opt-in path in
 * routes/leaderboard.js to reach into this module, which is real coupling
 * bought for a load problem nothing has demonstrated: the table has two rows.
 *
 * The rate limiter above is the fix that was actually needed — it bounds the
 * abuse case, which is the one a public Mini App creates. If aggregate load
 * ever becomes real, the answer is a materialised board updated on write, not
 * a memo that lies to whoever just joined.
 */

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

async function loadAccount(userId) {
  const [rows] = await pool.execute(
    'SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
  if (rows[0]) return rows[0];
  // SELECT-then-INSERT is a race: the Arena page and the dashboard's Arena
  // card can both miss and both insert, and user_id is the PRIMARY KEY — the
  // loser gets a duplicate-key 500 on what is supposed to be automatic
  // provisioning. The no-op update makes it idempotent WITHOUT touching an
  // existing balance; a real upsert here would reset a funded paper account.
  await pool.execute(
    `INSERT INTO arena_accounts (user_id, balance, created_at) VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE user_id = user_id`,
    [userId, arena.START_BALANCE, new Date()]);
  // Re-read: if we lost the race, the winner's row is the truth.
  const [again] = await pool.execute(
    'SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
  return again[0] || { user_id: userId, balance: arena.START_BALANCE };
}

async function loadPositions(userId) {
  const [rows] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [userId]);
  return rows;
}

// Practice-follow sweep: mirror unprocessed engine signals into this PAPER
// account at the live mark (lazy — runs on account reads, no background job).
// Returns the refreshed { positions, balance } after any opens.
const followLib = require('../lib/arena_follow');
const streaks = require('../lib/arena_streaks');
async function sweepFollows(userId, positions, marks) {
  const [fr] = await pool.execute('SELECT user_id, enabled, margin, leverage, last_signal_id FROM arena_follows WHERE user_id = ?', [userId]);
  const follow = fr[0];
  if (!follow || !Number(follow.enabled)) return { follow: follow || null, positions };
  // LIMIT is inlined, not bound. mysql2's execute() sends JS numbers as
  // DOUBLE, and MySQL refuses a DOUBLE as a prepared LIMIT argument —
  // ER_WRONG_ARGUMENTS. Because this sweep runs inside GET /account, that one
  // placeholder took the whole paper account down for every follower, while
  // the in-memory shim accepted it and kept every test green. The shim now
  // throws on `LIMIT ?` exactly like production does.
  const [sigs] = await pool.execute(
    'SELECT id, symbol, direction, stop_loss, take_profit FROM signals WHERE id > ? ORDER BY id ASC LIMIT 5',
    [Number(follow.last_signal_id) || 0]);
  if (!sigs.length) return { follow, positions };
  const acct = await loadAccount(userId);
  const plan = followLib.planFollows({ signals: sigs, positions, balance: acct.balance,
    prefs: { margin: follow.margin, leverage: follow.leverage }, marks });
  let bal = acct.balance;
  // A LIVE season's rule variants bind followed opens too — the cursor has
  // already advanced, so a non-compliant signal is skipped, never replayed.
  let seasonRow = null;
  try {
    const [srows] = await pool.execute('SELECT id, name, starts_at, ends_at, rules FROM arena_seasons');
    seasonRow = pickCurrentSeason(srows, new Date());
  } catch (e) { /* no season read → no constraint */ }
  const followHandle = plan.opens.length ? await handleFor(userId) : null;
  for (const o of plan.opens) {
    const sr = require('../lib/arena_seasons').checkSeasonRules(seasonRow, o);
    if (!sr.ok) continue;
    // Inherit the SIGNAL's own stop/target when they're valid against the
    // live fill — practice-follow teaches the engine's real exits. An exit
    // level the market already passed is dropped (never a fake fill).
    const sig = sigs.find((s) => Number(s.id) === o.signal_id) || {};
    const tsTp = arena.validateTpSl(o.direction, o.price, sig.take_profit, null);
    const tsSl = arena.validateTpSl(o.direction, o.price, null, sig.stop_loss);
    const tp = tsTp.ok ? tsTp.data.tp : null;
    const sl = tsSl.ok ? tsSl.data.sl : null;
    const openedAt = new Date();
    const rc = sealedOpen(followHandle, {
      symbol: o.symbol, direction: o.direction, entry: o.price,
      leverage: o.leverage, tp, sl, opened_at: openedAt });
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, trade_key, seal, seal_payload, sealed_at, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, o.symbol, o.direction, o.price, o.margin, o.leverage, 'signal', tp, sl,
        rc.trade_key, rc.seal, rc.seal_payload, rc.sealed_at, openedAt]);
    bal -= o.margin;
  }
  if (plan.opens.length) {
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?', [Math.round(bal * 100) / 100, userId]);
  }
  if (plan.last_id > (Number(follow.last_signal_id) || 0)) {
    await pool.execute('UPDATE arena_follows SET last_signal_id = ? WHERE user_id = ?', [plan.last_id, userId]);
  }
  return { follow, positions: await loadPositions(userId) };
}

// Settle automatic exits: liquidation (the margin is gone, no credit),
// stop-loss and take-profit (closed at the trigger price, margin + pnl
// credited back). One pass per account read; returns the survivors.
async function settleLiquidations(userId, positions, marks) {
  const alive = [];
  let credit = 0;
  for (const p of positions) {
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    // Trailing ratchet first — the stop tightens on the observed mark, then
    // that same mark is tested against the TIGHTENED level. Ratchets are
    // mechanical consequences of the disclosed rule, so they never set the
    // user-edit marker; only a human moving levels does.
    const ratchet = mark > 0 ? arena.trailRatchet(p, mark) : null;
    if (ratchet != null) {
      p.sl = ratchet;
      await pool.execute('UPDATE arena_positions SET sl = ? WHERE id = ? AND user_id = ?',
        [ratchet, p.id, userId]);
    }
    const exit = mark > 0 ? arena.exitCheck(p, mark) : null;
    if (!exit) { alive.push(p); continue; }
    const pnl = exit.reason === 'liquidated' ? -p.margin : arena.posPnl(p, exit.price);
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, seal_payload, sealed_at, opened_at, closed_at, signal_key, agent_slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, p.symbol, p.direction, p.entry, round2(exit.price),
        p.margin, p.leverage, round2(pnl), exit.reason,
        p.trade_key || null, p.seal || null, p.seal_payload || null, p.sealed_at || null,
        p.opened_at, new Date(), p.signal_key || null, p.agent_slug || null]);
    await pool.execute(
      'DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    if (exit.reason !== 'liquidated') credit += p.margin + pnl;
  }
  if (credit !== 0) {
    const acct = await loadAccount(userId);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + credit), userId]);
  }
  return alive;
}

// GET /api/arena/account — the owner's private account view (auto-provisions).
router.get('/account', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const acct = await loadAccount(userId);
    let marks = {};
    // Bounded: the client abandons this request at 14s, so a cold upstream
    // fetch must not eat the whole budget. Falls back to the last known map,
    // then to {} — a position then renders with a null mark, which the UI
    // already shows honestly as '—' rather than inventing a price.
    const tick = await getTickersWithin(6000);
    marks = tick.map;                       // DISPLAY: a recent stale mark is fine
    // ...but this handler also WRITES. settleLiquidations closes positions and
    // sweepFollows opens them, sealing the entry price into a Provable-Calls
    // receipt. Those are fills, and a fill may never be priced off a stale
    // mark. Past FILL_MAX_AGE_MS they get nothing and both steps no-op —
    // settleLiquidations skips a position without a mark, and planFollows
    // skips a signal with reason 'no_mark'. The work simply happens on the
    // next load, with a live price, instead of being written at a wrong one.
    const fillMarks = tick.ageMs <= FILL_MAX_AGE_MS ? marks : {};
    let positions = await loadPositions(userId);
    positions = await settleLiquidations(userId, positions, fillMarks);
    const swept = await sweepFollows(userId, positions, fillMarks);
    positions = swept.positions;
    // Re-read the balance — the sweep may have opened signal positions.
    const fresh = await loadAccount(userId);
    acct.balance = fresh.balance;
    const [history] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30', [userId]);
    // Full close history (uncapped) — streaks and weekly quests recompute
    // from ALL facts so they can never drift from the truth.
    const [allTrades] = await pool.execute(
      'SELECT symbol, pnl, reason, closed_at FROM arena_trades WHERE user_id = ?', [userId]);
    const eq = arena.equity(acct.balance, positions, marks);
    res.json({
      start_balance: arena.START_BALANCE,
      balance: round2(acct.balance),
      equity: round2(eq),
      return_pct: round2(arena.returnPct(eq)),
      limits: { min_margin: arena.MIN_MARGIN, max_leverage: arena.MAX_LEVERAGE, max_open: arena.MAX_OPEN },
      positions: positions.map((p) => {
        const mark = marks[p.symbol] && Number(marks[p.symbol].price);
        const pnl = mark > 0 ? arena.posPnl(p, mark) : null;
        return {
          id: p.id, symbol: p.symbol, direction: p.direction,
          entry: p.entry, mark: mark > 0 ? mark : null,
          margin: p.margin, leverage: p.leverage,
          pnl: pnl == null ? null : round2(pnl),
          pnl_pct: pnl == null ? null : round2(pnl / p.margin * 100),
          liq_price: arena.liqPrice(p),
          source: p.source || 'manual',
          tp: p.tp == null ? null : p.tp,
          sl: p.sl == null ? null : p.sl,
          // The open-time seal recorded the ORIGINAL exits — say when they
          // have been moved since, so the receipt cannot overstate discipline.
          exits_edited: !!Number(p.exits_edited || 0),
          trail_pct: p.trail_pct == null ? null : Number(p.trail_pct),
          key: p.seal ? p.trade_key : null,   // Provable Calls receipt address
          opened_at: p.opened_at,
        };
      }),
      follow: swept.follow ? { enabled: !!Number(swept.follow.enabled),
        margin: swept.follow.margin, leverage: swept.follow.leverage } : null,
      history,
      badges: require('../lib/arena_badges').computeArenaBadges({
        trades: history, returnPct: arena.returnPct(eq) }),
      streak: streaks.computeStreak(allTrades),
      quests: streaks.weeklyQuests(allTrades),
      // Private-only discipline read — derived from the same recorded closes
      // the history shows; declines to exist below its minimum sample.
      discipline: require('../lib/arena_discipline').computeDiscipline(allTrades),
      virtual: true,   // §4: this account holds no real funds
    });
  } catch (err) {
    console.error('Arena account error:', err.stack || err.message);
    const reason = safeReason(err);
    res.status(500).json({ error: 'Arena unavailable', ...(reason ? { reason } : {}) });
  }
});

// POST /api/arena/open { symbol, direction, margin, leverage } — market fill
// at the live public ticker price.
// \u2500\u2500 Authority Envelope: armed rules that GATE this paper account's opens.
// Compiled SERVER-side from the user's own words (the same intent-model the
// page and MCP run) so a client can never arm fabricated rules. \u00a74-safe:
// rules are percents, caps and scopes \u2014 never amounts.
router.get('/envelope', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT user_id, source_text, rules_json, enabled, created_at FROM arena_envelopes WHERE user_id = ?', [req.user.user_id]);
    const e = rows && rows[0];
    if (!e) return res.json({ armed: false });
    let rules = [];
    try { rules = JSON.parse(e.rules_json); } catch (x) { rules = []; }
    const { ENFORCEABLE } = require('../lib/envelope_guard');
    res.json({ armed: !!Number(e.enabled), source_text: e.source_text, rules,
      enforced_here: rules.filter((r) => ENFORCEABLE.has(r.id)).map((r) => r.id),
      not_enforced_here: rules.filter((r) => !ENFORCEABLE.has(r.id)).map((r) => r.id) });
  } catch (err) {
    console.error('Envelope read error:', err.stack || err.message);
    res.status(500).json({ error: 'Envelope unavailable' });
  }
});

// POST /envelope/preview { text } — the promise the Intent page has always
// made ("previews before it binds"), delivered. Compiles the policy SERVER
// side and replays it over the caller's OWN recorded opens. Stores nothing,
// arms nothing, and never produces a hypothetical equity curve (see
// lib/envelope_replay's header for why that number is unknowable).
router.post('/envelope/preview', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const text = String((req.body && req.body.text) || '').trim().slice(0, 500);
    if (!text) return res.status(400).json({ error: 'Describe your limits first' });
    const compiled = require('../public/js/intent-model.js').compile(text);
    const rules = (compiled && compiled.rules) || [];
    if (!rules.length) return res.status(400).json({ error: 'No enforceable rules found in that text' });
    const [rows] = await pool.execute(
      'SELECT id, symbol, direction, margin, leverage, pnl, opened_at FROM arena_trades WHERE user_id = ?',
      [req.user.user_id]);
    // OLDEST FIRST, sorted here rather than trusted from the driver: the
    // replay walks the real balance path, so order is load-bearing.
    const trades = (rows || []).slice().sort((a, b) => Number(a.id) - Number(b.id));
    const { replay } = require('../lib/envelope_replay');
    const report = replay(rules, trades, arena.START_BALANCE);
    const { ENFORCEABLE } = require('../lib/envelope_guard');
    res.json({
      ...report,
      rules,
      enforced_here: rules.filter((r) => ENFORCEABLE.has(r.id)).map((r) => r.id),
      not_enforced_here: rules.filter((r) => !ENFORCEABLE.has(r.id)).map((r) => r.id),
      armed: false,   // a preview arms nothing — stated in the payload
    });
  } catch (err) {
    console.error('Envelope preview error:', err.stack || err.message);
    res.status(500).json({ error: 'Preview unavailable' });
  }
});

router.put('/envelope', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const text = String((req.body && req.body.text) || '').trim().slice(0, 500);
    if (!text) return res.status(400).json({ error: 'Describe your limits first' });
    const compiled = require('../public/js/intent-model.js').compile(text);
    const rules = (compiled && compiled.rules) || [];
    if (!rules.length) return res.status(400).json({ error: 'No enforceable rules found in that text' });
    await pool.execute(
      `INSERT INTO arena_envelopes (user_id, source_text, rules_json, created_at) VALUES (?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE source_text = VALUES(source_text), rules_json = VALUES(rules_json), enabled = 1`,
      [req.user.user_id, text, JSON.stringify(rules), new Date()]);
    const { ENFORCEABLE } = require('../lib/envelope_guard');
    res.json({ armed: true, rules,
      enforced_here: rules.filter((r) => ENFORCEABLE.has(r.id)).map((r) => r.id),
      not_enforced_here: rules.filter((r) => !ENFORCEABLE.has(r.id)).map((r) => r.id) });
  } catch (err) {
    console.error('Envelope arm error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not arm the envelope' });
  }
});

router.delete('/envelope', authMiddleware, async (req, res) => {
  try {
    await pool.execute('DELETE FROM arena_envelopes WHERE user_id = ?', [req.user.user_id]);
    // Revocable is the whole point \u2014 disarming always succeeds.
    res.json({ armed: false });
  } catch (err) {
    console.error('Envelope disarm error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not disarm' });
  }
});

/**
 * Open a paper position. THE one path.
 *
 * Extracted from the HTTP handler so the MCP tool rides exactly this code
 * rather than a second copy: the season rules, the armed Authority Envelope,
 * the TP/SL validation and the seal are applied in one place, and an agent
 * gets the same refusals with the same reasons a browser does. Two copies of
 * a fail-closed gate is one copy that stops being fail-closed in whichever
 * door nobody is watching.
 *
 * Returns `{ status, body }` instead of touching `res`, because a caller that
 * is not an HTTP request still needs the status to know what happened.
 */
async function openForUser(userId, body) {
  try {
    const acct = await loadAccount(userId);
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return { status: 503, body: { error: 'Market data unavailable — try again shortly' } };
    }
    let positions = await loadPositions(userId);
    positions = await settleLiquidations(userId, positions, marks);
    const v = arena.validateOpen(body, acct.balance, positions.length);
    if (!v.ok) return { status: 400, body: { error: v.error, code: v.code } };
    // Season rule variants: a LIVE season may constrain opens (e.g. "max 5×,
    // majors only"). Enforced server-side; the refusal names the season.
    try {
      const [srows] = await pool.execute('SELECT id, name, starts_at, ends_at, rules FROM arena_seasons');
      const sr = require('../lib/arena_seasons').checkSeasonRules(
        pickCurrentSeason(srows, new Date()), v.data);
      if (!sr.ok) return { status: 400, body: { error: sr.error } };
    } catch (e) { /* season read failure never blocks an open */ }
    // The armed Authority Envelope, enforced deterministically. Unlike the
    // season read, a FAILURE here also refuses: an armed envelope that
    // cannot be read must fail CLOSED, or arming it means nothing.
    try {
      const [erows] = await pool.execute('SELECT user_id, source_text, rules_json, enabled, created_at FROM arena_envelopes WHERE user_id = ?', [userId]);
      const env = erows && erows[0];
      if (env && Number(env.enabled)) {
        const rules = JSON.parse(env.rules_json);
        const g = require('../lib/envelope_guard').checkOpen(rules, {
          symbol: v.data.symbol, direction: v.data.direction,
          margin: v.data.margin, leverage: v.data.leverage,
          balance: acct.balance, startBalance: arena.START_BALANCE });
        if (!g.ok) {
          return { status: 400, body: { error: g.violations[0].en
              .replace(/\{(\w+)\}/g, (m, k) => String(g.violations[0].params[k] ?? m)),
            code: 'ENVELOPE', violations: g.violations } };
        }
      }
    } catch (e) {
      console.error('Envelope check error:', e.stack || e.message);
      return { status: 503, body: { error: 'Your armed envelope could not be read — opens are refused until it can be (fail closed).' } };
    }
    const t = marks[v.data.symbol];
    const price = t && Number(t.price);
    if (!(price > 0)) return { status: 400, body: { error: 'Unknown symbol — use a listed USDT-M pair like BTCUSDT' } };
    // Optional TP/SL — validated against the actual fill price.
    const ts = arena.validateTpSl(v.data.direction, price, (body || {}).tp, (body || {}).sl);
    if (!ts.ok) return { status: 400, body: { error: ts.error, code: ts.code } };
    const openedAt = new Date();
    const rc = sealedOpen(await handleFor(userId), {
      symbol: v.data.symbol, direction: v.data.direction, entry: price,
      leverage: v.data.leverage, tp: ts.data.tp, sl: ts.data.sl, opened_at: openedAt });
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, trade_key, seal, seal_payload, sealed_at, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, v.data.symbol, v.data.direction, price, v.data.margin, v.data.leverage, 'manual', ts.data.tp, ts.data.sl,
        rc.trade_key, rc.seal, rc.seal_payload, rc.sealed_at, openedAt]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance - v.data.margin), userId]);
    return { status: 200, body: { ok: true, filled: { symbol: v.data.symbol, direction: v.data.direction, entry: price, margin: v.data.margin, leverage: v.data.leverage, tp: ts.data.tp, sl: ts.data.sl, key: rc.trade_key } } };
  } catch (err) {
    console.error('Arena open error:', err.stack || err.message);
    return { status: 500, body: { error: 'Arena unavailable', ...(safeReason(err) ? { reason: safeReason(err) } : {}) } };
  }
}

router.post('/open', authMiddleware, tradeLimit, async (req, res) => {
  const r = await openForUser(req.user.user_id, req.body);
  res.status(r.status).json(r.body);
});

// GET /api/arena/signals — the engine's recent calls, as a paper-tradeable
// list. Practice-follow is all-or-nothing and forward-only; this is the other
// half of the same idea: pick ONE call and put it on paper.
//
// §4: prices, levels, confidence and counts only. The signal's own entry, stop
// and target are public market levels; no amount — real or virtual — appears.
const signalTrade = require('../lib/arena_signal_trade');
router.get('/signals', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const [sigs] = await pool.execute(
      'SELECT id, symbol, direction, entry_price, stop_loss, take_profit, confidence, pattern, created_at FROM signals ORDER BY id DESC LIMIT 12');
    // A mark is nice-to-have here, not required: the list must still render
    // when upstream is slow, and the open route re-checks the price anyway.
    let marks = {};
    try { marks = (await getTickersWithin(4000)).map; } catch (e) { /* list without drift */ }
    const positions = await loadPositions(userId);
    res.json({
      signals: signalTrade.decorateForPicker(sigs, { positions, marks }),
      max_age_ms: signalTrade.MAX_SIGNAL_AGE_MS,
      limits: { min_margin: arena.MIN_MARGIN, max_leverage: arena.MAX_LEVERAGE, max_open: arena.MAX_OPEN },
      open_count: positions.length,
      virtual: true,
    });
  } catch (err) {
    console.error('Arena signals error:', err.stack || err.message);
    res.status(500).json({ error: 'Signals unavailable', reason: safeReason(err) });
  }
});

// POST /api/arena/open-signal { signal_id, margin, leverage } — open ONE named
// engine signal as a paper position, at the LIVE mark and carrying the
// signal's own exits where they still sit on the right side of that fill.
router.post('/open-signal', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    // Two ways to name a signal: the Arena panel sends the numeric id; the
    // dashboard's signal stream sends signal_key — the SAME public identifier
    // /call/<key> verification uses, because /api/signals (public) does not
    // expose row ids.
    const signalId = Number((req.body || {}).signal_id);
    const signalKey = String((req.body || {}).signal_key || '').slice(0, 128);
    if ((!Number.isInteger(signalId) || signalId <= 0) && !signalKey) {
      return res.status(400).json({ error: 'Invalid signal_id' });
    }
    const [srows] = Number.isInteger(signalId) && signalId > 0
      ? await pool.execute(
        'SELECT id, signal_key, symbol, direction, confidence, regime, entry_price, stop_loss, take_profit, created_at FROM signals WHERE id = ?', [signalId])
      : await pool.execute(
        'SELECT id, signal_key, symbol, direction, confidence, regime, entry_price, stop_loss, take_profit, created_at FROM signals WHERE signal_key = ?', [signalKey]);
    const sig = srows[0];
    if (!sig) return res.status(404).json({ error: 'That signal no longer exists' });
    // Signals arrive in whatever shape the bot speaks — 'BTC/USDT:USDT',
    // 'BTC/USDT', 'BTCUSDT'. The arena's whole world (marks, positions,
    // envelopes) is exchange-style 'BTCUSDT': normalize once at the door so
    // a dialect difference can never turn into a phantom "no live mark".
    sig.symbol = require('../lib/agent_match').baseOf(sig.symbol) + 'USDT';

    // A fill needs a fresh price, so this one uses getTickers() and fails
    // loudly rather than the bounded read the display path can tolerate.
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return res.status(503).json({ error: 'Market data unavailable — try again shortly' });
    }
    const acct = await loadAccount(userId);
    let positions = await loadPositions(userId);
    positions = await settleLiquidations(userId, positions, marks);

    const symbol = String(sig.symbol || '').toUpperCase();
    const plan = signalTrade.planSignalOpen({
      signal: sig, positions, balance: acct.balance,
      margin: (req.body || {}).margin, leverage: (req.body || {}).leverage,
      mark: marks[symbol] && Number(marks[symbol].price),
    });
    if (!plan.ok) return res.status(plan.code === 'no_mark' ? 503 : 400).json({ error: plan.error, code: plan.code });
    const d = plan.data;

    // A live season constrains a signal open exactly as it constrains a manual
    // one — the engine's name on the call does not exempt it from the rules.
    try {
      const [srows] = await pool.execute('SELECT id, name, starts_at, ends_at, rules FROM arena_seasons');
      const sr = require('../lib/arena_seasons').checkSeasonRules(
        pickCurrentSeason(srows, new Date()), d);
      if (!sr.ok) return res.status(400).json({ error: sr.error });
    } catch (e) { /* season read failure never blocks an open */ }

    // Optional agent attribution — "I am copying THIS agent's pick." The tag
    // sticks only when the claim VERIFIES server-side: the agent must exist in
    // the catalogue and its own published gates must actually admit this
    // signal (agentWouldTake — the same matcher the picks feed runs). A claim
    // that cannot be verified is dropped and the response says so; the open
    // itself proceeds either way — it is the member's trade regardless.
    let agentSlug = null;
    let attribution = null;
    const claimed = String((req.body || {}).agent_slug || '').toLowerCase().slice(0, 64);
    if (claimed) {
      if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(claimed)) {
        attribution = { attributed: false, reason: 'bad_slug' };
      } else {
        // Community strategies verify FIRST (local DB, always readable) —
        // their signal-checkable rules project onto the same gate shape the
        // engine publishes, so both catalogues face the identical matcher
        // and community records accumulate through the same sealed flow.
        const match = require('../lib/agent_match');
        let gates = null;
        let known = false;
        let readable = true;
        try {
          const cs = await require('../lib/user_strategies').getPublicBySlug(claimed);
          if (cs) { known = true; gates = match.rulesToGates(cs.rules, cs.regime); }
        } catch (e) { /* community store unreadable — the engine lookup decides */ }
        if (!known) {
          const cat = await require('../lib/agent_catalogue').loadCatalogueChecked();
          const agent = cat.agents.find((a) => String(a.id).toLowerCase() === claimed);
          if (agent) { known = true; gates = agent.scorecard && agent.scorecard.gates; }
          readable = cat.readable;
        }
        if (!known) {
          attribution = { attributed: false,
            reason: readable ? 'agent_unknown' : 'catalogue_unreadable' };
        } else if (!match.agentWouldTake(sig, gates)) {
          attribution = { attributed: false, reason: 'gates_reject' };
        } else {
          agentSlug = claimed;
          attribution = { attributed: true, agent_slug: claimed };
        }
      }
    }
    const openedAt = new Date();
    const rc = sealedOpen(await handleFor(userId), {
      symbol: d.symbol, direction: d.direction, entry: d.entry,
      leverage: d.leverage, tp: d.tp, sl: d.sl, opened_at: openedAt });
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, trade_key, seal, seal_payload, sealed_at, opened_at, signal_key, agent_slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, d.symbol, d.direction, d.entry, d.margin, d.leverage, 'signal', d.tp, d.sl,
        rc.trade_key, rc.seal, rc.seal_payload, rc.sealed_at, openedAt,
        sig.signal_key || null, agentSlug]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance - d.margin), userId]);
    res.json({ ok: true, filled: {
      signal_id: d.signal_id, symbol: d.symbol, direction: d.direction,
      entry: d.entry, margin: d.margin, leverage: d.leverage, tp: d.tp, sl: d.sl,
      // The honesty payload: what the call said, what you actually got, and
      // which of its exits the market had already passed.
      signal_entry: d.signal_entry, drift_pct: d.drift_pct, dropped: d.dropped,
      key: rc.trade_key,
    }, ...(attribution ? { attribution } : {}), virtual: true });
  } catch (err) {
    console.error('Arena open-signal error:', err.stack || err.message);
    res.status(500).json({ error: 'Arena unavailable', reason: safeReason(err) });
  }
});

// GET /api/arena/letter — "Your Arena week": the PRIVATE weekly recap,
// composed on demand from the owner's recorded closes for the last completed
// week. Deterministic (append-only inputs), so nothing is stored.
router.get('/letter', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const { lastCompletedWeek } = require('../lib/letter');
    const week = lastCompletedWeek();
    const [trades] = await pool.execute(
      'SELECT symbol, pnl, reason, closed_at FROM arena_trades WHERE user_id = ?', [userId]);
    const inWeek = trades.filter((t) => {
      const at = new Date(t.closed_at).getTime();
      return at >= week.start.getTime() && at < week.end.getTime();
    });
    const positions = await loadPositions(userId);
    const letter = require('../lib/arena_letter').composeArenaLetter(week,
      { trades: inWeek, openCount: positions.length });
    res.json({ letter });
  } catch (err) {
    console.error('Arena letter error:', err.stack || err.message);
    res.status(500).json({ error: 'Letter unavailable', reason: safeReason(err) });
  }
});

// POST /api/arena/exits { position_id, tp, sl } — move an open position's
// exits. Managing the exit is half of trading, and the Arena teaches trading;
// locking the levels at open taught the wrong lesson.
//
// Two honesty rules:
//   1. Levels validate against the LIVE mark, not the entry — the same
//      validator every other path uses. A stop above a long's current mark
//      would fire the instant it lands; that is not "setting a stop", it is
//      closing with extra steps, and the close button already exists.
//   2. The open-time seal recorded the ORIGINAL exits, so the position is
//      marked exits_edited and the payload says so. Without the marker a
//      trader could open with a tight stop, widen it after, and let the
//      receipt overstate their discipline.
//
// Sending null (or empty) for a level CLEARS it — a paper account may run
// stopless, but it does so visibly, not by accident.
router.post('/exits', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const posId = Number((req.body || {}).position_id);
    if (!Number.isInteger(posId) || posId <= 0) {
      return res.status(400).json({ error: 'Invalid position_id' });
    }
    const [rows] = await pool.execute(
      'SELECT id, user_id, symbol, direction, entry, margin, leverage, tp, sl FROM arena_positions WHERE id = ? AND user_id = ?', [posId, userId]);
    const p = rows[0];
    if (!p) return res.status(404).json({ error: 'Position not found' });
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return res.status(503).json({ error: 'Market data unavailable — try again shortly' });
    }
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    if (!(mark > 0)) return res.status(503).json({ error: 'No live mark for this symbol — try again shortly' });
    const tv = arena.validateTrail((req.body || {}).trail_pct);
    if (!tv.ok) return res.status(400).json({ error: tv.error, code: tv.code });
    const trailPct = tv.data.trail_pct;
    let slIn = (req.body || {}).sl;
    // A trail with no explicit stop seeds from the live mark at the trailing
    // distance — the ratchet only ever tightens from there.
    if (trailPct != null && (slIn == null || slIn === '')) {
      slIn = p.direction === 'LONG' ? mark * (1 - trailPct / 100) : mark * (1 + trailPct / 100);
    }
    const ts = arena.validateTpSl(p.direction, mark, (req.body || {}).tp, slIn);
    if (!ts.ok) return res.status(400).json({ error: ts.error, code: ts.code });
    if (trailPct != null && ts.data.sl == null) {
      return res.status(400).json({ error: 'a trailing stop needs a stop level to trail' });
    }
    await pool.execute(
      'UPDATE arena_positions SET tp = ?, sl = ?, trail_pct = ?, exits_edited = 1 WHERE id = ? AND user_id = ?',
      [ts.data.tp, ts.data.sl, trailPct, p.id, userId]);
    res.json({ ok: true, position_id: p.id, tp: ts.data.tp, sl: ts.data.sl,
      trail_pct: trailPct, exits_edited: true, mark, virtual: true });
  } catch (err) {
    console.error('Arena exits error:', err.stack || err.message);
    res.status(500).json({ error: 'Arena unavailable', reason: safeReason(err) });
  }
});

// POST /api/arena/close { position_id } — close at the live mark.
/**
 * Close a paper position. THE one path, for the same reason as `openForUser`:
 * the liquidation check, the exit price and the balance settlement live in
 * one place, so an agent closing over MCP cannot get a different answer from
 * a human closing in the browser.
 */
async function closeForUser(userId, positionId) {
  try {
    const posId = Number(positionId);
    if (!Number.isInteger(posId) || posId <= 0) {
      return { status: 400, body: { error: 'Invalid position_id' } };
    }
    const [rows] = await pool.execute(
      'SELECT id, user_id, symbol, direction, entry, margin, leverage, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE id = ? AND user_id = ?', [posId, userId]);
    const p = rows[0];
    if (!p) return { status: 404, body: { error: 'Position not found' } };
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return { status: 503, body: { error: 'Market data unavailable — try again shortly' } };
    }
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    if (!(mark > 0)) return { status: 503, body: { error: 'No live mark for this symbol — try again shortly' } };
    const liquidated = arena.isLiquidated(p, mark);
    const exitPrice = liquidated ? round2(arena.liqPrice(p)) : mark;
    const pnl = liquidated ? -p.margin : arena.posPnl(p, mark);
    const acct = await loadAccount(userId);
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, seal_payload, sealed_at, opened_at, closed_at, signal_key, agent_slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, p.symbol, p.direction, p.entry, exitPrice, p.margin, p.leverage,
        round2(pnl), liquidated ? 'liquidated' : 'manual',
        p.trade_key || null, p.seal || null, p.seal_payload || null, p.sealed_at || null,
        p.opened_at, new Date(), p.signal_key || null, p.agent_slug || null]);
    await pool.execute('DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + p.margin + pnl), userId]);
    return { status: 200, body: { ok: true, closed: { symbol: p.symbol, pnl: round2(pnl), exit_price: exitPrice, liquidated,
      // Percent return on margin — the §4-safe form of the same fact, and
      // what the public arena receipt already publishes. Added here so the
      // MCP tool never has to see a virtual dollar figure to compute it.
      pct: Number(p.margin) > 0 ? round2((pnl / Number(p.margin)) * 100) : null } } };
  } catch (err) {
    console.error('Arena close error:', err.stack || err.message);
    return { status: 500, body: { error: 'Arena unavailable', ...(safeReason(err) ? { reason: safeReason(err) } : {}) } };
  }
}

router.post('/close', authMiddleware, tradeLimit, async (req, res) => {
  const r = await closeForUser(req.user.user_id, (req.body || {}).position_id);
  res.status(r.status).json(r.body);
});

// The leaderboard computation — one source of truth shared by the JSON route
// below and the frame card (routes/frame.js). §4 is enforced HERE: the
// returned rows carry handle, percent and counts only — no balances, no
// dollar figures (not even virtual ones).
async function computeLeaderboard() {
  const [accounts] = await pool.execute('SELECT user_id, balance FROM arena_accounts');
  if (!accounts.length) return { rows: [], ranked_total: 0 };
    const [allPos] = await pool.execute(
      'SELECT id, user_id, symbol, direction, entry, margin, leverage FROM arena_positions');
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const [tradeCounts] = await pool.execute(
      'SELECT user_id, COUNT(*) AS n FROM arena_trades GROUP BY user_id');
    const countOf = new Map(tradeCounts.map((t) => [t.user_id, t.n]));
    // Provable Calls v2 on the BOARD: how many of each trader's closes carry a
    // verifiable open-time receipt. §4-safe (a count), and it makes the
    // leaderboard itself advertise which records can be checked. Fail-open —
    // a missing column on an old deployment just means no badges.
    let sealedOf = new Map();
    try {
      const [sealedCounts] = await pool.execute(
        'SELECT user_id, COUNT(*) AS n FROM arena_trades WHERE seal IS NOT NULL GROUP BY user_id');
      sealedOf = new Map(sealedCounts.map((t) => [t.user_id, Number(t.n) || 0]));
    } catch (e) { /* pre-receipt deployment — the board still ranks */ }
    let marks = {};
    marks = (await getTickersWithin(6000)).map;   // display only; rank on balances if slow
    const posOf = new Map();
    for (const p of allPos) {
      if (!posOf.has(p.user_id)) posOf.set(p.user_id, []);
      posOf.get(p.user_id).push(p);
    }
    const rows = accounts
      .filter((a) => handleOf.has(a.user_id))
      .map((a) => {
        const eq = arena.equity(a.balance, posOf.get(a.user_id) || [], marks);
        const closes = countOf.get(a.user_id) || 0;
        return {
          handle: handleOf.get(a.user_id),
          return_pct: round2(arena.returnPct(eq)),
          trades: closes + (posOf.get(a.user_id) || []).length,
          // Receipt-backed closes out of total closes — counts only (§4).
          sealed: sealedOf.get(a.user_id) || 0,
          closes,
        };
      })
      .sort((x, y) => y.return_pct - x.return_pct)
      .slice(0, 50)
      .map((r, i) => ({ rank: i + 1, ...r }));
  return { rows, ranked_total: rows.length, virtual: true };
}

router.get('/leaderboard', publicBoardLimit, async (req, res) => {
  try {
    res.json(await computeLeaderboard());
  } catch (err) {
    console.error('Arena leaderboard error:', err.stack || err.message);
    res.status(500).json({ error: 'Leaderboard unavailable' });
  }
});

// GET /api/arena/tape — PUBLIC. The trading floor's live tape: the latest
// closed paper trades from opted-in handles, newest first. §4: percent return
// on margin only — no dollar amounts (not even virtual ones), no balances, no
// user ids. Traders without a leaderboard handle never appear.
router.get('/tape', publicBoardLimit, async (req, res) => {
  try {
    const [trades] = await pool.execute(
      'SELECT id, user_id, symbol, direction, margin, pnl, reason, trade_key, seal, closed_at FROM arena_trades ORDER BY id DESC LIMIT 40');
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const rows = trades
      .filter((t) => handleOf.has(t.user_id))
      .slice(0, 12)
      .map((t) => ({
        handle: handleOf.get(t.user_id),
        symbol: t.symbol,
        direction: t.direction,
        // `: 0` here reported an unusable margin as an exactly break-even
        // trade. margin is DOUBLE NOT NULL and validation floors it at
        // MIN_MARGIN, so the branch is not reachable today — but its two
        // siblings (arena_trader.js:45 and the position payload above)
        // both return null, and the odd one out was the one on the public
        // front door. null is what the client now paints muted.
        pct: Number(t.margin) > 0 ? round2((Number(t.pnl) / Number(t.margin)) * 100) : null,
        reason: t.reason,
        key: t.seal ? t.trade_key : null,   // 🔏 verifiable receipt
        closed_at: t.closed_at,
      }));
    // Pulse line: counts only (§4-safe) — how alive the floor is right now.
    const [accounts] = await pool.execute('SELECT user_id, balance FROM arena_accounts');
    let trades24h = 0;
    try {
      const [cnt] = await pool.execute(
        'SELECT COUNT(*) AS n FROM arena_trades WHERE closed_at >= ?',
        [new Date(Date.now() - 24 * 3600 * 1000)]);
      trades24h = Number(cnt[0] && cnt[0].n) || 0;
    } catch (e) { /* pulse line degrades to 0, tape still serves */ }
    res.json({ rows, traders: accounts.length, trades_24h: trades24h, virtual: true });
  } catch (err) {
    console.error('Arena tape error:', err.stack || err.message);
    res.status(500).json({ error: 'Tape unavailable' });
  }
});

// POST /api/arena/follow { enabled, margin, leverage } — practice-follow the
// engine's signal stream into this PAPER account. §4: paper only, revocable
// any time; enabling starts from the CURRENT newest signal (never back-fills
// old calls, which would fake a history).
router.post('/follow', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const v = followLib.validateFollow(req.body);
    if (!v.ok) return res.status(400).json({ error: v.error, code: v.code });
    await loadAccount(userId);   // ensure the paper account exists
    // Start strictly from now: the newest existing signal id.
    let lastId = 0;
    try {
      const [latest] = await pool.execute(
        'SELECT id, symbol, direction FROM signals ORDER BY created_at DESC LIMIT 1');
      lastId = latest[0] ? Number(latest[0].id) || 0 : 0;
    } catch (e) { /* empty stream — start at 0 */ }
    // arena_follows.user_id is the PRIMARY KEY, so a bare INSERT fails with a
    // duplicate-key error the SECOND time anyone toggles this — every 500 the
    // user has ever seen here. It looked fine locally only because the
    // in-memory shim implements this statement as an upsert (db.js), so the
    // real MySQL behaviour is never exercised in tests.
    //
    // last_signal_id is refreshed on update as well as insert, deliberately:
    // re-enabling must start from the next signal and never back-fill old
    // calls, which is exactly what the panel promises.
    await pool.execute(
      `INSERT INTO arena_follows (user_id, enabled, margin, leverage, last_signal_id, created_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), margin = VALUES(margin),
                               leverage = VALUES(leverage), last_signal_id = VALUES(last_signal_id)`,
      [userId, v.data.enabled ? 1 : 0, v.data.margin, v.data.leverage, lastId, new Date()]);
    res.json({ ok: true, follow: v.data, virtual: true });
  } catch (err) {
    console.error('Arena follow error:', err.stack || err.message);
    res.status(500).json({ error: 'Follow update failed' });
  }
});

// GET /api/arena/trader/:handle — PUBLIC trader card for an opted-in handle.
// §4: percent / count / badges only — never an amount, not even virtual.
const traderLib = require('../lib/arena_trader');
router.get('/trader/:handle', publicBoardLimit, async (req, res) => {
  try {
    const handle = String(req.params.handle || '').trim();
    if (!traderLib.HANDLE_RE.test(handle)) return res.status(400).json({ error: 'Invalid handle' });
    const [u] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
    if (!u[0]) return res.status(404).json({ error: 'No such trader' });
    const userId = u[0].id;
    const [acct] = await pool.execute('SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
    if (!acct[0]) return res.status(404).json({ error: 'No arena account' });
    const positions = await loadPositions(userId);
    const [trades] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30', [userId]);
    let marks = {};
    marks = (await getTickersWithin(6000)).map;   // display only; percent from balance if slow
    res.json(traderLib.buildTraderCard({ handle, balance: acct[0].balance, positions, marks, trades }));
  } catch (err) {
    console.error('Arena trader error:', err.stack || err.message);
    res.status(500).json({ error: 'Trader card unavailable' });
  }
});

// ---- Competition seasons ------------------------------------------------
const seasons = require('../lib/arena_seasons');

// GET /api/arena/season — PUBLIC. The most recently authored season with its
// live status and (once it has started) the in-window standings. A season is
// a time window, never a reset — the all-time board keeps running.
/**
 * Which row is "the" season.
 *
 * This was `SELECT ... FROM arena_seasons LIMIT 1` with NO ORDER BY, which is
 * only correct while exactly one season has ever existed. MySQL is free to
 * return any row for an unordered LIMIT 1, and the in-memory shim used in tests
 * sorts newest-first — so the two disagree by construction and the bug is
 * invisible until a second season is authored. Genesis ends 2026-09-24; the
 * second season is not hypothetical, it is scheduled.
 *
 * A public board naming the WRONG season, with the wrong standings under it,
 * is not a degraded read — it is a confident answer to a question nobody asked.
 * Live wins; otherwise the most recent by start, so an ended season keeps
 * showing until its successor begins rather than blinking to null.
 */
function pickCurrentSeason(rows, now) {
  if (!rows || !rows.length) return null;
  const byNewest = rows.slice().sort((a, b) => new Date(b.starts_at) - new Date(a.starts_at));
  return byNewest.find((s) => seasons.seasonStatus(s, now) === 'live') || byNewest[0];
}

router.get('/season', publicBoardLimit, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at, rules FROM arena_seasons');
    const season = pickCurrentSeason(rows, new Date());
    if (!season) return res.json({ season: null });
    const status = seasons.seasonStatus(season, new Date());
    let rules = season.rules;
    if (typeof rules === 'string') { try { rules = JSON.parse(rules); } catch (e) { rules = null; } }
    const out = {
      season: { name: season.name, starts_at: season.starts_at, ends_at: season.ends_at, status,
        rules: rules || null },
      virtual: true,
    };
    if (status !== 'upcoming') {
      const [trades] = await pool.execute(
        'SELECT user_id, pnl, seal FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
        [season.starts_at, season.ends_at]);
      const [handles] = await pool.execute(
        'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
      const handleMap = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
      out.rows = seasons.seasonRanking(trades, handleMap);
      // Two counts, because an empty board has two unrelated causes and the
      // renderer was announcing a third thing that is never measured here:
      // "no one has joined this season". Nothing joins a season — the board is
      // derived from trades CLOSED inside the window, and `seasonRanking` then
      // drops every user without an opt-in handle (§4). So a board can be
      // empty because nobody has closed a trade yet, OR because several people
      // have and none of them shows a public handle. The leaderboard endpoint
      // has sent `ranked_total` for exactly this reason all along, and
      // embed-arena-view.js documents what it means at the top of the file;
      // this endpoint simply never sent it.
      out.closes_in_window = trades.length;
      out.ranked_total = new Set(
        trades.filter((t) => handleMap.get(t.user_id)).map((t) => t.user_id)).size;
    }
    res.json(out);
  } catch (err) {
    console.error('Arena season error:', err.stack || err.message);
    res.status(500).json({ error: 'Season unavailable' });
  }
});

// GET /api/arena/seasons — PUBLIC Hall of Champions: every ENDED season with
// its final podium (top 3). §4: opt-in handles + percent only, same as every
// arena board. Ended standings are immutable (the window is closed), so this
// is the permanent record.
router.get('/seasons', publicBoardLimit, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at FROM arena_seasons');
    const now = new Date();
    const ended = rows.filter((s) => seasons.seasonStatus(s, now) === 'ended').slice(0, 12);
    if (!ended.length) return res.json({ seasons: [] });
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const out = [];
    for (const s of ended) {
      const [trades] = await pool.execute(
        'SELECT user_id, pnl, seal FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
        [s.starts_at, s.ends_at]);
      out.push({
        name: s.name, starts_at: s.starts_at, ends_at: s.ends_at,
        podium: seasons.seasonRanking(trades, handleOf).slice(0, 3),
      });
    }
    res.json({ seasons: out, virtual: true });
  } catch (err) {
    console.error('Arena seasons history error:', err.stack || err.message);
    res.status(500).json({ error: 'Hall unavailable' });
  }
});

// POST /api/arena/season { name, starts_at, ends_at } — operator only.
/**
 * Is this caller the operator? Answers the row or null; never throws.
 *
 * Lifted out of POST /season because three admin routes now need it and three
 * copies of an authorisation check is three chances to relax one.
 */
async function adminOnly(req, res) {
  const [u] = await pool.execute('SELECT plan FROM users WHERE id = ?', [req.user.user_id]);
  if (!u[0] || String(u[0].plan) !== 'admin') {
    res.status(403).json({ error: 'admin_required', detail: 'Only the operator can manage seasons.' });
    return null;
  }
  return u[0];
}

/**
 * GET /api/arena/seasons/all — every season, with its status. ADMIN.
 *
 * THE OPERATOR AUTHORED A SEASON AND HAD NO WAY TO SEE IT. `/season` returns
 * only the current pick and `/seasons` lists only ENDED ones, so a season that
 * is upcoming — or one whose dates came out wrong — is invisible on every
 * surface including to the person who created it. The report was "I made a new
 * one and started today, I don't see it", and nothing in the API could have
 * answered whether it existed, when it runs, or why it was not winning.
 *
 * A create endpoint with no read is a write into the dark. This is the read.
 *
 * Admin-only because it exposes upcoming seasons, which are an unannounced
 * product decision until they start; the public surfaces stay as they were.
 */
router.get('/seasons/all', authMiddleware, async (req, res) => {
  try {
    if (!await adminOnly(req, res)) return;
    const [rows] = await pool.execute(
      'SELECT id, name, starts_at, ends_at, rules, created_at FROM arena_seasons');
    const now = new Date();
    const current = pickCurrentSeason(rows, now);
    const out = rows
      .slice()
      .sort((a, b) => new Date(b.starts_at) - new Date(a.starts_at))
      .map((r) => {
        let rules = r.rules;
        if (typeof rules === 'string') { try { rules = JSON.parse(rules); } catch (e) { rules = null; } }
        return {
          id: r.id,
          name: r.name,
          starts_at: r.starts_at,
          ends_at: r.ends_at,
          status: seasons.seasonStatus(r, now),
          // Which one the board and the trade gate are actually using. Without
          // it an operator looking at two live seasons cannot tell which is in
          // force, which is the question that brought them here.
          is_current: !!(current && Number(current.id) === Number(r.id)),
          rules: rules || null,
          created_at: r.created_at,
        };
      });
    res.json({ seasons: out, count: out.length });
  } catch (err) {
    console.error('Arena seasons/all error:', err.stack || err.message);
    // 503, not an empty list. "No seasons exist" and "we could not read them"
    // are different answers, and the empty one would send the operator to
    // create a duplicate of a season that is already there.
    res.status(503).json({ error: 'seasons_unavailable' });
  }
});

/**
 * DELETE /api/arena/seasons/:id — remove a season. ADMIN.
 *
 * Deletes the SEASON ROW ONLY. Trades are not touched and are not season-owned:
 * `arena_trades` carries no season id, and every ranking is computed by
 * matching `closed_at` against a season's window. So removing a mis-authored
 * season removes a WINDOW, and the trades that fell inside it keep existing and
 * keep counting toward whichever season's window still contains them.
 *
 * That is the honest behaviour and worth stating, because "delete season" reads
 * like it might discard results. It does not, and it cannot: there is nothing
 * on a trade that says which season it belonged to.
 *
 * Refuses to delete the LIVE season. Doing so would silently change which rules
 * gate every open — the exact confusion this pair of endpoints exists to end —
 * and the operator can end it deliberately by editing its window instead.
 */
router.delete('/seasons/:id', authMiddleware, async (req, res) => {
  try {
    if (!await adminOnly(req, res)) return;
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) {
      return res.status(400).json({ error: 'bad_id' });
    }
    const [rows] = await pool.execute(
      'SELECT id, name, starts_at, ends_at FROM arena_seasons WHERE id = ? LIMIT 1', [id]);
    const row = rows[0];
    // 404 over a silent success: reporting "deleted" for a row that was never
    // there tells the operator their mistake is cleaned up when it is not.
    if (!row) return res.status(404).json({ error: 'no_such_season' });

    const status = seasons.seasonStatus(row, new Date());
    if (status === 'live') {
      return res.status(409).json({
        error: 'season_is_live',
        detail: 'This season is running and gates every open. Change its window instead.',
      });
    }
    await pool.execute('DELETE FROM arena_seasons WHERE id = ?', [id]);
    res.json({ deleted: { id: row.id, name: row.name, status } });
  } catch (err) {
    console.error('Arena season delete error:', err.stack || err.message);
    res.status(503).json({ error: 'delete_failed' });
  }
});

router.post('/season', authMiddleware, async (req, res) => {
  try {
    if (!await adminOnly(req, res)) return;
    const v = seasons.validateSeason(req.body);
    if (!v.ok) return res.status(400).json({ error: v.error, code: v.code });
    await pool.execute(
      'INSERT INTO arena_seasons (name, starts_at, ends_at, rules, created_at) VALUES (?, ?, ?, ?, ?)',
      [v.data.name, v.data.starts_at, v.data.ends_at,
        v.data.rules ? JSON.stringify(v.data.rules) : null, new Date()]);
    // Announce the launch — best-effort, never fails the launch itself.
    // §4: name + window + mechanism only; no numbers of any kind.
    const live = v.data.starts_at <= new Date();
    const when = live ? 'is LIVE now' : `starts ${v.data.starts_at.toISOString().slice(0, 10)}`;
    try {
      await require('../lib/push').notifySubscribers({
        title: `🏟️ Arena season: ${v.data.name}`,
        body: `The season ${when} — same virtual stake for everyone, ranked on percent return. Take the crown.`,
        url: '/arena',
      });
    } catch (e) { /* push not configured — the banner still announces it */ }
    try {
      await pool.execute(
        `INSERT INTO agent_events (event_type, severity, symbol, title, body, data_json, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
        ['arena_season', 'info', null, `Arena season launched: ${v.data.name}`,
          `The paper-trading season ${when}. Same virtual stake for everyone, standings by percent return only — enter at /arena.`,
          JSON.stringify({ starts_at: v.data.starts_at, ends_at: v.data.ends_at }), new Date()]);
    } catch (e) { /* feed insert is best-effort too */ }
    res.json({ ok: true, season: { name: v.data.name, starts_at: v.data.starts_at, ends_at: v.data.ends_at } });
  } catch (err) {
    console.error('Arena season create error:', err.stack || err.message);
    res.status(500).json({ error: 'Season create failed' });
  }
});

// ── Agent keys: mint / list / revoke, JWT-authed ─────────────────────────
//
// These endpoints are for a HUMAN in the browser, so they sit behind the normal
// session. The key they produce is for an AGENT over MCP, and it can reach the
// paper Arena and nothing else — see lib/arena_keys.js for why that is
// structural rather than intentional.
const arenaKeys = require('../lib/arena_keys');

router.get('/keys', authMiddleware, async (req, res) => {
  try {
    res.json({ keys: await arenaKeys.list(req.user.user_id),
      max: arenaKeys.MAX_KEYS_PER_USER });
  } catch (err) {
    console.error('Arena keys list error:', err.stack || err.message);
    // 503, never `{ keys: [] }`: an unreadable list rendered as an empty one
    // tells a user they have no keys, and the next thing they do is mint a
    // duplicate for an agent that is still authenticating with the old one.
    res.status(503).json({ error: 'Could not read your keys' });
  }
});

router.post('/keys', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const out = await arenaKeys.mint(req.user.user_id, (req.body || {}).label);
    // The ONLY time the plaintext exists in a response. Said plainly, because
    // a user who assumes they can look it up later loses it silently.
    res.json({ ok: true, id: out.id, label: out.label, key: out.key,
      note: 'Copy this now — it is stored hashed and cannot be shown again. '
        + 'It can paper-trade the Arena and do nothing else.' });
  } catch (err) {
    if (err && err.code === 'TOO_MANY_KEYS') {
      // A literal, not err.message: this catch also sees driver errors, and a
      // guard cannot tell one Error from another. The cap is ours to state.
      return res.status(400).json({
        error: `At most ${arenaKeys.MAX_KEYS_PER_USER} active keys — revoke one first.` });
    }
    console.error('Arena key mint error:', err.stack || err.message);
    res.status(503).json({ error: 'Could not create a key' });
  }
});

router.post('/keys/revoke', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const ok = await arenaKeys.revoke(req.user.user_id, (req.body || {}).id);
    // 404 on a miss rather than a cheerful ok:true — "revoked" and "that was
    // never yours" must not look the same to someone who mistyped an id.
    if (!ok) return res.status(404).json({ error: 'No such active key' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Arena key revoke error:', err.stack || err.message);
    res.status(503).json({ error: 'Could not revoke that key' });
  }
});

module.exports = router;
module.exports.computeLeaderboard = computeLeaderboard;
module.exports.pickCurrentSeason = pickCurrentSeason;
// Exported so the MCP Arena tools ride the SAME open/close path the
// browser does, rather than growing a second, weaker door.
module.exports.openForUser = openForUser;
module.exports.closeForUser = closeForUser;
module.exports.loadPositions = loadPositions;
module.exports.loadAccount = loadAccount;
