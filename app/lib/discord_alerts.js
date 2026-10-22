'use strict';
/**
 * Operator alerts to a Discord channel, through an incoming webhook.
 *
 * The owner chose Discord for the anchor watch's alerts (22 October 2026).
 * The hook's URL is `ANCHOR_ALERTS_DISCORD_WEBHOOK` in web.env. Its last
 * segment is a token, so the URL is a credential:
 *   - it is read from the environment and checked against Discord's own
 *     webhook shape, so a mistyped setting is "not a Discord webhook URL",
 *     never a POST to somewhere else;
 *   - it is never logged, returned or put in an error. A failure is named by
 *     the HTTP status Discord answered, or by its class;
 *   - the text is scrubbed by the shared secret table before it is sent
 *     (lib/secret_shapes.js, which knows this URL's shape too).
 * Mentions are disabled on every message, so no text in a finding can ping
 * the channel. A message carries no account, no wallet address and no amount.
 */
const { scrubSecrets } = require('./secret_shapes');

const ENV = 'ANCHOR_ALERTS_DISCORD_WEBHOOK';
const HOOK_RE = /^https:\/\/(?:(?:canary|ptb)\.)?discord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\/\d{5,25}\/[A-Za-z0-9_-]{20,200}$/;
/** Discord's limit on a message's `content`. */
const MAX_CHARS = 2000;
const USERNAME = 'RUNECLAW anchors';

/** Whether a hook is configured: `{ configured: true, url }`, or `{ configured: false, reason }`. */
function hookOf(env = process.env) {
  const raw = String(env[ENV] || '').trim();
  if (!raw) return { configured: false, reason: 'not set' };
  if (!HOOK_RE.test(raw)) return { configured: false, reason: 'not a Discord webhook URL' };
  return { configured: true, url: raw };
}

/** The hook's state for a diagnostic: never the URL. */
function describe(env = process.env) {
  const h = hookOf(env);
  return h.configured ? { configured: true } : { configured: false, reason: h.reason };
}

/** Lines as one message under Discord's limit: whole lines, and the ones left out counted. */
function compose(lines) {
  const whole = lines.map(String).join('\n');
  if (whole.length <= MAX_CHARS) return whole;
  const out = [];
  let used = 0;
  for (let i = 0; i < lines.length; i += 1) {
    const line = String(lines[i]);
    const rest = lines.length - i;
    const tail = `… and ${rest} more line${rest === 1 ? '' : 's'} (see /diagz)`;
    if (used + line.length + 1 > MAX_CHARS - tail.length - 1) {
      out.push(tail);
      break;
    }
    out.push(line);
    used += line.length + 1;
  }
  return out.join('\n');
}

class DiscordError extends Error {}

/**
 * POST `content` to the configured hook. Resolves on a 2xx; throws a
 * DiscordError naming Discord's status, or one carrying the failure's class.
 * Neither message nor stack carries the URL.
 */
async function post(content, { fetchImpl = global.fetch, env = process.env, timeoutMs = 10000 } = {}) {
  const hook = hookOf(env);
  if (!hook.configured) {
    const e = new DiscordError(`no Discord webhook: ${hook.reason}`);
    e.name = 'NotConfigured';
    throw e;
  }
  const ctl = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  let r;
  try {
    r = await fetchImpl(hook.url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: scrubSecrets(String(content)), username: USERNAME, allowed_mentions: { parse: [] } }),
      ...(ctl ? { signal: ctl.signal } : {}),
    });
  } catch (err) {
    // A fetch error's message can carry the URL it was fetching: a fresh error, its class only.
    const e = new DiscordError(`Discord could not be reached (${(err && err.name) || 'Error'})`);
    e.name = (err && err.name) || 'Error';
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!r || !(r.status >= 200 && r.status < 300)) {
    const e = new DiscordError(`Discord answered ${r ? r.status : 'nothing'}`);
    e.name = 'DiscordHttpError';
    e.status = r ? r.status : null;
    throw e;
  }
}

module.exports = { ENV, HOOK_RE, MAX_CHARS, USERNAME, hookOf, describe, compose, post };
