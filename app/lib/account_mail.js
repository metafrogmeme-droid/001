'use strict';
/**
 * Account notices: the emails that tell a person something changed on their
 * account, so a change they did not make reaches them while it still matters,
 * and the two that are not security mail (the plan notice and the one-time
 * first-steps mail).
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
 * The words and the layout are lib/email_templates; each template says
 * whether it is a security mail. A security notice is always sent and never
 * asks about preferences. Any other kind needs the member's users row id,
 * carries an unsubscribe link and a one-click List-Unsubscribe header, and is
 * not sent when the member opted out, or when that cannot be read: a
 * preference nobody could read is not "opted in" (lib/email_prefs.js).
 *
 * `deliverable` is the one reading of "can this address receive mail". The
 * app synthesises addresses for accounts that have no inbox (Telegram-, X-,
 * Discord- and wallet-only sign-ins, and erased accounts), and those must
 * never be sent to: a relay bounces them back into the sender's own inbox,
 * and the person is told a message went out when none could.
 */
const mailer = require('./mailer');
const emailTemplates = require('./email_templates');
const emailPrefs = require('./email_prefs');

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

// The website's plan names, in rank order (basic < pro < elite): the order
// of the keys is the one reading of which way a change went. `admin` is
// never mailed: the website reads it as the operator, and the bot refuses to
// grant it to a website account.
const PLAN_NAMES = Object.freeze({ basic: 'Basic', pro: 'Pro', elite: 'Elite' });

/**
 * Which plan notice to send. The downgrade wording only when the plan before
 * the change is known and ranked higher; otherwise the upgrade wording, which
 * never says "upgraded", so it stays true when the direction is not known.
 * (The bot sends the plan the member held as `from`, bot/core/member_admin.py
 * set_tier; it leaves it out when the record held no tier.)
 */
function tierTemplate(tier, from) {
  const order = Object.keys(PLAN_NAMES);
  const now = order.indexOf(tier);
  const before = order.indexOf(from);
  return now >= 0 && before >= 0 && now < before ? 'tier_downgrade' : 'tier_upgrade';
}

function _isUserId(v) {
  return Number.isSafeInteger(v) && v > 0;
}

/**
 * The message for one notice: { subject, preheader, text, html, security }.
 * Throws on an unknown kind, an unknown plan, or a non-security kind with no
 * member to build its unsubscribe link for.
 */
function compose(kind, at = new Date(), vars = {}) {
  const security = emailTemplates.isSecurity(kind);
  const base = mailer.baseUrl();
  const v = { ...vars, base, when: emailTemplates.whenText(at) };
  if (kind === 'tier_upgrade' || kind === 'tier_downgrade') {
    if (!Object.prototype.hasOwnProperty.call(PLAN_NAMES, vars.tier)) {
      throw new Error(`no plan name for tier: ${vars.tier}`);
    }
    v.tier_name = PLAN_NAMES[vars.tier];
  }
  if (!security) {
    if (!_isUserId(vars.userId)) throw new Error(`${kind} needs the member's users row id`);
    v.unsubscribe_url = emailPrefs.preferencesUrl(base, vars.userId);
  }
  return emailTemplates.render(kind, v);
}

/**
 * Send one notice. Resolves to what happened, never rejects:
 *   { sent: true }
 * | { sent: false, reason: 'undeliverable' | 'not_configured' | 'failed'
 *                        | 'opted_out' | 'prefs_unreadable'
 *                        | 'no_unsubscribe_key' | 'no_member' }
 *
 * `vars.loadFeatures`, when given, is called only once the notice is going
 * to be sent (after the opt-out check), so a member who opted out costs no
 * read of the bot's plan ladder.
 */
async function notify(kind, email, at = new Date(), vars = {}) {
  if (!deliverable(email)) return { sent: false, reason: 'undeliverable' };
  try {
    const security = emailTemplates.isSecurity(kind);
    const v = { ...vars };
    let listUnsubscribe;
    if (!security) {
      if (!emailPrefs.available()) return { sent: false, reason: 'no_unsubscribe_key' };
      if (!_isUserId(v.userId)) return { sent: false, reason: 'no_member' };
      let out;
      try {
        out = await emailPrefs.optedOut(v.userId);
      } catch (err) {
        console.error(`[account_mail] ${kind} preference unreadable: ${err && err.name}`);
        return { sent: false, reason: 'prefs_unreadable' };
      }
      if (out) return { sent: false, reason: 'opted_out' };
      listUnsubscribe = emailPrefs.oneClickUrl(mailer.baseUrl(), v.userId);
    }
    if (typeof v.loadFeatures === 'function') {
      v.features = await v.loadFeatures();
      delete v.loadFeatures;
    }
    const msg = compose(kind, at, v);
    const r = await mailer.sendMail({
      to: String(email).trim(), subject: msg.subject, text: msg.text, html: msg.html,
      ...(listUnsubscribe ? { listUnsubscribe } : {}),
    });
    if (r && r.skipped) return { sent: false, reason: 'not_configured' };
    return { sent: true };
  } catch (err) {
    // The class, never the message: an SMTP reply can quote the recipient.
    console.error(`[account_mail] ${kind} notice failed: ${err && err.name}`);
    return { sent: false, reason: 'failed' };
  }
}

module.exports = { deliverable, compose, notify, tierTemplate, PLAN_NAMES };
