'use strict';
/**
 * /api/email-preferences — turn the mail that is not a security notice off
 * or back on: the plan notice and the one-time first-steps mail. Security
 * mail (verify and reset links, account-change notices) is not on this page
 * and is always sent.
 *
 *   GET  /api/email-preferences[?token=]        → { subscribed, via }
 *   POST /api/email-preferences[?token=]        { subscribed: true|false }
 *   POST /api/email-preferences/unsubscribe?token=   RFC 8058 one-click
 *
 * WHOSE ROW. The link's, when a token is sent: the token is the whole
 * credential (lib/email_prefs.js), and a token that does not verify is
 * refused (400 invalid_link). It never falls back to whoever is signed in,
 * so a forwarded link opened in someone else's browser changes nothing of
 * theirs. With no token, the signed-in member's own row, which is the way
 * back when an old link stops verifying (the key is rotated with
 * BOT_SYNC_SECRET). Neither: 401.
 *
 * NOT A LOGIN. Nothing here sets a cookie or returns a session or anything
 * about the account beyond the one preference.
 *
 * GET never changes anything: mail scanners and link previewers open links,
 * and an unsubscribe that fired on a scan would opt people out who never
 * asked. The page's buttons POST. The one-click endpoint is a POST too, as
 * RFC 8058 requires, and ignores its body.
 *
 * One limiter for all three, keyed by the member a valid token names, so
 * one mail provider's one-click traffic is not one shared bucket; an invalid
 * token or the signed-in path counts against the caller's address.
 */
const express = require('express');
const { optionalAuth } = require('../auth');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const prefs = require('../lib/email_prefs');

const router = express.Router();
router.use(optionalAuth);

function _token(req) {
  const q = req.query && req.query.token;
  if (typeof q === 'string' && q !== '') return q;
  const b = req.body && req.body.token;
  if (typeof b === 'string' && b !== '') return b;
  return null;
}

function _limitKey(req) {
  const t = _token(req);
  if (t) {
    try {
      const uid = prefs.verify(t);
      if (uid) return `email_prefs:u:${uid}`;
    } catch (e) { /* no key: counted by address below */ }
  }
  return `email_prefs:ip:${ipKey(req)}`;
}

const prefsLimit = rateLimit({
  name: 'email_prefs',
  windowMs: 15 * 60 * 1000,
  max: 30,
  key: _limitKey,
  message: 'Too many requests for email preferences. Try again later.',
});
router.use(prefsLimit);

/**
 * { uid, via } for the row this request may read or change, or
 * { status, error } when it may change none.
 */
async function whose(req, { linkOnly = false } = {}) {
  const token = _token(req);
  if (token) {
    let uid;
    try {
      uid = prefs.verify(token);
    } catch (e) {
      if (e && e.code === 'EMAIL_PREFS_NO_KEY') return { status: 503, error: 'email_preferences_unavailable' };
      throw e;
    }
    if (!uid) return { status: 400, error: 'invalid_link' };
    const row = await prefs.readRow(uid);
    // An erased account's link names a tombstone: nothing of anybody's.
    if (!row || row.plan === 'deleted') return { status: 400, error: 'invalid_link' };
    return { uid, via: 'link', row };
  }
  if (linkOnly) return { status: 400, error: 'invalid_link' };
  const sessionUid = req.user && Number(req.user.user_id);
  if (!Number.isSafeInteger(sessionUid) || sessionUid <= 0) {
    return { status: 401, error: 'sign_in_or_use_the_link' };
  }
  const row = await prefs.readRow(sessionUid);
  if (!row || row.plan === 'deleted') return { status: 401, error: 'sign_in_or_use_the_link' };
  return { uid: sessionUid, via: 'session', row };
}

function _private(res) {
  res.set('Cache-Control', 'private, no-store');
}

router.get('/', async (req, res) => {
  _private(res);
  try {
    const w = await whose(req);
    if (w.error) return res.status(w.status).json({ error: w.error });
    return res.json({ subscribed: !w.row.opted_out, via: w.via });
  } catch (err) {
    console.error('Email preferences read error:', err && err.stack);
    return res.status(500).json({ error: 'Email preferences could not be read' });
  }
});

router.post('/', async (req, res) => {
  _private(res);
  try {
    const subscribed = req.body && req.body.subscribed;
    if (typeof subscribed !== 'boolean') {
      return res.status(400).json({ error: 'subscribed must be true or false' });
    }
    const w = await whose(req);
    if (w.error) return res.status(w.status).json({ error: w.error });
    if (!(await prefs.setOptOut(w.uid, !subscribed, new Date()))) {
      return res.status(500).json({ error: 'Email preferences could not be saved' });
    }
    return res.json({ subscribed, via: w.via });
  } catch (err) {
    console.error('Email preferences write error:', err && err.stack);
    return res.status(500).json({ error: 'Email preferences could not be saved' });
  }
});

router.post('/unsubscribe', async (req, res) => {
  _private(res);
  try {
    const w = await whose(req, { linkOnly: true });
    if (w.error) return res.status(w.status).json({ error: w.error });
    if (!(await prefs.setOptOut(w.uid, true, new Date()))) {
      return res.status(500).json({ error: 'Email preferences could not be saved' });
    }
    return res.json({ subscribed: false, via: 'link' });
  } catch (err) {
    console.error('Email preferences one-click error:', err && err.stack);
    return res.status(500).json({ error: 'Email preferences could not be saved' });
  }
});

module.exports = router;
