/**
 * Is this request the OPERATOR's, for a surface that shows the operator's
 * account in dollars?
 *
 * SIGNED IN IS NOT THE OPERATOR. Registration is open and issues a session at
 * once, so `req.user ? value : scrubbed(value)` handed every free signup the
 * operator's equity, net P&L and per-trade sizes -- and, on the engine-wide
 * flight record, every OTHER account's trades too. Four surfaces made that
 * mistake the same way: the flight record, the weekly letter, the scan
 * payload and the portfolio summary.
 *
 * The answer is the plan, re-read from the database on every call and never
 * taken from the JWT, which is the rule the operator-only controls already
 * follow (`routes/controls.js` operatorGate, `routes/reports.js` /yield). A
 * read that fails answers false: the scrubbed view is the safe one, and it is
 * what an anonymous caller already gets.
 */
const { pool } = require('../db');

async function isOperator(req) {
  const uid = req && req.user ? req.user.user_id : null;
  if (uid === null || uid === undefined) return false;
  try {
    const [rows] = await pool.execute('SELECT plan FROM users WHERE id = ?', [uid]);
    return !!(rows && rows[0] && String(rows[0].plan) === 'admin');
  } catch (e) {
    console.error('operator check failed, serving the public view:', e.message);
    return false;
  }
}

module.exports = { isOperator };
