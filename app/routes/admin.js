/**
 * The operator's Admin page: the bot's runtime settings, read and changed in
 * one place, beside the website's own configuration state.
 *
 * THE WEB PROPOSES; THE BOT DECIDES. Every read and every change is forwarded
 * to the bot gateway (`/admin/settings`, `/admin/settings/set`), which
 * re-checks that the caller is an admin and writes through
 * `bot/core/settings_registry.py`, the path the Telegram commands take. A
 * change made here is bounded, saved and recorded exactly as one made with
 * /leverage or /autoconfirm is, and the bot's record names the door.
 *
 * This file adds the web's own locks in front of that:
 *
 *  - OPERATOR ONLY: `users.plan` re-read from the database on every request,
 *    never the JWT (signed in is not the operator), and a linked Telegram id
 *    the bot can check.
 *  - A CHANGE NEEDS 2FA. A stolen web session must not be able to raise the
 *    leverage or loosen the drawdown cap, so every change asks for a fresh
 *    authenticator code, and an account with no 2FA enrolled may read the
 *    page but not change anything from it. Telegram stays a door either way.
 *  - LIVE TRADING AND THE VENUE ARE NOT SETTABLE HERE. Refused before the
 *    gateway is asked, and the bot refuses them by name as well.
 *
 * The read is a composite of two sources, the bot's settings and this site's
 * configuration findings. Each is read on its own and a dead one is left out
 * and named (`bot_error`, `web_error`), so a bot outage does not blank the
 * website half, and the reverse.
 */
const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { postGateway, isConfigured } = require('../lib/gateway');
const { stepUpBlock } = require('../lib/stepup');
const { uidKey } = require('../lib/second_factor_lockout');
const { foreignIdentityBlock } = require('../lib/identity');
const { secLog } = require('../lib/seclog');
const { auditConfig } = require('../lib/config_audit');

const router = express.Router();
router.use(authMiddleware);
const readLimit = rateLimit({ name: 'admin.read', windowMs: 60000, max: 30, key: userKey });
const changeLimit = rateLimit({ name: 'admin.change', windowMs: 60000, max: 12, key: userKey });

// Settable only from Telegram, whatever this page is sent.
const LOCKED = new Set(['live_mode', 'venue']);
const KEY_RE = /^[a-z][a-z0-9_]{1,47}$/;

/** The caller's row when they are the operator; otherwise a 403 is sent and null returned. */
async function operatorRow(req, res) {
  const [rows] = await pool.execute(
    'SELECT plan, telegram_linked, telegram_id, totp_enabled, totp_secret FROM users WHERE id = ?',
    [req.user.user_id]);
  const u = rows && rows[0];
  if (!u || String(u.plan) !== 'admin') {
    secLog('admin_denied', req);
    res.status(403).json({ error: 'admin_required', detail: 'The Admin page is for the operator only.' });
    return null;
  }
  return u;
}

/**
 * This site's configuration findings, keys and levels with their sentences.
 * The one finding that names a host (plain-HTTP gateway) has the host taken
 * out: the boot log names it so it is actionable, and a web page is not a
 * place for internal hostnames.
 */
function webFindings(env = process.env) {
  const findings = auditConfig({ env, log: { warn() {}, error() {} }, onFatal() {} });
  let host = '';
  try { host = new URL(String(env.BOT_GATEWAY_URL || '').trim()).hostname; } catch (_) { host = ''; }
  return findings.map((f) => ({
    level: f.level,
    key: f.key,
    msg: host ? String(f.msg).split(host).join('the gateway host') : String(f.msg),
  }));
}

// GET /api/admin/settings — the bot's settings, the site's findings, and
// whether this account can change anything (2FA enrolled).
router.get('/settings', readLimit, async (req, res) => {
  let u;
  try {
    u = await operatorRow(req, res);
  } catch (err) {
    console.error('Admin operator check error:', err.stack || err.message);
    return res.status(500).json({ error: 'operator_check_failed' });
  }
  if (!u) return undefined;
  const out = {
    bot: null, bot_error: null,
    web: null, web_error: null,
    two_factor: Boolean(u.totp_enabled),
  };
  try {
    out.web = { findings: webFindings(), gateway_configured: isConfigured() };
  } catch (err) {
    out.web_error = (err && err.name) || 'Error';
  }
  if (!u.telegram_linked || !u.telegram_id) {
    out.bot_error = 'telegram_required';
  } else if (!isConfigured()) {
    out.bot_error = 'gateway_unconfigured';
  } else {
    try {
      const r = await postGateway('/admin/settings', { telegram_id: String(u.telegram_id) }, 12000);
      if (r.status >= 200 && r.status < 300 && r.data && typeof r.data === 'object') {
        out.bot = r.data;
      } else {
        out.bot_error = (r.data && typeof r.data.error === 'string') ? r.data.error : `status_${r.status}`;
      }
    } catch (err) {
      console.error('Admin settings gateway error:', err.stack || err.message);
      out.bot_error = 'unreachable';
    }
  }
  return res.json(out);
});

// POST /api/admin/settings  body: { key, value } or { key, reset: true },
// plus totp_code once the 2FA prompt has asked for it.
router.post('/settings', changeLimit, async (req, res) => {
  try {
    const u = await operatorRow(req, res);
    if (!u) return undefined;
    const b = req.body || {};
    const key = String(b.key || '');
    if (!KEY_RE.test(key)) return res.status(400).json({ error: 'bad_key' });
    if (LOCKED.has(key)) {
      secLog('admin_locked_setting', req, `key=${key}`);
      return res.status(403).json({
        error: 'not_web_editable',
        detail: 'Live trading and the venue are changed from Telegram only.',
      });
    }
    const reset = b.reset === true;
    const v = b.value;
    if (!reset && !(typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')) {
      return res.status(400).json({ error: 'value_required', detail: 'Send a value, or reset.' });
    }
    if (!u.telegram_linked || !u.telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Link your Telegram account first so the bot can verify the change.' });
    }
    if (!isConfigured()) {
      return res.status(503).json({ error: 'gateway_unconfigured', detail: 'The bot gateway is not configured, so settings cannot be changed from here right now.' });
    }
    if (!u.totp_enabled) {
      secLog('admin_change_no_2fa', req, `key=${key}`);
      return res.status(403).json({
        error: 'two_factor_setup_required',
        detail: 'Turn on two-factor sign-in before changing settings from the website. Reading them works without it, and Telegram still works.',
      });
    }
    const blk = await stepUpBlock(u.totp_enabled, u.totp_secret, b.totp_code,
      'Enter your 6-digit authenticator code to change this setting.',
      uidKey(req.user.user_id));
    if (blk) { secLog('admin_change_2fa', req, `key=${key}`); return res.status(blk.status).json(blk.body); }
    const mism = await foreignIdentityBlock(u.telegram_id, req.user.user_id);
    if (mism) { secLog('admin_identity_mismatch', req); return res.status(mism.status).json(mism.body); }

    const payload = { telegram_id: String(u.telegram_id), key };
    if (reset) payload.reset = true; else payload.value = v;
    const r = await postGateway('/admin/settings/set', payload, 15000);
    secLog('admin_setting', req, `key=${key} reset=${reset} status=${r.status}`);
    if (r.status >= 200 && r.status < 500) return res.status(r.status).json(r.data);
    // A 5xx from the bot may follow a write that landed, so this does not
    // say "nothing changed".
    return res.status(502).json({
      error: 'bot_error',
      detail: 'The bot could not confirm the change. Reload the page to read the value in force.',
    });
  } catch (err) {
    console.error('Admin setting error:', err.stack || err.message);
    return res.status(502).json({
      error: 'unconfirmed',
      detail: 'The change could not be confirmed. Reload the page to read the value in force.',
    });
  }
});

// ── Members ──────────────────────────────────────────────────────────────
//
// The same doors as the settings above: the bot re-checks the caller and
// writes through `bot/core/member_admin.py`, the path /approve, /revoke,
// /set_tier and /revoke_live take, and records the door. Granting live
// trading or the admin role or tier is Telegram-only, refused here by name
// before the bot is asked, and refused again there.
const MEMBER_ACTIONS = new Set(['approve', 'revoke', 'set_tier', 'revoke_live']);
const MEMBER_TELEGRAM_ONLY = {
  grant_live: 'Live trading is granted from Telegram only (/grant_live).',
  grant_admin: 'The admin role is granted from Telegram only (/approve <id> admin).',
};
const MEMBER_ID_RE = /^(?:[0-9]{1,20}|web:[0-9]{1,20})$/;
const MEMBER_WORD_RE = /^[a-z]{1,16}$/;

// GET /api/admin/members — the roster and the recent member changes, from the bot.
router.get('/members', readLimit, async (req, res) => {
  let u;
  try {
    u = await operatorRow(req, res);
  } catch (err) {
    console.error('Admin operator check error:', err.stack || err.message);
    return res.status(500).json({ error: 'operator_check_failed' });
  }
  if (!u) return undefined;
  const out = { bot: null, bot_error: null, two_factor: Boolean(u.totp_enabled) };
  if (!u.telegram_linked || !u.telegram_id) {
    out.bot_error = 'telegram_required';
  } else if (!isConfigured()) {
    out.bot_error = 'gateway_unconfigured';
  } else {
    try {
      const r = await postGateway('/admin/members', { telegram_id: String(u.telegram_id) }, 12000);
      if (r.status >= 200 && r.status < 300 && r.data && typeof r.data === 'object') {
        out.bot = r.data;
      } else {
        out.bot_error = (r.data && typeof r.data.error === 'string') ? r.data.error : `status_${r.status}`;
      }
    } catch (err) {
      console.error('Admin members gateway error:', err.stack || err.message);
      out.bot_error = 'unreachable';
    }
  }
  return res.json(out);
});

// POST /api/admin/members  body: { action, target, role?, tier? }, plus
// totp_code once the 2FA prompt has asked for it.
router.post('/members', changeLimit, async (req, res) => {
  try {
    const u = await operatorRow(req, res);
    if (!u) return undefined;
    const b = req.body || {};
    const action = String(b.action || '');
    if (Object.prototype.hasOwnProperty.call(MEMBER_TELEGRAM_ONLY, action)) {
      secLog('admin_member_telegram_only', req, `action=${action}`);
      return res.status(403).json({ error: 'telegram_only', detail: MEMBER_TELEGRAM_ONLY[action] });
    }
    if (!MEMBER_ACTIONS.has(action)) return res.status(400).json({ error: 'unknown_action' });
    const target = String(b.target || '').trim();
    if (!MEMBER_ID_RE.test(target)) return res.status(400).json({ error: 'bad_target' });
    const word = (v) => (v === undefined || v === null ? undefined : String(v).trim().toLowerCase());
    const role = word(b.role);
    const tier = word(b.tier);
    if ((role !== undefined && !MEMBER_WORD_RE.test(role)) || (tier !== undefined && !MEMBER_WORD_RE.test(tier))) {
      return res.status(400).json({ error: 'bad_value' });
    }
    if (role === 'admin' || tier === 'admin') {
      secLog('admin_member_telegram_only', req, `action=${action}`);
      return res.status(403).json({ error: 'telegram_only', detail: MEMBER_TELEGRAM_ONLY.grant_admin });
    }
    if (!u.telegram_linked || !u.telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Link your Telegram account first so the bot can verify the change.' });
    }
    if (!isConfigured()) {
      return res.status(503).json({ error: 'gateway_unconfigured', detail: 'The bot gateway is not configured, so members cannot be changed from here right now.' });
    }
    if (!u.totp_enabled) {
      secLog('admin_member_no_2fa', req, `action=${action}`);
      return res.status(403).json({
        error: 'two_factor_setup_required',
        detail: 'Turn on two-factor sign-in before changing members from the website. Reading the list works without it, and Telegram still works.',
      });
    }
    const blk = await stepUpBlock(u.totp_enabled, u.totp_secret, b.totp_code,
      'Enter your 6-digit authenticator code to change this member.',
      uidKey(req.user.user_id));
    if (blk) { secLog('admin_member_2fa', req, `action=${action}`); return res.status(blk.status).json(blk.body); }
    const mism = await foreignIdentityBlock(u.telegram_id, req.user.user_id);
    if (mism) { secLog('admin_identity_mismatch', req); return res.status(mism.status).json(mism.body); }

    const payload = { telegram_id: String(u.telegram_id), action, target };
    if (role !== undefined) payload.role = role;
    if (tier !== undefined) payload.tier = tier;
    const r = await postGateway('/admin/members/act', payload, 15000);
    secLog('admin_member', req, `action=${action} status=${r.status}`);
    if (r.status >= 200 && r.status < 500) return res.status(r.status).json(r.data);
    // A 5xx from the bot may follow a write that landed.
    return res.status(502).json({
      error: 'bot_error',
      detail: 'The bot could not confirm the change. Reload the page to read the member as they stand.',
    });
  } catch (err) {
    console.error('Admin member error:', err.stack || err.message);
    return res.status(502).json({
      error: 'unconfirmed',
      detail: 'The change could not be confirmed. Reload the page to read the member as they stand.',
    });
  }
});

router._webFindings = webFindings;
module.exports = router;
