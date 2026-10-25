'use strict';
/**
 * Which Pi Developer Portal app this request is for.
 *
 * The Mainnet app and a Testnet app are two apps on one server. The Testnet
 * app is recognized in two ways, and only these two:
 *
 *   1. Express's req.hostname equals PI_TESTNET_HOST (for example
 *      pi.humanoid-traders.com). That is the Host header with the port
 *      removed, and X-Forwarded-Host only when this process's trust proxy
 *      already trusts the connecting hop (server.js, TRUSTED_PROXY). A second
 *      reading of that header here would trust a caller the rest of the
 *      server does not. Hostnames are case-insensitive; the setting may name
 *      a port.
 *   2. The request carries X-Runeclaw-Pi-Proxy and its value matches
 *      PI_TESTNET_PROXY_SECRET, compared in constant time. This is how the
 *      owner's Cloudflare Worker, which fetches www and therefore cannot
 *      preserve Host, proves the browser was on the Testnet hostname.
 *      Unset, or shorter than 32 characters, the header is ignored. A wrong
 *      value, a repeated header, or any other header (including
 *      X-Forwarded-Host) does not count.
 *
 * The secret and the header value are never logged.
 */

const crypto = require('crypto');

const PROXY_HEADER = 'x-runeclaw-pi-proxy';
const MIN_PROXY_SECRET = 32;
const PUBLIC_WWW = 'https://www.humanoid-traders.com';

function requestHost(req) {
  return String((req && req.hostname) || '').trim().toLowerCase().replace(/:\d+$/, '');
}

function testnetHost(env = process.env) {
  return String((env && env.PI_TESTNET_HOST) || '').trim().toLowerCase().replace(/:\d+$/, '');
}

/**
 * One header value, or a refusal. A repeated header is joined by Node into
 * one comma-separated string or left as an array; either is not the secret
 * and not an origin a browser sent.
 */
function oneHeader(req, name) {
  if (!req || !req.headers) return '';
  const raw = req.headers[name];
  if (typeof raw !== 'string') return '';
  if (raw.includes(',')) return '';
  return raw;
}

/** The configured secret, or '' when the header path is disabled. */
function proxySecret(env = process.env) {
  const s = String((env && env.PI_TESTNET_PROXY_SECRET) || '');
  return s.length >= MIN_PROXY_SECRET ? s : '';
}

/**
 * True only when the proxy header matches the configured secret. A missing
 * or too-short secret is false, not a comparison against an empty string.
 */
function proxyAuthenticates(req, env = process.env) {
  const want = proxySecret(env);
  if (!want) return false;
  const given = oneHeader(req, PROXY_HEADER);
  const a = Buffer.from(given);
  const b = Buffer.from(want);
  // timingSafeEqual throws on unequal lengths. A different length is a miss,
  // not a 500.
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

function isTestnetHost(req, env = process.env) {
  const want = testnetHost(env);
  if (want.length > 0 && requestHost(req) === want) return true;
  return proxyAuthenticates(req, env);
}

/** https://<PI_TESTNET_HOST>, or '' when that setting is empty. */
function testnetOrigin(env = process.env) {
  const host = testnetHost(env);
  return host ? `https://${host}` : '';
}

/**
 * Absolute origin for the Pi page's own links. The Testnet app uses its
 * hostname; every other request keeps the Mainnet site. A Testnet decision
 * with no hostname configured does not invent a URL.
 */
function pageOrigin(req, env = process.env) {
  if (!isTestnetHost(req, env)) return PUBLIC_WWW;
  const origin = testnetOrigin(env);
  return /^https:\/\/[a-z0-9.-]+$/.test(origin) ? origin : PUBLIC_WWW;
}

/**
 * Whether a state-changing Pi payment may trust this request's Origin.
 *
 * No Origin: a non-browser caller. Browsers send Origin on POST.
 * The origin's host equals the request host: the page and the API are the
 * same host (www, or the Testnet host reached directly).
 * https://<PI_TESTNET_HOST>: only after the proxy secret has authenticated
 * the request. A www request that merely names that origin is refused.
 */
function paymentOriginAllowed(req, env = process.env) {
  const headers = req && req.headers;
  const raw = headers ? headers.origin : undefined;
  if (raw == null || raw === '') return true;
  if (typeof raw !== 'string' || raw.includes(',')) return false;
  let url;
  try { url = new URL(raw); } catch (e) { return false; }
  if (url.username || url.password) return false;
  if (url.pathname !== '/' || url.search || url.hash) return false;
  const name = url.hostname.toLowerCase();
  const localHttp = url.protocol === 'http:' && (name === 'localhost' || name === '127.0.0.1');
  if (url.protocol !== 'https:' && !localHttp) return false;
  if (name === requestHost(req)) return true;
  if (!proxyAuthenticates(req, env)) return false;
  const want = testnetOrigin(env);
  return want.length > 0 && url.origin === want;
}

module.exports = {
  requestHost, testnetHost, isTestnetHost, proxyAuthenticates, proxySecret,
  paymentOriginAllowed, testnetOrigin, pageOrigin,
  PROXY_HEADER, MIN_PROXY_SECRET, PUBLIC_WWW,
};
