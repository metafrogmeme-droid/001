'use strict';
/**
 * Writing what the chain said about Memeclaw: one launch (its coin, the
 * trades in its transaction, and its seal when the page planned its terms),
 * or one trade. The launch route (routes/memeclaw.js) and the indexer
 * (lib/memeclaw_indexer.js) both write through here, so a launch the page saw
 * and a launch the indexer found are recorded the same way.
 */
const mc = require('./memeclaw');
const store = require('./memeclaw_store');
const curve = require('./memeclaw_curve');

/** One decoded Trade event, as a row. */
async function recordTrade(t, blockTime) {
  return store.recordTrade({
    tx_hash: t.tx_hash, log_index: t.log_index, token: t.args.token, trader: t.args.trader,
    is_buy: t.args.isBuy, doge_amount: t.args.dogeAmount.toString(), token_amount: t.args.tokenAmount.toString(),
    doge_reserve: t.args.dogeReserve.toString(), token_reserve: t.args.tokenReserve.toString(),
    block_number: t.block_number, block_time: blockTime || null,
  });
}

/**
 * Record a launch the chain confirmed: `r` is { launch, trades, block_number,
 * block_time } as lib/memeclaw.readLaunch returns it. A name the page would
 * refuse is recorded hidden, with the rule it broke. The seal is minted only
 * when the page planned exactly these terms for exactly this creator.
 * Returns the coin's row.
 */
async function recordLaunch(cfg, r, now = new Date()) {
  const a = r.launch.args;
  const refused = mc.pageProblem({ name: a.name, symbol: a.symbol, description: '' });
  await store.recordCoin({
    token: a.token, chain_id: cfg.chain.chain_id, factory: cfg.factory, creator: a.creator,
    name: a.name, symbol: a.symbol, terms_hash: a.termsHash, tx_hash: r.launch.tx_hash,
    block_number: r.block_number, launched_at: r.block_time, supply: curve.SUPPLY.toString(),
  }, { hidden: refused ? `the page's rules: ${refused.reason}` : null, now });
  for (const t of r.trades) await recordTrade(t, r.block_time);
  const row = await store.coin(a.token);
  const plan = await store.planFor(a.termsHash);
  if (row && !row.seal && !refused && plan && mc.lower(plan.creator) === mc.lower(a.creator)) {
    const bought = r.trades
      .filter((t) => t.args.isBuy && mc.lower(t.args.trader) === mc.lower(a.creator) && mc.lower(t.args.token) === mc.lower(a.token))
      .reduce((sum, t) => sum + t.args.tokenAmount, 0n);
    await store.sealLaunch(row, plan, bought.toString(), now);
    await store.markPlanUsed(a.termsHash, now);
    return store.coin(a.token);
  }
  return row;
}

module.exports = { recordLaunch, recordTrade };
