const express = require('express');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const { pool } = require('./db');
const mailer = require('./lib/mailer');
const oauth2 = require('./lib/oauth2');
const { VENUES } = require('./lib/venues');
const { tokenFromRequest, setSession, clearSession } = require('./lib/session_cookie');
const { stepUpBlock } = require('./lib/stepup');
const { postGateway, isConfigured } = require('./lib/gateway');
const gateway = { isConfigured };
const { erasurePlan } = require('./lib/account_erasure');
const { aggregateStats } = require('./public/js/trade-stats');
const { secLog } = require('./lib/seclog');

// Self-custody sign-in verifier — optional dependency. Lazy so the app still
// boots (and every other auth route works) if ethers isn't installed on a
// deployment; the wallet routes then report themselves unavailable (503).
let _ethers = null;
try { _ethers = require('ethers'); } catch (_) { /* wallet sign-in disabled */ }

const router = express.Router();

// CRITICAL: No fallback secret. Refuse to start if unset or too short.
const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET || JWT_SECRET.length < 32) {
  // This exit happens at MODULE SCOPE, during require('./auth') — so from the
  // outside it does not look like a crash, it looks like the app never
  // finished loading. A 29-character JWT_SECRET crash-looped production for
  // hours behind exactly that disguise, and the old message ("must be set")
  // read as "unset" when the real problem was "set, but three characters too
  // short".
  //
  // So: say WHICH of the two it is, and how short. fs.writeSync rather than
  // console.error because the process leaves on the next line and a queued
  // write to a container's stdout pipe can be lost. The length is reported;
  // the value never is.
  const n = (JWT_SECRET || '').length;
  require('fs').writeSync(2,
    `FATAL: JWT_SECRET ${n ? `is only ${n} characters` : 'is not set'} — it must be at `
    + 'least 32. Refusing to start.\n'
    + '  Either unset it, in which case a strong secret is derived automatically from\n'
    + '  BOT_SYNC_SECRET, or generate one:\n'
    + '    node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'hex\'))"\n');
  process.exit(1);
}
// Session lifetime. Was '1h', which -- with no refresh-token flow ever built --
// logged users out every hour and broke every authenticated panel mid-session.
// Default to a generous 30d so day-to-day use stays signed in; operators who
// want shorter-lived tokens can set JWT_EXPIRY (e.g. '12h', '7d') in the env.
const JWT_EXPIRY = process.env.JWT_EXPIRY || '30d';

// Pin the algorithm on VERIFY, not just on sign.
//
// jsonwebtoken v9 already rejects `alg: none` when a key is supplied, and the
// secret here is symmetric, so the classic RS256->HS256 confusion does not
// apply — this is not a live hole. It is one line of defence-in-depth on the
// session boundary, and it stops the question having to be re-derived by the
// next reader: the token is HS256 or it is not a token.
const JWT_VERIFY_OPTS = { algorithms: ['HS256'] };

// OAuth providers (optional; each endpoint 503s cleanly when its secret is
// unset, so the site runs fine with only email/password until configured).
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || process.env.BOT_TOKEN || '';
const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '';
const TELEGRAM_AUTH_MAX_AGE_S = 86400; // reject widget payloads older than 24h

// Verify a Telegram Login Widget payload (pure; exported for tests).
// Per core.telegram.org/widgets/login#checking-authorization:
//   secret = SHA256(bot_token);  hash = HMAC_SHA256(data_check_string, secret)
// where data_check_string is the sorted "key=value" lines (excluding hash).
function verifyTelegramAuth(data, botToken, nowSec) {
  if (!data || !data.hash || !botToken) return false;
  const now = nowSec || Math.floor(Date.now() / 1000);
  const authDate = parseInt(data.auth_date, 10);
  if (!Number.isFinite(authDate) || now - authDate > TELEGRAM_AUTH_MAX_AGE_S) return false;
  const checkString = Object.keys(data)
    .filter((k) => k !== 'hash')
    .sort()
    .map((k) => `${k}=${data[k]}`)
    .join('\n');
  const secret = crypto.createHash('sha256').update(botToken).digest();
  const hmac = crypto.createHmac('sha256', secret).update(checkString).digest('hex');
  // Constant-time compare to avoid timing leaks.
  const a = Buffer.from(hmac, 'hex');
  const b = Buffer.from(String(data.hash), 'hex');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Rate limiting: per-IP sliding window
const loginAttempts = new Map(); // ip -> { count, firstAttempt, lockedUntil }
const RATE_LIMIT_WINDOW = 15 * 60 * 1000; // 15 min
const RATE_LIMIT_MAX = 10;
const LOCKOUT_DURATION = 5 * 60 * 1000; // 5 min lockout after max attempts

// RC-AUD-026: per-account (per-email) failed-login throttle. The per-IP limiter
// above does not stop distributed / rotating-IP credential stuffing against a
// single account. This in-process counter mirrors the per-IP one, keyed by the
// normalized email, so repeated failures against one account lock it out
// regardless of source IP.
const accountAttempts = new Map(); // email -> { count, firstAttempt, lockedUntil }
const ACCOUNT_RATE_LIMIT_MAX = 8;

function _pruneAttemptMap(map) {
  const now = Date.now();
  for (const [key, entry] of map) {
    if (now - entry.firstAttempt > RATE_LIMIT_WINDOW && (!entry.lockedUntil || now > entry.lockedUntil)) {
      map.delete(key);
    }
  }
  // Cap map size to prevent unbounded growth
  if (map.size > 10000) {
    const entries = [...map.entries()].sort((a, b) => a[1].firstAttempt - b[1].firstAttempt);
    for (let i = 0; i < entries.length - 5000; i++) map.delete(entries[i][0]);
  }
}

function pruneRateLimits() {
  _pruneAttemptMap(loginAttempts);
  _pruneAttemptMap(accountAttempts);
}
const _pruneTimer = setInterval(pruneRateLimits, 60000);
if (_pruneTimer.unref) _pruneTimer.unref(); // don't hold the event loop open (matches lib/rate_limit.js)

function checkRateLimit(ip) {
  const now = Date.now();
  const entry = loginAttempts.get(ip);
  if (!entry) return true;
  if (entry.lockedUntil && now < entry.lockedUntil) return false;
  if (now - entry.firstAttempt > RATE_LIMIT_WINDOW) { loginAttempts.delete(ip); return true; }
  return entry.count < RATE_LIMIT_MAX;
}

function recordAttempt(ip) {
  const now = Date.now();
  const entry = loginAttempts.get(ip) || { count: 0, firstAttempt: now };
  entry.count++;
  if (entry.count >= RATE_LIMIT_MAX) entry.lockedUntil = now + LOCKOUT_DURATION;
  loginAttempts.set(ip, entry);
}

// RC-AUD-026: per-account counterparts to the per-IP helpers above.
function checkAccountLockout(email) {
  const now = Date.now();
  const entry = accountAttempts.get(email);
  if (!entry) return true;
  if (entry.lockedUntil && now < entry.lockedUntil) return false;
  if (now - entry.firstAttempt > RATE_LIMIT_WINDOW) { accountAttempts.delete(email); return true; }
  return entry.count < ACCOUNT_RATE_LIMIT_MAX;
}

function recordAccountFailure(email) {
  const now = Date.now();
  const entry = accountAttempts.get(email) || { count: 0, firstAttempt: now };
  entry.count++;
  if (entry.count >= ACCOUNT_RATE_LIMIT_MAX) entry.lockedUntil = now + LOCKOUT_DURATION;
  accountAttempts.set(email, entry);
}

function clearAccountFailures(email) {
  accountAttempts.delete(email);
}

// -- Middleware --

/**
 * Has this token been revoked since it was issued?
 *
 * A JWT is a bearer credential with no server-side state, so `jwt.verify`
 * answers "was this signed by us and is it still inside its lifetime" and
 * NOTHING about whether the session is still meant to exist. With a 30-day
 * default that made a stolen token a month of unrevocable account access —
 * on a platform where an account can submit exchange API keys.
 *
 * The fix is the one `bot/api/token_store.py` has run since RC-AUD-020: a
 * per-user epoch, stamped into the token at issue and bumped whenever every
 * outstanding session should die (logout, password change). A token whose
 * epoch is behind the user's current one is refused.
 *
 * ONE READER for both middlewares, deliberately. They ask the same question
 * and answer it differently — 401 versus degrade-to-anonymous — and two
 * copies of the check is how one of them stops checking.
 *
 * Read per request rather than cached. That is one primary-key lookup, and it
 * makes revocation immediate; a cache would make it eventually-immediate,
 * which is a different promise than the one "log out everywhere" makes.
 *
 * Fails CLOSED on a database error, unlike the Python side's availability
 * trade-off: there, a Redis blip must not break auth for a running trading
 * bot; here the caller is a browser that can retry, and the cost of guessing
 * wrong is honouring a token that may have been revoked.
 */
async function tokenIsCurrent(payload) {
  if (!payload || payload.user_id == null) return false;
  const [rows] = await pool.execute(
    'SELECT token_epoch FROM users WHERE id = ?', [payload.user_id]);
  // NO ROW IS NOT EVIDENCE OF REVOCATION. The first version of this returned
  // false here — "user deleted, token is dead" — which sounds right and
  // conflates two different questions. This function asks "has this session
  // been revoked"; a missing row answers "I cannot tell", and the repo's own
  // rule is that absent is not a measurement.
  //
  // It is also the difference between a focused change and a broad one: the
  // reject-on-missing version 401'd every caller whose user row lives
  // somewhere this query cannot see, which is a much larger behavioural change
  // than the one being made, arrived at by accident.
  //
  // "A deleted user must not authenticate" is a real and separate property. It
  // deserves its own explicit check rather than riding in as a side effect of
  // the revocation lookup — every route already scopes its queries by
  // user_id, so a deleted user sees nothing regardless.
  if (!rows.length) return true;
  const current = Number(rows[0].token_epoch) || 0;
  // A token minted before this column existed carries no epoch. Treating that
  // as 0 is what keeps the deploy from logging everybody out; it is safe
  // because a revocation BUMPS the epoch above 0, so any pre-existing token
  // is refused the moment it actually matters.
  const minted = Number(payload.epoch) || 0;
  return minted >= current;
}

async function authMiddleware(req, res, next) {
  // Authorization header first, then the HttpOnly cookie. The order is the
  // reason this change moves nothing for existing callers: anything already
  // sending a Bearer token takes the identical path it always did.
  const raw = tokenFromRequest(req);
  if (!raw) {
    return res.status(401).json({ error: 'Missing token' });
  }
  let payload;
  try {
    payload = jwt.verify(raw, JWT_SECRET, JWT_VERIFY_OPTS);
  } catch {
    return res.status(401).json({ error: 'Invalid token' });
  }
  try {
    if (!await tokenIsCurrent(payload)) {
      return res.status(401).json({ error: 'Session expired' });
    }
  } catch (err) {
    console.error('Token revocation check failed:', err.stack || err.message);
    return res.status(503).json({ error: 'auth_unavailable' });
  }
  req.user = payload;
  next();
}

/**
 * Auth that does not REFUSE — it only identifies.
 *
 * `req.user` is set when a valid Bearer token is present and left undefined
 * otherwise; the request always continues. For a surface that must stay
 * reachable anonymously but must show LESS to an anonymous caller, this is the
 * difference between "who are you" and "you may not pass", and only the first
 * question is being asked.
 *
 * An invalid token is treated as anonymous rather than rejected, deliberately:
 * an expired session on a page that also serves the public should degrade to
 * the public view, not to a 401. That is safe here precisely because `req.user`
 * only ever ADDS to a response.
 */
async function optionalAuth(req, _res, next) {
  const raw = tokenFromRequest(req);
  if (raw) {
    try {
      const payload = jwt.verify(raw, JWT_SECRET, JWT_VERIFY_OPTS);
      // A revoked token identifies nobody. Degrading to anonymous rather than
      // refusing is this function's whole contract — but it must degrade, not
      // sail through: `req.user` only ever ADDS to a response, and a logged-out
      // session must stop adding.
      if (await tokenIsCurrent(payload)) req.user = payload;
    } catch {
      /* anonymous — an unreadable or revoked token is simply not a caller */
    }
  }
  next();
}

// -- Helpers --

function signToken(user) {
  // The epoch travels IN the token. Stamping it at issue is what lets a later
  // bump invalidate this specific token without a revocation list to store,
  // scan or expire.
  return jwt.sign(
    { user_id: user.id, email: user.email, epoch: Number(user.token_epoch) || 0 },
    JWT_SECRET, { expiresIn: JWT_EXPIRY });
}

/**
 * End every outstanding session for a user. Returns the new epoch.
 *
 * `UPDATE ... SET token_epoch = token_epoch + 1` is done in SQL rather than
 * read-then-write so two concurrent revocations cannot both read N and both
 * write N+1 — which would leave one of the two logouts unenforced, and the
 * user believing otherwise.
 */
async function revokeUserTokens(userId) {
  await pool.execute(
    'UPDATE users SET token_epoch = token_epoch + 1 WHERE id = ?', [userId]);
  const [rows] = await pool.execute(
    'SELECT token_epoch FROM users WHERE id = ?', [userId]);
  return rows.length ? Number(rows[0].token_epoch) || 0 : 0;
}

// The standard paper starting stake, same constant routes/leaderboard.js
// scores percentages against.
const PAPER_BASE = 10000;

async function getUserEquity(userId) {
  // Use latest synced equity snapshot if available
  const [snapRows] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]
  );
  if (snapRows.length > 0) {
    // A row can exist with an unparseable equity; parseFloat would hand back
    // NaN, which survives arithmetic and JSON-serializes to null anyway. Say
    // null deliberately instead of arriving there by accident.
    const e = parseFloat(snapRows[0].equity);
    return Number.isFinite(e) ? e : null;
  }
  // The operator (BOT_USER_ID) trades LIVE — there is no paper baseline to fall
  // back to. If no real synced snapshot exists, the honest answer is null
  // ("unavailable"), never a fabricated $10k. Paper users below still get the
  // paper baseline, which is correct for them.
  const BOT_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;
  if (userId === BOT_USER_ID) return null;

  // Paper users: baseline + realized P&L — but only over a book we can READ.
  // This was `COALESCE(SUM(pnl), 0)` plus a second `|| 0` in JS, so a paper
  // user whose closed trades were all unpriced read exactly $10,000.00: the
  // same number as a brand-new account and as one traded to precise
  // break-even. `trades.pnl` is nullable and a CLOSED row with no recorded
  // P&L genuinely occurs (routes/sync.js forwards the gateway's `pnl`
  // uncoerced), so that was not a hypothetical.
  //
  // routes/portfolio.js, routes/leaderboard.js, routes/sync.js and
  // routes/trades.js were each rewritten to score the priced rows explicitly.
  // This function was the fourth path and was missed for the same reason
  // portfolio.js's own comment gives for having been missed: nobody was
  // reading it while auditing the trade routes. Same query, same reader
  // (aggregateStats), so the paths cannot drift apart again.
  const [pnlRows] = await pool.execute(
    "SELECT COUNT(*) AS total, " +
    "SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) AS scored, " +
    "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, " +
    "SUM(pnl) AS net_pnl " +
    "FROM trades WHERE user_id = ? AND status = ?",
    [userId, 'CLOSED']);
  const closed = aggregateStats(pnlRows[0]);

  // No closed trades at all is a READING, not an absence: a paper account that
  // has not closed anything holds exactly the starting stake.
  if (closed.total === 0) return PAPER_BASE;

  // Any unpriced row makes the sum PARTIAL, and a partial total printed as a
  // whole one is the row of CLAUDE.md's table this function was sitting on.
  // Equity is a single scalar with nowhere to carry a caveat, so it guards
  // rather than omits. null is already a value this endpoint returns (the
  // operator branch above), so every consumer already handles it.
  if (closed.unpriced > 0 || closed.net_pnl === null) return null;

  return PAPER_BASE + closed.net_pnl;
}

// The single place that turns a user into the session JSON the SPA stores after
// ANY successful auth — register, login, Telegram/Google widget, OAuth callback.
// Each of those used to hand-build this object and had drifted: telegram_linked
// was hardcoded `false` by register/google, `true` by telegram, and OMITTED
// entirely by the OAuth callback — so a Google/Discord user who HAD linked
// Telegram looked unlinked to the app (and lost access to live controls). Always
// read the authoritative flags back from the row so every entry point agrees.
// `extra` carries per-route additions (register's email_pending, the OAuth
// callback's provider/linked markers).
async function sessionResponse(user, extra = {}) {
  const [rows] = await pool.execute(
    'SELECT id, email, plan, telegram_linked, email_verified, referral_code, '
    + 'token_epoch FROM users WHERE id = ?',
    [user.id]);
  const u = rows[0] || user;
  // The epoch is read back here for the same reason the flags above are: this
  // is the one funnel every token goes through, and stamping it from the
  // caller's partial user object would mint tokens at epoch 0 forever. That
  // is not a stale-looking number — after any revocation it is a token born
  // already revoked, so nobody could log back in after logging out.
  const token = signToken({ id: u.id, email: u.email, token_epoch: u.token_epoch });
  const equity = await getUserEquity(u.id);
  return {
    token, user_id: u.id, email: u.email, plan: u.plan || 'free',
    telegram_linked: !!u.telegram_linked, email_verified: !!u.email_verified,
    referral_code: u.referral_code || null,
    equity, ...extra,
  };
}

/**
 * `sessionResponse`, plus the HttpOnly cookie, sent.
 *
 * The token keeps travelling in the BODY as well, and that is not an oversight
 * — the MCP tools, the Telegram link flow and every curl in the runbook read
 * it from there, and none of them is an XSS target. What changes is that the
 * browser no longer has to keep its copy anywhere a script can reach.
 *
 * One funnel, for the same reason `sessionResponse` is one: six call sites mint
 * sessions, and a cookie set at five of them is a login that works everywhere
 * except the path nobody tested.
 */
async function sendSession(req, res, user, extra = {}) {
  const body = await sessionResponse(user, extra);
  setSession(req, res, body.token);
  return res.json(body);
}

// A short, URL-safe, non-enumerable invite code (8 chars). Random rather than
// derived from the user id so a code never leaks the account id or count.
function genReferralCode() {
  return crypto.randomBytes(6).toString('base64url');
}

// Referral tiers — milestones by how many friends signed up through your link.
//
// A PERK IS A PROMISE, SO EACH ONE CARRIES WHETHER IT IS IN FORCE.
//
// This table used to be five prose strings and a comment saying they were
// "aspirational and mostly land with the token/billing layer". The comment was
// true and the card could not show it: `Protocol revenue share when the token
// launches.` rendered in the same voice, at the same weight, beside the same
// gold chip as `Your invite link is live.` — one of which is true today and one
// of which needs a token that docs/TOKEN_ROADMAP.md opens by saying does not
// exist. The code knew and the surface did not say.
//
// Two of them were worse than aspirational: "Priority support" and "Early
// access to new agents & features" are the PAID Pro and Elite plans' own
// selling points, offered here for one and three invites and granted by
// nothing. Connector's replacement is the perk that was already REAL and went
// unmentioned — app/lib/duel_squads.js builds squads out of exactly this
// referral graph, and one recruit is what turns you into a captain.
//
// `state` is read by app/public/js/referral-tier-model.js, which paints planned
// perks differently AND prints `requires` beside them, because colour is a
// claim and colour alone does not survive greyscale or a screen reader. This
// endpoint still grants nothing: `referralTier` has one caller, right below,
// and nothing in the tree gates a feature on a referral count.
const REFERRAL_TIERS = [
  { at: 0, name: 'Starter', state: 'live', requires: null,
    perk: 'Your invite link is live — share it to climb.' },
  { at: 1, name: 'Connector', state: 'live', requires: null,
    perk: 'Everyone who joins on your link rides in your squad on the Daily '
      + 'Duel board — you both need a public handle for it to show.' },
  { at: 3, name: 'Advocate', state: 'planned',
    perk: 'A say in what gets built next.',
    requires: 'Not in force yet — nothing in the product is weighted by '
      + 'referrals today, so this is an intention rather than a benefit.' },
  { at: 10, name: 'Ambassador', state: 'planned',
    perk: 'Fee credits.',
    requires: 'Would ride on the $RCLAW token, which does not exist yet — no '
      + 'token has launched and no sale has run.' },
  { at: 25, name: 'Legend', state: 'planned',
    perk: 'A share of protocol revenue.',
    requires: 'Would ride on the $RCLAW token, which does not exist yet — no '
      + 'token has launched and no sale has run. Not an offer.' },
];

// The tier for a referral count, or NULL when the count is not a measurement.
//
// Not `Math.max(0, Number(count) || 0)`: that answered Starter for null,
// undefined, NaN, '' and 'abc' alike, and Starter is also a real and common
// true state — so five distinct failures were indistinguishable from the most
// ordinary success. A tier is a statement about how many people someone
// brought; computing one from a number nobody read makes that statement from
// no evidence. Callers omit the tier instead.
function referralTier(count) {
  if (!Number.isInteger(count) || count < 0) return null;
  let idx = 0;
  for (let i = 0; i < REFERRAL_TIERS.length; i++) {
    if (count >= REFERRAL_TIERS[i].at) idx = i;
  }
  const cur = REFERRAL_TIERS[idx];
  const nx = REFERRAL_TIERS[idx + 1] || null;
  return {
    tier: { name: cur.name, perk: cur.perk, index: idx,
            state: cur.state, requires: cur.requires },
    next: nx ? { name: nx.name, at: nx.at, remaining: Math.max(0, nx.at - count) } : null,
  };
}

// -- Account-management helpers (email verification + password reset) --

// Tokens are stored HASHED in the DB; only the raw token travels in the email
// link, so a DB leak can't be replayed to verify an address or reset a password.
function _hashToken(raw) {
  return crypto.createHash('sha256').update(raw).digest('hex');
}

const VERIFY_TTL_MS = 24 * 60 * 60 * 1000; // 24h
const RESET_TTL_MS = 30 * 60 * 1000;       // 30m

// Issue a verification token for a user and email the link. Best-effort: a
// mailer failure never breaks the calling flow (registration still succeeds).
// The welcome half of the first email. Kept as ONE message rather than two:
// a fresh account receiving a "welcome" and a "verify" back to back reads as a
// mailing list, and the verify link is the only thing either of them needs the
// reader to do.
//
// It states what the account actually gets, in the order it becomes true —
// paper now, Telegram link next, live only with operator approval. A welcome
// that implies live trading is one tap away is the same promise-then-refuse
// the bot's own onboarding was fixed for; the person just finds out later.
function _welcomeBlocks(link) {
  const text =
    'Welcome to RUNECLAW.\n\n'
    + 'Confirm your email to finish setting up (link valid 24h):\n'
    + `${link}\n\n`
    + 'What you can do straight away:\n'
    + '  - Paper trading and the dashboard — no setup, no exchange keys.\n'
    + '  - Ask the assistant about any market from the chat panel.\n\n'
    + 'Optional next steps:\n'
    + '  - Link Telegram from the dashboard to manage exchange keys and your\n'
    + '    live-trading controls, and to get alerts in Telegram.\n'
    + '  - Live trading additionally needs the operator to approve your\n'
    + '    account. Paper works without any of this.\n\n'
    + "If you didn't create an account, ignore this message.";
  const html =
    '<p>Welcome to <b>RUNECLAW</b>.</p>'
    + `<p><a href="${link}">Confirm your email</a> to finish setting up (valid 24h).</p>`
    + '<p><b>Straight away:</b> paper trading and the dashboard — no setup, no '
    + 'exchange keys — plus the assistant in the chat panel.</p>'
    + '<p><b>Optional:</b> link Telegram from the dashboard for exchange-key '
    + 'management, your live-trading controls and alerts. Live trading also '
    + 'needs operator approval; paper works without any of it.</p>'
    + '<p style="color:#888;font-size:12px">If you didn\'t create an account, '
    + 'ignore this message.</p>';
  return { text, html };
}

// Issue a verification token for a user and email the link. Best-effort: a
// mailer failure never breaks the calling flow (registration still succeeds).
// `welcome` sends the fuller first-contact version (registration); the resend
// path keeps the short one, since by then they know what the product is.
async function sendVerificationEmail(userId, email, { welcome = false } = {}) {
  try {
    const raw = crypto.randomBytes(32).toString('hex');
    const expires = new Date(Date.now() + VERIFY_TTL_MS);
    await pool.execute(
      'UPDATE users SET verify_token = ?, verify_token_expires = ? WHERE id = ?',
      [_hashToken(raw), expires, userId]
    );
    const base = mailer.baseUrl();
    const link = `${base}/verify?token=${raw}`;
    const body = welcome ? _welcomeBlocks(link) : {
      text: `Confirm your email to finish setting up RUNECLAW.\n\nOpen this link (valid 24h):\n${link}\n\nIf you didn't create an account, ignore this message.`,
      html: `<p>Confirm your email to finish setting up <b>RUNECLAW</b>.</p>`
        + `<p><a href="${link}">Verify my email</a> (valid 24h)</p>`
        + `<p style="color:#888;font-size:12px">If you didn't create an account, ignore this message.</p>`,
    };
    await mailer.sendMail({
      to: email,
      subject: welcome ? 'Welcome to RUNECLAW — confirm your email'
                       : 'Verify your RUNECLAW email',
      text: body.text,
      html: body.html,
    });
    return true;
  } catch (err) {
    console.error('sendVerificationEmail error:', err.stack || err.message);
    return false;
  }
}

// -- Routes --

router.post('/register', async (req, res) => {
  try {
    // Rate-limit registration per IP (reuses the login limiter) so the
    // endpoint can't be used for automated mass account creation — it was
    // the only unthrottled auth route (deep-audit finding).
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many attempts. Try again later.' });
    }
    recordAttempt(clientIp);

    const { email, password, ref } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });
    // Validate email format
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ error: 'Invalid email format' });
    if (password.length < 10) return res.status(400).json({ error: 'Password must be at least 10 characters' });

    const normalizedEmail = email.trim().toLowerCase();
    const hash = await bcrypt.hash(password, 12);
    const [result] = await pool.execute(
      'INSERT INTO users (email, password_hash) VALUES (?, ?)',
      [normalizedEmail, hash]
    );
    const userId = result.insertId;

    // Invite / referral bookkeeping. Best-effort — a referral hiccup must never
    // fail account creation. Mint this user's own share code, and if they
    // arrived via a valid ?ref= code, credit the referrer (self-referral and
    // unknown codes are ignored).
    try {
      await pool.execute('UPDATE users SET referral_code = ? WHERE id = ?',
        [genReferralCode(), userId]);
      const refCode = typeof ref === 'string' ? ref.trim() : '';
      if (refCode) {
        const [refRows] = await pool.execute(
          'SELECT id FROM users WHERE referral_code = ?', [refCode]);
        const referrerId = refRows[0] && refRows[0].id;
        if (referrerId && referrerId !== userId) {
          await pool.execute('UPDATE users SET referred_by = ? WHERE id = ?',
            [referrerId, userId]);
        }
      }
    } catch (e) {
      console.error('Referral bookkeeping failed:', e.message);
    }

    // Best-effort verification email (no-op when SMTP unconfigured). Fired
    // before responding so the token is persisted; never blocks on delivery
    // errors (sendVerificationEmail swallows them).
    await sendVerificationEmail(userId, normalizedEmail, { welcome: true });
    await sendSession(req, res, { id: userId, email: normalizedEmail },
      { email_pending: mailer.isConfigured() });
  } catch (err) {
    // Uniform response to prevent user enumeration (don't reveal ER_DUP_ENTRY)
    if (err.code === 'ER_DUP_ENTRY') return res.status(400).json({ error: 'Registration failed. Please try a different email.' });
    console.error('Register error:', err.stack || err.message);
    res.status(500).json({ error: 'Registration failed' });
  }
});

router.post('/login', async (req, res) => {
  try {
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many login attempts. Try again later.' });
    }

    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });

    const normalizedEmail = email.trim().toLowerCase();
    // RC-AUD-026: per-account lockout, in addition to the per-IP check above.
    if (!checkAccountLockout(normalizedEmail)) {
      return res.status(429).json({ error: 'Too many login attempts. Try again later.' });
    }

    const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', [normalizedEmail]);
    if (rows.length === 0) {
      recordAttempt(clientIp);
      recordAccountFailure(normalizedEmail);
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    const user = rows[0];
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      recordAttempt(clientIp);
      recordAccountFailure(normalizedEmail);
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    // 2FA (MH1): password alone is not a session when TOTP is enabled. The
    // second factor rides the SAME login call (totp_code, or a one-time
    // backup code) — no intermediate half-authenticated token to steal.
    // Failed/missing codes count toward the same lockout counters as bad
    // passwords, so codes can't be brute-forced past the rate limits.
    if (user.totp_enabled) {
      const totp = require('./lib/totp');
      const code = String((req.body || {}).totp_code || '').trim();
      if (!code) {
        return res.status(401).json({
          error: 'Two-factor code required', two_factor_required: true,
        });
      }
      if (!totp.verifyTotp(user.totp_secret, code)) {
        let backups = [];
        try { backups = JSON.parse(user.totp_backup_codes || '[]'); } catch (e) { backups = []; }
        const remaining = totp.consumeBackupCode(code, backups);
        if (remaining === null) {
          recordAttempt(clientIp);
          recordAccountFailure(normalizedEmail);
          return res.status(401).json({
            error: 'Invalid two-factor code', two_factor_required: true,
          });
        }
        // COMPARE-AND-SWAP, BECAUSE THE OBVIOUS WRITE RESURRECTS SPENT CODES.
        //
        // This was a bare `UPDATE ... SET totp_backup_codes = ?`, and the
        // read-modify-write around it had no transaction. `consumeBackupCode`
        // returns a COPY of the list with one hash removed, so two logins
        // racing on the same row do not merely both succeed — the second write
        // puts the first one's code back:
        //
        //   both read [c1, c2, c3]
        //   A spends c1 -> writes [c2, c3]
        //   B spends c2 -> writes [c1, c3]      <- c1 is valid again
        //
        // A spent backup code returning to the list is worse than the
        // double-use it starts as, and /2fa/enable tells the user in as many
        // words that each code "works a single time".
        //
        // The WHERE clause pins the exact bytes we read. A racing write changes
        // them, this UPDATE matches zero rows, and the login is refused rather
        // than served from a list we no longer understand. Fail-closed is right
        // here: a concurrent backup-code redemption on one account is either a
        // double-submit or an attack, and neither deserves a session.
        const [upd] = await pool.execute(
          'UPDATE users SET totp_backup_codes = ? WHERE id = ? AND totp_backup_codes <=> ?',
          [JSON.stringify(remaining), user.id, user.totp_backup_codes]);
        if (!upd || upd.affectedRows !== 1) {
          recordAccountFailure(normalizedEmail);
          return res.status(409).json({
            error: 'That backup code was just used. Try another one.',
            two_factor_required: true,
          });
        }
      }
    }

    // Successful login — clear this account's failure counter.
    clearAccountFailures(normalizedEmail);
    await sendSession(req, res, user);
  } catch (err) {
    console.error('Login error:', err.stack || err.message);
    res.status(500).json({ error: 'Login failed' });
  }
});

router.get('/me', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    const equity = await getUserEquity(user.id);
    res.json({ user_id: user.id, email: user.email, plan: user.plan,
               telegram_linked: !!user.telegram_linked,
               email_verified: !!user.email_verified,
               referral_code: user.referral_code || null,
               wallet_address: user.wallet_address || null,
               sol_address: user.sol_address || null,
               has_password: !!user.password_hash, equity });
  } catch (err) {
    console.error('Me error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to fetch user' });
  }
});

// ── Two-factor auth (MH1) ────────────────────────────────────────────────────
// Setup is two-phase so a typo'd authenticator can never lock the account:
// /2fa/setup stages a secret (enabled stays 0), /2fa/enable turns it on only
// after the user proves their app generates valid codes. Disabling requires a
// current code (or backup code) — a stolen session alone can't strip 2FA.

router.get('/2fa/status', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    let backups = [];
    try { backups = JSON.parse(rows[0].totp_backup_codes || '[]'); } catch (e) { backups = []; }
    res.json({
      enabled: !!rows[0].totp_enabled,
      pending: !rows[0].totp_enabled && !!rows[0].totp_secret,
      backup_codes_remaining: rows[0].totp_enabled ? backups.length : null,
    });
  } catch (err) {
    res.status(500).json({ error: '2FA status failed' });
  }
});

router.post('/2fa/setup', authMiddleware, async (req, res) => {
  try {
    const totp = require('./lib/totp');
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    if (rows[0].totp_enabled) {
      return res.status(400).json({ error: '2FA is already enabled — disable it first to rotate.' });
    }
    const secret = totp.generateSecret();
    // SEALED FOR STORAGE, PLAIN IN THE RESPONSE. The user has to scan the real
    // secret exactly once; what the database keeps is the envelope. Without
    // WEB_CREDS_KEY sealSecret passes through and the row stays plaintext —
    // reported honestly below rather than assumed.
    await pool.execute(
      'UPDATE users SET totp_secret = ?, totp_enabled = ?, totp_backup_codes = ? WHERE id = ?',
      [totp.sealSecret(secret), 0, null, req.user.user_id]);
    res.json({
      secret,
      otpauth: totp.otpauthUri(secret, rows[0].email),
      // A user enrolling a permanent second factor is entitled to know whether
      // the seed is encrypted where it lands. Absent a key it is not, and
      // saying so is cheaper than a claim nobody checked.
      encrypted_at_rest: totp.secretsAreSealed(),
      note: 'Scan or enter this in your authenticator app, then confirm a code '
        + 'at /2fa/enable. 2FA is NOT active until confirmed.',
    });
  } catch (err) {
    res.status(500).json({ error: '2FA setup failed' });
  }
});

router.post('/2fa/enable', authMiddleware, async (req, res) => {
  try {
    const totp = require('./lib/totp');
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    if (user.totp_enabled) return res.status(400).json({ error: '2FA is already enabled.' });
    if (!user.totp_secret) return res.status(400).json({ error: 'Run /2fa/setup first.' });
    if (!totp.verifyTotp(user.totp_secret, (req.body || {}).code)) {
      return res.status(401).json({ error: 'Code does not match — check your authenticator app.' });
    }
    const { codes, hashes } = totp.generateBackupCodes();
    // MIGRATE THE ROW HERE. This used to write `user.totp_secret` straight
    // back, so a legacy plaintext seed stayed plaintext for the life of the
    // account — the fix would have covered new enrolments and nobody else.
    // The code was just verified against this secret, so it is readable; if it
    // somehow is not, keep what was there rather than writing a sealed null.
    const opened = totp.openSecret(user.totp_secret);
    const stored = opened ? totp.sealSecret(opened) : user.totp_secret;
    await pool.execute(
      'UPDATE users SET totp_secret = ?, totp_enabled = ?, totp_backup_codes = ? WHERE id = ?',
      [stored, 1, JSON.stringify(hashes), req.user.user_id]);
    res.json({
      enabled: true,
      backup_codes: codes,
      note: 'Save these one-time backup codes now — they are shown ONCE and '
        + 'each works a single time if you lose your authenticator.',
    });
  } catch (err) {
    res.status(500).json({ error: '2FA enable failed' });
  }
});

router.post('/2fa/disable', authMiddleware, async (req, res) => {
  try {
    const totp = require('./lib/totp');
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    if (!user.totp_enabled) return res.status(400).json({ error: '2FA is not enabled.' });
    const code = String((req.body || {}).code || '').trim();
    let ok = totp.verifyTotp(user.totp_secret, code);
    if (!ok) {
      let backups = [];
      try { backups = JSON.parse(user.totp_backup_codes || '[]'); } catch (e) { backups = []; }
      ok = totp.consumeBackupCode(code, backups) !== null;
    }
    if (!ok) return res.status(401).json({ error: 'A valid current code (or backup code) is required to disable 2FA.' });
    await pool.execute(
      'UPDATE users SET totp_secret = ?, totp_enabled = ?, totp_backup_codes = ? WHERE id = ?',
      [null, 0, null, req.user.user_id]);
    res.json({ enabled: false });
  } catch (err) {
    res.status(500).json({ error: '2FA disable failed' });
  }
});

// Invite friends — the caller's own share code + how many signed up with it.
// Back-fills a code for accounts created before referrals existed.
router.get('/referrals', authMiddleware, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute('SELECT referral_code FROM users WHERE id = ?', [uid]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    let code = rows[0].referral_code;
    if (!code) {
      code = genReferralCode();
      await pool.execute('UPDATE users SET referral_code = ? WHERE id = ?', [code, uid]);
    }
    const [joined] = await pool.execute('SELECT id FROM users WHERE referred_by = ?', [uid]);
    const count = joined.length;
    // Spread whatever the tier says, or nothing. OMIT rather than guard: the
    // panel above the tier block — the invite link and the share buttons — is
    // independently true and must not be blanked because the ladder could not
    // be computed. The browser model treats a missing `tier` as "print no
    // ladder", which is the honest reading; it used to treat it as Starter.
    const t = referralTier(count);
    res.json({ code, count, ...(t || {}) });
  } catch (err) {
    console.error('Referrals error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to load referrals' });
  }
});

router.post('/link-token', authMiddleware, async (req, res) => {
  try {
    const token = crypto.randomBytes(16).toString('hex');
    const expires = new Date(Date.now() + 10 * 60 * 1000); // 10 min
    await pool.execute(
      'UPDATE users SET link_token = ?, link_token_expires = ? WHERE id = ?',
      [token, expires, req.user.user_id]
    );
    res.json({ token });
  } catch (err) {
    console.error('Link token error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to generate token' });
  }
});

// -- Validate link token (called by the Telegram bot) --
//
// RC-2026-001. This route binds a Telegram identity to a web account, and the
// identity it binds is `chat_id`, read verbatim from the request body. The
// `link_token` proves which WEB ACCOUNT is being linked. It proves nothing
// whatsoever about the chat_id beside it, and `/link-token` mints a valid
// token for the caller's OWN row on request — so any registered user could
// bind any Telegram id they liked to their own account, and
// `resolveBotIdentity` would hand them that person's bot identity thereafter.
// The module doing the handing (`app/lib/identity.js`) opens by promising
// "the browser can never choose who it acts as". It was resolving server-side
// from a row whose contents the browser had chosen.
//
// `scripts/guard_lint.py` exempts this route from `express-route-auth` with
// the note "the token IS the credential being checked". True of the token, and
// read as though it covered the request. A second, wholly unauthenticated
// parameter was sitting next to it.
//
// The fix is the mechanism every other bot-channel endpoint in this app
// already uses: X-Bot-Secret, compared in constant time
// (`app/routes/sync.js:273`).

/**
 * Three-valued, and the third value is the entire reason this is a function.
 *
 * `unconfigured` is not `bad` and it is certainly not `ok`. A server with no
 * BOT_SYNC_SECRET has not CHECKED anything: reporting that as a pass is
 * "absent is never a measurement", and reporting it as a wrong secret sends an
 * operator hunting a mismatch that does not exist. The two failures need
 * different responses (503 vs 403) because they need different actions.
 *
 * @returns {'ok'|'bad'|'unconfigured'}
 */
function linkBotSecretVerdict(given, expected) {
  const want = String(expected || '');
  if (want.length < 32) return 'unconfigured';
  const a = Buffer.from(String(given || ''));
  const b = Buffer.from(want);
  // timingSafeEqual THROWS on unequal-length buffers. The length check is what
  // keeps a wrong-length secret a clean 403 instead of a crash to 500 — the
  // same trap botAuth documents at sync.js:280.
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return 'bad';
  return 'ok';
}

/**
 * Observe-first ladder, because this is a TWO-SIDED change across TWO DEPLOY
 * TARGETS. The bot box and the web container deploy separately — that is the
 * 2026-08-25 incident in one line ("the bot box never serves `app/`"). If the
 * server half lands first, every real /link is refused until the bot half
 * follows.
 *
 *   block  (default)  refuse anything without a valid secret
 *   warn              allow, but log every request that WOULD have been refused
 *   off               no check
 *
 * The default is `block`. A CRITICAL that stays open until somebody remembers
 * to flip a flag is an open CRITICAL, and the deploy-order problem is solved
 * by deploying the bot first — which costs one ordering note. `warn` exists
 * for an operator who must go web-first and wants the transition window
 * visible; it is a choice they make deliberately, not a state they land in.
 *
 * Read per request rather than at module scope so a change takes effect
 * without a restart, for the reason `bot/utils/website_sync.py:104` gives
 * about its own header: a vault restore or an admin repair should not need one.
 */
function linkBotAuth(req, res, next) {
  const gate = String(process.env.LINK_BOT_SECRET_GATE || 'block').toLowerCase();
  if (gate === 'off') return next();

  const verdict = linkBotSecretVerdict(
    req.headers['x-bot-secret'], process.env.BOT_SYNC_SECRET);
  if (verdict === 'ok') return next();

  if (gate === 'warn') {
    console.warn(
      `link-gate WARN: /validate-token would be refused (${verdict}). ` +
      'Set LINK_BOT_SECRET_GATE=block once the bot half is deployed.');
    try { secLog('link_gate_warn', req, { verdict }); } catch (e) { /* never block the route */ }
    return next();
  }

  // Coarse codes from a fixed vocabulary — the /readyz rule. The caller learns
  // which ACTION is needed (configure the server vs fix the secret) and nothing
  // about the secret itself.
  if (verdict === 'unconfigured') {
    return res.status(503).json({ error: 'link_not_configured' });
  }
  return res.status(403).json({ error: 'invalid_bot_secret' });
}

router.post('/validate-token', linkBotAuth, async (req, res) => {
  try {
    const { token, chat_id } = req.body;
    if (!token || !chat_id) return res.status(400).json({ error: 'token and chat_id required' });

    // Find user with this token that hasn't expired
    const [rows] = await pool.execute(
      'SELECT id, email, plan, token_epoch FROM users WHERE link_token = ? AND link_token_expires > ?',
      [token, new Date()]
    );

    if (rows.length === 0) {
      return res.status(404).json({ error: 'Token invalid or expired' });
    }

    const user = rows[0];
    const tgId = String(chat_id).slice(0, 32);

    // The half of RC-2026-001 that does not depend on the deploy order.
    //
    // Even with the bot channel authenticated above, moving a chat_id that
    // already belongs to a DIFFERENT row is the takeover itself, and it is the
    // one outcome the victim cannot undo — their bot identity simply starts
    // resolving to somebody else's account. Refusing it needs no secret and no
    // bot-side change, so it holds at every rung of the ladder, including `off`.
    //
    // `id != ?` and not a bare match: re-linking the SAME Telegram id to the
    // SAME account is an ordinary re-link (a user who ran /link twice) and must
    // keep working.
    const [claimed] = await pool.execute(
      'SELECT id FROM users WHERE telegram_id = ? AND id != ?', [tgId, user.id]);
    if (claimed.length > 0) {
      secLog('link_telegram_id_already_claimed', req, { user_id: user.id });
      return res.status(409).json({ error: 'telegram_already_linked' });
    }

    // Consume the token, mark telegram linked, and RECORD the telegram id so the
    // website can attach exchange-credential submissions to the right bot account.
    await pool.execute(
      'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
      [tgId, user.id]
    );

    res.json({ user_id: user.id, email: user.email, plan: user.plan });
  } catch (err) {
    console.error('Validate token error:', err.stack || err.message);
    res.status(500).json({ error: 'Token validation failed' });
  }
});

// -- OAuth: find-or-create by provider identity --
// 1) match the provider id; 2) else link to an existing email account;
// 3) else create a passwordless account. Returns the user row (id, email, plan).
const _PROVIDER_ID_COLUMN = {
  google: 'google_id',
  telegram: 'telegram_id',
  discord: 'discord_id',
  x: 'x_id',
  wallet: 'wallet_address',
  // Farcaster fids arrive from SIWF (lib/siwf.js), verified before they reach
  // here. Stored as a string like every other provider id so the shared
  // find-or-create path needs no special case.
  farcaster: 'farcaster_fid',
};

async function findOrCreateOAuthUser({ provider, providerId, email, avatarUrl }) {
  const idCol = _PROVIDER_ID_COLUMN[provider] || 'telegram_id';
  const [byId] = await pool.execute(
    `SELECT id, email, plan, token_epoch FROM users WHERE ${idCol} = ? LIMIT 1`, [providerId]);
  if (byId.length) return byId[0];

  if (email) {
    const [byEmail] = await pool.execute(
      'SELECT id, email, plan, token_epoch FROM users WHERE email = ? LIMIT 1', [email]);
    if (byEmail.length) {
      await pool.execute(`UPDATE users SET ${idCol} = ? WHERE id = ?`,
        [providerId, byEmail[0].id]);
      return byEmail[0];
    }
  }
  // Some providers give no email (Telegram, X) — synthesize a unique,
  // non-routable placeholder so the NOT-NULL/UNIQUE email column is satisfied;
  // the user can add a real one later. Keep Telegram's historical "tg-" prefix.
  const phUser = provider === 'telegram' ? 'tg' : provider;
  const phDomain = provider === 'telegram' ? 'telegram' : provider;
  const finalEmail = email || `${phUser}-${providerId}@${phDomain}.runeclaw.local`;
  // SELECT-then-INSERT: two concurrent logins with the same provider account
  // both miss above and both insert, and email / the provider id column are
  // UNIQUE — the loser would 500 on a login that should simply succeed. On a
  // duplicate, re-read: the winner already created exactly the row we wanted.
  //
  // Deliberately a catch rather than an upsert. An upsert here would let a
  // second provider account overwrite an existing user's row.
  try {
    const [result] = await pool.execute(
      `INSERT INTO users (email, ${idCol}, avatar_url, telegram_linked) VALUES (?, ?, ?, ?)`,
      [finalEmail, providerId, avatarUrl || null, provider === 'telegram']);
    return { id: result.insertId, email: finalEmail, plan: 'free' };
  } catch (err) {
    if (err && err.code === 'ER_DUP_ENTRY') {
      const [again] = await pool.execute(
        `SELECT id, email, plan, token_epoch FROM users WHERE ${idCol} = ? LIMIT 1`, [providerId]);
      if (again.length) return again[0];
    }
    throw err;
  }
}

// -- Public provider config (no secrets) so the login page knows what to show --
router.get('/config', (_req, res) => {
  res.json({
    google_client_id: GOOGLE_CLIENT_ID || null,
    // Telegram widget needs the bot USERNAME (public — it is already printed
    // verbatim on the landing page, in every i18n locale and in the dashboard;
    // the SECRET is TELEGRAM_BOT_TOKEN, which never leaves the server).
    // Requiring an env var for a value the site already publishes meant the
    // login button silently rendered nothing: on 2026-07-31 the operator
    // registered the domain with BotFather and the widget still would not
    // have appeared, with no error anywhere to say why. Default to the
    // published username; TELEGRAM_BOT_USERNAME still overrides for forks.
    // The token gate stays: without it no login can be VERIFIED, and
    // advertising a button that cannot complete is worse than hiding it.
    telegram_bot: (TELEGRAM_BOT_TOKEN
      && (process.env.TELEGRAM_BOT_USERNAME || 'HTRUNECLAW_bot')) || null,
    // Redirect-based providers (Discord, X) — advertised only when configured.
    oauth_providers: oauth2.configuredProviders(),
    // Self-custody wallet sign-in is available when the verifier is installed.
    wallet_login: !!_ethers,
    // Footer social links — operator-set, rendered only when present (no guessing).
    social_links: {
      x: process.env.SOCIAL_X_URL || null,
      discord: process.env.SOCIAL_DISCORD_URL || null,
      telegram: process.env.SOCIAL_TELEGRAM_URL || 'https://t.me/HTRUNECLAW_bot',
    },
    // Connectable exchange venues + their field specs, so the Account UI renders
    // the right connect form per venue (public: field names/types, no secrets).
    venues: VENUES,
  });
});

// -- Self-custody sign-in (Sign-In-With-Ethereum) --
// The client requests a one-time nonce, signs a plain login message with
// personal_sign, and posts { address, signature }. The server verifies the
// signature recovers to the claimed address, consumes the nonce (single-use,
// short TTL — anti-replay), and finds-or-creates a passwordless account keyed
// by wallet_address. Non-custodial: the wallet only signs a login message here,
// it never authorizes a transaction, and no private key ever leaves the wallet.
const _ADDR_RE = /^0x[a-fA-F0-9]{40}$/;
const _NONCE_TTL_MS = 5 * 60 * 1000;
// Link codes + sign nonces live in a durable store (DB-backed, in-memory
// fallback) so the phone/QR flow survives a web restart or a second instance
// between "show QR" and "phone signs". See lib/wallet_link_store.
const _linkStore = require('./lib/wallet_link_store');

router.post('/wallet/nonce', async (req, res) => {
  if (!_ethers) return res.status(503).json({ error: 'Wallet sign-in is not available on this deployment.' });
  const clientIp = req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
  if (!checkRateLimit(clientIp)) return res.status(429).json({ error: 'Too many attempts. Try again later.' });
  const address = String((req.body || {}).address || '').trim();
  if (!_ADDR_RE.test(address)) return res.status(400).json({ error: 'A valid wallet address is required.' });
  const nonce = crypto.randomBytes(16).toString('hex');
  const message = 'RUNECLAW — sign in with your wallet.\n\n'
    + 'This only proves you own this address. It will NOT trigger a transaction or cost gas.\n\n'
    + `Address: ${address}\nNonce: ${nonce}\nIssued: ${new Date().toISOString()}`;
  await _linkStore.putNonce(address, message, Date.now() + _NONCE_TTL_MS);
  res.json({ message });
});

router.post('/wallet/verify', async (req, res) => {
  try {
    if (!_ethers) return res.status(503).json({ error: 'Wallet sign-in is not available on this deployment.' });
    const address = String((req.body || {}).address || '').trim();
    const signature = String((req.body || {}).signature || '').trim();
    if (!_ADDR_RE.test(address) || !signature) {
      return res.status(400).json({ error: 'Address and signature are required.' });
    }
    const rec = await _linkStore.getNonce(address);
    if (!rec || rec.expires < Date.now()) {
      return res.status(400).json({ error: 'Login request expired — please try again.' });
    }
    let recovered;
    try { recovered = _ethers.verifyMessage(rec.message, signature); }
    catch (_) { return res.status(401).json({ error: 'Signature verification failed.' }); }
    if (String(recovered).toLowerCase() !== address.toLowerCase()) {
      return res.status(401).json({ error: 'Signature does not match the wallet.' });
    }
    await _linkStore.delNonce(address);   // single-use
    const user = await findOrCreateOAuthUser({
      provider: 'wallet', providerId: address.toLowerCase(), email: null,
    });
    await sendSession(req, res, user, { provider: 'wallet' });
  } catch (err) {
    console.error('Wallet verify error:', err.stack || err.message);
    res.status(500).json({ error: 'Wallet sign-in failed' });
  }
});

// -- Link/unlink a wallet on the ALREADY-LOGGED-IN account --
// The landing page's "Continue with a wallet" is a LOGIN method; users who
// signed up with email/Telegram had no way to attach a wallet at all, even
// though every wallet/net-worth panel pointed them at one. Same SIWE-style
// proof as login (nonce → personal_sign → recover), then the address is
// stored on the caller's own row. Read-only linkage: it unlocks balance
// mirroring only, never any signing surface.
router.post('/wallet/link', authMiddleware, async (req, res) => {
  try {
    if (!_ethers) return res.status(503).json({ error: 'Wallet linking is not available on this deployment.' });
    const address = String((req.body || {}).address || '').trim();
    const signature = String((req.body || {}).signature || '').trim();
    if (!_ADDR_RE.test(address) || !signature) {
      return res.status(400).json({ error: 'Address and signature are required.' });
    }
    const lower = address.toLowerCase();
    const rec = await _linkStore.getNonce(lower);
    if (!rec || rec.expires < Date.now()) {
      return res.status(400).json({ error: 'Link request expired — please try again.' });
    }
    let recovered;
    try { recovered = _ethers.verifyMessage(rec.message, signature); }
    catch (_) { return res.status(401).json({ error: 'Signature verification failed.' }); }
    if (String(recovered).toLowerCase() !== lower) {
      return res.status(401).json({ error: 'Signature does not match the wallet.' });
    }
    await _linkStore.delNonce(lower);   // single-use
    // A wallet identifies at most one account (it is also a login key).
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE wallet_address = ? LIMIT 1', [lower]);
    if (rows.length && rows[0].id !== req.user.user_id) {
      return res.status(409).json({ error: 'That wallet is already linked to another account.' });
    }
    await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
      [lower, req.user.user_id]);
    res.json({ ok: true, address: lower });
  } catch (err) {
    console.error('Wallet link error:', err.stack || err.message);
    res.status(500).json({ error: 'Wallet link failed' });
  }
});

// -- Phone linking via QR: mint a single-use code bound to THIS account --
// Desktop shows the QR; the phone opens /wallet-link?code=… inside a wallet
// app's browser and proves ownership with the same SIWE-style signature.
// The code is the bearer: 10-minute TTL, single-use, server-side user
// binding — the phone never needs the desktop's JWT.
const LINK_CODE_TTL_MS = 10 * 60_000;

// A phone-link QR is scanned by a DIFFERENT device on a DIFFERENT network, so
// the URL inside it has to be the public one. req.get('host') is not: behind a
// serverless/VPC front end it is whatever internal hop reached the app. In
// production that produced
//   http://page-…-vpc.fcapp.run/wallet-link?code=…
// — an internal hostname over plain http. The phone showed "connection is not
// secure" and the flow could never complete, while the desktop showed a QR that
// looked perfectly fine. A QR pointing nowhere is worse than no QR, so this
// refuses to issue one rather than hand out an unusable code.
// The public URL is one question with one answer — see lib/public_origin.
// It used to be answered here from req.get('host'), which behind a
// serverless/VPC front end is an INTERNAL hostname: the phone-link QR
// shipped pointing at http://page-…-vpc.fcapp.run and no phone could ever
// reach it, while every server-side test passed.
const _publicOrigin = require('./lib/public_origin');
const publicOrigin = (req) => _publicOrigin.resolve(req);

router.post('/wallet/link-code', authMiddleware, async (req, res) => {
  try {
    if (!_ethers) return res.status(503).json({ error: 'Wallet linking is not available on this deployment.' });
    // Work out the public URL BEFORE burning a code: a code issued against an
    // origin the phone cannot reach is a code the user can never redeem.
    const po = publicOrigin(req);
    if (po.error) return res.status(503).json({ error: po.error });
    const code = crypto.randomBytes(16).toString('hex');
    await _linkStore.putCode(code, req.user.user_id, Date.now() + LINK_CODE_TTL_MS);
    const url = `${po.origin}/wallet-link?code=${code}`;
    let svg = null;
    try {
      // margin:4 = the 4-module quiet zone the QR spec requires; a tighter zone
      // is a common cause of phones failing to lock onto the code.
      svg = await require('qrcode').toString(url, { type: 'svg', margin: 4, width: 240 });
    } catch (e) { /* QR lib missing → the URL alone still works */ }
    res.json({ code, url, svg, expires_in_sec: LINK_CODE_TTL_MS / 1000 });
  } catch (err) {
    console.error('Wallet link-code error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not create a link code' });
  }
});

// Redeemed FROM THE PHONE — no JWT; the single-use code carries the binding.
router.post('/wallet/link-by-code', async (req, res) => {
  try {
    if (!_ethers) return res.status(503).json({ error: 'Wallet linking is not available on this deployment.' });
    const code = String((req.body || {}).code || '').trim();
    const address = String((req.body || {}).address || '').trim();
    const signature = String((req.body || {}).signature || '').trim();
    if (!/^[0-9a-f]{32}$/.test(code) || !_ADDR_RE.test(address) || !signature) {
      return res.status(400).json({ error: 'Code, address and signature are required.' });
    }
    const rec = await _linkStore.getCode(code);
    if (!rec || rec.expires < Date.now()) {
      return res.status(400).json({ error: 'This link code has expired — generate a fresh QR on your computer.' });
    }
    const lower = address.toLowerCase();
    const nrec = await _linkStore.getNonce(lower);
    if (!nrec || nrec.expires < Date.now()) {
      return res.status(400).json({ error: 'Signing request expired — try again.' });
    }
    let recovered;
    try { recovered = _ethers.verifyMessage(nrec.message, signature); }
    catch (_) { return res.status(401).json({ error: 'Signature verification failed.' }); }
    if (String(recovered).toLowerCase() !== lower) {
      return res.status(401).json({ error: 'Signature does not match the wallet.' });
    }
    await _linkStore.delNonce(lower);
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE wallet_address = ? LIMIT 1', [lower]);
    if (rows.length && rows[0].id !== rec.userId) {
      return res.status(409).json({ error: 'That wallet is already linked to another account.' });
    }
    await _linkStore.delCode(code);   // single-use — only after every check passed
    await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
      [lower, rec.userId]);
    res.json({ ok: true, address: lower });
  } catch (err) {
    console.error('Wallet link-by-code error:', err.stack || err.message);
    res.status(500).json({ error: 'Wallet link failed' });
  }
});

router.post('/wallet/unlink', authMiddleware, async (req, res) => {
  try {
    await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
      [null, req.user.user_id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Wallet unlink error:', err.stack || err.message);
    res.status(500).json({ error: 'Wallet unlink failed' });
  }
});

// Solana link nonce — issue a message for the wallet to sign (connect-and-sign
// upgrade of the watch-only flow). Reuses the chain-agnostic nonce store.
router.post('/wallet/solana/nonce', authMiddleware, async (req, res) => {
  try {
    const { isSolanaAddress } = require('./lib/solana');
    const address = String((req.body || {}).address || '').trim();
    if (!isSolanaAddress(address)) {
      return res.status(400).json({ error: 'Not a valid Solana address (base58).' });
    }
    const nonce = crypto.randomBytes(16).toString('hex');
    const message = 'RUNECLAW — link your Solana wallet.\n\n'
      + 'This only proves you own this address. It will NOT trigger a transaction or cost fees.\n\n'
      + `Address: ${address}\nNonce: ${nonce}\nIssued: ${new Date().toISOString()}`;
    // Namespace the key so it can't collide with an EVM nonce for the same string.
    await _linkStore.putNonce('sol:' + address, message, Date.now() + _NONCE_TTL_MS);
    res.json({ message });
  } catch (err) {
    console.error('Solana nonce error:', err.stack || err.message);
    res.status(500).json({ error: 'Could not start Solana wallet link' });
  }
});

// Solana link. Backward-compatible:
//   - address only            → WATCH-only mirror (honestly unauthenticated),
//                               feeds public balance READS (lib/solana.js).
//   - address + signature     → CONNECT-AND-SIGN, ed25519-verified ownership.
// Either way the address never touches a signing surface here — non-custodial.
router.post('/wallet/solana', authMiddleware, async (req, res) => {
  try {
    const { isSolanaAddress } = require('./lib/solana');
    const address = String((req.body || {}).address || '').trim();
    const signature = String((req.body || {}).signature || '').trim();
    if (!isSolanaAddress(address)) {
      return res.status(400).json({ error: 'Not a valid Solana address (base58).' });
    }
    let verified = false;
    if (signature) {
      const rec = await _linkStore.getNonce('sol:' + address);
      if (!rec || rec.expires < Date.now()) {
        return res.status(400).json({ error: 'Link request expired — please try again.' });
      }
      const { verifySignedMessage } = require('./lib/solana_verify');
      if (!verifySignedMessage(rec.message, signature, address)) {
        return res.status(401).json({ error: 'Signature does not match the wallet.' });
      }
      await _linkStore.delNonce('sol:' + address); // single-use
      verified = true;
    }
    // Persist WHICH of the two this was. Storing only the address threw the
    // ed25519 result away, so a proven wallet and a pasted string were
    // indistinguishable on the next page load — and the UI called both a
    // "watch address", understating one and overstating the other.
    await pool.execute('UPDATE users SET sol_address = ?, sol_verified = ? WHERE id = ?',
      [address, verified ? 1 : 0, req.user.user_id]);
    res.json({ ok: true, sol_address: address, verified });
  } catch (err) {
    console.error('Solana link error:', err.stack || err.message);
    res.status(500).json({ error: 'Solana address link failed' });
  }
});

router.post('/wallet/solana/unlink', authMiddleware, async (req, res) => {
  try {
    await pool.execute('UPDATE users SET sol_address = ?, sol_verified = 0 WHERE id = ?',
      [null, req.user.user_id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Solana watch unlink error:', err.stack || err.message);
    res.status(500).json({ error: 'Solana address unlink failed' });
  }
});

// -- Login / register with Telegram (Login Widget) --
router.post('/telegram', async (req, res) => {
  try {
    if (!TELEGRAM_BOT_TOKEN) return res.status(503).json({ error: 'Telegram login not configured' });
    const data = req.body || {};
    if (!verifyTelegramAuth(data, TELEGRAM_BOT_TOKEN)) {
      return res.status(401).json({ error: 'Telegram verification failed' });
    }
    const user = await findOrCreateOAuthUser({
      provider: 'telegram', providerId: String(data.id).slice(0, 32),
      email: null, avatarUrl: data.photo_url,
    });
    await sendSession(req, res, user);
  } catch (err) {
    console.error('Telegram auth error:', err.stack || err.message);
    res.status(500).json({ error: 'Telegram login failed' });
  }
});

// -- Login / register with Google (Identity Services credential) --
router.post('/google', async (req, res) => {
  try {
    if (!GOOGLE_CLIENT_ID) return res.status(503).json({ error: 'Google login not configured' });
    const { credential } = req.body || {};
    if (!credential) return res.status(400).json({ error: 'Missing credential' });
    // Verify the ID token with Google (dep-free: the tokeninfo endpoint checks
    // signature + expiry for us; we still assert audience + verified email).
    const resp = await fetch(
      `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(credential)}`);
    if (!resp.ok) return res.status(401).json({ error: 'Google verification failed' });
    const info = await resp.json();
    if (info.aud !== GOOGLE_CLIENT_ID) return res.status(401).json({ error: 'Token audience mismatch' });
    if (info.email_verified !== 'true' && info.email_verified !== true) {
      return res.status(401).json({ error: 'Google email not verified' });
    }
    const user = await findOrCreateOAuthUser({
      provider: 'google', providerId: String(info.sub),
      email: String(info.email).trim().toLowerCase(), avatarUrl: info.picture,
    });
    await sendSession(req, res, user);
  } catch (err) {
    console.error('Google auth error:', err.stack || err.message);
    res.status(500).json({ error: 'Google login failed' });
  }
});

// -- Change password (authenticated) --
// If the account already has a password, the current one must be supplied and
// correct. OAuth-only accounts (no password_hash) can SET one without a current
// password — this is how a Google/Telegram user adds email/password login.
router.post('/change-password', authMiddleware, async (req, res) => {
  try {
    const { current_password, new_password } = req.body || {};
    if (!new_password || String(new_password).length < 10) {
      return res.status(400).json({ error: 'New password must be at least 10 characters' });
    }
    const [rows] = await pool.execute(
      'SELECT id, password_hash FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    if (user.password_hash) {
      if (!current_password) return res.status(400).json({ error: 'Current password required' });
      const ok = await bcrypt.compare(String(current_password), user.password_hash);
      if (!ok) return res.status(401).json({ error: 'Current password is incorrect' });
    }
    const hash = await bcrypt.hash(String(new_password), 12);
    await pool.execute('UPDATE users SET password_hash = ? WHERE id = ?', [hash, user.id]);
    // Changing a password is how someone responds to "my account is
    // compromised", and until now it did not touch the attacker's session at
    // all — they kept a valid token for up to thirty more days. Revoke every
    // outstanding token, then hand the caller a fresh one so the act of
    // securing the account does not log the owner out of the tab they did it
    // in.
    await revokeUserTokens(user.id);
    const [fresh] = await pool.execute(
      'SELECT id, email, token_epoch FROM users WHERE id = ?', [user.id]);
    const token = fresh.length ? signToken(fresh[0]) : undefined;
    // The revocation above just invalidated the cookie this request arrived
    // with. Re-issuing it is what keeps the promise in the comment — the act
    // of securing the account must not log the owner out of the tab they did
    // it in — and without this the cookie session ends one request later.
    if (token) setSession(req, res, token);
    res.json({ ok: true, token });
  } catch (err) {
    console.error('Change-password error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to change password' });
  }
});

/**
 * POST /api/auth/logout — end every session for the caller.
 *
 * There was no logout route at all. The client dropped the token from local
 * storage and called it done, which ends the session on exactly one device
 * and does nothing whatsoever to a copy someone else holds. "Log out" that
 * only forgets is the same class of claim this repo keeps auditing: a
 * reassuring word for something that did not happen.
 *
 * Scope is every session, not just this token. A user reaching for logout
 * because they suspect a compromise means "everywhere", and per-token
 * revocation would need a list of live jtis to store and expire — state the
 * epoch avoids entirely.
 */
router.post('/logout', authMiddleware, async (req, res) => {
  try {
    const epoch = await revokeUserTokens(req.user.user_id);
    // Both, and in this order. The epoch bump is what actually ends the
    // session — a cookie the browser keeps is already dead server-side — but
    // leaving it set means the readable rc_auth flag still says "logged in",
    // so the UI renders a session whose every request 401s.
    clearSession(req, res);
    res.json({ ok: true, sessions_ended: true, epoch });
  } catch (err) {
    console.error('Logout error:', err.stack || err.message);
    // Never report success for a revocation that did not land — the user
    // would walk away believing the stolen token is dead.
    res.status(500).json({ error: 'Failed to end sessions' });
  }
});

// -- Forgot password: issue a reset link (unauthenticated) --
// Always returns 200 with the same body regardless of whether the email exists,
// to avoid account enumeration. Rate-limited per IP (shared login limiter).
router.post('/forgot-password', async (req, res) => {
  const generic = { ok: true, message: 'If that email has an account, a reset link is on its way.' };
  // "a reset link is on its way" is false when the mailer is a no-op, and the
  // person then waits, re-checks spam, and concludes the account is broken.
  //
  // Saying so leaks NOTHING. The generic body exists to hide whether an
  // account exists; whether this deployment has SMTP configured is a property
  // of the server and is identical for every address, including addresses that
  // have no account. So the unconfigured branch can be honest, while the
  // configured branch keeps the generic wording — including when a send
  // throws, since only accounts with a password attempt one and a distinct
  // error there WOULD be an enumeration oracle.
  if (!mailer.isConfigured()) {
    return res.json({
      ok: false,
      error: 'email_not_configured',
      message: 'Password reset by email is not available on this deployment — '
        + 'no mail server is configured, so no link can be sent. Ask the '
        + 'operator to set up SMTP, or sign in with a linked social account.',
    });
  }
  try {
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many attempts. Try again later.' });
    }
    recordAttempt(clientIp);

    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });
    const normalizedEmail = String(email).trim().toLowerCase();

    const [rows] = await pool.execute(
      'SELECT id, email, password_hash FROM users WHERE email = ?', [normalizedEmail]);
    // Only send for accounts that actually have a password to reset.
    if (rows.length && rows[0].password_hash) {
      const raw = crypto.randomBytes(32).toString('hex');
      const expires = new Date(Date.now() + RESET_TTL_MS);
      await pool.execute(
        'UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE id = ?',
        [_hashToken(raw), expires, rows[0].id]);
      const link = `${mailer.baseUrl()}/reset?token=${raw}`;
      try {
        await mailer.sendMail({
          to: normalizedEmail,
          subject: 'Reset your RUNECLAW password',
          text: `Reset your RUNECLAW password using this link (valid 30 minutes):\n${link}\n\nIf you didn't request this, ignore this email — your password is unchanged.`,
          html: `<p>Reset your <b>RUNECLAW</b> password.</p>`
            + `<p><a href="${link}">Choose a new password</a> (valid 30 minutes)</p>`
            + `<p style="color:#888;font-size:12px">If you didn't request this, ignore this email — your password is unchanged.</p>`,
        });
      } catch (mailErr) {
        console.error('Reset email send failed:', mailErr.message);
      }
    }
    res.json(generic);
  } catch (err) {
    console.error('Forgot-password error:', err.stack || err.message);
    // Still return the generic body — don't leak that something errored.
    res.json(generic);
  }
});

// -- Reset password with a token (unauthenticated) --
router.post('/reset-password', async (req, res) => {
  try {
    const { token, new_password } = req.body || {};
    if (!token || !new_password) return res.status(400).json({ error: 'token and new_password required' });
    if (String(new_password).length < 10) {
      return res.status(400).json({ error: 'Password must be at least 10 characters' });
    }
    const [rows] = await pool.execute(
      'SELECT id, email FROM users WHERE reset_token = ? AND reset_token_expires > ?',
      [_hashToken(String(token)), new Date()]);
    if (rows.length === 0) return res.status(400).json({ error: 'Reset link is invalid or expired' });

    const hash = await bcrypt.hash(String(new_password), 12);
    await pool.execute(
      'UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL WHERE id = ?',
      [hash, rows[0].id]);
    // A reset is the OTHER half of "I have been compromised", and the half
    // where the legitimate owner has already lost access — so any session the
    // attacker holds must die here too. No replacement token: this route is
    // unauthenticated, and the user logs in with the password they just set.
    await revokeUserTokens(rows[0].id);
    // A successful reset clears any per-account lockout so the user can log in.
    clearAccountFailures(String(rows[0].email).trim().toLowerCase());
    res.json({ ok: true });
  } catch (err) {
    console.error('Reset-password error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to reset password' });
  }
});

// -- Verify email with a token (unauthenticated; link from the email) --
router.post('/verify-email', async (req, res) => {
  try {
    const token = (req.body && req.body.token) || req.query.token;
    if (!token) return res.status(400).json({ error: 'token required' });
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE verify_token = ? AND verify_token_expires > ?',
      [_hashToken(String(token)), new Date()]);
    if (rows.length === 0) return res.status(400).json({ error: 'Verification link is invalid or expired' });
    await pool.execute(
      'UPDATE users SET email_verified = TRUE, verify_token = NULL, verify_token_expires = NULL WHERE id = ?',
      [rows[0].id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Verify-email error:', err.stack || err.message);
    res.status(500).json({ error: 'Verification failed' });
  }
});

// -- Resend the verification email (authenticated) --
router.post('/send-verification', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT id, email, email_verified FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    if (rows[0].email_verified) return res.json({ ok: true, already_verified: true });
    if (String(rows[0].email).endsWith('@telegram.runeclaw.local')) {
      return res.status(400).json({ error: 'Add a real email address first' });
    }
    await sendVerificationEmail(rows[0].id, rows[0].email);
    res.json({ ok: true, sent: mailer.isConfigured() });
  } catch (err) {
    console.error('Send-verification error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to send verification' });
  }
});

// -- Redirect-based OAuth2 (Discord, X) — login OR link to a logged-in account --
//
// Google/Telegram use client-side widgets; Discord and X need the server to
// drive the authorization-code round-trip. Two short-lived in-memory stores back
// it (single-replica, like the rate limiters above): oauthFlows holds the CSRF
// state + PKCE verifier + optional link target between /start and /callback;
// oauthLinkKeys is a one-time handoff so a logged-in browser can enter LINK mode
// without putting its bearer token in a redirect URL.
const oauthFlows = new Map();     // state -> { provider, verifier, linkUserId, exp }
const oauthLinkKeys = new Map();  // linkKey -> { userId, exp }
const OAUTH_FLOW_TTL_MS = 10 * 60 * 1000;
const OAUTH_LINK_TTL_MS = 5 * 60 * 1000;

function _sweepOauth() {
  const now = Date.now();
  for (const [k, v] of oauthFlows) if (v.exp < now) oauthFlows.delete(k);
  for (const [k, v] of oauthLinkKeys) if (v.exp < now) oauthLinkKeys.delete(k);
}
const _oauthTimer = setInterval(_sweepOauth, 60000);
if (_oauthTimer.unref) _oauthTimer.unref();

function _oauthRedirectUri(provider) {
  // Providers require an absolute, pre-registered redirect URI. Operators set
  // APP_BASE_URL and register `${APP_BASE_URL}/api/auth/oauth/<provider>/callback`.
  const base = (process.env.APP_BASE_URL || '').replace(/\/+$/, '');
  return `${base}/api/auth/oauth/${provider}/callback`;
}

// Authed: mint a one-time key so a logged-in user can LINK a provider to their
// existing account (rather than creating/logging into a separate one).
router.post('/oauth-link-token', authMiddleware, (req, res) => {
  const key = crypto.randomBytes(24).toString('hex');
  oauthLinkKeys.set(key, { userId: req.user.user_id, exp: Date.now() + OAUTH_LINK_TTL_MS });
  res.json({ link_key: key });
});

// Begin the redirect flow → 302 to the provider's consent screen.
router.get('/oauth/:provider/start', (req, res) => {
  const provider = req.params.provider;
  if (!oauth2.isProviderConfigured(provider)) {
    return res.status(503).send('This login provider is not configured.');
  }
  let linkUserId = null;
  const linkKey = req.query.link ? String(req.query.link) : '';
  const lk = linkKey ? oauthLinkKeys.get(linkKey) : null;
  if (lk && lk.exp > Date.now()) { linkUserId = lk.userId; oauthLinkKeys.delete(linkKey); }
  const state = oauth2.randomState();
  const { verifier, challenge } = oauth2.pkcePair();
  oauthFlows.set(state, { provider, verifier, linkUserId, exp: Date.now() + OAUTH_FLOW_TTL_MS });
  const url = oauth2.buildAuthorizeUrl(provider, {
    redirectUri: _oauthRedirectUri(provider), state, challenge,
  });
  res.redirect(url);
});

// Provider redirects back here with ?code&state. Exchange → profile → session,
// then hand the session to the SPA via the URL fragment (never a query string,
// which would land the JWT in server/proxy access logs).
router.get('/oauth/:provider/callback', async (req, res) => {
  const provider = req.params.provider;
  const fail = (msg) => res.redirect(`/#oauth_error=${encodeURIComponent(msg)}`);
  try {
    const { code, state, error } = req.query;
    if (error) return fail(String(error));
    if (!code || !state) return fail('missing code or state');
    const flow = oauthFlows.get(String(state));
    if (!flow || flow.exp < Date.now() || flow.provider !== provider) {
      return fail('invalid or expired login attempt');
    }
    oauthFlows.delete(String(state));

    const accessToken = await oauth2.exchangeCode(provider, {
      code: String(code), redirectUri: _oauthRedirectUri(provider), verifier: flow.verifier,
    });
    const profile = await oauth2.fetchProfile(provider, accessToken);

    let user;
    if (flow.linkUserId) {
      // LINK mode: attach this provider identity to the already-logged-in user.
      const idCol = _PROVIDER_ID_COLUMN[provider];
      await pool.execute(`UPDATE users SET ${idCol} = ? WHERE id = ?`,
        [profile.providerId, flow.linkUserId]);
      const [rows] = await pool.execute(
        'SELECT id, email, plan FROM users WHERE id = ?', [flow.linkUserId]);
      user = rows[0];
    } else {
      user = await findOrCreateOAuthUser({
        provider, providerId: profile.providerId,
        email: profile.email, avatarUrl: profile.avatarUrl,
      });
    }
    if (!user) return fail('could not complete login');

    const body = await sessionResponse(user, {
      provider, linked: Boolean(flow.linkUserId) });
    // The cookie is set on the redirect itself, so the session exists before
    // the landing page runs a line of script. The fragment payload still
    // carries the profile the page renders on arrival; what it no longer has
    // to be is the only place the session lives.
    setSession(req, res, body.token);
    const payload = Buffer.from(JSON.stringify(body)).toString('base64');
    res.redirect(`/#oauth=${payload}`);
  } catch (err) {
    console.error(`OAuth ${provider} callback error:`, err.stack || err.message);
    return fail('login failed');
  }
});

/**
 * DELETE /api/auth/account — erase this account, here and on the bot.
 *
 * THERE WAS NO DELETION PATH AT ALL. Not a broken one: no route, no SQL, no
 * deactivate flag that anything set. The privacy page had to say so outright,
 * because "you may request erasure" written over a system that cannot perform
 * it is the same defect as any other confident claim about a state that does
 * not exist.
 *
 * THE BOT IS PURGED FIRST, AND A FAILURE THERE ABORTS EVERYTHING.
 *
 * A user's exchange API keys live in the bot's encrypted vault, not in this
 * database. So the dangerous ordering is the obvious one: delete the rows,
 * report success, and leave the bot holding the credentials that move real
 * money under a message saying the account is gone. Purging the bot first
 * inverts the failure — a bot that clears and a database that then fails
 * leaves an account whose bot state is gone, which is visible, recoverable and
 * retryable. Keys surviving an account are none of those.
 *
 * So: bot first; abort with 502 and change NOTHING here if it does not confirm
 * every store. `handle_account_purge` answers per store precisely so this can
 * tell "deleted" from "nothing to delete" from "that store raised".
 *
 * AUTHENTICATION IS DELIBERATELY HEAVY. Password re-entry even for a session
 * that is already valid, plus a 2FA step-up when enrolled — the same bar as
 * enabling live trading, because this is equally irreversible and a stolen
 * session must not be able to erase somebody's history.
 */
router.delete('/account', authMiddleware, async (req, res) => {
  const uid = req.user.user_id;
  let started = false;                 // has any row been touched? (see below)
  try {
    const [rows] = await pool.execute(
      'SELECT id, email, password_hash, telegram_id, totp_enabled, totp_secret, '
      // Read BEFORE they are nulled: `wallet_link_nonces` is keyed by the
      // address itself, so once the users row is blanked there is nothing left
      // to find those rows by.
      + 'wallet_address, sol_address FROM users WHERE id = ?', [uid]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    const b = req.body || {};

    // A password-less account (OAuth, wallet, Telegram-only) has no password to
    // re-enter; 2FA and the confirmation phrase carry the weight there.
    if (user.password_hash) {
      const ok = b.password && await bcrypt.compare(String(b.password), user.password_hash);
      if (!ok) {
        secLog('account_delete_bad_password', req);
        return res.status(401).json({ error: 'Password is incorrect.' });
      }
    }
    const blk = stepUpBlock(user.totp_enabled, user.totp_secret, b.totp_code,
      'Enter your 6-digit authenticator code to delete your account.');
    if (blk) { secLog('account_delete_2fa', req); return res.status(blk.status).json(blk.body); }

    // A typed phrase, because every other control on this account is
    // reversible and this one is not. Cheap, and the only thing standing
    // between a mis-click and an irreversible action.
    if (String(b.confirm || '').trim().toUpperCase() !== 'DELETE') {
      return res.status(400).json({
        error: 'Type DELETE to confirm. This cannot be undone.',
        confirm_required: true,
      });
    }

    // ── the bot half, first ──────────────────────────────────────────────
    //
    // RC-2026-020. This was gated on `user.telegram_id`, so an account that
    // never linked Telegram skipped the bot half ENTIRELY and the web half
    // then reported the account deleted — while the bot's SQLite still held
    // that person's llm_api_key, news key, pasted notes and portfolio, keyed
    // by the `web:<id>` identity the gateway provisions on first request.
    // "It holds your exchange credentials — deleting here first would leave
    // them behind" is the reason given three lines down for refusing to
    // proceed without the bot; it is just as true for a web-only account.
    //
    // A telegram_id SPELLED as a web identity is refused rather than sent.
    // It is the one construction where one account's purge key can name a
    // different human: `web:5` as a telegram_id resolves bot-side to the same
    // row as website user 5.
    let botStores = null;
    const rawTg = user.telegram_id == null ? '' : String(user.telegram_id);
    if (/^web:/i.test(rawTg)) {
      secLog('account_delete_ambiguous_identity', req, rawTg);
      return res.status(409).json({
        error: 'Your account was NOT deleted. Its Telegram identifier is '
          + 'ambiguous, so deleting could act on a different account. '
          + 'Nothing has been changed. Please contact support.',
      });
    }
    const botIdentity = rawTg || `web:${user.id}`;
    if (gateway.isConfigured()) {
      let purge;
      try {
        purge = await postGateway('/account/purge',
          { telegram_id: botIdentity }, 15000);
      } catch (e) {
        secLog('account_delete_bot_unreachable', req, e.message);
        return res.status(502).json({
          error: 'Your account was NOT deleted. The trading bot could not be '
            + 'reached, and it holds your exchange credentials — deleting here '
            + 'first would leave them behind. Nothing has been changed. Please '
            + 'try again shortly.',
        });
      }
      // `postGateway` resolves an ENVELOPE — `{status, data}` — and does not
      // reject on a 4xx or 5xx. Reading `purge.purged` off the envelope was
      // always `undefined`, so this branch refused every deletion that reached
      // a working gateway. The route test caught it on its first run; no
      // source scan of "does it check purged" could have, because the check
      // was there and was reading the wrong object.
      const body = (purge && purge.data) || {};
      botStores = body.stores || null;
      if (!purge || purge.status !== 200 || body.purged !== true) {
        secLog('account_delete_bot_partial', req, JSON.stringify(botStores));
        return res.status(502).json({
          error: 'Your account was NOT deleted. The trading bot could not '
            + 'clear everything it holds, so nothing here was changed either.',
          bot_stores: botStores,
        });
      }
    }

    // ── the web half ─────────────────────────────────────────────────────
    //
    // `started` is what makes the catch below able to tell the truth. Its
    // first version answered "Account deletion failed — nothing was removed."
    // to every exception, and the route test then drove a fault AFTER the
    // deletes had run (a two-argument function called with one) — so the
    // reassuring half of that sentence was false in exactly the case where it
    // mattered. A blanket "nothing happened" is a claim about a state nobody
    // measured.
    const plan = erasurePlan(uid, {
      addresses: [user.wallet_address, user.sol_address],
    });
    for (const step of plan) {
      started = true;
      await pool.execute(step.sql, step.params);
    }
    clearSession(req, res);
    secLog('account_deleted', req, `bot=${JSON.stringify(botStores)}`);
    res.json({
      deleted: true,
      bot_stores: botStores,
      note: 'Your account and its data have been erased. Records that name '
        + 'only an account id — such as who referred whom — keep that id so '
        + 'other people\'s history stays intact; nothing in them identifies you.',
    });
  } catch (err) {
    console.error('Account delete error:', err.stack || err.message);
    res.status(500).json({
      error: started
        ? 'Account deletion failed part-way through. Some of your data has '
          + 'already been removed and some may remain. Please contact support '
          + 'rather than assuming either outcome.'
        : 'Account deletion failed — nothing was removed.',
      partial: started,
    });
  }
});

module.exports = {
  router, authMiddleware, optionalAuth, verifyTelegramAuth, findOrCreateOAuthUser,
  revokeUserTokens, tokenIsCurrent, signToken,
  sendVerificationEmail, sessionResponse,
  // Exported so app/test/referral_tier_honesty.test.js can read the table
  // itself. What a tier PROMISES is the load-bearing part, and it cannot be
  // pinned through the endpoint without registering twenty-five accounts.
  REFERRAL_TIERS, referralTier,
  // RC-2026-001. Exported so the ladder can be driven directly: the three
  // verdicts and the three rungs are nine cases, and routing all nine through
  // HTTP would test the router more than the decision.
  linkBotSecretVerdict, linkBotAuth,
};
