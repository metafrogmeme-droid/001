'use strict';
/**
 * No web test listens on a port the OS may hand out.
 *
 * `node --test` runs these files in parallel. Three of them started a
 * stand-in bot gateway on a fixed port in Linux's ephemeral range (39877,
 * 39878, 39879). Any other file's `listen(0)` or outbound socket can be given
 * one of those first, and #546's CI lost that race:
 *
 *     Error: listen EADDRINUSE: address already in use 127.0.0.1:39878
 *
 * The file died before its first subtest, on a change that touched nothing
 * under app/. A test that wants a server asks for port 0 and reads the port
 * it was given. A fixed port below the range (`hardening.test.js` boots the
 * real server on 3311) is never handed to a `listen(0)`, so only the range is
 * refused.
 *
 * A scan, because no run can show the race on demand: it loses only when
 * another socket happens to draw the same number.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { codeOnly } = require('./helpers/code_only');

// Linux's default net.ipv4.ip_local_port_range. IANA's dynamic range starts
// at 49152, which this covers.
const EPHEMERAL_LOW = 32768;

// A port written as a number where a server listens or a URL names one.
const SHAPES = [
  /\blisten\(\s*(\d{2,5})\b/g,
  /\b[A-Z_]*PORT\s*=\s*(\d{2,5})\b/g,
  /\/\/(?:127\.0\.0\.1|localhost):(\d{2,5})\b/g,
];

function ephemeralPorts(src) {
  const code = codeOnly(src);
  const out = [];
  for (const re of SHAPES) {
    for (const m of code.matchAll(re)) {
      const port = Number(m[1]);
      if (port >= EPHEMERAL_LOW && port <= 65535) {
        const line = code.slice(0, m.index).split('\n').length;
        out.push(`${line}: ${m[0]}`);
      }
    }
  }
  return out;
}

test('the reading finds a fixed ephemeral port in each shape, and passes the rest', () => {
  assert.deepEqual(ephemeralPorts("server.listen(39878, '127.0.0.1');"), ["1: listen(39878"]);
  assert.deepEqual(ephemeralPorts('const GW_PORT = 39877;'), ['1: GW_PORT = 39877']);
  assert.deepEqual(ephemeralPorts("const u = 'http://127.0.0.1:40000/x';"), ['1: //127.0.0.1:40000']);
  assert.deepEqual(ephemeralPorts("server.listen(0, '127.0.0.1');"), []);
  assert.deepEqual(ephemeralPorts('const PORT = 3311;'), []);
  assert.deepEqual(ephemeralPorts("const u = 'http://localhost:8080';"), []);
  // A comment that quotes the old line is not the code doing it.
  assert.deepEqual(ephemeralPorts('// was: const GW_PORT = 39878;'), []);
});

test('no web test listens on, or points a URL at, a fixed ephemeral port', () => {
  const dir = __dirname;
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js'));
  assert.ok(files.length > 100, 'expected the web suite');
  const self = path.basename(__filename);
  const hits = [];
  for (const f of files) {
    if (f === self) continue;  // its own planted examples
    for (const h of ephemeralPorts(fs.readFileSync(path.join(dir, f), 'utf8'))) {
      hits.push(`${f}:${h}`);
    }
  }
  assert.deepEqual(hits, [], 'listen on port 0 and read the port it was given:\n  ' + hits.join('\n  '));
});
