/**
 * RUNECLAW MCP server — the agent-hub interface.
 *
 * A minimal, dependency-free Model Context Protocol server (Streamable HTTP,
 * stateless JSON responses) that lets ANY MCP-capable agent — Claude, agent
 * frameworks, other bots — consume RUNECLAW's intelligence as tools.
 *
 * Scope is deliberate. Every tool is READ-ONLY and falls in one of two
 * families:
 *   - intelligence — serves data this site already publishes without auth
 *     (public track record, signal stream, agent feed, RWA radar, DEX
 *     comparison, showcase trade, what-if replay over the public history, the
 *     weekly letter derived from that same public data);
 *   - Guardian safety — evaluates input the CALLER supplies (marked
 *     `computesOnInput: true`), storing nothing and reading no account.
 * No tool can see a user's account, and no tool can act — trade-capable MCP
 * tools are a separate, gated decision for the operator.
 *
 * Protocol: JSON-RPC 2.0 over POST /mcp (MCP Streamable HTTP transport,
 * stateless mode — plain JSON responses, no SSE stream, no sessions).
 * GET/DELETE answer 405, per spec for servers that don't offer a stream.
 */

const express = require('express');
const { pool } = require('../db');
const { getLatestFlight } = require('./sync');
// The public /track page's own arithmetic. Imported rather than re-derived:
// M9 was these two surfaces answering the same question differently while a
// comment here promised they shared one source of truth.
const { classifyPnls, outcomeOf } = require('./track');
const { sanitizeRecord } = require('../lib/flight');
const { getGateway, isConfigured: gatewayConfigured } = require('../lib/gateway');
// The Guardian safety models. Pure functions of caller-supplied input — they
// touch no account, read no database, move nothing, and are the same code the
// browser pages run, so a tool answer and the website always agree.
const firewall = require('../public/js/firewall-model.js');
const intent = require('../public/js/intent-model.js');
const escape = require('../public/js/escape-model.js');
const stress = require('../public/js/stress-model.js');

const router = express.Router();

const PROTOCOL_VERSION = '2025-03-26';
const SERVER_INFO = { name: 'runeclaw', version: '2.0.0' };

// Per-IP limiter: MCP is public surface. Uses the shared limiter (periodic
// idle-bucket pruning) — the earlier hand-rolled map never expired entries
// and, once full, evicted the OLDEST-INSERTED key, which could reset an
// actively-limited IP's counter under bucket churn.
const { rateLimit, ipKey } = require('../lib/rate_limit');
router.use(rateLimit({ windowMs: 60_000, max: 60, key: ipKey, message: 'rate_limited' }));

// The global express.json() (1 MB) has already parsed the body by the time
// the per-route 64 KB parser runs, making that cap a no-op. Enforce the
// intended MCP payload bound explicitly.
const MAX_MCP_BODY_BYTES = 64 * 1024;
router.use((req, res, next) => {
  const len = parseInt(req.headers['content-length'] || '0', 10);
  if (len > MAX_MCP_BODY_BYTES) {
    return res.status(413).json({ error: 'Payload too large (64 KB max)' });
  }
  next();
});

// ── Tool registry ────────────────────────────────────────────────────────────
// Each tool: { description, inputSchema, handler(args) -> JSON-serializable }.
// Handlers reuse the exact libraries behind the public site — no new data
// paths, no new exposure.

const TOOLS = {
  // ── Guardian: the safety layer, callable by ANY agent ──────────────────
  // Every tool below this block answers "what has RUNECLAW done?". These four
  // answer "is what YOUR agent is about to do safe?" — the same models the
  // Guardian pages run, on input the caller supplies. They still act on
  // nothing: no account is read, no funds move, no signature is produced.
  // Every result is a heuristic read with reasons, never a verdict.
  scan_transaction: {
    // Evaluates caller-supplied input rather than serving data the public
    // site publishes. The ERC-8257 manifest derives its tool-family split
    // from this marker, so the on-chain record cannot claim otherwise.
    computesOnInput: true,
    description: 'Pre-signature safety scan for an autonomous agent. Paste '
      + 'anything the agent is about to act on — a message, a token name or '
      + 'metadata, a URL, an address, a signing request — and get back flagged '
      + 'attack patterns with reasons: prompt-injection instructions, '
      + 'seed-phrase lures, drain and unlimited-approval language, hidden or '
      + 'look-alike characters, phishing URLs and address poisoning. Runs '
      + 'locally on the text you send; nothing is stored. A clean result is '
      + 'NOT a guarantee and a flag is not a verdict — always verify the '
      + 'destination address, amount and approval scope yourself.',
    inputSchema: {
      type: 'object',
      properties: { text: { type: 'string', description: 'The message, metadata, URL, address or signing request to scan.' } },
      required: ['text'],
      additionalProperties: false,
    },
    handler: async ({ text }) => {
      const t = String(text == null ? '' : text).slice(0, 20000);
      if (!t.trim()) throw new Error('text is required');
      const r = firewall.scanText(t);
      return {
        level: r.level, score: r.score, flags: r.flags,
        heuristic: true,
        note: 'Heuristic pre-sign scan. A clean result is not a guarantee and '
          + 'a flag is not a verdict. Nothing was stored.',
      };
    },
  },
  xray_transaction: {
    // Evaluates caller-supplied input rather than serving data the public
    // site publishes. The ERC-8257 manifest derives its tool-family split
    // from this marker, so the on-chain record cannot claim otherwise.
    computesOnInput: true,
    description: 'Decode what a transaction actually DOES before signing it. '
      + 'Send calldata (plus optional to/value) and get back the exact '
      + 'decoded actions for the known selector set — approve (with the '
      + 'unlimited line at 2^128 raw units), increaseAllowance, transfer, '
      + 'transferFrom, EIP-2612 permit (an approval moved by signature), '
      + 'setApprovalForAll (the classic drain primitive), ERC-721/1155 '
      + 'safeTransferFrom, and multicall batches unwrapped call by call — '
      + 'with heuristic flags, never verdicts. Amounts are RAW token units '
      + '(decimals are a chain read this tool deliberately does not do). '
      + 'Anything outside the known set answers UNKNOWN — unknown is not the '
      + 'same as safe. Pure decode: nothing sent here is stored, no chain is '
      + 'read, no account is seen.',
    inputSchema: {
      type: 'object',
      properties: {
        data: { type: 'string', maxLength: 100000, description: 'The transaction calldata, 0x-hex.' },
        to: { type: 'string', description: 'Optional destination address (echoed for context only).' },
        value: { type: 'string', description: 'Optional native value in wei (decimal or 0x-hex string).' },
      },
      required: ['data'],
      additionalProperties: false,
    },
    handler: async ({ data, to, value }) => {
      const r = require('../public/js/txray-model.js')
        .decodeTx({ data: String(data || ''), value: value == null ? '0' : String(value) });
      return { ...(to ? { to: String(to).slice(0, 64) } : {}), ...r,
        note: 'Heuristic decode of the known selector set. A flag is not a '
          + 'verdict and unknown is not safe. Nothing sent here is stored.' };
    },
  },
  compile_intent: {
    // Evaluates caller-supplied input rather than serving data the public
    // site publishes. The ERC-8257 manifest derives its tool-family split
    // from this marker, so the on-chain record cannot claim otherwise.
    computesOnInput: true,
    description: 'Turn a plain-language mandate into a deterministic, '
      + 'revocable Authority Envelope: typed rules, each tagged with WHO '
      + 'enforces it (the wallet, a risk gate, or a human approval). Example '
      + 'input: "only majors, max 5% per trade, no shorts, stop if down 8%". '
      + 'This is a compiler preview — it binds nothing, signs nothing and '
      + 'moves no funds. Percent and ratio only; it never emits a dollar '
      + 'figure.',
    inputSchema: {
      type: 'object',
      properties: { mandate: { type: 'string', description: 'The limits, in plain words.' } },
      required: ['mandate'],
      additionalProperties: false,
    },
    handler: async ({ mandate }) => {
      const m = String(mandate == null ? '' : mandate).slice(0, 4000);
      if (!m.trim()) throw new Error('mandate is required');
      const c = intent.compile(m);
      return { ...c, binds_nothing: true,
        note: 'Compiler preview. Nothing is bound, signed or moved; a real '
          + 'envelope is tighten-only and revocable at any time.' };
    },
  },
  stress_portfolio: {
    // Evaluates caller-supplied input rather than serving data the public
    // site publishes. The ERC-8257 manifest derives its tool-family split
    // from this marker, so the on-chain record cannot claim otherwise.
    computesOnInput: true,
    description: 'Run a hypothetical book through the same scenarios the '
      + 'Portfolio Stress Lab uses — a majors −30% drop, an alt crash, a '
      + 'stablecoin depeg, a liquidation cascade and a black swan — and get '
      + 'back the drawdown, which leveraged legs liquidate, and what breaks '
      + 'first. Percent-only on a book you supply: never a real balance, '
      + 'never a dollar figure, and not a prediction.',
    inputSchema: {
      type: 'object',
      properties: {
        book: {
          type: 'array',
          description: 'Positions as percent weights of the book.',
          items: {
            type: 'object',
            properties: {
              asset: { type: 'string' },
              weight: { type: 'number', description: 'Percent of the book.' },
              leverage: { type: 'number', description: 'Multiplier, 1 = spot.' },
              dir: { type: 'string', description: 'long or short; defaults to long.' },
            },
            required: ['asset', 'weight'],
          },
        },
      },
      required: ['book'],
      additionalProperties: false,
    },
    handler: async ({ book }) => {
      if (!Array.isArray(book) || !book.length) throw new Error('book must be a non-empty array');
      if (book.length > 60) throw new Error('book is capped at 60 positions');
      const clean = book.map((p) => ({
        asset: String(p.asset || '').slice(0, 24),
        weight: Number(p.weight) || 0,
        leverage: Number(p.leverage) || 1,
        dir: p.dir === 'short' ? 'short' : 'long',
      }));
      return {
        scenarios: stress.runAll(clean),
        percent_only: true,
        note: 'Deterministic simulation of a HYPOTHETICAL book. Not a '
          + 'prediction, not a real account, and not investment advice — real '
          + 'liquidations depend on your venue\'s maintenance margin, funding '
          + 'and slippage.',
      };
    },
  },
  plan_escape: {
    // Evaluates caller-supplied input rather than serving data the public
    // site publishes. The ERC-8257 manifest derives its tool-family split
    // from this marker, so the on-chain record cannot claim otherwise.
    computesOnInput: true,
    description: 'Sequence a dependency-aware emergency exit for a complex '
      + 'book: close leverage before it liquidates, repay debt to unlock '
      + 'collateral, exit LPs and staking to reclaim the underlying, then '
      + 'convert and bridge home — flagging what is locked and cannot be '
      + 'exited yet. Planning only: it never executes, places or signs '
      + 'anything.',
    inputSchema: {
      type: 'object',
      properties: {
        positions: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              type: { type: 'string', enum: ['perp', 'borrow', 'lp', 'staked', 'collateral', 'spot', 'bridged'],
                description: 'One of the supported position kinds. An unrecognised kind is treated as spot, which would silently reorder the plan — so this is enumerated rather than free text.' },
              asset: { type: 'string' },
              where: { type: 'string', description: 'Venue or chain.' },
              size: { type: 'number', description: 'Percent of the book.' },
            },
            required: ['type', 'asset'],
          },
        },
      },
      required: ['positions'],
      additionalProperties: false,
    },
    handler: async ({ positions }) => {
      if (!Array.isArray(positions) || !positions.length) throw new Error('positions must be a non-empty array');
      if (positions.length > 60) throw new Error('positions is capped at 60 entries');
      const VALID = new Set(escape.TYPES);
      const bad = positions.map((p) => String(p.type || '')).filter((t) => !VALID.has(t));
      if (bad.length) {
        throw new Error(`unsupported position type(s): ${[...new Set(bad)].join(', ')}. `
          + `Supported: ${escape.TYPES.join(', ')}`);
      }
      const clean = positions.map((p) => ({
        type: String(p.type).slice(0, 24),
        asset: String(p.asset || '').slice(0, 24),
        where: String(p.where || '').slice(0, 40),
        size: Number(p.size) || 0,
      }));
      const plan = escape.buildPlan(clean);
      return { ...plan, executes_nothing: true,
        note: 'A plan, not an action. It moves no funds, places no orders and '
          + 'signs nothing — you execute each step yourself. Cooldowns, exit '
          + 'fees, slippage, gas and bridge risk are real and not modelled.' };
    },
  },

  // The sixth Guardian module. Unlike the four above it reads no caller input —
  // it is a market-wide read of PUBLIC venue data, the same payload
  // /api/market/sentinel already serves, so it belongs to the published-data
  // family and adds no new exposure.
  verify_call: {
    description: 'Verify a sealed RUNECLAW call by its public id (a signal_key '
      + 'or an arena:… trade key). Returns the decision-time seal and its '
      + 'payload, a server-side recomputation check (seal_matches_payload), '
      + 'the Merkle proof into the day\u2019s published root, and — when the '
      + 'operator has anchored that day on Base — the anchor transaction whose '
      + 'block time bounds the seal. Everything needed to re-verify '
      + 'independently rides in the response: sha256 the payload yourself, '
      + 'replay the proof, and compare the anchor calldata on Basescan. Trust '
      + 'nothing here you can recompute.',
    inputSchema: {
      type: 'object',
      properties: { key: { type: 'string', minLength: 4, maxLength: 128,
        description: 'signal_key or arena:… trade key from any public feed' } },
      required: ['key'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const { lookupCall } = require('./call');
      const r = await lookupCall(String((args && args.key) || ''));
      if (r.code !== 200) return { found: false, error: r.body.error };
      const crypto = require('crypto');
      // The one check the server can honestly perform FOR the caller — and
      // the caller can (and should) repeat it from the same two fields.
      const recomputed = crypto.createHash('sha256')
        .update(String(r.body.seal_payload || ''), 'utf8').digest('hex');
      return { found: true, ...r.body,
        seal_matches_payload: recomputed === String(r.body.seal),
        verify_yourself: 'seal = sha256(utf8(seal_payload)). Merkle: parent = '
          + 'sha256(utf8(leftHex+rightHex)), leaves deduped + sorted asc. '
          + 'Anchor calldata = hex(utf8("RCROOT1:<day>:<root>")) on Base.' };
    },
  },

  get_seal_roots: {
    description: 'The daily seal roots feed: one Merkle root per completed UTC '
      + 'day, committing to every seal minted that day (engine calls + arena '
      + 'paper trades), with the Base anchor transaction where the operator '
      + 'has anchored the day. Mirror a root anywhere and no call can be '
      + 'back-inserted into that day without changing it; an anchored day\u2019s '
      + 'block time bounds every seal in it with a fact no one at RUNECLAW '
      + 'controls. Days with zero seals are omitted, never invented.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const { listRoots } = require('../lib/seal_roots');
      const roots = await listRoots(30);
      return {
        construction: 'leaves = day seals (64-hex) deduped + sorted asc; '
          + 'parent = sha256(utf8(leftHex+rightHex)); odd node promoted; '
          + 'single leaf = root',
        anchor_payload_format: 'hex(utf8("RCROOT1:<day>:<root>")) as calldata '
          + 'of a zero-value Base transaction',
        roots,
      };
    },
  },

  get_gas: {
    description: 'Live gas across the EVM chains RUNECLAW mirrors (Ethereum, '
      + 'Base, Arbitrum, Optimism, BNB Chain, Avalanche, Polygon): current gas price in '
      + 'gwei per chain, the live native-coin price, and xfer_cost_usd — a '
      + 'FLOOR for one bridge-ish transaction (gas × 100k gas units × native '
      + 'price; bridge fees, relayers, L1 data fees and destination gas all '
      + 'come on top). Public market facts read over keyless public RPCs, '
      + 'indicative only — the node’s current suggestion, not a quote for '
      + 'any transaction. A chain that could not be read is omitted, never '
      + 'invented; a chain without a fresh native mark carries gwei but no '
      + 'cost fields. Cached ~60s.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const body = await require('../lib/gas_read').readGasCached();
      return { ...body,
        note: 'Indicative market facts, not advice and not a quote. '
          + 'xfer_cost_usd is a floor that can understate the true cost of '
          + 'moving an asset, never overstate it.' };
    },
  },

  get_systemic_risk: {
    description: 'Systemic Risk Sentinel: a market-wide crowding and herding '
      + 'read over the whole USDT-perp universe from public venue data — where '
      + 'positioning is one-sided, where funding is hot, and where open '
      + 'interest surged since the last poll, which together are the setup for '
      + 'a liquidation cascade. Use it to ask whether the trade your agent '
      + 'wants is the trade everyone else is already in. Heuristic flags with '
      + 'reasons, never a verdict, and never a forecast: crowding says nothing '
      + 'about direction or timing.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const s = await require('../lib/sentinel_live').getSentinel();
      return { ...s, heuristic: true,
        note: 'Public market facts (tickers, open interest, funding) read for '
          + 'crowding. Heuristic flags with reasons, never a verdict and never '
          + 'a forecast — a crowded book can stay crowded.' };
    },
  },

  get_track_record: {
    // §4: this endpoint is PUBLIC (mounted with rate limiting only — the
    // router.use above it is a body-size cap, not auth), so it carries
    // percent / ratio / count and no dollar amounts. It used to return
    // `net_pnl_usd` and a per-trade `pnl` in dollars while its own `source`
    // claimed "same data as the public /track page" — /track publishes equity
    // INDEXED TO 100 precisely to avoid this, so the tool was strictly more
    // revealing than the page it said it mirrored.
    //
    // The query has no margin or notional column, so there is no honest
    // denominator for a per-trade return percentage. Rather than invent one,
    // each recent trade reports its OUTCOME — a category, which §4 permits
    // and which is what a track record is actually read for. Omit, never
    // invent.
    //
    // The description also promised drawdown, monthly PnL and an equity curve
    // that this handler has never returned. Corrected to what it emits.
    description: "RUNECLAW's public verifiable track record: closed-trade "
      + 'stats (win rate, profit factor) and recent trade outcomes — all from '
      + 'recorded history, nothing hand-entered. Percent/ratio/count only: no '
      + 'dollar amounts are published.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      // Reuse the public route's aggregation by querying the same tables it
      // does — via an internal fetch to keep one source of truth.
      const [trades] = await pool.execute(
        `SELECT symbol, direction, pnl, fees, opened_at, closed_at
           FROM trades WHERE user_id = ? AND status = 'CLOSED'
            AND closed_at IS NOT NULL ORDER BY closed_at ASC`,
        [parseInt(process.env.BOT_USER_ID) || 1]);
      // The comment above claimed one source of truth; the arithmetic below
      // was its own. `parseFloat(t.pnl) || 0` counted every unpriced close as
      // a measured break-even, and `trades.length` as the win-rate denominator
      // then dragged the rate down with rows nobody had scored — so the machine
      // -readable record understated the number the page it mirrors published.
      // The query filters status='CLOSED' AND closed_at IS NOT NULL but NOT
      // `pnl IS NOT NULL`, and trades.pnl is DECIMAL(14,2) NULLABLE, so such a
      // row is reachable. Now it uses the page's own helper.
      const { priced, unpriced, wins, losses } = classifyPnls(trades.map(t => t.pnl));
      const grossWin = wins.reduce((a, b) => a + b, 0);
      const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
      return {
        trades: trades.length,
        wins: wins.length,
        // Published so the figures reconcile. A win rate over a denominator the
        // reader cannot see is not checkable.
        unpriced,
        win_rate_pct: priced.length
          ? Math.round(wins.length / priced.length * 10000) / 100 : null,
        // profit_factor is gross-win / gross-loss — a RATIO, so it carries the
        // performance signal net_pnl_usd used to, without the dollar figure.
        profit_factor: grossLoss > 0 ? Math.round(grossWin / grossLoss * 100) / 100 : null,
        recent_trades: trades.slice(-10).reverse().map(t => ({
          symbol: t.symbol, direction: t.direction,
          // Outcome, not amount — and four of them. `flat` is its own answer
          // rather than being folded into a loss, and `unknown` is its own
          // answer rather than being folded into `flat`: a scratch is not a
          // losing trade, and a close nobody priced is not a scratch.
          result: outcomeOf(t.pnl),
          closed_at: t.closed_at,
        })),
        source: 'recorded closed trades (same data as the public /track page, '
          + 'which is likewise published without dollar amounts)',
      };
    },
  },

  get_proof_of_pnl: {
    description: "RUNECLAW's continuously-published Proof-of-PnL: the sealed, "
      + 'public-safe track-record statement with a SHA-256 publish_hash over the '
      + 'canonical bundle, its freshness, the trust tier and reconciliation '
      + 'status, and the ERC-8004 identity anchor (honestly UNVERIFIED until a '
      + 'real tx confirms it). Machine counterpart of the /proof page: an agent '
      + 'can re-derive the hash itself instead of trusting this response. '
      + 'Re-derive: canonical = JSON with recursively sorted keys, no whitespace, '
      + 'UTF-8 (every number already a string); publish_hash = SHA-256(canonical).',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      if (!gatewayConfigured()) return { published: false, error: 'not_configured' };
      const r = await getGateway('/public/proofofpnl', 15000);
      if (r.status < 200 || r.status >= 300) return { published: false, error: 'unavailable' };
      const d = r.data || {};
      // Pass the sealed statement through verbatim so the caller verifies the
      // SAME bytes we did — plus a machine-readable re-derivation recipe.
      return {
        ...d,
        reverify: {
          canonicalization: 'json.dumps(bundle, sort_keys=True, separators=(",",":"), ensure_ascii=False)',
          hash: 'sha256(utf8(canonical))',
          note: 'All numbers in the bundle are strings, so a recursive key-sort '
            + '+ JSON.stringify reproduces the canonical bytes in any language.',
        },
      };
    },
  },

  get_signals: {
    description: 'The most recent trade signals the engine generated — taken '
      + 'or not — with direction, confidence, pattern, entry/stop/target and '
      + 'resolved outcome where known.',
    inputSchema: {
      type: 'object',
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      const [rows] = await pool.execute(
        `SELECT signal_key, symbol, direction, confidence, pattern, regime,
                entry_price, stop_loss, take_profit, rr, status, pnl, created_at
           FROM signals ORDER BY created_at DESC LIMIT ${limit}`, []);
      return { signals: rows };
    },
  },

  get_flight_record: {
    description: 'Guardian Flight Recorder: the tamper-evident ledger of the '
      + "agent's recent trading decisions. Each record carries full provenance — "
      + 'the reasoning, ranked voter contributions, LLM model/prompt version, the '
      + 'risk-gate verdict, and the realised outcome (PnL/close) — plus the '
      + 'engine-verified SHA-256 hash-chain status proving the log is unaltered.',
    inputSchema: {
      type: 'object',
      properties: {
        limit: { type: 'integer', minimum: 1, maximum: 50 },
        decision_id: { type: 'string', description: 'Return only this decision' },
      },
      additionalProperties: false,
    },
    // H2: both return paths run through sanitizeRecord.
    //
    // They returned `flight.records` verbatim — the objects POST
    // /api/bot/sync/flight stores unmodified and getLatestFlight hands back raw
    // — and flight_recorder.py puts `size_usd` and `pnl_usd` on every one. /mcp
    // is mounted with no auth (server.js:349), so any anonymous caller read the
    // operator's live per-decision position sizes and realized dollar P&L, from
    // which account scale follows.
    //
    // The scrubber this needed already existed and already had a caller:
    // public_flight.js does `records.map(sanitizeRecord)` for exactly this data,
    // and lib/flight.js says in its header that the dollar-carrying record is
    // reserved for the authed view. The rule was written, the tool was written,
    // one of two public surfaces used it.
    handler: async (args) => {
      const flight = await getLatestFlight();
      const all = (flight && Array.isArray(flight.records)) ? flight.records : [];
      const chain = (flight && flight.chain) || {};
      if (args && args.decision_id) {
        const rec = all.find((r) => r && r.decision_id === args.decision_id);
        return { record: rec ? sanitizeRecord(rec) : null, chain };
      }
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      return {
        chain: {
          verified: chain.ok !== false,
          entries: chain.length ?? null,
          tip_hash: chain.tip_hash ?? null,
        },
        records: all.slice(0, limit).map(sanitizeRecord),
        source: 'engine-side hash-chained audit ledger (logs/audit_chain.jsonl)',
        disclosure: 'Percent, ratio and R-multiple only — no dollar amounts. '
          + 'The full record is reserved for the authenticated operator view.',
      };
    },
  },

  get_agent_feed: {
    description: "The agent's public mind-stream: recent scans, trade theses, "
      + 'opens/closes, stop moves and alerts, as emitted live by the engine.',
    inputSchema: {
      type: 'object',
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      const [rows] = await pool.execute(
        `SELECT event_type, severity, symbol, title, body, created_at
           FROM agent_events ORDER BY id DESC LIMIT ${limit}`, []);
      return { events: rows };
    },
  },

  get_rwa_radar: {
    description: 'Read-only tokenized real-world-asset sector radar from live '
      + 'venue tickers: RWA platforms, RWA-narrative chains and RWA-adjacent '
      + 'DeFi with volume-weighted 24h change and sector-vs-BTC read.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/rwa').getRadar(),
  },

  get_meme_radar: {
    description: 'Read-only meme & AI-agent token radar from live DEXScreener '
      + 'DEX pairs: trending on-chain tokens ranked by real 24h volume, each '
      + 'with an explicit SAFETY read (liquidity depth, pair age, buy/sell '
      + 'balance, risk tier). Intelligence only — never trades, never launches '
      + 'tokens. Memecoins are extremely high risk; most go to zero.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/meme').getRadar(),
  },

  get_dex_compare: {
    description: 'DEX↔CEX basis: live Hyperliquid mid prices for the majors '
      + "against this venue's perpetual prices, in bps. Read-only public data.",
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/dex').getDexCompare(),
  },

  get_showcase_trade: {
    description: 'One real recorded trade (the largest |PnL| close of the '
      + 'last 14 days, win or loss) — the same pick the landing page animates. '
      + 'Null when there is nothing real to show.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      // Same pick logic as /api/public/replay-trade, via its module cache.
      const [rows] = await pool.execute(
        `SELECT symbol, direction, entry_price, exit_price, size_usd, pnl,
                opened_at, closed_at
           FROM trades WHERE user_id = ? AND status = 'CLOSED'
            AND closed_at IS NOT NULL ORDER BY closed_at ASC`,
        [parseInt(process.env.BOT_USER_ID) || 1]);
      const usable = rows.filter(t => isFinite(parseFloat(t.pnl)));
      if (!usable.length) return { trade: null };
      const cutoff = Date.now() - 14 * 86_400_000;
      const recent = usable.filter(t => new Date(t.closed_at).getTime() >= cutoff);
      const src = recent.length ? recent : [usable[usable.length - 1]];
      const pick = src.reduce((a, b) =>
        Math.abs(parseFloat(b.pnl)) > Math.abs(parseFloat(a.pnl)) ? b : a);
      // §4: prices are public market facts; the SIZE and the dollar PnL are
      // not. The route this tool mirrors converts to pnl_pct under exactly
      // that comment — this one returned the raw row, so size_usd and pnl
      // went out on the unauthenticated surface. Selecting the largest
      // absolute pnl still uses the dollar figure; it just never leaves.
      const _size = parseFloat(pick.size_usd);
      const _pnl = parseFloat(pick.pnl);
      return { trade: {
        symbol: pick.symbol,
        direction: pick.direction,
        entry_price: pick.entry_price,
        exit_price: pick.exit_price,
        // Absent, not zero: a missing size has no honest percentage.
        pnl_pct: (isFinite(_size) && _size > 0 && isFinite(_pnl))
          ? Math.round((_pnl / _size) * 10000) / 100 : null,
        result: _pnl > 0 ? 'win' : _pnl < 0 ? 'loss' : 'flat',
        opened_at: pick.opened_at,
        closed_at: pick.closed_at,
      } };
    },
  },

  run_what_if: {
    description: 'Hypothetical replay: what if every recorded agent trade had '
      + 'been mirrored with a fixed stake? Real recorded entries/exits/fees, '
      + 'scaled — never simulated. Always labelled hypothetical.',
    inputSchema: {
      type: 'object',
      properties: {
        stake_usd: { type: 'number', minimum: 10, maximum: 1000000 },
        days: { type: 'integer', minimum: 0, maximum: 3650 },
        symbol: { type: 'string', maxLength: 12 },
      },
      additionalProperties: false,
    },
    handler: async (args) => ({
      hypothetical: true,
      ...(await require('../lib/replay').runReplay({
        stake: args?.stake_usd || 1000,
        days: args?.days || 0,
        symbol: args?.symbol || '',
      })),
    }),
  },

  get_weekly_letter: {
    description: "The Agent Letter — the weekly fund-style letter composed "
      + 'entirely from recorded data (trades, equity, signal flow). Latest '
      + 'completed ISO week; generated on first request.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const letters = require('../lib/letter');
      // §4: /mcp is UNAUTHENTICATED (the router.use above the tool registry
      // is a 64 KB body cap, not auth). getLetter composes the PRIVATE
      // letter — equity start->end and "Net PnL $…". composePublicLetter
      // exists for exactly this surface and the verify_call tool one block
      // down already uses getPublicLetter; this one reached for the private
      // composer instead.
      const week = letters.lastCompletedWeek();
      return await letters.getPublicLetter(week.key || week);
    },
  },

  // ── v2 tools ──────────────────────────────────────────────────────────────

  research_token: {
    description: 'Evidence dossier for a listed coin — live market read, '
      + 'sector membership, DEX presence, the deterministic SAFETY read '
      + '(heuristic red flags, never a verdict), engine signal history and '
      + "the agent's own recorded track record on the coin. Composed only "
      + 'from trusted live sources and recorded platform history; a coin the '
      + 'venue does not list returns listed:false (nothing to research '
      + 'honestly).',
    inputSchema: {
      type: 'object',
      properties: { symbol: { type: 'string', maxLength: 12 } },
      required: ['symbol'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const base = String(args.symbol || '').toUpperCase()
        .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
      if (!base) return { listed: false, error: 'symbol required' };
      const d = await require('../lib/research').buildDossier(base);
      return d ? { listed: true, ...d }
        : { listed: false, note: 'Not listed on the venue — no trusted live data.' };
    },
  },

  scan_token_safety: {
    description: 'Deterministic token safety heuristics for a coin: thin '
      + 'venue volume, extreme/parabolic 24h moves, on-chain liquidity depth, '
      + 'pair age, honeypot pattern (buys but no sells), one-sided flow, and '
      + 'CEX↔DEX price gap — tiered standard/elevated/high/extreme. Flags '
      + 'are heuristics with reasons, NEVER a verdict: "no flags" means the '
      + 'checks found nothing, not that the token is safe.',
    inputSchema: {
      type: 'object',
      properties: { symbol: { type: 'string', maxLength: 12 } },
      required: ['symbol'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const base = String(args.symbol || '').toUpperCase()
        .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
      if (!base) return { error: 'symbol required' };
      let ticker = null;
      try { ticker = (await require('../lib/tickers').getTickers())[`${base}USDT`] || null; }
      catch (e) { /* CEX side degrades; on-chain checks still run */ }
      return require('../lib/token_safety').scanToken(base, { ticker });
    },
  },

  get_leaderboard: {
    description: 'The public verifiable leaderboard: anonymous handles ranked '
      + 'by re-verified sealed statements (win rate, profit factor, round '
      + 'trips — never account sizes or dollar amounts). Optional season '
      + '"YYYY-MM" returns that frozen monthly board.',
    inputSchema: {
      type: 'object',
      properties: { season: { type: 'string', maxLength: 7 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const season = String(args?.season || '');
      if (season && !/^\d{4}-\d{2}$/.test(season)) return { error: 'season must be YYYY-MM' };
      if (!gatewayConfigured()) return { available: false, error: 'not_configured' };
      const r = await getGateway(`/public/leaderboard${season ? `?season=${season}` : ''}`, 15000);
      if (r.status < 200 || r.status >= 300) return { available: false, error: 'unavailable' };
      return r.data;
    },
  },

  get_agent_card: {
    description: 'The ERC-8004 identity card behind a published agent '
      + 'address: identity, sealed track-record linkage, server-side '
      + 'verification result (re-derived hash + Ed25519), trust tier and '
      + 'reconciliation status. The anchor stays honestly UNVERIFIED until a '
      + 'real on-chain transaction confirms it. Unknown addresses return '
      + 'found:false.',
    inputSchema: {
      type: 'object',
      properties: { address: { type: 'string', maxLength: 42 } },
      required: ['address'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const addr = String(args.address || '').toLowerCase();
      if (!/^0x[0-9a-f]{40}$/.test(addr)) return { error: 'address must be 0x + 40 hex chars' };
      if (!gatewayConfigured()) return { found: false, error: 'not_configured' };
      const r = await getGateway(`/public/agent/${addr}`, 15000);
      if (r.status === 404) return { found: false };
      if (r.status < 200 || r.status >= 300) return { found: false, error: 'unavailable' };
      return { found: true, ...r.data };
    },
  },

  get_arena_trader: {
    description: 'One public paper-arena trader record by opt-in handle: '
      + 'settled percent return, close/seal counts, streak, badges and the '
      + 'recent closes with their receipt keys — the same §4-safe card the '
      + 'website serves (percent and counts only, never an account amount, '
      + 'not even a virtual one). Unknown handles return found:false — '
      + 'handles are opt-in and absence is not a verdict.',
    inputSchema: {
      type: 'object',
      properties: { handle: { type: 'string', minLength: 3, maxLength: 20,
        description: 'an opt-in leaderboard handle from the public board' } },
      required: ['handle'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const { fetchTraderCard, HANDLE_RE } = require('../lib/arena_trader');
      const handle = String((args && args.handle) || '').trim();
      if (!HANDLE_RE.test(handle)) return { found: false, error: 'handle must be 3-20 word characters' };
      const card = await fetchTraderCard(handle);
      if (!card) return { found: false, note: 'handles are opt-in; absence is not a verdict' };
      return { found: true, ...card };
    },
  },

  get_paper_leaderboard: {
    description: 'The paper-trading arena leaderboard: anonymous opt-in '
      + 'handles ranked by percent return on the same virtual stake, with '
      + 'close counts and how many closes carry verifiable open-time '
      + 'receipts. Percent and counts only — never balances, never dollar '
      + 'amounts (not even virtual ones). The exact payload the public '
      + 'website serves, from the same computation.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const { computeLeaderboard } = require('./arena');
      return computeLeaderboard();
    },
  },

  get_rune_stats: {
    description: 'The soulbound Rune of Entry collection state, read live '
      + 'from Base: deployed or not, the contract address when one exists, '
      + 'and the minted count — null when the chain would not answer '
      + '(unknown is never reported as zero). The rune is free (gas only), '
      + 'one per wallet, non-transferable forever, and explicitly not an '
      + 'investment.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const nft = require('../lib/nft');
      const stats = await nft.readStats();
      return { ...stats,
        honesty: stats.deployed && stats.minted_count == null
          ? 'the chain did not answer — the count is unknown, not zero' : undefined };
    },
  },

  get_public_letter: {
    description: 'The PUBLIC edition of the weekly Agent Letter — the same '
      + 'recorded data recomposed with no dollar figure (counts, win rate, '
      + 'profit factor, equity percent change, alpha vs holding, regime '
      + 'reads). Optional week "YYYY-Wnn"; defaults to the latest completed '
      + 'ISO week. Only completed weeks exist.',
    inputSchema: {
      type: 'object',
      properties: { week: { type: 'string', maxLength: 8 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const letters = require('../lib/letter');
      const week = String(args?.week || '') || letters.lastCompletedWeek().key;
      if (!/^\d{4}-W\d{2}$/.test(week)) return { error: 'week must be YYYY-Wnn' };
      const letter = await letters.getPublicLetter(week);
      return letter || { found: false, week };
    },
  },

  get_airdrop_radar: {
    description: 'Curated airdrop & testnet campaign radar with guided '
      + 'checklists — status, cost, effort, requirements and official links. '
      + 'GUIDED-ONLY by design: the human performs and signs every step; '
      + 'RUNECLAW never automates participation, never farms with multiple '
      + 'wallets (sybil activity gets retroactively disqualified anyway). '
      + 'Campaigns churn — verify on the official link before acting.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/airdrops').getPublicAirdropRadar(),
  },

  get_alpha_intel: {
    description: "Derived analytics over the agent's public recorded closed "
      + 'trades: alpha vs simply holding each traded asset (rebuilt from each '
      + "trade's own entry/exit prices — no external price history, fully "
      + 're-derivable), expectancy, payoff ratio, profit factor, max realized '
      + 'drawdown and streaks. Same rows as get_track_record.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () =>
      require('../lib/intel').getUserIntel(parseInt(process.env.BOT_USER_ID) || 1),
  },
};

// ── JSON-RPC plumbing ────────────────────────────────────────────────────────

function rpcResult(id, result) { return { jsonrpc: '2.0', id, result }; }
function rpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id ?? null, error: { code, message } };
}

async function handleRpc(msg) {
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') {
    return rpcError(msg && msg.id, -32600, 'Invalid request');
  }
  const { id, method, params } = msg;

  if (method === 'initialize') {
    return rpcResult(id, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: SERVER_INFO,
      instructions: 'RUNECLAW read-only trading intelligence. Every tool serves '
        + 'data the public site already publishes; no tool can access accounts '
        + 'or place trades. Past performance never predicts future results.',
    });
  }
  if (method === 'notifications/initialized' || method.startsWith('notifications/')) {
    return null;                                     // notification → no body
  }
  if (method === 'ping') return rpcResult(id, {});
  if (method === 'tools/list') {
    return rpcResult(id, {
      tools: Object.entries(TOOLS).map(([name, t]) => ({
        name,
        description: t.description,
        inputSchema: t.inputSchema,
        annotations: { readOnlyHint: true, openWorldHint: false },
      })),
    });
  }
  if (method === 'tools/call') {
    const name = params && params.name;
    const tool = TOOLS[name];
    if (!tool) return rpcError(id, -32602, `Unknown tool: ${name}`);
    const argErr = validateArgs(tool.inputSchema, params.arguments);
    if (argErr) return rpcError(id, -32602, argErr);
    try {
      const out = await tool.handler(params.arguments || {});
      return rpcResult(id, {
        content: [{ type: 'text', text: JSON.stringify(out) }],
        isError: false,
      });
    } catch (e) {
      return rpcResult(id, {
        content: [{ type: 'text', text: `Tool failed: ${String(e.message || e).slice(0, 200)}` }],
        isError: true,
      });
    }
  }
  return rpcError(id, -32601, `Method not found: ${method}`);
}

/**
 * Enforce each tool's declared inputSchema before dispatch (previously the
 * schema was advertised but arguments went to handlers unvalidated). Minimal
 * on purpose — object shape, known keys, primitive types, string caps —
 * matching the simple schemas this server declares.
 */
function validateArgs(schema, args) {
  for (const k of (schema && schema.required) || []) {
    if (args == null || typeof args !== 'object' || !(k in args)) {
      return `Missing required argument: ${k}`;
    }
  }
  if (args == null) return null;
  if (typeof args !== 'object' || Array.isArray(args)) return 'arguments must be an object';
  const props = (schema && schema.properties) || {};
  for (const [k, v] of Object.entries(args)) {
    const spec = props[k];
    if (!spec) {
      if (schema && schema.additionalProperties === false) return `Unknown argument: ${k}`;
      continue;
    }
    if (spec.type === 'string') {
      if (typeof v !== 'string') return `${k} must be a string`;
      if (v.length > 200) return `${k} too long (200 max)`;
    } else if (spec.type === 'number' || spec.type === 'integer') {
      if (typeof v !== 'number' || !isFinite(v)) return `${k} must be a number`;
      if (spec.type === 'integer' && !Number.isInteger(v)) return `${k} must be an integer`;
    } else if (spec.type === 'boolean' && typeof v !== 'boolean') {
      return `${k} must be a boolean`;
    }
  }
  return null;
}

router.post('/', express.json({ limit: '64kb' }), async (req, res) => {
  try {
    const out = await handleRpc(req.body);
    if (out === null) return res.status(202).end();  // notification accepted
    res.json(out);
  } catch (err) {
    res.status(500).json(rpcError(null, -32603, 'Internal error'));
  }
});

// Stateless server: no SSE stream to open, no sessions to delete.
router.get('/', (req, res) => res.status(405).json({ error: 'No stream — POST JSON-RPC to this endpoint' }));
router.delete('/', (req, res) => res.status(405).json({ error: 'Stateless server' }));

module.exports = router;
// Shared with the ERC-8257 tool endpoint (routes/tool8257.js) so the on-chain
// manifest and /mcp can never drift — one read-only tool registry.
module.exports.TOOLS = TOOLS;
module.exports.validateArgs = validateArgs;
