'use strict';
/**
 * Confidence Duel — a player's own rating of a Daily Duel call.
 *
 * Three words, one spelling: `low`, `medium`, `high`. The word is sealed
 * inside the existing `duel_pick` payload (callseal.canonicalDuelPayload),
 * after every field that payload already had, and only when a caller passed
 * one. A receipt sealed without it omits the key. Verifiers hash the stored
 * string, so an older receipt still checks.
 *
 * It is a self-rating. It is not money, not a count that pays, and not a
 * price. The flag `DUEL_CONFIDENCE_ENABLED` defaults off. Off,
 * no caller is handed a level, and a level a client sends anyway is not
 * sealed, so the payload is the one it was.
 *
 * Calibration counts how often `high` calls were right. `pct` is null when
 * none of them has settled into correct or wrong: a missing rate is not 0%.
 * A push, a call still running, and a call whose result could not be read
 * are `unscored`, not wrong.
 */

const LEVELS = Object.freeze(['low', 'medium', 'high']);

function enabled(env = process.env) {
  return /^(1|true|yes|on)$/i.test(String(env.DUEL_CONFIDENCE_ENABLED || '').trim());
}

/** One of LEVELS, or null. Anything else is not a rating. */
function levelOf(raw) {
  const s = String(raw == null ? '' : raw).trim().toLowerCase();
  return LEVELS.indexOf(s) >= 0 ? s : null;
}

/** The rating sealed in a payload string, or null when it has none. */
function fromPayload(raw) {
  if (typeof raw !== 'string' || !raw) return null;
  let body;
  try { body = JSON.parse(raw); } catch (e) { return null; }
  if (!body || typeof body !== 'object') return null;
  return levelOf(body.confidence);
}

/**
 * `{ level, correct, scored, unscored, pct }` over rows of
 * `{ confidence, state }`. Only `high` is counted. `state` is the duel's
 * own verdict (`correct`, `wrong`, `push`, …) or null when there isn't one.
 */
function calibration(rows) {
  let correct = 0;
  let scored = 0;
  let unscored = 0;
  for (const row of rows || []) {
    if (!row || row.confidence !== 'high') continue;
    if (row.state === 'correct') { correct += 1; scored += 1; }
    else if (row.state === 'wrong') scored += 1;
    else unscored += 1;
  }
  return {
    level: 'high',
    correct,
    scored,
    unscored,
    pct: scored > 0 ? Math.round((correct / scored) * 1000) / 10 : null,
  };
}

/**
 * The same stat for stored picks. `entries` are `duel.scoreEntries`'s rows;
 * a pick whose round was not scored is unscored, not wrong.
 */
function fromPicks(picks, entries) {
  const by = new Map();
  for (const e of entries || []) {
    if (e && e.round_id != null) by.set(String(e.round_id), e.state);
  }
  const rows = [];
  for (const p of picks || []) {
    const level = fromPayload(p && p.seal_payload);
    if (!level) continue;
    const key = String(p.round_id);
    rows.push({ confidence: level, state: by.has(key) ? by.get(key) : null });
  }
  return calibration(rows);
}

module.exports = { LEVELS, enabled, levelOf, fromPayload, calibration, fromPicks };
