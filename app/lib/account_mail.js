'use strict';
/**
 * Account notices: the emails that tell a person something changed on their
 * account, so a change they did not make reaches them while it still matters.
 *
 * The account already revoked every session on a password change or reset,
 * and asked for a code before 2FA came off or the account was deleted. None
 * of that reached the OWNER: an attacker holding a stolen session could turn
 * 2FA off, and the first the owner heard of it was the next thing that went
 * wrong. A notice to the address on record is the standard answer, and the
 * only one that reaches someone who is not signed in.
 *
 * Best-effort, like every send in this app: a notice never fails or delays
 * the action it reports. `notify` does not throw, and the routes call it
 * without awaiting the delivery. It never carries a token, a code or a
 * secret: it names what changed and when, and what to do if it was not them.
 *
 * `deliverable` is the one reading of "can this address receive mail". The
 * app synthesises addresses for accounts that have no inbox (Telegram-, X-,
 * Discord- and wallet-only sign-ins, and erased accounts), and those must
 * never be sent to: a relay bounces them back into the sender's own inbox,
 * and the person is told a message went out when none could.
 */
const mailer = require('./mailer');

// The two shapes the app writes for an account with no real address:
// `<provider>-<id>@<provider>.runeclaw.local` (auth.js, findOrCreateOAuthUser)
// and `deleted-<id>@account.invalid` (account_erasure.js). Both TLDs are
// reserved and can never route, so no real address is caught by either.
const SYNTHETIC = /\.runeclaw\.local$|@account\.invalid$/i;

function deliverable(email) {
  const e = String(email == null ? '' : email).trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return false;
  return !SYNTHETIC.test(e);
}

function _when(at) {
  // "2026-10-14 15:32 UTC": unambiguous across the 14 languages the site
  // speaks, and no time zone the reader has to guess.
  return `${at.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

function _escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

const SECURITY = 'This is an automatic security notice from RUNECLAW.';

// The website's plan names. `admin` is never mailed: the website reads it as
// the operator, and the bot refuses to grant it to a website account.
const PLAN_NAMES = { basic: 'Basic', pro: 'Pro', elite: 'Elite' };

// What each change says. `done` is what the account already did about it,
// stated only where the route really does it (both password routes revoke
// every session). `ifNot` is the one thing to do if it was not them.
const NOTICES = {
  password_set: {
    subject: 'A password was added to your RUNECLAW account',
    what: 'A password was added to your RUNECLAW account',
    done: 'You can now sign in with your email and that password, as well as the way you signed in before. Every other signed-in session was signed out.',
    ifNot: (base) => `If this wasn't you, someone has signed in to your account. Reset the password now with "Forgot your password?" at ${base}/ and reply to this email.`,
  },
  password_changed: {
    subject: 'Your RUNECLAW password was changed',
    what: 'The password on your RUNECLAW account was changed',
    done: 'Every other signed-in session was signed out.',
    ifNot: (base) => `If this wasn't you, reset your password now with "Forgot your password?" at ${base}/ and reply to this email.`,
  },
  password_reset: {
    subject: 'Your RUNECLAW password was reset',
    what: 'The password on your RUNECLAW account was reset with an emailed link',
    done: 'Every signed-in session was signed out.',
    ifNot: (base) => `If this wasn't you, someone can read your email. Secure your email account first, then reset your RUNECLAW password again at ${base}/ and reply to this email.`,
  },
  totp_enabled: {
    subject: 'Two-factor authentication is on',
    what: 'Two-factor authentication was turned on for your RUNECLAW account',
    done: 'Signing in with a password now also asks for a code from your authenticator app.',
    ifNot: () => "If this wasn't you, someone has signed in to your account. Reply to this email straight away.",
  },
  totp_disabled: {
    subject: 'Two-factor authentication was turned off',
    what: 'Two-factor authentication was turned off for your RUNECLAW account',
    // The route asks for a signed-in session and a current code, not the
    // password, so the notice claims exactly that much about whoever did it.
    done: 'Signing in no longer asks for a code from your authenticator app.',
    ifNot: (base) => `If this wasn't you, someone is signed in to your account and has one of your codes. Reset your password now with "Forgot your password?" at ${base}/ (that signs every session out), turn two-factor back on, and reply to this email.`,
  },
  account_deleted: {
    subject: 'Your RUNECLAW account was deleted',
    what: 'Your RUNECLAW account was deleted',
    // What the route's own reply says, and no more: the bot half only runs
    // when a gateway is configured, so "and in the bot" is not always true.
    done: 'Your account and its data have been erased.',
    ifNot: () => "If this wasn't you, reply to this email straight away. A deletion cannot be undone, but we can look at how it happened.",
  },
  // Not a security notice: the operator changed the member's plan, and a
  // website-only member has no Telegram chat to be told in.
  tier_changed: {
    subject: (v) => `Your RUNECLAW plan is now ${PLAN_NAMES[v.tier]}`,
    what: (v) => `Your RUNECLAW plan was changed to ${PLAN_NAMES[v.tier]}`,
    done: (v, base) => `Your dashboard shows what it includes: ${base}/dashboard`,
    ifNot: null,
    footer: 'This is an automatic notice from RUNECLAW.',
  },
};

const _say = (x, v, base) => (typeof x === 'function' ? x(v, base) : x);

function compose(kind, at = new Date(), vars = {}) {
  const n = NOTICES[kind];
  if (!n) throw new Error(`unknown account notice: ${kind}`);
  if (kind === 'tier_changed' && !PLAN_NAMES[vars.tier]) {
    throw new Error(`no plan name for tier: ${vars.tier}`);
  }
  const base = mailer.baseUrl();
  const lines = [`${_say(n.what, vars, base)} on ${_when(at)}.`, _say(n.done, vars, base)];
  // ifNot reads the base as its first argument; tier_changed has none.
  if (n.ifNot) lines.push(n.ifNot(base));
  const footer = n.footer || SECURITY;
  return {
    subject: _say(n.subject, vars, base),
    text: `${lines.join('\n\n')}\n\n${footer}`,
    html: lines.map((l) => `<p>${_escape(l)}</p>`).join('')
      + `<p style="color:#888;font-size:12px">${_escape(footer)}</p>`,
  };
}

/**
 * Send one notice. Resolves to what happened, never rejects:
 *   { sent: true } | { sent: false, reason: 'undeliverable' | 'not_configured' | 'failed' }
 */
async function notify(kind, email, at = new Date(), vars = {}) {
  if (!deliverable(email)) return { sent: false, reason: 'undeliverable' };
  try {
    const msg = compose(kind, at, vars);
    const r = await mailer.sendMail({ to: String(email).trim(), ...msg });
    if (r && r.skipped) return { sent: false, reason: 'not_configured' };
    return { sent: true };
  } catch (err) {
    // The class, never the message: an SMTP reply can quote the recipient.
    console.error(`[account_mail] ${kind} notice failed: ${err && err.name}`);
    return { sent: false, reason: 'failed' };
  }
}

module.exports = { deliverable, compose, notify, NOTICES, PLAN_NAMES };
