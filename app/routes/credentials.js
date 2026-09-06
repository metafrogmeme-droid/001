/**
 * Exchange-credential management (user-facing, JWT-authed).
 *
 * The user submits exchange API keys here. They are protected at rest
 * immediately into a short-lived `pending_credentials` row; the bot PULLS
 * pending rows over the shared-secret channel, imports them into its own
 * Fernet store keyed by telegram_id, and the row is deleted. Raw keys are
 * NEVER stored in plaintext and NEVER logged.
 *
 * WHAT PROTECTS THEM, and why there are two answers. The submission is SEALED
 * to the bot's own published key (lib/sealing_key.js — RSA-OAEP over a fresh
 * AES-256-GCM content key), which means this app cannot read back what it just
 * stored, and means an operator configures NOTHING: the bot generates the key
 * on first use and publishes the public half over the sync channel it already
 * authenticates on. Failing that, a deployment still carrying the legacy
 * shared WEB_CREDS_KEY encrypts under it, so rows keep flowing while a bot is
 * upgraded.
 *
 * Failing BOTH, this refuses. That is the one thing that must not change:
 * nothing is queued that nobody can open, and nothing is queued in the clear.
 * The 503 says which of the two is missing, in a fixed vocabulary — a user
 * whose keys will not save deserves to know it is not something they typed.
 *
 * Prerequisite: the account must have linked Telegram (so we know which bot
 * account the keys belong to). Keys should be withdrawal-disabled on the venue.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const creds = require('../lib/creds_crypto');
const sealing = require('../lib/sealing_key');
const { isVenue, venueFields } = require('../lib/venues');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { stepUpBlock } = require('../lib/stepup');
const { uidKey } = require('../lib/second_factor_lockout');
const { foreignIdentityBlock } = require('../lib/identity');

const router = express.Router();
router.use(authMiddleware);

// Per-user limit on the mutating credential endpoints (submit/disconnect). GET
// /status is left unlimited (cheap read). Money endpoint — keep it tight.
const credLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

// Max accepted length for any single key field (a real Bitget key is ~64 chars;
// this bounds a malicious oversized payload before it is encrypted/stored).
const MAX_FIELD = 512;

// Security audit line (never logs key material).
function secLog(event, req, extra) {
  const uid = req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
}

async function _userRow(uid) {
  // totp_* ride along because the mutating routes below step up on them — see
  // the comment at POST / for why connecting a key is at least as sensitive as
  // the actions that already required a code.
  const [rows] = await pool.execute(
    'SELECT telegram_linked, telegram_id, totp_enabled, totp_secret '
    + 'FROM users WHERE id = ?', [uid]);
  return rows[0] || null;
}

/**
 * How a submission can be protected right now — the one place that decides.
 *
 * `{ mode: 'sealed', rec }`   the bot's published key (preferred: this app
 *                             cannot reopen what it stores)
 * `{ mode: 'legacy' }`        the shared WEB_CREDS_KEY is set
 * `{ mode: 'off', reason }`   neither; submissions are refused
 *
 * THE REASON VOCABULARY IS FIXED and the three values are distinct on purpose:
 *
 *   awaiting_bot_key        the bot has not published one yet — it will, on
 *                           its next credential pull; this is the ordinary
 *                           state of a deployment whose bot is starting up
 *   sealing_key_unusable    it published something this build refuses (wrong
 *                           algorithm, non-RSA, undersized) — republishing the
 *                           same record cannot fix it
 *   sealing_key_unreadable  the read itself failed; we do not know whether a
 *                           key exists
 *
 * The third is why this returns a reason at all rather than a boolean. A
 * database that will not answer is not a bot that never called, and rendering
 * it as one sends the operator to restart the wrong process. Unreadable is
 * never absent — the same rule the rest of this app is built on.
 *
 * The legacy key is only consulted when the sealed path is unavailable, so a
 * deployment that still sets WEB_CREDS_KEY (lib/totp.js needs it for 2FA
 * secrets at rest) upgrades to sealing by itself the moment the bot publishes.
 */
async function protection() {
  let rec = null;
  let keyErr = null;
  try {
    rec = await sealing.readSealingKey();
  } catch (err) {
    keyErr = err;
    console.error('Sealing key read failed:', err.stack || err.message);
  }
  if (rec) return { mode: 'sealed', rec };
  if (creds.isConfigured()) return { mode: 'legacy' };
  if (keyErr) {
    return { mode: 'off',
      reason: keyErr.unusableSealingKey ? 'sealing_key_unusable' : 'sealing_key_unreadable' };
  }
  return { mode: 'off', reason: 'awaiting_bot_key' };
}

// What a refusal SAYS. Authored here, never an exception message — a driver
// string on a money form is how internal detail reaches a user's screen.
const OFF_DETAIL = {
  awaiting_bot_key:
    'The bot has not published its key yet, so there is nothing to seal your '
    + 'keys to. It publishes on its next check-in — try again shortly. Nothing '
    + 'you typed was stored.',
  sealing_key_unusable:
    'The bot published a key this site cannot use, so your keys were not '
    + 'stored. This needs an operator; retrying will not help.',
  sealing_key_unreadable:
    'We could not check how to protect your keys, so we did not store them. '
    + 'This is a fault on our side — please try again shortly.',
};

// GET /api/credentials/status -> { linked, connected, pending }
router.get('/status', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    const [st] = await pool.execute(
      'SELECT connected, exchange, last_error FROM exchange_status WHERE user_id = ?', [uid]);
    const [pend] = await pool.execute(
      'SELECT action, exchange FROM pending_credentials WHERE user_id = ?', [uid]);
    const connectedRows = st.filter(r => !!r.connected);
    const prot = await protection();
    res.json({
      linked: !!(u && u.telegram_linked),
      // Multi-venue: every exchange's own state, side by side. `last_error`
      // rides along so a REJECTED key reads as rejected-and-why rather than
      // as "not connected", which is what an untried key also looks like.
      venues: st.map(r => ({ venue: r.exchange || 'bitget', connected: !!r.connected,
                             last_error: r.last_error || null })),
      // Legacy single-venue fields (older clients): the first connected one.
      connected: connectedRows.length > 0,
      venue: connectedRows.length > 0 ? (connectedRows[0].exchange || 'bitget') : null,
      pending: pend.length > 0 ? pend[0].action : null,
      pending_venue: pend.length > 0 ? (pend[0].exchange || 'bitget') : null,
      // THREE-VALUED. `false` is a claim — "this form is off" — and an
      // unreadable key store has not earned it, so that case reads null and
      // says why in crypto_reason. `crypto_ready` was `creds.isConfigured()`,
      // which answered about the legacy shared key alone and so reported a
      // perfectly working sealed deployment as not ready.
      crypto_ready: prot.mode === 'off'
        ? (prot.reason === 'sealing_key_unreadable' ? null : false)
        : true,
      crypto_mode: prot.mode,
      crypto_reason: prot.mode === 'off' ? prot.reason : null,
      // The same sentence the 503 carries, so the panel can say it BEFORE the
      // user types their API keys instead of after. A form that cannot
      // succeed must not invite secrets into it.
      crypto_detail: prot.mode === 'off' ? OFF_DETAIL[prot.reason] : null,
    });
  } catch (err) {
    console.error('Cred status error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to read status' });
  }
});

// POST /api/credentials  body: { venue?, ...venue-specific fields }
//   bitget:      { api_key, api_secret, passphrase }
//   hyperliquid: { wallet_address, agent_private_key }
router.post('/', credLimit, async (req, res) => {
  try {
    // FIRST, before anything is read from the body. Refusing after collecting
    // the fields would be the same refusal with the keys sitting in memory.
    const prot = await protection();
    if (prot.mode === 'off') {
      return res.status(503).json({
        error: 'connect_unavailable',
        reason: prot.reason,
        detail: OFF_DETAIL[prot.reason],
      });
    }
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    if (!u || !u.telegram_linked || !u.telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Live trading and exchange keys require a linked Telegram account. Paper trading works without it.' });
    }
    // ── 2FA STEP-UP, and the asymmetry that made it necessary ──────────────
    //
    // /api/controls requires a fresh code to ENABLE live trading or RAISE a
    // margin cap, and /api/trade/confirm requires one to place an order.
    // Connecting the exchange credential those actions ultimately SPEND
    // required nothing at all — so a stolen session (the threat lib/stepup.js
    // names in its header) could POST its own venue keys here and the
    // victim's engine would place their live orders on the attacker's
    // account, while the account they had connected stopped being reconciled.
    // DELETE below is the same door pointed at availability.
    const blk = stepUpBlock(u.totp_enabled, u.totp_secret, (req.body || {}).totp_code,
      'Enter your 6-digit authenticator code to connect exchange keys.',
      uidKey(uid));
    if (blk) { secLog('creds_connect_2fa', req); return res.status(blk.status).json(blk.body); }

    // Same subject or refuse: the keys are filed under this row's telegram_id,
    // so the session and that identity must agree — see lib/identity.
    const mism = await foreignIdentityBlock(u.telegram_id, uid);
    if (mism) { secLog('creds_identity_mismatch', req); return res.status(mism.status).json(mism.body); }

    const body = req.body || {};
    const venue = String(body.venue || 'bitget').toLowerCase();
    if (!isVenue(venue)) {
      return res.status(400).json({ error: 'Unknown venue.' });
    }
    // Collect exactly the venue's required fields; reject missing/malformed.
    const fields = venueFields(venue);
    const plain = { venue };
    for (const f of fields) {
      const v = body[f];
      if (!v || typeof v !== 'string' || v.length > MAX_FIELD) {
        return res.status(400).json({ error: `Missing or malformed field: ${f}.` });
      }
      plain[f] = String(v);
    }
    // Protect the secret material immediately (venue rides along so the bot's
    // pull imports it into the right venue). Never logged. `sealJSON` throws
    // rather than degrading to the shared key, and the catch below answers 500
    // — a submission must never be stored under weaker protection than the
    // gate above just promised.
    const payload = prot.mode === 'sealed'
      ? creds.sealJSON(plain, { pem: prot.rec.pem, kid: prot.rec.kid })
      : creds.encryptJSON(plain);
    await pool.execute(
      `INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
       VALUES (?, ?, ?, 'connect', ?)
       ON DUPLICATE KEY UPDATE telegram_id = VALUES(telegram_id),
         exchange = VALUES(exchange), action = 'connect',
         encrypted_payload = VALUES(encrypted_payload),
         created_at = CURRENT_TIMESTAMP`,
      [uid, String(u.telegram_id), venue, payload]
    );
    secLog('exchange_connect_submitted', req, `venue=${venue} protection=${prot.mode}`);
    res.json({ ok: true, pending: 'connect', venue });
  } catch (err) {
    console.error('Cred submit error:', err.stack || err.message); // never logs the body
    res.status(500).json({ error: 'Failed to submit credentials' });
  }
});

// DELETE /api/credentials -> queue a disconnect (bot removes them from its store)
//
// NO STEP-UP HERE, DELIBERATELY, and it is the same rule routes/controls.js
// states for /stop: de-risking is never gated. Disconnecting keys is how a
// user stops the bot trading their account, and a 403 on that path — because
// they lost their authenticator, or because the lockout window is open —
// would hold a live account hostage to close a hole that costs an attacker
// nothing but availability. The connect direction is gated; the retreat is not.
router.delete('/', credLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    const tg = u && u.telegram_id ? String(u.telegram_id) : '';
    // Venue-scoped disconnect: ?venue=bybit removes ONLY that exchange's keys
    // (the bot's store.delete_venue). Without the param, fall back to the
    // first connected venue for older clients.
    let venue = String(req.query.venue || '').toLowerCase();
    if (venue && !isVenue(venue)) {
      return res.status(400).json({ error: 'Unknown venue.' });
    }
    if (!venue) {
      const [st] = await pool.execute(
        'SELECT connected, exchange FROM exchange_status WHERE user_id = ?', [uid]);
      const first = st.find(r => !!r.connected) || st[0];
      venue = (first && first.exchange) || 'bitget';
    }
    await pool.execute(
      `INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
       VALUES (?, ?, ?, 'disconnect', NULL)
       ON DUPLICATE KEY UPDATE exchange = VALUES(exchange), action = 'disconnect',
         encrypted_payload = NULL, created_at = CURRENT_TIMESTAMP`,
      [uid, tg, venue]
    );
    secLog('exchange_disconnect_requested', req);
    res.json({ ok: true, pending: 'disconnect' });
  } catch (err) {
    console.error('Cred disconnect error:', err.stack || err.message);
    res.status(500).json({ error: 'Failed to queue disconnect' });
  }
});

module.exports = router;
