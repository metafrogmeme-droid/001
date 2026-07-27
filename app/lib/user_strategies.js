'use strict';
/**
 * User-authored strategies — a member builds a strategy from the SAME intent
 * vocabulary the engine's risk gate speaks (direction, min confidence, min R:R,
 * size caps, symbol allow/deny…), saves drafts, and publishes to the community
 * marketplace for others to browse.
 *
 * §4 by construction: a strategy here is a CONFIG (rule chips + prose), never a
 * performance claim — there are NO dollar fields and NO stats/scorecard (a
 * verified backtest belongs to the frozen-benchmark Lab, not to self-report).
 * Everything is percent / ratio / count / list. Web-side only: saving or
 * publishing a strategy never touches a trade or the bot.
 */

const crypto = require('crypto');
const { pool } = require('../db');

const MAX_PER_USER = 20;         // drafts + published
const MAX_PUBLIC_PER_USER = 5;   // published at once
const PUBLIC_LIST_LIMIT = 120;

// The rule vocabulary, mirrored from the engine's intent policy. Percent caps,
// a positions count, confidence (0–1), reward:risk, symbol/strategy lists and a
// direction enum. No dollar rule exists — the gate never sizes in dollars here.
const PCT_TYPES = new Set([
  'max_position_pct', 'max_symbol_exposure_pct', 'max_portfolio_exposure_pct',
  'max_daily_loss_pct', 'max_drawdown_pct', 'min_free_margin_pct',
]);
const LIST_TYPES = new Set(['allowed_symbols', 'blocked_symbols', 'allowed_strategy_types']);
const DIRECTIONS = new Set(['long_only', 'short_only', 'both']);
const REGIMES = new Set(['any', 'trend_up', 'trend_down', 'range', 'volatile']);
const HORIZONS = new Set(['scalp', 'intraday', 'swing', 'position']);

const clamp = (s, n) => String(s == null ? '' : s).trim().slice(0, n);
const round2 = (v) => Math.round(v * 100) / 100;

function slugify(name) {
  const base = String(name || '').toLowerCase().normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'strategy';
  // A short random suffix guarantees a unique, SLUG_RE-shaped id without needing
  // the insert id (the mock pool doesn't return one) or a collision query.
  return base + '-' + crypto.randomBytes(3).toString('hex');
}

/** Validate one {type,value} rule → { ok, rule?, error? }. */
function validateRule(raw) {
  if (!raw || typeof raw !== 'object') return { ok: false, error: 'Malformed rule.' };
  const type = String(raw.type || '');
  if (type === 'max_open_positions') {
    const v = Math.round(Number(raw.value));
    if (!Number.isFinite(v) || v < 1 || v > 50) return { ok: false, error: 'Max open positions must be 1–50.' };
    return { ok: true, rule: { type, value: v } };
  }
  if (type === 'min_confidence') {
    let v = Number(raw.value);
    if (v > 1 && v <= 100) v = v / 100;               // accept a percent, store 0–1
    if (!Number.isFinite(v) || v <= 0 || v > 1) return { ok: false, error: 'Min confidence must be between 0 and 1.' };
    return { ok: true, rule: { type, value: round2(v) } };
  }
  if (type === 'min_rr') {
    const v = Number(raw.value);
    if (!Number.isFinite(v) || v < 0.1 || v > 20) return { ok: false, error: 'Min reward:risk must be 0.1–20.' };
    return { ok: true, rule: { type, value: round2(v) } };
  }
  if (PCT_TYPES.has(type)) {
    const v = Number(raw.value);
    if (!Number.isFinite(v) || v <= 0 || v > 100) return { ok: false, error: `${type} must be a percent 0–100.` };
    return { ok: true, rule: { type, value: round2(v) } };
  }
  if (type === 'direction') {
    const v = String(raw.value || '');
    if (!DIRECTIONS.has(v)) return { ok: false, error: 'Direction must be long_only, short_only or both.' };
    return { ok: true, rule: { type, value: v } };
  }
  if (LIST_TYPES.has(type)) {
    let arr = raw.value;
    if (typeof arr === 'string') arr = arr.split(/[,\s]+/);
    if (!Array.isArray(arr)) return { ok: false, error: `${type} needs a list.` };
    const isSym = type !== 'allowed_strategy_types';
    const out = [];
    for (const item of arr) {
      let s = String(item || '').trim();
      if (!s) continue;
      if (isSym) { s = s.toUpperCase().replace(/USDT$/, ''); if (!/^[A-Z0-9]{1,12}$/.test(s)) continue; }
      else { s = s.toLowerCase().replace(/[^a-z0-9_]/g, '').slice(0, 24); if (!s) continue; }
      if (out.indexOf(s) === -1) out.push(s);
      if (out.length >= 20) break;
    }
    if (!out.length) return { ok: false, error: `${type} had no valid entries.` };
    return { ok: true, rule: { type, value: out } };
  }
  return { ok: false, error: `Unknown rule "${type}".` };
}

/** Validate the whole strategy payload → { ok, data?, error? }. */
function validateStrategy(input) {
  const b = input || {};
  const name = clamp(b.name, 80);
  if (name.length < 2) return { ok: false, error: 'Give your strategy a name (2+ characters).' };
  const tagline = clamp(b.tagline, 160);
  const how = clamp(b.how, 600);
  // Icon: keep at most a couple of emoji-ish characters, never markup.
  const icon = clamp(b.icon, 8).replace(/[<>&"'`]/g, '') || '🧠';
  const risk_label = clamp(b.risk_label, 24);
  let regime = clamp(b.regime, 24).toLowerCase();
  if (regime && !REGIMES.has(regime)) regime = '';
  let horizon = clamp(b.horizon, 24).toLowerCase();
  if (horizon && !HORIZONS.has(horizon)) horizon = '';

  const rawRules = Array.isArray(b.rules) ? b.rules : [];
  if (rawRules.length > 12) return { ok: false, error: 'A strategy can carry up to 12 rules.' };
  const seen = new Set();
  const rules = [];
  for (const r of rawRules) {
    const v = validateRule(r);
    if (!v.ok) return { ok: false, error: v.error };
    if (seen.has(v.rule.type)) return { ok: false, error: `Duplicate rule: ${v.rule.type}.` };
    seen.add(v.rule.type);
    rules.push(v.rule);
  }
  return { ok: true, data: { name, tagline, how, icon, risk_label, regime, horizon, rules } };
}

// ── derived risk profile (computed, never authored) ──────────────────────────
// A deterministic RESTATEMENT of what the rules literally say — computed
// server-side so the public card carries a label no author can type. It is a
// constraint-shape read, never a safety verdict: the tier is always paired
// with the literal facts it was derived from, and free-text risk_label stays
// the author's voice, visibly separate. Facts follow the envelope-violation
// {id, en, params} pattern so the UI can whole-line translate them.
const CONTAINMENT_AXES = {
  loss: new Set(['max_daily_loss_pct', 'max_drawdown_pct']),
  sizing: new Set(['max_position_pct', 'max_symbol_exposure_pct',
    'max_portfolio_exposure_pct', 'min_free_margin_pct']),
  count: new Set(['max_open_positions']),
  scope: new Set(['allowed_symbols', 'blocked_symbols']),
};
const FACTS = {
  max_position_pct: (v) => ({ id: 'pos', en: '≤{v}% per position', params: { v } }),
  max_symbol_exposure_pct: (v) => ({ id: 'sym', en: 'symbol exposure ≤{v}%', params: { v } }),
  max_portfolio_exposure_pct: (v) => ({ id: 'port', en: 'portfolio exposure ≤{v}%', params: { v } }),
  max_daily_loss_pct: (v) => ({ id: 'daily', en: 'daily loss halt at {v}%', params: { v } }),
  max_drawdown_pct: (v) => ({ id: 'dd', en: 'drawdown halt at {v}%', params: { v } }),
  min_free_margin_pct: (v) => ({ id: 'margin', en: 'free margin ≥{v}%', params: { v } }),
  max_open_positions: (v) => ({ id: 'open', en: '≤{v} open positions', params: { v } }),
  min_confidence: (v) => ({ id: 'conf', en: 'confidence ≥{v}%', params: { v: Math.round(v * 100) } }),
  min_rr: (v) => ({ id: 'rr', en: 'R:R ≥{v}', params: { v } }),
};
function deriveRiskProfile(rules) {
  const rs = Array.isArray(rules) ? rules : [];
  const facts = [];
  const axes = new Set();
  for (const r of rs) {
    if (!r || !r.type) continue;
    for (const [axis, types] of Object.entries(CONTAINMENT_AXES)) {
      if (types.has(r.type)) axes.add(axis);
    }
    if (r.type === 'direction' && (r.value === 'long_only' || r.value === 'short_only')) {
      axes.add('direction');
      facts.push({ id: r.value === 'long_only' ? 'dir_long' : 'dir_short',
        en: r.value === 'long_only' ? 'long only' : 'short only', params: {} });
    } else if (r.type === 'allowed_symbols' && Array.isArray(r.value) && r.value.length) {
      facts.push({ id: 'scope', en: '{n} symbols only', params: { n: r.value.length } });
    } else if (FACTS[r.type]) {
      facts.push(FACTS[r.type](r.value));
    }
  }
  // Selectivity rules (confidence, R:R, strategy types) never count as
  // containment — being picky about entries contains nothing once in.
  const tier = (axes.has('loss') && axes.has('sizing') && axes.size >= 4) ? 'tight'
    : axes.size >= 2 ? 'capped' : 'light';
  return { tier, axes: [...axes].sort(), facts };
}

// ── public shape (marketplace card, §4-safe: config only, no stats) ──────────
function toPublicCard(row) {
  let rules = [];
  try { rules = JSON.parse(row.rules || '[]'); } catch (e) { rules = []; }
  return {
    id: row.slug, slug: row.slug, name: row.name, icon: row.icon || '🧠',
    tagline: row.tagline || '', how: row.how || '',
    risk_label: row.risk_label || '', regime: row.regime || '', horizon: row.horizon || '',
    rules, risk_profile: deriveRiskProfile(rules), community: true,
    updated_at: row.updated_at || row.created_at || null,
  };
}
function toMine(row) {
  const card = toPublicCard(row);
  card.dbId = row.id;
  card.visibility = row.visibility || 'draft';
  return card;
}

// ── store ops ────────────────────────────────────────────────────────────────
async function listMine(userId) {
  const [rows] = await pool.execute(
    'SELECT * FROM user_strategies WHERE user_id = ? ORDER BY id DESC LIMIT 50', [userId]);
  return rows.map(toMine);
}
async function getMine(userId, id) {
  const [rows] = await pool.execute(
    'SELECT * FROM user_strategies WHERE id = ? AND user_id = ?', [Number(id), userId]);
  return rows[0] ? toMine(rows[0]) : null;
}
async function create(userId, input) {
  const v = validateStrategy(input);
  if (!v.ok) return v;
  const [cnt] = await pool.execute('SELECT COUNT(*) AS n FROM user_strategies WHERE user_id = ?', [userId]);
  if ((cnt[0]?.n || 0) >= MAX_PER_USER) {
    return { ok: false, error: `You've reached ${MAX_PER_USER} strategies — delete one first.` };
  }
  const d = v.data;
  const slug = slugify(d.name);
  const now = new Date();
  await pool.execute(
    `INSERT INTO user_strategies
       (user_id, slug, name, tagline, how, icon, rules, risk_label, regime, horizon, visibility, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)`,
    [userId, slug, d.name, d.tagline, d.how, d.icon, JSON.stringify(d.rules),
      d.risk_label, d.regime, d.horizon, now, now]);
  return { ok: true, slug };
}
async function update(userId, id, input) {
  const existing = await getMine(userId, id);
  if (!existing) return { ok: false, error: 'Strategy not found.' };
  const v = validateStrategy(input);
  if (!v.ok) return v;
  const d = v.data;
  await pool.execute(
    `UPDATE user_strategies SET name = ?, tagline = ?, how = ?, icon = ?, rules = ?,
       risk_label = ?, regime = ?, horizon = ?, updated_at = ? WHERE id = ? AND user_id = ?`,
    [d.name, d.tagline, d.how, d.icon, JSON.stringify(d.rules),
      d.risk_label, d.regime, d.horizon, new Date(), Number(id), userId]);
  return { ok: true };
}
async function remove(userId, id) {
  const [r] = await pool.execute(
    'DELETE FROM user_strategies WHERE id = ? AND user_id = ?', [Number(id), userId]);
  return (r.affectedRows || 0) > 0;
}
async function setVisibility(userId, id, visibility) {
  const vis = visibility === 'public' ? 'public' : 'draft';
  const existing = await getMine(userId, id);
  if (!existing) return { ok: false, error: 'Strategy not found.' };
  if (vis === 'public') {
    if (!existing.rules.length) return { ok: false, error: 'Add at least one rule before publishing.' };
    const [cnt] = await pool.execute(
      "SELECT COUNT(*) AS n FROM user_strategies WHERE user_id = ? AND visibility = 'public'", [userId]);
    if ((cnt[0]?.n || 0) >= MAX_PUBLIC_PER_USER && existing.visibility !== 'public') {
      return { ok: false, error: `You can publish up to ${MAX_PUBLIC_PER_USER} strategies — unpublish one first.` };
    }
    // The FIRST publish date sticks forever (COALESCE): re-saves never bump
    // it, and no publish/unpublish cycle can buy a fresher list position.
    await pool.execute(
      `UPDATE user_strategies SET visibility = 'public', updated_at = ?,
         published_at = COALESCE(published_at, ?) WHERE id = ? AND user_id = ?`,
      [new Date(), new Date(), Number(id), userId]);
    return { ok: true, visibility: vis };
  }
  await pool.execute(
    'UPDATE user_strategies SET visibility = ?, updated_at = ? WHERE id = ? AND user_id = ?',
    [vis, new Date(), Number(id), userId]);
  return { ok: true, visibility: vis };
}
async function listPublic(limit, filters) {
  const n = Math.min(PUBLIC_LIST_LIMIT, Math.max(1, Math.floor(Number(limit)) || PUBLIC_LIST_LIMIT));
  // Ordered by the FIRST publish date (created_at for legacy rows that predate
  // the column — a real date, never an invented one). updated_at buys nothing:
  // re-saving a strategy no longer resurfaces it.
  // Inlined, not bound: a bound LIMIT is ER_WRONG_ARGUMENTS on real MySQL
  // (mysql2 sends numbers as DOUBLE). Safe to interpolate — the cap is clamped
  // to a small positive integer above.
  const [rows] = await pool.execute(
    `SELECT * FROM user_strategies WHERE visibility = 'public'
     ORDER BY COALESCE(published_at, created_at) DESC LIMIT ${PUBLIC_LIST_LIMIT}`);
  let cards = rows.map(toPublicCard);
  // Server-side search/filter over the bounded public window (≤120 rows) —
  // identical semantics on real MySQL and the in-memory shim.
  const f = filters || {};
  if (f.q) {
    const q = String(f.q).toLowerCase().slice(0, 80);
    cards = cards.filter((c) =>
      (c.name + ' ' + c.tagline + ' ' + c.how).toLowerCase().includes(q));
  }
  if (f.regime) cards = cards.filter((c) => c.regime === f.regime);
  if (f.horizon) cards = cards.filter((c) => c.horizon === f.horizon);
  return cards.slice(0, n);
}
async function getPublicBySlug(slug) {
  const [rows] = await pool.execute(
    "SELECT * FROM user_strategies WHERE slug = ? AND visibility = 'public'", [String(slug || '')]);
  return rows[0] ? toPublicCard(rows[0]) : null;
}

module.exports = {
  MAX_PER_USER, MAX_PUBLIC_PER_USER,
  PCT_TYPES, LIST_TYPES, DIRECTIONS, REGIMES, HORIZONS,
  slugify, validateRule, validateStrategy, toPublicCard, deriveRiskProfile,
  listMine, getMine, create, update, remove, setVisibility, listPublic, getPublicBySlug,
};
