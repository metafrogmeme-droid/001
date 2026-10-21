'use strict';
/**
 * Memeclaw's API: a memecoin launchpad on the DogeOS testnet
 * (docs/memeclaw/MEMECLAW_PLAN.md, lib/memeclaw.js).
 *
 *   GET  /api/memeclaw/config               chain, factory, curve, limits; enabled or why not
 *   POST /api/memeclaw/launch-plan          { creator, name, symbol, description?, dev_buy? }
 *   POST /api/memeclaw/coins                { tx_hash }: read the launch from the chain, record it
 *   GET  /api/memeclaw/coins                the coins the page lists
 *   GET  /api/memeclaw/coins/:address       one coin, its trades and the launch reading;
 *                                           ?wallet= adds what that wallet holds, read now
 *   POST /api/memeclaw/trade-plan           { action: buy|sell, coin, wallet, amount, slippage_bps? }
 *   POST /api/memeclaw/trades               { tx_hash }: read a buy or sell from the chain, record it
 *   GET  /api/memeclaw/tx/:hash             pending, success, failed or missing, as the chain says
 *
 * PRACTICE MODE (lib/memeclaw_paper.js): practice DOGE, no wallet, no chain.
 * A player is a random key the browser keeps, sent as X-Memeclaw-Practice.
 *   POST   /api/memeclaw/paper/players         a new practice player: { key, player }
 *   GET    /api/memeclaw/paper/me              practice DOGE, holdings, refill, launches left
 *   DELETE /api/memeclaw/paper/me              forget this player
 *   POST   /api/memeclaw/paper/refill          back to the start, once a day, when nearly empty
 *   POST   /api/memeclaw/paper/launch          { name, symbol, description?, dev_buy? }
 *   POST   /api/memeclaw/paper/quote           { coin, action, amount }
 *   POST   /api/memeclaw/paper/trade           { coin, action, amount, min_out }
 *   GET    /api/memeclaw/paper/coins, /paper/coins/:id
 *   POST   /api/memeclaw/paper/coins/:id/hide  operator
 *   POST /api/memeclaw/coins/:address/hide  operator: { reason } hides, '' shows again
 *   GET  /api/memeclaw/deploy-plan          operator: the factory's creation transaction
 *   POST /api/memeclaw/deployment           operator: { tx_hash } of that deploy, checked
 *
 * NO ACCOUNT, NO KEY. Launching and trading need a wallet, not a sign-in: the
 * chain says who launched and who traded, and the server records only what
 * a receipt or an event log says. The server never signs or sends; every
 * plan is a transaction for the person's own wallet. Only the operator's
 * moderation and deploy doors need a session.
 *
 * OFF UNTIL CHECKED. Nothing is planned until MEMECLAW_FACTORY names an
 * address whose code is, byte for byte, the factory this site carries
 * (lib/memeclaw.checkFactory). A wrong address is "mismatch", an unreadable
 * chain "unknown", and neither is "on".
 *
 * NO DOLLARS. Amounts are testnet DOGE and coin units, in base units as the
 * chain wrote them; nothing here converts to a currency.
 */
const express = require('express');
const { authMiddleware, optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { isOperator } = require('../lib/operator_view');
const { publicChains } = require('../lib/root_anchor');
const memeclawConfig = require('../lib/memeclaw_config');
const mc = require('../lib/memeclaw');
const store = require('../lib/memeclaw_store');
const record = require('../lib/memeclaw_record');
const paper = require('../lib/memeclaw_paper');
const curve = require('../lib/memeclaw_curve');
const mchain = require('../lib/memeclaw_chain');

const router = express.Router();

const planLimit = rateLimit({ name: 'memeclaw.plan', windowMs: 60000, max: 12, key: ipKey });
const recordLimit = rateLimit({ name: 'memeclaw.record', windowMs: 60000, max: 20, key: ipKey });
const readLimit = rateLimit({ name: 'memeclaw.read', windowMs: 60000, max: 120, key: ipKey });
const tradeLimit = rateLimit({ name: 'memeclaw.trade', windowMs: 60000, max: 30, key: ipKey });
const operatorLimit = rateLimit({ name: 'memeclaw.operator', windowMs: 60000, max: 20, key: ipKey });

const className = (e) => (e && e.name ? String(e.name) : 'Error');

/** Test seam: { env, now, checkFactory }. */
function deps() { return router._deps || {}; }
function env() { return deps().env || process.env; }
function now() { return deps().now ? deps().now() : new Date(); }

async function config() {
  return memeclawConfig.config({ env: env(), checkFactory: deps().checkFactory });
}

/** The config when Memeclaw is on, else the 503 already sent. */
async function requireOn(res) {
  const cfg = await config();
  if (!cfg.enabled) {
    res.status(503).json({ error: 'memeclaw_off', reason: cfg.reason });
    return null;
  }
  return cfg;
}

// ── what a page is told about a coin ────────────────────────────────────────

function coinView(row) {
  const state = { dogeReserve: BigInt(row.doge_reserve), tokenReserve: BigInt(row.token_reserve) };
  return {
    address: row.token,
    name: row.name,
    symbol: row.symbol,
    description: row.terms_recorded ? (row.description || '') : null,
    terms: row.terms_recorded ? 'recorded' : 'not_recorded',
    terms_hash: row.terms_hash,
    creator: row.creator,
    launched_at: row.launched_at,
    tx_hash: row.tx_hash,
    block_number: Number(row.block_number),
    receipt: row.seal ? `launch:${row.seal}` : null,
    doge_reserve: String(row.doge_reserve),
    token_reserve: String(row.token_reserve),
    sold_bps: curve.soldBps(state),
    price_doge: curve.priceText(state),
    max_buy: curve.maxBuy(state).toString(),
    trade_count: Number(row.trade_count || 0),
    last_trade_at: row.last_trade_at || null,
    // Reserves as of the newest trade this site has read, or the launch.
    read_to_block: row.last_block == null ? Number(row.block_number) : Number(row.last_block),
  };
}

function tradeView(t) {
  return {
    tx_hash: t.tx_hash, log_index: Number(t.log_index), trader: t.trader, side: Number(t.is_buy) ? 'buy' : 'sell',
    doge_amount: String(t.doge_amount), token_amount: String(t.token_amount),
    block_number: Number(t.block_number), block_time: t.block_time || null,
  };
}

const bpsOf = (amount) => Number(BigInt(amount) * 10000n / curve.SUPPLY);

/**
 * The launch, read: facts about how the coin was made and what its creator
 * bought, never a verdict about the coin. "Not read" where this site has
 * not read it.
 */
async function reading(row) {
  const mine = await store.tradesBy(row.token, row.creator);
  let atLaunch = 0n; let net = 0n;
  for (const t of mine) {
    const amt = BigInt(t.token_amount);
    if (Number(t.is_buy)) { net += amt; if (t.tx_hash === row.tx_hash) atLaunch += amt; } else net -= amt;
  }
  return {
    made_by: 'the Memeclaw factory: a fixed supply created once, no owner, no mint function, no fee',
    supply: curve.SUPPLY.toString(),
    creator_bought_at_launch_bps: bpsOf(atLaunch),
    // Bought from the curve, less sold back to it. Coins moved by transfer are
    // not traded on the curve and are not in this figure.
    creator_net_from_curve_bps: Number(net) < 0 ? null : bpsOf(net),
    creator_coins_launched: await store.coinsBy(row.creator),
    holders: null,
    holders_note: 'not read: this site reads trades on the curve, not every transfer',
  };
}

// ── routes ──────────────────────────────────────────────────────────────────

router.get('/config', readLimit, async (req, res) => {
  try {
    const cfg = await config();
    res.set('Cache-Control', 'no-store');
    return res.json(cfg);
  } catch (err) {
    console.error('Memeclaw config failed:', className(err));
    return res.status(503).json({ error: 'config_unreadable', exception: className(err) });
  }
});

router.post('/launch-plan', planLimit, async (req, res) => {
  try {
    const cfg = await requireOn(res);
    if (!cfg) return undefined;
    const b = req.body || {};
    const out = mc.launchPlan({
      factory: cfg.factory, chainId: cfg.chain.chain_id, creator: b.creator,
      name: typeof b.name === 'string' ? b.name : '', symbol: typeof b.symbol === 'string' ? b.symbol : '',
      description: typeof b.description === 'string' ? b.description : '',
      devBuy: b.dev_buy == null || b.dev_buy === '' ? '0' : String(b.dev_buy), now: now(),
    });
    if (!out.ok) return res.status(400).json({ error: out.reason, detail: out.detail || null });
    await store.savePlan({ ...out.plan.terms, terms_hash: out.plan.terms_hash, dev_buy: out.plan.dev_buy }, now());
    return res.json(out.plan);
  } catch (err) {
    console.error('Memeclaw launch plan failed:', className(err));
    return res.status(503).json({ error: 'plan_unavailable', exception: className(err) });
  }
});

router.post('/coins', recordLimit, async (req, res) => {
  try {
    const cfg = await requireOn(res);
    if (!cfg) return undefined;
    const tx = mc.lower((req.body || {}).tx_hash);
    const r = await mc.readLaunch(tx, cfg.factory);
    if (r.status === 'unknown') return res.status(503).json({ error: 'chain_unreadable', reason: r.reason });
    if (r.status !== 'verified') return res.status(400).json({ error: 'not_a_launch', reason: r.reason });
    const row = await record.recordLaunch(cfg, r, now());
    return res.json({ coin: coinView(row), sealed: !!row.seal });
  } catch (err) {
    console.error('Memeclaw launch record failed:', className(err));
    return res.status(503).json({ error: 'record_unavailable', exception: className(err) });
  }
});

router.post('/trades', recordLimit, async (req, res) => {
  try {
    const cfg = await requireOn(res);
    if (!cfg) return undefined;
    const r = await mc.readTrades((req.body || {}).tx_hash, cfg.factory);
    if (r.status === 'unknown') return res.status(503).json({ error: 'chain_unreadable', reason: r.reason });
    if (r.status !== 'verified') return res.status(400).json({ error: 'not_a_trade', reason: r.reason });
    let recorded = 0;
    for (const t of r.trades) if (await record.recordTrade(t, r.block_time)) recorded += 1;
    return res.json({ ok: true, trades: r.trades.length, recorded });
  } catch (err) {
    console.error('Memeclaw trade record failed:', className(err));
    return res.status(503).json({ error: 'record_unavailable', exception: className(err) });
  }
});

router.get('/tx/:hash', readLimit, async (req, res) => {
  try {
    const s = await mc.txState(req.params.hash);
    if (s.status === 'unknown') return res.status(503).json({ error: 'chain_unreadable', reason: s.reason });
    res.set('Cache-Control', 'no-store');
    return res.json(s);
  } catch (err) {
    console.error('Memeclaw tx read failed:', className(err));
    return res.status(503).json({ error: 'tx_unreadable', exception: className(err) });
  }
});

router.get('/coins', readLimit, optionalAuth, async (req, res) => {
  try {
    const operator = await isOperator(req);
    const rows = await store.coins();
    const list = rows.filter((r) => operator || !r.hidden_at).slice(0, 200)
      .map((r) => (operator ? { ...coinView(r), hidden: r.hidden_at ? (r.hidden_reason || 'hidden') : null } : coinView(r)));
    res.set('Cache-Control', 'no-store');
    return res.json({ coins: list, count: list.length });
  } catch (err) {
    console.error('Memeclaw coin list failed:', className(err));
    return res.status(503).json({ error: 'coins_unreadable', exception: className(err) });
  }
});

router.get('/coins/:address', readLimit, optionalAuth, async (req, res) => {
  try {
    const addr = mc.lower(req.params.address);
    if (!mc.ADDR_RE.test(addr)) return res.status(400).json({ error: 'coin_address' });
    const row = await store.coin(addr);
    if (!row) return res.status(404).json({ error: 'unknown_coin' });
    const operator = await isOperator(req);
    // A hidden coin is still on the chain; the page just does not repeat its name.
    if (row.hidden_at && !operator) return res.status(404).json({ error: 'hidden' });
    const body = { coin: coinView(row), trades: (await store.trades(addr)).map(tradeView), reading: await reading(row) };
    if (operator) body.coin.hidden = row.hidden_at ? (row.hidden_reason || 'hidden') : null;
    // ?wallet= — how many of this coin that wallet holds, read from the
    // chain now: null with the reason when it could not be read, never 0.
    const wallet = mc.lower(req.query.wallet || '');
    if (mc.ADDR_RE.test(wallet)) {
      try {
        body.wallet = { address: wallet, held: (await mc.readHeld(addr, wallet)).toString() };
      } catch (e) {
        body.wallet = { address: wallet, held: null, unread: className(e) };
      }
    }
    res.set('Cache-Control', 'no-store');
    return res.json(body);
  } catch (err) {
    console.error('Memeclaw coin read failed:', className(err));
    return res.status(503).json({ error: 'coin_unreadable', exception: className(err) });
  }
});

router.post('/trade-plan', tradeLimit, async (req, res) => {
  try {
    const cfg = await requireOn(res);
    if (!cfg) return undefined;
    const b = req.body || {};
    const out = await mc.tradePlan({
      factory: cfg.factory, chainId: cfg.chain.chain_id, action: b.action, token: b.coin, wallet: b.wallet,
      amount: b.amount == null ? '' : String(b.amount),
      slippageBps: b.slippage_bps == null ? mc.DEFAULT_SLIPPAGE_BPS : b.slippage_bps, now: now(),
    });
    if (!out.ok) return res.status(400).json({ error: out.reason, max_buy: out.max_buy || null, held: out.held || null });
    return res.json(out);
  } catch (err) {
    if (err instanceof mchain.ChainUnreadable) return res.status(503).json({ error: 'chain_unreadable' });
    console.error('Memeclaw trade plan failed:', className(err));
    return res.status(503).json({ error: 'plan_unavailable', exception: className(err) });
  }
});

router.post('/coins/:address/hide', operatorLimit, authMiddleware, async (req, res) => {
  try {
    if (!(await isOperator(req))) return res.status(403).json({ error: 'operator_only' });
    const addr = mc.lower(req.params.address);
    if (!mc.ADDR_RE.test(addr)) return res.status(400).json({ error: 'coin_address' });
    const reason = String((req.body || {}).reason || '').trim();
    const ok = await store.setHidden(addr, reason || null, now());
    if (!ok) return res.status(404).json({ error: 'unknown_coin' });
    return res.json({ ok: true, hidden: !!reason });
  } catch (err) {
    console.error('Memeclaw hide failed:', className(err));
    return res.status(503).json({ error: 'hide_unavailable', exception: className(err) });
  }
});

router.get('/deploy-plan', operatorLimit, authMiddleware, async (req, res) => {
  if (!(await isOperator(req))) return res.status(403).json({ error: 'operator_only' });
  const chain = publicChains().find((c) => c.key === mchain.CHAIN_KEY);
  if (!chain || chain.testnet !== true) return res.status(503).json({ error: 'no_testnet' });
  return res.json({ ...mc.deployPlan(chain.chain_id), compiler: mc.ARTIFACT.compiler,
    source_sha256: mc.ARTIFACT.source_sha256,
    // The first coins, for the operator's wallet to launch once the factory is on.
    starters: mc.STARTER_COINS });
});

router.post('/deployment', operatorLimit, authMiddleware, async (req, res) => {
  try {
    if (!(await isOperator(req))) return res.status(403).json({ error: 'operator_only' });
    const r = await mc.readDeployment((req.body || {}).tx_hash);
    if (r.status === 'unknown') return res.status(503).json({ error: 'chain_unreadable', reason: r.reason });
    if (r.status !== 'verified') return res.status(400).json({ error: 'not_the_factory', reason: r.reason });
    return res.json({ ok: true, address: r.address, block_number: r.block_number,
      set: { MEMECLAW_FACTORY: r.address, MEMECLAW_FACTORY_BLOCK: String(r.block_number) } });
  } catch (err) {
    console.error('Memeclaw deployment check failed:', className(err));
    return res.status(503).json({ error: 'check_unavailable', exception: className(err) });
  }
});

// ── practice mode: no wallet, no chain (lib/memeclaw_paper.js) ───────────────

const paperNewLimit = rateLimit({ name: 'memeclaw.paper.new', windowMs: 3600000, max: 10, key: ipKey });
const paperActLimit = rateLimit({ name: 'memeclaw.paper.act', windowMs: 60000, max: 60, key: ipKey });

/** The practice player the request's key belongs to, or null with the 401 already sent. */
async function practicePlayer(req, res) {
  const p = await paper.playerFor(String(req.get('x-memeclaw-practice') || '').trim().toLowerCase());
  if (!p) {
    res.status(401).json({ error: 'practice_key' });
    return null;
  }
  return p;
}

function paperRefused(res, err, what) {
  if (err instanceof paper.PaperRefusal) {
    const code = err.reason === 'unknown_player' ? 401 : 400;
    return res.status(code).json({ error: err.reason === 'unknown_player' ? 'practice_key' : err.reason, ...err.extra });
  }
  console.error(`Memeclaw practice ${what} failed:`, className(err));
  return res.status(503).json({ error: `${what}_unavailable`, exception: className(err) });
}

function paperCoinView(row) {
  const state = { dogeReserve: BigInt(String(row.doge_reserve)), tokenReserve: BigInt(String(row.token_reserve)) };
  return {
    mode: 'practice',
    id: row.id,
    name: row.name,
    symbol: row.symbol,
    description: row.description || '',
    creator: row.creator,
    created_at: row.created_at,
    doge_reserve: state.dogeReserve.toString(),
    token_reserve: state.tokenReserve.toString(),
    sold_bps: curve.soldBps(state),
    price_doge: curve.priceText(state),
    max_buy: curve.maxBuy(state).toString(),
    trade_count: Number(row.trade_count || 0),
    last_trade_at: row.last_trade_at || null,
  };
}

function paperTradeView(t) {
  return { id: Number(t.id), trader: t.player, side: Number(t.is_buy) ? 'buy' : 'sell',
    doge_amount: String(t.doge_amount), token_amount: String(t.token_amount), at: t.created_at };
}

router.post('/paper/players', paperNewLimit, async (req, res) => {
  try {
    const out = await paper.newPlayer(now());
    return res.json({ key: out.key, player: { id: out.player.id, practice_doge: out.player.doge } });
  } catch (err) { return paperRefused(res, err, 'player'); }
});

router.get('/paper/me', readLimit, async (req, res) => {
  try {
    const p = await practicePlayer(req, res);
    if (!p) return undefined;
    const holdings = await paper.holdingsOf(p.id);
    const t = now().getTime();
    const nextAt = p.refilled_at ? new Date(new Date(p.refilled_at).getTime() + paper.REFILL_EVERY_MS) : null;
    res.set('Cache-Control', 'no-store');
    return res.json({
      player: { id: p.id, practice_doge: String(BigInt(String(p.doge).split('.')[0])) },
      holdings,
      refill: { below: paper.REFILL_BELOW.toString(), to: paper.START_DOGE.toString(),
        next_at: nextAt && nextAt.getTime() > t ? nextAt.toISOString() : null },
      launches_left_today: p.launches_day === now().toISOString().slice(0, 10)
        ? Math.max(0, paper.LAUNCHES_PER_DAY - Number(p.launches_today)) : paper.LAUNCHES_PER_DAY,
    });
  } catch (err) { return paperRefused(res, err, 'player'); }
});

router.delete('/paper/me', paperActLimit, async (req, res) => {
  try {
    const p = await practicePlayer(req, res);
    if (!p) return undefined;
    await paper.forget(p.id);
    return res.json({ ok: true });
  } catch (err) { return paperRefused(res, err, 'forget'); }
});

router.post('/paper/refill', paperActLimit, async (req, res) => {
  try {
    const p = await practicePlayer(req, res);
    if (!p) return undefined;
    return res.json({ ok: true, practice_doge: await paper.refill(p.id, now()) });
  } catch (err) { return paperRefused(res, err, 'refill'); }
});

router.post('/paper/launch', paperActLimit, async (req, res) => {
  try {
    const p = await practicePlayer(req, res);
    if (!p) return undefined;
    const b = req.body || {};
    const id = await paper.launch(p.id, {
      name: typeof b.name === 'string' ? b.name : '', symbol: typeof b.symbol === 'string' ? b.symbol : '',
      description: typeof b.description === 'string' ? b.description : '',
      devBuy: b.dev_buy == null || b.dev_buy === '' ? '0' : String(b.dev_buy),
    }, now());
    return res.json({ coin: paperCoinView(await paper.coin(id)) });
  } catch (err) { return paperRefused(res, err, 'launch'); }
});

router.post('/paper/quote', readLimit, async (req, res) => {
  try {
    const b = req.body || {};
    const row = await paper.coin(b.coin);
    if (!row || row.hidden_at) return res.status(404).json({ error: 'unknown_coin' });
    if (!mc.WEI_RE.test(String(b.amount)) || BigInt(b.amount) === 0n) return res.status(400).json({ error: 'amount' });
    const state = { dogeReserve: BigInt(String(row.doge_reserve)), tokenReserve: BigInt(String(row.token_reserve)) };
    const amount = BigInt(b.amount);
    if (b.action === 'buy') {
      const gets = curve.quoteBuy(state, amount);
      if (gets === null) return res.status(400).json({ error: 'sold_out', max_buy: curve.maxBuy(state).toString() });
      return res.json({ gets: gets.toString() });
    }
    if (b.action === 'sell') return res.json({ gets: curve.quoteSell(state, amount).toString() });
    return res.status(400).json({ error: 'action' });
  } catch (err) { return paperRefused(res, err, 'quote'); }
});

router.post('/paper/trade', paperActLimit, async (req, res) => {
  try {
    const p = await practicePlayer(req, res);
    if (!p) return undefined;
    const b = req.body || {};
    const out = await paper.trade(p.id, { coin: b.coin, action: b.action, amount: b.amount == null ? '' : String(b.amount),
      minOut: b.min_out == null ? '0' : String(b.min_out) }, now());
    return res.json({ ok: true, gets: out.gets, practice_doge: out.doge, held: out.held });
  } catch (err) { return paperRefused(res, err, 'trade'); }
});

router.get('/paper/coins', readLimit, optionalAuth, async (req, res) => {
  try {
    const operator = await isOperator(req);
    const rows = await paper.coins();
    const list = rows.filter((r) => operator || !r.hidden_at).slice(0, 200)
      .map((r) => (operator ? { ...paperCoinView(r), hidden: r.hidden_at ? (r.hidden_reason || 'hidden') : null } : paperCoinView(r)));
    res.set('Cache-Control', 'no-store');
    return res.json({ coins: list, count: list.length });
  } catch (err) { return paperRefused(res, err, 'coins'); }
});

router.get('/paper/coins/:id', readLimit, optionalAuth, async (req, res) => {
  try {
    const row = await paper.coin(req.params.id);
    if (!row) return res.status(404).json({ error: 'unknown_coin' });
    const operator = await isOperator(req);
    if (row.hidden_at && !operator) return res.status(404).json({ error: 'hidden' });
    res.set('Cache-Control', 'no-store');
    return res.json({ coin: paperCoinView(row), trades: (await paper.trades(row.id)).map(paperTradeView) });
  } catch (err) { return paperRefused(res, err, 'coin'); }
});

router.post('/paper/coins/:id/hide', operatorLimit, authMiddleware, async (req, res) => {
  try {
    if (!(await isOperator(req))) return res.status(403).json({ error: 'operator_only' });
    const reason = String((req.body || {}).reason || '').trim();
    const ok = await paper.setHidden(req.params.id, reason || null, now());
    if (!ok) return res.status(404).json({ error: 'unknown_coin' });
    return res.json({ ok: true, hidden: !!reason });
  } catch (err) { return paperRefused(res, err, 'hide'); }
});

router.config = config;
router._resetChecks = memeclawConfig._resetChecks;
module.exports = router;
