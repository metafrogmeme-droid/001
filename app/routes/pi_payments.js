'use strict';
/**
 * Pi tips: the Pi Browser app's one payment, user to app, and nothing it unlocks.
 *
 *   GET  /api/pi/payments/tips        { enabled, amounts?, memo? }
 *   POST /api/pi/payments/tips        { amount }             -> { orderId, amount, memo, metadata }
 *   POST /api/pi/payments/approve     { paymentId }          -> { ok, status: 'approved' }
 *   POST /api/pi/payments/complete    { paymentId, txid }    -> { ok, status: 'completed' }
 *   POST /api/pi/payments/incomplete  { paymentId }          -> { ok, status }
 *
 * Every route needs the session the Pi sign-in issued (authMiddleware), and the
 * account must be a Pi account: a tip is matched to the person through the
 * uid App Studio vouched for at sign-in (users.pi_uid), never through anything
 * the page sends.
 *
 * THE SERVER OWNS THE TIP. The page asks for one of the amounts this server
 * offers (lib/pi_payments.tipAmounts) and gets back an order: an id, the
 * amount, the memo and the metadata to hand Pi.createPayment. At approval and
 * completion the page sends only Pi's payment id (and the txid); the payment
 * itself is read back from Pi with the server-only key and must match the
 * order: this person, this amount, this order, user to app, not cancelled
 * (lib/pi_payments.mismatches).
 *
 * IDEMPOTENT, BECAUSE PI RETRIES. The SDK re-sends approval and completion
 * every ~10 s until they succeed, and an interrupted payment comes back
 * through /incomplete on the next sign-in. So each step writes what it is
 * about to rely on BEFORE the call to Pi that cannot be taken back: the
 * payment id before approval (unique, so one Pi payment settles one order),
 * the txid before completion. A repeat finds the row already in that state
 * and answers the same way; a different payment or txid for the same order is
 * a 409. A tip grants nothing, so "fulfilled once" is the status moving to
 * `completed` once, guarded by `status <> 'completed'`.
 *
 * Answers: 400 malformed, 403 not a Pi account or not this person's payment,
 * 404 a payment Pi does not know, 409 a payment that does not match its
 * order, 503 when Pi cannot be asked or payments are not configured. A 503 is
 * ours, never a refusal of the person.
 */

const crypto = require('crypto');
const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const pay = require('../lib/pi_payments');

const router = express.Router();

/** Pi retries approval and completion every ~10 s; 30 a minute is well above that. */
const limit = rateLimit({ name: 'pi_payments', windowMs: 60000, max: 30, key: userKey });

/** Test seam: { fetch, apiKey, apiBase } for lib/pi_payments, and { env }. */
function deps() { return router._deps || {}; }
function env() { return deps().env || process.env; }
function isConfigured() {
  const d = deps();
  return d.apiKey !== undefined ? Boolean(d.apiKey) : pay.configured(env());
}

function unavailable(res, err, where) {
  if (err instanceof pay.PaymentsNotConfigured) {
    return res.status(503).json({ error: 'payments_not_configured' });
  }
  // The class and Pi's status line only: the key is in a header we never log.
  console.error(`Pi payments ${where} unavailable: ${err.constructor.name}: ${err.message}`);
  return res.status(503).json({ error: 'payments_unavailable' });
}

async function piUidOf(userId) {
  const [rows] = await pool.execute('SELECT pi_uid FROM users WHERE id = ? LIMIT 1', [userId]);
  const uid = rows[0] && rows[0].pi_uid;
  return typeof uid === 'string' && uid ? uid : null;
}

async function orderOf(orderId, userId) {
  const [rows] = await pool.execute(
    'SELECT id, user_id, kind, amount, payment_id, txid, status FROM pi_payments WHERE id = ? AND user_id = ? LIMIT 1',
    [orderId, userId]);
  return rows[0] || null;
}

router.get('/tips', authMiddleware, limit, (req, res) => {
  if (!isConfigured()) return res.json({ enabled: false });
  return res.json({ enabled: true, amounts: pay.tipAmounts(env()), memo: pay.TIP_MEMO });
});

router.post('/tips', authMiddleware, limit, async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'payments_not_configured' });
  const offered = pay.tipAmounts(env()).find((a) => pay.sameAmount(a, req.body && req.body.amount));
  if (offered === undefined) return res.status(400).json({ error: 'amount_not_offered' });
  try {
    if (!(await piUidOf(req.user.user_id))) return res.status(403).json({ error: 'pi_account_required' });
    const id = crypto.randomBytes(12).toString('hex');
    const now = new Date();
    await pool.execute(
      'INSERT INTO pi_payments (id, user_id, kind, amount, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
      [id, req.user.user_id, pay.TIP_KIND, offered, 'created', now, now]);
    return res.json({
      orderId: id,
      amount: offered,
      memo: pay.TIP_MEMO,
      metadata: { orderId: id, kind: pay.TIP_KIND },
    });
  } catch (err) {
    console.error('Pi tip order error:', err.constructor.name);
    return res.status(500).json({ error: 'order_failed' });
  }
});

/**
 * The payment as Pi holds it, matched to this person and their order, or the
 * answer to send when it cannot be. Returns { payment, order } or null after
 * responding.
 */
async function matched(req, res, paymentId, where) {
  if (typeof paymentId !== 'string' || !pay.PAYMENT_ID_RE.test(paymentId)) {
    res.status(400).json({ error: 'payment_id_required' });
    return null;
  }
  if (!isConfigured()) {
    res.status(503).json({ error: 'payments_not_configured' });
    return null;
  }
  const piUid = await piUidOf(req.user.user_id);
  if (!piUid) {
    res.status(403).json({ error: 'pi_account_required' });
    return null;
  }
  let payment;
  try {
    payment = await pay.getPayment(paymentId, deps());
  } catch (err) {
    unavailable(res, err, where);
    return null;
  }
  if (!payment) {
    res.status(404).json({ error: 'payment_unknown' });
    return null;
  }
  // Whose it is, before anything about it is looked up: another person's
  // payment id learns nothing here but that it is not theirs.
  if (payment.user_uid !== piUid) {
    res.status(403).json({ error: 'not_your_payment' });
    return null;
  }
  const orderId = payment.metadata && payment.metadata.orderId;
  const order = typeof orderId === 'string' ? await orderOf(orderId, req.user.user_id) : null;
  const reasons = pay.mismatches(payment, order, piUid);
  if (reasons.length) {
    res.status(409).json({ error: 'payment_mismatch', reasons });
    return null;
  }
  return { payment, order };
}

router.post('/approve', authMiddleware, limit, async (req, res) => {
  const paymentId = req.body && req.body.paymentId;
  try {
    const m = await matched(req, res, paymentId, 'approve');
    if (!m) return undefined;
    const { payment, order } = m;
    if (order.payment_id && order.payment_id !== paymentId) {
      return res.status(409).json({ error: 'order_already_paid' });
    }
    if (!order.payment_id) {
      // Written before Pi is asked to approve. `payment_id IS NULL` makes two
      // racing approvals for one order resolve to one winner; the unique key
      // stops one payment claiming two orders.
      let claimed;
      try {
        [claimed] = await pool.execute(
          'UPDATE pi_payments SET payment_id = ?, updated_at = ? WHERE id = ? AND user_id = ? AND payment_id IS NULL',
          [paymentId, new Date(), order.id, req.user.user_id]);
      } catch (err) {
        if (err.code === 'ER_DUP_ENTRY') return res.status(409).json({ error: 'payment_already_used' });
        throw err;
      }
      if (claimed.affectedRows !== 1) {
        const now = await orderOf(order.id, req.user.user_id);
        if (!now || now.payment_id !== paymentId) return res.status(409).json({ error: 'order_already_paid' });
      }
    }
    if (!(payment.status && payment.status.developer_approved)) {
      try {
        await pay.approvePayment(paymentId, deps());
      } catch (err) {
        return unavailable(res, err, 'approve');
      }
    }
    await pool.execute(
      'UPDATE pi_payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?',
      ['approved', new Date(), order.id, 'created']);
    return res.json({ ok: true, status: 'approved' });
  } catch (err) {
    console.error('Pi tip approve error:', err.constructor.name);
    return res.status(500).json({ error: 'approve_failed' });
  }
});

/** Complete a matched payment whose transaction Pi reports as `txid`. */
async function complete(req, res, m, paymentId, txid) {
  const { payment, order } = m;
  // Approval went through this server, which wrote the payment id first; a
  // payment this order never claimed is not one we approved.
  if (order.payment_id !== paymentId) return res.status(409).json({ error: 'not_approved_here' });
  const tx = payment.transaction;
  if (!tx || tx.txid !== txid) return res.status(409).json({ error: 'txid_mismatch' });
  if (order.status === 'completed') return res.json({ ok: true, status: 'completed' });

  const [kept] = await pool.execute(
    'UPDATE pi_payments SET txid = ?, updated_at = ? WHERE id = ? AND (txid IS NULL OR txid = ?)',
    [txid, new Date(), order.id, txid]);
  if (kept.affectedRows !== 1) return res.status(409).json({ error: 'txid_mismatch' });

  if (!(payment.status && payment.status.developer_completed)) {
    try {
      await pay.completePayment(paymentId, txid, deps());
    } catch (err) {
      // Not complete: nothing is recorded as received (Pi's rule).
      return unavailable(res, err, 'complete');
    }
  }
  // The one transition a tip has. A tip grants nothing, so this is the whole
  // of fulfilment, and `status <> 'completed'` keeps it to once.
  await pool.execute(
    'UPDATE pi_payments SET status = ?, updated_at = ? WHERE id = ? AND status <> ?',
    ['completed', new Date(), order.id, 'completed']);
  return res.json({ ok: true, status: 'completed' });
}

router.post('/complete', authMiddleware, limit, async (req, res) => {
  const paymentId = req.body && req.body.paymentId;
  const txid = req.body && req.body.txid;
  if (typeof txid !== 'string' || !pay.TXID_RE.test(txid)) {
    return res.status(400).json({ error: 'txid_required' });
  }
  try {
    const m = await matched(req, res, paymentId, 'complete');
    if (!m) return undefined;
    return await complete(req, res, m, paymentId, txid);
  } catch (err) {
    console.error('Pi tip complete error:', err.constructor.name);
    return res.status(500).json({ error: 'complete_failed' });
  }
});

/**
 * A payment the SDK reported as incomplete at sign-in: submitted to the
 * blockchain, not yet completed by us. Pi requires it to be completed before
 * the person can make another. The txid is Pi's, read back, not the page's.
 */
router.post('/incomplete', authMiddleware, limit, async (req, res) => {
  const paymentId = req.body && req.body.paymentId;
  try {
    const m = await matched(req, res, paymentId, 'incomplete');
    if (!m) return undefined;
    const tx = m.payment.transaction;
    if (!tx || typeof tx.txid !== 'string' || !pay.TXID_RE.test(tx.txid)) {
      return res.status(409).json({ error: 'not_submitted' });
    }
    return await complete(req, res, m, paymentId, tx.txid);
  } catch (err) {
    console.error('Pi tip incomplete error:', err.constructor.name);
    return res.status(500).json({ error: 'complete_failed' });
  }
});

module.exports = router;
