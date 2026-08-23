'use strict';
/**
 * The wire format for a user's venue selection, web ↔ bot.
 *
 * ONE parser, used by the route that accepts a selection and the sync channel
 * that serves it, because the failure mode of two is not a crash — it is the
 * web and the bot quietly disagreeing about which venues someone trades on.
 *
 * THE COLUMN HAS THREE STATES AND COLLAPSING ANY TWO IS A BUG:
 *
 *   null   no venue change proposed. The user touched some OTHER control.
 *   ''     proposed: clear the selection, back to a single venue.
 *   'a,b'  proposed: trade these.
 *
 * `null` vs `''` is the difference between "leave my venues alone" and "turn
 * multi-venue off". A writer that sent `''` for both would silently drop
 * somebody's selection every time they changed an unrelated setting, and the
 * only symptom would be their book quietly concentrating onto one venue.
 */

const { isVenue } = require('./venues');

/** Max venues one person may select. A bound, not a policy — see parseSelection. */
const MAX_VENUES = 8;

/**
 * Parse a client-supplied selection into a canonical array.
 *
 * @returns {{ok: true, venues: string[]}|{ok: false, error: string}}
 *
 * Refuses rather than filtering. Dropping an unrecognised venue and saving the
 * rest would answer a request nobody made: the user asked to trade on A and B,
 * and silently storing only A leaves them believing B is live. Every rejection
 * here names what was wrong with it.
 */
function parseSelection(raw) {
  if (raw === null || raw === undefined) return { ok: false, error: 'no venues field' };
  let list = raw;
  if (typeof raw === 'string') {
    list = raw.split(',').map((s) => s.trim()).filter(Boolean);
  }
  if (!Array.isArray(list)) return { ok: false, error: 'venues must be a list' };
  const out = [];
  for (const v of list) {
    const n = String(v || '').toLowerCase().trim();
    if (!n) continue;
    if (!isVenue(n)) return { ok: false, error: `not a supported venue: ${v}` };
    if (!out.includes(n)) out.push(n);
  }
  // BOUND THE CANONICAL LIST, not the raw one. Checking length first refuses a
  // client that repeats the same venue — a selection of one, rejected for
  // being too large. The bound is about how many venues a person trades, and
  // that is only knowable after duplicates collapse.
  if (out.length > MAX_VENUES) {
    return { ok: false, error: `at most ${MAX_VENUES} venues` };
  }
  return { ok: true, venues: out };
}

/** Canonical string for the DB column. `[]` serialises to `''`, never to null. */
function serializeSelection(venues) {
  return (venues || []).join(',');
}

/**
 * Read the column back. `null`/`undefined` → `null` (nothing proposed);
 * anything else → an array, possibly empty.
 *
 * The `null` case is returned as `null` rather than `[]` precisely because a
 * caller that treats them alike reintroduces the collapse this file exists to
 * prevent — and it would do it on the READ side, where it is harder to see.
 */
function deserializeSelection(value) {
  if (value === null || value === undefined) return null;
  return String(value).split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
}

module.exports = { parseSelection, serializeSelection, deserializeSelection, MAX_VENUES };
