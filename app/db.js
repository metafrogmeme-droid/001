/**
 * Database layer - uses MySQL (TiDB) when DATABASE_URL is available,
 * falls back to in-memory storage for demo/development.
 */

let pool = null;
let memDb = null;
let USE_MYSQL = !!process.env.DATABASE_URL;

// Which statement is in flight, as a SHORT descriptor — "CREATE TABLE users".
// migrate() runs 80+ statements; a bare ER_PARSE_ERROR from the server names
// none of them, so an operator sees "the database failed" and cannot tell
// which DDL a MySQL-compatible-but-not-identical server rejected. Recording
// the descriptor turns that into one line. It is a verb and an object name
// from DDL this repository authors — no user data, no values, no secrets.
let _lastStatement = null;

/** The last statement descriptor attempted, or null. Never throws. */
function lastStatement() {
  return _lastStatement;
}

/** 'CREATE TABLE IF NOT EXISTS users (…' → 'CREATE TABLE users'. */
function describeSql(sql) {
  try {
    const flat = String(sql || '').replace(/\s+/g, ' ').trim();
    const m = flat.match(
      /^(CREATE TABLE|CREATE INDEX|ALTER TABLE|DROP TABLE|CREATE UNIQUE INDEX)\s+(?:IF NOT EXISTS\s+)?(?:IF EXISTS\s+)?`?([A-Za-z0-9_]+)`?/i);
    if (m) return `${m[1].toUpperCase()} ${m[2]}`;
    return flat.slice(0, 40);
  } catch (e) {
    return null;
  }
}

/**
 * A mysql2 pool config from a connection URL, with `?ssl=` normalised.
 *
 * mysql2 accepts several spellings of the `ssl` query parameter and rejects
 * others outright, BEFORE a single packet is sent:
 *
 *   ?ssl={"rejectUnauthorized":true}   accepted (raw or percent-encoded)
 *   ?ssl=Amazon%20RDS                  accepted — a named bundled CA profile
 *   ?ssl=true  ·  ?ssl=1               THROWS "SSL profile must be an object,
 *                                      instead it's a boolean"
 *
 * That last spelling is the one most connection-string builders emit, and the
 * failure it produces is indistinguishable from a database outage at a glance:
 * the pool cannot be created, this file fails closed and loud, and the operator
 * goes looking at the database. It is a string format.
 *
 * So the boolean spellings are converted to the object mysql2 wanted, and
 * everything else is passed through untouched — a named profile still resolves
 * to its bundled CA, and an explicit object is still honoured verbatim. The
 * secure reading of a bare `ssl=true` is that the certificate SHOULD be
 * verified, so it becomes `{rejectUnauthorized: true}` rather than a disabled
 * check; nothing here can weaken a TLS setting the operator asked for.
 *
 * TWO MORE SPELLINGS, BOTH FOUND IN PRODUCTION.
 *
 * The mechanism behind all of this is one line in mysql2's own URL parser
 * (`connection_config.js`): every query parameter is fed to `JSON.parse` and
 * falls back to a plain string when that throws. So the value's JSON validity
 * — not its meaning — decides what mysql2 receives.
 *
 *   ?ssl={"rejectUnauthorized":true}  valid JSON  → an object. WORKS, and this
 *                                    function must not touch it: intercepting
 *                                    it would discard a `ca`, or override an
 *                                    explicit `rejectUnauthorized:false`.
 *   ?ssl={rejectUnauthorized:true}    NOT JSON (bare key) → a string → mysql2
 *                                    looks it up as a named profile and throws
 *                                    "Unknown SSL profile". Normalised here.
 *
 *   ?ssl-mode=REQUIRED · ?sslmode=require · ?sslaccept=strict
 *                                    mysql2 does not know these keys. It prints
 *                                    "Ignoring invalid configuration option"
 *                                    to stderr and CONNECTS IN PLAINTEXT.
 *
 * That last one is the only fail-OPEN case in the set, which makes it the
 * dangerous one: a URL that asked for TLS, credentials on the wire, and a
 * warning on a stream nobody reads. DigitalOcean, Aiven and PlanetScale all
 * emit that spelling. A requested-but-unhonoured TLS setting is exactly the
 * "absent is never a measurement" rule at the network layer, so the modes that
 * unambiguously REQUIRE encryption are converted rather than dropped. Values
 * that merely prefer it (`PREFERRED`, `allow`) are left alone — plaintext is
 * within what they permit, and guessing past that would be inventing intent.
 *
 * IT NEVER LOGS OR RETURNS THE URL. It carries the password, and a connection
 * error that quotes it would put credentials in the log this file prints on the
 * way down.
 */

/** Keys that request TLS in a spelling mysql2 silently ignores. */
const TLS_MODE_KEYS = ['ssl-mode', 'sslmode', 'sslaccept'];
/** Values on those keys that REQUIRE encryption; anything else is not assumed. */
const TLS_MODE_REQUIRES = new Set(['required', 'require', 'strict',
  'verify_ca', 'verify-ca', 'verify_identity', 'verify-full', 'verify-identity']);

/**
 * `{rejectUnauthorized:true}` — an object literal that is not valid JSON.
 * Quote the bare keys and try once. NEVER `eval`: this string arrives from the
 * environment, and looking like an object literal is not a licence to execute
 * it. Unparseable input returns null and the caller passes the URL through, so
 * mysql2 names the problem instead of this function guessing at it.
 */
function looseObject(text) {
  const quoted = text
    .replace(/'/g, '"')
    .replace(/([{,]\s*)([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":');
  try {
    const v = JSON.parse(quoted);
    return v && typeof v === 'object' && !Array.isArray(v) ? v : null;
  } catch (_) {
    return null;
  }
}

function poolConfigFrom(rawUrl) {
  let u;
  try {
    u = new URL(rawUrl);
  } catch (_) {
    // Not parseable as a URL — hand it to mysql2 unchanged so the driver
    // produces its own error. Guessing at a malformed URL is worse than
    // letting the thing that owns the format complain about it.
    return rawUrl;
  }

  const ssl = u.searchParams.get('ssl');
  if (ssl !== null) {
    const raw = ssl.trim();
    const flag = raw.toLowerCase();
    if (flag === 'true' || flag === '1') {
      u.searchParams.delete('ssl');
      return { uri: u.toString(), ssl: { rejectUnauthorized: true } };
    }
    if (raw.startsWith('{')) {
      // Valid JSON already reaches mysql2 as the object the operator wrote.
      // Returning here rather than re-deriving it is the whole point: the
      // re-derived version cannot carry a `ca`, and would silently upgrade a
      // deliberate `rejectUnauthorized:false` into a connection that fails.
      try {
        JSON.parse(raw);
        return rawUrl;
      } catch (_) { /* not JSON — the bare-key form, normalised below */ }
      const opts = looseObject(raw);
      if (!opts) return rawUrl;
      u.searchParams.delete('ssl');
      return { uri: u.toString(), ssl: opts };
    }
    // A named profile, `false`, `0`, or something unrecognised: mysql2 owns
    // the meaning of all of those and handles them without help.
    return rawUrl;
  }

  // No `ssl` param. Does another spelling ask for TLS in a way mysql2 drops?
  let requiresTls = false;
  let found = false;
  for (const key of TLS_MODE_KEYS) {
    const v = u.searchParams.get(key);
    if (v === null) continue;
    found = true;
    if (TLS_MODE_REQUIRES.has(v.trim().toLowerCase())) requiresTls = true;
  }
  // Only rewrite when the answer is unambiguous. A `PREFERRED` left in place
  // still draws mysql2's warning, which is correct — we did not understand it,
  // so we do not quietly absorb it.
  if (!found || !requiresTls) return rawUrl;

  for (const key of TLS_MODE_KEYS) u.searchParams.delete(key);
  return { uri: u.toString(), ssl: { rejectUnauthorized: true } };
}

if (USE_MYSQL) {
  try {
    const mysql = require('mysql2/promise');
    pool = mysql.createPool(poolConfigFrom(process.env.DATABASE_URL));
    // Wrap once, permanently: every execute/query records what it is about to
    // run. Cheap (a regex on a string this file authored), and it cannot
    // change behaviour — it delegates unconditionally and never swallows.
    for (const fn of ['execute', 'query']) {
      const orig = pool[fn].bind(pool);
      pool[fn] = (sql, ...rest) => {
        try { _lastStatement = describeSql(sql); } catch (e) { /* never block a query */ }
        return orig(sql, ...rest);
      };
    }
    console.log('Using MySQL database');
  } catch (err) {
    // FAIL CLOSED. This used to log and set `USE_MYSQL = false`, which
    // demoted a production deployment to an empty in-memory store and kept
    // serving 200s. Every panel then rendered "no trades", "no positions",
    // "no data" — each one truthful about the store it was reading and each
    // one a lie about the account. A 500 is a bad minute; a 200-serving
    // amnesiac is a user concluding their positions are gone.
    //
    // `DATABASE_URL` being set IS the operator saying "use this database".
    // The only honest responses to "I cannot" are to say so and stop. Note
    // that `createPool` is inside this try as well, so a malformed URL — a
    // typo — took the same silent path; `mysql2` is a declared dependency, so
    // a genuine import failure means a broken install, not a missing extra.
    //
    // Same rule bot/utils/state_guard.py already applies to the bot: "Fail
    // CLOSED and LOUD. An operator who sees this refuse has a five-second
    // fix; an operator who does not see it loses the vault and finds out days
    // later."
    console.error('FATAL: DATABASE_URL is set but the MySQL pool could not be '
      + 'created. Refusing to start on an in-memory database — it would serve '
      + 'empty results as if they were real.');
    console.error('  cause:', err.stack || err.message);
    console.error('  unset DATABASE_URL to run deliberately in-memory.');
    throw new Error(`database unavailable: ${err.message}`);
  }
}

// ── In-memory database ──────────────────────────────────────────

class MemoryDB {
  /**
   * Which in-memory store backs each `user_id`-keyed table, for erasure.
   *
   * `key: 'user_id'` means the object is keyed BY the user id, so the delete
   * is one property lookup. The plain objects without it (`walletLinkCodes`)
   * are keyed by something else and carry `user_id` in the VALUE, which is the
   * distinction the old wallet_link_codes branch got wrong.
   *
   * Pinned against `account_erasure.js` by a test: a table added to the
   * erasure list with no store here would throw at runtime, and this map is
   * the only place that knowledge lives.
   */
  static USER_SCOPED_STORES = {
    pending_credentials: { field: 'pendingCreds' },
    exchange_status: { field: 'exchangeStatus', key: 'user_id' },
    pending_controls: { field: 'pendingControls' },
    user_controls: { field: 'userControls', key: 'user_id' },
    pending_flatten: { field: 'pendingFlatten' },
    arena_api_keys: { field: 'arenaApiKeys' },
    arena_envelopes: { field: 'envelopes' },
    trades: { field: 'trades' },
    equity_snapshots: { field: 'snapshots' },
    arena_accounts: { field: 'arenaAccounts', key: 'user_id' },
    arena_positions: { field: 'arenaPositions' },
    arena_trades: { field: 'arenaTrades' },
    wallet_link_codes: { field: 'walletLinkCodes' },
    push_subscriptions: { field: 'pushSubs' },
    copy_subscriptions: { field: 'copySubs' },
    user_profiles: { field: 'userProfiles', key: 'user_id' },
    user_alerts: { field: 'userAlerts' },
    user_strategies: { field: 'userStrategies' },
    user_watchlist: { field: 'watchlist' },
    arena_follows: { field: 'arenaFollows', key: 'user_id' },
    duel_picks: { field: 'duelPicks' },
    learn_diary: { field: 'learnDiary' },
    learn_progress: { field: 'learnProgress' },
  };

  // ── Transactions ────────────────────────────────────────────────────────
  //
  // The suite and the no-DATABASE_URL deployment mode both run on this class.
  // Without these, `withTransaction` could only ever be exercised against a
  // live MySQL — so the rollback path would ship having never once executed
  // in a test, which is the shape this repo spends its guard tests on.
  //
  // The snapshot is GENERIC over own enumerable properties on purpose. A
  // hand-listed set of tables would silently miss the next one added to the
  // constructor, and a rollback that restores some tables and not others is
  // worse than no rollback at all: it leaves a tree nobody designed. Same
  // reason preflight parses ci.yml instead of restating it.
  _snapshot() {
    const snap = {};
    for (const k of Object.keys(this)) {
      const v = this[k];
      snap[k] = (v && typeof v === 'object') ? structuredClone(v) : v;
    }
    return snap;
  }

  _restore(snap) {
    for (const k of Object.keys(snap)) this[k] = snap[k];
    // A key created DURING the transaction is not in the snapshot; drop it,
    // or a rolled-back write survives as a table nobody rolled back.
    for (const k of Object.keys(this)) {
      if (!(k in snap)) delete this[k];
    }
  }

  async getConnection() {
    const db = this;
    let snap = null;
    return {
      execute: (...a) => db.execute(...a),
      query: (...a) => db.execute(...a),
      beginTransaction: async () => { snap = db._snapshot(); },
      commit: async () => { snap = null; },
      rollback: async () => {
        // Rolling back without a snapshot would silently do nothing and
        // report success — the caller believes its writes were undone.
        if (snap === null) throw new Error('rollback without beginTransaction');
        db._restore(snap);
        snap = null;
      },
      release: () => { snap = null; },
    };
  }

  constructor() {
    this.users = [];
    this.trades = [];
    this.snapshots = [];
    this._nextUserId = 1;
    this._nextTradeId = 1;
    this._nextSnapId = 1;
    this.scanCache = null; // { scan_json, updated_at }
    this.signals = [];     // global signal stream (UPSERT by signal_key)
    this._nextSignalId = 1;
    this.agentEvents = []; // public agent mind-stream feed (bounded ring)
    this._nextAgentEventId = 1;
    this.reportsCache = null;  // { reports_json, updated_at } (single row)
    this.flightCache = null;   // { flight_json, updated_at } (single row)
    this.walletLinkCodes = {};   // code -> { user_id, expires_at }
    this.walletLinkNonces = {};  // address -> { message, expires_at }
    this.userProfiles = {};    // user_id -> { risk_pref, watchlist, prefs }
    this.pushSubs = [];        // web-push subscriptions (UPSERT by endpoint)
    this._nextPushSubId = 1;
    this.pendingStance = null; // { mode, requested_by, telegram_id, created_at } (single row)
    this.pendingCreds = [];   // pending_credentials (UPSERT by user_id)
    this.sealingKey = null;   // bot_sealing_key: { kid, pem, alg, updated_at }
    this.exchangeStatus = {}; // user_id -> { connected }
    this.pendingControls = []; // pending_controls (UPSERT by user_id)
    this.userControls = {};   // user_id -> { live_enabled, max_margin, paused, allowlisted }
    this.pendingFlatten = []; // pending_flatten (UPSERT by user_id)
    this.userAlerts = [];     // custom "tell me when…" tripwires
    this._nextAlertId = 1;
    this.userStrategies = []; // user-authored marketplace strategies (config only)
    this._nextStrategyId = 1;
    this.agentLetters = [];   // weekly agent letters (UPSERT-free; one per week_key)
    this.learnDiary = [];     // study-room diary: one entry per (user_id, day)
    this.envelopes = [];      // armed authority envelopes: one per user_id
    this.learnProgress = [];  // study-room lessons read: one row per (user_id, slug)
    this.copySubs = [];       // strategy-agent follows (UNIQUE user_id+agent_id)
    this._nextCopySubId = 1;
    this.arenaAccounts = {};  // user_id -> { balance, created_at } (paper arena)
    this.arenaPositions = []; // open paper positions
    this._nextArenaPosId = 1;
    this.arenaTrades = [];    // closed paper trades (history)
    this._nextArenaTradeId = 1;
    this.arenaSeasons = [];   // named competition windows (no resets)
    this._nextArenaSeasonId = 1;
    this.arenaFollows = {};   // user_id -> practice-follow prefs (paper only)
    this.watchlist = [];      // { user_id, symbol, created_at } (starred symbols)
    this.sealRoots = [];      // { day, root, seal_count, computed_at } — immutable daily Merkle roots
    this.duelRounds = [];     // Daily Duel rounds, unique on (day, idx)
    this._nextDuelRoundId = 1;
    this.duelPicks = [];      // Daily Duel picks, unique on (user_id, round_id)
    this._nextDuelPickId = 1;
    // The arena key routes ARE implemented here now — see the ARENA API KEYS
    // branches below. They were not, which left mint/verify/bind/revoke
    // reachable only by source scan while the MCP write tools authenticate
    // through them.
    this.arenaApiKeys = [];
    this._nextArenaKeyId = 1;
    this.agents = [];         // { id, slug, user_id, display_name, seal, seal_payload, sealed_at }
    this._nextAgentId = 1;
    this.scanSeals = [];      // { id, scan_key, user_id, agent_slug, tool, seal, seal_payload, sealed_at }
    this._nextScanSealId = 1;
  }

  // Minimal query interface matching mysql2 pool.execute() return format
  async execute(sql, params = []) {
    const cmd = sql.trim().toUpperCase();

    // Refuse a bound LIMIT exactly like production does. mysql2's execute()
    // sends JS numbers as DOUBLE, and MySQL rejects a DOUBLE as a prepared
    // LIMIT argument — ER_WRONG_ARGUMENTS. The shim used to accept `LIMIT ?`
    // happily, which is how one placeholder in the follow sweep 500ed
    // GET /api/arena/account for every follower in production while the whole
    // suite stayed green. A shim that is more permissive than the database it
    // stands in for is not a test double, it is a blindfold.
    if (/LIMIT\s+\?/.test(cmd)) {
      const err = new Error('Incorrect arguments to LIMIT');
      err.code = 'ER_WRONG_ARGUMENTS';
      throw err;
    }

    // SCHEMA STATEMENTS ARE DELIBERATELY IGNORED, not unimplemented.
    //
    // The shim's "tables" are JS arrays whose shape is fixed in code, so DDL
    // has nothing to apply and no-opping is the correct answer — unlike a
    // SELECT or an INSERT, where having no branch means the caller is being
    // handed an invented result.
    //
    // Listed explicitly because the fall-through below now throws. `migrate()`
    // runs 80+ of these, each inside its own try/catch, so treating them as
    // unimplemented would raise twenty-odd exceptions on every boot and have
    // them all swallowed — noise that trains people to ignore the very signal
    // the throw exists to give. Caught by this file's own test rather than in
    // review.
    if (cmd.startsWith('CREATE TABLE') || cmd.startsWith('ALTER TABLE')
        || cmd.startsWith('CREATE INDEX') || cmd.startsWith('CREATE UNIQUE INDEX')
        || cmd.startsWith('DROP TABLE') || cmd.startsWith('DROP INDEX')) {
      return [[], []];
    }

    // ACCOUNT ERASURE — every `DELETE FROM <t> WHERE user_id = ?` in one place.
    //
    // `app/lib/account_erasure.js` emits this one statement shape against 23
    // tables. Half of them had no DELETE branch here at all, so the deletion
    // route threw ER_MEMORYDB_UNIMPLEMENTED on the first uncovered table — in
    // the suite AND in the no-DATABASE_URL deployment mode this class exists
    // to serve.
    //
    // Deliberately matched with an ANCHORED regex on the whole statement
    // rather than `cmd.includes('DELETE FROM …')`. Sitting this high in the
    // dispatcher, a substring test would shadow the narrower deletes below it
    // — `DELETE FROM user_alerts WHERE id = ? AND user_id = ?` deletes ONE
    // alert, and swallowing it here would silently erase the lot. Extra
    // clauses fall through to the branch that understands them.
    //
    // It also fixes a wrong answer that was already here: the generic
    // `DELETE FROM wallet_link_codes` branch treats `params[0]` as the CODE
    // (its primary key), so the erasure statement, which passes a user id,
    // deleted nothing and reported `affectedRows: 1` while doing it.
    const erase = cmd.match(/^DELETE FROM (\w+) WHERE USER_ID = \?$/);
    if (erase) {
      const store = MemoryDB.USER_SCOPED_STORES[erase[1].toLowerCase()];
      if (store) {
        const uid = String(params[0]);
        let removed = 0;
        if (store.key === 'user_id') {              // object keyed BY user id
          if (Object.prototype.hasOwnProperty.call(this[store.field], uid)) {
            delete this[store.field][uid]; removed = 1;
          }
        } else if (Array.isArray(this[store.field])) {
          const before = this[store.field].length;
          this[store.field] = this[store.field].filter(
            (r) => String(r.user_id) !== uid);
          removed = before - this[store.field].length;
        } else {                                    // object whose VALUES carry it
          for (const k of Object.keys(this[store.field])) {
            if (String(this[store.field][k].user_id) === uid) {
              delete this[store.field][k]; removed += 1;
            }
          }
        }
        return [{ affectedRows: removed }, []];
      }
      // No entry in the map is NOT "nothing to delete" — it is an unmapped
      // table, and answering `affectedRows: 0` would report a successful
      // erasure of rows nobody looked for. Fall through to the throw.
    }

    // -- SIGNALS -- (checked before TRADES: the stats query shares COUNT(*)/wins
    // aliases with trade handlers, so it must match here first.)
    if (cmd.includes('INSERT INTO SIGNALS')) {
      // params: signal_key, symbol, direction, confidence, score, pattern,
      // regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
      // created_at, resolved_at. ON DUPLICATE KEY updates status/pnl/resolved_at.
      const cols = ['signal_key','symbol','direction','confidence','score','pattern',
        'regime','entry_price','stop_loss','take_profit','rr','thesis','status','pnl',
        'created_at','resolved_at','seal','seal_payload','sealed_at'];
      const row = {}; cols.forEach((k, i) => { row[k] = params[i]; });
      const existing = this.signals.find(s => s.signal_key === row.signal_key);
      if (existing) {
        // Provable Calls: ON DUPLICATE updates outcome fields ONLY — the
        // seal, payload and every decision-time value stay untouched.
        existing.status = row.status; existing.pnl = row.pnl; existing.resolved_at = row.resolved_at;
      } else {
        row.id = this._nextSignalId++;
        this.signals.push(row);
      }
      return [{ affectedRows: 1 }, []];
    }

    if (cmd.includes('FROM SIGNALS') && cmd.includes('COUNT(*)') && cmd.includes('CREATED_AT >=')) {
      // welcome-back digest: signals since a cutoff
      const lo = new Date(params[0]).getTime();
      const n = this.signals.filter(s => new Date(s.created_at).getTime() >= lo).length;
      return [[{ n }], []];
    }
    if (cmd.includes('FROM SIGNALS') && cmd.includes('COUNT(*)')) {
      const resolved = this.signals.filter(s => s.pnl !== null && s.pnl !== undefined);
      const wins = resolved.filter(s => Number(s.pnl) > 0).length;
      const net_pnl = resolved.reduce((a, s) => a + (Number(s.pnl) || 0), 0);
      return [[{ resolved: resolved.length, wins, net_pnl }], []];
    }

    if (cmd.includes('FROM SIGNALS') && cmd.includes('SIGNAL_KEY = ?')) {
      // Provable Calls verify: one sealed row by its stable key
      const rows = this.signals.filter(s => s.signal_key === params[0]);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM SIGNALS') && cmd.includes('SEALED_AT >=')) {
      // Daily seal roots: seals minted inside one UTC day window
      const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
      const rows = this.signals.filter(s => s.sealed_at
        && new Date(s.sealed_at).getTime() >= lo && new Date(s.sealed_at).getTime() < hi);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM SIGNALS') && cmd.includes('RESOLVED_AT >=')) {
      // daily digest: rows resolved since a cutoff (NULL resolved_at never
      // passes a >= comparison, mirroring MySQL)
      const lo = new Date(params[0]).getTime();
      const rows = this.signals.filter(
        s => s.resolved_at && new Date(s.resolved_at).getTime() >= lo);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM SIGNALS') && cmd.includes('WHERE ID = ?')) {
      // Open ONE named signal as a paper trade.
      //
      // Without this branch the catch-all below took over, and the catch-all
      // ignores WHERE and reads the LAST PARAM as a LIMIT — so asking for
      // signal 3 returned the three newest signals and the route opened
      // srows[0], a different call than the user clicked. It would have looked
      // perfectly fine in tests and traded the wrong symbol in production.
      const id = Number(params[0]);
      const rows = this.signals.filter(s => Number(s.id) === id);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM SIGNALS') && cmd.includes('ID >')) {
      // Practice-follow sweep: WHERE id > ? ORDER BY id ASC LIMIT 5. The
      // limit comes from the inline SQL — reading it from the last param
      // would now misread the cursor id as a row count.
      const after = Number(params[0]) || 0;
      const m = cmd.match(/LIMIT\s+(\d+)/);
      const lim = m ? Number(m[1]) : 5;
      const rows = this.signals.filter(s => Number(s.id) > after)
        .sort((a, b) => Number(a.id) - Number(b.id)).slice(0, lim);
      return [rows.map(r => ({ ...r })), []];
    }

    if (cmd.includes('FROM SIGNALS')) {
      // Filters are ignored in the mock; newest-first up to the LIMIT. An
      // INLINE `LIMIT 12` wins over the last param — reading the last param
      // when the statement has none produced NaN and silently fell back to 50,
      // so a query that asked for 12 rows got 50 here and 12 in MySQL.
      const inline = cmd.match(/LIMIT\s+(\d+)/);
      const limit = inline ? Number(inline[1]) : (parseInt(params[params.length - 1]) || 50);
      const rows = [...this.signals]
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        .slice(0, limit);
      return [rows, []];
    }

    // -- AGENT LETTERS (weekly fund-style letter; one row per ISO week) --
    // -- LEARN PROGRESS (study room lessons; one row per user per slug) --
    if (cmd.includes('INSERT INTO LEARN_PROGRESS')) {
      // params: user_id, slug, done_at. ON DUPLICATE keeps the FIRST done_at
      // (re-reading never rewrites history); a bare insert throws like MySQL.
      const existing = this.learnProgress.find(
        (e) => e.user_id === params[0] && e.slug === params[1]);
      if (existing) {
        if (!cmd.includes('ON DUPLICATE KEY UPDATE')) {
          const err = new Error(`Duplicate entry '${params[0]}-${params[1]}' for key 'uq_learn_prog'`);
          err.code = 'ER_DUP_ENTRY';
          throw err;
        }
        return [{ affectedRows: 0 }, []];
      }
      this.learnProgress.push({ user_id: params[0], slug: params[1], done_at: params[2] });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM LEARN_PROGRESS')) {
      const rows = this.learnProgress.filter((e) => e.user_id === params[0]);
      return [rows.map((r) => ({ ...r })), []];
    }

    // -- ARENA ENVELOPES (armed authority envelopes; one per user) --
    if (cmd.includes('INSERT INTO ARENA_ENVELOPES')) {
      // params: user_id, source_text, rules_json, created_at. PK(user_id):
      // ON DUPLICATE upserts (re-arming replaces the envelope); a bare
      // duplicate INSERT throws exactly as MySQL would.
      const existing = this.envelopes.find(e => e.user_id === params[0]);
      if (existing) {
        if (!cmd.includes('ON DUPLICATE KEY UPDATE')) {
          const err = new Error(`Duplicate entry '${params[0]}' for key 'PRIMARY'`);
          err.code = 'ER_DUP_ENTRY';
          throw err;
        }
        existing.source_text = params[1];
        existing.rules_json = params[2];
        existing.enabled = 1;
        return [{ affectedRows: 2 }, []];
      }
      this.envelopes.push({ user_id: params[0], source_text: params[1],
        rules_json: params[2], enabled: 1, created_at: params[3] });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM ARENA_ENVELOPES') && !cmd.startsWith('DELETE')) {
      const rows = this.envelopes.filter(x => x.user_id === params[0]);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('DELETE FROM ARENA_ENVELOPES')) {
      const before = this.envelopes.length;
      this.envelopes = this.envelopes.filter(x => x.user_id !== params[0]);
      return [{ affectedRows: before - this.envelopes.length }, []];
    }

    // -- LEARN DIARY (study room; one entry per user per UTC day) --
    if (cmd.includes('INSERT INTO LEARN_DIARY')) {
      // params: user_id, day, body, created_at. Unique (user_id, day) is
      // enforced exactly as MySQL enforces it: with ON DUPLICATE the second
      // write upserts (body replaced, edited_at set — a race loser IS a
      // second write); without it, ER_DUP_ENTRY throws.
      const existing = this.learnDiary.find(e => e.user_id === params[0] && e.day === params[1]);
      if (existing) {
        if (!cmd.includes('ON DUPLICATE KEY UPDATE')) {
          const err = new Error(`Duplicate entry '${params[0]}-${params[1]}' for key 'uq_learn_user_day'`);
          err.code = 'ER_DUP_ENTRY';
          throw err;
        }
        existing.body = params[2];
        existing.edited_at = params[3];
        return [{ affectedRows: 2 }, []];
      }
      this.learnDiary.push({ user_id: params[0], day: params[1], body: params[2],
        created_at: params[3], edited_at: null });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM LEARN_DIARY')) {
      if (cmd.includes('AND DAY = ?')) {
        const rows = this.learnDiary.filter(x => x.user_id === params[0] && x.day === params[1]);
        return [rows.map(r => ({ ...r })), []];
      }
      // list: WHERE user_id = ? ORDER BY day DESC LIMIT 60 (inline limit)
      const rows = this.learnDiary.filter(x => x.user_id === params[0])
        .sort((a, b) => String(b.day).localeCompare(String(a.day)))
        .slice(0, 60);
      return [rows.map(r => ({ ...r })), []];
    }

    if (cmd.includes('INSERT INTO AGENT_LETTERS')) {
      // params: week_key, generated_at, letter_json
      if (!this.agentLetters.some(l => l.week_key === params[0])) {
        this.agentLetters.push({ week_key: params[0], generated_at: params[1],
          letter_json: params[2] });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM AGENT_LETTERS')) {
      if (cmd.includes('WHERE WEEK_KEY')) {
        return [this.agentLetters.filter(l => l.week_key === params[0]).map(r => ({ ...r })), []];
      }
      const rows = [...this.agentLetters]
        .sort((a, b) => String(b.week_key).localeCompare(String(a.week_key)))
        .slice(0, 52)
        .map(({ week_key, generated_at }) => ({ week_key, generated_at }));
      return [rows, []];
    }

    // -- USER ALERTS (custom "tell me when…" tripwires; one-shot) --
    // Checked before USERS handlers: 'USER_ALERTS' must never fall through
    // to a substring match on 'USERS'.
    if (cmd.includes('INSERT INTO USER_ALERTS')) {
      // params: user_id, symbol, metric, op, threshold, mode, cooldown_min, created_at
      this.userAlerts.push({
        id: this._nextAlertId++, user_id: params[0], symbol: params[1],
        metric: params[2], op: params[3], threshold: params[4],
        mode: params[5], cooldown_min: params[6],
        active: 1, trigger_price: null, created_at: params[7], triggered_at: null,
      });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM USER_ALERTS') && cmd.includes('COUNT(*)')) {
      const n = this.userAlerts.filter(
        a => a.user_id === params[0] && a.active === 1).length;
      return [[{ n }], []];
    }
    if (cmd.includes('UPDATE USER_ALERTS')) {
      // Two shapes share the params (triggered_at, trigger_price, id [, cutoff]):
      //  one-shot disarm  … SET ACTIVE = 0, …          WHERE id AND active = 1
      //  recurring restamp … SET triggered_at, … WHERE id AND active = 1
      //                      AND (triggered_at IS NULL OR triggered_at <= ?)
      const a = this.userAlerts.find(x => x.id === params[2] && x.active === 1);
      if (!a) return [{ affectedRows: 0 }, []];
      if (cmd.includes('ACTIVE = 0')) {
        a.active = 0;
      } else if (params.length > 3) {
        // cooldown guard: only restamp if the last fire is old enough
        const cutoff = new Date(params[3]).getTime();
        const last = a.triggered_at ? new Date(a.triggered_at).getTime() : null;
        if (last !== null && last > cutoff) return [{ affectedRows: 0 }, []];
      }
      a.triggered_at = params[0]; a.trigger_price = params[1];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM USER_ALERTS')) {
      // params: id, user_id (own rows only)
      const before = this.userAlerts.length;
      this.userAlerts = this.userAlerts.filter(
        a => !(a.id === params[0] && a.user_id === params[1]));
      return [{ affectedRows: before - this.userAlerts.length }, []];
    }
    if (cmd.includes('FROM USER_ALERTS')) {
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.userAlerts.filter(a => a.user_id === params[0])
          .sort((a, b) => b.id - a.id).slice(0, 50);
        return [rows.map(r => ({ ...r })), []];
      }
      // engine sweep: WHERE active = 1
      return [this.userAlerts.filter(a => a.active === 1).map(r => ({ ...r })), []];
    }

    // -- USER STRATEGIES (user-authored marketplace strategies; config only) --
    // Placed before USERS handlers so a substring match on 'USERS' can't swallow
    // these ('USER_STRATEGIES' shares the 'USER' prefix). No dollar fields (§4).
    if (cmd.includes('INSERT INTO USER_STRATEGIES')) {
      // params: user_id, slug, name, tagline, how, icon, rules, risk_label, regime, horizon, created_at, updated_at
      this.userStrategies.push({
        id: this._nextStrategyId++, user_id: params[0], slug: params[1], name: params[2],
        tagline: params[3], how: params[4], icon: params[5], rules: params[6],
        risk_label: params[7], regime: params[8], horizon: params[9],
        visibility: 'draft', published_at: null, created_at: params[10], updated_at: params[11],
      });
      return [{ affectedRows: 1, insertId: this._nextStrategyId - 1 }, []];
    }
    if (cmd.includes('FROM USER_STRATEGIES') && cmd.includes('COUNT(*)')) {
      const pub = cmd.includes("VISIBILITY = 'PUBLIC'");
      const n = this.userStrategies.filter(
        s => s.user_id === params[0] && (!pub || s.visibility === 'public')).length;
      return [[{ n }], []];
    }
    if (cmd.includes('UPDATE USER_STRATEGIES')) {
      if (cmd.includes('SET VISIBILITY')) {
        if (cmd.includes('PUBLISHED_AT')) {
          // publish — params: updated_at, published_at, id, user_id.
          // COALESCE semantics: the FIRST publish date sticks forever, so no
          // publish/unpublish cycle can ever buy a fresher position.
          const s = this.userStrategies.find(x => x.id === params[2] && x.user_id === params[3]);
          if (!s) return [{ affectedRows: 0 }, []];
          s.visibility = 'public'; s.updated_at = params[0];
          if (!s.published_at) s.published_at = params[1];
          return [{ affectedRows: 1 }, []];
        }
        // unpublish — params: visibility, updated_at, id, user_id
        const s = this.userStrategies.find(x => x.id === params[2] && x.user_id === params[3]);
        if (!s) return [{ affectedRows: 0 }, []];
        s.visibility = params[0]; s.updated_at = params[1];
        return [{ affectedRows: 1 }, []];
      }
      // full-row edit — params: name, tagline, how, icon, rules, risk_label, regime, horizon, updated_at, id, user_id
      const s = this.userStrategies.find(x => x.id === params[9] && x.user_id === params[10]);
      if (!s) return [{ affectedRows: 0 }, []];
      s.name = params[0]; s.tagline = params[1]; s.how = params[2]; s.icon = params[3];
      s.rules = params[4]; s.risk_label = params[5]; s.regime = params[6]; s.horizon = params[7];
      s.updated_at = params[8];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM USER_STRATEGIES')) {
      // params: id, user_id (own rows only)
      const before = this.userStrategies.length;
      this.userStrategies = this.userStrategies.filter(
        s => !(s.id === params[0] && s.user_id === params[1]));
      return [{ affectedRows: before - this.userStrategies.length }, []];
    }
    if (cmd.includes('FROM USER_STRATEGIES')) {
      if (cmd.includes('AND USER_ID')) {
        // getById: WHERE id = ? AND user_id = ?
        const rows = this.userStrategies.filter(s => s.id === params[0] && s.user_id === params[1]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.userStrategies.filter(s => s.user_id === params[0])
          .sort((a, b) => b.id - a.id).slice(0, 50);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE SLUG')) {
        const rows = this.userStrategies.filter(
          s => s.slug === params[0] && s.visibility === 'public');
        return [rows.map(r => ({ ...r })), []];
      }
      // public list: WHERE visibility = 'public' ... LIMIT <n> (inline — a
      // bound LIMIT is ER_WRONG_ARGUMENTS on real MySQL, and now here too)
      const lm = cmd.match(/LIMIT\s+(\d+)/);
      const rows = this.userStrategies.filter(s => s.visibility === 'public')
        .sort((a, b) => new Date(b.published_at || b.created_at) - new Date(a.published_at || a.created_at))
        .slice(0, lm ? Number(lm[1]) : 120);
      return [rows.map(r => ({ ...r })), []];
    }

    // -- PAPER TRADING ARENA (virtual accounts; §4: no real funds ever) --
    if (cmd.includes('INSERT INTO ARENA_ACCOUNTS')) {
      // params: user_id, balance, created_at
      if (!this.arenaAccounts[params[0]]) {
        this.arenaAccounts[params[0]] = { user_id: params[0], balance: params[1], created_at: params[2] };
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('UPDATE ARENA_ACCOUNTS')) {
      // params: balance, user_id
      const a = this.arenaAccounts[params[1]];
      if (!a) return [{ affectedRows: 0 }, []];
      a.balance = params[0];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM ARENA_ACCOUNTS')) {
      if (cmd.includes('WHERE USER_ID')) {
        const a = this.arenaAccounts[params[0]];
        return [a ? [{ ...a }] : [], []];
      }
      // leaderboard: all accounts
      return [Object.values(this.arenaAccounts).map(a => ({ ...a })), []];
    }
    if (cmd.includes('INSERT INTO ARENA_POSITIONS')) {
      // Two shapes: legacy (user_id, symbol, direction, entry, margin,
      // leverage, source, tp, sl, opened_at) and sealed — same prefix with
      // (trade_key, seal, seal_payload, sealed_at) between sl and opened_at.
      const sealed = cmd.includes('TRADE_KEY');
      this.arenaPositions.push({
        id: this._nextArenaPosId++, user_id: params[0], symbol: params[1],
        direction: params[2], entry: params[3], margin: params[4],
        leverage: params[5], source: params[6] || 'manual',
        tp: params[7] == null ? null : params[7], sl: params[8] == null ? null : params[8],
        trade_key: sealed ? params[9] : null,
        seal: sealed ? params[10] : null,
        seal_payload: sealed ? params[11] : null,
        sealed_at: sealed ? params[12] : null,
        opened_at: sealed ? params[13] : params[9],
        signal_key: cmd.includes('AGENT_SLUG') ? params[14] : null,
        agent_slug: cmd.includes('AGENT_SLUG') ? params[15] : null,
      });
      return [{ affectedRows: 1, insertId: this._nextArenaPosId - 1 }, []];
    }
    if (cmd.includes('INSERT INTO ARENA_FOLLOWS')) {
      // upsert — params: user_id, enabled, margin, leverage, last_signal_id, created_at
      this.arenaFollows[params[0]] = { user_id: params[0], enabled: params[1],
        margin: params[2], leverage: params[3], last_signal_id: params[4], created_at: params[5] };
      return [{ affectedRows: 1 }, []];
    }
    // -- USER WATCHLIST (starred symbols; UNIQUE(user_id, symbol)) --
    if (cmd.includes('INSERT INTO USER_WATCHLIST')) {
      // params: user_id, symbol, created_at
      if (!this.watchlist.some(w => w.user_id === params[0] && w.symbol === params[1])) {
        this.watchlist.push({ user_id: params[0], symbol: params[1], created_at: params[2] });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM USER_WATCHLIST')) {
      // params: user_id, symbol
      const before = this.watchlist.length;
      this.watchlist = this.watchlist.filter(
        w => !(w.user_id === params[0] && w.symbol === params[1]));
      return [{ affectedRows: before - this.watchlist.length }, []];
    }
    if (cmd.includes('FROM USER_WATCHLIST')) {
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.watchlist.filter(w => w.user_id === params[0]);
        return [rows.map(r => ({ ...r })), []];
      }
      return [this.watchlist.map(r => ({ ...r })), []];   // pattern watch: everyone
    }
    if (cmd.includes('UPDATE ARENA_FOLLOWS')) {
      // params: last_signal_id, user_id
      const f = this.arenaFollows[params[1]];
      if (!f) return [{ affectedRows: 0 }, []];
      f.last_signal_id = params[0];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM ARENA_FOLLOWS')) {
      const f = this.arenaFollows[params[0]];
      return [f ? [{ ...f }] : [], []];
    }
    if (cmd.includes('UPDATE ARENA_POSITIONS')) {
      if (cmd.includes('TRAIL_PCT = ?')) {
        // Exit edit with trail: SET tp = ?, sl = ?, trail_pct = ?, exits_edited = 1
        //                       WHERE id = ? AND user_id = ?
        const pos = this.arenaPositions.find(p => p.id === params[3] && p.user_id === params[4]);
        if (pos) { pos.tp = params[0]; pos.sl = params[1]; pos.trail_pct = params[2]; pos.exits_edited = 1; }
        return [{ affectedRows: pos ? 1 : 0 }, []];
      }
      if (cmd.includes('SET SL = ?')) {
        // Trail ratchet (mechanical): SET sl = ? WHERE id = ? AND user_id = ?
        const pos = this.arenaPositions.find(p => p.id === params[1] && p.user_id === params[2]);
        if (pos) pos.sl = params[0];
        return [{ affectedRows: pos ? 1 : 0 }, []];
      }
      // Legacy exit edit: SET tp = ?, sl = ?, exits_edited = 1 WHERE id = ? AND user_id = ?
      const pos = this.arenaPositions.find(p => p.id === params[2] && p.user_id === params[3]);
      if (pos) { pos.tp = params[0]; pos.sl = params[1]; pos.exits_edited = 1; }
      return [{ affectedRows: pos ? 1 : 0 }, []];
    }
    if (cmd.includes('DELETE FROM ARENA_POSITIONS')) {
      // params: id, user_id (own rows only)
      const before = this.arenaPositions.length;
      this.arenaPositions = this.arenaPositions.filter(
        p => !(p.id === params[0] && p.user_id === params[1]));
      return [{ affectedRows: before - this.arenaPositions.length }, []];
    }
    if (cmd.includes('FROM ARENA_POSITIONS') && cmd.includes('WHERE AGENT_SLUG')) {
      return [[{ n: this.arenaPositions.filter(x => x.agent_slug === params[0]).length }], []];
    }
    if (cmd.includes('FROM ARENA_POSITIONS')) {
      if (cmd.includes('WHERE TRADE_KEY')) {
        // Provable Calls verify: one open position by its receipt key
        const rows = this.arenaPositions.filter(p => p.trade_key === params[0]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('SEALED_AT >=')) {
        // Daily seal roots: seals minted inside one UTC day window
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        const rows = this.arenaPositions.filter(p => p.sealed_at
          && new Date(p.sealed_at).getTime() >= lo && new Date(p.sealed_at).getTime() < hi);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE ID')) {
        // params: id, user_id
        const rows = this.arenaPositions.filter(p => p.id === params[0] && p.user_id === params[1]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.arenaPositions.filter(p => p.user_id === params[0])
          .sort((a, b) => b.id - a.id);
        return [rows.map(r => ({ ...r })), []];
      }
      // leaderboard: all open positions
      return [this.arenaPositions.map(r => ({ ...r })), []];
    }
    if (cmd.includes('INSERT INTO ARENA_TRADES')) {
      // Two shapes: legacy (user_id, symbol, direction, entry, exit_price,
      // margin, leverage, pnl, reason, opened_at, closed_at) and sealed —
      // (trade_key, seal, seal_payload, sealed_at) between reason and opened_at.
      const sealed = cmd.includes('TRADE_KEY');
      this.arenaTrades.push({
        id: this._nextArenaTradeId++, user_id: params[0], symbol: params[1],
        direction: params[2], entry: params[3], exit_price: params[4],
        margin: params[5], leverage: params[6], pnl: params[7],
        reason: params[8],
        trade_key: sealed ? params[9] : null,
        seal: sealed ? params[10] : null,
        seal_payload: sealed ? params[11] : null,
        sealed_at: sealed ? params[12] : null,
        opened_at: sealed ? params[13] : params[9],
        closed_at: sealed ? params[14] : params[10],
        signal_key: cmd.includes('AGENT_SLUG') ? params[15] : null,
        agent_slug: cmd.includes('AGENT_SLUG') ? params[16] : null,
        // Appended LAST in the column list so every position above is
        // unchanged and the two older shapes keep working untouched.
        source: cmd.includes('SOURCE') ? (params[17] || 'manual') : 'manual',
      });
      return [{ affectedRows: 1, insertId: this._nextArenaTradeId - 1 }, []];
    }
    if (cmd.includes('INSERT INTO ARENA_SEASONS')) {
      // Two shapes: (name, starts, ends, created) legacy, or with RULES at
      // index 3 — (name, starts, ends, rules, created).
      const hasRules = cmd.includes('RULES');
      this.arenaSeasons.push({ id: this._nextArenaSeasonId++, name: params[0],
        starts_at: params[1], ends_at: params[2],
        rules: hasRules ? params[3] : null,
        created_at: hasRules ? params[4] : params[3] });
      return [{ affectedRows: 1, insertId: this._nextArenaSeasonId - 1 }, []];
    }
    if (cmd.includes('UPDATE ARENA_SEASONS')) {
      // ceremony flags — params: value(1), id  (SET announced_live / announced_end)
      const srow = this.arenaSeasons.find(x => x.id === params[1]);
      if (!srow) return [{ affectedRows: 0 }, []];
      if (cmd.includes('ANNOUNCED_LIVE')) srow.announced_live = params[0];
      if (cmd.includes('ANNOUNCED_END')) srow.announced_end = params[0];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM ARENA_SEASONS')) {
      // newest first — the route picks the relevant one
      const rows = this.arenaSeasons.slice().sort((a, b) => b.id - a.id);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM ARENA_TRADES') && cmd.includes('WHERE AGENT_SLUG')) {
      // Per-agent record: attributed closes, newest first.
      const rows = this.arenaTrades.filter(t => t.agent_slug === params[0])
        .sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at)).slice(0, 500);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM ARENA_TRADES')) {
      if (cmd.includes('WHERE TRADE_KEY')) {
        // Provable Calls verify: one closed trade by its receipt key
        const rows = this.arenaTrades.filter(t => t.trade_key === params[0]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('SEALED_AT >=')) {
        // Daily seal roots: seals minted inside one UTC day window
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        const rows = this.arenaTrades.filter(t => t.sealed_at
          && new Date(t.sealed_at).getTime() >= lo && new Date(t.sealed_at).getTime() < hi);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('COUNT(*)') && cmd.includes('CLOSED_AT >=')) {
        // tape pulse: WHERE closed_at >= ? (single cutoff, count only)
        const lo = new Date(params[0]).getTime();
        const n = this.arenaTrades.filter(
          t => new Date(t.closed_at).getTime() >= lo).length;
        return [[{ n }], []];
      }
      if (cmd.includes('USER_ID = ?') && cmd.includes('CLOSED_AT >=') && cmd.includes('CLOSED_AT <')) {
        // learn diary: the caller's closes inside ONE UTC day window. Must be
        // matched BEFORE the single-cutoff branch below — falling through
        // there would silently ignore the upper bound and attach every close
        // since `lo` to the diary day.
        const lo = new Date(params[1]).getTime(), hi = new Date(params[2]).getTime();
        const rows = this.arenaTrades.filter(t => t.user_id === params[0]
          && new Date(t.closed_at).getTime() >= lo
          && new Date(t.closed_at).getTime() < hi);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('USER_ID = ?') && cmd.includes('CLOSED_AT >=')) {
        // welcome-back digest: the caller's own closes since a cutoff
        const lo = new Date(params[1]).getTime();
        const rows = this.arenaTrades.filter(t => t.user_id === params[0]
          && new Date(t.closed_at).getTime() >= lo);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('CLOSED_AT >=')) {
        // season window: WHERE closed_at >= ? AND closed_at <= ?
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        const rows = this.arenaTrades.filter(t => {
          const c = new Date(t.closed_at).getTime();
          return c >= lo && c <= hi;
        });
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('COUNT(*)') && cmd.includes('GROUP BY USER_ID')) {
        // Two shapes: all closes, and receipt-backed closes only (leaderboard
        // 🔏 badge). The sealed filter must be honored BEFORE the generic count.
        const sealedOnly = cmd.includes('SEAL IS NOT NULL');
        const counts = {};
        for (const t of this.arenaTrades) {
          if (sealedOnly && !t.seal) continue;
          counts[t.user_id] = (counts[t.user_id] || 0) + 1;
        }
        return [Object.entries(counts).map(([user_id, n]) => ({ user_id: Number(user_id), n })), []];
      }
      if (!cmd.includes('WHERE')) {
        // live tape: newest closes across ALL users (route maps to handles)
        const rows = this.arenaTrades.slice().sort((a, b) => b.id - a.id).slice(0, 40);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE USER_ID') && !cmd.includes('LIMIT') && !cmd.includes('CLOSED_AT')) {
        // streaks/quests: the user's FULL close history (no cap)
        const rows = this.arenaTrades.filter(t => t.user_id === params[0])
          .sort((a, b) => b.id - a.id);
        return [rows.map(r => ({ ...r })), []];
      }
      // history: WHERE user_id = ? ORDER BY id DESC LIMIT 30
      const rows = this.arenaTrades.filter(t => t.user_id === params[0])
        .sort((a, b) => b.id - a.id).slice(0, 30);
      return [rows.map(r => ({ ...r })), []];
    }

    // -- SEAL ROOTS (Provable Calls v3 — one immutable Merkle root per UTC day) --
    if (cmd.includes('UPDATE SEAL_ROOTS')) {
      // Anchor record: SET anchor_tx = ?, anchored_at = ? WHERE day = ? AND anchor_tx IS NULL
      // First anchor wins — an anchored root is immutable like the root itself.
      const row = this.sealRoots.find(r => r.day === params[2] && r.anchor_tx == null);
      if (row) { row.anchor_tx = params[0]; row.anchored_at = params[1]; }
      return [{ affectedRows: row ? 1 : 0 }, []];
    }
    if (cmd.includes('INSERT INTO SEAL_ROOTS')) {
      // params: day, root, seal_count, leaves, computed_at — first write wins (immutable)
      if (!this.sealRoots.some(r => r.day === params[0])) {
        this.sealRoots.push({ day: params[0], root: params[1],
          seal_count: params[2], leaves: params[3], computed_at: params[4] });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM SEAL_ROOTS')) {
      if (cmd.includes('WHERE DAY')) {
        return [this.sealRoots.filter(r => r.day === params[0]).map(r => ({ ...r })), []];
      }
      const rows = this.sealRoots.slice().sort((a, b) => (a.day < b.day ? 1 : -1));
      return [rows.map(r => ({ ...r })), []];
    }

    // -- ARENA API KEYS --
    //
    // These branches did not exist. `lib/arena_keys.js` was therefore
    // unreachable under the shim, so mint/verify/bind/revoke — the entire
    // credential path an autonomous agent authenticates with — had never been
    // exercised by a test, only source-scanned. That is the distinction
    // CLAUDE.md draws between code being PRESENT and code being REACHED, and
    // the arena_open MCP tool sits behind it.
    //
    // Ordered specific-first: the UPDATEs share a table name, so a looser
    // branch above a tighter one would shadow it.
    if (cmd.includes('INSERT INTO ARENA_API_KEYS')) {
      // params: user_id, key_hash, label, created_at
      if (this.arenaApiKeys.some((k) => k.key_hash === params[1])) {
        const err = new Error("Duplicate entry for key 'uniq_arena_key_hash'");
        err.code = 'ER_DUP_ENTRY';
        throw err;
      }
      this.arenaApiKeys.push({
        id: this._nextArenaKeyId++, user_id: params[0], key_hash: params[1],
        label: params[2] || '', created_at: params[3],
        last_used_at: null, revoked_at: null, agent_slug: null,
      });
      return [{ affectedRows: 1, insertId: this._nextArenaKeyId - 1 }, []];
    }
    if (cmd.includes('UPDATE ARENA_API_KEYS')) {
      const live = (k) => k.revoked_at == null;
      if (cmd.includes('SET LAST_USED_AT')) {
        const k = this.arenaApiKeys.find((x) => x.id === params[1]);
        if (k) k.last_used_at = params[0];
        return [{ affectedRows: k ? 1 : 0 }, []];
      }
      if (cmd.includes('SET AGENT_SLUG = NULL')) {
        const k = this.arenaApiKeys.find((x) => x.id === params[0] && x.user_id === params[1]);
        if (k) k.agent_slug = null;
        return [{ affectedRows: k ? 1 : 0 }, []];
      }
      if (cmd.includes('SET AGENT_SLUG')) {
        const k = this.arenaApiKeys.find((x) => x.id === params[1] && x.user_id === params[2]);
        if (k) k.agent_slug = params[0];
        return [{ affectedRows: k ? 1 : 0 }, []];
      }
      if (cmd.includes('SET REVOKED_AT')) {
        const k = this.arenaApiKeys.find(
          (x) => x.id === params[1] && x.user_id === params[2] && live(x));
        if (k) k.revoked_at = params[0];
        return [{ affectedRows: k ? 1 : 0 }, []];
      }
      return [{ affectedRows: 0 }, []];
    }
    if (cmd.includes('FROM ARENA_API_KEYS')) {
      const live = this.arenaApiKeys.filter((k) => k.revoked_at == null);
      if (cmd.includes('COUNT(*)')) {
        return [[{ n: live.filter((k) => k.user_id === params[0]).length }], []];
      }
      if (cmd.includes('WHERE KEY_HASH')) {
        return [live.filter((k) => k.key_hash === params[0]).map((k) => ({ ...k })), []];
      }
      if (cmd.includes('WHERE ID = ? AND USER_ID')) {
        return [live.filter((k) => k.id === params[0] && k.user_id === params[1])
          .map((k) => ({ ...k })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        return [live.filter((k) => k.user_id === params[0])
          .sort((a, b) => b.id - a.id).map((k) => ({ ...k })), []];
      }
      return [live.map((k) => ({ ...k })), []];
    }

    // -- SCAN SEALS (pre-signature receipts; UNIQUE on scan_key) --
    if (cmd.includes('INSERT INTO SCAN_SEALS')) {
      // params: scan_key, user_id, agent_slug, tool, seal, seal_payload, sealed_at
      if (this.scanSeals.some((s) => s.scan_key === params[0])) {
        const err = new Error("Duplicate entry for key 'uniq_scan_key'");
        err.code = 'ER_DUP_ENTRY';
        throw err;
      }
      this.scanSeals.push({
        id: this._nextScanSealId++, scan_key: params[0],
        user_id: params[1] == null ? null : params[1],
        agent_slug: params[2] == null ? null : params[2],
        tool: params[3], seal: params[4], seal_payload: params[5],
        sealed_at: params[6],
      });
      return [{ affectedRows: 1, insertId: this._nextScanSealId - 1 }, []];
    }
    if (cmd.includes('FROM SCAN_SEALS')) {
      if (cmd.includes('WHERE SCAN_KEY')) {
        return [this.scanSeals.filter((s) => s.scan_key === params[0])
          .map((s) => ({ ...s })), []];
      }
      // The daily sweep: SELECT seal ... WHERE sealed_at >= ? AND < ?
      if (cmd.includes('WHERE SEALED_AT')) {
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        return [this.scanSeals
          .filter((s) => s.sealed_at && new Date(s.sealed_at).getTime() >= lo
                      && new Date(s.sealed_at).getTime() < hi)
          .map((s) => ({ seal: s.seal })), []];
      }
      if (cmd.includes('WHERE AGENT_SLUG')) {
        return [this.scanSeals.filter((s) => s.agent_slug === params[0])
          .sort((a, b) => b.id - a.id).map((s) => ({ ...s })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        return [this.scanSeals.filter((s) => s.user_id === params[0])
          .sort((a, b) => b.id - a.id).map((s) => ({ ...s })), []];
      }
      return [this.scanSeals.map((s) => ({ ...s })), []];
    }

    // -- AGENTS (claimed slugs; UNIQUE on slug) --
    //
    // The UNIQUE index is modelled rather than assumed. lib/agents.js checks
    // availability and then inserts, and relies on the index to arbitrate two
    // concurrent claims — a shim that accepted the second write would let a
    // test pass against a race that production refuses, which is exactly the
    // blindfold the LIMIT branch above exists to prevent.
    if (cmd.includes('INSERT INTO AGENTS')) {
      // params: slug, user_id, display_name, seal, seal_payload, sealed_at, created_at
      if (this.agents.some((a) => a.slug === params[0])) {
        const err = new Error("Duplicate entry for key 'uniq_agent_slug'");
        err.code = 'ER_DUP_ENTRY';
        throw err;
      }
      this.agents.push({
        id: this._nextAgentId++, slug: params[0], user_id: params[1],
        display_name: params[2] == null ? null : params[2], seal: params[3],
        seal_payload: params[4], sealed_at: params[5], created_at: params[6],
      });
      return [{ affectedRows: 1, insertId: this._nextAgentId - 1 }, []];
    }
    if (cmd.includes('FROM AGENTS')) {
      if (cmd.includes('COUNT(*)')) {
        return [[{ n: this.agents.filter((a) => a.user_id === params[0]).length }], []];
      }
      // The ownership question — slug AND user together, never either alone.
      if (cmd.includes('WHERE SLUG = ? AND USER_ID')) {
        return [this.agents.filter((a) => a.slug === params[0] && a.user_id === params[1])
          .map((a) => ({ ...a })), []];
      }
      if (cmd.includes('WHERE SLUG')) {
        return [this.agents.filter((a) => a.slug === params[0]).map((a) => ({ ...a })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        return [this.agents.filter((a) => a.user_id === params[0])
          .sort((a, b) => b.id - a.id).map((a) => ({ ...a })), []];
      }
      // seal_roots' daily sweep: SELECT seal FROM agents WHERE sealed_at >= ? AND < ?
      if (cmd.includes('WHERE SEALED_AT')) {
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        return [this.agents
          .filter((a) => a.sealed_at && new Date(a.sealed_at).getTime() >= lo
                      && new Date(a.sealed_at).getTime() < hi)
          .map((a) => ({ seal: a.seal })), []];
      }
      return [this.agents.map((a) => ({ ...a })), []];
    }

    // -- PUSH SUBSCRIPTIONS (web push; UPSERT by endpoint) --
    if (cmd.includes('INSERT INTO PUSH_SUBSCRIPTIONS')) {
      // params: user_id, endpoint, keys_json
      const i = this.pushSubs.findIndex(s => s.endpoint === params[1]);
      if (i >= 0) {
        this.pushSubs[i].user_id = params[0];
        this.pushSubs[i].keys_json = params[2];
      } else {
        this.pushSubs.push({ id: this._nextPushSubId++, user_id: params[0],
          endpoint: params[1], keys_json: params[2], created_at: new Date() });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PUSH_SUBSCRIPTIONS')) {
      if (cmd.includes('ORDER BY ID ASC')) {          // drop oldest for user
        const mine = this.pushSubs.filter(s => s.user_id === params[0])
          .sort((a, b) => a.id - b.id);
        if (mine.length) this.pushSubs = this.pushSubs.filter(s => s.id !== mine[0].id);
      } else if (cmd.includes('AND ENDPOINT')) {      // user-scoped unsubscribe
        this.pushSubs = this.pushSubs.filter(
          s => !(s.user_id === params[0] && s.endpoint === params[1]));
      } else {                                        // prune by endpoint (410)
        this.pushSubs = this.pushSubs.filter(s => s.endpoint !== params[0]);
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PUSH_SUBSCRIPTIONS') && cmd.includes('COUNT(*)')) {
      return [[{ n: this.pushSubs.filter(s => s.user_id === params[0]).length }], []];
    }
    if (cmd.includes('FROM PUSH_SUBSCRIPTIONS')) {
      const rows = cmd.includes('WHERE USER_ID')
        ? this.pushSubs.filter(s => s.user_id === params[0])
        : [...this.pushSubs].sort((a, b) => b.id - a.id);
      return [rows.map(s => ({ ...s })), []];
    }

    // -- COPY SUBSCRIPTIONS (strategy-agent follows; UNIQUE user_id+agent_id) --
    if (cmd.includes('INSERT INTO COPY_SUBSCRIPTIONS')) {
      // params: user_id, agent_id. Idempotent on (user_id, agent_id).
      if (!this.copySubs.some(s => s.user_id === params[0] && s.agent_id === params[1])) {
        this.copySubs.push({ id: this._nextCopySubId++, user_id: params[0],
          agent_id: params[1], created_at: new Date() });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM COPY_SUBSCRIPTIONS')) {
      // params: user_id, agent_id (user-scoped unfollow).
      this.copySubs = this.copySubs.filter(
        s => !(s.user_id === params[0] && s.agent_id === params[1]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM COPY_SUBSCRIPTIONS') && cmd.includes('GROUP BY AGENT_ID')) {
      const byAgent = new Map();
      for (const sub of this.copySubs) {
        if (!byAgent.has(sub.agent_id)) byAgent.set(sub.agent_id, new Set());
        byAgent.get(sub.agent_id).add(sub.user_id);
      }
      return [[...byAgent.entries()].map(([agent_id, users]) => ({ agent_id, n: users.size })), []];
    }
    if (cmd.includes('FROM COPY_SUBSCRIPTIONS') && cmd.includes('COUNT(*)')) {
      return [[{ n: this.copySubs.filter(s => s.user_id === params[0]).length }], []];
    }
    if (cmd.includes('FROM COPY_SUBSCRIPTIONS')) {
      const rows = this.copySubs.filter(s => s.user_id === params[0])
        .sort((a, b) => a.id - b.id);
      return [rows.map(s => ({ ...s })), []];
    }

    // -- USER PROFILES (per-user agent profile: risk pref, watchlist, prefs) --
    if (cmd.includes('INTO USER_PROFILES')) {
      // params: user_id, risk_pref, watchlist, prefs (UPSERT by user_id)
      this.userProfiles[params[0]] = {
        user_id: params[0], risk_pref: params[1],
        watchlist: params[2], prefs: params[3], updated_at: new Date(),
      };
      return [{ affectedRows: 1 }, []];
    }
    // Topic-push fan-out: ALL profiles (no user_id param). Matched before the
    // single-profile lookup, which requires params[0].
    if (cmd.includes('FROM USER_PROFILES') && cmd.includes('LIMIT 2000')) {
      return [Object.values(this.userProfiles).slice(0, 2000)
        .map(p => ({ user_id: p.user_id, prefs: p.prefs })), []];
    }
    if (cmd.includes('FROM USER_PROFILES')) {
      const p = this.userProfiles[params[0]];
      return [p ? [{ ...p }] : [], []];
    }

    // -- REPORTS CACHE (single-row, like scan_cache) --
    if (cmd.includes('REPLACE INTO REPORTS_CACHE')) {
      this.reportsCache = { reports_json: params[0], updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM REPORTS_CACHE')) {
      return [this.reportsCache ? [{ ...this.reportsCache }] : [], []];
    }

    // -- FLIGHT CACHE (single-row, same shape) --
    // The ONLY table the suite exercised that the shim did not implement:
    // measured by logging every fall-through across all 2,388 tests, which
    // produced exactly five hits and one table name. Both statements were
    // answered with empty rows, so the Guardian flight recorder wrote to
    // nothing and read back nothing, and every test over it agreed.
    if (cmd.includes('REPLACE INTO FLIGHT_CACHE')) {
      this.flightCache = { id: 1, flight_json: params[0], updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM FLIGHT_CACHE')) {
      return [this.flightCache ? [{ ...this.flightCache }] : [], []];
    }

    // -- PENDING STANCE (single-row admin request queue) --
    if (cmd.includes('REPLACE INTO PENDING_STANCE')) {
      // params: mode, requested_by, telegram_id
      this.pendingStance = {
        id: 1, mode: params[0], requested_by: params[1],
        telegram_id: params[2], created_at: new Date(),
      };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PENDING_STANCE')) {
      // `WHERE requested_by = ?` comes from account erasure and must clear the
      // row only when it is that person's request. The approval path deletes
      // `WHERE id = 1` and still clears unconditionally.
      if (cmd.includes('REQUESTED_BY')) {
        const mine = this.pendingStance
          && String(this.pendingStance.requested_by) === String(params[0]);
        if (mine) this.pendingStance = null;
        return [{ affectedRows: mine ? 1 : 0 }, []];
      }
      this.pendingStance = null;
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_STANCE')) {
      return [this.pendingStance ? [{ ...this.pendingStance }] : [], []];
    }

    // -- AGENT EVENTS (public mind-stream feed; bounded ring) --
    if (cmd.includes('INSERT INTO AGENT_EVENTS')) {
      // params: event_type, severity, symbol, title, body, data_json, created_at
      const row = {
        id: this._nextAgentEventId++,
        event_type: params[0], severity: params[1], symbol: params[2],
        title: params[3], body: params[4], data_json: params[5],
        created_at: params[6] || new Date(),
      };
      this.agentEvents.push(row);
      return [{ insertId: row.id }, []];
    }
    if (cmd.includes('FROM AGENT_EVENTS') && cmd.includes('COUNT(*)')) {
      // welcome-back digest: engine events since a cutoff
      const lo = new Date(params[0]).getTime();
      const n = this.agentEvents.filter(
        e => new Date(e.created_at).getTime() >= lo).length;
      return [[{ n }], []];
    }
    if (cmd.includes('FROM AGENT_EVENTS') && cmd.includes('OFFSET')) {
      // Prune probe: SELECT id ... ORDER BY id DESC LIMIT 1 OFFSET <keep>
      const m = cmd.match(/OFFSET\s+(\d+)/);
      const off = m ? parseInt(m[1]) : 0;
      const sorted = [...this.agentEvents].sort((a, b) => b.id - a.id);
      return [sorted.slice(off, off + 1).map(r => ({ id: r.id })), []];
    }
    if (cmd.includes('DELETE FROM AGENT_EVENTS')) {
      // DELETE ... WHERE id <= ? (ring-buffer prune)
      const cutoff = Number(params[0]);
      this.agentEvents = this.agentEvents.filter(e => e.id > cutoff);
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM AGENT_EVENTS')) {
      const m = cmd.match(/LIMIT\s+(\d+)/);
      const limit = m ? parseInt(m[1]) : 50;
      const rows = [...this.agentEvents].sort((a, b) => b.id - a.id).slice(0, limit);
      return [rows, []];
    }

    // -- BOT SEALING KEY (single row; public half only) --
    if (cmd.includes('INTO BOT_SEALING_KEY')) {
      // params: kid, pem, alg — id is the literal 1 in the SQL.
      this.sealingKey = { kid: params[0], pem: params[1], alg: params[2],
        updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM BOT_SEALING_KEY')) {
      return [this.sealingKey ? [{ ...this.sealingKey }] : [], []];
    }

    // -- PENDING CREDENTIALS / EXCHANGE STATUS --
    if (cmd.includes('INSERT INTO PENDING_CREDENTIALS')) {
      // params: user_id, telegram_id, exchange(venue), [encrypted_payload] —
      // action is a literal in the SQL ('connect'/'disconnect'). UPSERT by user_id.
      const action = cmd.includes("'DISCONNECT'") ? 'disconnect' : 'connect';
      const row = {
        user_id: params[0], telegram_id: params[1],
        exchange: params[2] || 'bitget', action,
        encrypted_payload: action === 'disconnect' ? null : params[3],
        created_at: new Date(),
      };
      const i = this.pendingCreds.findIndex(p => p.user_id === row.user_id);
      if (i >= 0) this.pendingCreds[i] = { id: this.pendingCreds[i].id, ...row };
      else this.pendingCreds.push({ id: this.pendingCreds.length + 1, ...row });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PENDING_CREDENTIALS')) {
      this.pendingCreds = this.pendingCreds.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_CREDENTIALS')) {  // SELECT (DELETE handled above)
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingCreds.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingCreds].sort((a, b) => a.created_at - b.created_at), []];
    }
    if (cmd.includes('INSERT INTO EXCHANGE_STATUS')) {
      // params: user_id, exchange(venue), connected, [last_error] — upsert per
      // (user, venue) so multiple connected exchanges coexist. The success
      // path writes a literal NULL in the SQL and passes 3 params; the
      // rejection path passes the reason as a 4th.
      const key = String(params[0]);
      if (!this.exchangeStatus[key] || !Array.isArray(this.exchangeStatus[key])) {
        this.exchangeStatus[key] = [];
      }
      const venue = params[1] || 'bitget';
      const lastError = params.length > 3 ? (params[3] ?? null) : null;
      const row = this.exchangeStatus[key].find(r => r.exchange === venue);
      if (row) {
        row.connected = !!params[2];
        row.last_error = lastError;
      } else {
        this.exchangeStatus[key].push(
          { exchange: venue, connected: !!params[2], last_error: lastError });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM EXCHANGE_STATUS')) {
      const rows = this.exchangeStatus[String(params[0])];
      return [Array.isArray(rows)
        ? rows.map(r => ({ connected: r.connected, exchange: r.exchange || 'bitget',
                           last_error: r.last_error ?? null }))
        : [], []];
    }

    // -- PENDING CONTROLS / USER CONTROLS --
    if (cmd.includes('DELETE FROM PENDING_CONTROLS')) {
      this.pendingControls = this.pendingControls.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('INSERT INTO PENDING_CONTROLS')) {
      // params: user_id, telegram_id, live_enabled, max_margin, paused
      const row = { user_id: params[0], telegram_id: params[1],
        live_enabled: params[2], max_margin: params[3], paused: params[4],
        created_at: new Date() };
      const i = this.pendingControls.findIndex(p => String(p.user_id) === String(row.user_id));
      if (i >= 0) this.pendingControls[i] = { id: this.pendingControls[i].id, ...row };
      else this.pendingControls.push({ id: this.pendingControls.length + 1, ...row });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_CONTROLS')) {
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingControls.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingControls].sort((a, b) => a.created_at - b.created_at), []];
    }
    if (cmd.includes('INSERT INTO USER_CONTROLS')) {
      // params: user_id, live_enabled, max_margin, paused, allowlisted
      this.userControls[params[0]] = {
        live_enabled: !!params[1], max_margin: params[2],
        paused: !!params[3], allowlisted: !!params[4],
      };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM USER_CONTROLS')) {
      const c = this.userControls[params[0]];
      return [c ? [c] : [], []];
    }
    if (cmd.includes('DELETE FROM PENDING_FLATTEN')) {
      this.pendingFlatten = this.pendingFlatten.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('INTO PENDING_FLATTEN')) {
      const row = { user_id: params[0], telegram_id: params[1], created_at: new Date() };
      const i = this.pendingFlatten.findIndex(p => String(p.user_id) === String(row.user_id));
      if (i >= 0) this.pendingFlatten[i] = row; else this.pendingFlatten.push(row);
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_FLATTEN')) {
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingFlatten.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingFlatten].sort((a, b) => a.created_at - b.created_at), []];
    }

    // -- USERS --
    if (cmd.includes('INSERT INTO USERS')) {
      const exists = this.users.find(u => u.email === params[0]);
      if (exists) {
        const err = new Error('Duplicate entry'); err.code = 'ER_DUP_ENTRY'; throw err;
      }
      const user = { id: this._nextUserId++, email: params[0], password_hash: null,
        google_id: null, telegram_id: null, discord_id: null, x_id: null,
        wallet_address: null, avatar_url: null, plan: 'free',
        telegram_linked: false, link_token: null, link_token_expires: null,
        email_verified: false, verify_token: null, verify_token_expires: null,
        reset_token: null, reset_token_expires: null,
        referral_code: null, referred_by: null,
        // Mirrors the MySQL column's `NOT NULL DEFAULT 0`. Without it the
        // shim returns `undefined`, every comparison reads as epoch 0, and
        // token revocation is silently INERT — which matters more than usual
        // because the whole web suite runs against this shim, so the feature
        // would have looked tested while doing nothing.
        token_epoch: 0,
        leaderboard_handle: null, created_at: new Date() };
      // Column order varies: email/password vs the OAuth passwordless inserts.
      if (cmd.includes('PASSWORD_HASH')) {
        user.password_hash = params[1];
      } else if (cmd.includes('GOOGLE_ID')) {
        user.google_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('TELEGRAM_ID')) {
        user.telegram_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('DISCORD_ID')) {
        user.discord_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('X_ID')) {
        user.x_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('WALLET_ADDRESS')) {
        user.wallet_address = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      }
      this.users.push(user);
      return [{ insertId: user.id }, []];
    }

    if (cmd.includes('FROM USERS WHERE GOOGLE_ID')) {
      return [this.users.filter(u => u.google_id === params[0]), []];
    }

    if (cmd.includes('FROM USERS WHERE TELEGRAM_ID')) {
      // HONOUR THE WHERE CLAUSE WE WERE ACTUALLY GIVEN — the lesson the
      // siwf_nonces UPDATE below records, running the other way.
      //
      // This branch answered `telegram_id = ?` and nothing else, so
      // `... AND id != ?` (auth.js /validate-token, RC-2026-001) matched the
      // very row it names to EXCLUDE. The shim was LESS correct than the
      // statement it was handed, which is the worse direction of the two: a
      // user re-linking their own Telegram id was told it belonged to someone
      // else, in tests only, because MySQL honours the clause. The double
      // disagreed with production and the TEST was the thing lying.
      //
      // Found by a test that expected 200 and got 409 — not by reading this.
      const excludeId = cmd.includes('AND ID != ?') || cmd.includes('AND ID <> ?');
      return [this.users.filter(u =>
        String(u.telegram_id) === String(params[0])
        && (!excludeId || String(u.id) !== String(params[1]))), []];
    }

    if (cmd.includes('FROM USERS WHERE DISCORD_ID')) {
      return [this.users.filter(u => String(u.discord_id) === String(params[0])), []];
    }

    if (cmd.includes('FROM USERS WHERE X_ID')) {
      return [this.users.filter(u => String(u.x_id) === String(params[0])), []];
    }

    if (cmd.includes('FROM USERS WHERE WALLET_ADDRESS')) {
      return [this.users.filter(u => u.wallet_address != null
        && String(u.wallet_address).toLowerCase() === String(params[0]).toLowerCase()), []];
    }

    if (cmd.startsWith('UPDATE USERS SET GOOGLE_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.google_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET TELEGRAM_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.telegram_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET DISCORD_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.discord_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET X_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.x_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET WALLET_ADDRESS')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.wallet_address = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET SOL_ADDRESS')) {
      // Two shapes, and the shim must honour BOTH — matching only the first
      // silently no-ops the second, which is how a shim quietly diverges from
      // the database it stands in for:
      //   [sol_address|null, id]                → address only
      //   [sol_address|null, sol_verified, id]  → address + ownership claim
      const withVerified = params.length === 3;
      const user = this.users.find(u => u.id === params[withVerified ? 2 : 1]);
      if (user) {
        user.sol_address = params[0];
        if (withVerified) user.sol_verified = params[1];
      }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    // -- Referral / invite --
    // Revocation (M12). The increment is done in SQL on MySQL so two
    // concurrent logouts cannot both read N and both write N+1; the shim is
    // single-threaded, so `+= 1` is the faithful equivalent.
    if (cmd.startsWith('UPDATE USERS SET TOKEN_EPOCH')) {
      const user = this.users.find(u => u.id === params[0]);
      if (user) user.token_epoch = (Number(user.token_epoch) || 0) + 1;
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET REFERRAL_CODE')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.referral_code = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET REFERRED_BY')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.referred_by = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE REFERRAL_CODE')) {
      return [this.users.filter(u => u.referral_code != null && u.referral_code === params[0]), []];
    }
    if (cmd.includes('FROM USERS WHERE REFERRED_BY')) {
      return [this.users.filter(u => u.referred_by === params[0]), []];
    }

    // -- Leaderboard opt-in (anonymous handle) --
    if (cmd.startsWith('UPDATE USERS SET LAST_SEEN_AT')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.last_seen_at = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET LEADERBOARD_HANDLE')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.leaderboard_handle = params[0];  // params[0] may be null (opt-out)
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    // Bot sync desired-state pull: opted-in AND bot-linked, aliased columns.
    // Matched on both predicates (the route's SQL is multi-line, so exact
    // 'FROM USERS WHERE …' adjacency can't be relied on). Must be matched
    // BEFORE the generic handle handler below.
    if (cmd.includes('LEADERBOARD_HANDLE IS NOT NULL')
        && cmd.includes('TELEGRAM_ID IS NOT NULL')) {
      return [this.users
        .filter(u => u.leaderboard_handle != null && u.telegram_id != null)
        .slice(0, 500)
        .map(u => ({ user_id: u.id, telegram_id: u.telegram_id,
                     handle: u.leaderboard_handle })), []];
    }
    if (cmd.includes('FROM USERS WHERE LEADERBOARD_HANDLE IS NOT NULL')) {
      return [this.users.filter(u => u.leaderboard_handle != null), []];
    }
    if (cmd.includes('FROM USERS WHERE LEADERBOARD_HANDLE')) {  // = ?  (uniqueness check)
      return [this.users.filter(u => u.leaderboard_handle != null
        && String(u.leaderboard_handle).toLowerCase() === String(params[0]).toLowerCase()), []];
    }

    if (cmd.includes('FROM USERS WHERE EMAIL')) {
      const rows = this.users.filter(u => u.email === params[0]);
      return [rows, []];
    }

    if (cmd.includes('FROM USERS WHERE ID')) {
      const rows = this.users.filter(u => u.id === params[0]);
      return [rows, []];
    }

    if (cmd.startsWith('UPDATE USERS SET TELEGRAM_LINKED')) {
      // params: [linked, id] — plain linked-flag set (test fixtures / admin
      // tooling; the real link flow goes through the LINK_TOKEN branch below).
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.telegram_linked = !!params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET LINK_TOKEN')) {
      // Could be the link-token generation (3 params) or token consumption (1 param)
      if (cmd.includes('TELEGRAM_LINKED')) {
        // Consume token: ...telegram_linked=TRUE[, telegram_id=?] WHERE id=?
        // params end with the user id; telegram_id (if present) is just before it.
        const userId = params[params.length - 1];
        const tgId = cmd.includes('TELEGRAM_ID') ? params[params.length - 2] : null;
        const user = this.users.find(u => u.id === userId);
        if (user) {
          user.link_token = null; user.link_token_expires = null; user.telegram_linked = true;
          if (tgId != null) user.telegram_id = tgId;
        }
        return [{ affectedRows: user ? 1 : 0 }, []];
      }
      const user = this.users.find(u => u.id === params[2]);
      if (user) { user.link_token = params[0]; user.link_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.includes('FROM USERS WHERE LINK_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.link_token === params[0] && u.link_token_expires > now);
      return [rows, []];
    }

    // -- Account management: verify + reset tokens, password changes --
    if (cmd.startsWith('UPDATE USERS SET VERIFY_TOKEN')) {
      // params: [verify_token, verify_token_expires, id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.verify_token = params[0]; user.verify_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE VERIFY_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.verify_token === params[0] && u.verify_token_expires > now);
      return [rows, []];
    }
    if (cmd.startsWith('UPDATE USERS SET EMAIL_VERIFIED')) {
      // params: [id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.email_verified = true; user.verify_token = null; user.verify_token_expires = null; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET RESET_TOKEN')) {
      // params: [reset_token, reset_token_expires, id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.reset_token = params[0]; user.reset_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE RESET_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.reset_token === params[0] && u.reset_token_expires > now);
      return [rows, []];
    }
    if (cmd.startsWith('UPDATE USERS SET PASSWORD_HASH')) {
      // Reset flow clears reset_token too: [hash, id] or [hash, id] with reset clear.
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) {
        user.password_hash = params[0];
        if (cmd.includes('RESET_TOKEN')) { user.reset_token = null; user.reset_token_expires = null; }
      }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    // -- TRADES --
    if (cmd.includes('DELETE FROM TRADES') && cmd.includes('USER_ID')) {
      if (cmd.includes('LIMIT 1')) {
        // Delete ONE open trade. Honour the tighter key when the statement
        // carries it -- sync.js's close event matches direction + entry_price
        // so it closes THIS position and not an arbitrary same-symbol one --
        // and report the real affectedRows. The previous branch matched on
        // symbol alone and always answered 0, which would have made the
        // caller's "nothing matched, fall back to symbol-only" path delete a
        // SECOND row on this backend. MySQL reports the true count; this
        // shim has to as well or the fallback is a bug here and not there.
        const tight = cmd.includes('DIRECTION') && cmd.includes('ENTRY_PRICE');
        const idx = this.trades.findIndex(t =>
          t.user_id === params[0] && t.symbol === params[1] && t.status === 'OPEN'
          && (!tight || (String(t.direction) === String(params[2])
                         && Number(t.entry_price) === Number(params[3]))));
        if (idx >= 0) this.trades.splice(idx, 1);
        return [{ affectedRows: idx >= 0 ? 1 : 0 }, []];
      } else if (cmd.includes("STATUS = 'OPEN'")) {
        // Delete only the user's OPEN rows (portfolio write-through refresh)
        this.trades = this.trades.filter(t => !(t.user_id === params[0] && t.status === 'OPEN'));
      } else {
        this.trades = this.trades.filter(t => t.user_id !== params[0]);
      }
      return [{ affectedRows: 0 }, []];
    }

    if (cmd.includes('INSERT INTO TRADES')) {
      // Parse the COLUMN LIST, not the parameter count. The previous branch
      // dispatched on params.length (11 / 10 / 9) beside a comment asserting
      // that "both real call sites bind exactly 11 params" -- true when it
      // was written, and untrue once `venue` and `event_id` joined the real
      // statements: a full-sync OPEN position (11 params) was then stored as
      // a CLOSED trade with exit_price = its size, and a trade-event close
      // (12 params) fell through every branch and stored `{ id }` alone. A
      // column the statement names is bound in order; a quoted literal is
      // taken as written; anything else is refused, like every other shape
      // this shim does not understand. Schema defaults fill what the
      // statement did not say, so the row reads like the MySQL row would.
      const m = /INSERT INTO trades\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)/i.exec(sql);
      if (!m) throw new Error('MemoryDB: unparseable INSERT INTO trades');
      const cols = m[1].split(',').map((c) => c.trim().toLowerCase()).filter(Boolean);
      const vals = m[2].split(',').map((v) => v.trim()).filter(Boolean);
      if (cols.length !== vals.length) {
        throw new Error(`MemoryDB: INSERT INTO trades names ${cols.length} columns but supplies ${vals.length} values`);
      }
      const trade = { id: this._nextTradeId++ };
      let pi = 0;
      cols.forEach((c, k) => {
        const v = vals[k];
        if (v === '?') trade[c] = params[pi++];
        else if (/^'.*'$/.test(v)) trade[c] = v.slice(1, -1);
        else if (/^NULL$/i.test(v)) trade[c] = null;
        else if (/^-?\d+(\.\d+)?$/.test(v)) trade[c] = Number(v);
        else throw new Error(`MemoryDB: unsupported value ${v} in INSERT INTO trades`);
      });
      if (pi !== params.length) {
        throw new Error(`MemoryDB: INSERT INTO trades binds ${pi} placeholders but received ${params.length} params`);
      }
      if (!('status' in trade)) trade.status = 'OPEN';          // schema DEFAULT 'OPEN'
      if (!('fees' in trade)) trade.fees = 0;                   // DEFAULT 0
      if (!('venue' in trade)) trade.venue = 'bitget';          // DEFAULT 'bitget'
      if (!('opened_at' in trade)) trade.opened_at = new Date(); // DEFAULT CURRENT_TIMESTAMP
      this.trades.push(trade);
      return [{ insertId: trade.id }, []];
    }

    if (cmd.includes('UPDATE USERS SET TOTP_SECRET')) {
      // params: [secret|null, enabled, backup_codes_json|null, id]
      const u = this.users.find(x => x.id === params[params.length - 1]);
      if (u) {
        u.totp_secret = params[0];
        u.totp_enabled = params[1];
        u.totp_backup_codes = params[2];
      }
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET TOTP_BACKUP_CODES')) {
      // params: [backup_codes_json, id]
      const u = this.users.find(x => x.id === params[1]);
      if (u) u.totp_backup_codes = params[0];
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET PLAN')) {
      // Tier sync: params [plan, telegram_id]
      const u = this.users.find(x => String(x.telegram_id) === String(params[1]));
      if (u) u.plan = params[0];
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    // The users half of account erasure: tombstone the row in place.
    //
    // Identified by its `plan = 'deleted'` clause rather than by a prefix — an
    // `UPDATE USERS SET EMAIL = ?` also fits an ordinary address change, and
    // answering that one with a full identity wipe would be catastrophic.
    //
    // The nulled columns are read OUT OF THE STATEMENT rather than restated
    // here. `IDENTIFYING_COLUMNS` changes when somebody adds an identifying
    // field, and a second hand-maintained copy of that list in the shim would
    // drift exactly once: silently, the day it started to matter.
    if (cmd.startsWith('UPDATE USERS SET EMAIL = ?')
        && cmd.includes("PLAN = 'DELETED'")) {
      const u = this.users.find(x => String(x.id) === String(params[params.length - 1]));
      if (!u) return [{ affectedRows: 0 }, []];
      u.email = params[0];
      for (const m of sql.matchAll(/(\w+)\s*=\s*NULL/gi)) u[m[1]] = null;
      u.telegram_linked = false;
      u.email_verified = false;
      u.token_epoch = (u.token_epoch || 0) + 1;
      u.plan = 'deleted';
      return [{ affectedRows: 1 }, []];
    }

    if (cmd.includes('UPDATE TRADES SET NOTES')) {
      // params: notes, id, user_id
      const t = this.trades.find(t => t.id === params[1] && t.user_id === params[2]);
      if (t) t.notes = params[0];
      return [{ affectedRows: t ? 1 : 0 }, []];
    }

    // The scored-denominator aggregate, shared by routes/portfolio.js,
    // routes/leaderboard.js and routes/sync.js:
    //
    //   COUNT(*) AS total, SUM(CASE WHEN pnl IS NOT NULL ...) AS scored,
    //   SUM(CASE WHEN pnl > 0 ...) AS wins, SUM(pnl) AS net_pnl
    //
    // `trades.pnl` is nullable, so `total` and `scored` are DIFFERENT numbers
    // and this shim must not collapse them — that distinction is the entire
    // reason the query exists. Matched before the older handlers below, whose
    // looser `COUNT(*) && PNL > 0` test would otherwise swallow it and answer
    // with a lone `wins`, leaving every other field undefined.
    if (cmd.includes('PNL IS NOT NULL') && cmd.includes('COUNT(*)') && cmd.includes('SUM(PNL)')) {
      const all = params.length
        ? this.trades.filter(t => t.user_id === params[0]
            && t.status === (params[1] || 'CLOSED'))
        : this.trades.filter(t => t.status === 'CLOSED');
      const priced = all.filter(t => t.pnl !== null && t.pnl !== undefined
        && Number.isFinite(parseFloat(t.pnl)));
      return [[{
        total: all.length,
        scored: priced.length,
        wins: priced.filter(t => parseFloat(t.pnl) > 0).length,
        // SUM over an empty set is NULL in MySQL, not 0 — and the callers rely
        // on that to tell an unpriceable book from a flat one.
        net_pnl: priced.length
          ? priced.reduce((a, t) => a + parseFloat(t.pnl), 0) : null,
      }], []];
    }

    if (cmd.includes('SUM(PNL)') && cmd.includes('SUM(FEES)') && cmd.includes('COUNT(*)')) {
      const closed = this.trades.filter(t => t.user_id === params[0] && t.status === 'CLOSED');
      const net_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      const total_fees = closed.reduce((a, t) => a + (parseFloat(t.fees) || 0), 0);
      return [[{ net_pnl, total_fees, total_trades: closed.length }], []];
    }

    if (cmd.includes('COALESCE(SUM(PNL)') && !cmd.includes('SUM(FEES)')) {
      const closed = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]);
      const total_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      return [[{ total_pnl }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('PNL > 0')) {
      const wins = this.trades.filter(t => t.user_id === params[0] && t.status === params[1] && parseFloat(t.pnl) > 0);
      return [[{ wins: wins.length }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('FROM TRADES') && cmd.includes('STATUS') && !cmd.includes('PNL')) {
      if (cmd.includes('OPEN')) {
        const count = this.trades.filter(t => t.user_id === params[0] && t.status === 'OPEN').length;
        return [[{ open_count: count }], []];
      }
      // /api/trades/history's count query has status = 'CLOSED' as a literal,
      // not a bound param -- there is no params[1] to compare against there.
      const status = cmd.includes("STATUS = 'CLOSED'") ? 'CLOSED' : params[1];
      const count = this.trades.filter(t => t.user_id === params[0] && t.status === status).length;
      return [[{ total: count }], []];
    }

    if (cmd.includes('SELECT PNL, SIZE_USD')) {
      const rows = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]).sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at));
      return [rows, []];
    }

    if (cmd.includes('COALESCE(CLOSED_AT')) {
      // GET /api/trades/activity -- both OPEN and CLOSED trades, newest first
      const limit = params[1] || 60;
      const rows = this.trades.filter(t => t.user_id === params[0])
        .sort((a, b) => new Date(b.closed_at || b.opened_at) - new Date(a.closed_at || a.opened_at))
        .slice(0, limit);
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'CLOSED'") && cmd.includes('ORDER BY CLOSED_AT ASC')) {
      // Track-record aggregation: full closed history, oldest first.
      const rows = this.trades
        .filter(t => t.user_id === params[0] && t.status === 'CLOSED' && t.closed_at)
        .sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at));
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'CLOSED'") && cmd.includes('ORDER BY CLOSED_AT DESC')) {
      let rows = this.trades.filter(t => t.user_id === params[0] && t.status === 'CLOSED').sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at));
      const limit = params[1] || 50;
      const offset = params[2] || 0;
      rows = rows.slice(offset, offset + limit);
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'OPEN'") && cmd.includes('ORDER BY OPENED_AT')) {
      const rows = this.trades.filter(t => t.user_id === params[0] && t.status === 'OPEN').sort((a, b) => new Date(b.opened_at) - new Date(a.opened_at));
      return [rows, []];
    }

    // -- EQUITY SNAPSHOTS --
    if (cmd.includes('DELETE FROM EQUITY_SNAPSHOTS')) {
      this.snapshots = this.snapshots.filter(s => s.user_id !== params[0]);
      return [{ affectedRows: 0 }, []];
    }

    if (cmd.includes('INSERT INTO EQUITY_SNAPSHOTS')) {
      this.snapshots.push({ id: this._nextSnapId++, user_id: params[0], equity: params[1], snapshot_at: params[2] });
      return [{ insertId: this._nextSnapId - 1 }, []];
    }

    // Global equity snapshot query (no user_id filter) - for public portfolio summary
    if (cmd.includes('FROM EQUITY_SNAPSHOTS') && cmd.includes('ORDER BY SNAPSHOT_AT DESC') && params.length === 0) {
      const rows = [...this.snapshots].sort((a, b) => new Date(b.snapshot_at) - new Date(a.snapshot_at)).slice(0, 1);
      return [rows, []];
    }

    if (cmd.includes('FROM EQUITY_SNAPSHOTS') && cmd.includes('ORDER BY SNAPSHOT_AT DESC')) {
      const rows = this.snapshots.filter(s => s.user_id === params[0]).sort((a, b) => new Date(b.snapshot_at) - new Date(a.snapshot_at)).slice(0, 1);
      return [rows, []];
    }

    if (cmd.includes('FROM EQUITY_SNAPSHOTS')) {
      const rows = this.snapshots.filter(s => s.user_id === params[0]).sort((a, b) => new Date(a.snapshot_at) - new Date(b.snapshot_at)).slice(0, 365);
      return [rows, []];
    }

    // Global trade stats queries (no user_id filter) - for public portfolio summary
    if (cmd.includes('COUNT(*)') && cmd.includes('SUM(PNL)') && cmd.includes("STATUS = 'CLOSED'") && params.length === 0) {
      const closed = this.trades.filter(t => t.status === 'CLOSED');
      const net_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      return [[{ total: closed.length, net_pnl }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes("STATUS = 'OPEN'") && params.length === 0) {
      const count = this.trades.filter(t => t.status === 'OPEN').length;
      return [[{ open_count: count }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('PNL > 0') && params.length === 0) {
      const wins = this.trades.filter(t => t.status === 'CLOSED' && parseFloat(t.pnl) > 0).length;
      return [[{ wins }], []];
    }

    // -- SCAN CACHE --
    if (cmd.includes('REPLACE INTO SCAN_CACHE') || (cmd.includes('INSERT') && cmd.includes('SCAN_CACHE'))) {
      this.scanCache = { id: 1, scan_json: params[0], updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }

    if (cmd.includes('FROM SCAN_CACHE')) {
      return [this.scanCache ? [this.scanCache] : [], []];
    }

    // -- WALLET LINK CODES (phone/QR flow) --
    if (cmd.includes('INTO WALLET_LINK_CODES')) {          // REPLACE INTO ... (code, user_id, expires_at)
      this.walletLinkCodes[params[0]] = { code: params[0], user_id: params[1], expires_at: params[2] };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM WALLET_LINK_CODES')) {
      if (cmd.includes('EXPIRES_AT <')) {                  // prune expired
        const cutoff = params[0];
        for (const k of Object.keys(this.walletLinkCodes)) {
          if (this.walletLinkCodes[k].expires_at < cutoff) delete this.walletLinkCodes[k];
        }
      } else {
        delete this.walletLinkCodes[params[0]];
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM WALLET_LINK_CODES')) {
      const rec = this.walletLinkCodes[params[0]];
      return [rec ? [{ user_id: rec.user_id, expires_at: rec.expires_at }] : [], []];
    }

    // -- WALLET LINK NONCES (phone/QR flow) --
    if (cmd.includes('INTO WALLET_LINK_NONCES')) {         // REPLACE INTO ... (address, message, expires_at)
      this.walletLinkNonces[params[0]] = { address: params[0], message: params[1], expires_at: params[2] };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM WALLET_LINK_NONCES')) {
      if (cmd.includes('EXPIRES_AT <')) {
        const cutoff = params[0];
        for (const k of Object.keys(this.walletLinkNonces)) {
          if (this.walletLinkNonces[k].expires_at < cutoff) delete this.walletLinkNonces[k];
        }
      } else {
        delete this.walletLinkNonces[params[0]];
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM WALLET_LINK_NONCES')) {
      const rec = this.walletLinkNonces[params[0]];
      return [rec ? [{ message: rec.message, expires_at: rec.expires_at }] : [], []];
    }

    // -- DAILY DUEL --
    // Both tables carry a UNIQUE key in the real schema and BOTH are enforced
    // here. They are not conveniences: (day, idx) is what makes lazy round
    // creation race-safe, and (user_id, round_id) IS the write-once anti-cheat.
    // A shim that let a second pick through would keep the suite green while
    // production let players revise a call after seeing the outcome.
    if (cmd.includes('INSERT INTO DUEL_ROUNDS') || cmd.includes('INSERT IGNORE INTO DUEL_ROUNDS')) {
      const [day, idx] = params;
      if (this.duelRounds.some(r => r.day === day && Number(r.idx) === Number(idx))) {
        // INSERT IGNORE: the row already exists, nothing inserted, no error.
        return [{ affectedRows: 0, insertId: 0 }, []];
      }
      this.duelRounds.push({
        id: this._nextDuelRoundId++, day, idx: Number(idx), symbol: params[2],
        agent_direction: params[3] == null ? null : params[3],
        signal_key: params[4] == null ? null : params[4],
        created_at: new Date().toISOString(),
      });
      return [{ affectedRows: 1, insertId: this._nextDuelRoundId - 1 }, []];
    }
    if (cmd.includes('FROM DUEL_ROUNDS')) {
      // '>=' is checked first: 'WHERE DAY >= ?' does not contain 'WHERE DAY = ?',
      // but keeping the wider window ahead of the exact match documents intent.
      let rows;
      if (cmd.includes('WHERE DAY >=')) {
        rows = this.duelRounds.filter(r => r.day >= params[0]);
      } else if (cmd.includes('WHERE DAY =')) {
        rows = this.duelRounds.filter(r => r.day === params[0]);
      } else {
        rows = this.duelRounds.slice();
      }
      rows = rows.slice().sort((a, b) =>
        (a.day < b.day ? -1 : a.day > b.day ? 1 : a.idx - b.idx));
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('INSERT INTO DUEL_PICKS')) {
      const [user_id, round_id] = params;
      if (this.duelPicks.some(p => Number(p.user_id) === Number(user_id)
        && Number(p.round_id) === Number(round_id))) {
        const err = new Error("Duplicate entry for key 'uniq_duel_pick'");
        err.code = 'ER_DUP_ENTRY';
        err.errno = 1062;
        throw err;
      }
      this.duelPicks.push({
        id: this._nextDuelPickId++, user_id: Number(user_id), round_id: Number(round_id),
        pick: params[2],
        entry_price: params[3],
        resolves_at: params[4],
        settle_price: null, settle_state: null, settled_at: null,
        seal: params[5] == null ? null : params[5],
        seal_payload: params[6] == null ? null : params[6],
        created_at: params[7] || new Date().toISOString(),
      });
      return [{ affectedRows: 1, insertId: this._nextDuelPickId - 1 }, []];
    }
    if (cmd.startsWith('UPDATE DUEL_PICKS SET SETTLE')) {
      // params: settle_price, settle_state, settled_at, id
      const p = this.duelPicks.find(x => Number(x.id) === Number(params[3]));
      if (p) {
        p.settle_price = params[0] == null ? null : params[0];
        p.settle_state = params[1] == null ? null : params[1];
        p.settled_at = params[2];
      }
      return [{ affectedRows: p ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM DUEL_PICKS')) {
      let rows;
      if (cmd.includes('WHERE USER_ID =')) {
        rows = this.duelPicks.filter(p => Number(p.user_id) === Number(params[0]));
      } else if (cmd.includes('WHERE CREATED_AT >=')) {
        rows = this.duelPicks.filter(p =>
          new Date(p.created_at).getTime() >= new Date(params[0]).getTime());
      } else {
        rows = this.duelPicks.slice();
      }
      return [rows.map(p => ({ ...p })), []];
    }

    // ── siwf_nonces: single-use sign-in nonces ────────────────────────────
    // Implemented rather than left to fall through, because the property under
    // test is that a nonce is spendable EXACTLY ONCE — and a shim that
    // answered empty rows would report every nonce as unknown, which passes a
    // "replay is refused" test for entirely the wrong reason.
    if (cmd.startsWith('INSERT INTO SIWF_NONCES')) {
      this.siwfNonces = this.siwfNonces || [];
      this.siwfNonces.push({
        nonce: params[0], created_at: params[1], expires_at: params[2], used_at: null,
      });
      return [{ affectedRows: 1 }, []];
    }
    // Matched on the TABLE, not an exact column list. The shim's job here is
    // to model the row, and pinning the projection would make it answer one
    // caller and throw at the next — which reads as "unimplemented" for a
    // statement that is merely spelled differently.
    if (cmd.startsWith('SELECT') && cmd.includes('FROM SIWF_NONCES')) {
      this.siwfNonces = this.siwfNonces || [];
      const hit = this.siwfNonces.filter(n => n.nonce === params[0]);
      return [hit.map(n => ({ ...n })), []];
    }
    if (cmd.startsWith('UPDATE SIWF_NONCES SET USED_AT')) {
      // The `AND used_at IS NULL` condition is the whole point: it is what
      // makes two concurrent consumers of one nonce resolve to one winner.
      // Dropping it here would make the race test pass against a shim that
      // does not model the thing being tested.
      this.siwfNonces = this.siwfNonces || [];
      // HONOUR THE WHERE CLAUSE WE WERE ACTUALLY GIVEN. The first version
      // applied `used_at IS NULL` unconditionally, which made the shim MORE
      // correct than the statement it was handed — so deleting that condition
      // from the real SQL changed nothing here and the race test went on
      // passing. A mutation is what said so: the guard was in the double, not
      // in the code under test.
      const guarded = cmd.includes('USED_AT IS NULL');
      const row = this.siwfNonces.find(n =>
        n.nonce === params[1] && (!guarded || n.used_at == null));
      if (!row) return [{ affectedRows: 0 }, []];
      row.used_at = params[0];
      return [{ affectedRows: 1 }, []];
    }

    // NO BRANCH MATCHED. This used to `return [[], []]` — the shim inventing
    // an answer it does not have, in the one shape this repository treats as
    // the founding defect: a failed read rendering as an empty result.
    //
    // It is not hypothetical and it is not rare in consequence. Every test in
    // the ~2,400-test web suite runs against this class, so an unimplemented
    // statement did not fail — it passed, quietly, and the surface above it
    // was asserted to be correct while reading from nothing. `flight_cache`
    // above was exactly that: four writes and a read, all answered with empty
    // rows, all green.
    //
    // Measured before switching: logging every fall-through across the whole
    // suite produced five hits and a single table. So this throw costs
    // nothing today, and the next unimplemented statement announces itself
    // instead of being absorbed.
    //
    // The message carries `describeSql`'s short descriptor — a verb and a
    // table name from DDL this repository authors. No values, no user data,
    // no secrets, same contract as `_lastStatement`.
    const err = new Error(
      `MemoryDB has no handler for: ${describeSql(sql) || 'unrecognised statement'}. `
      + 'The in-memory shim answers only the statements it implements; '
      + 'returning empty rows here would report "no data" for a query that '
      + 'was never run. Add a branch for it in MemoryDB.query().');
    err.code = 'ER_MEMORYDB_UNIMPLEMENTED';
    throw err;
  }
}

if (!USE_MYSQL) {
  memDb = new MemoryDB();
  pool = memDb;
  console.log('Using in-memory database (no DATABASE_URL found)');
}

/**
 * Every table the migration creates. Kept beside the DDL and pinned to it by
 * a test, so the two cannot drift: a new CREATE TABLE without a new entry
 * here would make the fast path below skip it forever.
 */
const EXPECTED_TABLES = Object.freeze([
  'users',
  'trades',
  'equity_snapshots',
  'scan_cache',
  'wallet_link_codes',
  'wallet_link_nonces',
  'signals',
  'push_subscriptions',
  'copy_subscriptions',
  'user_profiles',
  'agent_letters',
  'user_alerts',
  'user_strategies',
  'reports_cache',
  'flight_cache',
  'pending_stance',
  'agent_events',
  'bot_sealing_key',
  'pending_credentials',
  'exchange_status',
  'pending_controls',
  'user_controls',
  'pending_flatten',
  'arena_accounts',
  'arena_positions',
  'arena_trades',
  'arena_api_keys',
  'arena_envelopes',
  'agents',
  'scan_seals',
  'learn_diary',
  'learn_progress',
  'seal_roots',
  'arena_seasons',
  'arena_follows',
  'user_watchlist',
  'duel_rounds',
  'duel_picks',
  'siwf_nonces',
]);

/**
 * Is the schema already in place? Returns true only when EVERY expected table
 * exists — a partial schema must still run the full DDL.
 *
 * Why this exists: the migration is 33 distributed DDL statements, and on a
 * serverless cluster it takes minutes. Re-running it on every boot to create
 * tables that already exist burned that time on each restart, held the
 * readiness gate closed the whole while, and spent connections doing nothing.
 * One information_schema query answers the same question in milliseconds.
 *
 * Fail-SAFE: any error returns false, so an unreadable check runs the
 * migration rather than skipping it. Being slow is recoverable; silently
 * skipping schema creation is not.
 */
async function schemaIsCurrent() {
  try {
    const [rows] = await pool.query(
      'SELECT TABLE_NAME AS t FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()');
    const have = new Set((rows || []).map((r) => String(r.t || r.TABLE_NAME || '').toLowerCase()));
    return EXPECTED_TABLES.every((t) => have.has(t.toLowerCase()));
  } catch (e) {
    return false;
  }
}

async function migrate() {
  // DDL goes through query(), NOT execute().
  //
  // execute() uses mysql2's binary PREPARED-STATEMENT protocol, and server
  // support for PREPARING DDL is not universal — TiDB answers a statement it
  // cannot prepare with ER_PARSE_ERROR (1064), a SYNTAX error, for SQL that is
  // perfectly valid. That is what took the website's database down: the
  // connection, TLS and the credentials were all fine and the server was
  // answering, but every migration attempt died on a 1064 that named nothing,
  // which read for hours like a connection-string or an allowlist fault.
  //
  // Nothing here is parameterised — 32 CREATE TABLE statements, no
  // placeholders, no bound values — so preparing them buys exactly nothing and
  // costs portability. query() sends the text protocol, which is what DDL
  // wants. Every OTHER call in this file still uses execute(): those carry
  // user values, and prepared statements are how they stay injection-safe.
  if (USE_MYSQL) {
    // Fast path: if every table is already there, the 33 statements below
    // are no-ops that still cost minutes on a serverless cluster. Skip them.
    if (await schemaIsCurrent()) {
      console.log('Schema already current — skipping DDL');
      return;
    }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        plan VARCHAR(50) DEFAULT 'free',
        telegram_linked BOOLEAN DEFAULT FALSE,
        telegram_id VARCHAR(32) DEFAULT NULL,
        link_token VARCHAR(100),
        link_token_expires TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Back-fill telegram_id on pre-existing deployments (CREATE TABLE IF NOT
    // EXISTS won't add it). Ignore the duplicate-column error if already present.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN telegram_id VARCHAR(32) DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    // Token epoch (M12): bumped on logout and on password change, so every
    // token minted before that moment stops verifying. Without it a stolen
    // 30-day JWT was an unrevocable month of account access — including
    // submitting exchange API keys. `bot/api/token_store.py` has done this
    // since RC-AUD-020; the Express side never got it.
    //
    // DEFAULT 0 matters: existing users get epoch 0, and tokens minted before
    // this column existed carry no epoch, which reads as 0 — so nobody is
    // logged out by the deploy that adds it.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN token_epoch INT NOT NULL DEFAULT 0');
    } catch (e) { /* exists */ }
    // 2FA (MH1): TOTP secret, enabled flag, hashed one-time backup codes.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_enabled TINYINT NOT NULL DEFAULT 0');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_backup_codes TEXT DEFAULT NULL');
    } catch (e) { /* exists */ }
    // OAuth: google_id + avatar_url, and password_hash must be nullable
    // (OAuth accounts have no password). Each guarded — ignore if present.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN google_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Social OAuth expansion: Discord + X (Twitter) provider identities.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN discord_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN x_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Multi-venue exchange keys: exchange_status becomes one row per
    // (user, venue) so several connected exchanges coexist. DROP+ADD in one
    // statement is idempotent — re-running recreates the same composite key.
    try {
      await pool.execute('ALTER TABLE exchange_status DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, exchange)');
    } catch (e) { /* already composite / column constraints — fine */ }
    // Alerts 2.0: recurring mode + cooldown on pre-existing deployments.
    try {
      await pool.execute("ALTER TABLE user_alerts ADD COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'once'");
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE user_alerts ADD COLUMN cooldown_min INT NOT NULL DEFAULT 60');
    } catch (e) { /* exists */ }
    // Self-custody sign-in: the user's EVM wallet address (lowercased, unique).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN wallet_address VARCHAR(42) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Solana WATCH address (base58, read-only mirror — no signing surface).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN sol_address VARCHAR(48) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Was that address PROVEN by an ed25519 signature, or merely typed in?
    // The server has always verified the signature correctly and returned the
    // answer — then discarded it, so a proven address and a pasted one were
    // stored identically and both rendered as "watch address". Defaults to 0,
    // which is the honest reading of every row that existed before this column:
    // unverified until someone signs.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN sol_verified TINYINT(1) NOT NULL DEFAULT 0');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_wallet_address ON users (wallet_address)');
    } catch (e) { /* index exists */ }
    try {
      await pool.execute('ALTER TABLE users MODIFY COLUMN password_hash VARCHAR(255) NULL');
    } catch (e) { /* already nullable */ }
    // Account management: email verification + password reset. Tokens are
    // stored HASHED (sha256 hex) — a DB leak can't be replayed to reset/verify.
    // Each ALTER is guarded so re-running migrate() on a live DB is a no-op.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN verify_token VARCHAR(100) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN verify_token_expires TIMESTAMP NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN reset_token VARCHAR(100) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP NULL');
    } catch (e) { /* exists */ }
    // Invite / referral: each user's own share code + who referred them.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN referral_code VARCHAR(16) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN referred_by INT DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_referral_code ON users (referral_code)');
    } catch (e) { /* index exists */ }
    // Leaderboard opt-in: an anonymous display handle (NULL = not on the board).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN leaderboard_handle VARCHAR(24) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_leaderboard_handle ON users (leaderboard_handle)');
    } catch (e) { /* index exists */ }
    // Welcome-back digest: when the user last read /api/since (NULL = never).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP NULL DEFAULT NULL');
    } catch (e) { /* exists */ }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS trades (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(30) NOT NULL,
        direction VARCHAR(10) NOT NULL,
        entry_price DECIMAL(18,8) NOT NULL,
        exit_price DECIMAL(18,8),
        size_usd DECIMAL(14,2) NOT NULL,
        pnl DECIMAL(14,2),
        fees DECIMAL(14,2) DEFAULT 0,
        status VARCHAR(10) DEFAULT 'OPEN',
        pattern VARCHAR(100),
        stop_loss DECIMAL(18,8),
        take_profit DECIMAL(18,8),
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP NULL,
        notes TEXT DEFAULT NULL,
        venue VARCHAR(20) NOT NULL DEFAULT 'bitget',
        INDEX idx_user_status (user_id, status),
        INDEX idx_user_opened (user_id, opened_at)
      )
    `);
    // Back-fill notes on pre-existing deployments (CREATE TABLE IF NOT EXISTS
    // won't add it). Ignore the duplicate-column error if already present.
    try {
      await pool.execute('ALTER TABLE trades ADD COLUMN notes TEXT DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    // WHERE THE TRADE HAPPENED. The bot learned to record this (TradeExecution
    // and JournalEntry both carry it), and the attribution died at the wire:
    // this table had no column for it and the sync sent none, so the dashboard
    // — the surface someone actually looks at — could never show it.
    //
    // NOT NULL DEFAULT 'bitget' because that is what every existing row IS. A
    // nullable column would make history read as "venue unknown" when it is
    // known perfectly well, and would put a NULL through every group-by.
    try {
      await pool.execute(
        "ALTER TABLE trades ADD COLUMN venue VARCHAR(20) NOT NULL DEFAULT 'bitget'");
    } catch (e) { /* column already exists — fine */ }
    // Multi-venue selection, for deployments whose tables predate it. Nullable
    // on BOTH: NULL means "nothing proposed" / "the bot has not told us yet",
    // and neither is the same as "no venues".
    for (const stmt of [
      'ALTER TABLE pending_controls ADD COLUMN venues VARCHAR(200) DEFAULT NULL',
      'ALTER TABLE user_controls ADD COLUMN venues VARCHAR(200) DEFAULT NULL',
      'ALTER TABLE user_controls ADD COLUMN venues_mode VARCHAR(16) DEFAULT NULL',
    ]) {
      try { await pool.execute(stmt); } catch (e) { /* already there — fine */ }
    }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS equity_snapshots (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        equity DECIMAL(14,2) NOT NULL,
        snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user_snap (user_id, snapshot_at)
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS scan_cache (
        id INT PRIMARY KEY DEFAULT 1,
        scan_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Phone/QR wallet-link: short-lived single-use codes + sign nonces persisted
    // so the flow survives a web restart or a second instance between "show QR"
    // and "phone signs" (see lib/wallet_link_store). expires_at is epoch ms.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS wallet_link_codes (
        code VARCHAR(32) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        expires_at BIGINT NOT NULL
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS wallet_link_nonces (
        address VARCHAR(64) PRIMARY KEY,
        message TEXT NOT NULL,
        expires_at BIGINT NOT NULL
      )
    `);
    // Global signal stream (every generated signal, taken or not). signal_key is
    // a stable per-signal id from the bot so re-syncs UPSERT (update outcome)
    // instead of duplicating. pnl/status are filled when the signal resolves.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS signals (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        signal_key VARCHAR(128) NOT NULL UNIQUE,
        symbol VARCHAR(32) NOT NULL,
        direction VARCHAR(8) NOT NULL,
        confidence DECIMAL(6,4) DEFAULT 0,
        score DECIMAL(10,4) DEFAULT 0,
        pattern VARCHAR(64) DEFAULT NULL,
        regime VARCHAR(32) DEFAULT NULL,
        entry_price DECIMAL(20,8) DEFAULT 0,
        stop_loss DECIMAL(20,8) DEFAULT 0,
        take_profit DECIMAL(20,8) DEFAULT 0,
        rr DECIMAL(10,4) DEFAULT 0,
        thesis TEXT DEFAULT NULL,
        status VARCHAR(16) DEFAULT 'NEW',
        pnl DECIMAL(20,8) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP NULL DEFAULT NULL,
        seal VARCHAR(64) DEFAULT NULL,
        seal_payload TEXT DEFAULT NULL,
        sealed_at TIMESTAMP NULL DEFAULT NULL,
        INDEX idx_created (created_at),
        INDEX idx_symbol (symbol)
      )
    `);
    // Provable Calls columns for pre-existing installs (fresh installs get
    // them from the CREATE above).
    for (const ddl of [
      'ALTER TABLE signals ADD COLUMN seal VARCHAR(64) DEFAULT NULL',
      'ALTER TABLE signals ADD COLUMN seal_payload TEXT DEFAULT NULL',
      'ALTER TABLE signals ADD COLUMN sealed_at TIMESTAMP NULL DEFAULT NULL',
    ]) {
      try { await pool.execute(ddl); } catch (e) { /* exists */ }
    }
    // Web-push subscriptions (opt-in, per browser). endpoint is the unique
    // key so re-subscribing the same browser updates instead of duplicating.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS push_subscriptions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        endpoint VARCHAR(500) NOT NULL UNIQUE,
        keys_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_push_user (user_id)
      )
    `);
    // Strategy-Agent follows (Marketplace Phase 3). A user "follows" a listed
    // agent to surface its live would-take picks and (opt-in) milestone alerts.
    // Follow-only — nothing here moves real funds; copying is user-initiated and
    // paper-only via the normal trade ticket. agent_id is a catalogue slug.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS copy_subscriptions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        agent_id VARCHAR(64) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_copy_user_agent (user_id, agent_id),
        INDEX idx_copy_user (user_id)
      )
    `);
    // Per-user agent profile: the user's OWN risk preference (display + chat
    // context only — never touches the operator bot's global stance), pinned
    // watchlist, and UI prefs. JSON columns are validated/whitelisted by
    // routes/profile.js before write.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INT PRIMARY KEY,
        risk_pref VARCHAR(16) DEFAULT NULL,
        watchlist TEXT DEFAULT NULL,
        prefs TEXT DEFAULT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Weekly agent letters — one per completed ISO week, composed entirely
    // from recorded data (lib/letter.js). week_key is the natural key.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS agent_letters (
        id INT AUTO_INCREMENT PRIMARY KEY,
        week_key VARCHAR(10) NOT NULL UNIQUE,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        letter_json LONGTEXT NOT NULL
      )
    `);
    // Custom user alerts ("tell me when BTC drops below 100k"). One-shot
    // tripwires: the alert engine (lib/alerts.js) evaluates active rows
    // against public tickers and deactivates a row as it trips. Notification
    // only — an alert can never place or touch a trade.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS user_alerts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(24) NOT NULL,
        metric VARCHAR(20) NOT NULL DEFAULT 'price',
        op VARCHAR(2) NOT NULL,
        threshold DOUBLE NOT NULL,
        mode VARCHAR(12) NOT NULL DEFAULT 'once',
        cooldown_min INT NOT NULL DEFAULT 60,
        active TINYINT NOT NULL DEFAULT 1,
        trigger_price DOUBLE DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        triggered_at TIMESTAMP NULL DEFAULT NULL,
        INDEX idx_alerts_user (user_id),
        INDEX idx_alerts_active (active)
      )
    `);
    // User-authored marketplace strategies. A strategy is a CONFIG (intent-rule
    // chips + prose), never a performance claim — no dollar/stat columns (§4).
    // `rules` is a JSON array of {type,value}; `visibility` gates the public
    // marketplace. Saving/publishing never touches a trade.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS user_strategies (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        slug VARCHAR(64) NOT NULL,
        name VARCHAR(80) NOT NULL,
        tagline VARCHAR(160),
        how VARCHAR(600),
        icon VARCHAR(8),
        rules JSON NOT NULL,
        risk_label VARCHAR(24),
        regime VARCHAR(24),
        horizon VARCHAR(24),
        visibility VARCHAR(12) NOT NULL DEFAULT 'draft',
        published_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_strat_user (user_id),
        INDEX idx_strat_pub (visibility, slug)
      )
    `);
    // Back-fill published_at on pre-existing deployments (CREATE TABLE IF NOT
    // EXISTS won't add it). NULL for already-public legacy rows on purpose —
    // inventing a publish date they never had would be a fabricated number;
    // the public ordering falls back to created_at, which is real.
    try {
      await pool.execute('ALTER TABLE user_strategies ADD COLUMN published_at TIMESTAMP NULL DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    // Arena attribution: which signal (and which agent's pick) opened a paper
    // position, carried onto the closed trade. Nullable — only the verified
    // copy flow ever writes them; every other open stays NULL forever.
    for (const ddl of [
      'ALTER TABLE arena_positions ADD COLUMN signal_key VARCHAR(128) NULL',
      'ALTER TABLE arena_positions ADD COLUMN agent_slug VARCHAR(64) NULL',
      'ALTER TABLE arena_trades ADD COLUMN signal_key VARCHAR(128) NULL',
      'ALTER TABLE arena_trades ADD COLUMN agent_slug VARCHAR(64) NULL',
      'ALTER TABLE arena_trades ADD INDEX idx_arena_trades_agent (agent_slug)',
    ]) {
      try { await pool.execute(ddl); } catch (e) { /* already applied — fine */ }
    }
    // Bot-pushed intelligence reports (funding scan / arb tracker / parity /
    // yield radar) — single-row cache like scan_cache. The yield section is
    // operator-sensitive and only served to admin-plan users (routes/reports.js).
    await pool.query(`
      CREATE TABLE IF NOT EXISTS reports_cache (
        id INT PRIMARY KEY DEFAULT 1,
        reports_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Guardian Flight Recorder cache (single row): the bot pushes recent joined
    // decision records + the engine-verified hash-chain status. Read-only
    // provenance surface for the website — the authoritative ledger lives
    // bot-side in logs/audit_chain.jsonl.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS flight_cache (
        id INT PRIMARY KEY DEFAULT 1,
        flight_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Admin-queued strategy-stance change (global, single in-flight row).
    // The bot pulls it, re-verifies the requester's tier is 'admin' against
    // its OWN UserStore, applies RUNTIME.strategy_mode, then acks (deletes).
    await pool.query(`
      CREATE TABLE IF NOT EXISTS pending_stance (
        id INT PRIMARY KEY DEFAULT 1,
        mode VARCHAR(16) NOT NULL,
        requested_by INT NOT NULL,
        telegram_id VARCHAR(32) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Public agent mind-stream feed (bot-pushed, SSE-rebroadcast). Bounded
    // ring: the sync route prunes to the newest ~500 rows. No user data —
    // operator-agent activity only, pre-sanitized bot-side (agent_feed.py).
    await pool.query(`
      CREATE TABLE IF NOT EXISTS agent_events (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        event_type VARCHAR(24) NOT NULL,
        severity VARCHAR(16) DEFAULT 'info',
        symbol VARCHAR(32) DEFAULT NULL,
        title VARCHAR(300) NOT NULL,
        body TEXT DEFAULT NULL,
        data_json TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_agent_events_created (created_at)
      )
    `);
    // The bot's PUBLIC sealing key, published over the bot-secret sync channel
    // (lib/sealing_key.js). Single row. Nothing secret lives here — the private
    // half never leaves the bot — but it is persisted rather than kept in
    // memory because the web app restarts on every deploy, and a key that died
    // with the process would leave the connect form off until the bot's next
    // hourly publish.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS bot_sealing_key (
        id TINYINT PRIMARY KEY,
        kid VARCHAR(32) NOT NULL,
        pem TEXT NOT NULL,
        alg VARCHAR(64) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Pending exchange-credential submissions. The website seals the keys to
    // the bot's published key (RSA-OAEP + AES-256-GCM, above) — or, on a
    // deployment still running the legacy shared-key path, encrypts them under
    // WEB_CREDS_KEY — into encrypted_payload; the bot PULLS pending rows over
    // the shared-secret channel, imports them into its own Fernet store keyed
    // by telegram_id, then the row is deleted. One in-flight request per user
    // (UPSERT). Raw keys are NEVER stored in plaintext.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS pending_credentials (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        telegram_id VARCHAR(32) NOT NULL,
        exchange VARCHAR(16) DEFAULT 'bitget',
        action VARCHAR(16) DEFAULT 'connect',
        encrypted_payload TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Per-user exchange connection status, set by the bot's ack after it imports
    // (connect) or removes (disconnect) the credentials. Drives the web UI badge.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS exchange_status (
        user_id INT NOT NULL,
        exchange VARCHAR(16) NOT NULL DEFAULT 'bitget',
        connected BOOLEAN DEFAULT FALSE,
        -- Why the last attempt failed, in the VENUE's own words, or NULL when
        -- the last attempt succeeded. Without it a rejected key had nowhere to
        -- land: the ack was discarded, the pending row was never cleared, and
        -- the card sat on "applying…" forever while the bot re-failed the same
        -- row every 30s. A user cannot fix "applying"; they can fix "IP not
        -- whitelisted".
        last_error VARCHAR(200) DEFAULT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, exchange)
      )
    `);
    // Back-fill on pre-existing deployments — CREATE TABLE IF NOT EXISTS will
    // not add it. NULL default, so a row written before this column existed
    // reads as "no recorded failure", which is what it means.
    try {
      await pool.execute('ALTER TABLE exchange_status ADD COLUMN last_error VARCHAR(200) DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    // Pending per-user live-control changes (flags/numbers, not secrets — no
    // encryption). The web queues a change; the bot pulls + applies it via the
    // UserStore (live on/off, per-trade margin cap, pause-to-paper), then acks.
    // NULL columns mean "leave unchanged". One in-flight request per user.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS pending_controls (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        telegram_id VARCHAR(32) NOT NULL,
        live_enabled TINYINT DEFAULT NULL,
        max_margin DECIMAL(20,2) DEFAULT NULL,
        paused TINYINT DEFAULT NULL,
        -- Multi-venue: the venues this user PROPOSES to trade on, comma-
        -- separated. THREE states, and collapsing any two of them is a bug:
        --   NULL  no venue change proposed (another control may be)
        --   ''    proposed: clear the selection, back to a single venue
        --   'a,b' proposed: trade these
        -- NULL vs '' is the difference between "leave my venues alone" and
        -- "turn multi-venue off", and a writer that sends '' for both would
        -- silently drop somebody's selection every time they changed an
        -- unrelated control.
        venues VARCHAR(200) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Current applied control state, written back by the bot's ack (the bot's
    // UserStore is the source of truth; this mirrors it for the web UI).
    await pool.query(`
      CREATE TABLE IF NOT EXISTS user_controls (
        user_id INT PRIMARY KEY,
        live_enabled BOOLEAN DEFAULT FALSE,
        max_margin DECIMAL(20,2) DEFAULT NULL,
        paused BOOLEAN DEFAULT FALSE,
        allowlisted BOOLEAN DEFAULT FALSE,
        -- The venues the BOT actually holds, written by its ack. Distinct from
        -- pending_controls.venues on purpose: a user who sets venues on the web
        -- and is shown a tick before the bot has applied them believes they are
        -- trading two venues while every order still goes to one. This module's
        -- own docstring records that exact failure for pause-to-paper — the site
        -- showed "paused" while confirmed trades went to the exchange.
        venues VARCHAR(200) DEFAULT NULL,
        -- off | shadow | enforce, from the bot's ack. Stored NEXT TO the
        -- selection because a stored selection reads back identically whether
        -- multi-venue is enforcing or off, so the selection alone cannot answer
        -- whether the book is actually spread across them. Same shape, same
        -- reason, as user_controls tracking paused separately from whether
        -- paper mode is even available.
        --
        -- Kept free of backticks and question marks on purpose: this comment
        -- lives INSIDE a JS template literal, so a backtick ends the string,
        -- and the injection-boundary test greps for a placeholder character
        -- within pool.query template literals without stripping comments.
        -- Prose that asks a question reads as an unprepared query. CLAUDE.md
        -- records four earlier false failures of that shape; this file just
        -- produced the fifth and sixth.
        venues_mode VARCHAR(16) DEFAULT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Emergency-stop flatten requests. Separate from pending_controls because the
    // bot processes it asynchronously (closes the user's live positions via THEIR
    // own executor) and must not clear the request until the close completes.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS pending_flatten (
        user_id INT PRIMARY KEY,
        telegram_id VARCHAR(32) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Paper Trading Arena — virtual accounts for every registered user, no
    // exchange keys or bot gateway required. §4: virtual funds only; the public
    // leaderboard built on these shows percent return + opt-in handles only.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_accounts (
        user_id INT PRIMARY KEY,
        balance DOUBLE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_positions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        direction VARCHAR(5) NOT NULL,
        entry DOUBLE NOT NULL,
        margin DOUBLE NOT NULL,
        leverage INT NOT NULL,
        source VARCHAR(10) NOT NULL DEFAULT 'manual',
        tp DOUBLE NULL,
        sl DOUBLE NULL,
        exits_edited TINYINT(1) NOT NULL DEFAULT 0,
        trail_pct DOUBLE NULL,
        trade_key VARCHAR(40) NULL,
        seal VARCHAR(64) NULL,
        seal_payload TEXT NULL,
        sealed_at TIMESTAMP NULL,
        signal_key VARCHAR(128) NULL,
        agent_slug VARCHAR(64) NULL,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_arena_pos_user (user_id),
        INDEX idx_arena_pos_key (trade_key),
        INDEX idx_arena_pos_agent (agent_slug)
      )
    `);
    // Back-fill columns on pre-existing deployments (CREATE TABLE IF NOT
    // EXISTS won't add them).
    try {
      await pool.execute("ALTER TABLE arena_positions ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'manual'");
    } catch (e) { /* already present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN tp DOUBLE NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN sl DOUBLE NULL'); } catch (e) { /* present */ }
    // Exit-edit marker: the open-time seal records the ORIGINAL exits, so a
    // later edit must be visible or the receipt overstates discipline.
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN exits_edited TINYINT(1) NOT NULL DEFAULT 0'); } catch (e) { /* present */ }
    // Trailing distance (percent). When set, `sl` holds the ratcheted level.
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN trail_pct DOUBLE NULL'); } catch (e) { /* present */ }
    // Provable Calls v2 — arena receipts sealed at open time.
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN trade_key VARCHAR(40) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN seal VARCHAR(64) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN seal_payload TEXT NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD COLUMN sealed_at TIMESTAMP NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_positions ADD INDEX idx_arena_pos_key (trade_key)'); } catch (e) { /* present */ }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_trades (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        direction VARCHAR(5) NOT NULL,
        entry DOUBLE NOT NULL,
        exit_price DOUBLE NOT NULL,
        margin DOUBLE NOT NULL,
        leverage INT NOT NULL,
        pnl DOUBLE NOT NULL,
        reason VARCHAR(12) NOT NULL,
        trade_key VARCHAR(40) NULL,
        seal VARCHAR(64) NULL,
        seal_payload TEXT NULL,
        sealed_at TIMESTAMP NULL,
        signal_key VARCHAR(128) NULL,
        agent_slug VARCHAR(64) NULL,
        opened_at TIMESTAMP NULL,
        closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source VARCHAR(10) NOT NULL DEFAULT 'manual',
        INDEX idx_arena_tr_user (user_id),
        INDEX idx_arena_tr_key (trade_key),
        INDEX idx_arena_trades_agent (agent_slug)
      )
    `);
    // Provenance, carried from the position on close. `arena_positions` has
    // had `source` all along; the closed row did not, and the per-agent record
    // is built from CLOSED rows — so without this the record cannot tell a
    // trade the agent made itself from one a member copied, and would publish
    // the two summed under a heading that means only the second.
    try {
      await pool.execute("ALTER TABLE arena_trades ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'manual'");
    } catch (e) { /* already present */ }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_api_keys (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        key_hash CHAR(64) NOT NULL,
        label VARCHAR(40) NOT NULL DEFAULT '',
        created_at TIMESTAMP NULL,
        last_used_at TIMESTAMP NULL,
        revoked_at TIMESTAMP NULL,
        agent_slug VARCHAR(64) NULL,
        UNIQUE KEY uniq_arena_key_hash (key_hash),
        KEY idx_arena_keys_user (user_id)
      )
    `);
    // The identity this key trades as. NULL means "trades as its owner", which
    // is every key that existed before this column — the agent record only
    // ever sees a slug that was deliberately bound.
    try {
      await pool.execute('ALTER TABLE arena_api_keys ADD COLUMN agent_slug VARCHAR(64) NULL');
    } catch (e) { /* already present */ }
    // Pre-signature scan receipts. The INPUT IS NEVER STORED — only its
    // sha256, inside `seal_payload`. Both scanners promise callers that
    // nothing they send is kept, and sealing must not quietly break that
    // promise: what is retained is a commitment to the bytes, which is
    // checkable by whoever holds them and inert to everyone else.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS scan_seals (
        id INT AUTO_INCREMENT PRIMARY KEY,
        scan_key VARCHAR(40) NOT NULL,
        user_id INT NULL,
        agent_slug VARCHAR(64) NULL,
        tool VARCHAR(32) NOT NULL,
        seal CHAR(64) NOT NULL,
        seal_payload TEXT NOT NULL,
        sealed_at TIMESTAMP NULL,
        UNIQUE KEY uniq_scan_key (scan_key),
        KEY idx_scan_seals_user (user_id),
        KEY idx_scan_seals_agent (agent_slug),
        KEY idx_scan_seals_sealed (sealed_at)
      )
    `);
    // Agent identity — a slug that belongs to someone. `seal`/`sealed_at`
    // are not decoration: lib/seal_roots.js selects seals by `sealed_at`
    // across every sealed surface, so a claim rides into that day's Merkle
    // root and is anchored on Base with the trades. "This agent existed on
    // this date" then rests on a block timestamp rather than on this column.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS agents (
        id INT AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(64) NOT NULL,
        user_id INT NOT NULL,
        display_name VARCHAR(80) NULL,
        seal CHAR(64) NOT NULL,
        seal_payload TEXT NOT NULL,
        sealed_at TIMESTAMP NULL,
        created_at TIMESTAMP NULL,
        UNIQUE KEY uniq_agent_slug (slug),
        KEY idx_agents_user (user_id),
        KEY idx_agents_sealed (sealed_at)
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_envelopes (
        user_id INT PRIMARY KEY,
        source_text TEXT NOT NULL,
        rules_json TEXT NOT NULL,
        enabled TINYINT NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS learn_diary (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        day CHAR(10) NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMP NULL,
        edited_at TIMESTAMP NULL,
        UNIQUE KEY uq_learn_user_day (user_id, day),
        INDEX idx_learn_user (user_id)
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS learn_progress (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        slug VARCHAR(80) NOT NULL,
        done_at TIMESTAMP NULL,
        UNIQUE KEY uq_learn_prog (user_id, slug),
        INDEX idx_learn_prog_user (user_id)
      )
    `);
    // Durable idempotency for bot trade-event delivery (#1015 follow-up).
    // The in-process guard there covers the retry window -- seconds, per
    // container -- and a restart landing between an attempt and its retry
    // could still double-insert. This makes the database the authority.
    //
    // NULLABLE on purpose: MySQL permits many NULLs in a UNIQUE index, so
    // every pre-existing row keeps working untouched and only rows that
    // carry an id are constrained. An additive column plus an index; no
    // backfill, no rewrite of history.
    try { await pool.execute('ALTER TABLE trades ADD COLUMN event_id VARCHAR(64) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE trades ADD UNIQUE INDEX idx_trades_event_id (event_id)'); } catch (e) { /* present */ }
    // RC-2026-001, the race the application-level check in
    // `app/auth.js` /validate-token cannot close on its own: two concurrent
    // calls can both read "this telegram_id is unclaimed" before either
    // writes. A unique index makes the second one lose at the database.
    // NULLs are unconstrained in a MySQL unique index, so the many accounts
    // that have never linked are untouched.
    //
    // This catch DISTINGUISHES where the ones around it deliberately do not,
    // and the difference matters here. `/* present */` reads every failure as
    // "the index is already there" — which, for an index whose job is to stop
    // an account takeover, would report a security control as installed on
    // exactly the deployment where it could not be. The two outcomes need
    // different words because they need different actions from an operator.
    try {
      await pool.execute(
        'ALTER TABLE users ADD UNIQUE INDEX uniq_users_telegram_id (telegram_id)');
    } catch (e) {
      const msg = (e && e.message) || '';
      if (e && e.code === 'ER_DUP_KEYNAME') {
        /* already installed by an earlier boot */
      } else if (e && e.code === 'ER_DUP_ENTRY') {
        console.error(
          'SECURITY: users.telegram_id holds DUPLICATE values, so the unique index ' +
          'was NOT installed. Two web accounts share one Telegram identity — which ' +
          'is the RC-2026-001 outcome. Find them with: SELECT telegram_id, ' +
          'COUNT(*) c FROM users WHERE telegram_id IS NOT NULL GROUP BY telegram_id ' +
          'HAVING c > 1; resolve, then restart to install the index.');
      } else {
        console.error('users.telegram_id unique index NOT installed:', msg);
      }
    }
    // Provable Calls v2 — the seal rides the position onto the closed trade.
    try { await pool.execute('ALTER TABLE arena_trades ADD COLUMN trade_key VARCHAR(40) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_trades ADD COLUMN seal VARCHAR(64) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_trades ADD COLUMN seal_payload TEXT NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_trades ADD COLUMN sealed_at TIMESTAMP NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_trades ADD INDEX idx_arena_tr_key (trade_key)'); } catch (e) { /* present */ }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS seal_roots (
        day VARCHAR(10) PRIMARY KEY,
        root VARCHAR(64) NOT NULL,
        seal_count INT NOT NULL,
        leaves MEDIUMTEXT NULL,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        anchor_tx VARCHAR(66) NULL,
        anchored_at TIMESTAMP NULL
      )
    `);
    // Anchor columns for pre-existing deployments.
    try { await pool.execute('ALTER TABLE seal_roots ADD COLUMN anchor_tx VARCHAR(66) NULL'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE seal_roots ADD COLUMN anchored_at TIMESTAMP NULL'); } catch (e) { /* present */ }
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_seasons (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(60) NOT NULL,
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NOT NULL,
        announced_live TINYINT NOT NULL DEFAULT 0,
        announced_end TINYINT NOT NULL DEFAULT 0,
        rules TEXT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Sign In With Farcaster: server-issued, SINGLE-USE nonces.
    //
    // In a table rather than in memory because the web app may run more than
    // one replica: an in-process Set would issue a nonce on one and fail to
    // find it on the next, so sign-in would work or not depending on which
    // container answered. `used_at` is what makes replay impossible — the row
    // is not deleted on use, so a replayed nonce is DISTINGUISHABLE from one
    // that never existed, and the logs can tell those apart.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS siwf_nonces (
        nonce VARCHAR(64) PRIMARY KEY,
        created_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL
      )
    `);
    // Farcaster identity, joining google_id / telegram_id / discord_id / x_id
    // on the same find-or-create path (auth.js `_PROVIDER_ID_COLUMN`).
    try {
      await pool.query('ALTER TABLE users ADD COLUMN farcaster_fid VARCHAR(32) DEFAULT NULL');
    } catch (e) { /* column already exists */ }
    try {
      await pool.query('CREATE UNIQUE INDEX idx_users_farcaster_fid ON users (farcaster_fid)');
    } catch (e) { /* index already exists */ }
    // Ceremony flags back-fill for pre-existing deployments.
    try { await pool.execute('ALTER TABLE arena_seasons ADD COLUMN announced_live TINYINT NOT NULL DEFAULT 0'); } catch (e) { /* present */ }
    try { await pool.execute('ALTER TABLE arena_seasons ADD COLUMN announced_end TINYINT NOT NULL DEFAULT 0'); } catch (e) { /* present */ }
    // Season rule variants (JSON, null = open season) back-fill.
    try { await pool.execute('ALTER TABLE arena_seasons ADD COLUMN rules TEXT NULL'); } catch (e) { /* present */ }
    // Practice-follow: mirror engine signals into the PAPER arena account.
    // §4: paper only — this can never route to a live venue.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS arena_follows (
        user_id INT PRIMARY KEY,
        enabled TINYINT NOT NULL DEFAULT 0,
        margin DOUBLE NOT NULL,
        leverage INT NOT NULL,
        last_signal_id BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS user_watchlist (
        user_id INT NOT NULL,
        symbol VARCHAR(30) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, symbol)
      )
    `);
    // Daily Duel — the prediction game. A round is a SYMBOL and the agent's
    // stance on it, shared by everyone that UTC day. The unique (day, idx) key
    // is what makes lazy creation race-safe: concurrent first-readers all
    // INSERT IGNORE and then read back the same three rows.
    //
    // Deliberately no price and no horizon here. Both belong to the pick, so
    // that a player calling late in the day gets their own 24h window instead
    // of a free look at how the day has already gone.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS duel_rounds (
        id INT AUTO_INCREMENT PRIMARY KEY,
        day CHAR(10) NOT NULL,
        idx TINYINT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        agent_direction VARCHAR(5) NULL,
        signal_key VARCHAR(128) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_duel_round (day, idx),
        INDEX idx_duel_rounds_day (day)
      )
    `);
    // One call per player per round, write-once: the unique key is the
    // anti-cheat. A call cannot be revised after the outcome is visible
    // because a second write simply has nowhere to land.
    //
    // settle_price NULL means "not settled yet"; settle_state 'unresolved' is
    // the terminal "we never got a price". Both are absences and both are
    // excluded from accuracy — neither is ever written as a zero.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS duel_picks (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        round_id INT NOT NULL,
        pick VARCHAR(5) NOT NULL,
        entry_price DOUBLE NOT NULL,
        resolves_at TIMESTAMP NOT NULL,
        settle_price DOUBLE NULL,
        settle_state VARCHAR(12) NULL,
        settled_at TIMESTAMP NULL,
        seal VARCHAR(64) NULL,
        seal_payload TEXT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_duel_pick (user_id, round_id),
        INDEX idx_duel_picks_user (user_id),
        INDEX idx_duel_picks_round (round_id),
        INDEX idx_duel_picks_due (settle_state, resolves_at)
      )
    `);
  }
  // In-memory DB needs no migration
}

/**
 * Which store is actually serving: 'mysql' or 'memory'.
 *
 * Until now the only trace of this was one `console.log` at boot, so "am I on
 * the real database?" was a question you answered by finding a startup line in
 * a log — or by noticing your data was missing. A surface that wants to refuse
 * to report a record, or a readiness probe that wants to say so, had nothing
 * to ask.
 *
 * Deliberately a function rather than a captured boolean: exported constants
 * snapshot at require time, which is the same import-time-capture that made
 * `DASHBOARD_KEY` untestable in H7.
 */
function backend() {
  return USE_MYSQL ? 'mysql' : 'memory';
}

/**
 * Run `fn(conn)` inside a database transaction, or throw.
 *
 * WHAT THIS EXISTS FOR. `POST /api/bot/sync` ran
 * `DELETE FROM trades WHERE user_id = ?` under autocommit and then replaced
 * the rows with a loop of individual INSERTs. Any throw in that loop — a
 * malformed row from the bot, a dropped connection, a deadlock — left the
 * DELETE committed and the account's entire trade history gone, permanently,
 * behind a response that said only "Sync failed". On a Restart=always bot
 * syncing on a schedule, one persistently bad row would destroy the history
 * on every attempt and never restore it.
 *
 * `beginTransaction` appeared NOWHERE in app/ before this: not one route was
 * atomic. That made it a systemic absence rather than one handler's oversight.
 *
 * It THROWS on a backend that cannot do transactions rather than quietly
 * running `fn` without one. A silent fallback would mean the caller believes
 * its writes are atomic when they are not, which is the failure this helper
 * exists to remove, reintroduced one level up. Both supported backends
 * implement `getConnection`, so the throw is a guard against a future third,
 * not a live path.
 */
async function withTransaction(fn) {
  if (!pool || typeof pool.getConnection !== 'function') {
    throw new Error('withTransaction: this database backend has no '
      + 'getConnection — refusing to run a non-atomic write as if it were atomic');
  }
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    const out = await fn(conn);
    await conn.commit();
    return out;
  } catch (err) {
    // A rollback that itself fails must not mask the original error: that is
    // the one the caller needs, and the rollback failure is the lesser fact.
    try { await conn.rollback(); } catch (rbErr) {
      console.error('Rollback failed after:', err && err.message, '->', rbErr && rbErr.message);
    }
    throw err;
  } finally {
    try { conn.release(); } catch (_) { /* pool already gone */ }
  }
}

module.exports = {
  pool, migrate, lastStatement, describeSql, backend, withTransaction,
  EXPECTED_TABLES, schemaIsCurrent,
  // Exported for tests. The URL it normalises is the one thing in this process
  // that cannot be exercised end-to-end without a live database, so the string
  // handling is tested directly instead of being trusted.
  poolConfigFrom,
};
