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

/**
 * Arm the two timers every gateway request needs, and return a disarm().
 *
 * CONNECT is the fast-fail from above: a socket, or nothing, in a few seconds.
 *
 * DEADLINE is an absolute cap from the moment the request starts, and it
 * exists because `timeout:` on http.request is NOT one. Node's socket timeout
 * measures INACTIVITY: every byte that arrives resets it. A gateway that
 * sends headers and then trickles — or a proxy that keeps the connection
 * warm — holds the request open indefinitely while never being idle long
 * enough to trip. The caller asked for a 20s budget and could wait forever.
 *
 * This is the same shape as the engine's hung analyze phase earlier today: a
 * bound that only fires on one failure mode is not a bound. Retrying,
 * catching, and resetting all share the property that they only help if the
 * thing can finish. So the inactivity timeout stays (it still catches a
 * stalled socket sooner) and an absolute deadline sits over it.
 *
 * It matters here specifically because there is a CDN in front of this app
 * with its own patience. Whenever our budget can exceed the edge's, the
 * visitor stops getting our honest {"error": ...} and starts getting the
 * edge's opaque 502 — which is exactly how tonight's chat outage presented.
 */
function armTimers(req, timeoutMs, reject) {
  let connectTimer = null;
  let deadlineTimer = null;
  let settled = false;

  const disarm = () => {
    if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; }
    if (deadlineTimer) { clearTimeout(deadlineTimer); deadlineTimer = null; }
  };
  // One rejection, one destroy. Without this a deadline firing after a
  // resolve would tear down a socket whose response we already returned.
  const fail = (err) => {
    if (settled) return;
    settled = true;
    disarm();
    req.destroy();
    reject(err);
  };

  if (timeoutMs > 0) {
    deadlineTimer = setTimeout(() => {
      const e = new Error('Gateway timeout');
      e.code = 'GATEWAY_DEADLINE';
      fail(e);
    }, timeoutMs);
  }

  // Fast-fail the CONNECT phase only. Cleared the moment a socket is
  // established, so a slow model reply is never cut short by it.
  req.on('socket', (sock) => {
    if (!CONNECT_TIMEOUT_MS) return;
    if (sock.connecting === false) return;          // already up (pooled)
    connectTimer = setTimeout(() => {
      const e = new Error('Gateway unreachable — no connection established');
      e.code = 'GATEWAY_UNREACHABLE';
      fail(e);
    }, CONNECT_TIMEOUT_MS);
    const clear = () => { if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; } };
    sock.once('connect', clear);
    sock.once('error', clear);
  });
  req.on('error', (e) => fail(e));
  req.on('timeout', () => fail(new Error('Gateway timeout')));

  return {
    // Called on a successful response so a late timer cannot fire.
    done: () => { settled = true; disarm(); },
    settled: () => settled,
  };
}

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
    const req = mod.request(url, { method, timeout: timeoutMs, headers }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        if (timers.settled()) return;               // deadline already fired
        timers.done();
        try { resolve({ status: res.statusCode, data: JSON.parse(data || '{}') }); }
        catch (e) { reject(new Error('Invalid JSON from gateway')); }
      });
    });
    const timers = armTimers(req, timeoutMs, reject);
    if (payload) req.write(payload);
    req.end();
  });
}

// Binary sibling of requestJSON for the gateway's non-JSON endpoints (today:
// /share-card PNG). Collects raw Buffer chunks and never JSON.parses — routing
// a binary response through requestJSON/relay would corrupt it or throw.
//
// It shares armTimers with requestJSON deliberately. The connect fast-fail
// was added to requestJSON alone, so this sibling kept the exact bug that
// change existed to kill: against a firewall that DROPS packets it waited the
// full response budget for a connection that was never coming — past the
// edge's patience, so the visitor got a 502 instead of our error. Two copies
// of a request path is how one of them stays broken.
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
      res.on('end', () => {
        if (timers.settled()) return;
        timers.done();
        resolve({
          status: res.statusCode,
          contentType: res.headers['content-type'] || '',
          body: Buffer.concat(chunks),
        });
      });
    });
    const timers = armTimers(req, timeoutMs, reject);
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

// ── Streaming (SSE) relay ─────────────────────────────────────────────────
//
// The bot gateway's /chat/stream answers as text/event-stream: `delta` frames
// as the model produces text, `tool` frames while it reads something, and one
// `final` frame carrying exactly the JSON the non-streaming route would have
// returned. This relays the frames byte-for-byte, so the browser reads one
// protocol whether the answer came from the model, an intercept, or a
// refusal — and never JSON.parses a stream mid-way.
//
// Same armTimers as requestJSON: connect fast-fail and an ABSOLUTE deadline.
// The deadline matters more here, not less — a stream that keeps trickling
// is precisely the shape the inactivity timeout cannot end.

function sseFrame(event, obj) {
  return `event: ${event}\ndata: ${JSON.stringify(obj)}\n\n`;
}

function sseHead(res) {
  if (res.headersSent) return;
  res.status(200);
  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('X-Accel-Buffering', 'no');
  if (typeof res.flushHeaders === 'function') res.flushHeaders();
}

// One `final` frame and done — for an answer decided locally (an intercept
// hit, a refusal) on the streaming route, so the client's reader needs no
// second code path.
function sseFinal(res, status, body) {
  sseHead(res);
  res.write(sseFrame('final', { status, body }));
  res.end();
}

function relayStream(method, gwPath, body, res, timeoutMs = 60000) {
  return new Promise((resolve) => {
    const url = `${BOT_GATEWAY_URL}/gateway${gwPath}`;
    const mod = url.startsWith('https:') ? https : http;
    const payload = body === undefined ? null : JSON.stringify(body);
    const headers = { 'X-Gateway-Secret': GATEWAY_SECRET, Accept: 'text/event-stream' };
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(payload);
    }
    let finished = false;
    const finish = (fn) => {
      if (finished) return;
      finished = true;
      try { fn(); } catch (e) { /* the browser may already be gone */ }
      resolve();
    };
    const req = mod.request(url, { method, timeout: timeoutMs, headers }, (up) => {
      const ct = String(up.headers['content-type'] || '');
      const streaming = up.statusCode >= 200 && up.statusCode < 300 && ct.includes('text/event-stream');
      if (!streaming) {
        // A plain JSON answer (a 4xx, or a gateway without the stream route
        // yet): collect it and hand it over as the one `final` frame.
        let data = '';
        up.on('data', (d) => data += d);
        up.on('end', () => finish(() => {
          timers.done();
          let parsed;
          try { parsed = JSON.parse(data || '{}'); } catch (e) { parsed = { error: 'Bot gateway error' }; }
          const status = up.statusCode >= 500 ? 502 : up.statusCode;
          sseFinal(res, status, status === 502 ? { error: 'Bot gateway error' } : parsed);
        }));
        return;
      }
      sseHead(res);
      up.on('data', (chunk) => { try { res.write(chunk); } catch (e) { /* closed */ } });
      up.on('end', () => finish(() => { timers.done(); res.end(); }));
      up.on('error', () => finish(() => {
        res.write(sseFrame('error', { error: 'Bot gateway error' }));
        res.end();
      }));
    });
    const timers = armTimers(req, timeoutMs, (err) => finish(() => {
      if (!res.headersSent) {
        res.status(502).json({ error: 'Chat unavailable' });
      } else {
        res.write(sseFrame('error', { error: err && err.code === 'GATEWAY_DEADLINE'
          ? 'Timed out waiting for the bot' : 'Chat unavailable' }));
        res.end();
      }
    }));
    // The browser went away: stop the upstream turn rather than finishing it
    // for nobody.
    res.on('close', () => { if (!finished) { finished = true; timers.done(); req.destroy(); resolve(); } });
    if (payload) req.write(payload);
    req.end();
  });
}

module.exports = {
  isConfigured,
  relay,
  sseFinal,
  sseFrame,
  postGateway: (p, b, t) => requestJSON('POST', p, b, t),
  getGateway: (p, t) => requestJSON('GET', p, undefined, t),
  getGatewayBinary: (p, t) => requestBinary(p, t),
  postGatewayStream: (p, b, res, t) => relayStream('POST', p, b, res, t),
};
