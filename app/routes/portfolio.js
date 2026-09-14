/**
 * Per-user portfolio truth (JWT-authed).
 *
 * GET /api/portfolio — fetches the caller's OWN paper portfolio from the bot
 * gateway (engine.user_portfolios) and write-throughs it into the website DB
 * under the JWT user_id, so every existing /api/trades/* endpoint (stats,
 * history, equity-curve, breakdown, activity, journal notes) becomes
 * per-user-correct without query changes.
 *
 * Why: routes/sync.js server-forces all bot pushes to the operator account
 * (user 1); a web user's trades otherwise never reach the DB.
 *
 * Write-through rules:
 *  - equity snapshot inserted only when equity changed or the latest snapshot
 *    is older than 15 minutes (keeps the curve meaningful, bounds growth);
 *  - CLOSED trades are UPSERTED by (symbol, closed_at, pnl) — only missing
 *    rows are inserted, so journal notes on existing rows survive (unlike
 *    sync.js's delete-all pattern). The key can theoretically collide for two
 *    identical same-second closes — acceptable for paper v1.
 *  - OPEN rows are replaced wholesale (they carry no user annotations).
 *
 * On gateway failure the route degrades to DB-only data with stale: true.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { readLiveMode, modeWord } = require('../lib/live_mode');
const { aggregateStats } = require('../public/js/trade-stats');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

const pfLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

const SNAPSHOT_MIN_INTERVAL_MS = 15 * 60 * 1000;

// The operator's website account: the bot's sync channel (routes/sync.js)
// writes this user's LIVE account data directly into the DB. For this user the
// gateway paper portfolio must never be fetched or written through — it would
// pollute the live equity curve with paper-tracker numbers.
const BOT_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

// GET /api/portfolio/intel — derived-only analytics over the caller's OWN
// recorded closed trades (alpha vs holding, expectancy, payoff, drawdown,
// streaks). Purely a re-read of rows already in the DB; nothing is fetched
// from the gateway and nothing is estimated.
router.get('/intel', pfLimit, async (req, res) => {
  try {
    const { getUserIntel } = require('../lib/intel');
    res.json({ intel: await getUserIntel(req.user.user_id) });
  } catch (e) {
    res.status(500).json({ error: 'Intel unavailable' });
  }
});

async function operatorPortfolio(userId) {
  const [snaps] = await pool.execute(
    'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]);
  const [open] = await pool.execute(
    "SELECT * FROM trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC",
    [userId]);
  // `trades.pnl` is DECIMAL(14,2) and NULLABLE (db.js), and a CLOSED row with
  // no recorded P&L genuinely reaches user 1 — routes/sync.js forwards the
  // gateway's `pnl` uncoerced on both its insert paths. The two queries that
  // used to be here were the two banned shapes from CLAUDE.md's table, side by
  // side: `COALESCE(SUM(pnl), 0)` printed an unpriceable book as a measured
  // $0.00, and `wins / COUNT(*)` put every unpriced row in the denominator
  // only, so each one dragged the operator's win rate DOWN.
  //
  // sync.js and routes/trades.js were both rewritten to score the priced rows
  // explicitly; this function — reachable only for the operator account, which
  // is why it was missed — never got that fix. Same query, same reader
  // (aggregateStats), so the three paths cannot drift apart again.
  const [pnlRows] = await pool.execute(
    "SELECT COUNT(*) AS total, " +
    "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS scored, " +
    "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, " +
    "SUM(pnl) AS net_pnl " +
    "FROM trades WHERE user_id = ? AND status = ?",
    [userId, 'CLOSED']);
  // Live/paper mode AND live-availability from the bot's own scan payload
  // (circuit_breaker). Mode and equity must be derived together so the header
  // can never say LIVE over a stale/paper number.
  //
  // THREE VALUES, NOT TWO. This was `let live = false` closed by
  // `catch (e) { /* mode stays PAPER */ }`, and driven through the real route
  // it published a confident `mode: 'PAPER'` on the OPERATOR account -- the
  // one with real money -- from six distinct failed reads: no scan_cache row
  // (a cold start, before the first sync), a NULL scan_json, a payload with no
  // circuit_breaker, one with no live_mode, a malformed scan_json, and the
  // SELECT itself throwing. Every one of those produced a payload
  // byte-identical to a genuine reading of live_mode:false. No field told
  // them apart. `dbFallback` in this same file was cured of exactly this and
  // its comment calls it "a CLAIM about which account the user is trading,
  // manufactured from a failed read"; this path did not get the memo, and the
  // comment on the catch stated the defect as though it were the design.
  //
  // `null` is NOT READ. The payload carries it as `mode: null`, which every
  // client renderer already prints as MODE ? -- the client needed no change.
  // The read is `lib/live_mode.readLiveMode` -- ONE reading for the four
  // producers that used to answer this three different ways (track.js had
  // it right; sync.js and this path did not). The contract and why it is a
  // strict boolean are stated there, once.
  let live = null;
  let cbUnavailable = false;
  let cbEquity = null;
  let cbFresh = false;
  let cbAt = null;
  try {
    const [rows] = await pool.execute('SELECT scan_json, updated_at FROM scan_cache WHERE id = 1');
    if (rows.length && rows[0].scan_json) {
      const scan = JSON.parse(rows[0].scan_json);
      const cb = scan.circuit_breaker || {};
      live = readLiveMode(cb);
      cbUnavailable = cb.live_unavailable === true;
      const ts = scan.received_at || rows[0].updated_at;
      cbFresh = !!ts && (Date.now() - new Date(ts).getTime()) < 30 * 60 * 1000;
      if (cbFresh) cbAt = new Date(ts).toISOString();
      const e = parseFloat(cb.equity);
      if (Number.isFinite(e) && e > 0) cbEquity = e;
    }
  } catch (e) { /* live stays null: the mode was NOT READ, and the payload says so */ }
  const closed = aggregateStats(pnlRows[0]);
  const snap = snaps[0];
  const fresh = snap && (Date.now() - new Date(snap.snapshot_at).getTime()) < 30 * 60 * 1000;

  // In LIVE mode the operator has no paper baseline: show a REAL, fresh balance
  // or explicitly nothing. Two real sources, freshest first:
  //  1. the scan payload's executor-backed equity (cb.equity) — the bot
  //     refreshes it EVERY scan cycle;
  //  2. the close-driven equity snapshot — only written on live closes, so a
  //     quiet stretch with no closes previously read "BALANCE UNAVAILABLE"
  //     even while a perfectly fresh reading sat in the scan cache.
  // Only when neither source is fresh/real does the header say unavailable.
  let equity = snap ? parseFloat(snap.equity) : null;
  // WHICH ROW THE PUBLISHED EQUITY CAME FROM, because `stale` below has to
  // describe the figure that is actually sent. It was `!fresh` -- the close-
  // driven SNAPSHOT row's age -- while `equity` may have been replaced by
  // cbEquity, which the bot refreshes every scan cycle and cbFresh has just
  // gated to under thirty minutes. So a seconds-old reading went out as
  // `stale: true` whenever the last close happened to be old, and the client
  // printed "bot offline -- last known" over a number the bot had just sent.
  // A false caution about a measured number is still a false claim.
  let equitySource = snap ? 'snapshot' : null;
  let liveUnavailable = false;
  if (live) {
    if (!cbUnavailable && cbFresh && cbEquity != null) {
      equity = cbEquity;
      equitySource = 'scan';
      // Write through so the operator's equity curve keeps growing between
      // closes (same change/staleness thresholds as the per-user path).
      try {
        const changed = !snap || Math.abs(parseFloat(snap.equity) - cbEquity) > 0.005;
        const staleSnap = !snap || (Date.now() - new Date(snap.snapshot_at).getTime()) > SNAPSHOT_MIN_INTERVAL_MS;
        if (changed || staleSnap) {
          await pool.execute(
            'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
            [userId, cbEquity, new Date()]);
        }
      } catch (e) { /* curve write-through is best-effort */ }
    } else if (cbUnavailable || !fresh) {
      equity = null;
      liveUnavailable = true;
    }
  }
  return {
    // `null` when the scan cache could not be read or held no live_mode: the
    // one value `readMode` on the client renders as MODE ? rather than as a
    // claim. Its `stale`-escape hatch is disabled for source:'sync' on
    // purpose (a stale operator push is still a real reading), which is
    // exactly why this path could not lean on `stale` and had to say `null`.
    mode: modeWord(live),
    source: 'sync',
    equity,
    live_unavailable: liveUnavailable,
    total_pnl: closed.net_pnl,
    daily_pnl: null, // not tracked on the sync path
    win_rate: closed.win_rate,
    total_trades: closed.total,
    // How much of the book each figure could be computed over travels WITH the
    // figures, exactly as it does on the sync path — a win rate over 8 of 20
    // closes and one over 20 of 20 are different claims.
    scored_trades: closed.scored,
    unpriced_trades: closed.unpriced,
    open_positions: open,
    closed_trades: [],
    linked: true,
    // Describes the equity that was PUBLISHED. A scan-cache reading is fresh
    // by construction (cbFresh gated it), so it is never called stale because
    // an unrelated snapshot row beside it is old.
    stale: equitySource === 'scan' ? false : !fresh,
    // WHERE EACH FIGURE CAME FROM, per figure, because the flags above each
    // answer a different question and a client reading them together had to
    // guess. `stale` is about the equity row; `live_unavailable` is about the
    // venue; neither says that the open rows are the bot's last sync and the
    // daily figure is not tracked here at all. The vocabulary is shared with
    // dbFallback and the gateway branch below, and the metric cluster reads
    // it instead of inferring "memory" from `stale` — which mislabelled a
    // seconds-old scan-cache balance whenever the snapshot beside it was old.
    provenance: {
      equity: liveUnavailable ? 'unread'
        : equitySource === 'scan' ? 'scan_cache'
        : snap ? 'snapshot' : 'never_stored',
      open_positions: 'sync_rows',
      daily_pnl: 'absent',
    },
    as_of: {
      equity: equitySource === 'scan' ? cbAt
        : (snap && !liveUnavailable) ? new Date(snap.snapshot_at).toISOString() : null,
      // The sync ingest rewrites the open rows wholesale and records no time
      // per row: an age nobody recorded is not printed.
      open_positions: null,
    },
  };
}

async function writeThrough(userId, pf) {
  // 1. Equity snapshot (only on change or staleness)
  if (pf.equity != null) {
    const [snaps] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [userId]);
    const last = snaps[0];
    const changed = !last || Math.abs(parseFloat(last.equity) - pf.equity) > 0.005;
    const stale = !last || (Date.now() - new Date(last.snapshot_at).getTime()) > SNAPSHOT_MIN_INTERVAL_MS;
    if (changed || stale) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [userId, pf.equity, new Date()]);
    }
  }

  // 2. Closed trades: insert only rows we haven't stored yet (preserves notes)
  const closed = pf.closed_trades || [];
  if (closed.length) {
    const [existing] = await pool.execute(
      `SELECT symbol, closed_at, pnl FROM trades WHERE user_id = ? AND status = 'CLOSED' ORDER BY closed_at DESC LIMIT 500`,
      [userId]);
    const seen = new Set(existing.map(t =>
      `${t.symbol}|${new Date(t.closed_at).getTime()}|${parseFloat(t.pnl)}`));
    for (const t of closed) {
      const closedAt = t.closed_at ? new Date(t.closed_at) : new Date();
      const key = `${t.symbol}|${closedAt.getTime()}|${parseFloat(t.pnl)}`;
      if (seen.has(key)) continue;
      await pool.execute(
        `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
        [userId, t.symbol, t.direction, t.entry_price, t.exit_price,
         t.size_usd, t.pnl, t.commission || 0, t.strategy_type || null,
         t.opened_at ? new Date(t.opened_at) : new Date(), closedAt]);
    }
  }

  // 3. Open rows: replace wholesale (no user annotations on OPEN rows)
  await pool.execute(
    "DELETE FROM trades WHERE user_id = ? AND status = 'OPEN'", [userId]);
  for (const p of (pf.open_positions || [])) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, opened_at)
       VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)`,
      [userId, p.symbol, p.direction, p.entry_price, p.size_usd,
       p.commission || 0, p.strategy_type || null,
       p.stop_loss || null, p.take_profit || null,
       p.opened_at ? new Date(p.opened_at) : new Date()]);
  }
}

// THE LAST THING WE STORED, and it is not a reading of the account. Every
// caller below returns this when the bot could not be asked, and each one
// stamped `mode: 'PAPER'` on the way out — a CLAIM about which account the
// user is trading, manufactured from a failed read. A live user whose gateway
// blipped saw their dashboard labelled PAPER. `stale: true` already travels
// with it; the mode is `null` now, which is what "nobody could tell us" is,
// and the header strip reads both.
async function dbFallback(userId) {
  const [snaps] = await pool.execute(
    'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]);
  const [open] = await pool.execute(
    "SELECT * FROM trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC",
    [userId]);
  // WHETHER THIS SITE EVER STORED ANYTHING FOR THE ACCOUNT. `open` above is
  // `[]` both for a book that was synced and is flat and for an account the
  // gateway never answered — writeThrough only runs on the gateway path — so
  // an empty list here was a count produced by no read of anything, and the
  // hero printed "Open 0" over it. A row ever stored, or a snapshot ever
  // taken, is the reading; neither is "never synced".
  const [ever] = await pool.execute(
    'SELECT COUNT(*) AS stored FROM trades WHERE user_id = ?', [userId]);
  const snap = snaps[0] || null;
  const everSynced = !!snap || Number(ever && ever[0] && ever[0].stored) > 0;
  return {
    equity: snap ? parseFloat(snap.equity) : null,
    open_positions: open,
    closed_trades: [],
    stale: true,
    // A MEMORY, named as one per figure — the same vocabulary the operator
    // and gateway branches publish, so a client never has to infer it from
    // `stale`, which the unconfigured branch stamps false over this very
    // object (correctly: it describes the DEPLOYMENT there, not the figures).
    provenance: {
      equity: snap ? 'snapshot' : 'never_stored',
      open_positions: everSynced ? 'db_rows' : 'never_stored',
      daily_pnl: 'absent',
    },
    as_of: {
      equity: snap ? new Date(snap.snapshot_at).toISOString() : null,
      open_positions: null,
    },
  };
}

// GET /api/portfolio
router.get('/', pfLimit, async (req, res) => {
  const userId = req.user.user_id;
  try {
    if (userId === BOT_USER_ID) {
      // Operator: authoritative live data from the sync channel, never the
      // gateway paper portfolio (and never a paper write-through).
      return res.json(await operatorPortfolio(userId));
    }
    if (!gateway.isConfigured()) {
      // No gateway configured at all: there is no bot to have an account ON,
      // so PAPER is the deployment's own state rather than a guess about it.
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER', stale: false, unconfigured: true });
    }
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/portfolio?telegram_id=${encodeURIComponent(ident.id)}`, 15000);
    if (r.status !== 200) {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: null });
    }
    const pf = r.data;
    try {
      await writeThrough(userId, pf);
    } catch (err) {
      console.error('Portfolio write-through error:', err.stack || err.message);
    }
    // Every figure here is the bot's own answer, read just now; `updated_at`
    // is the bot's serialisation instant, which for a payload read this
    // second IS the read time.
    const at = typeof pf.updated_at === 'string' ? pf.updated_at : null;
    return res.json({
      ...pf, linked: ident.linked, stale: false,
      provenance: { equity: 'gateway', open_positions: 'gateway', daily_pnl: 'gateway' },
      as_of: { equity: at, open_positions: at, daily_pnl: at },
    });
  } catch (err) {
    console.error('Portfolio proxy error:', err.stack || err.message);
    try {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: null });
    } catch (e) {
      return res.status(502).json({ error: 'Portfolio unavailable' });
    }
  }
});

module.exports = router;
