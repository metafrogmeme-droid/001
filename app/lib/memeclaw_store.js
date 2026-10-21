'use strict';
/**
 * Memeclaw's rows: the launches the page planned, the coins the chain says
 * the factory created, and every trade on their curves.
 *
 *   memeclaw_plans   terms the page sealed for a wallet, waiting for its launch
 *   memeclaw_coins   one row per coin; `terms_recorded` says whether the page
 *                    holds its terms (a coin launched straight at the contract
 *                    does not), and the reserves are those after its newest trade
 *   memeclaw_trades  one row per Trade event, (tx_hash, log_index) unique
 *
 * No account and no user id: the chain names the creator and every trader,
 * and nothing here is tied to a sign-in.
 *
 * Amounts are base-unit decimal strings, as the chain wrote them: an
 * 18-decimal coin does not fit a JSON number, and a reserve rounded on its
 * way into the database would be a quote that pays something else.
 */
const { pool } = require('../db');
const { sealMemeclawLaunch } = require('./callseal');
const { lower } = require('./memeclaw');

const COIN_COLS = 'token, chain_id, factory, creator, name, symbol, description, terms_hash, terms_recorded, '
  + 'tx_hash, block_number, launched_at, doge_reserve, token_reserve, last_block, last_log_index, '
  + 'trade_count, last_trade_at, seal, seal_payload, sealed_at, hidden_at, hidden_reason';
const TRADE_COLS = 'tx_hash, log_index, token, trader, is_buy, doge_amount, token_amount, '
  + 'doge_reserve, token_reserve, block_number, block_time';
const PLAN_COLS = 'terms_hash, chain_id, factory, creator, name, symbol, description, dev_buy, created_at, used_at';

// ── plans ───────────────────────────────────────────────────────────────────

/** Keep a plan. The same terms planned twice are one plan: true either way. */
async function savePlan(p, now = new Date()) {
  try {
    await pool.execute(
      `INSERT INTO memeclaw_plans (${PLAN_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)`,
      [lower(p.terms_hash), Number(p.chain_id), lower(p.factory), lower(p.creator), p.name, p.symbol,
        p.description || '', String(p.dev_buy || '0'), now]);
  } catch (e) {
    if (!(e && e.code === 'ER_DUP_ENTRY')) throw e;
  }
  return true;
}

async function planFor(termsHash) {
  const [rows] = await pool.execute(
    `SELECT ${PLAN_COLS} FROM memeclaw_plans WHERE terms_hash = ?`, [lower(termsHash)]);
  return rows[0] || null;
}

async function markPlanUsed(termsHash, now = new Date()) {
  const [r] = await pool.execute(
    'UPDATE memeclaw_plans SET used_at = ? WHERE terms_hash = ? AND used_at IS NULL', [now, lower(termsHash)]);
  return !!(r && r.affectedRows);
}

/** Plans never launched, older than `before`, are dropped. */
async function prunePlans(before) {
  const [r] = await pool.execute(
    'DELETE FROM memeclaw_plans WHERE created_at < ? AND used_at IS NULL', [before]);
  return (r && r.affectedRows) || 0;
}

// ── coins ───────────────────────────────────────────────────────────────────

/**
 * Record a coin the chain says the factory created. First write wins; true
 * when this call wrote it. `hidden` carries the page's refusal for a coin
 * launched straight at the contract under a name the page would not take.
 */
async function recordCoin(c, { hidden = null, now = new Date() } = {}) {
  const [r] = await pool.execute(
    `INSERT IGNORE INTO memeclaw_coins (token, chain_id, factory, creator, name, symbol, description, terms_hash, terms_recorded, `
    + 'tx_hash, block_number, launched_at, doge_reserve, token_reserve, last_block, last_log_index, trade_count, '
    + 'hidden_at, hidden_reason, created_at) '
    + 'VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?, ?)',
    [lower(c.token), Number(c.chain_id), lower(c.factory), lower(c.creator), String(c.name), String(c.symbol),
      lower(c.terms_hash), lower(c.tx_hash), Number(c.block_number), c.launched_at,
      '0', String(c.supply), hidden ? now : null, hidden ? String(hidden).slice(0, 120) : null, now]);
  return !!(r && r.affectedRows);
}

/**
 * Attach the page's terms to a coin and seal the launch (v8). Only when the
 * chain's terms hash is the plan's, and only once: a coin already sealed, or
 * launched under other terms, is left as it is. Returns the seal or null.
 */
async function sealLaunch(coin, plan, creatorBought, now = new Date()) {
  if (!plan || lower(plan.terms_hash) !== lower(coin.terms_hash)) return null;
  const { seal, seal_payload } = sealMemeclawLaunch({
    chain_id: coin.chain_id, factory: coin.factory, token: coin.token, creator: coin.creator,
    name: coin.name, symbol: coin.symbol, description: plan.description || '',
    terms_hash: coin.terms_hash, tx: coin.tx_hash, block_number: coin.block_number,
    launched_at: coin.launched_at, creator_bought: String(creatorBought || '0'),
  });
  const [r] = await pool.execute(
    'UPDATE memeclaw_coins SET description = ?, terms_recorded = 1, seal = ?, seal_payload = ?, sealed_at = ? '
    + 'WHERE token = ? AND seal IS NULL AND terms_hash = ?',
    [plan.description || '', seal, seal_payload, now, lower(coin.token), lower(coin.terms_hash)]);
  return r && r.affectedRows ? seal : null;
}

async function coin(token) {
  const [rows] = await pool.execute(`SELECT ${COIN_COLS} FROM memeclaw_coins WHERE token = ?`, [lower(token)]);
  return rows[0] || null;
}

async function coinBySeal(seal) {
  const [rows] = await pool.execute(`SELECT ${COIN_COLS} FROM memeclaw_coins WHERE seal = ?`, [String(seal)]);
  return rows[0] || null;
}

/** Every coin, newest launch first (at most 500; the page shows fewer). */
async function coins() {
  const [rows] = await pool.execute(`SELECT ${COIN_COLS} FROM memeclaw_coins ORDER BY block_number DESC LIMIT 500`);
  return rows;
}

async function coinsBy(creator) {
  const [rows] = await pool.execute('SELECT COUNT(*) AS n FROM memeclaw_coins WHERE creator = ?', [lower(creator)]);
  return Number(rows[0] ? rows[0].n : 0);
}

async function setHidden(token, reason, now = new Date()) {
  const [r] = await pool.execute(
    'UPDATE memeclaw_coins SET hidden_at = ?, hidden_reason = ? WHERE token = ?',
    [reason ? now : null, reason ? String(reason).slice(0, 120) : null, lower(token)]);
  return !!(r && r.affectedRows);
}

// ── trades ──────────────────────────────────────────────────────────────────

/**
 * Record one Trade event and move the coin's reserves to it, if it is the
 * newest the coin has seen (block, then log index). A trade already recorded
 * changes nothing. True when this call wrote the trade.
 */
async function recordTrade(t) {
  const [r] = await pool.execute(
    `INSERT IGNORE INTO memeclaw_trades (${TRADE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [lower(t.tx_hash), Number(t.log_index), lower(t.token), lower(t.trader), t.is_buy ? 1 : 0,
      String(t.doge_amount), String(t.token_amount), String(t.doge_reserve), String(t.token_reserve),
      Number(t.block_number), t.block_time || null]);
  const wrote = !!(r && r.affectedRows);
  if (!wrote) return false;
  await pool.execute('UPDATE memeclaw_coins SET trade_count = trade_count + 1 WHERE token = ?', [lower(t.token)]);
  await pool.execute(
    'UPDATE memeclaw_coins SET doge_reserve = ?, token_reserve = ?, last_block = ?, last_log_index = ?, last_trade_at = ? '
    + 'WHERE token = ? AND (last_block IS NULL OR last_block < ? OR (last_block = ? AND last_log_index < ?))',
    [String(t.doge_reserve), String(t.token_reserve), Number(t.block_number), Number(t.log_index),
      t.block_time || null, lower(t.token), Number(t.block_number), Number(t.block_number), Number(t.log_index)]);
  return true;
}

/** The newest trades on a coin, newest first. */
async function trades(token) {
  const [rows] = await pool.execute(
    `SELECT ${TRADE_COLS} FROM memeclaw_trades WHERE token = ? ORDER BY block_number DESC, log_index DESC LIMIT 50`,
    [lower(token)]);
  return rows;
}

/** One trader's trades on a coin (the creator's, for the launch reading). */
async function tradesBy(token, trader) {
  const [rows] = await pool.execute(
    `SELECT ${TRADE_COLS} FROM memeclaw_trades WHERE token = ? AND trader = ?`, [lower(token), lower(trader)]);
  return rows;
}

module.exports = {
  savePlan, planFor, markPlanUsed, prunePlans,
  recordCoin, sealLaunch, coin, coinBySeal, coins, coinsBy, setHidden,
  recordTrade, trades, tradesBy,
};
