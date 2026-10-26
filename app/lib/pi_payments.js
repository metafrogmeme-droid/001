'use strict';
/**
 * Pi Payments, user to app: the server half of a tip in the Pi Browser app.
 *
 * A Pi payment has three parties and two of them are not us. The page asks the
 * Pi SDK to create a payment; Pi's servers hand the page an id and wait for
 * THIS server to approve it; the person signs the blockchain transaction; Pi's
 * servers then wait for this server to complete it. Approval and completion
 * are calls to the Pi Platform API, authorised by the app's server API key,
 * which never leaves this process (Pi Platform docs, platform_API.md):
 *
 *   GET  https://api.minepi.com/v2/payments/{id}            the payment, as Pi holds it
 *   POST https://api.minepi.com/v2/payments/{id}/approve    let the person sign
 *   POST https://api.minepi.com/v2/payments/{id}/complete   { txid }: it happened
 *   Authorization: Key <the app's server API key>
 *     PI_NETWORK_API_KEY, or PI_TESTNET_API_KEY when the request is the
 *     Testnet app (lib/pi_host) and that key is set. Unset there, the
 *     Mainnet key. A request that is not the Testnet app never reads the
 *     Testnet key.
 *
 * WHAT THE PAGE SAYS IS NEVER THE PAYMENT. The page sends a payment id (and a
 * txid at completion); everything else (whose payment, how much, which order,
 * which direction, which transaction) is read back from Pi here and checked
 * against the order this server created. A page can name any id it likes, so
 * a payment that is not this person's, not this amount, or not this order is
 * refused before anything is approved or completed (`mismatches`).
 *
 * Pi's helper packages for this (pi-sdk-express, pi-sdk-js) were unpublished
 * from npm on 2026-03-19, so the three calls are made here directly, and the
 * page calls window.Pi.createPayment itself.
 *
 * Three answers, as in lib/pi_auth.js: a payment, `null` (Pi says there is no
 * such payment), or a throw (Pi could not be asked, refused our key, or
 * answered something unusable). A throw is ours to report, never a verdict on
 * the person.
 */

const { isTestnetHost } = require('./pi_host');

const API_BASE = 'https://api.minepi.com';
const TIMEOUT_MS = 10000;

/** Pi's payment ids are short opaque strings; txids are hex hashes. */
const PAYMENT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const TXID_RE = /^[A-Za-z0-9]{1,128}$/;

/** What the tip is called on the person's Pi payment screen. */
const TIP_MEMO = 'Support RUNECLAW';
const TIP_KIND = 'tip';
/** What Coach Plus is called on the person's Pi payment screen. */
const COACH_PLUS_MEMO = 'RUNECLAW Coach Plus';

/** The amounts a person may choose, in Pi, unless PI_TIP_AMOUNTS says otherwise. */
const DEFAULT_TIP_AMOUNTS = Object.freeze([1, 3, 5]);
const MAX_TIP = 100;

/** Raised when the server has no API key: payments are not offered, not broken. */
class PaymentsNotConfigured extends Error {}

/**
 * The server API key for this request. On the Testnet app, PI_TESTNET_API_KEY
 * when it is set; otherwise PI_NETWORK_API_KEY, on that app and every other.
 * Sign-in does not use a key (lib/pi_auth.js checks the access token with
 * GET /v2/me).
 * `req` may be omitted by a caller with no request: that is the Mainnet key.
 */
function apiKey(env = process.env, req) {
  if (req && isTestnetHost(req, env)) {
    const test = String(env.PI_TESTNET_API_KEY || '').trim();
    if (test) return test;
  }
  const k = String(env.PI_NETWORK_API_KEY || '').trim();
  return k || null;
}

function configured(env = process.env, req) {
  return apiKey(env, req) !== null;
}

/**
 * The tip amounts, from PI_TIP_AMOUNTS ("1,3,5") or the default. An entry that
 * is not a positive amount of at most MAX_TIP Pi with at most 7 decimals is
 * dropped; a setting with nothing usable left falls back to the default rather
 * than offering nothing.
 */
function tipAmounts(env = process.env) {
  const raw = String(env.PI_TIP_AMOUNTS || '').trim();
  if (!raw) return DEFAULT_TIP_AMOUNTS.slice();
  const out = [];
  for (const part of raw.split(',')) {
    const s = part.trim();
    if (!/^\d+(\.\d{1,7})?$/.test(s)) continue;
    const n = Number(s);
    if (n > 0 && n <= MAX_TIP && !out.includes(n)) out.push(n);
  }
  return out.length ? out.sort((a, b) => a - b) : DEFAULT_TIP_AMOUNTS.slice();
}

async function call(method, path, body, deps) {
  const key = deps.apiKey !== undefined ? deps.apiKey : apiKey();
  if (!key) throw new PaymentsNotConfigured('PI_NETWORK_API_KEY is not set');
  const fetchImpl = deps.fetch || globalThis.fetch;
  const headers = { authorization: `Key ${key}`, accept: 'application/json' };
  if (body) headers['content-type'] = 'application/json';
  return fetchImpl(`${deps.apiBase || API_BASE}/v2${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(deps.timeoutMs || TIMEOUT_MS),
  });
}

async function json(r, what) {
  try {
    return await r.json();
  } catch (_) {
    throw new Error(`Pi Platform ${what} answered a body that is not JSON`);
  }
}

/** The payment as Pi holds it, or null when Pi says it does not exist. */
async function getPayment(paymentId, deps = {}) {
  const r = await call('GET', `/payments/${encodeURIComponent(paymentId)}`, null, deps);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Pi Platform payment read answered ${r.status}`);
  const p = await json(r, 'payment read');
  if (!p || typeof p !== 'object' || p.identifier !== paymentId) {
    throw new Error('Pi Platform payment read answered a different payment');
  }
  return p;
}

async function approvePayment(paymentId, deps = {}) {
  const r = await call('POST', `/payments/${encodeURIComponent(paymentId)}/approve`, null, deps);
  if (!r.ok) throw new Error(`Pi Platform approve answered ${r.status}`);
  return json(r, 'approve');
}

async function completePayment(paymentId, txid, deps = {}) {
  const r = await call('POST', `/payments/${encodeURIComponent(paymentId)}/complete`, { txid }, deps);
  // Pi's rule: a non-200 here means the payment is NOT complete, and whatever
  // it paid for must not be delivered (platform docs, payments.md).
  if (!r.ok) throw new Error(`Pi Platform complete answered ${r.status}`);
  return json(r, 'complete');
}

/** Amounts as Pi returns them (numbers) against ours (DECIMAL strings). */
function sameAmount(a, b) {
  const x = Number(a);
  const y = Number(b);
  return Number.isFinite(x) && Number.isFinite(y) && Math.abs(x - y) < 1e-7;
}

/**
 * Why this payment may not settle this order for this person: an empty list
 * means it may. Every field is Pi's record, not the page's word.
 */
function mismatches(payment, order, piUid) {
  const out = [];
  const status = payment.status || {};
  const meta = payment.metadata || {};
  if (payment.direction !== 'user_to_app') out.push('direction');
  if (!piUid || payment.user_uid !== piUid) out.push('user');
  if (!order || meta.orderId !== order.id) out.push('order');
  if (order && !sameAmount(payment.amount, order.amount)) out.push('amount');
  if (order && meta.kind !== order.kind) out.push('kind');
  if (status.cancelled || status.user_cancelled) out.push('cancelled');
  return out;
}

module.exports = {
  getPayment, approvePayment, completePayment, mismatches, sameAmount,
  tipAmounts, configured, apiKey, PaymentsNotConfigured,
  API_BASE, TIMEOUT_MS, PAYMENT_ID_RE, TXID_RE, TIP_MEMO, TIP_KIND, COACH_PLUS_MEMO,
  DEFAULT_TIP_AMOUNTS, MAX_TIP,
};
