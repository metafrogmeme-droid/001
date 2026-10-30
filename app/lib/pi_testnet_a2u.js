'use strict';
/**
 * Temporary Pi Testnet App-to-User payouts.
 *
 * The Pi Developer Portal asks the paired Testnet app for App-to-User
 * payments to 5 unique wallets before it will grant a Mainnet app wallet.
 * This is that payout, and nothing else.
 *
 * OFF unless PI_TESTNET_A2U_ENABLED is exactly "1" (whitespace trimmed, the
 * same reading as the other Pi switches). The app-wallet seed
 * (PI_TESTNET_APP_WALLET_SEED) and the Testnet server key (PI_TESTNET_API_KEY)
 * are read only after that check. The Mainnet server key is not a name this
 * file uses.
 *
 * MAINNET IS A REFUSAL, not a configuration. The transaction is signed with
 * the passphrase "Pi Testnet" and submitted to https://api.testnet.minepi.com.
 * A payment Pi describes with any other network, a horizon or platform host
 * that is not the Testnet one, or a seed whose public key is one of the
 * configured Mainnet wallets (PI_MAINNET_APP_WALLET, PI_MAINNET_WALLET,
 * PI_MAINNET_WALLETS, or any other env name that contains both MAINNET and
 * WALLET and holds a Stellar public key) is refused before anything is signed.
 *
 * The flow is the one Pi documents for a server-side payment: POST /v2/payments
 * with the uid, amount, memo and metadata; build and sign the Stellar payment
 * on Testnet; submit it; POST /v2/payments/{id}/complete with the txid. An
 * incomplete server payment for that uid is finished instead of creating a
 * second one. One uid is paid at most once, and at most 10 payments are
 * created in total. The amount is PI_TESTNET_A2U_AMOUNT or 0.01, and a
 * configured amount above 0.1 is refused rather than reduced.
 *
 * A uid is payable only after it has signed in to the Testnet app
 * (recordSignin, called from the /v2/me sign-in when the request is that app).
 *
 * Nothing here logs, returns or interpolates the seed or the API key. Errors
 * are codes.
 */

const { UID_RE } = require('./pi_auth');
const {
  Keypair, TransactionBuilder, Operation, Asset, Memo, Horizon,
} = require('@stellar/stellar-sdk');

const TESTNET_PASSPHRASE = 'Pi Testnet';
const TESTNET_HORIZON = 'https://api.testnet.minepi.com';
const API_BASE = 'https://api.minepi.com';
const MEMO = 'RUNECLAW testnet';
const FEE = '100000';
const MAX_PAYMENTS = 10;
const MAX_AMOUNT = 0.1;
const DEFAULT_AMOUNT = '0.01';
const TIMEOUT_MS = 15000;
const AMOUNT_RE = /^\d+(\.\d{1,7})?$/;
const PUBLIC_RE = /^G[A-Z2-7]{55}$/;
const PAYMENT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const TXID_RE = /^[a-fA-F0-9]{64}$/;
const WALLET_ENVS = ['PI_MAINNET_APP_WALLET', 'PI_MAINNET_WALLET', 'PI_MAINNET_WALLETS'];

const STATUSES = {
  off: 403,
  not_signed_in: 403,
  mainnet_network: 403,
  mainnet_horizon: 403,
  mainnet_wallet: 403,
  from_address: 403,
  direction: 403,
  destination: 403,
  amount: 403,
  user: 403,
  cancelled: 409,
  bad_uid: 400,
  bad_amount: 400,
  bad_seed: 503,
  not_configured: 503,
  already_paid: 409,
  cap: 409,
  create_failed: 503,
  submit_failed: 503,
  complete_failed: 503,
  incomplete_unreadable: 503,
  payment_unreadable: 503,
  horizon_unreadable: 503,
  platform_unreachable: 503,
  not_json: 503,
  failed: 503,
};

class A2UError extends Error {
  constructor(code) {
    super(code);
    this.name = 'A2UError';
    this.code = code;
  }
}

function coded(code) {
  return new A2UError(code);
}

function enabled(env) {
  return String(env.PI_TESTNET_A2U_ENABLED == null ? '' : env.PI_TESTNET_A2U_ENABLED).trim() === '1';
}

/** The seed, or null. The property is not read unless the flag is on. */
function readSeed(env) {
  if (!enabled(env)) return null;
  const raw = env.PI_TESTNET_APP_WALLET_SEED;
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  return s || null;
}

/** The Testnet server key, or null. Not read unless the flag is on. */
function testnetKey(env) {
  if (!enabled(env)) return null;
  const raw = env.PI_TESTNET_API_KEY;
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  return s || null;
}

function amountOf(env) {
  const raw = env.PI_TESTNET_A2U_AMOUNT;
  const text = raw == null || String(raw).trim() === '' ? DEFAULT_AMOUNT : String(raw).trim();
  if (!AMOUNT_RE.test(text)) return { ok: false };
  const n = Number(text);
  if (!Number.isFinite(n) || !(n > 0) || n > MAX_AMOUNT) return { ok: false };
  return { ok: true, amount: text, n };
}

function sameAmount(a, b) {
  const x = Number(a);
  const y = Number(b);
  return Number.isFinite(x) && Number.isFinite(y) && Math.abs(x - y) < 1e-7;
}

function amountString(v) {
  if (typeof v === 'string' && AMOUNT_RE.test(v.trim())) return v.trim();
  if (typeof v === 'number' && Number.isFinite(v)) {
    const s = v.toFixed(7).replace(/\.?0+$/, '');
    if (AMOUNT_RE.test(s)) return s;
  }
  throw coded('amount');
}

function addPublicKeys(raw, out) {
  for (const part of String(raw == null ? '' : raw).split(',')) {
    const s = part.trim();
    if (PUBLIC_RE.test(s) && !out.includes(s)) out.push(s);
  }
}

/** Stellar public keys configured as Mainnet wallets. Secrets are not keys. */
function mainnetWallets(env) {
  const out = [];
  for (const name of WALLET_ENVS) addPublicKeys(env[name], out);
  for (const name of Object.keys(env)) {
    if (!name.includes('MAINNET') || !name.includes('WALLET')) continue;
    if (WALLET_ENVS.includes(name)) continue;
    addPublicKeys(env[name], out);
  }
  return out;
}

function keypairFromSeed(seed) {
  try {
    return Keypair.fromSecret(seed);
  } catch (err) {
    throw coded('bad_seed');
  }
}

function assertHorizon(url) {
  let parsed;
  try { parsed = new URL(url); } catch (err) { throw coded('mainnet_horizon'); }
  const host = parsed.hostname.toLowerCase();
  if (parsed.protocol !== 'https:' || host !== 'api.testnet.minepi.com') throw coded('mainnet_horizon');
  if (host.includes('mainnet')) throw coded('mainnet_horizon');
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw coded('mainnet_horizon');
  if (parsed.pathname !== '/' && parsed.pathname !== '') throw coded('mainnet_horizon');
}

function assertPlatform(url) {
  let parsed;
  try { parsed = new URL(url); } catch (err) { throw coded('mainnet_horizon'); }
  const host = parsed.hostname.toLowerCase();
  if (parsed.protocol !== 'https:' || host.includes('mainnet') || host === 'api.mainnet.minepi.com') {
    throw coded('mainnet_horizon');
  }
}

function chainMemo(payment) {
  const id = typeof payment.identifier === 'string' ? payment.identifier : '';
  if (Buffer.byteLength(id) > 0 && Buffer.byteLength(id) <= 28) return id;
  const memo = typeof payment.memo === 'string' ? payment.memo : '';
  if (Buffer.byteLength(memo) > 0 && Buffer.byteLength(memo) <= 28) return memo;
  return MEMO;
}

function dbPool(deps) {
  if (deps && deps.pool) return deps.pool;
  return require('../db').pool;
}

async function recordSignin(uid, deps = {}) {
  if (typeof uid !== 'string' || !UID_RE.test(uid)) return false;
  const now = new Date();
  await dbPool(deps).execute(
    'INSERT INTO pi_testnet_signins (uid, signed_in_at) VALUES (?, ?) '
    + 'ON DUPLICATE KEY UPDATE signed_in_at = VALUES(signed_in_at)',
    [uid, now]);
  return true;
}

async function listSignins(deps = {}) {
  const [rows] = await dbPool(deps).execute(
    'SELECT uid, signed_in_at FROM pi_testnet_signins ORDER BY signed_in_at DESC LIMIT 50');
  return (rows || []).map((r) => ({ uid: r.uid, signed_in_at: r.signed_in_at }));
}

async function hasSignin(uid, deps) {
  const [rows] = await dbPool(deps).execute(
    'SELECT uid FROM pi_testnet_signins WHERE uid = ? LIMIT 1', [uid]);
  return !!(rows && rows[0] && rows[0].uid === uid);
}

async function listPayments(deps = {}) {
  const [rows] = await dbPool(deps).execute(
    'SELECT uid, amount, payment_id, txid, status, created_at, updated_at FROM pi_testnet_a2u');
  return rows || [];
}

async function getPaymentRow(uid, deps) {
  const [rows] = await dbPool(deps).execute(
    'SELECT uid, amount, payment_id, txid, status, created_at, updated_at '
    + 'FROM pi_testnet_a2u WHERE uid = ? LIMIT 1', [uid]);
  return (rows && rows[0]) || null;
}

async function insertOpen(uid, amount, deps) {
  const now = new Date();
  try {
    await dbPool(deps).execute(
      'INSERT INTO pi_testnet_a2u (uid, amount, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
      [uid, amount, 'open', now, now]);
  } catch (err) {
    if (err && (err.code === 'ER_DUP_ENTRY' || err.errno === 1062)) return;
    throw err;
  }
}

async function setPaymentId(uid, paymentId, deps) {
  const [result] = await dbPool(deps).execute(
    'UPDATE pi_testnet_a2u SET payment_id = ?, updated_at = ? WHERE uid = ? AND payment_id IS NULL',
    [paymentId, new Date(), uid]);
  if (result && result.affectedRows === 1) return;
  const row = await getPaymentRow(uid, deps);
  if (row && row.payment_id === paymentId) return;
  throw coded('create_failed');
}

async function setTxid(uid, txid, deps) {
  await dbPool(deps).execute(
    'UPDATE pi_testnet_a2u SET txid = ?, updated_at = ? WHERE uid = ? AND (txid IS NULL OR txid = ?)',
    [txid, new Date(), uid, txid]);
}

async function setStatus(uid, status, deps) {
  await dbPool(deps).execute(
    'UPDATE pi_testnet_a2u SET status = ?, updated_at = ? WHERE uid = ?',
    [status, new Date(), uid]);
}

async function deleteOpen(uid, deps) {
  await dbPool(deps).execute(
    'DELETE FROM pi_testnet_a2u WHERE uid = ? AND payment_id IS NULL', [uid]);
}

function view(row) {
  return {
    uid: row.uid,
    amount: row.amount,
    payment_id: row.payment_id,
    txid: row.txid,
    status: row.status,
  };
}

async function platform(method, path, body, deps) {
  const key = testnetKey(deps.env);
  if (!key) throw coded('not_configured');
  const base = deps.apiBase || API_BASE;
  assertPlatform(base);
  const fetchImpl = deps.fetch || globalThis.fetch;
  const headers = { authorization: `Key ${key}`, accept: 'application/json' };
  if (body != null) headers['content-type'] = 'application/json';
  try {
    return await fetchImpl(`${String(base).replace(/\/+$/, '')}/v2${path}`, {
      method,
      headers,
      body: body == null ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(deps.timeoutMs || TIMEOUT_MS),
    });
  } catch (err) {
    throw coded('platform_unreachable');
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (err) {
    throw coded('not_json');
  }
}

function assertSignable(payment, publicKey, expected) {
  if (!payment || typeof payment !== 'object') throw coded('payment_unreadable');
  if (payment.network !== TESTNET_PASSPHRASE) throw coded('mainnet_network');
  if (payment.direction !== 'app_to_user') throw coded('direction');
  if (payment.from_address !== publicKey) throw coded('from_address');
  if (typeof payment.to_address !== 'string' || !PUBLIC_RE.test(payment.to_address)) {
    throw coded('destination');
  }
  if (!sameAmount(payment.amount, expected.amount)) throw coded('amount');
  const status = payment.status || {};
  if (status.cancelled || status.user_cancelled) throw coded('cancelled');
}

async function incompletePayments(deps) {
  const response = await platform('GET', '/payments/incomplete_server_payments', null, deps);
  if (!response.ok) throw coded('incomplete_unreadable');
  const body = await readJson(response);
  if (!body || typeof body !== 'object' || Array.isArray(body)
      || !Array.isArray(body.incomplete_server_payments)) {
    throw coded('incomplete_unreadable');
  }
  return body.incomplete_server_payments;
}

async function getPlatformPayment(id, deps) {
  const response = await platform('GET', `/payments/${encodeURIComponent(id)}`, null, deps);
  if (response.status === 404) return null;
  if (!response.ok) throw coded('payment_unreadable');
  const body = await readJson(response);
  if (!body || body.identifier !== id) throw coded('payment_unreadable');
  return body;
}

async function createOnPlatform(uid, amountNumber, deps) {
  const response = await platform('POST', '/payments', {
    payment: { amount: amountNumber, memo: MEMO, metadata: { kind: 'testnet_a2u' }, uid },
  }, deps);
  if (!response.ok) throw coded('create_failed');
  const body = await readJson(response);
  if (!body || typeof body.identifier !== 'string' || !PAYMENT_ID_RE.test(body.identifier)) {
    throw coded('create_failed');
  }
  if (body.user_uid !== uid) throw coded('user');
  return body;
}

async function completeOnPlatform(id, txid, deps) {
  const response = await platform('POST', `/payments/${encodeURIComponent(id)}/complete`, { txid }, deps);
  if (!response.ok) throw coded('complete_failed');
  try { await response.json(); } catch (err) { /* a 2xx is Pi's completion */ }
}

function openHorizon(url) {
  assertHorizon(url);
  const server = new Horizon.Server(url);
  return {
    loadAccount: (publicKey) => server.loadAccount(publicKey),
    submitTransaction: (tx) => server.submitTransaction(tx),
  };
}

async function submitSigned(payment, seed, publicKey, deps) {
  const horizonUrl = deps.horizonUrl || TESTNET_HORIZON;
  assertHorizon(horizonUrl);
  assertSignable(payment, publicKey, amountOf(deps.env));
  const horizon = deps.horizon || openHorizon(horizonUrl);
  let account;
  try {
    account = await horizon.loadAccount(publicKey);
  } catch (err) {
    throw coded('horizon_unreadable');
  }
  const tx = new TransactionBuilder(account, {
    fee: FEE,
    networkPassphrase: TESTNET_PASSPHRASE,
  })
    .addOperation(Operation.payment({
      destination: payment.to_address,
      asset: Asset.native(),
      amount: amountString(payment.amount),
    }))
    .addMemo(Memo.text(chainMemo(payment)))
    .setTimeout(180)
    .build();
  if (tx.networkPassphrase !== TESTNET_PASSPHRASE) throw coded('mainnet_network');
  const kp = keypairFromSeed(seed);
  if (kp.publicKey() !== publicKey) throw coded('mainnet_wallet');
  tx.sign(kp);
  let result;
  try {
    result = await horizon.submitTransaction(tx);
  } catch (err) {
    throw coded('submit_failed');
  }
  const txid = result && (typeof result.hash === 'string' ? result.hash : result.id);
  if (typeof txid !== 'string' || !TXID_RE.test(txid)) throw coded('submit_failed');
  return { txid: txid.toLowerCase(), tx };
}

/**
 * Finish one Testnet A2U payment Pi already created for this uid.
 * Returns true when this call completed it.
 */
async function finishOne(payment, publicKey, seed, deps) {
  if (!payment || payment.user_uid == null) throw coded('payment_unreadable');
  const expected = amountOf(deps.env);
  if (!expected.ok) throw coded('bad_amount');
  assertSignable(payment, publicKey, expected);
  const uid = payment.user_uid;
  if (!(await hasSignin(uid, deps))) throw coded('not_signed_in');
  let row = await getPaymentRow(uid, deps);
  if (!row) {
    const all = await listPayments(deps);
    if (all.length >= MAX_PAYMENTS) throw coded('cap');
    await insertOpen(uid, expected.amount, deps);
    row = await getPaymentRow(uid, deps);
  }
  if (row && row.status === 'completed') return false;
  if (row && !row.payment_id) await setPaymentId(uid, payment.identifier, deps);
  const existingTx = (payment.transaction && payment.transaction.txid)
    || (row && row.txid)
    || null;
  let txid = typeof existingTx === 'string' && TXID_RE.test(existingTx) ? existingTx.toLowerCase() : null;
  if (!txid) {
    const submitted = await submitSigned(payment, seed, publicKey, deps);
    txid = submitted.txid;
  }
  await setTxid(uid, txid, deps);
  if (payment.status && payment.status.developer_completed) {
    await setStatus(uid, 'completed', deps);
    return true;
  }
  await completeOnPlatform(payment.identifier, txid, deps);
  await setStatus(uid, 'completed', deps);
  return true;
}

async function pay(uid, deps = {}) {
  const env = deps.env || process.env;
  const use = { ...deps, env };
  if (!enabled(env)) throw coded('off');
  if (!testnetKey(env) || !readSeed(env)) throw coded('not_configured');
  const amt = amountOf(env);
  if (!amt.ok) throw coded('bad_amount');
  if (typeof uid !== 'string' || !UID_RE.test(uid)) throw coded('bad_uid');

  const seed = readSeed(env);
  const kp = keypairFromSeed(seed);
  const publicKey = kp.publicKey();
  if (mainnetWallets(env).includes(publicKey)) throw coded('mainnet_wallet');
  assertHorizon(use.horizonUrl || TESTNET_HORIZON);
  assertPlatform(use.apiBase || API_BASE);

  if (!(await hasSignin(uid, use))) throw coded('not_signed_in');

  const before = await getPaymentRow(uid, use);
  if (before && before.status === 'completed') throw coded('already_paid');

  const outstanding = await incompletePayments(use);
  for (const item of outstanding) {
    if (!item || typeof item !== 'object' || item.network !== TESTNET_PASSPHRASE) {
      throw coded('mainnet_network');
    }
  }
  const mine = outstanding.filter((item) => item && item.user_uid === uid && item.direction === 'app_to_user');
  if (mine.length > 1) throw coded('cap');
  if (mine.length === 1) {
    await finishOne(mine[0], publicKey, seed, use);
    const done = await getPaymentRow(uid, use);
    if (!done || done.status !== 'completed') throw coded('complete_failed');
    return view(done);
  }

  if (before && before.payment_id) {
    const payment = await getPlatformPayment(before.payment_id, use);
    if (!payment) throw coded('payment_unreadable');
    if (payment.user_uid !== uid) throw coded('user');
    await finishOne(payment, publicKey, seed, use);
    const done = await getPaymentRow(uid, use);
    if (!done || done.status !== 'completed') throw coded('complete_failed');
    return view(done);
  }

  const all = await listPayments(use);
  if (all.length >= MAX_PAYMENTS) throw coded('cap');
  if (!before) await insertOpen(uid, amt.amount, use);
  const reserved = await listPayments(use);
  if (reserved.length > MAX_PAYMENTS) {
    await deleteOpen(uid, use);
    throw coded('cap');
  }

  let created;
  try {
    created = await createOnPlatform(uid, amt.n, use);
  } catch (err) {
    if (err instanceof A2UError && err.code === 'user') {
      /* Pi created a payment for someone else. Do not keep a slot that did not pay this uid. */
      await deleteOpen(uid, use);
    } else if (err instanceof A2UError && (err.code === 'create_failed' || err.code === 'not_json'
        || err.code === 'platform_unreachable')) {
      await deleteOpen(uid, use);
    }
    throw err;
  }
  await setPaymentId(uid, created.identifier, use);
  assertSignable(created, publicKey, amt);
  const submitted = await submitSigned(created, seed, publicKey, use);
  await setTxid(uid, submitted.txid, use);
  await completeOnPlatform(created.identifier, submitted.txid, use);
  await setStatus(uid, 'completed', use);
  const done = await getPaymentRow(uid, use);
  return view(done);
}

module.exports = {
  pay, recordSignin, listSignins, listPayments, mainnetWallets, amountOf, enabled,
  A2UError, STATUSES,
  TESTNET_PASSPHRASE, TESTNET_HORIZON, API_BASE, MEMO, FEE,
  MAX_PAYMENTS, MAX_AMOUNT, DEFAULT_AMOUNT,
};
