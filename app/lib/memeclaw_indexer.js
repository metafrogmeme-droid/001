'use strict';
/**
 * Memeclaw's indexer: the factory's events, read from the DogeOS testnet on
 * the shared schedule, into memeclaw_coins and memeclaw_trades.
 *
 * WHY IT EXISTS. A launch made through the page is recorded when the page
 * hands back its transaction (POST /api/memeclaw/coins). Everything else is
 * only on the chain: every buy and sell, a launch whose tab was closed before
 * it confirmed, a launch made straight at the contract. The page shows what
 * this has read, and says up to which block.
 *
 * READ IN ORDER, REMEMBERED ACROSS INSTANCES. The next block to read lives in
 * job_state (lib/job_state.js), and moves only after every event up to it is
 * written. A range the RPC refuses as too wide is halved and asked again. It
 * reads only blocks CONFIRMATIONS behind the head, the chain's documented
 * reorg depth (17 on the DogeOS testnet, lib/root_anchor.js), so a reorg does
 * not leave a trade here the chain no longer has. It was 3 until 21 October
 * 2026, under the depth DogeOS documents.
 *
 * WHERE IT STARTS. MEMECLAW_FACTORY_BLOCK, the block the factory was deployed
 * in (the operator's deploy check prints it). Without it, the indexer starts
 * MAX_BACKFILL blocks behind the head and says so: older trades of a coin it
 * never saw launch are not on the page.
 */
const health = require('./watch_health').register('memeclaw_indexer');
const jobState = require('./job_state');
const mc = require('./memeclaw');
const mchain = require('./memeclaw_chain');
const store = require('./memeclaw_store');
const record = require('./memeclaw_record');
const { eventTopic } = require('./abi_decode');

const JOB = 'memeclaw_indexer';
const CHUNK = 2000;
const MIN_CHUNK = 50;
const MAX_CHUNKS = 10;
// The registry's reorg depth for the chain, never fewer than 3.
const CONFIRMATIONS = Math.max(3, (require('./root_anchor').chainInfo(mchain.CHAIN_KEY) || {}).reorg_depth || 0);
const MAX_BACKFILL = 10000;
const PLAN_DAYS = 1;

const TOPICS = [[eventTopic(mc.EVENTS.Launched), eventTopic(mc.EVENTS.Trade)]];

/** One run: { read_from, read_to, events } or a skip/failure reported to health. */
async function runOnce(deps = {}) {
  const cfg = await (deps.config || require('./memeclaw_config').config)();
  if (!cfg.enabled) { health.skipped(cfg.reason || 'off'); return { skipped: cfg.reason || 'off' }; }
  const c = deps.chain || mchain;
  const now = deps.now ? deps.now() : new Date();
  try {
    const head = Number(await c.blockNumber()) - CONFIRMATIONS;
    const saved = await jobState.load(JOB);
    let from;
    let note = null;
    if (saved.found && saved.value && saved.value.factory === cfg.factory && Number.isInteger(saved.value.next_block)) {
      from = saved.value.next_block;
    } else if (Number.isInteger(cfg.factory_block)) {
      from = cfg.factory_block;
    } else {
      from = Math.max(0, head - MAX_BACKFILL);
      note = `no MEMECLAW_FACTORY_BLOCK: started ${MAX_BACKFILL} blocks back`;
    }
    const start = from;
    let events = 0;
    let span = CHUNK;
    for (let i = 0; i < MAX_CHUNKS && from <= head; i += 1) {
      const to = Math.min(from + span - 1, head);
      let logs;
      try {
        logs = await c.logs({ address: cfg.factory, fromBlock: mchain.hexNum(from), toBlock: mchain.hexNum(to), topics: TOPICS });
      } catch (e) {
        // The RPC answered that the range is too wide or too full: ask again, smaller.
        if (!(e instanceof mchain.ChainUnreadable) && span > MIN_CHUNK) { span = Math.max(MIN_CHUNK, Math.floor(span / 2)); i -= 1; continue; }
        throw e;
      }
      events += await apply(cfg, mc.factoryEvents(logs, cfg.factory), c);
      await jobState.save(JOB, { factory: cfg.factory, next_block: to + 1, read_at: now.toISOString() });
      from = to + 1;
    }
    await store.prunePlans(new Date(now.getTime() - PLAN_DAYS * 86400000));
    health.ok();
    return { read_from: start, read_to: from - 1, events, note };
  } catch (e) {
    health.failed(e);
    return { failed: e && e.name ? e.name : 'Error' };
  }
}

/** Write one range's events, in chain order, launch transactions as a whole. */
async function apply(cfg, events, c) {
  const times = new Map();
  const timeOf = async (n) => {
    if (!times.has(n)) {
      const b = await c.block(n);
      if (!b || !b.timestamp) throw new mchain.ChainUnreadable(`block ${n} has no time`);
      times.set(n, new Date(Number(BigInt(b.timestamp)) * 1000));
    }
    return times.get(n);
  };
  const byTx = new Map();
  for (const e of events) {
    if (!byTx.has(e.tx_hash)) byTx.set(e.tx_hash, []);
    byTx.get(e.tx_hash).push(e);
  }
  let n = 0;
  for (const [, evs] of byTx) {
    const blockTime = await timeOf(evs[0].block_number);
    const launches = evs.filter((e) => e.name === 'Launched');
    const trades = evs.filter((e) => e.name === 'Trade');
    for (const launch of launches) {
      await record.recordLaunch(cfg, { launch, trades: trades.filter((t) => mc.lower(t.args.token) === mc.lower(launch.args.token)),
        block_number: launch.block_number, block_time: blockTime });
    }
    const launched = new Set(launches.map((l) => mc.lower(l.args.token)));
    for (const t of trades) {
      if (!launched.has(mc.lower(t.args.token))) await record.recordTrade(t, blockTime);
    }
    n += evs.length;
  }
  return n;
}

let timer = null;
function startMemeclawIndexer(intervalMs = 60_000) {
  if (timer) return;
  timer = require('./shared_schedule').every(JOB, intervalMs, () => runOnce());
}

module.exports = { startMemeclawIndexer, runOnce, JOB, CHUNK, CONFIRMATIONS, MAX_BACKFILL };
