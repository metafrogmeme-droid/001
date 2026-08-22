'use strict';
/**
 * One line, one format, for security-relevant refusals and completions.
 *
 * This lived as a private function inside `routes/controls.js`, which was fine
 * while controls was the only surface logging a step-up refusal. Account
 * deletion needs the same line from `auth.js`, and copying six lines across is
 * how two formats appear and how a log filter written for one silently misses
 * the other. Extracted rather than duplicated, for the reason
 * `test/helpers/code_only.js` gives about its own two copies: the same bug in
 * two places is one bug with two places to recur.
 *
 * NEVER LOG THE SUBJECT OF THE EVENT, only its actor and a short reason. The
 * user id identifies an account for an operator reading a log; an email, a
 * token or a code would put the thing being protected into the record that
 * exists to protect it.
 */
function secLog(event, req, extra) {
  const uid = req && req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
}

module.exports = { secLog };
