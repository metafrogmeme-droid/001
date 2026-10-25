'use strict';
/**
 * Admin preview of a product flag that is still off for everyone else.
 *
 * The public reader of each flag stays where it is (`enabled`,
 * `flag_on`, `avalancheEnabled`). This module asks that reader, and when the answer is
 * off it asks the existing operator check: `users.plan === 'admin'`, re-read
 * from the database (`lib/operator_view.js` `isOperator`). Signed in is not
 * enough. A failed read is not an admin. There is no second allowlist.
 *
 * An unknown flag is off. Preview never grants a name this file does not list.
 * A sibling feature registers by adding its public reader to `READERS` (the
 * reader must not be named `enabled` when that name would collide — see
 * bot/scam_or_safe.py) and calling `flag_on_for(req, FLAG)` at the 404.
 *
 * The banner is the sentence a page shows while a feature is on only because
 * the caller is an admin.
 */

const { isOperator } = require('./operator_view');
const { tokenFromRequest } = require('./session_cookie');

const BANNER = 'Admin preview. Not public.';

/**
 * Public readers, required lazily. Several of those modules require this
 * file, so a top-level require here would cycle.
 */
const READERS = {
  RWA_PRECHECK_ENABLED(env) {
    return require('./rwa_flag').enabled(env) === true;
  },
  CLAW_GUARD_V01(env) {
    return require('./claw_guard_v01').enabled(env) === true;
  },
  SCAM_OR_SAFE_V0(env) {
    return require('./scam_or_safe').enabled(env) === true;
  },
  DUEL_CONFIDENCE_ENABLED(env) {
    return require('./duel_confidence').enabled(env) === true;
  },
  DUEL_CHIPS_ENABLED(env) {
    return require('./duel_chips').enabled(env) === true;
  },
  AVALANCHE_ANCHOR_ENABLED(env) {
    return require('./root_anchor').avalancheEnabled(env) === true;
  },
  SAR_ORACLE_ENABLED(env) {
    return require('./sar/flag').flag_on(env) === true;
  },
  SRB_ENABLED(env) {
    return require('./srb_flag').flag_on(env) === true;
  },
  FVB_ENABLED(env) {
    return require('./fvb_flag').flagOn(env) === true;
  },
  COVER_ADL_ENABLED(env) {
    return require('../../contracts/cover/flags.cjs').adlOn(env) === true;
  },
  COVER_STOPGAP_ENABLED(env) {
    return require('../../contracts/cover/flags.cjs').stopGapOn(env) === true;
  },
};

function publicOn(flag, env = process.env) {
  if (!Object.prototype.hasOwnProperty.call(READERS, flag)) return false;
  return READERS[flag](env) === true;
}

/** A bearer token or the session cookie. `rc_auth` is not a credential. */
function hasCredential(req) {
  return Boolean(tokenFromRequest(req));
}

/**
 * Name the caller only when a credential is present. `app/auth.js` exits the
 * process at load if JWT_SECRET is missing, so a flag-off anonymous request
 * must not load it.
 */
async function identify(req) {
  if (!req || req.user) return;
  if (!hasCredential(req)) return;
  const { optionalAuth } = require('../auth');
  await optionalAuth(req, {}, () => {});
}

async function flag_on_for(req, flag, env = process.env) {
  if (!Object.prototype.hasOwnProperty.call(READERS, flag)) return false;
  if (publicOn(flag, env) === true) return true;
  try {
    await identify(req);
    return (await isOperator(req)) === true;
  } catch (e) {
    return false;
  }
}

/** Flags that are publicly off and on for this caller only. One operator read. */
async function previewFlags(req, env = process.env) {
  const off = Object.keys(READERS).filter((flag) => publicOn(flag, env) !== true);
  if (!off.length) return [];
  try {
    await identify(req);
    if ((await isOperator(req)) !== true) return [];
  } catch (e) {
    return [];
  }
  return off;
}

function bannerHtml() {
  return '<p class="admin-preview" role="note" style="padding:8px 12px;border:1px solid currentColor;font:14px/1.4 system-ui,sans-serif">'
    + BANNER + '</p>';
}

function withBanner(html) {
  const doc = String(html == null ? '' : html);
  const tag = bannerHtml();
  if (doc.includes(BANNER)) return doc;
  const at = doc.search(/<body\b[^>]*>/i);
  if (at < 0) return tag + doc;
  const end = doc.indexOf('>', at);
  if (end < 0) return tag + doc;
  return doc.slice(0, end + 1) + tag + doc.slice(end + 1);
}

module.exports = {
  BANNER, READERS, publicOn, hasCredential, identify, flag_on_for, previewFlags, bannerHtml, withBanner,
};

// --- XNAV begin ---
// Lazy: xnav_flag requires this module, so the reader cannot be a top-level require.
READERS.XNAV_ENABLED = function (env) {
  return require('./xnav_flag').flagOn(env) === true;
};
// --- XNAV end ---
// --- LIMIT SCANNER SHADOW begin ---
// Lazy: the flag module requires this file, so the reader cannot load at the top.
READERS.LIMIT_SCANNER_SHADOW = function (env) {
  return require('./limit_scanner_shadow_flag').flagOn(env) === true;
};
// --- LIMIT SCANNER SHADOW end ---
