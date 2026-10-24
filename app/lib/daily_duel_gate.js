'use strict';
/**
 * The anonymous Daily Duel bot's questions, answered by the duel that already
 * exists. This file decides nothing of its own.
 *
 *   cutoff   the instant `duel.isCallable` stops taking a direction call:
 *            the UTC day rolls. `cutoffFor` is that instant, checked against
 *            `isCallable` rather than a second clock.
 *   horizon  `duel.HORIZON_HOURS`, via `duel.horizonFor`.
 *   price    `tickers.getTickers`, the map `duel_service.placePick` snaps an
 *            entry from. A caller-supplied entry is ignored.
 *   market   the first direction round of `duel.buildRounds` for that day.
 *            The bot has no signals table, so the agent's own calls are not
 *            an input; the pool top-up is the round the builder deals without
 *            them. The same builder, not a symbol chosen here.
 *   seal     `callseal.sealDuelPick`. The payload is that function's. Fields
 *            this module was not asked to seal — a Telegram id, a chat, a
 *            username, the salted key — are not copied onto it.
 *
 * No key is read or signed here. Anchoring stays on /doge.
 */

const duel = require('./duel');
const { sealDuelPick } = require('./callseal');
const tickers = require('./tickers');
const scam = require('./scam_or_safe');
const confidence = require('./duel_confidence');

const PICKS = Object.freeze({ up: 'long', down: 'short', long: 'long', short: 'short' });

/** Public flag, or the bot's own admin check passed as caller_is_admin.
 * The field is never copied onto a receipt. */
function confidenceAdmitted(input) {
  return confidence.enabled() === true || (input != null && input.caller_is_admin === true);
}

function rules() {
  return {
    ok: true,
    horizon_hours: duel.HORIZON_HOURS,
    price_reader: 'getTickers',
    price_module: 'app/lib/tickers.js',
    // The one reading of the public flag. Off, this is false. An admin
    // preview is offer/seal/calibration, which the bot asks per caller.
    confidence: confidence.enabled(),
  };
}

/** The first instant a direction call on `day` is no longer callable. */
function cutoffFor(day) {
  const start = duel.dayStartMs(day);
  if (!Number.isFinite(start)) return null;
  // isCallable is `dayKey(now) === day`. The next UTC midnight is the first
  // moment that stops being true. 24h is the length of the UTC day that
  // comparison uses, read off dayStartMs, not a new cutoff hour.
  const at = new Date(start + 24 * 60 * 60 * 1000);
  return at.toISOString();
}

function directionRound(day, marks) {
  const rounds = duel.buildRounds(day, [], marks || {});
  return rounds.find((r) => r.kind === 'direction') || null;
}

function priceOf(marks, symbol) {
  const mark = marks && marks[symbol];
  const v = mark == null ? NaN : Number(mark.price);
  return Number.isFinite(v) && v > 0 ? v : null;
}

async function readMarks(input) {
  if (input && Object.prototype.hasOwnProperty.call(input, 'marks')) return input.marks;
  try {
    return await tickers.getTickers();
  } catch (e) {
    return null;
  }
}

function describe(day, now, marks, input) {
  const cutoff_at = cutoffFor(day);
  const round = marks ? directionRound(day, marks) : null;
  const symbol = round ? round.symbol : null;
  const entry_price = symbol ? priceOf(marks, symbol) : null;
  const callable = Boolean(symbol) && duel.isCallable({ day, kind: 'direction' }, now);
  return {
    ok: true,
    day,
    callable,
    cutoff_at,
    horizon_hours: duel.HORIZON_HOURS,
    symbol,
    entry_price,
    unreadable: entry_price == null,
    price_reader: 'getTickers',
    price_module: 'app/lib/tickers.js',
    confidence: confidenceAdmitted(input),
  };
}

async function offer(input) {
  const now = input && input.now ? new Date(input.now) : new Date();
  if (Number.isNaN(now.getTime())) return { ok: false, error: 'clock' };
  const day = duel.dayKey(now);
  const marks = await readMarks(input || {});
  return describe(day, now, marks, input);
}

/**
 * Seal one up/down call. `input.telegram_id` and every other identity field
 * are dropped on purpose: the receipt is public, and the id is not a fact
 * the seal is about.
 */
async function sealCall(input) {
  const src = input || {};
  const now = src.now ? new Date(src.now) : new Date();
  if (Number.isNaN(now.getTime())) return { ok: false, error: 'clock' };
  const day = duel.dayKey(now);
  if (src.day && String(src.day) !== day) return { ok: false, error: 'closed', day, cutoff_at: cutoffFor(String(src.day)) };
  if (!duel.isCallable({ day, kind: 'direction' }, now)) {
    return { ok: false, error: 'closed', day, cutoff_at: cutoffFor(day) };
  }
  const pick = PICKS[String(src.pick || '').trim().toLowerCase()];
  if (pick !== 'long' && pick !== 'short') return { ok: false, error: 'pick' };
  const marks = await readMarks(src);
  const round = marks ? directionRound(day, marks) : null;
  if (!round) return { ok: false, error: 'unreadable', day, cutoff_at: cutoffFor(day) };
  if (src.symbol && String(src.symbol) !== round.symbol) {
    return { ok: false, error: 'moved', day, symbol: round.symbol, cutoff_at: cutoffFor(day) };
  }
  const entry = priceOf(marks, round.symbol);
  if (entry == null) return { ok: false, error: 'unreadable', day, symbol: round.symbol, cutoff_at: cutoffFor(day) };
  // A handle that is only digits is an id wearing a name. Anonymous until
  // the website can issue a one-time code; nothing here accepts one.
  let handle = null;
  if (src.handle) {
    handle = String(src.handle);
    if (/^\d{5,}$/.test(handle)) return { ok: false, error: 'handle' };
  }
  const picked_at = now.toISOString();
  const resolves_at = duel.horizonFor(picked_at);
  // No round_id: this call is not a row in duel_rounds. Omitting the key
  // makes sealDuelPick write null. Number(null) would be 0, a round that
  // does not exist wearing an id.
  const facts = {
    day,
    kind: 'direction',
    symbol: round.symbol,
    symbol_b: null,
    threshold_pct: null,
    handle,
    pick,
    entry_price: entry,
    entry_price_b: null,
    resolves_at,
    picked_at,
  };
  // Off: a rating on the input is dropped, so the sealed string is the one
  // a call without it has always been. On: a valid rating is sealed with the
  // call; a missing one is still a call (other clients of this gate do not
  // send one); a present one that is not low, medium or high is refused.
  if (confidenceAdmitted(src) && src.confidence != null && src.confidence !== '') {
    const level = confidence.levelOf(src.confidence);
    if (!level) return { ok: false, error: 'confidence', day, cutoff_at: cutoffFor(day) };
    facts.confidence = level;
  }
  const receipt = sealDuelPick(facts);
  return {
    ok: true,
    seal: receipt.seal,
    seal_payload: receipt.seal_payload,
    pick,
    symbol: round.symbol,
    day,
    entry_price: entry,
    resolves_at,
    picked_at,
    cutoff_at: cutoffFor(day),
    horizon_hours: duel.HORIZON_HOURS,
  };
}

/**
 * How often high-confidence calls were right, for the anonymous bot.
 *
 * The bot stores no settlement. A call whose window has not ended, a call
 * this function cannot price, and a call that is not a direction call are
 * left out of the rate — not counted wrong. The price is the one read now,
 * after the window, the same reader the entry used. Off, this does not read
 * a price and reports the flag.
 */
async function calibrationOf(input) {
  if (!confidenceAdmitted(input)) return { ok: true, enabled: false };
  const src = input || {};
  const now = src.now ? new Date(src.now) : new Date();
  if (Number.isNaN(now.getTime())) return { ok: false, error: 'clock' };
  const marks = await readMarks(src);
  const nowIso = now.toISOString();
  const rows = [];
  const payloads = Array.isArray(src.payloads) ? src.payloads : [];
  for (const raw of payloads) {
    let body;
    try { body = JSON.parse(String(raw || '')); } catch (e) { continue; }
    if (!body || typeof body !== 'object') continue;
    const level = confidence.levelOf(body.confidence);
    if (!level) continue;
    const resolves = typeof body.resolves_at === 'string' ? body.resolves_at : '';
    const priced = resolves && nowIso >= resolves && (!body.category || body.category === 'direction');
    const price = priced && marks ? priceOf(marks, body.symbol) : null;
    if (!priced || price == null) {
      rows.push({ confidence: level, state: null });
      continue;
    }
    const outcome = duel.outcomeOf(body.entry_price, price);
    rows.push({ confidence: level, state: duel.scorePick(body.pick, outcome, null).state });
  }
  return { ok: true, enabled: true, high: confidence.calibration(rows) };
}

/** Consecutive duel days with a call. `duel.duelStreak`, not a second definition. */
function streak(input) {
  const src = input || {};
  const now = src.now ? new Date(src.now) : new Date();
  if (Number.isNaN(now.getTime())) return { ok: false, error: 'clock' };
  const picks = (Array.isArray(src.picked_at) ? src.picked_at : [])
    .filter((t) => typeof t === 'string' && t)
    .map((t) => ({ created_at: t }));
  const s = duel.duelStreak(picks, now);
  return { ok: true, current: s.current, best: s.best, active_today: s.active_today };
}

async function main() {
  const cmd = process.argv[2];
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  let input = {};
  const raw = Buffer.concat(chunks).toString('utf8').trim();
  if (raw) {
    try { input = JSON.parse(raw); } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: 'input' }));
      return;
    }
  }
  let out;
  if (cmd === 'rules') {
    const day = input.day || new Date().toISOString().slice(0, 10);
    out = Object.assign(rules(), { day, cutoff_at: cutoffFor(day) });
  } else if (cmd === 'offer') out = await offer(input);
  else if (cmd === 'seal') out = await sealCall(input);
  else if (cmd === 'streak') out = streak(input);
  else if (cmd === 'sos-offer') out = scam.offer(input);
  else if (cmd === 'sos-seal') out = scam.sealPick(input);
  else if (cmd === 'calibration') out = await calibrationOf(input);
  else out = { ok: false, error: 'command' };
  process.stdout.write(JSON.stringify(out));
}

module.exports = {
  rules, cutoffFor, offer, sealCall, streak, calibrationOf, directionRound, priceOf,
};

if (require.main === module) {
  main().catch(() => {
    process.stdout.write(JSON.stringify({ ok: false, error: 'failed' }));
    process.exitCode = 1;
  });
}
