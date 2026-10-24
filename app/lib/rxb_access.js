'use strict';
/**
 * Who may read Recursion Breaker.
 *
 * The flag on: anyone. The reading is a public signal.
 * The flag off: an operator can test it, and everyone else gets 404.
 */

const { flag_on } = require('./rxb_flag');
const operatorView = require('./operator_view');

async function access(req, deps) {
  const open = flag_on(deps && deps.env);
  const ask = (deps && deps.isOperator) || operatorView.isOperator;
  let operator = false;
  try {
    operator = await ask(req) === true;
  } catch (err) {
    operator = false;
  }
  if (open) return { ok: true, audience: 'public' };
  if (operator) return { ok: true, audience: 'admin_test' };
  return { ok: false, audience: null };
}

function dollarClaim(value) {
  if (value == null || typeof value === 'number' || typeof value === 'boolean') return false;
  if (typeof value === 'string') {
    return value.includes('$') || /\b(usd|usdc|dollars)\b/i.test(value);
  }
  if (Array.isArray(value)) return value.some(dollarClaim);
  if (typeof value === 'object') {
    return Object.entries(value).some(([key, item]) => /usd|\$/i.test(key) || dollarClaim(item));
  }
  return false;
}

module.exports = { access, dollarClaim };
