'use strict';
/**
 * Memeclaw's practice mode: launch and trade coins with practice DOGE, with
 * no wallet and no chain (owner, 21 October 2026: "all just testnet and paper
 * for now").
 *
 * THE SAME CURVE. A practice coin is priced by lib/memeclaw_curve.js, which
 * the contracts suite drives against the real factory's bytecode, so a
 * practice trade pays what the same trade on the testnet would. Practice
 * DOGE is a number in this database and nothing else: it cannot be bought,
 * sold, sent or withdrawn, and no practice coin exists on any chain.
 *
 * NO ACCOUNT. A practice player is a random key the page keeps in the
 * browser; the server keeps only its SHA-256. Losing the key loses the
 * practice balance, and the page says so.
 *
 * ATOMIC. A trade reads the coin, the player and the holding FOR UPDATE in
 * one transaction (db.withTransaction) and writes all of them or none, so
 * two trades on one coin cannot both price against the same reserves.
 * Locks are always taken coin, then player, then holding.
 *
 * THE PAGE'S RULES. A practice coin's name, symbol and description pass the
 * same rules as a testnet launch (lib/memeclaw.pageProblem): a practice
 * stock is still a stock.
 */
const crypto = require('crypto');
const { pool, withTransaction } = require('../db');
const mc = require('./memeclaw');
const curve = require('./memeclaw_curve');

const START_DOGE = 100n * curve.E18;
const REFILL_BELOW = 1n * curve.E18;
const REFILL_EVERY_MS = 24 * 3600 * 1000;
const LAUNCHES_PER_DAY = 5;
const KEY_RE = /^[0-9a-f]{64}$/;
const ID_RE = /^p[0-9a-f]{16}$/;
const PLAYER_RE = /^[0-9a-f]{16}$/;

const big = (v) => BigInt(String(v == null ? '0' : v).split('.')[0]);
const hashKey = (key) => crypto.createHash('sha256').update(String(key), 'utf8').digest('hex');

class PaperRefusal extends Error {
  constructor(reason, extra) {
    super(reason);
    this.name = 'PaperRefusal';
    this.reason = reason;
    this.extra = extra || {};
  }
}

const PLAYER_COLS = 'id, key_hash, doge, launches_today, launches_day, refilled_at, created_at';
const COIN_COLS = 'id, name, symbol, description, creator, doge_reserve, token_reserve, trade_count, created_at, last_trade_at, hidden_at, hidden_reason';
const TRADE_COLS = 'id, coin, player, is_buy, doge_amount, token_amount, doge_reserve, token_reserve, created_at';

// ── players ─────────────────────────────────────────────────────────────────

/** A new practice player: { key, player }. The key is shown once, to its browser. */
async function newPlayer(now = new Date()) {
  const key = crypto.randomBytes(32).toString('hex');
  const id = crypto.randomBytes(8).toString('hex');
  await pool.execute(
    `INSERT INTO memeclaw_paper_players (${PLAYER_COLS}) VALUES (?, ?, ?, 0, NULL, NULL, ?)`,
    [id, hashKey(key), START_DOGE.toString(), now]);
  return { key, player: { id, doge: START_DOGE.toString() } };
}

/** The player a key belongs to, or null. */
async function playerFor(key) {
  if (!KEY_RE.test(String(key || ''))) return null;
  const [rows] = await pool.execute(`SELECT ${PLAYER_COLS} FROM memeclaw_paper_players WHERE key_hash = ?`, [hashKey(key)]);
  return rows[0] || null;
}

async function holdingsOf(playerId) {
  const [rows] = await pool.execute(
    'SELECT coin, amount FROM memeclaw_paper_holdings WHERE player = ?', [playerId]);
  return rows.filter((r) => big(r.amount) > 0n).map((r) => ({ coin: r.coin, amount: String(big(r.amount)) }));
}

/** Forget a player: the key, the balance and the holdings. Coins and trades stay, under the anonymous id. */
async function forget(playerId) {
  await pool.execute('DELETE FROM memeclaw_paper_holdings WHERE player = ?', [playerId]);
  const [r] = await pool.execute('DELETE FROM memeclaw_paper_players WHERE id = ?', [playerId]);
  return !!(r && r.affectedRows);
}

/**
 * Top a player back up to START_DOGE, once a day, and only when nearly
 * empty: practice money for practising, not a balance to farm.
 */
async function refill(playerId, now = new Date()) {
  return withTransaction(async (conn) => {
    const [rows] = await conn.execute(`SELECT ${PLAYER_COLS} FROM memeclaw_paper_players WHERE id = ? FOR UPDATE`, [playerId]);
    const p = rows[0];
    if (!p) throw new PaperRefusal('unknown_player');
    if (big(p.doge) >= REFILL_BELOW) throw new PaperRefusal('refill_not_needed');
    if (p.refilled_at && now.getTime() - new Date(p.refilled_at).getTime() < REFILL_EVERY_MS) {
      throw new PaperRefusal('refill_too_soon', { next_at: new Date(new Date(p.refilled_at).getTime() + REFILL_EVERY_MS).toISOString() });
    }
    await conn.execute('UPDATE memeclaw_paper_players SET doge = ?, refilled_at = ? WHERE id = ?', [START_DOGE.toString(), now, playerId]);
    return START_DOGE.toString();
  });
}

// ── coins and trades ────────────────────────────────────────────────────────

async function lockCoin(conn, id) {
  const [rows] = await conn.execute(`SELECT ${COIN_COLS} FROM memeclaw_paper_coins WHERE id = ? FOR UPDATE`, [id]);
  return rows[0] || null;
}
async function lockPlayer(conn, id) {
  const [rows] = await conn.execute(`SELECT ${PLAYER_COLS} FROM memeclaw_paper_players WHERE id = ? FOR UPDATE`, [id]);
  return rows[0] || null;
}
async function lockHolding(conn, player, coin) {
  const [rows] = await conn.execute(
    'SELECT amount FROM memeclaw_paper_holdings WHERE player = ? AND coin = ? FOR UPDATE', [player, coin]);
  return rows[0] ? big(rows[0].amount) : 0n;
}

/** Apply one priced trade inside `conn`: balances, reserves, holding, and the trade row. */
async function writeTrade(conn, { coin, player, isBuy, dogeAmount, tokenAmount, state, held, doge, now }) {
  await conn.execute('UPDATE memeclaw_paper_players SET doge = ? WHERE id = ?', [doge.toString(), player]);
  await conn.execute(
    'UPDATE memeclaw_paper_coins SET doge_reserve = ?, token_reserve = ?, trade_count = trade_count + 1, last_trade_at = ? WHERE id = ?',
    [state.dogeReserve.toString(), state.tokenReserve.toString(), now, coin]);
  await conn.execute(
    'INSERT INTO memeclaw_paper_holdings (player, coin, amount) VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE amount = VALUES(amount)',
    [player, coin, held.toString()]);
  await conn.execute(
    'INSERT INTO memeclaw_paper_trades (coin, player, is_buy, doge_amount, token_amount, doge_reserve, token_reserve, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [coin, player, isBuy ? 1 : 0, dogeAmount.toString(), tokenAmount.toString(),
      state.dogeReserve.toString(), state.tokenReserve.toString(), now]);
}

/**
 * Launch a practice coin, with an optional first buy for its creator.
 * Returns the coin's id. Throws PaperRefusal for what the page will not take.
 */
async function launch(playerId, { name, symbol, description, devBuy = '0' }, now = new Date()) {
  const desc = mc.cleanDescription(description);
  const problem = mc.pageProblem({ name, symbol, description: desc });
  if (problem) throw new PaperRefusal(problem.reason, problem.detail ? { detail: problem.detail } : {});
  if (!mc.WEI_RE.test(String(devBuy))) throw new PaperRefusal('dev_buy_amount');
  const value = BigInt(devBuy);
  const fresh = { dogeReserve: 0n, tokenReserve: curve.SUPPLY };
  if (value > curve.maxBuy(fresh)) throw new PaperRefusal('dev_buy_too_large');
  const id = 'p' + crypto.randomBytes(8).toString('hex');
  const day = now.toISOString().slice(0, 10);
  await withTransaction(async (conn) => {
    const p = await lockPlayer(conn, playerId);
    if (!p) throw new PaperRefusal('unknown_player');
    const today = p.launches_day === day ? Number(p.launches_today) : 0;
    if (today >= LAUNCHES_PER_DAY) throw new PaperRefusal('launches_today', { limit: LAUNCHES_PER_DAY });
    if (big(p.doge) < value) throw new PaperRefusal('not_enough_doge', { doge: String(big(p.doge)) });
    await conn.execute('UPDATE memeclaw_paper_players SET launches_today = ?, launches_day = ? WHERE id = ?', [today + 1, day, playerId]);
    await conn.execute(
      `INSERT INTO memeclaw_paper_coins (${COIN_COLS}) VALUES (?, ?, ?, ?, ?, '0', ?, 0, ?, NULL, NULL, NULL)`,
      [id, name, symbol, desc, playerId, curve.SUPPLY.toString(), now]);
    if (value > 0n) {
      const out = curve.quoteBuy(fresh, value);
      if (!out) throw new PaperRefusal('dev_buy_too_small');
      await writeTrade(conn, { coin: id, player: playerId, isBuy: true, dogeAmount: value, tokenAmount: out,
        state: { dogeReserve: value, tokenReserve: curve.SUPPLY - out }, held: out, doge: big(p.doge) - value, now });
    }
  });
  return id;
}

/**
 * A practice buy or sell at the coin's current reserves. `minOut` is the
 * floor the page quoted; a trade that would pay less (someone traded first)
 * is refused rather than filled at a price the player did not see.
 * Returns { gets, doge, held }.
 */
async function trade(playerId, { coin, action, amount, minOut = '0' }, now = new Date()) {
  if (!ID_RE.test(String(coin || ''))) throw new PaperRefusal('coin_address');
  if (!mc.WEI_RE.test(String(amount)) || BigInt(amount) === 0n) throw new PaperRefusal('amount');
  if (!mc.WEI_RE.test(String(minOut))) throw new PaperRefusal('amount');
  if (action !== 'buy' && action !== 'sell') throw new PaperRefusal('action');
  const value = BigInt(amount);
  const floor = BigInt(minOut);
  return withTransaction(async (conn) => {
    const c = await lockCoin(conn, coin);
    if (!c || c.hidden_at) throw new PaperRefusal('unknown_coin');
    const p = await lockPlayer(conn, playerId);
    if (!p) throw new PaperRefusal('unknown_player');
    const held = await lockHolding(conn, playerId, coin);
    const state = { dogeReserve: big(c.doge_reserve), tokenReserve: big(c.token_reserve) };
    const doge = big(p.doge);
    if (action === 'buy') {
      if (doge < value) throw new PaperRefusal('not_enough_doge', { doge: doge.toString() });
      const out = curve.quoteBuy(state, value);
      if (out === null) throw new PaperRefusal('sold_out', { max_buy: curve.maxBuy(state).toString() });
      if (out === 0n) throw new PaperRefusal('too_small');
      if (out < floor) throw new PaperRefusal('slippage', { gets: out.toString() });
      const next = { dogeReserve: state.dogeReserve + value, tokenReserve: state.tokenReserve - out };
      await writeTrade(conn, { coin, player: playerId, isBuy: true, dogeAmount: value, tokenAmount: out,
        state: next, held: held + out, doge: doge - value, now });
      return { gets: out.toString(), doge: (doge - value).toString(), held: (held + out).toString() };
    }
    if (held < value) throw new PaperRefusal('not_held', { held: held.toString() });
    const out = curve.quoteSell(state, value);
    if (out === 0n) throw new PaperRefusal('too_small');
    if (out > state.dogeReserve) throw new PaperRefusal('too_small'); // cannot happen on this curve; never pay what is not there
    if (out < floor) throw new PaperRefusal('slippage', { gets: out.toString() });
    const next = { dogeReserve: state.dogeReserve - out, tokenReserve: state.tokenReserve + value };
    await writeTrade(conn, { coin, player: playerId, isBuy: false, dogeAmount: out, tokenAmount: value,
      state: next, held: held - value, doge: doge + out, now });
    return { gets: out.toString(), doge: (doge + out).toString(), held: (held - value).toString() };
  });
}

// ── reads ───────────────────────────────────────────────────────────────────

async function coins() {
  const [rows] = await pool.execute(`SELECT ${COIN_COLS} FROM memeclaw_paper_coins ORDER BY created_at DESC LIMIT 500`);
  return rows;
}
async function coin(id) {
  if (!ID_RE.test(String(id || ''))) return null;
  const [rows] = await pool.execute(`SELECT ${COIN_COLS} FROM memeclaw_paper_coins WHERE id = ?`, [id]);
  return rows[0] || null;
}
async function trades(id) {
  const [rows] = await pool.execute(
    `SELECT ${TRADE_COLS} FROM memeclaw_paper_trades WHERE coin = ? ORDER BY id DESC LIMIT 50`, [id]);
  return rows;
}
async function setHidden(id, reason, now = new Date()) {
  const [r] = await pool.execute('UPDATE memeclaw_paper_coins SET hidden_at = ?, hidden_reason = ? WHERE id = ?',
    [reason ? now : null, reason ? String(reason).slice(0, 120) : null, id]);
  return !!(r && r.affectedRows);
}

module.exports = {
  START_DOGE, REFILL_BELOW, REFILL_EVERY_MS, LAUNCHES_PER_DAY, KEY_RE, ID_RE, PLAYER_RE, PaperRefusal, hashKey,
  newPlayer, playerFor, holdingsOf, forget, refill, launch, trade, coins, coin, trades, setHidden,
};
