'use strict';
/**
 * Coach AI disclosure for Ask the Claw.
 *
 * Two sentences, owned here. The Pi page draws the same words
 * (public/js/pi-coach.js); a test holds the copies equal.
 *
 *   NOTICE  the reader is talking to an AI, not a person.
 *   MARK    a reply a model generated. A fixed answer written in the repo
 *           is not marked: the notice still covers the conversation.
 *
 * COACH_AI_DISCLOSURE_ENABLED is off unless it is "1" or "true".
 * The public sees it only when that is on and
 * COACH_AI_DISCLOSURE_NETWORK is "testnet". Any other network, including
 * an unset one, keeps the public reader off. While the public reader is
 * off, an operator sees the same sentences as an admin preview.
 *
 * This module does not sign, does not read a key, and does not call a chain.
 *
 * product/spec-ai-disclosure-2026-10-26.md was not in the repository on
 * 2026-10-30 (main and every remote branch). These sentences are the
 * disclosure. No statute is named.
 */

const { flag_on_for } = require('./admin_preview');

const FLAG = 'COACH_AI_DISCLOSURE_ENABLED';
const NETWORK = 'COACH_AI_DISCLOSURE_NETWORK';
const NOTICE = 'You are talking to the Coach, an AI. It is not a person.';
const MARK = 'AI-generated';
const MODEL_INTENT = 'pi_coach';

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
  return { notice: NOTICE, mark: MARK, preview: view.preview === true };
}

/**
 * Fields for one coach reply. A model answer is marked. A fixed answer
 * carries the notice only. An off view adds nothing.
 */
function replyFields(view, intent) {
  const body = payload(view);
  if (!body) return {};
  if (intent === MODEL_INTENT) {
    return { ai_generated: true, ai_mark: MARK, ai_disclosure: body };
  }
  return { ai_disclosure: body };
}

module.exports = {
  FLAG, NETWORK, NOTICE, MARK, MODEL_INTENT,
  flagOn, testnet, publicOn, viewFor, payload, replyFields,
};
