'use strict';
/**
 * `trust proxy: 1` is a hop COUNT, not a trust boundary.
 *
 * Express with a numeric setting takes the Nth-from-right X-Forwarded-For
 * entry and calls it req.ip, regardless of who connected. Reach the server
 * off-proxy — a misrouted port, a container published by accident, an attacker
 * already inside the network — and req.ip is attacker-chosen. It is the bucket
 * key for the failed-LOGIN limiter (auth.js:589), so rotating one header per
 * request buys a fresh lockout bucket every time.
 *
 * bot/utils/client_ip.py fixed this on the Python side and wrote the rule
 * down. The Node half never got it. These tests drive the express behaviour
 * end-to-end, because the whole defect is that the one-character config reads
 * as though it does the right thing.
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const { trustProxyFrom, isTrustedProxy, normalize } = require('../lib/trusted_proxy');

/** Start an express app with a given trust-proxy setting; return {port, close}. */
async function serve(trust) {
  const app = express();
  app.set('trust proxy', trust);
  app.get('/whoami', (req, res) => res.json({ ip: req.ip }));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return {
    port: server.address().port,
    close: () => new Promise((r) => server.close(r)),
  };
}

function get(port, headers) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port, path: '/whoami', headers }, (res) => {
        let b = '';
        res.on('data', (c) => { b += c; });
        res.on('end', () => resolve(JSON.parse(b)));
      });
    req.on('error', reject);
    req.end();
  });
}

// ── the defect, driven ─────────────────────────────────────────────────────

test('a spoofed X-Forwarded-For does NOT become req.ip when nothing is trusted',
  async () => {
    const s = await serve(trustProxyFrom(''));   // TRUSTED_PROXY unset
    try {
      const { ip } = await get(s.port, { 'X-Forwarded-For': '203.0.113.9' });
      assert.notStrictEqual(normalize(ip), '203.0.113.9',
        'req.ip is the value the caller typed into X-Forwarded-For. That is '
        + 'the bucket key for the failed-login rate limiter, so an attacker '
        + 'rotating the header gets an unlimited number of login attempts.');
      assert.strictEqual(normalize(ip), '127.0.0.1',
        'with no trusted hop, req.ip must be the peer — the only address TCP '
        + 'will vouch for');
    } finally { await s.close(); }
  });

test('the old `trust proxy: 1` setting is what accepting a spoof looks like',
  async () => {
    // Not a regression guard — a demonstration that the replaced setting was
    // genuinely unsafe, so nobody restores it believing it was equivalent.
    const s = await serve(1);
    try {
      const { ip } = await get(s.port, { 'X-Forwarded-For': '203.0.113.9' });
      assert.strictEqual(normalize(ip), '203.0.113.9',
        'if this ever stops holding, express changed its numeric trust-proxy '
        + 'semantics and this file needs rereading');
    } finally { await s.close(); }
  });

test('a spoofed header IS honoured when the peer is a configured hop',
  async () => {
    // The fix must not break the legitimate case: behind nginx, the real
    // client address only exists in the header, and a limiter that buckets
    // every visitor into the proxy's single address is useless.
    const s = await serve(trustProxyFrom('127.0.0.0/8'));
    try {
      const { ip } = await get(s.port, { 'X-Forwarded-For': '203.0.113.9' });
      assert.strictEqual(normalize(ip), '203.0.113.9',
        'the loopback peer is inside the configured 127.0.0.0/8, so the '
        + 'header it wrote is evidence');
    } finally { await s.close(); }
  });

test('a peer outside the configured range is not believed', async () => {
  const s = await serve(trustProxyFrom('172.28.0.0/16'));
  try {
    const { ip } = await get(s.port, { 'X-Forwarded-For': '203.0.113.9' });
    assert.strictEqual(normalize(ip), '127.0.0.1',
      'the peer (127.0.0.1) is not in 172.28.0.0/16, so its X-Forwarded-For '
      + 'is not evidence about anything');
  } finally { await s.close(); }
});

test('the rightmost-entry attack is refused too', async () => {
  // nginx sets `X-Forwarded-For $proxy_add_x_forwarded_for`, which APPENDS the
  // real peer on the right. An attacker who sends their own list is trying to
  // make one of their entries land wherever the server looks.
  const s = await serve(trustProxyFrom(''));
  try {
    const { ip } = await get(s.port, { 'X-Forwarded-For': '1.1.1.1, 2.2.2.2, 3.3.3.3' });
    assert.strictEqual(normalize(ip), '127.0.0.1');
  } finally { await s.close(); }
});

// ── the parser ─────────────────────────────────────────────────────────────

test('TRUSTED_PROXY accepts bare addresses and CIDRs, like the Python half', () => {
  assert.ok(isTrustedProxy('172.28.0.5', '172.28.0.0/16'));
  assert.ok(!isTrustedProxy('172.29.0.5', '172.28.0.0/16'));
  assert.ok(isTrustedProxy('10.0.0.1', '10.0.0.1'));
  assert.ok(!isTrustedProxy('10.0.0.2', '10.0.0.1'));
  assert.ok(isTrustedProxy('192.168.1.7', '10.0.0.0/8, 192.168.0.0/16'));
});

test('IPv4-mapped IPv6 peers resolve to the same trust answer', () => {
  // Node hands back ::ffff:172.28.0.5 on a dual-stack socket. Failing to
  // normalise it would silently distrust the real proxy and put every visitor
  // back in one bucket.
  assert.ok(isTrustedProxy('::ffff:172.28.0.5', '172.28.0.0/16'));
  assert.strictEqual(normalize('::ffff:127.0.0.1'), '127.0.0.1');
});

test('an unparseable entry is ignored and does not widen trust', () => {
  // A typo must not silently trust everything, and must not silently trust
  // nothing while looking configured.
  assert.ok(!isTrustedProxy('203.0.113.9', 'not-an-ip'));
  assert.ok(isTrustedProxy('10.0.0.1', 'not-an-ip, 10.0.0.0/8'),
    'one bad entry must not discard the good ones alongside it');
});

test('an empty or missing setting trusts nothing', () => {
  assert.strictEqual(trustProxyFrom(''), false);
  assert.strictEqual(trustProxyFrom(undefined), false);
  assert.strictEqual(trustProxyFrom('   ,  , '), false);
});

test('server.js does not use a numeric trust-proxy hop count', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  // Strip line and block comments: the comment above the fix quotes the old
  // setting, and a raw scan cannot tell a warning about it from the thing.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const m = code.match(/app\.set\(\s*['"]trust proxy['"]\s*,\s*([^)]+)\)/);
  assert.ok(m, 'server.js no longer sets trust proxy at all');
  assert.ok(!/^\s*\d+\s*$/.test(m[1]),
    `trust proxy is set to the hop count ${m[1].trim()} again. A count does not `
    + 'check who connected, so req.ip — which keys the failed-login limiter — '
    + 'becomes attacker-chosen off-proxy. Use trustProxyFrom(TRUSTED_PROXY).');
});
