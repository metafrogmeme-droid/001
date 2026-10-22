'use strict';
/**
 * Ask the Claw, the website's half: who may ask, how many times a day, and
 * the facts the coach is handed.
 *
 * The coach itself is the bot's (bot/web/pi_coach.py, the gateway's
 * /chat/pi-coach): an account-free, tool-free model turn with its own prompt,
 * a fixed answer for token questions (and for a price question when no live
 * price could be read), and an output check.
 * This side decides the rest, because it holds the account:
 *
 *   ALLOWANCE. A Pi account asks up to `free` questions a UTC day, or `plus`
 *   while a Coach Plus payment completed within its window (pi_payments,
 *   kind coach_plus). The count is a row per person per day
 *   (pi_coach_usage), taken before the model runs and given back when no
 *   model answered: a fixed sentence or an outage spends nothing. The row is
 *   in the database, not in memory, so a restart or a second instance does
 *   not hand out a second allowance.
 *
 *   FACTS. Read here for this person, never taken from the page: their open
 *   practice positions and last closed trades, today's Duel card as
 *   /api/duel/today draws it for them (so a stance still sealed stays
 *   sealed: the card withholds it until they call, and the coach is handed
 *   the card), and the one signal or round they asked "Why?" about. Credits,
 *   not money; percent and counts where the board would show them.
 *
 *   LIVE PRICES. For the coins this question is about (the card's, ones it
 *   names, open positions', today's rounds'), read from the same ticker the
 *   Arena prices trades with, with the time they were read. Market prices
 *   are public market facts; a read that failed is said, never a zero.
 */

const { pool } = require('../db');

const COACH_PLUS_KIND = 'coach_plus';

function intEnv(env, name, dflt, lo, hi) {
  const raw = String((env || {})[name] || '').trim();
  if (!/^\d+$/.test(raw)) return dflt;
  const n = Number(raw);
  return n >= lo && n <= hi ? n : dflt;
}

/** The two daily allowances, from PI_COACH_DAILY and PI_COACH_PLUS_DAILY. */
function limits(env = process.env) {
  const free = intEnv(env, 'PI_COACH_DAILY', 20, 1, 1000);
  const plus = intEnv(env, 'PI_COACH_PLUS_DAILY', 100, 1, 5000);
  return { free, plus: Math.max(plus, free) };
}

/**
 * Coach Plus as offered: { amount, days } in Pi, or null when it is not
 * offered (PI_COACH_PLUS_PI unset, or not a positive amount of at most 100 Pi
 * with at most 7 decimals). Off unless the operator sets a price.
 */
function plusOffer(env = process.env) {
  const raw = String(env.PI_COACH_PLUS_PI || '').trim();
  if (!/^\d+(\.\d{1,7})?$/.test(raw)) return null;
  const amount = Number(raw);
  if (!(amount > 0 && amount <= 100)) return null;
  return { amount, days: plusDays(env) };
}

/** How long a completed Coach Plus runs (PI_COACH_PLUS_DAYS, default 30). */
function plusDays(env = process.env) {
  return intEnv(env, 'PI_COACH_PLUS_DAYS', 30, 1, 365);
}

function utcDay(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

/**
 * When this person's Coach Plus ends, or null when they have none running.
 * A payment counts once it is COMPLETED (Pi's rule: an incomplete payment
 * delivers nothing), and its window runs from that completion.
 */
async function plusUntil(userId, days, now = new Date()) {
  if (!(days > 0)) return null;
  const [rows] = await pool.execute(
    'SELECT updated_at FROM pi_payments WHERE user_id = ? AND kind = ? AND status = ? ORDER BY updated_at DESC LIMIT 1',
    [userId, COACH_PLUS_KIND, 'completed']);
  const at = rows[0] && rows[0].updated_at ? new Date(rows[0].updated_at) : null;
  if (!at || Number.isNaN(at.getTime())) return null;
  const until = new Date(at.getTime() + days * 86400000);
  return until > now ? until : null;
}

async function usedOn(userId, day) {
  const [rows] = await pool.execute(
    'SELECT count FROM pi_coach_usage WHERE user_id = ? AND day = ?', [userId, day]);
  return rows[0] ? Number(rows[0].count) || 0 : 0;
}

/**
 * Take one question from today's allowance. True when taken. The UPDATE's
 * `count < ?` is the cap, so two requests at the limit cannot both pass; the
 * first question of a day inserts the row, and a racing insert falls back to
 * the guarded update.
 */
async function take(userId, day, limit) {
  const [r] = await pool.execute(
    'UPDATE pi_coach_usage SET count = count + 1 WHERE user_id = ? AND day = ? AND count < ?',
    [userId, day, limit]);
  if (r.affectedRows === 1) return true;
  if (await usedOn(userId, day) > 0) return false;
  try {
    await pool.execute('INSERT INTO pi_coach_usage (user_id, day, count) VALUES (?, ?, ?)', [userId, day, 1]);
    return limit >= 1;
  } catch (err) {
    if (!(err && (err.code === 'ER_DUP_ENTRY' || err.errno === 1062))) throw err;
    const [again] = await pool.execute(
      'UPDATE pi_coach_usage SET count = count + 1 WHERE user_id = ? AND day = ? AND count < ?',
      [userId, day, limit]);
    return again.affectedRows === 1;
  }
}

/** Give a question back: no model answered it. */
async function giveBack(userId, day) {
  await pool.execute(
    'UPDATE pi_coach_usage SET count = count - 1 WHERE user_id = ? AND day = ? AND count > 0', [userId, day]);
}

// ── facts ───────────────────────────────────────────────────────────────

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function iso(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '—' : d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}
function two(v) { const n = num(v); return n === null ? '—' : n.toFixed(2); }

function positionLine(p) {
  return `${p.symbol} ${String(p.direction || '').toUpperCase()} ${num(p.leverage) === null ? '' : p.leverage + 'x '}`
    + `entry ${num(p.entry) === null ? '—' : p.entry}, ${two(p.margin)} credits, opened ${iso(p.opened_at)}`
    + (num(p.sl) !== null ? `, stop ${p.sl}` : '') + (num(p.tp) !== null ? `, target ${p.tp}` : '');
}

function closedLine(t) {
  const margin = num(t.margin);
  const pnl = num(t.pnl);
  const pct = margin && pnl !== null ? ` (${pnl >= 0 ? '+' : ''}${(pnl / margin * 100).toFixed(1)}% on margin)` : '';
  return `${t.symbol} ${String(t.direction || '').toUpperCase()} ${num(t.leverage) === null ? '' : t.leverage + 'x '}`
    + `entry ${t.entry} exit ${t.exit_price}, result ${pnl === null ? '—' : (pnl >= 0 ? '+' : '') + pnl.toFixed(2)} credits${pct}, `
    + `closed by ${t.reason || '—'} at ${iso(t.closed_at)}`;
}

function roundLine(r) {
  const D = require('../public/js/duel.js');
  const kind = r.kind || 'direction';
  const what = kind === 'mover' ? `${r.symbol} vs ${r.symbol_b}`
    : kind === 'sports' && r.match ? `${r.match.home} vs ${r.match.away}` : r.symbol;
  let line = `Round ${r.id} (${kind}) ${what}: ${D.questionText(r)}`;
  if (r.my_call) {
    line += ` You called ${String(r.my_call.pick).toUpperCase()}`;
    if (r.my_result && r.my_result.state) line += `, result ${r.my_result.state}`;
    else line += ', not settled yet';
  } else {
    line += r.callable ? ' Not called yet.' : ' Closed, not called.';
  }
  // Only what the card itself shows: the Claw's stance is on the card once
  // this person has called (or always, on a back/fade round).
  if ('agent_direction' in r) {
    line += ` The Claw's call: ${r.agent_direction == null ? 'pass' : String(r.agent_direction).toUpperCase()}.`;
  }
  return line;
}

/**
 * The head of the live-prices line. The bot's coach reads it to know the
 * facts carry prices (bot/web/pi_coach.py LIVE_PRICES_HEAD, held equal by a
 * test): with it, a price question goes to the model; without it, the coach
 * says it could not read one. A read that failed writes a different line.
 */
const LIVE_PRICES_HEAD = 'Live prices (Bitget USDT-M futures), read ';
/** The most symbols one question is handed prices for. */
const LIVE_MAX = 6;
/** How long a question waits for the ticker read before taking the last one known. */
const LIVE_BUDGET_MS = 2500;

/** A stored symbol as the ticker map spells it: 'BTC/USDT:USDT' and 'btcusdt' are 'BTCUSDT'. */
function tickerSymbol(s) {
  return String(s || '').toUpperCase().replace(/:USDT$/, '').replace('/', '');
}

/** Coins a question names, as the ticker map spells them (LTC, ltc, LTCUSDT -> LTCUSDT). */
function namedSymbols(text, map) {
  const out = [];
  for (const w of String(text || '').toUpperCase().match(/[A-Z0-9]{2,15}/g) || []) {
    const sym = w.endsWith('USDT') && map[w] ? w : map[w + 'USDT'] ? w + 'USDT' : null;
    if (sym && !out.includes(sym)) out.push(sym);
  }
  return out;
}

/**
 * The live-prices line for the symbols this question is about, from the one
 * ticker reader the Arena prices trades with (lib/tickers.js). A read that
 * failed is said; a symbol the venue does not list is said; a price is
 * printed as the venue gave it, with its 24h change when that was read, and
 * the line says when it was read. Absent symbols leave the line out.
 */
async function livePrices(symbols, text, deps = {}) {
  const tickers = deps.tickers || require('./tickers');
  let read;
  try { read = await tickers.getTickersWithin(LIVE_BUDGET_MS); } catch (e) { read = null; }
  const failed = !read || !Number.isFinite(read.ageMs);
  const map = !failed && read.map && typeof read.map === 'object' ? read.map : {};
  // With no map, a coin the question names can only be recognised when it is
  // spelled as a pair; the failure is still said for it, never dropped.
  const named = failed
    ? (String(text || '').toUpperCase().match(/\b[A-Z0-9]{2,15}USDT\b/g) || [])
    : namedSymbols(text, map);
  const want = [];
  for (const s of [...named, ...symbols]) {
    if (s && !want.includes(s) && want.length < LIVE_MAX) want.push(s);
  }
  if (!want.length) return null;
  if (failed) return 'Live prices: could not be read just now.';
  const at = new Date((deps.now ? deps.now() : Date.now()) - read.ageMs);
  const age = read.ageMs > 60000 ? ` (${Math.round(read.ageMs / 60000)} min old)` : '';
  const lines = want.map((sym) => {
    const t = map[sym];
    const price = t ? num(t.price) : null;
    if (price === null) return `- ${sym}: no price read`;
    const change = num(t.change);
    return `- ${sym} ${price}` + (change === null ? ', 24h change not read' : `, 24h ${change >= 0 ? '+' : ''}${change.toFixed(2)}%`);
  });
  return LIVE_PRICES_HEAD + iso(at) + age + ':\n' + lines.join('\n');
}

/**
 * The facts block for one question. Each source is read on its own; one that
 * cannot be read is said to be unreadable, never left out as if empty, and
 * never fails the question (the coach can still explain a stop-loss).
 */
async function facts(userId, about, deps = {}, text = '') {
  const arena = deps.arena || require('../routes/arena');
  const duel = deps.duel || require('./duel_service');
  const out = [];
  const symbols = [];
  const note = (s) => { const t = typeof s === 'string' ? tickerSymbol(s) : ''; if (t && !symbols.includes(t)) symbols.push(t); };

  try {
    const positions = await arena.loadPositions(userId);
    positions.forEach((p) => note(p.symbol));
    out.push(positions.length
      ? 'Open practice positions:\n' + positions.slice(0, 6).map((p) => '- ' + positionLine(p)).join('\n')
      : 'Open practice positions: none.');
  } catch (e) {
    out.push('Open practice positions: could not be read just now.');
  }
  try {
    const [rows] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30',
      [userId]);
    out.push(rows.length
      ? 'Last closed practice trades:\n' + rows.slice(0, 3).map((t) => '- ' + closedLine(t)).join('\n')
      : 'Closed practice trades: none yet.');
  } catch (e) {
    out.push('Closed practice trades: could not be read just now.');
  }
  let card = null;
  try {
    card = await duel.cardFor(userId);
    const rounds = Array.isArray(card && card.rounds) ? card.rounds : [];
    rounds.forEach((r) => { if (r.kind !== 'sports') { note(r.symbol); note(r.symbol_b); } });
    out.push(rounds.length
      ? `Today's Daily Duel (${card.day} UTC):\n` + rounds.map((r) => '- ' + roundLine(r)).join('\n')
      : 'Today\'s Daily Duel: no rounds on the card.');
  } catch (e) {
    out.push('Today\'s Daily Duel: could not be read just now.');
  }

  const a = about && typeof about === 'object' ? about : null;
  if (a && a.kind === 'signal' && typeof a.key === 'string' && a.key.length <= 128) {
    try {
      const [rows] = await pool.execute(
        'SELECT id, signal_key, symbol, direction, confidence, regime, entry_price, stop_loss, take_profit, status, created_at, expires_at FROM signals WHERE signal_key = ?',
        [a.key]);
      const s = rows[0];
      // The card's coin first: it is the one the question is about.
      if (s) symbols.unshift(tickerSymbol(s.symbol));
      out.push(s
        ? `The signal card they asked about: ${s.symbol} ${s.direction}, entry ${s.entry_price}, stop ${s.stop_loss}, `
          + `target ${s.take_profit}, regime ${s.regime || '—'}, status ${s.status}, made ${iso(s.created_at)}.`
        : 'The signal card they asked about could not be found.');
    } catch (e) {
      out.push('The signal card they asked about could not be read just now.');
    }
  } else if (a && a.kind === 'round' && Number.isInteger(a.id) && card && Array.isArray(card.rounds)) {
    const r = card.rounds.find((x) => x.id === a.id);
    out.push(r ? 'The round they asked about: ' + roundLine(r) : 'The round they asked about is not on today\'s card.');
  }
  const live = await livePrices(symbols, text, deps);
  if (live) out.push(live);
  return out.join('\n');
}

module.exports = { limits, plusOffer, plusDays, plusUntil, utcDay, usedOn, take, giveBack, facts, livePrices, namedSymbols, tickerSymbol,
  COACH_PLUS_KIND, LIVE_PRICES_HEAD, LIVE_MAX };
