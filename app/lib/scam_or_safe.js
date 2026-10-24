'use strict';
/**
 * Scam or Safe? Daily — one decoded signing request per UTC day.
 *
 * The call is a Daily Duel pick. `sealDuelPick` (lib/callseal.js) seals it,
 * category `scam_or_safe`, pick `safe` or `risky`. There is no second seal.
 * The category is not one of lib/duel.js KINDS: those are the market card,
 * and this game must not add a round to it.
 *
 * DAY. The puzzle is `sha256("scam-or-safe:v0:" + UTC day)` as a uint32,
 * modulo the catalog length, in file order. The UTC day is `duel.dayKey`,
 * the same key the Daily Duel uses. Everyone gets the same puzzle that day.
 * Adding or reordering a puzzle changes which day lands on which id. A sealed
 * pick stores the id in the receipt's `symbol`, so a later reorder still
 * reveals the puzzle that was sealed, not whatever today maps to now.
 *
 * REVEAL. The offer never carries the verdict or the explanation. The seal
 * is written first. The explanation is attached only after
 * `sha256(seal_payload)` matches the stored seal. The words are the catalog's
 * (reviewed into the repo, so this feature holds no model key and signs
 * nothing). Every reveal names them with `AI_LABEL`, and `AI_NOTE` is the
 * coach's own disclosure sentence (public/js/pi-coach.js).
 *
 * ADDING A PUZZLE. Append one object to scam_or_safe_puzzles.json:
 *   id          `sos-` plus two unused letters
 *   chain       a `key` from lib/wallet.js CHAINS (parsed, not retyped)
 *   method      approve | permit | transfer | setApprovalForAll | increaseAllowance
 *   verdict     safe | risky
 *   fields      include Amount set to ▒▒▒ or [amount hidden]
 *   explanation two to four sentences
 * Addresses only as the made-up placeholder `0xEXAMPLE…` plus two digits
 * (`0xEXAMPLE…01`). A hex fragment such as `0x12ab…9f` is the start and end
 * of a real account and is refused. No digits outside that placeholder, no
 * URL, no full address, and none of: token, presale, price, $RCLAW, stake.
 * The audit in this file refuses the catalog otherwise.
 *
 * FLAG. `SCAM_OR_SAFE_V0` unset is off. On is 1, true, on, or yes.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const { sealDuelPick } = require('./callseal');
const duel = require('./duel');

const PUZZLES = path.join(__dirname, 'scam_or_safe_puzzles.json');
const WALLET_JS = path.join(__dirname, 'wallet.js');
const DAY_MS = 86400000;
const METHODS = Object.freeze(['approve', 'permit', 'transfer', 'setApprovalForAll', 'increaseAllowance']);
const BLANKS = Object.freeze(['\u2592\u2592\u2592', '[amount hidden]']);
const AI_LABEL = 'AI-written explanation';
const AI_NOTE = 'Education and practice, not advice.';
const MADE_UP = 'Made-up training example, not a check of any real contract or address.';
const KEY_RE = /^(?:[0-9a-f]{64}|w:[0-9a-f]{64})$/;
// The only account text a puzzle may carry. EXAMPLE is not hex, so the
// characters cannot be the start of a real address.
const MASK_RE = /0xEXAMPLE\u2026[0-9]{2}/g;
const REALISH_RE = /0x[a-f0-9]{2,8}\u2026[a-f0-9]{2,4}/i;
const BANNED_RE = /\b(tokens?|presales?|prices?|stakes?)\b|\$?rclaw|\$/i;
const URL_RE = /https?:\/\/|www\.|javascript:|walletconnect|metamask|\bwc:|\b[a-z0-9-]+\.(com|io|org|net|app|xyz|eth)\b/i;
const FULL_ADDR_RE = /0x[a-fA-F0-9]{40}/;

let _catalog = null;
const _dbs = new Map();

function enabled(env = process.env) {
  const v = String(env && env.SCAM_OR_SAFE_V0 != null ? env.SCAM_OR_SAFE_V0 : '').trim().toLowerCase();
  return v === '1' || v === 'true' || v === 'on' || v === 'yes';
}

function stateDir(env = process.env) {
  const raw = env && env.SCAM_OR_SAFE_STATE_DIR != null ? String(env.SCAM_OR_SAFE_STATE_DIR).trim() : '';
  if (raw) return path.resolve(raw);
  return path.join(__dirname, '..', '..', 'data', 'scam-or-safe');
}

/** Chain key → label, parsed from lib/wallet.js so this file does not keep a second list. */
function chainTable(src) {
  const text = src == null ? fs.readFileSync(WALLET_JS, 'utf8') : String(src);
  const code = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  const out = {};
  for (const m of code.matchAll(/key:\s*'([a-z0-9]+)',\s*label:\s*'([^']+)'/g)) out[m[1]] = m[2];
  return out;
}

function supportedChains() {
  return Object.keys(chainTable());
}

function sentenceCount(text) {
  const parts = String(text || '').split(/[.!?]+/).map((s) => s.trim()).filter(Boolean);
  return parts.length;
}

/**
 * Why one puzzle cannot ship, or []. A hit is a hard rule, named so a test
 * can require the reason and not merely a boolean.
 */
function problems(puzzle, chains = chainTable()) {
  const out = [];
  if (!puzzle || typeof puzzle !== 'object') return ['not an object'];
  const id = String(puzzle.id || '');
  if (!/^sos-[a-z]{2}$/.test(id)) out.push(id + ': id');
  if (!chains[puzzle.chain]) out.push(id + ': chain');
  if (METHODS.indexOf(puzzle.method) < 0) out.push(id + ': method');
  if (puzzle.verdict !== 'safe' && puzzle.verdict !== 'risky') out.push(id + ': verdict');
  const n = sentenceCount(puzzle.explanation);
  if (n < 2 || n > 4) out.push(id + ': explanation length ' + n);
  if (!Array.isArray(puzzle.fields) || puzzle.fields.length === 0) out.push(id + ': fields');
  const fields = Array.isArray(puzzle.fields) ? puzzle.fields : [];
  const amount = fields.find((f) => f && /^amount$/i.test(String(f.k)));
  if (!amount || BLANKS.indexOf(String(amount.v)) < 0) out.push(id + ': amount not blanked');
  const label = chains[puzzle.chain];
  const chainField = fields.find((f) => f && String(f.k) === 'Chain');
  if (label && (!chainField || chainField.v !== label)) out.push(id + ': chain label');
  const blobs = [id, puzzle.chain, puzzle.method, puzzle.verdict, puzzle.explanation]
    .concat(fields.map((f) => (f ? String(f.k) + ' ' + String(f.v) : '')));
  for (const blob of blobs) {
    const text = String(blob);
    if (BANNED_RE.test(text)) out.push(id + ': banned word');
    BANNED_RE.lastIndex = 0;
    if (URL_RE.test(text)) out.push(id + ': url');
    URL_RE.lastIndex = 0;
    if (FULL_ADDR_RE.test(text)) out.push(id + ': full address');
    if (REALISH_RE.test(text)) out.push(id + ': real-looking mask');
    REALISH_RE.lastIndex = 0;
    const residue = text.replace(MASK_RE, '');
    MASK_RE.lastIndex = 0;
    if (/\d/.test(residue) || /0x/i.test(residue)) out.push(id + ': amount or address');
  }
  return out;
}

function audit(list, chains = chainTable()) {
  const out = [];
  if (!Array.isArray(list)) return ['catalog is not a list'];
  if (list.length < 30) out.push('catalog has ' + list.length + ', need at least 30');
  const ids = new Set();
  const seenChains = new Set();
  const verdicts = new Set();
  for (const p of list) {
    if (p && ids.has(p.id)) out.push(p.id + ': duplicate id');
    if (p && p.id) ids.add(p.id);
    if (p && p.chain) seenChains.add(p.chain);
    if (p && p.verdict) verdicts.add(p.verdict);
    out.push(...problems(p, chains));
  }
  if (!verdicts.has('safe') || !verdicts.has('risky')) out.push('catalog needs both verdicts');
  for (const key of Object.keys(chains)) {
    if (!seenChains.has(key)) out.push('no puzzle for chain ' + key);
  }
  return out;
}

function catalog() {
  if (_catalog) return _catalog;
  const list = JSON.parse(fs.readFileSync(PUZZLES, 'utf8'));
  const found = audit(list);
  if (found.length) {
    const err = new Error('scam_or_safe catalog: ' + found.slice(0, 8).join('; '));
    err.code = 'CATALOG';
    throw err;
  }
  _catalog = list;
  return list;
}

function indexForDay(day, n) {
  const count = Number(n);
  if (!Number.isInteger(count) || count <= 0) return null;
  const buf = crypto.createHash('sha256').update('scam-or-safe:v0:' + String(day), 'utf8').digest();
  return buf.readUInt32BE(0) % count;
}

function puzzleForDay(day, list) {
  const puzzles = list || catalog();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day))) return null;
  const i = indexForDay(String(day), puzzles.length);
  return i == null ? null : puzzles[i];
}

function cutoffFor(day) {
  const start = duel.dayStartMs(day);
  if (!Number.isFinite(start)) return null;
  return new Date(start + DAY_MS).toISOString();
}

function prevDay(day) {
  const t = Date.parse(String(day) + 'T00:00:00Z');
  if (!Number.isFinite(t)) return null;
  return new Date(t - DAY_MS).toISOString().slice(0, 10);
}

function webPlayerKey(userId) {
  if (userId == null || userId === '') return null;
  return 'w:' + crypto.createHash('sha256').update('scam-or-safe:' + String(userId), 'utf8').digest('hex');
}

function hashOf(payload) {
  return crypto.createHash('sha256').update(String(payload), 'utf8').digest('hex');
}

function hashesEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length || a.length === 0) return false;
  return crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));
}

/** The sealed payload, parsed, or null when the hash or the shape does not match. */
function verifySeal(seal, sealPayload) {
  if (!hashesEqual(hashOf(sealPayload), String(seal || ''))) return null;
  let body;
  try { body = JSON.parse(sealPayload); } catch (e) { return null; }
  if (!body || body.kind !== 'duel_pick' || body.category !== 'scam_or_safe') return null;
  if (body.pick !== 'safe' && body.pick !== 'risky') return null;
  if (body.entry_price !== null || body.entry_price_b !== null) return null;
  if (typeof body.symbol !== 'string' || typeof body.day !== 'string') return null;
  return body;
}

function publicField(field) {
  const k = String(field && field.k || '');
  const v = String(field && field.v || '');
  if (/^amount$/i.test(k)) return { k, v: BLANKS.indexOf(v) >= 0 ? v : '\u2592\u2592\u2592' };
  const fake = /^0xEXAMPLE\u2026[0-9]{2}$/.test(v);
  if (!fake && (FULL_ADDR_RE.test(v) || /0x/i.test(v) || URL_RE.test(v) || BANNED_RE.test(v))) return { k, v: '[withheld]' };
  return { k, v };
}

function publicPuzzle(puzzle) {
  const chains = chainTable();
  return {
    id: puzzle.id,
    chain: puzzle.chain,
    chain_label: chains[puzzle.chain] || null,
    method: puzzle.method,
    fields: (puzzle.fields || []).map(publicField),
    read_only: true,
  };
}

function pctOf(correct, total) {
  if (typeof correct !== 'number' || typeof total !== 'number' || total <= 0) return null;
  return Math.round((correct * 1000) / total) / 10;
}

function openDb(dir) {
  const { DatabaseSync } = require('node:sqlite');
  fs.mkdirSync(dir, { recursive: true });
  const db = new DatabaseSync(path.join(dir, 'picks.sqlite'));
  db.exec(
    'CREATE TABLE IF NOT EXISTS picks ('
    + ' player_key TEXT NOT NULL,'
    + ' day TEXT NOT NULL,'
    + ' puzzle_id TEXT NOT NULL,'
    + ' pick TEXT NOT NULL,'
    + ' seal TEXT NOT NULL,'
    + ' seal_payload TEXT NOT NULL,'
    + ' picked_at TEXT NOT NULL,'
    + ' PRIMARY KEY (player_key, day))',
  );
  return db;
}

function dbFor(dir) {
  const key = path.resolve(dir);
  let db = _dbs.get(key);
  if (!db) {
    db = openDb(key);
    _dbs.set(key, db);
  }
  return db;
}

function rowFor(dir, playerKey, day) {
  const row = dbFor(dir).prepare(
    'SELECT player_key, day, puzzle_id, pick, seal, seal_payload, picked_at'
    + ' FROM picks WHERE player_key = ? AND day = ?',
  ).get(playerKey, day);
  return row || null;
}

function rowsForPlayer(dir, playerKey) {
  return dbFor(dir).prepare(
    'SELECT player_key, day, puzzle_id, pick, seal, seal_payload, picked_at'
    + ' FROM picks WHERE player_key = ?',
  ).all(playerKey);
}

function rowsForDay(dir, day) {
  return dbFor(dir).prepare(
    'SELECT player_key, day, puzzle_id, pick, seal, seal_payload, picked_at FROM picks WHERE day = ?',
  ).all(day);
}

function allRows(dir) {
  return dbFor(dir).prepare(
    'SELECT player_key, day, puzzle_id, pick, seal, seal_payload, picked_at FROM picks',
  ).all();
}

function insertOrFirst(dir, row) {
  const db = dbFor(dir);
  try {
    db.prepare(
      'INSERT INTO picks (player_key, day, puzzle_id, pick, seal, seal_payload, picked_at)'
      + ' VALUES (?, ?, ?, ?, ?, ?, ?)',
    ).run(row.player_key, row.day, row.puzzle_id, row.pick, row.seal, row.seal_payload, row.picked_at);
  } catch (e) {
    if (!e || !/UNIQUE/.test(String(e.message))) throw e;
  }
  const stored = rowFor(dir, row.player_key, row.day);
  if (!stored) throw new Error('store');
  return stored;
}

function puzzleById(list, id) {
  return list.find((p) => p.id === id) || null;
}

/** correct | wrong | unreadable for one stored row. */
function judge(row, list) {
  const body = verifySeal(row.seal, row.seal_payload);
  if (!body || body.symbol !== row.puzzle_id || body.pick !== row.pick || body.day !== row.day) {
    return { state: 'unreadable', puzzle: null };
  }
  const puzzle = puzzleById(list, body.symbol);
  if (!puzzle) return { state: 'unreadable', puzzle: null };
  return { state: body.pick === puzzle.verdict ? 'correct' : 'wrong', puzzle, body };
}

function rightShare(dir, day, list) {
  const rows = rowsForDay(dir, day);
  let correct = 0;
  let total = 0;
  let unreadable = 0;
  for (const row of rows) {
    const j = judge(row, list);
    if (j.state === 'unreadable') unreadable += 1;
    else {
      total += 1;
      if (j.state === 'correct') correct += 1;
    }
  }
  return { correct, total, pct: pctOf(correct, total), unreadable };
}

function streakFrom(judged, now) {
  const today = duel.dayKey(now);
  const by = new Map(judged.map((j) => [j.day, j.state]));
  function runEnding(anchor) {
    let n = 0;
    let cursor = anchor;
    while (by.get(cursor) === 'correct') {
      n += 1;
      cursor = prevDay(cursor);
      if (!cursor) break;
    }
    return n;
  }
  const state = by.get(today);
  let current = null;
  let active_today = false;
  let unreadable = false;
  if (state === 'unreadable') {
    current = null;
    active_today = false;
    unreadable = true;
  } else if (state === 'wrong') {
    current = 0;
    active_today = true;
  } else if (state === 'correct') {
    current = runEnding(today);
    active_today = true;
  } else {
    const y = prevDay(today);
    const ys = y ? by.get(y) : null;
    if (ys === 'unreadable') unreadable = true;
    else if (ys === 'correct') current = runEnding(y);
    else current = 0;
    active_today = false;
  }
  const days = [...by.keys()].sort();
  let best = null;
  let run = 0;
  let prev = null;
  let anyRead = false;
  for (const day of days) {
    const st = by.get(day);
    if (st === 'unreadable') { run = 0; prev = null; continue; }
    anyRead = true;
    if (st === 'correct' && prev && prevDay(day) === prev) run += 1;
    else run = st === 'correct' ? 1 : 0;
    prev = st === 'correct' ? day : null;
    if (best == null || run > best) best = run;
  }
  if (!anyRead) best = unreadable ? null : 0;
  else if (best == null) best = 0;
  return { current, best, active_today, unreadable };
}

function streakFor(dir, playerKey, now, list) {
  const judged = rowsForPlayer(dir, playerKey).map((row) => {
    const j = judge(row, list);
    return { day: row.day, state: j.state };
  });
  return streakFrom(judged, now);
}

function rankFor(dir, playerKey, now, list) {
  const grouped = new Map();
  for (const row of allRows(dir)) {
    if (!grouped.has(row.player_key)) grouped.set(row.player_key, []);
    grouped.get(row.player_key).push(row);
  }
  const scores = [];
  let mine = null;
  for (const [key, rows] of grouped) {
    const s = streakFrom(rows.map((row) => ({ day: row.day, state: judge(row, list).state })), now);
    if (s.current == null) {
      if (key === playerKey) mine = null;
      continue;
    }
    scores.push(s.current);
    if (key === playerKey) mine = s.current;
  }
  if (mine == null) return { place: null, players: scores.length };
  let ahead = 0;
  for (const n of scores) if (n > mine) ahead += 1;
  return { place: ahead + 1, players: scores.length };
}

function revealRow(row, list, now, dir) {
  const j = judge(row, list);
  const verified = j.state !== 'unreadable';
  const puzzle = verified ? j.puzzle : puzzleById(list, row.puzzle_id);
  const base = {
    ok: true,
    game: 'scam_or_safe',
    day: row.day,
    cutoff_at: cutoffFor(row.day),
    day_basis: 'UTC',
    sealed: true,
    verified,
    pick: verified ? j.body.pick : null,
    puzzle: puzzle ? publicPuzzle(puzzle) : null,
    seal: row.seal,
    seal_payload: row.seal_payload,
    picked_at: row.picked_at,
  };
  if (!verified || !puzzle) {
    return Object.assign(base, {
      verdict: null,
      correct: null,
      explanation: null,
      ai_label: null,
      ai_note: null,
      made_up: MADE_UP,
      right: rightShare(dir, row.day, list),
      streak: { current: null, best: null, active_today: false, unreadable: true },
      rank: { place: null, players: null },
    });
  }
  return Object.assign(base, {
    verdict: puzzle.verdict,
    correct: j.state === 'correct',
    explanation: puzzle.explanation,
    ai_label: AI_LABEL,
    ai_note: AI_NOTE,
    made_up: MADE_UP,
    right: rightShare(dir, row.day, list),
    streak: streakFor(dir, row.player_key, now, list),
    rank: rankFor(dir, row.player_key, now, list),
  });
}

function clockOf(input) {
  const now = input && input.now ? new Date(input.now) : new Date();
  if (Number.isNaN(now.getTime())) return null;
  return now;
}

function admitted(input, env) {
  return enabled(env) || (input != null && input.caller_is_admin === true);
}

function offer(input, env = process.env) {
  if (!admitted(input, env)) return { ok: false, error: 'off' };
  const now = clockOf(input);
  if (!now) return { ok: false, error: 'clock' };
  const day = duel.dayKey(now);
  let list;
  try { list = catalog(); } catch (e) { return { ok: false, error: 'unreadable' }; }
  const puzzle = puzzleForDay(day, list);
  if (!puzzle) return { ok: false, error: 'unreadable', day };
  const dir = stateDir(env);
  const key = input && input.player_key ? String(input.player_key) : '';
  if (KEY_RE.test(key)) {
    const existing = rowFor(dir, key, day);
    if (existing) return revealRow(existing, list, now, dir);
  }
  return {
    ok: true,
    game: 'scam_or_safe',
    day,
    cutoff_at: cutoffFor(day),
    day_basis: 'UTC',
    sealed: false,
    verified: false,
    puzzle: publicPuzzle(puzzle),
  };
}

function sealPick(input, env = process.env) {
  if (!admitted(input, env)) return { ok: false, error: 'off' };
  const src = input || {};
  const now = clockOf(src);
  if (!now) return { ok: false, error: 'clock' };
  const day = duel.dayKey(now);
  if (src.day && String(src.day) !== day) return { ok: false, error: 'closed', day, cutoff_at: cutoffFor(String(src.day)) };
  const pick = String(src.pick || '').trim().toLowerCase();
  if (pick !== 'safe' && pick !== 'risky') return { ok: false, error: 'pick' };
  const key = String(src.player_key || '');
  if (!KEY_RE.test(key)) return { ok: false, error: 'player' };
  let list;
  try { list = catalog(); } catch (e) { return { ok: false, error: 'unreadable' }; }
  const puzzle = puzzleForDay(day, list);
  if (!puzzle) return { ok: false, error: 'unreadable', day, cutoff_at: cutoffFor(day) };
  if (src.puzzle_id && String(src.puzzle_id) !== puzzle.id) {
    return { ok: false, error: 'moved', day, puzzle_id: puzzle.id, cutoff_at: cutoffFor(day) };
  }
  const dir = stateDir(env);
  const existing = rowFor(dir, key, day);
  if (existing) return revealRow(existing, list, now, dir);
  const picked_at = now.toISOString();
  const facts = {
    day,
    kind: 'scam_or_safe',
    symbol: puzzle.id,
    symbol_b: null,
    threshold_pct: null,
    handle: null,
    pick,
    entry_price: null,
    entry_price_b: null,
    resolves_at: cutoffFor(day),
    picked_at,
  };
  const receipt = sealDuelPick(facts);
  const stored = insertOrFirst(dir, {
    player_key: key,
    day,
    puzzle_id: puzzle.id,
    pick,
    seal: receipt.seal,
    seal_payload: receipt.seal_payload,
    picked_at,
  });
  return revealRow(stored, list, now, dir);
}

module.exports = {
  AI_LABEL, AI_NOTE, MADE_UP, BLANKS, METHODS, PUZZLES,
  enabled, stateDir, chainTable, supportedChains,
  problems, audit, catalog, indexForDay, puzzleForDay, cutoffFor, prevDay,
  webPlayerKey, hashOf, verifySeal, publicPuzzle, publicField, pctOf,
  offer, sealPick, streakFrom, sentenceCount,
};
