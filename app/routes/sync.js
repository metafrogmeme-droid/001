/**
 * Bot -> Website data sync endpoint.
 * The Telegram bot calls this to push real portfolio & trade data.
 * Authenticated via a shared secret (BOT_SYNC_SECRET).
 */

const express = require('express');
const crypto = require('crypto');
const { pool, withTransaction } = require('../db');
// LAZY on purpose. `require('../auth')` at module load makes this file fatally
// depend on the WEB session secret — auth.js exits the process when JWT_SECRET
// is unset — and sync.js is the BOT channel, authenticated by BOT_SYNC_SECRET.
// The eager version broke test/status_scan_rehydrate.test.js, which exercises
// scan rehydration and has no business needing a JWT secret. Node caches the
// require, so this costs one map lookup per request and keeps the coupling
// where it belongs: on the one route that actually reads a web session.
const optionalAuth = (req, res, next) => require('../auth').optionalAuth(req, res, next);
const { scrub, DOLLAR_KEY } = require('../lib/flight');
const { winStats, realizedTotal, aggregateStats } = require('../public/js/trade-stats');
const { broadcast } = require('./stream');

/**
 * The summary an anonymous caller may see: the same object with every dollar
 * amount removed, via the SAME scrubber the public flight feed uses. Reusing it
 * rather than hand-listing keys is the point — a field added to the summary
 * later is redacted by default instead of needing someone to remember.
 */
function summaryFor(req, summary) {
  if (!summary) return summary;
  return req.user ? summary : { ...scrub(summary), disclosure: 'Anonymous view — '
    + 'counts and rates only, no dollar amounts. Sign in for equity and P&L.' };
}

const { isVenue } = require('../lib/venues');

/**
 * The venue a synced trade happened on, or the default.
 *
 * VALIDATED, not trusted. This is the bot-secret channel, so the sender is
 * authenticated — but the value lands in a NOT NULL column that group-bys and
 * a public payload read, and an unrecognised string would show up on the
 * dashboard as a venue that does not exist. `isVenue` is the same check the
 * credential route uses, so the two cannot disagree about what a venue is.
 *
 * An older bot sends no venue at all. That is not "unknown" — every trade it
 * has ever placed went to Bitget — so the default is a back-fill of a fact and
 * the column stays NOT NULL.
 */
function venueOf(t) {
  const v = String((t && t.venue) || '').toLowerCase().trim();
  return isVenue(v) ? v : 'bitget';
}

const router = express.Router();

// Best-effort nudge to connected dashboards -- never let a broadcast issue
// affect the actual sync response (the bot's write already succeeded).
function nudge(type, data) {
  try { broadcast(type, data); } catch (e) { /* non-fatal */ }
}

// Dedupe key for the most recently notified closed trade. The full-replace
// POST / sync fires on every portfolio sync, not just on a fresh close, so
// without this a "trade" SSE event (and therefore a browser notification)
// would re-fire for the same already-seen close every time the bot re-syncs.
let lastNotifiedClose = null;

// CRITICAL: No fallback secret. Refuse to serve sync if unset.
const SYNC_SECRET = process.env.BOT_SYNC_SECRET;
if (!SYNC_SECRET || SYNC_SECRET.length < 32) {
  console.error('WARNING: BOT_SYNC_SECRET must be set (>=32 chars) for sync endpoints to work.');
  console.error('Generate one: node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'hex\'))"');
  // Don't crash the server — sync routes will just reject all requests
}

// Authorized bot user ID: only this user's data can be written via sync.
// In a single-operator deployment, the bot always syncs as user 1.
const AUTHORIZED_BOT_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

// -- In-memory stores (persist within same cold start) --
let latestScan = null;
let latestPortfolio = null; // { equity, open_count, net_pnl, total_trades, win_rate, updated_at }
// Guardian Flight Recorder: recent joined decision records + engine-verified
// chain status. { records: [...], chain: {ok,length,tip_hash,problems}, updated_at }
let latestFlight = null;

// The deep-scan pattern block only rides /deepscan syncs; a regular /scan (or
// the autonomous cycle's empty push) must NOT wipe the last readout. We carry
// the previous block forward until a fresh one arrives or it ages past this TTL.
const DEEPSCAN_TTL_MS = 6 * 60 * 60 * 1000; // 6h

/**
 * GET /api/bot/sync/scan
 *
 * DOLLARS ARE AUTHENTICATED — the same rule, and the same mechanism, as
 * /portfolio-summary below.
 *
 * This used to say "no auth required — data is public market info". Most of
 * the payload is: regime, symbols, entry cards, macro, the deep-scan block.
 * But the handler returns `{...incoming}`, a verbatim echo of whatever the bot
 * posted, and `_build_scan_payload`'s `circuit_breaker` section carries
 * `equity` — the operator's live account balance — and `net_pnl`, cumulative
 * dollar P&L.
 *
 * So the redaction /portfolio-summary was given was reachable around: the same
 * two numbers, from the same source object, served unauthenticated under a
 * different key. `curl /api/bot/sync/scan | jq .scan.circuit_breaker.equity`
 * needed no session at all.
 *
 * The echo is the deeper half. An endpoint that republishes an ingested blob
 * cannot be audited by reading it, because its shape is defined in another
 * repo and changes without review here. So the top level also drops anything
 * `DOLLAR_KEY` names — a no-op against today's keys (regime, symbols,
 * entry_cards, key_call, features, macro, deepscan, timestamp), and the point
 * is precisely that: a money field the engine adds later is redacted by
 * default rather than leaking until somebody remembers.
 *
 * WHY NOT `scrub(scan)` WHOLESALE. It was written that way first. `DOLLAR_KEY`
 * matches /usd/i against the KEY, and `symbols` is keyed by TICKER — so it
 * deleted BTCUSDT, ETHUSDT and every other pair, which is the entire market
 * payload. `deepscan.test.js` caught it. The rule is built for field names and
 * cannot be pointed at a map whose keys are instruments.
 *
 * It also blanks `$` inside free text, which would have turned `key_call`'s
 * "BTC RSI: 52 | Price: $63,500.00" into "Price: ⋯" — a public market fact
 * redacted only for sharing a format with an account figure. `key_call` is
 * built from bias, ticker names, BTC RSI/price and a timestamp; it carries no
 * account figure, so it is left whole.
 *
 * NOT A GATE. `optionalAuth` never 401s. Anonymous readers keep the scan and
 * the connection chip keeps working — the standing test that /scan must stay
 * reachable is honoured, because breaking the panel was never the fix.
 */
function scanFor(req, scan) {
  if (!scan || req.user) return scan;
  const out = {};
  for (const k of Object.keys(scan)) {
    if (DOLLAR_KEY.test(k)) continue;
    out[k] = scan[k];
  }
  // The one section that is an ACCOUNT read rather than a market read.
  if (scan.circuit_breaker) out.circuit_breaker = scrub(scan.circuit_breaker);
  out.disclosure = 'Anonymous view — market data, counts and rates. Account '
    + 'equity and dollar P&L are removed. Sign in for the full read.';
  return out;
}

router.get('/scan', optionalAuth, async (req, res) => {
  if (latestScan) {
    return res.json({ scan: scanFor(req, latestScan) });
  }
  // Cold start: try to load from DB
  try {
    const [rows] = await pool.execute('SELECT scan_json, updated_at FROM scan_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].scan_json) {
      latestScan = JSON.parse(rows[0].scan_json);
      return res.json({ scan: scanFor(req, latestScan) });
    }
  } catch (err) {
    console.error('Scan cache load error:', err.stack || err.message);
  }
  return res.json({ scan: null, message: 'No scan data yet. Run /scan in Telegram.' });
});

/**
 * GET /api/bot/sync/portfolio-summary
 *
 * DOLLARS ARE AUTHENTICATED. This used to say "no auth required — shows synced
 * data", which describes where the numbers came from, not whether they are safe
 * to publish. The payload carries `equity` — the operator's account balance —
 * and `net_pnl`, cumulative dollar P&L; the DB fallback below reads
 * `equity_snapshots` and `SUM(pnl) FROM trades` with no user scoping at all.
 * Both routes above `router.use(botAuth)` (line ~160), so neither ever saw it.
 *
 * That is the §4 line this repo draws everywhere else: `sanitizeRecord`'s
 * dollar-key list names `equity` explicitly, public_flight.js "strips every
 * dollar figure", /api/guardian/incidents promises "percent/flags only", and
 * public_letter.js publishes equity as a PERCENT change. One endpoint served
 * the raw figure, and nothing in app/ or bot/ calls it — so it was exposure
 * with no consumer.
 *
 * Kept reachable and redacted rather than moved below botAuth, because "no
 * caller in this repo" is not "no caller": an external dashboard could be
 * reading it, and a 401 would break it silently where a redaction degrades it
 * honestly. Anonymous callers get the counts and rates — open positions, total
 * trades, win rate, mode — which is what a summary is for.
 */
router.get('/portfolio-summary', optionalAuth, async (req, res) => {
  // Return cached in-memory summary if available
  if (latestPortfolio) {
    return res.json({ portfolio: summaryFor(req, latestPortfolio) });
  }
  // Try to build from persisted scan data (circuit_breaker has live exchange data)
  if (!latestScan) {
    try {
      const [rows] = await pool.execute('SELECT scan_json FROM scan_cache WHERE id = 1');
      if (rows.length > 0 && rows[0].scan_json) {
        latestScan = JSON.parse(rows[0].scan_json);
      }
    } catch (err) { /* ignore */ }
  }
  const cb = latestScan?.circuit_breaker;
  if (cb && (cb.equity != null || cb.total_trades != null || cb.live_unavailable)) {
    latestPortfolio = {
      // Preserve null when the bot flagged the live account UNAVAILABLE — never
      // coerce it to 0 or a paper baseline (the dashboard renders "—" +
      // "live account unavailable" instead of a fake balance).
      equity: cb.live_unavailable ? null : (cb.equity || 0),
      open_count: cb.open_count || 0,
      // The equity null was honoured here and the two figures beside it were
      // not — `?? null` rather than `|| 0`, so a bot that says "we could not
      // price this record" is not overruled by the ingest. `|| 0` also ate a
      // genuine 0.0, which is a real break-even and a real 0% win rate.
      net_pnl: cb.net_pnl ?? null,
      total_trades: cb.total_trades ?? null,
      win_rate: cb.win_rate ?? null,
      record_unreadable: !!cb.record_unreadable,
      mode: cb.live_mode ? 'LIVE' : 'PAPER',
      live_unavailable: !!cb.live_unavailable,
      updated_at: latestScan.received_at || latestScan.timestamp || new Date().toISOString()
    };
    return res.json({ portfolio: summaryFor(req, latestPortfolio) });
  }
  // Final fallback: read from DB
  try {
    const [snapRows] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots ORDER BY snapshot_at DESC LIMIT 1'
    );
    // `trades.pnl` is `DECIMAL(14,2)` — NULLABLE — so a CLOSED row with no
    // recorded P&L is reachable, and the previous three queries handled it
    // twice wrongly: `COALESCE(SUM(pnl),0)` printed an unpriceable book as a
    // measured $0.00, and `wins / COUNT(*)` put unpriced rows in the
    // denominator only, so each one pushed the win rate DOWN. Count the
    // priced rows explicitly and let them be the denominator.
    const [tradeRows] = await pool.execute(
      "SELECT COUNT(*) AS total, " +
      "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS scored, " +
      "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, " +
      "SUM(pnl) AS net_pnl " +
      "FROM trades WHERE status = 'CLOSED'"
    );
    const [openRows] = await pool.execute(
      "SELECT COUNT(*) as open_count FROM trades WHERE status = 'OPEN'"
    );
    const closed = aggregateStats(tradeRows[0]);
    // No invented balances: with no snapshot and no trades there is simply no
    // portfolio yet — the UI renders a real empty state, not a phantom number.
    if (snapRows.length === 0 && closed.total === 0) {
      return res.json({ portfolio: null });
    }
    const equity = snapRows.length > 0 ? parseFloat(snapRows[0].equity) : null;
    const openCount = openRows[0]?.open_count || 0;

    latestPortfolio = {
      equity, open_count: openCount, net_pnl: closed.net_pnl,
      total_trades: closed.total, win_rate: closed.win_rate,
      scored_trades: closed.scored, unpriced_trades: closed.unpriced,
      updated_at: snapRows[0]?.snapshot_at || new Date().toISOString()
    };
    res.json({ portfolio: summaryFor(req, latestPortfolio) });
  } catch (err) {
    // This was a bare `res.json({ portfolio: null })` with no logging —
    // byte-identical to the genuine cold-start empty state twelve lines up,
    // which is the one thing it must never be confused with. A DB outage
    // rendered as "no portfolio yet", and left no trace to find it by: every
    // other catch in this file logs err.stack, this one swallowed.
    console.error('Portfolio summary error:', err.stack || err.message);
    res.status(503).json({ error: 'portfolio_summary_unavailable' });
  }
});

// Auth middleware for bot sync — constant-time comparison
function botAuth(req, res, next) {
  if (!SYNC_SECRET) {
    return res.status(503).json({ error: 'Sync not configured (BOT_SYNC_SECRET unset)' });
  }
  const secret = req.headers['x-bot-secret'];
  const a = Buffer.from(secret || '');
  const b = Buffer.from(SYNC_SECRET);
  // timingSafeEqual THROWS on unequal-length buffers — length-check first so a
  // wrong-length secret returns a clean 403 instead of crashing to a 500.
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return res.status(403).json({ error: 'Invalid bot secret' });
  }
  next();
}

router.use(botAuth);

/**
 * POST /api/bot/sync
 * Body: {
 *   equity: number,
 *   positions: [{ symbol, direction, entry_price, size_usd, fees, pattern, stop_loss, take_profit, opened_at }],
 *   closed_trades: [{ symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at }]
 * }
 *
 * Replaces all trade data for the authorized bot user. user_id is server-enforced, not client-supplied.
 */
router.post('/', async (req, res) => {
  try {
    const user_id = AUTHORIZED_BOT_USER_ID; // Server-enforced, ignores any client-supplied user_id
    const { equity, positions, closed_trades } = req.body;
    // Truthful equity: the bot sends a real number, or null/absent when the
    // LIVE balance can't be read (bot-side resolve_display_equity ->
    // (None,"unavailable")). NEVER coerce that to 0 or a paper baseline — a
    // fake number under a LIVE header is exactly the bug we're killing.
    const eq = Number.isFinite(equity) ? Number(equity) : null;

    // ATOMIC, because the alternative destroyed the account's history.
    //
    // This ran the DELETE below under autocommit and then replaced the rows
    // with a loop of individual INSERTs. Any throw in that loop — a malformed
    // row from the bot, a dropped connection, a deadlock — left the DELETE
    // committed and every trade and equity snapshot for this user gone,
    // permanently, behind a response that said only "Sync failed". On a
    // Restart=always bot syncing on a schedule, one persistently bad row would
    // destroy the history on every attempt and never restore it.
    //
    // `withTransaction` throws rather than silently running without one, so a
    // backend that cannot do transactions fails loudly here instead of
    // quietly reproducing the bug this comment describes.
    await withTransaction(async (conn) => {
    // Clear existing trades and snapshots for this user
    await conn.execute('DELETE FROM trades WHERE user_id = ?', [user_id]);
    // Only replace the equity curve when we actually have a real reading;
    // when equity is unavailable, leave the prior snapshots intact (they age
    // out via the freshness gate) rather than stamping a fake point.
    if (eq !== null) {
      await conn.execute('DELETE FROM equity_snapshots WHERE user_id = ?', [user_id]);
    }

    // Insert closed trades
    if (closed_trades && closed_trades.length > 0) {
      for (const t of closed_trades) {
        await conn.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at, venue)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?)`,
          [user_id, t.symbol, t.direction, t.entry_price, t.exit_price,
           t.size_usd, t.pnl, t.fees || 0, t.pattern || null,
           t.opened_at ? new Date(t.opened_at) : new Date(),
           t.closed_at ? new Date(t.closed_at) : new Date(),
           venueOf(t)]
        );
      }
    }

    // Insert open positions
    if (positions && positions.length > 0) {
      for (const p of positions) {
        await conn.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, opened_at, venue)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)`,
          [user_id, p.symbol, p.direction, p.entry_price,
           p.size_usd, p.fees || 0, p.pattern || null,
           p.stop_loss || null, p.take_profit || null,
           p.opened_at ? new Date(p.opened_at) : new Date(),
           venueOf(p)]
        );
      }
    }

    // Insert equity snapshot only for a real reading (see eq above).
    if (eq !== null) {
      await conn.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [user_id, eq, new Date()]
      );
    }
    });   // ── end withTransaction: every write above lands, or none does ──

    // In-memory state is updated only AFTER the commit. Stamping it before
    // would leave `latestPortfolio` describing rows a rollback then discarded
    // — the API reporting a sync the database does not have.
    // Update in-memory portfolio summary
    const closedCount = (closed_trades || []).length;
    const openCount = (positions || []).length;
    // `parseFloat(t.pnl) || 0` summed every unpriced close as a break-even and
    // `wins / closedCount` scored it as a loss — the two banned shapes from
    // CLAUDE.md's table, on the same two lines. Same reader as the GET path
    // above and as bot/utils/win_rate.py, so the three cannot disagree.
    const ws = winStats(closed_trades);
    latestPortfolio = {
      equity: eq, open_count: openCount,
      net_pnl: realizedTotal(closed_trades),
      total_trades: closedCount,
      win_rate: ws.rate === null ? null : ws.rate * 100,
      scored_trades: ws.scored, unpriced_trades: ws.unscored,
      updated_at: new Date().toISOString()
    };

    // Notify about a genuinely NEW closed trade, deduped against the last one
    // we already surfaced (this endpoint replaces the whole trade list on
    // every sync, not just when a fresh close happens).
    const lastClosed = (closed_trades && closed_trades.length) ? closed_trades[closed_trades.length - 1] : null;
    if (lastClosed) {
      const key = `${lastClosed.symbol}|${lastClosed.closed_at}|${lastClosed.pnl}`;
      if (key !== lastNotifiedClose) {
        lastNotifiedClose = key;
        nudge('trade', { symbol: lastClosed.symbol, direction: lastClosed.direction, pnl: lastClosed.pnl });
      }
    }
    nudge('portfolio');
    res.json({ ok: true, synced: { closed: closedCount, open: openCount, equity: eq } });
  } catch (err) {
    console.error('Sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Sync failed' });
  }
});

/**
 * POST /api/bot/trade-event
 * Called by the bot when a single trade opens or closes.
 * Body: { event: "open"|"close", trade: {...}, equity }
 */
// Event ids seen recently, so a RETRY of a delivery whose response was lost
// does not append a second trade. This endpoint inserts unconditionally: a
// replayed `open` would create a phantom position, and a replayed `close`
// would delete another OPEN row and fabricate a closed trade to sit beside
// it -- corrupting the P&L history with a trade that never happened. Until
// this guard existed the bot could not retry a trade sync at all, so a single
// 503 dropped the event permanently.
//
// Deliberately in-process and bounded. It covers the RETRY WINDOW -- seconds,
// per _RETRY_BACKOFF on the bot side -- not a cross-restart replay, and it is
// per-container. That is the honest scope: it makes retrying safe, and it is
// not a durable idempotency ledger. A restart between the original and its
// retry can still double-insert; that window is milliseconds wide and is the
// price of not adding a schema migration to a hot path.
const SEEN_EVENTS = new Map();          // event_id -> ms timestamp
const SEEN_EVENT_TTL_MS = 10 * 60 * 1000;
const SEEN_EVENT_MAX = 2000;

// mysql2 reports a unique-constraint violation as ER_DUP_ENTRY / errno 1062.
// Matched on both because the code is the stable API and the errno is the one
// that survives a driver that forgets to set it. Anything else re-throws --
// swallowing an unknown database error as "duplicate" would report success for
// a trade that was never written, which is the failure this whole path exists
// to prevent.
function _isDuplicateKey(err) {
  if (!err) return false;
  return err.code === 'ER_DUP_ENTRY' || err.errno === 1062;
}

function _seenEvent(id) {
  if (!id) return false;                // no id supplied: cannot dedupe
  const now = Date.now();
  const at = SEEN_EVENTS.get(id);
  if (at !== undefined && now - at < SEEN_EVENT_TTL_MS) return true;
  SEEN_EVENTS.set(id, now);
  if (SEEN_EVENTS.size > SEEN_EVENT_MAX) {
    // Oldest-first eviction; Map preserves insertion order.
    for (const k of SEEN_EVENTS.keys()) {
      SEEN_EVENTS.delete(k);
      if (SEEN_EVENTS.size <= SEEN_EVENT_MAX) break;
    }
  }
  return false;
}

router.post('/trade-event', async (req, res) => {
  try {
    const user_id = AUTHORIZED_BOT_USER_ID; // Server-enforced
    const { event, trade, equity, event_id } = req.body;
    if (!event || !trade) {
      return res.status(400).json({ error: 'event and trade required' });
    }

    // ok:true, because from the bot's side this delivery DID succeed -- the
    // event is recorded. Reporting a failure here would send a correct client
    // into a retry loop over work that is already done.
    if (_seenEvent(event_id)) {
      return res.json({ ok: true, duplicate: true });
    }

    const eid = event_id ? String(event_id).slice(0, 64) : null;
    try {
      if (event === 'open') {
        await pool.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, event_id)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)`,
          [user_id, trade.symbol, trade.direction, trade.entry_price,
           trade.size_usd, trade.fees || 0, trade.pattern || null,
           trade.stop_loss || null, trade.take_profit || null, eid]
        );
      } else if (event === 'close') {
        // ATOMIC. The comment this replaces declined a transaction because the
        // in-memory pool had none, and "a safety property that only holds on
        // one of two backends is not one" -- right, and stale once MemoryDB
        // learned begin/commit/rollback. withTransaction holds on both and
        // refuses, rather than degrading, on any backend that cannot.
        //
        // INSERT stays before DELETE inside it: a duplicate-key rejection of
        // the INSERT is answered by the catch below as "already recorded",
        // and with the INSERT first there is no DELETE to undo on that path.
        // What the transaction buys is the window where an open row lingered
        // beside its close until the next replace-all sync -- a phantom
        // position on /positions, on a money surface.
        await withTransaction(async (conn) => {
          await conn.execute(
            `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at, event_id)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?)`,
            [user_id, trade.symbol, trade.direction, trade.entry_price, trade.exit_price,
             trade.size_usd, trade.pnl, trade.fees || 0, trade.pattern || null,
             trade.opened_at ? new Date(trade.opened_at) : new Date(),
             trade.closed_at ? new Date(trade.closed_at) : new Date(), eid]
          );
          // CLOSE THIS POSITION, NOT AN ARBITRARY ONE. Symbol-only LIMIT 1
          // closed whichever same-symbol row came first, and two opens on one
          // symbol is a real state (website_sync.py and
          // dedupe_duplicate_positions both exist because of it). Match on
          // direction + entry_price; fall back to symbol-only ONLY when the
          // tight key hit nothing, so a differently-rounded entry_price still
          // closes something rather than stranding the row.
          const dir = trade.direction == null ? null : String(trade.direction);
          const entry = Number(trade.entry_price);
          let matched = 0;
          if (dir && Number.isFinite(entry)) {
            const [r] = await conn.execute(
              "DELETE FROM trades WHERE user_id = ? AND symbol = ? AND status = 'OPEN' "
              + "AND direction = ? AND entry_price = ? LIMIT 1",
              [user_id, trade.symbol, dir, entry]
            );
            matched = Number(r && r.affectedRows) || 0;
          }
          if (!matched) {
            await conn.execute(
              "DELETE FROM trades WHERE user_id = ? AND symbol = ? AND status = 'OPEN' LIMIT 1",
              [user_id, trade.symbol]
            );
          }
        });
      }
    } catch (err) {
      // The database recognised this delivery. Same answer as the in-process
      // guard above, for the same reason: from the client's side the event IS
      // recorded, and reporting a failure would send a correct client into a
      // retry loop over work already done.
      if (_isDuplicateKey(err)) {
        return res.json({ ok: true, duplicate: true, durable: true });
      }
      throw err;
    }

    // Record equity snapshot only for a real reading — never a coerced
    // 0/undefined when the live balance is unavailable.
    if (Number.isFinite(equity)) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [user_id, Number(equity), new Date()]
      );
    }

    nudge('trade');
    res.json({ ok: true });
  } catch (err) {
    console.error('Trade event error:', err.stack || err.message);
    res.status(500).json({ error: 'Trade event failed' });
  }
});

// -- In-memory scan data store is declared above (before botAuth) --

/**
 * POST /api/bot/sync/tiers
 * Bot mirrors its membership tiers ({telegram_id, tier} rows) so users.plan
 * follows the bot's tier authority — /set_tier in Telegram is the ONLY way
 * a tier changes; this endpoint just reflects it. (X-Bot-Secret authed.)
 */
const VALID_TIERS = new Set(['basic', 'pro', 'elite', 'admin']);
router.post('/tiers', async (req, res) => {
  try {
    const rows = Array.isArray(req.body?.tiers) ? req.body.tiers.slice(0, 500) : [];
    let updated = 0;
    for (const r of rows) {
      const tgId = String(r?.telegram_id || '').trim();
      const tier = String(r?.tier || '').toLowerCase();
      if (!tgId || !VALID_TIERS.has(tier)) continue;
      const [result] = await pool.execute(
        'UPDATE users SET plan = ? WHERE telegram_id = ?', [tier, tgId]);
      updated += (result && result.affectedRows) || 0;
    }
    res.json({ ok: true, received: rows.length, updated });
  } catch (err) {
    console.error('Tier sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Tier sync failed' });
  }
});

/**
 * POST /api/bot/sync/telegram-unlink
 * Body: { user_id, chat_id }
 *
 * The other half of the bot's /unlink. It used to be a one-sided operation:
 * the bot deleted its local user_telegram row and told the person "Unlinked
 * from you@example.com. Your data is preserved." — while this database still
 * held telegram_linked = TRUE and their telegram_id. Every consumer of the
 * link (routes/credentials.js, routes/controls.js) gates on exactly that pair,
 * so a user who had disconnected their Telegram could still have exchange-key
 * submissions and live-trading controls routed to that chat. The message
 * asserted a disconnection that had happened on one side only.
 *
 * It clears the FLAG and NOT telegram_id, deliberately. That column does double
 * duty as the Telegram OAuth identity (_PROVIDER_ID_COLUMN in auth.js): a user
 * who signed in with the Telegram widget would be locked out of their own
 * account by a "tidier" unlink that nulled it. Since every consumer requires
 * `telegram_linked && telegram_id`, dropping the flag is sufficient and is the
 * whole job.
 *
 * chat_id must match the stored telegram_id. The bot supplies both from its
 * own record; requiring agreement means a stale or wrong user_id unlinks
 * nothing rather than unlinking somebody else. (X-Bot-Secret authed.)
 */
router.post('/telegram-unlink', async (req, res) => {
  try {
    const userId = Number(req.body?.user_id);
    const chatId = String(req.body?.chat_id || '').trim();
    if (!Number.isInteger(userId) || userId <= 0 || !chatId) {
      return res.status(400).json({ error: 'user_id and chat_id required' });
    }
    const [rows] = await pool.execute(
      'SELECT id, telegram_id, telegram_linked FROM users WHERE id = ?', [userId]);
    const u = rows && rows[0];
    if (!u) return res.status(404).json({ error: 'No such user' });
    if (String(u.telegram_id || '') !== chatId) {
      // Not an error the user can fix, and not something to paper over: say
      // which way it disagreed so the bot can report honestly instead of
      // claiming a disconnection it did not make.
      return res.status(409).json({ error: 'chat_id does not match this account',
                                    linked: !!u.telegram_linked });
    }
    await pool.execute('UPDATE users SET telegram_linked = ? WHERE id = ?',
                       [false, userId]);
    res.json({ ok: true, unlinked: true, user_id: userId });
  } catch (err) {
    console.error('Telegram unlink error:', err.stack || err.message);
    res.status(500).json({ error: 'Unlink failed' });
  }
});

/**
 * POST /api/bot/sync/reports
 * Bot pushes the hourly intelligence reports payload (funding scan, arb
 * paper tracker, parity headline, yield radar) built by bot/core/web_reports.
 * Single-row cache like scan_cache. The yield section is operator-sensitive —
 * the read side (routes/reports.js) only serves it to admin-plan users.
 */
let latestReports = null;
router.post('/reports', async (req, res) => {
  try {
    latestReports = { ...req.body, received_at: new Date().toISOString() };
    try {
      await pool.execute(
        'REPLACE INTO reports_cache (id, reports_json) VALUES (1, ?)',
        [JSON.stringify(latestReports)]);
    } catch (dbErr) {
      console.error('Reports cache write error:', dbErr.message);
    }
    nudge('reports');
    res.json({ ok: true });
  } catch (err) {
    console.error('Reports sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Reports sync failed' });
  }
});
// Read-side accessor for routes/reports.js: in-memory first, DB on cold start.
/**
 * The reports blob — THROWS when the cache could not be read.
 *
 * "Cold-start miss is fine" conflated two states that are not the same: the
 * cache holding no row yet (genuinely no reports pushed) and the read failing
 * (we have no idea). Swallowing both returned null for both, and routes/reports.js
 * served that null as its honest-empty branch — so a DB outage rendered as an
 * hourly intelligence scan that found nothing, and the catch written to prevent
 * exactly that was unreachable, because nothing below it could ever throw.
 *
 * Stale still beats blind: an in-memory blob is a real prior read and is
 * returned without touching the DB at all.
 */
async function readReports() {
  if (latestReports) return latestReports;
  const [rows] = await pool.execute(
    'SELECT reports_json FROM reports_cache WHERE id = 1');
  if (rows.length > 0 && rows[0].reports_json) {
    latestReports = JSON.parse(rows[0].reports_json);
  }
  return latestReports;   // null here means genuinely none yet
}

/**
 * The same read for callers that prefer a miss to a throw — lib/status.js
 * probes this as one signal among several and must not fail the whole health
 * read because one cache is unreachable.
 */
async function getLatestReports() {
  try {
    return await readReports();
  } catch (err) { return latestReports; }
}

/**
 * GET /api/bot/sync/stance/pending + POST /api/bot/sync/stance/ack
 * Round trip for the admin-queued GLOBAL stance change (routes/controls.js
 * queues it; the bot pulls, re-verifies the requester's tier is 'admin'
 * against its own UserStore, applies, then acks — which clears the row
 * whether applied or rejected, so a bad request can't retry forever).
 */
router.get('/stance/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT mode, requested_by, telegram_id FROM pending_stance WHERE id = 1');
    res.json({ pending: rows[0] || null });
  } catch (err) {
    console.error('Stance pending error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read pending stance' });
  }
});
router.post('/stance/ack', async (req, res) => {
  try {
    await pool.execute('DELETE FROM pending_stance WHERE id = 1');
    res.json({ ok: true });
  } catch (err) {
    console.error('Stance ack error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to ack stance' });
  }
});

/**
 * POST /api/bot/sync/events
 * Bot pushes public agent mind-stream events (bot/core/agent_feed.py):
 * scan cycles, trade theses, opens/closes, trailing-stop moves, alerts,
 * stance changes. Stored in a bounded ring (agent_events) and re-broadcast
 * live to connected clients as SSE 'activity' events. (X-Bot-Secret authed.)
 *
 * Body: { events: [{ event_type, severity, symbol, title, body, data, ts }] }
 */
const FEED_TYPES = new Set(['scan', 'thesis', 'trade_open', 'trade_close',
  'sl_move', 'alert', 'stance', 'info']);
const FEED_SEVERITIES = new Set(['info', 'success', 'warning', 'critical']);
const FEED_KEEP = 500;           // ring size: newest N rows survive pruning
let feedInsertsSincePrune = 0;
router.post('/events', async (req, res) => {
  try {
    const events = Array.isArray(req.body?.events) ? req.body.events.slice(0, 50) : [];
    if (events.length === 0) {
      return res.status(400).json({ error: 'events array required' });
    }
    let inserted = 0;
    for (const ev of events) {
      const title = String(ev?.title || '').slice(0, 300);
      if (!title) continue;
      const type = FEED_TYPES.has(ev.event_type) ? ev.event_type : 'info';
      const severity = FEED_SEVERITIES.has(ev.severity) ? ev.severity : 'info';
      const symbol = String(ev.symbol || '').slice(0, 32);
      const body = String(ev.body || '').slice(0, 600);
      let dataJson = null;
      try {
        dataJson = ev.data && typeof ev.data === 'object'
          ? JSON.stringify(ev.data).slice(0, 2000) : null;
      } catch (e) { dataJson = null; }
      const ts = ev.ts ? new Date(ev.ts) : new Date();
      const at = isNaN(ts.getTime()) ? new Date() : ts;
      // Per-event fail-soft WITH the real driver error logged: one bad row
      // must not abort the batch, and a silent 500 to the bot's
      // fire-and-forget push left the feed empty with no trace of why.
      try {
        await pool.execute(
          `INSERT INTO agent_events (event_type, severity, symbol, title, body, data_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)`,
          [type, severity, symbol || null, title, body || null, dataJson, at]);
      } catch (insErr) {
        console.error('agent_events insert failed:', insErr.message,
          `(type=${type} at=${at.toISOString()})`);
        continue;
      }
      inserted++;
      nudge('activity', {
        event_type: type, severity, symbol, title, body,
        data: (ev.data && typeof ev.data === 'object') ? ev.data : {},
        created_at: at.toISOString(),
      });
      // Web push for the moments users actually want on their phone: trades
      // and alerts (scans/theses would be spam). Fire-and-forget — a push
      // service hiccup must never slow the bot's ingest.
      if (type === 'trade_open' || type === 'trade_close'
          || (type === 'alert' && severity !== 'info')) {
        try {
          const { notifySubscribers } = require('../lib/push');
          setImmediate(() => notifySubscribers({
            title: `RUNECLAW — ${title}`,
            body: body || 'Open the live feed for details.',
            url: '/dashboard#feed',
          }).catch(() => {}));
        } catch (e) { /* push is optional */ }
      }
    }
    // Ring-buffer prune, amortized (LIMIT/OFFSET inlined — placeholder
    // LIMITs break on some MySQL backends, see the markets-panel fix).
    feedInsertsSincePrune += inserted;
    if (feedInsertsSincePrune >= 50) {
      feedInsertsSincePrune = 0;
      try {
        const [old] = await pool.execute(
          `SELECT id FROM agent_events ORDER BY id DESC LIMIT 1 OFFSET ${FEED_KEEP}`);
        if (old.length > 0) {
          await pool.execute('DELETE FROM agent_events WHERE id <= ?', [old[0].id]);
        }
      } catch (pruneErr) { /* prune is best-effort */ }
    }
    res.json({ ok: true, inserted });
  } catch (err) {
    console.error('Agent feed sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Feed sync failed' });
  }
});

/**
 * POST /api/bot/sync/scan
 * Bot pushes GetClaw scan results after each scan cycle.
 * (authenticated — requires X-Bot-Secret)
 */
router.post('/scan', async (req, res) => {
  try {
    const incoming = req.body || {};
    // Preserve the deep-scan pattern block across scans that don't carry one.
    // A fresh block (from /deepscan) is stamped with its web arrival time; a
    // carried-forward block is dropped once older than the TTL.
    let deepscan = incoming.deepscan
      ? { ...incoming.deepscan, received_at: new Date().toISOString() }
      : (latestScan && latestScan.deepscan) || null;
    if (deepscan && deepscan.received_at) {
      const age = Date.now() - new Date(deepscan.received_at).getTime();
      if (!(age >= 0 && age < DEEPSCAN_TTL_MS)) deepscan = null;
    }
    latestScan = {
      ...incoming,
      ...(deepscan ? { deepscan } : {}),
      received_at: new Date().toISOString(),
    };
    // Persist to DB so it survives cold starts
    try {
      await pool.execute(
        'REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
        [JSON.stringify(latestScan)]
      );
    } catch (dbErr) {
      console.error('Scan cache write error:', dbErr.message);
    }
    // Update portfolio summary from circuit_breaker if present
    const cb = latestScan.circuit_breaker;
    if (cb && (cb.equity != null || cb.total_trades != null || cb.live_unavailable)) {
      // Same contract as the GET cold-start path below — and it matters MORE
      // here: this ingest runs on every bot scan sync and overwrites the
      // in-memory summary, so a careful null on the cold path was being
      // stamped back to `equity: 0` seconds later by this one. A live account
      // whose balance cannot be read must render "—", never "$0.00" — zero
      // reads as "account wiped", which is a fabricated (and alarming) number.
      latestPortfolio = {
        equity: cb.live_unavailable ? null : (cb.equity || 0),
        open_count: cb.open_count || 0,
        // Same contract as the GET path above, and it matters more here for
        // the same reason the equity comment gives: this ingest runs on every
        // scan sync and stamps over whatever the cold path carefully set.
        net_pnl: cb.net_pnl ?? null,
        total_trades: cb.total_trades ?? null,
        win_rate: cb.win_rate ?? null,
        record_unreadable: !!cb.record_unreadable,
        mode: cb.live_mode ? 'LIVE' : 'PAPER',
        live_unavailable: !!cb.live_unavailable,
        updated_at: latestScan.received_at,
      };
    }
    nudge('scan');
    res.json({ ok: true });
  } catch (err) {
    console.error('Scan sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Scan sync failed' });
  }
});

/**
 * POST /api/bot/sync/flight
 * Body: { records: [ joined DECISION↔OUTCOME flight records ],
 *         chain: { ok, length, tip_hash, problems } }
 *
 * Guardian Flight Recorder ingest. The bot pushes recent provenance-complete
 * decision records and the authoritative hash-chain verification result. Purely
 * a read-only mirror for the website — the tamper-evident ledger itself lives
 * bot-side. Bot-secret authed (botAuth middleware above).
 */
router.post('/flight', async (req, res) => {
  try {
    const body = req.body || {};
    const records = Array.isArray(body.records) ? body.records.slice(0, 200) : [];
    const chain = (body.chain && typeof body.chain === 'object') ? body.chain : {};
    const policy = (body.policy && typeof body.policy === 'object') ? body.policy : null;
    // Guardian console posture (chain health + per-module risk + armed flags).
    // Read-only, optional — older bots don't send it, so it stays null then.
    const guardian_status = (body.guardian_status && typeof body.guardian_status === 'object')
      ? body.guardian_status : null;
    latestFlight = { records, chain, policy, guardian_status, updated_at: new Date().toISOString() };
    // Safety incidents (blocks & recoveries) mirrored from the sealed chain.
    // Only attach when the bot actually sent the field — an OLDER bot omits it,
    // and leaving `incidents` absent lets the incidents route derive from
    // rejected records during the deploy transition (a sent [] means "synced,
    // genuinely none" and is left as-is).
    if (Array.isArray(body.incidents)) latestFlight.incidents = body.incidents.slice(0, 60);
    // Persist so it survives cold starts (table may not exist on older DBs —
    // in-memory still serves in that case).
    try {
      await pool.execute(
        'REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)',
        [JSON.stringify(latestFlight)]
      );
    } catch (dbErr) {
      console.error('Flight cache write error:', dbErr.message);
    }
    nudge('flight', { count: records.length, chain_ok: chain.ok !== false });
    res.json({ ok: true, stored: records.length });
  } catch (err) {
    console.error('Flight sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Flight sync failed' });
  }
});

/**
 * POST /api/bot/sync/signals
 * Body: { signals: [{ signal_key, symbol, direction, confidence, score, pattern,
 *         regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
 *         created_at, resolved_at }] }
 *
 * Append/UPSERT to the global signal stream. signal_key is the stable per-signal
 * id from the bot, so re-syncing the same signal updates its outcome (status/pnl)
 * rather than duplicating. Global stream (not per-user); the dashboard joins each
 * user's taken trades to it. Bot-secret authed (botAuth middleware above).
 */
router.post('/signals', async (req, res) => {
  try {
    const list = Array.isArray(req.body && req.body.signals) ? req.body.signals : [];
    if (list.length === 0) return res.json({ ok: true, upserted: 0 });
    // Cap a single batch to bound the write cost of a malformed/huge payload.
    const batch = list.slice(0, 500);
    let upserted = 0;
    const { sealCall } = require('../lib/callseal');
    for (const s of batch) {
      if (!s || !s.signal_key || !s.symbol || !s.direction) continue;
      // Provable Calls: the receipt is sealed from the EXACT coerced values
      // being stored, at the moment the call first lands. ON DUPLICATE KEY
      // updates only outcome fields — a re-sync (resolution) can never touch
      // the seal, the payload, or any decision-time value.
      const fixed = {
        signal_key: String(s.signal_key).slice(0, 128),
        symbol: String(s.symbol).slice(0, 32),
        direction: String(s.direction).slice(0, 8),
        confidence: Number(s.confidence) || 0,
        entry_price: Number(s.entry_price) || 0,
        stop_loss: Number(s.stop_loss) || 0,
        take_profit: Number(s.take_profit) || 0,
        pattern: s.pattern ? String(s.pattern).slice(0, 64) : null,
        regime: s.regime ? String(s.regime).slice(0, 32) : null,
        // Sealed as of kind v4: the REASON for the call is now inside the hash,
        // not merely stored beside it. Same truncation the column takes, so the
        // sealed string and the stored row can never disagree.
        thesis: s.thesis != null ? String(s.thesis) : null,
        created_at: s.created_at ? new Date(s.created_at) : new Date(),
      };
      const receipt = sealCall(fixed);
      await pool.execute(
        `INSERT INTO signals
           (signal_key, symbol, direction, confidence, score, pattern, regime,
            entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
            created_at, resolved_at, seal, seal_payload, sealed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           status = VALUES(status), pnl = VALUES(pnl),
           resolved_at = VALUES(resolved_at)`,
        [
          fixed.signal_key,
          fixed.symbol,
          fixed.direction,
          fixed.confidence,
          Number(s.score) || 0,
          fixed.pattern,
          fixed.regime,
          fixed.entry_price,
          fixed.stop_loss,
          fixed.take_profit,
          Number(s.rr) || 0,
          fixed.thesis,
          s.status ? String(s.status).slice(0, 16) : 'NEW',
          (s.pnl === null || s.pnl === undefined) ? null : Number(s.pnl),
          fixed.created_at,
          s.resolved_at ? new Date(s.resolved_at) : null,
          receipt.seal,
          receipt.seal_payload,
          new Date(),
        ]
      );
      upserted++;
    }
    nudge('signals');
    res.json({ ok: true, upserted });
  } catch (err) {
    console.error('Signals sync error:', err.stack || err.message);
    res.status(500).json({ error: 'Signals sync failed' });
  }
});

/**
 * POST /api/bot/sync/credentials/sealing-key
 * Body: { kid, pem, alg }
 *
 * THIS IS WHAT TURNS THE CONNECT FORM ON. The bot publishes the PUBLIC half of
 * its credential-sealing keypair here (first pull after boot, again on change,
 * hourly otherwise); routes/credentials.js seals each submission to it, so the
 * website stores exchange keys it cannot itself read.
 *
 * It replaces a two-deployment key ceremony: WEB_CREDS_KEY had to be generated
 * by hand and set IDENTICALLY in the bot's env and this app's, and until both
 * were set the connect form answered 503 — which is what "I entered my API
 * keys on the website and nothing saved" was.
 *
 * Nothing secret crosses this endpoint, so a rejection here is about the key
 * being unusable, never about it being sensitive. The record is VETTED before
 * it is stored (lib/sealing_key.js): the alternative is finding out it is
 * malformed at submit time, on a user's screen, with their keys already typed
 * in. Bot-secret authed by the middleware above.
 */
router.post('/credentials/sealing-key', async (req, res) => {
  try {
    const rec = await require('../lib/sealing_key').storeSealingKey(req.body || {});
    res.json({ ok: true, kid: rec.kid });
  } catch (err) {
    // Two different faults, and answering both the same would send the
    // operator to the wrong side: a key we refuse is the BOT's to fix and
    // republishing cannot help, a write we could not do is ours and the next
    // publish retries into it.
    const bad = !!err.unusableSealingKey;
    console.error('Sealing key publish %s:', bad ? 'rejected' : 'failed',
                  err.stack || err.message);
    // Through safeErrorText even though the caller is the authenticated bot:
    // the reason IS worth returning — it is what a bot operator needs to fix
    // the key — and F-15 is a rule about the shape of a response, not about
    // who is reading it. An OpenSSL error carrying a path is still a path.
    if (bad) {
      return res.status(400).json({ error: 'unusable_sealing_key',
                                    detail: require('../lib/safe_error').safeErrorText(err) });
    }
    res.status(500).json({ error: 'Failed to store the sealing key' });
  }
});

/**
 * GET /api/bot/sync/credentials/pending
 * Bot pulls pending exchange-credential requests (sealed to its own key, or
 * encrypted under the legacy shared WEB_CREDS_KEY). The bot opens them, imports
 * into its Fernet store keyed by telegram_id (connect) or removes them
 * (disconnect), then ACKs so the row is cleared. Bot-secret authed.
 */
router.get('/credentials/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT user_id, telegram_id, exchange, action, encrypted_payload, created_at
       FROM pending_credentials ORDER BY created_at ASC LIMIT 100`
    );
    res.json({ pending: rows });
  } catch (err) {
    console.error('Cred pending fetch error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch pending credentials' });
  }
});

/**
 * POST /api/bot/sync/credentials/ack
 * Body: { acks: [{ user_id, action, ok }] }
 * For each successful ack, delete the pending row and update connection status
 * (connect -> connected=true, disconnect -> connected=false). Bot-secret authed.
 */
router.post('/credentials/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    let rejected = 0;
    for (const a of acks) {
      // A FAILED ACK IS AN ANSWER AND IT WAS BEING THROWN AWAY.
      //
      // This read `if (!a || a.user_id == null || !a.ok) continue;` — so every
      // `ok: false` was dropped: the error string went nowhere, the
      // pending_credentials row was never deleted, and /status therefore kept
      // returning `pending: 'connect'` forever. The card showed "⏳ applying
      // bitget…" with no timeout while the bot re-pulled and re-failed the
      // same row every 30 seconds. The bot's own contract distinguishes three
      // outcomes precisely so this endpoint can act on them, and it acted on
      // one; a row it deliberately leaves UN-acked (transient) never arrives
      // here at all, so clearing on a received failure cannot lose a retry.
      if (!a || a.user_id == null) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      // Carry the venue from the pending row (or the bot's ack) so the status
      // badge names the right exchange instead of always "bitget".
      const [prow] = await pool.execute(
        'SELECT exchange FROM pending_credentials WHERE user_id = ?', [uid]);
      const venue = String(a.venue || (prow[0] && prow[0].exchange) || 'bitget').toLowerCase();
      await pool.execute('DELETE FROM pending_credentials WHERE user_id = ?', [uid]);

      if (!a.ok) {
        // Not connected, and SAY WHY. The reason is the venue's own words as
        // relayed by the bot, bounded here as well as bot-side because this is
        // the boundary that writes it to a column and serves it to a browser.
        const why = String(a.error || 'the exchange rejected these keys').slice(0, 200);
        await pool.execute(
          `INSERT INTO exchange_status (user_id, exchange, connected, last_error)
           VALUES (?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE exchange = VALUES(exchange),
             connected = VALUES(connected), last_error = VALUES(last_error),
             updated_at = CURRENT_TIMESTAMP`,
          [uid, venue, false, why]
        );
        rejected++;
        continue;
      }

      const connected = a.action === 'disconnect' ? false : true;
      await pool.execute(
        `INSERT INTO exchange_status (user_id, exchange, connected, last_error)
         VALUES (?, ?, ?, NULL)
         ON DUPLICATE KEY UPDATE exchange = VALUES(exchange),
           connected = VALUES(connected), last_error = NULL,
           updated_at = CURRENT_TIMESTAMP`,
        [uid, venue, connected]
      );
      applied++;
    }
    res.json({ ok: true, applied, rejected });
  } catch (err) {
    console.error('Cred ack error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to ack credentials' });
  }
});

/**
 * GET /api/bot/sync/controls/pending
 * Bot pulls pending live-control changes (live on/off, margin cap, pause).
 * Bot-secret authed.
 */
router.get('/controls/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT user_id, telegram_id, live_enabled, max_margin, paused, venues, created_at
       FROM pending_controls ORDER BY created_at ASC LIMIT 200`
    );
    res.json({ pending: rows });
  } catch (err) {
    console.error('Controls pending fetch error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch pending controls' });
  }
});

/**
 * POST /api/bot/sync/controls/ack
 * Body: { acks: [{ user_id, live_enabled, max_margin, paused, allowlisted, ok }] }
 * Bot reports the APPLIED state (from its UserStore). Clears the pending row and
 * mirrors the state into user_controls for the web UI. Bot-secret authed.
 */
router.post('/controls/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    for (const a of acks) {
      if (!a || a.user_id == null || !a.ok) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      await pool.execute('DELETE FROM pending_controls WHERE user_id = ?', [uid]);
      await pool.execute(
        `INSERT INTO user_controls (user_id, live_enabled, max_margin, paused, allowlisted, venues, venues_mode)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON DUPLICATE KEY UPDATE live_enabled = VALUES(live_enabled),
           max_margin = VALUES(max_margin), paused = VALUES(paused),
           allowlisted = VALUES(allowlisted), venues = VALUES(venues),
           venues_mode = VALUES(venues_mode), updated_at = CURRENT_TIMESTAMP`,
        [uid, a.live_enabled ? 1 : 0,
         (a.max_margin === null || a.max_margin === undefined) ? null : Number(a.max_margin),
         a.paused ? 1 : 0, a.allowlisted ? 1 : 0,
         // The venues the bot ACTUALLY holds. `undefined` (an older bot that
         // does not send the field) writes NULL — "we have not been told" —
         // rather than '' , which would claim the bot had cleared the
         // selection. An ack that omits a field has said nothing about it.
         (a.venues === null || a.venues === undefined) ? null : String(a.venues),
         // NULL from an older bot means "we have not been told", which the UI
         // must not render as `off` — that would be a confident claim about a
         // control nobody reported on.
         (a.venues_mode === null || a.venues_mode === undefined)
           ? null : String(a.venues_mode)]
      );
      applied++;
    }
    res.json({ ok: true, applied });
  } catch (err) {
    console.error('Controls ack error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to ack controls' });
  }
});

/**
 * GET /api/bot/sync/flatten/pending  — bot pulls emergency-stop flatten requests.
 * POST /api/bot/sync/flatten/ack { acks:[{user_id, ok}] } — clear completed ones.
 * Bot-secret authed. The bot closes the user's positions via THEIR own executor
 * before acking, so a failed close is retried next poll (row is left in place).
 */
router.get('/flatten/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT user_id, telegram_id, created_at FROM pending_flatten ORDER BY created_at ASC LIMIT 200');
    res.json({ pending: rows });
  } catch (err) {
    console.error('Flatten pending error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch flatten requests' });
  }
});

router.post('/flatten/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    for (const a of acks) {
      if (!a || a.user_id == null || !a.ok) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      await pool.execute('DELETE FROM pending_flatten WHERE user_id = ?', [uid]);
      applied++;
    }
    res.json({ ok: true, applied });
  } catch (err) {
    console.error('Flatten ack error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to ack flatten' });
  }
});

/**
 * GET /api/bot/sync/leaderboard/pending
 * DESIRED-STATE pull (not a queue — no ack needed, idempotent): every user who
 * has opted in to the public leaderboard (anonymous handle set) AND has a
 * linked bot account. The bot publishes each user's own sealed, size-agnostic
 * statement under that handle and reconcile-removes handles that drop out of
 * this set, so opt-out (handle cleared) takes effect on the next pull.
 * Bot-secret authed.
 */
router.get('/leaderboard/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT id AS user_id, telegram_id, leaderboard_handle AS handle
         FROM users
        WHERE leaderboard_handle IS NOT NULL AND telegram_id IS NOT NULL
        LIMIT 500`
    );
    res.json({ optins: rows });
  } catch (err) {
    console.error('Leaderboard pending fetch error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch leaderboard opt-ins' });
  }
});

/**
 * Telegram-parity reads (bot-secret authed): the bot renders the SAME
 * Node-side intelligence surfaces the web panels use — /exposure /research
 * /rwa on Telegram call these instead of duplicating the logic in Python.
 * All read-only; exposure maps the caller's telegram_id to their web account.
 */
router.get('/exposure', async (req, res) => {
  try {
    const tg = String(req.query.telegram_id || '').slice(0, 32);
    if (!tg) return res.status(400).json({ error: 'telegram_id required' });
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE telegram_id = ?', [tg]);
    if (!rows.length) return res.status(404).json({ error: 'No linked web account' });
    res.json(await require('../lib/exposure').buildExposure(rows[0].id));
  } catch (err) {
    console.error('Sync exposure error:', err.stack || err.message);
    res.status(500).json({ error: 'Exposure unavailable' });
  }
});

router.get('/research/:symbol', async (req, res) => {
  try {
    const base = String(req.params.symbol || '').toUpperCase()
      .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
    if (!base) return res.status(400).json({ error: 'symbol required' });
    const d = await require('../lib/research').buildDossier(base);
    if (!d) return res.status(404).json({ error: 'Not listed on the venue — no trusted data' });
    res.json(d);
  } catch (err) {
    console.error('Sync research error:', err.stack || err.message);
    res.status(500).json({ error: 'Research unavailable' });
  }
});

router.get('/rwa', async (req, res) => {
  try {
    res.json(await require('../lib/rwa').getRadar());
  } catch (err) {
    console.error('Sync rwa error:', err.stack || err.message);
    res.status(500).json({ error: 'RWA radar unavailable' });
  }
});

// DEX taker-flow radar for the engine's gated on-chain voter (PR JJ) — the
// bot pulls the SAME payload the public Markets panel renders.
router.get('/onchain-flow', async (req, res) => {
  try {
    res.json(await require('../lib/onchain_flow').getFlowRadar());
  } catch (err) {
    console.error('Sync onchain-flow error:', err.stack || err.message);
    res.status(500).json({ error: 'Flow radar unavailable' });
  }
});

// Read-side accessor for routes/guardian.js: in-memory first, DB on cold start.
async function getLatestFlight() {
  if (latestFlight) return latestFlight;
  try {
    const [rows] = await pool.execute(
      'SELECT flight_json FROM flight_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].flight_json) {
      latestFlight = JSON.parse(rows[0].flight_json);
    }
  } catch (err) { /* cold-start miss / table absent is fine */ }
  return latestFlight;
}

/**
 * Daily Duel over the bot channel.
 *
 * The Telegram surface is deliberately THIN: it reads the card and records a
 * call through lib/duel_service, the same code the web route uses. Scoring the
 * duel a second time in Python would be a second set of rules that agreed with
 * the first only until one of them changed.
 *
 * Identity is resolved here from the linked Telegram id, never trusted from
 * the body — the bot says who is asking, and the mapping to a web account is
 * this side's to make.
 */
async function duelUserFor(req) {
  const tg = String(req.query.telegram_id || (req.body || {}).telegram_id || '').slice(0, 32);
  if (!tg) return { error: 'telegram_id required', status: 400 };
  const [rows] = await pool.execute('SELECT id FROM users WHERE telegram_id = ?', [tg]);
  if (!rows.length) return { error: 'No linked web account', status: 404 };
  return { id: rows[0].id };
}

router.get('/duel', async (req, res) => {
  try {
    const who = await duelUserFor(req);
    if (who.error) return res.status(who.status).json({ error: who.error });
    const svc = require('../lib/duel_service');
    const [card, rec] = [await svc.cardFor(who.id), await svc.recordFor(who.id)];
    res.json({
      day: card.day,
      horizon_hours: card.horizon_hours,
      rounds: card.rounds,
      open: card.open,
      accuracy: rec.accuracy,
      marks: rec.marks,
      streak: rec.streak,
      counts_only: true,
    });
  } catch (err) {
    console.error('Sync duel error:', err.stack || err.message);
    res.status(503).json({ error: 'The duel card is unavailable', reason: err.rcReason || null });
  }
});

router.post('/duel/pick', async (req, res) => {
  try {
    const who = await duelUserFor(req);
    if (who.error) return res.status(who.status).json({ error: who.error });
    const body = req.body || {};
    const out = await require('../lib/duel_service')
      .placePick(who.id, Number(body.round_id), body.pick);
    if (!out.ok) {
      const { status, ...rest } = out;
      return res.status(status).json(rest);
    }
    res.json(out);
  } catch (err) {
    console.error('Sync duel pick error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not record your call' });
  }
});

module.exports = router;
// Named accessor for routes/reports.js (in-memory + DB cold-start fallback).
module.exports.getLatestReports = getLatestReports;
module.exports.readReports = readReports;
// Named accessor for routes/macro.js + lib/status.js. In-memory first, then the
// DB (scan_cache) on cold start — so a web restart (every deploy on an
// ephemeral host) doesn't reset the scan to "no data" while the last engine
// push is still sitting in the DB. Mirrors getLatestReports().
async function getLatestScan() {
  if (latestScan) return latestScan;
  try {
    const [rows] = await pool.execute('SELECT scan_json FROM scan_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].scan_json) {
      latestScan = JSON.parse(rows[0].scan_json);
    }
  } catch (err) { /* cold-start miss / table absent is fine */ }
  return latestScan;
}
module.exports.getLatestScan = getLatestScan;
// Named accessor for routes/guardian.js — the Flight Recorder ledger mirror.
module.exports.getLatestFlight = getLatestFlight;
// Exported so the redaction can be TESTED, not just read. The first version of
// its test reimplemented `req.user ? summary : scrub(summary)` locally and
// therefore passed with the real function reverted to returning the raw
// summary — a vacuous test of the exact kind this audit keeps finding.
module.exports.summaryFor = summaryFor;
// Same reason, same lesson: `scanFor` is exported so its test calls the
// SHIPPED function rather than re-deriving the answer next to it.
module.exports.scanFor = scanFor;
