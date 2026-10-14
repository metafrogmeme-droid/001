/**
 * Real-time push (SSE) for the dashboard.
 *
 * The dashboard previously only learned about new scans/trades/signals by
 * polling on a timer (5-60s). This gives connected clients an immediate
 * "something changed, go re-fetch" nudge over one long-lived HTTP response.
 * Most events are a bare "refresh now". One is not: `activity` carries the
 * mind-stream event itself, and a thesis, an open or a stop move names a
 * live call's side and levels. A live call is a signed-in member's
 * (lib/signal_access.js), so each connection records at connect whether a
 * member opened it, and an event the sender gave a visitors' form is written
 * in that form to everyone else. The answer is read once, at connect: a
 * session that ends mid-stream keeps what it was shown until the browser
 * reconnects, which EventSource does on its own.
 */

const express = require('express');
const { signedIn } = require('../lib/signal_access');

// Lazy, as routes/sync.js does: auth.js requires the database layer, and this
// module is required by the sync route that the database layer's tests load.
const optionalAuth = (req, res, next) => require('../auth').optionalAuth(req, res, next);

const router = express.Router();

const HEARTBEAT_MS = 25000;
// Bound total concurrent connections so a slow client leak (or a burst of
// tabs) can't exhaust server file descriptors.
const MAX_CLIENTS = 500;
// ...and bound them PER CLIENT too. MAX_CLIENTS alone stops fd exhaustion and
// does nothing about monopolisation: one caller opening 500 streams takes every
// slot and every other visitor gets a 503. A global cap answers "can the server
// survive this?"; it does not answer "can anyone else still use it?".
const MAX_PER_IP = 12;

const clients = new Set();
const perIp = new Map();   // key -> count

function ipKey(req) {
  return req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
}

// One place that forgets a connection, so the two paths cannot disagree. They
// did: broadcast() deleted a client whose write threw, and the heartbeat
// swallowed the same error without deleting — so a socket that died between
// broadcasts stayed in the set, holding its per-IP slot, until 'close' fired.
function drop(res) {
  if (!clients.delete(res)) return;
  const k = res.__rcIpKey;
  const n = (perIp.get(k) || 1) - 1;
  if (n <= 0) perIp.delete(k); else perIp.set(k, n);
}

/**
 * Write one event to every connection. `opts.forVisitors`, when given, is the
 * event as a visitor may see it, and goes to every connection a member did
 * not open; without it every connection gets `data`.
 */
function broadcast(type, data, opts) {
  if (clients.size === 0) return;
  const frame = (d) => `event: ${type}\ndata: ${JSON.stringify(d || {})}\n\n`;
  const member = frame(data);
  const visitor = opts && Object.prototype.hasOwnProperty.call(opts, 'forVisitors')
    ? frame(opts.forVisitors) : member;
  for (const res of clients) {
    try { res.write(res.__rcMember === true ? member : visitor); } catch (e) { drop(res); }
  }
}

router.get('/', optionalAuth, (req, res) => {
  if (clients.size >= MAX_CLIENTS) {
    return res.status(503).end();
  }
  const key = ipKey(req);
  if ((perIp.get(key) || 0) >= MAX_PER_IP) {
    // 429, not 503: this caller has too many streams open, which is a
    // different fact from the server being full, and only one of the two is
    // the caller's to fix.
    return res.status(429).end();
  }
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // disable nginx response buffering
  res.flushHeaders?.();
  res.write(': connected\n\n');

  res.__rcIpKey = key;
  res.__rcMember = signedIn(req);
  clients.add(res);
  perIp.set(key, (perIp.get(key) || 0) + 1);

  const hb = setInterval(() => {
    // A failed heartbeat write means the socket is gone. Forget it here rather
    // than waiting for 'close', which may never arrive on a half-open TCP
    // connection — that wait is what let dead entries hold slots.
    try { res.write(': ping\n\n'); } catch (e) { clearInterval(hb); drop(res); }
  }, HEARTBEAT_MS);

  req.on('close', () => {
    clearInterval(hb);
    drop(res);
  });
});

module.exports = { router, broadcast, MAX_CLIENTS, MAX_PER_IP };
