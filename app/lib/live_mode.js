'use strict';
/**
 * Is the bot trading a real account? Read off its scan payload, three-valued.
 *
 * FOUR PRODUCERS ANSWERED THIS AND GAVE THREE ANSWERS. `routes/track.js` had
 * it right, with the right comment: "absent cache means unknown, not a
 * guess" -- `typeof cb.live_mode === 'boolean'`, else null. `routes/sync.js`
 * did `cb.live_mode ? 'LIVE' : 'PAPER'` at both of its ingest sites, so a
 * payload with no live_mode in it was PAPER. `routes/portfolio.js`'s operator
 * path did `let live = false` ... `catch { mode stays PAPER }`, so SIX
 * distinct failed reads -- a cold scan_cache, a NULL scan_json, a payload with
 * no circuit_breaker, one with no live_mode, a malformed scan_json, the SELECT
 * throwing -- each published a `mode: 'PAPER'` on the OPERATOR account that
 * was byte-identical to a genuine reading of live_mode:false.
 *
 * PAPER is the one word that says "none of this is real money". Manufacturing
 * it from an absence, on the account that has the real money, is the most
 * expensive direction that mistake has. And a second copy of the reading is a
 * second answer: the correct one in track.js did nothing for the two beside
 * it, because nothing made them the same reading.
 *
 * The contract is a strict boolean because that is what is sent:
 * scan_skill.py emits `"live_mode": not CONFIG.simulation_mode and
 * CONFIG.live_trading_enabled`, a Python bool, always present, from one
 * producer. A string, a number, a null, or an absent key in that slot is not
 * evidence either way, and the safe direction for "not evidence" is unknown.
 */

/**
 * `true` / `false` when the payload carries a reading, `null` when it does
 * not. Never a guess: an absent or malformed circuit_breaker is null.
 */
function readLiveMode(cb) {
  // Only `!cb` is needed: a string, number or boolean in this slot has no
  // `live_mode` property, so the typeof check below already answers null for
  // it. A `typeof cb !== 'object'` guard here survived the mutation round
  // unkilled because it could not change any answer -- dead code, removed.
  if (!cb) return null;
  return typeof cb.live_mode === 'boolean' ? cb.live_mode : null;
}

/**
 * The word a surface prints for a reading. `null` in, `null` out -- the client's
 * `readMode` renders that as MODE ? rather than as a claim.
 */
function modeWord(live) {
  if (live === true) return 'LIVE';
  if (live === false) return 'PAPER';
  return null;
}

module.exports = { readLiveMode, modeWord };
