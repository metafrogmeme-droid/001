'use strict';
/**
 * Coach disclosure for Ask the Claw and the diary coach.
 *
 * The sentences live here. The Pi page draws the same words
 * (public/js/pi-coach.js); a test holds the copies equal.
 *
 * COACH_AI_DISCLOSURE_ENABLED is off unless it is "1" or "true".
 * The public sees it only when that is on and
 * COACH_AI_DISCLOSURE_NETWORK is "testnet". Any other network, including
 * an unset one, keeps the public reader off. While the public reader is
 * off, an operator sees the same sentences as an admin preview.
 *
 * This module does not sign, does not read a key, and does not call a chain.
 */

const { flag_on_for } = require('./admin_preview');

const FLAG = 'COACH_AI_DISCLOSURE_ENABLED';
const NETWORK = 'COACH_AI_DISCLOSURE_NETWORK';
const NOTICE = "You're chatting with an AI coach, not a person. It can be wrong. It is not financial advice.";
const BADGE = 'AI';
const BADGE_TIP = 'AI-generated reply';
const SESSION_PREFIX = '🤖 AI coach. Not a person, not financial advice.';
const FIXED_INTENTS = new Set([
  'pi_coach_token', 'pi_coach_no_feed', 'pi_coach_withheld', 'pi_coach_unavailable',
]);

function source(env) {
  return env || process.env;
}

function flagOn(env) {
  const raw = source(env)[FLAG];
  if (raw == null) return false;
  const v = String(raw).trim().toLowerCase();
  return v === '1' || v === 'true';
}

/** True only for the word testnet. Unset is not a network. */
function testnet(env) {
  const raw = source(env)[NETWORK];
  if (raw == null) return false;
  return String(raw).trim().toLowerCase() === 'testnet';
}

function publicOn(env) {
  return flagOn(env) === true && testnet(env) === true;
}

/**
 * { show, preview }. show is whether this caller sees the sentences.
 * preview is whether that is only because they are an operator.
 * A thrown read is off.
 */
async function viewFor(req, env) {
  try {
    const src = source(env);
    if (publicOn(src) === true) return { show: true, preview: false };
    const admitted = (await flag_on_for(req, FLAG, src)) === true;
    if (admitted !== true) return { show: false, preview: false };
    return { show: true, preview: true };
  } catch (err) {
    return { show: false, preview: false };
  }
}

/** The object a response carries, or null when this caller does not see it. */
function payload(view) {
  if (!view || view.show !== true) return null;
  return {
    notice: NOTICE,
    badge: BADGE,
    badge_tip: BADGE_TIP,
    session_prefix: SESSION_PREFIX,
    preview: view.preview === true,
  };
}

/** Fields for one coach reply. Off adds nothing. On marks the reply. */
function replyFields(view) {
  const body = payload(view);
  if (!body) return {};
  return { ai_generated: true, ai_disclosure: body };
}

function stripPrefix(reply) {
  const text = String(reply == null ? '' : reply);
  if (!text.startsWith(SESSION_PREFIX)) return text;
  return text.slice(SESSION_PREFIX.length).replace(/^\n/, '');
}

/** The session's first reply starts with the prefix. A later one does not. */
function prefixReply(reply, first) {
  const text = stripPrefix(reply);
  if (first !== true) return text;
  return SESSION_PREFIX + '\n' + text;
}

/**
 * The JSON for one Pi coach reply. A fixed answer stays translatable, and
 * carries session_prefix when this is the first reply of the session. A
 * model's answer starts with the prefix in reply_html as well.
 */
function presentReply(replyHtml, intent, view, first) {
  const clean = stripPrefix(replyHtml);
  const fields = replyFields(view);
  if (!fields.ai_generated) return { reply_html: clean };
  const out = Object.assign({ reply_html: clean }, fields);
  if (first !== true) return out;
  out.session_prefix = SESSION_PREFIX;
  if (!FIXED_INTENTS.has(intent)) out.reply_html = prefixReply(clean, true);
  return out;
}

module.exports = {
  FLAG, NETWORK, NOTICE, BADGE, BADGE_TIP, SESSION_PREFIX, FIXED_INTENTS,
  flagOn, testnet, publicOn, viewFor, payload, replyFields,
  stripPrefix, prefixReply, presentReply,
};
