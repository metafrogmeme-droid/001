'use strict';
/**
 * Post-quantum TLS, checked against real handshakes (app/lib/pq_tls.js and
 * app/scripts/pq_tls_check.js): local TLS servers, one that negotiates
 * X25519MLKEM768 and one that knows only classical groups, a closed port, and
 * a server that accepts the connection and never answers.
 *
 *   pq            the hybrid was chosen
 *   classical     a classical group was chosen, the hybrid offered first
 *   unknown       no handshake, with the error's code and never its message
 *   cannot_check  the local library cannot offer the hybrid: no verdict
 * and the exit status the operator reads: 0, 1, or 3 for "not a verdict".
 *
 * The certificate is made for the run by the openssl command line and lives
 * in a temporary directory: no key is committed.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const tls = require('node:tls');
const { execFileSync } = require('node:child_process');

const pq = require('../lib/pq_tls');
const cli = require('../scripts/pq_tls_check');

let dir = null;
let cert = null;
let key = null;
let rsaCert = null;
let rsaKey = null;
try {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pq-tls-'));
  execFileSync('openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes',
    '-keyout', path.join(dir, 'k.pem'), '-out', path.join(dir, 'c.pem'), '-days', '1', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost'], { stdio: 'ignore' });
  cert = fs.readFileSync(path.join(dir, 'c.pem'));
  key = fs.readFileSync(path.join(dir, 'k.pem'));
  // An RSA certificate too: a TLS 1.2 server may transport its key with RSA
  // and agree no ephemeral group at all.
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', path.join(dir, 'rk.pem'),
    '-out', path.join(dir, 'rc.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], { stdio: 'ignore' });
  rsaCert = fs.readFileSync(path.join(dir, 'rc.pem'));
  rsaKey = fs.readFileSync(path.join(dir, 'rk.pem'));
} catch (e) { cert = null; }
const SKIP = !cert ? 'the openssl command line could not make a test certificate'
  : !pq.canOffer() ? `this Node's TLS library cannot offer ${pq.PQ_GROUP}` : null;

const servers = [];
function serveTls(groups) {
  return new Promise((resolve) => {
    const s = tls.createServer({ key, cert, ecdhCurve: groups, minVersion: 'TLSv1.2' }, (c) => c.end());
    s.listen(0, '127.0.0.1', () => { servers.push(s); resolve(s.address().port); });
  });
}
function serveSilent() {
  return new Promise((resolve) => {
    const socks = [];
    const s = net.createServer((c) => { socks.push(c); });
    s.on('close', () => socks.forEach((c) => c.destroy()));
    s.listen(0, '127.0.0.1', () => { servers.push({ close: () => { socks.forEach((c) => c.destroy()); s.close(); } }); resolve(s.address().port); });
  });
}
async function closedPort() {
  const s = net.createServer();
  await new Promise((r) => s.listen(0, '127.0.0.1', r));
  const port = s.address().port;
  await new Promise((r) => s.close(r));
  return port;
}
const local = { servername: 'localhost' };
test.after(() => { servers.forEach((s) => s.close()); if (dir) fs.rmSync(dir, { recursive: true, force: true }); });

test('the hybrid is pq, a classical server is classical, and the group is the one negotiated', SKIP ? { skip: SKIP } : {}, async () => {
  const hybrid = await serveTls(`${pq.PQ_GROUP}:X25519`);
  const classical = await serveTls('X25519:P-256');
  const a = await pq.checkHost('127.0.0.1', hybrid, { ...local, ca: cert });
  assert.deepStrictEqual([a.verdict, a.group, a.protocol], ['pq', pq.PQ_GROUP, 'TLSv1.3']);
  const b = await pq.checkHost('127.0.0.1', classical, { ...local, ca: cert });
  assert.deepStrictEqual([b.verdict, b.group], ['classical', 'X25519'], 'offered the hybrid first, and given X25519');
  // A server that knows the hybrid but prefers X25519 gives browsers X25519.
  const prefers = await serveTls(`X25519:${pq.PQ_GROUP}`);
  const c = await pq.checkHost('127.0.0.1', prefers, { ...local, ca: cert });
  assert.ok(['pq', 'classical'].includes(c.verdict) && c.group, 'whatever it chose is reported as chosen');
  assert.strictEqual(c.verdict === 'pq', c.group === pq.PQ_GROUP);
});

test('a TLS 1.2 handshake with no named group is classical, not unreadable', SKIP ? { skip: SKIP } : {}, async () => {
  const port = await new Promise((resolve) => {
    const srv = tls.createServer({ key: rsaKey, cert: rsaCert, maxVersion: 'TLSv1.2', ciphers: 'AES128-GCM-SHA256' }, (c) => c.end());
    srv.listen(0, '127.0.0.1', () => { servers.push(srv); resolve(srv.address().port); });
  });
  const r = await pq.checkHost('127.0.0.1', port, { ...local, ca: rsaCert });
  assert.deepStrictEqual([r.verdict, r.group, r.protocol], ['classical', 'no ephemeral key exchange', 'TLSv1.2']);
});

test('no handshake is unknown, by its code, never "not post-quantum"', SKIP ? { skip: SKIP } : {}, async () => {
  const shut = await pq.checkHost('127.0.0.1', await closedPort(), local);
  assert.deepStrictEqual([shut.verdict, shut.reason], ['unknown', 'ECONNREFUSED']);
  const silent = await pq.checkHost('127.0.0.1', await serveSilent(), { ...local, timeoutMs: 300 });
  assert.deepStrictEqual([silent.verdict, silent.reason], ['unknown', 'no handshake within 300 ms']);
  // An untrusted certificate fails TLS: a failure to check, not a classical host.
  const hybrid = await serveTls(pq.PQ_GROUP);
  const untrusted = await pq.checkHost('127.0.0.1', hybrid, local);
  assert.strictEqual(untrusted.verdict, 'unknown');
  assert.match(untrusted.reason, /^[A-Z_]+$/, 'the error code, never its message');
  // A library that cannot offer the hybrid measures nothing about the host.
  const lib = await pq.checkHost('127.0.0.1', hybrid, { ...local, ca: cert, canOffer: () => false });
  assert.strictEqual(lib.verdict, 'cannot_check');
});

test('the exit status: 0 all pq, 1 any classical, 3 anything unchecked or nothing checked', () => {
  const r = (verdict) => ({ verdict });
  assert.strictEqual(pq.exitCode([r('pq'), r('pq')]), 0);
  assert.strictEqual(pq.exitCode([r('pq'), r('classical')]), 1);
  assert.strictEqual(pq.exitCode([r('unknown'), r('classical')]), 1, 'a finding stands whatever else was unread');
  assert.strictEqual(pq.exitCode([r('pq'), r('unknown')]), 3);
  assert.strictEqual(pq.exitCode([r('pq'), r('cannot_check')]), 3);
  assert.strictEqual(pq.exitCode([]), 3, 'nothing checked is not a pass');
});

test('the deploy\'s hosts come from the variables that already name them', () => {
  assert.deepStrictEqual(pq.deployHosts({}), [
    { host: 'humanoid-traders.com', port: 443 }, { host: 'www.humanoid-traders.com', port: 443 }]);
  assert.deepStrictEqual(pq.deployHosts({ WEB_URL: 'https://www.example.org', MEMECLAW_HOSTS: 'memeclaw.fun, www.memeclaw.fun',
    BOT_GATEWAY_URL: 'https://gw.example.org:8443/gateway' }).map((h) => `${h.host}:${h.port}`),
  ['www.example.org:443', 'example.org:443', 'memeclaw.fun:443', 'www.memeclaw.fun:443', 'gw.example.org:8443']);
  assert.deepStrictEqual(pq.deployHosts({ WEB_URL: 'https://a.test', BOT_GATEWAY_URL: 'http://127.0.0.1:8080' }).map((h) => h.host),
    ['a.test', 'www.a.test'], 'a loopback http gateway is not a TLS host');
});

test('the script prints a line per host and exits as verify_deploy.sh does', SKIP ? { skip: SKIP } : {}, async () => {
  const hybrid = await serveTls(`${pq.PQ_GROUP}:X25519`);
  const classical = await serveTls('X25519');
  const lines = [];
  const log = (s) => lines.push(s);
  // The script verifies against the system's roots; the test server's own CA
  // is handed to the same check the script runs.
  const check = (h, p) => pq.checkHost(h, p, { servername: 'localhost', ca: cert });
  assert.strictEqual(await cli.main([`localhost:${hybrid}`], {}, log, check), 0);
  assert.match(lines.join('\n'), new RegExp(`^PQ        localhost:${hybrid}  X25519MLKEM768 \\(TLSv1\\.3\\)$`, 'm'));
  lines.length = 0;
  assert.strictEqual(await cli.main([`localhost:${hybrid}`, `localhost:${classical}`], {}, log, check), 1);
  assert.match(lines.join('\n'), /CLASSICAL localhost:\d+  X25519 \(TLSv1\.3\): not post-quantum/);
  lines.length = 0;
  assert.strictEqual(await cli.main([`localhost:${await closedPort()}`, '--json'], {}, log, check), 3);
  const out = JSON.parse(lines[0]);
  assert.deepStrictEqual([out.exit, out.results[0].verdict], [3, 'unknown']);
  lines.length = 0;
  assert.strictEqual(await cli.main(['not a host!'], {}, log, check), 3);
  assert.match(lines[0], /^not a host: not a host!$/);
  lines.length = 0;
  // No arguments: the deploy's hosts, from the environment it is given.
  const seen = [];
  await cli.main([], { WEB_URL: 'https://a.test', MEMECLAW_HOSTS: 'b.test' }, log, async (h, p) => { seen.push(`${h}:${p}`); return { host: h, port: p, verdict: 'pq', group: pq.PQ_GROUP }; });
  assert.deepStrictEqual(seen, ['a.test:443', 'www.a.test:443', 'b.test:443']);
  assert.deepStrictEqual(cli.parseTarget('https://www.humanoid-traders.com/duel'), { host: 'www.humanoid-traders.com', port: 443 });
  assert.strictEqual(cli.parseTarget('a b'), null);
});
