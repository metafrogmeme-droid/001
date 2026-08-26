'use strict';
/**
 * An agent trading as ITSELF — the credential path end to end.
 *
 * WHAT WAS MISSING
 *
 * An autonomous agent could already paper-trade over MCP, and its trades were
 * sealed, rooted and ranked. But `openForUser` — where every `arena_open` call
 * lands — had no `agent_slug` in its INSERT at all and stamped every row
 * 'manual'. `/api/public/agent-record/:slug` selects `WHERE agent_slug = ?`, so
 * an autonomous agent's own trades were invisible to the one surface that
 * exists to publish an agent's record. Its identity was its user account.
 *
 * THE HONESTY CONSTRAINT THAT SHAPED IT
 *
 * `lib/agent_record.js` describes itself as "THE COPIERS' RECORD, NOT THE
 * ENGINE'S" — median return across MEMBERS who copied a pick, sized by each
 * member. Stamping an agent's own trades with the same slug would silently
 * redefine every field on that endpoint: trades nobody copied, sized by the
 * agent, with `copiers` counting the agent as one of its own followers. So the
 * two are split by `source` and never summed, and the split ships in the same
 * change as the ability to create the rows.
 *
 * WHY THE SLUG LIVES ON THE KEY
 *
 * Not in the tool's arguments. An agent that could name itself per-trade could
 * write into any record whose slug it can spell; `bindAgent` accepts only a
 * slug its owner has already claimed in the `agents` table.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const { pool } = require('../db');
const agents = require('../lib/agents');
const arenaKeys = require('../lib/arena_keys');
const { computeAgentRecord } = require('../lib/agent_record');

const OWNER = 5150;
const STRANGER = 6161;

function reset() {
  for (const t of ['agents', 'arenaApiKeys', 'arenaTrades', 'arenaPositions']) {
    if (pool[t]) pool[t].length = 0;
  }
}
test.beforeEach(reset);

// ── the binding ─────────────────────────────────────────────────────────────

test('a key binds only to a slug its owner has claimed', async () => {
  const { id } = await arenaKeys.mint(OWNER, 'agent key');
  await agents.claim(OWNER, 'my-bot', 'My Bot');
  await agents.claim(STRANGER, 'their-bot', 'Their Bot');

  const ok = await arenaKeys.bindAgent(OWNER, id, 'my-bot');
  assert.equal(ok.ok, true);
  assert.equal(ok.agent_slug, 'my-bot');

  // The whole point: a slug decides which PUBLIC record this key writes into.
  const theirs = await arenaKeys.bindAgent(OWNER, id, 'their-bot');
  assert.equal(theirs.ok, false, 'bound to a slug somebody else claimed');
  assert.equal(theirs.code, 'not_yours');

  const nothing = await arenaKeys.bindAgent(OWNER, id, 'never-claimed');
  assert.equal(nothing.ok, false);

  // Still bound to the legitimate one — a refused bind must not clear it.
  const [rows] = await pool.execute(
    'SELECT id, label, created_at, last_used_at, agent_slug FROM arena_api_keys WHERE user_id = ? AND revoked_at IS NULL ORDER BY id DESC', [OWNER]);
  assert.equal(rows[0].agent_slug, 'my-bot');
});

test("another account's key cannot be bound at all", async () => {
  const { id } = await arenaKeys.mint(OWNER, 'k');
  await agents.claim(STRANGER, 'their-bot');
  const r = await arenaKeys.bindAgent(STRANGER, id, 'their-bot');
  assert.equal(r.ok, false);
  assert.equal(r.code, 'no_key', 'a stranger reached a key that is not theirs');
});

test('verify carries the binding, and an unbound key is attributed to nobody', async () => {
  const { key, id } = await arenaKeys.mint(OWNER, 'k');
  const before = await arenaKeys.verify(key);
  assert.equal(before.userId, OWNER);
  assert.equal(before.agentSlug, null, 'an unbound key must not name an agent');

  await agents.claim(OWNER, 'my-bot');
  await arenaKeys.bindAgent(OWNER, id, 'my-bot');
  const after = await arenaKeys.verify(key);
  assert.equal(after.agentSlug, 'my-bot');

  // Unbinding is a real operation, not just a different value.
  await arenaKeys.bindAgent(OWNER, id, null);
  assert.equal((await arenaKeys.verify(key)).agentSlug, null);
});

test('a revoked key verifies as nothing, binding or not', async () => {
  const { key, id } = await arenaKeys.mint(OWNER, 'k');
  await agents.claim(OWNER, 'my-bot');
  await arenaKeys.bindAgent(OWNER, id, 'my-bot');
  await arenaKeys.revoke(OWNER, id);
  assert.equal(await arenaKeys.verify(key), null);
});

// ── the record split ────────────────────────────────────────────────────────

const trade = (over) => ({
  user_id: 1, symbol: 'BTCUSDT', direction: 'LONG', margin: 100, pnl: 10,
  reason: 'manual', source: 'signal', trade_key: 'k', sealed_at: 'then',
  closed_at: 'now', ...over,
});

test("an agent's own trades never enter the copiers' numbers", () => {
  const rec = computeAgentRecord([
    trade({ user_id: 11, pnl: 10 }),           // a member copied a pick
    trade({ user_id: 12, pnl: -20 }),          // and another
    trade({ user_id: 99, pnl: 500, source: 'agent' }),   // the agent itself
    trade({ user_id: 99, pnl: 400, source: 'agent' }),
  ]);
  assert.equal(rec.trades, 2, "the agent's own trades were counted as copies");
  assert.equal(rec.copiers, 2, 'the agent was counted as one of its own followers');
  assert.equal(rec.best_rom_pct, 10, 'a spectacular self-traded return leaked into the copiers record');
  assert.equal(rec.own.trades, 2);
  assert.equal(rec.own.best_rom_pct, 500);
  // `copiers` is meaningless for a record of the agent's own trading and must
  // not appear on it wearing a plausible number.
  assert.ok(!('copiers' in rec.own), 'the own record carries a copiers count');
});

test('own is NULL when the agent has never traded for itself', () => {
  const rec = computeAgentRecord([trade({ user_id: 11 })]);
  // Not a zeroed block: "never traded for itself" and "traded and scored
  // nothing" are different facts, and `trades: 0` beside a null median reads
  // as the second.
  assert.equal(rec.own, null);
  assert.equal(rec.trades, 1);
});

test('a row with NO source is a copier row, never the agent', () => {
  // Every row written before the column existed. Defaulting the other way
  // would reclassify the entire historical record as the agent's own trading.
  const rec = computeAgentRecord([
    { ...trade({ user_id: 11 }), source: undefined },
    { ...trade({ user_id: 12 }), source: null },
  ]);
  assert.equal(rec.trades, 2);
  assert.equal(rec.own, null);
});

test('recent lists are per-record and do not bleed', () => {
  const rec = computeAgentRecord([
    trade({ user_id: 11, symbol: 'ETHUSDT' }),
    trade({ user_id: 99, symbol: 'SOLUSDT', source: 'agent' }),
  ]);
  assert.deepEqual(rec.recent.map((r) => r.symbol), ['ETHUSDT']);
  assert.deepEqual(rec.own.recent.map((r) => r.symbol), ['SOLUSDT']);
});

// ── the whole path, driven for real ─────────────────────────────────────────
//
// Everything above tests a piece. This drives an agent's key through the MCP
// write tool, opens a position, closes it, and reads the public record — the
// only kind of test that can tell code that is PRESENT from code that is
// REACHED. `openForUser`'s missing agent_slug was invisible to every existing
// test precisely because nothing walked this path end to end.

const { setTickerFetcher } = require('../lib/tickers');
const mcp = require('../routes/mcp');
const { closeForUser, loadPositions } = require('../routes/arena');
const agentRecordRoute = require('../routes/agent_record');

test.after(() => setTickerFetcher(null));

test('an agent trades as itself and lands under OWN on the public record', async () => {
  reset();
  setTickerFetcher(async () => ({ BTCUSDT: { price: 100 } }));

  const { key, id } = await arenaKeys.mint(OWNER, 'bot key');
  assert.equal((await agents.claim(OWNER, 'lonewolf', 'Lone Wolf')).ok, true);
  assert.equal((await arenaKeys.bindAgent(OWNER, id, 'lonewolf')).ok, true);

  // Exactly what routes/mcp.js builds from a verified key.
  const ident = await arenaKeys.verify(key);
  const ctx = { userId: ident.userId, agentSlug: ident.agentSlug };

  const opened = await mcp.WRITE_TOOLS.arena_open.handler(
    { symbol: 'BTCUSDT', direction: 'LONG', stake_pct: 2, leverage: 2 }, ctx);
  assert.ok(opened, 'the open was refused');

  // The position must carry the identity — this is the line that did not exist.
  const [pos] = await loadPositions(OWNER);
  assert.equal(pos.agent_slug, 'lonewolf', 'the open did not record the agent');
  assert.equal(pos.source, 'agent', 'the open did not record the provenance');

  // Close it in profit, then read the record the way a stranger would.
  setTickerFetcher(async () => ({ BTCUSDT: { price: 120 } }));
  const closed = await closeForUser(OWNER, pos.id);
  assert.equal(closed.status, 200, JSON.stringify(closed.body));

  const [trades] = await pool.execute(
    'SELECT agent_slug, source, pnl FROM arena_trades WHERE user_id = ?', [OWNER]);
  assert.equal(trades.length, 1);
  assert.equal(trades[0].agent_slug, 'lonewolf', 'the close dropped the agent tag');
  assert.equal(trades[0].source, 'agent', 'the close dropped the provenance');

  // And the public surface: under `own`, and NOT in the copiers' numbers.
  agentRecordRoute._resetCache();
  const [rows] = await pool.execute(
    `SELECT user_id, symbol, direction, margin, pnl, reason, source, trade_key, sealed_at, closed_at
     FROM arena_trades WHERE agent_slug = ? ORDER BY closed_at DESC LIMIT 500`, ['lonewolf']);
  const rec = computeAgentRecord(rows);
  assert.equal(rec.own.trades, 1, "the agent's own trade is missing from its own record");
  assert.ok(rec.own.best_rom_pct > 0);
  assert.equal(rec.trades, 0, "a self-traded row was published as a copier's result");
  assert.equal(rec.copiers, 0, 'the agent was counted as its own follower');
});

test('an UNBOUND key trades as nobody — no slug is invented', async () => {
  reset();
  setTickerFetcher(async () => ({ BTCUSDT: { price: 100 } }));
  const { key } = await arenaKeys.mint(OWNER, 'plain key');
  const ident = await arenaKeys.verify(key);

  await mcp.WRITE_TOOLS.arena_open.handler(
    { symbol: 'BTCUSDT', direction: 'LONG', stake_pct: 2, leverage: 2 },
    { userId: ident.userId, agentSlug: ident.agentSlug });

  const [pos] = await loadPositions(OWNER);
  assert.equal(pos.agent_slug, null, 'an unbound key was attributed to an agent');
  assert.equal(pos.source, 'manual', "an unbound key's trade was marked as an agent's own");
});

test('an agent cannot name itself — the slug is not an argument', () => {
  // The identity comes from the binding on the presented key, never from the
  // call. If arena_open accepted a slug, any key could write into any record
  // whose slug it can spell, and the ownership check in bindAgent would be
  // decoration. The schema is what makes that structural rather than a
  // convention: unknown properties are refused outright.
  const schema = mcp.WRITE_TOOLS.arena_open.inputSchema;
  assert.equal(schema.additionalProperties, false,
    'arena_open accepts unknown properties — an agent could smuggle a slug');
  for (const k of Object.keys(schema.properties)) {
    assert.ok(!/agent|slug/i.test(k), `arena_open takes "${k}" — identity must not be an argument`);
  }
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'mcp.js'), 'utf8');
  assert.match(src, /openForUser\(ctx\.userId, intent, \{ agentSlug: ctx\.agentSlug \}\)/,
    'the slug must come from the verified context, not from args');
});
