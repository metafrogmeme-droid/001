'use strict';
/**
 * Canonical copy. app/lib/sar is a byte copy the site loads.
 * Do not edit one side.
 *
 * Threshold alerts on the public SaR95 reading. 3% is 300 bps and 5% is
 * 500 bps, the paper's levels, strictly greater. A missing reading is not
 * an all-clear. The webhook payload is that reading. It carries no dollar
 * field. Unset webhook sends nothing. This module does not sign.
 */

const { flag_on } = require('./flag');

const THREE_BPS = 300n;
const FIVE_BPS = 500n;

function readSar(snapshot) {
  const sys = snapshot && snapshot.sys;
  if (!sys || typeof sys !== 'object') return { bps: null, reason: 'sar_absent' };
  if (sys.sar95_bps == null) return { bps: null, reason: sys.reason || 'sar_absent' };
  if (typeof sys.sar95_bps !== 'string' || !/^\d+$/.test(sys.sar95_bps)) {
    return { bps: null, reason: 'sar_unreadable' };
  }
  try {
    return { bps: BigInt(sys.sar95_bps), reason: null };
  } catch {
    return { bps: null, reason: 'sar_unreadable' };
  }
}

function evaluate(snapshot) {
  const read = readSar(snapshot);
  if (read.bps == null) {
    return { status: 'not_read', reason: read.reason, crossed: [], sar95_bps: null };
  }
  const crossed = [];
  if (read.bps > THREE_BPS) crossed.push('3pct');
  if (read.bps > FIVE_BPS) crossed.push('5pct');
  return {
    status: 'read',
    reason: null,
    crossed,
    sar95_bps: read.bps.toString(),
  };
}

function webhookUrl(env) {
  const src = env || process.env;
  const raw = src.SAR_ORACLE_ALERT_WEBHOOK;
  if (raw == null || String(raw).trim() === '') return { url: null, reason: 'not_configured' };
  let parsed;
  try {
    parsed = new URL(String(raw).trim());
  } catch {
    return { url: null, reason: 'webhook_unreadable' };
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    return { url: null, reason: 'webhook_unreadable' };
  }
  return { url: parsed.toString(), reason: null };
}

/**
 * POST the public evaluation when a threshold was crossed. No webhook, no
 * crossing, or the flag off: no request. A failed request does not throw.
 */
async function maybeSend(snapshot, env, fetchImpl) {
  const src = env || process.env;
  if (!flag_on(src)) return { status: 'flag_off', sent: false };
  const hook = webhookUrl(src);
  if (!hook.url) return { status: hook.reason === 'not_configured' ? 'not_configured' : 'not_sent', reason: hook.reason, sent: false };
  const evaluation = evaluate(snapshot);
  if (evaluation.status !== 'read' || evaluation.crossed.length === 0) {
    return { status: 'no_alert', sent: false };
  }
  const payload = JSON.stringify(evaluation);
  if (payload.indexOf('_usd_') !== -1) return { status: 'not_sent', reason: 'dollar_key', sent: false };
  const send = fetchImpl || globalThis.fetch;
  try {
    const res = await send(hook.url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: payload,
    });
    if (!res || res.ok !== true) return { status: 'not_sent', reason: 'webhook_rejected', sent: false };
    return { status: 'sent', sent: true };
  } catch (err) {
    return { status: 'not_sent', reason: err && err.name ? err.name : 'Error', sent: false };
  }
}

module.exports = {
  THREE_BPS, FIVE_BPS, readSar, evaluate, webhookUrl, maybeSend,
};
