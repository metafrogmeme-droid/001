/**
 * Bot user-gateway client — server-to-server calls from the website to the
 * bot process's /gateway/* endpoints (web chat + manual trades), authenticated
 * with the shared WEB_GATEWAY_SECRET (>=32 chars, same value on both sides).
 *
 * The browser NEVER talks to the gateway directly: routes/chat.js and
 * routes/webtrade.js authenticate the user with JWT, look up their linked
 * telegram_id server-side, and forward here. Modeled on routes/insight.js.
 */

const http = require('http');
const https = require('https');

const BOT_GATEWAY_URL = (process.env.BOT_GATEWAY_URL || 'http://localhost:8080').replace(/\/+$/, '');
const GATEWAY_SECRET = process.env.WEB_GATEWAY_SECRET || '';

function isConfigured() {
  return GATEWAY_SECRET.length >= 32;
}

// How long to wait for the TCP CONNECTION, as opposed to the response.
//
// These are different failures and they deserve different patience. A chat
// reply legitimately takes many seconds — there is a model behind it. Getting
// a socket to the bot does not: on a reachable host it is milliseconds, and
// if it has not happened in a few seconds it is not going to.
//
// Sharing one long budget between them cost a diagnosis. An unreachable
// gateway (a firewall that DROPS rather than REJECTS, so packets vanish
// instead of bouncing) made the request hang for the full response timeout —
// longer than the CDN in front of this app was willing to wait. The visitor
// got the edge's opaque "error code: 502" instead of this app's
// {"error":"Chat unavailable"}, and the operator was left unable to tell
// "cannot reach the bot" from "the app is down". Failing the connect fast
// means our own honest error arrives first.
const CONNECT_TIMEOUT_MS = Number(process.env.GATEWAY_CONNECT_TIMEOUT_MS || 4000);

function requestJSON(method, gwPath, body, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const url = `${BOT_GATEWAY_URL}/gateway${gwPath}`;
    const mod = url.startsWith('https:') ? https : http;
    const payload = body === undefined ? null : JSON.stringify(body);
    const headers = { 'X-Gateway-Secret': GATEWAY_SECRET };
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(payload);
    }
    let connectTimer = null;
    const req = mod.request(url, { method, timeout: timeoutMs, headers }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data || '{}') }); }
        catch (e) { reject(new Error('Invalid JSON from gateway')); }
      });
    });
    // Fast-fail the CONNECT phase only. Cleared the moment a socket is
    // established, so a slow model reply is never cut short by it.
    req.on('socket', (sock) => {
      if (!CONNECT_TIMEOUT_MS) return;
      if (sock.connecting === false) return;          // already up (pooled)
      connectTimer = setTimeout(() => {
        req.destroy();
        const e = new Error('Gateway unreachable — no connection established');
        e.code = 'GATEWAY_UNREACHABLE';
        reject(e);
      }, CONNECT_TIMEOUT_MS);
      const clear = () => { if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; } };
      sock.once('connect', clear);
      sock.once('error', clear);
    });
    req.on('error', (e) => { if (connectTimer) clearTimeout(connectTimer); reject(e); });
    req.on('timeout', () => {
      if (connectTimer) clearTimeout(connectTimer);
      req.destroy();
      reject(new Error('Gateway timeout'));
    });
    if (payload) req.write(payload);
    req.end();
  });
}

// Binary sibling of requestJSON for the gateway's non-JSON endpoints (today:
// /share-card PNG). Collects raw Buffer chunks and never JSON.parses — routing
// a binary response through requestJSON/relay would corrupt it or throw.
function requestBinary(gwPath, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const url = `${BOT_GATEWAY_URL}/gateway${gwPath}`;
    const mod = url.startsWith('https:') ? https : http;
    const req = mod.request(url, {
      method: 'GET', timeout: timeoutMs,
      headers: { 'X-Gateway-Secret': GATEWAY_SECRET },
    }, (res) => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        contentType: res.headers['content-type'] || '',
        body: Buffer.concat(chunks),
      }));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Gateway timeout')); });
    req.end();
  });
}

// Forward a gateway response to the browser: pass 4xx through verbatim (the
// UI distinguishes chat_admin_only / not_proposer / live_not_enabled / ...),
// collapse 5xx to a 502.
function relay(res, r) {
  if (r.status >= 200 && r.status < 300) return res.json(r.data);
  if (r.status >= 400 && r.status < 500) return res.status(r.status).json(r.data);
  return res.status(502).json({ error: 'Bot gateway error' });
}

module.exports = {
  isConfigured,
  relay,
  postGateway: (p, b, t) => requestJSON('POST', p, b, t),
  getGateway: (p, t) => requestJSON('GET', p, undefined, t),
  getGatewayBinary: (p, t) => requestBinary(p, t),
};
