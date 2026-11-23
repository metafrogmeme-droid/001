'use strict';
/**
 * Email preferences: whether a member still wants the mail that is not a
 * security notice (the plan notice and the one-time first-steps mail), and
 * the signed link that lets them say no from the mail itself.
 *
 * THE LINK IS NOT A LOGIN. It names one member and one purpose, and the only
 * thing it can do is read or flip one column, `users.email_opted_out_at`. It
 * sets no cookie, mints no session and returns nothing about the account.
 * Forwarding a plan notice lets the recipient change one preference, which
 * is the trade every unsubscribe link makes.
 *
 * THE KEY. HMAC-SHA256(BOT_SYNC_SECRET, 'runeclaw-email-prefs-v1'), the same
 * derivation server.js uses for the session key, from a secret the web
 * process already refuses to start without (server.js, the BOT_SYNC_SECRET
 * check). Not JWT_SECRET: on a deployment that sets none, server.js derives
 * one at boot, and the onboarding job (scripts/email_onboarding_job.js) is a
 * separate process that would never see that value, so its links would not
 * verify. BOT_SYNC_SECRET is in the environment or the secrets vault for
 * both. It is read on every call, never at load, so a test or a script that
 * sets it late is honoured. Rotating BOT_SYNC_SECRET retires every link
 * already sent; a member can still sign in and open /email-preferences.
 *
 * Token: `<user id>.<base64url HMAC(key, 'email-prefs:v1:<user id>')>`,
 * compared in constant time. The id is the users row id, which is stable;
 * an email address is not the secret and is not in the link.
 *
 * UNREADABLE IS NEVER ZERO. `optedOut` throws when the row or the column
 * cannot be read, and lib/account_mail.js then does not send: a preference
 * nobody could read is not "opted in". Security mail never asks.
 */
const crypto = require('crypto');

const KEY_PURPOSE = 'runeclaw-email-prefs-v1';
const UID = /^[1-9][0-9]{0,18}$/;
const TOKEN = /^([1-9][0-9]{0,18})\.([A-Za-z0-9_-]{43})$/;
const MIN_SECRET = 32;

class NoKeyError extends Error {
  constructor() {
    super('email preferences need BOT_SYNC_SECRET (32 characters or more)');
    this.name = 'NoKeyError';
    this.code = 'EMAIL_PREFS_NO_KEY';
  }
}

function _key(env = process.env) {
  const secret = String(env.BOT_SYNC_SECRET || '');
  if (secret.length < MIN_SECRET) return null;
  return crypto.createHmac('sha256', secret).update(KEY_PURPOSE).digest();
}

/** Can links be signed and checked on this deployment? */
function available(env) {
  return _key(env) !== null;
}

function _mac(key, id) {
  return crypto.createHmac('sha256', key).update(`email-prefs:v1:${id}`).digest('base64url');
}

/** The link token for a users row id. Throws without a key. */
function tokenFor(userId, env) {
  const key = _key(env);
  if (!key) throw new NoKeyError();
  const id = String(userId);
  if (!UID.test(id) || !Number.isSafeInteger(Number(id))) throw new Error('not a user id');
  return `${id}.${_mac(key, id)}`;
}

/**
 * The users row id a token names, or null when it is not one this
 * deployment signed. Throws NoKeyError when there is no key to check with,
 * which is a different answer from "this link is not valid".
 */
function verify(token, env) {
  const key = _key(env);
  if (!key) throw new NoKeyError();
  const m = TOKEN.exec(String(token == null ? '' : token).trim());
  if (!m) return null;
  const id = Number(m[1]);
  if (!Number.isSafeInteger(id)) return null;
  const want = Buffer.from(_mac(key, m[1]));
  const got = Buffer.from(m[2]);
  if (want.length !== got.length || !crypto.timingSafeEqual(want, got)) return null;
  return id;
}

/** The page a mail's unsubscribe line opens. */
function preferencesUrl(base, userId, env) {
  return `${String(base).replace(/\/+$/, '')}/email-preferences?token=${tokenFor(userId, env)}`;
}

/** The RFC 8058 one-click endpoint the List-Unsubscribe header names. */
function oneClickUrl(base, userId, env) {
  return `${String(base).replace(/\/+$/, '')}/api/email-preferences/unsubscribe?token=${tokenFor(userId, env)}`;
}

function _pool(deps) {
  return (deps && deps.pool) || require('../db').pool;
}

/**
 * The member's row as this module reads it: { id, plan, opted_out } or null
 * when there is no such row. Throws when the column is not there to read.
 */
async function readRow(userId, deps) {
  const [rows] = await _pool(deps).execute(
    'SELECT id, plan, email_opted_out_at FROM users WHERE id = ?', [Number(userId)]);
  const row = rows && rows[0];
  if (!row) return null;
  if (!Object.prototype.hasOwnProperty.call(row, 'email_opted_out_at')) {
    throw new Error('users.email_opted_out_at was not returned');
  }
  return { id: Number(row.id), plan: row.plan, opted_out: row.email_opted_out_at != null };
}

/** Has this member opted out? Throws when that cannot be read. */
async function optedOut(userId, deps) {
  const row = await readRow(userId, deps);
  if (!row) throw new Error('no such member');
  return row.opted_out;
}

/** Opt out (true) or back in (false). Resolves to whether a row changed. */
async function setOptOut(userId, out, now = new Date(), deps) {
  const [r] = await _pool(deps).execute(
    'UPDATE users SET email_opted_out_at = ? WHERE id = ?', [out ? now : null, Number(userId)]);
  return !!(r && r.affectedRows === 1);
}

module.exports = {
  available, tokenFor, verify, preferencesUrl, oneClickUrl,
  readRow, optedOut, setOptOut, NoKeyError, KEY_PURPOSE,
};
