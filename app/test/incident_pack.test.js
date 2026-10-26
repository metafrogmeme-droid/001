'use strict';

/**
 * Incident pack. Off unless INCIDENT_PACK_ENABLED is 1 or true.
 * While it is off, an operator gets a preview and everyone else gets 404.
 * When it is on, the same route answers. The runner stands in for the
 * chain read. Nothing here signs.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.INCIDENT_PACK_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { flagOn, FLAG } = require('../lib/incident_pack_flag');
const route = require('../routes/incident_pack');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const OPERATOR = 96111;
const STRANGER = 96112;
const ADDRESS = '0x' + '86'.repeat(20);
if (!pool.users.some((u) => u.id === OPERATOR)) {
  pool.users.push({ id: OPERATOR, email: 'pack-op@example.com', plan: 'admin', token_epoch: 0 });
}
if (!pool.users.some((u) => u.id === STRANGER)) {
  pool.users.push({ id: STRANGER, email: 'pack-free@example.com', plan: 'free', token_epoch: 0 });
}
const OP = jwt.sign({ user_id: OPERATOR, email: 'pack-op@example.com', epoch: 0 }, process.env.JWT_SECRET);
const FREE = jwt.sign({ user_id: STRANGER, email: 'pack-free@example.com', epoch: 0 }, process.env.JWT_SECRET);

function app() {
  const server = express();
  server.use('/api/incident-pack', route);
  return server;
}

function listen(server) {
  return new Promise((resolve) => {
    const httpServer = server.listen(0, '127.0.0.1', () => resolve(httpServer));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function post(httpServer, token, body) {
  const data = Buffer.from(JSON.stringify(body || {}));
  return new Promise((resolve, reject) => {
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': data.length,
    };
    if (token) headers.authorization = 'Bearer ' + token;
    const req = http.request({
      host: '127.0.0.1', port: httpServer.address().port, path: '/api/incident-pack',
      method: 'POST', headers,
    }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => {
        let parsed = b;
        try { parsed = JSON.parse(b); } catch (err) { parsed = b; }
        resolve({ status: res.statusCode, body: parsed, cache: res.headers['cache-control'] });
      });
    });
    req.on('error', reject);
    req.end(data);
  });
}

const ASK = {
  address: ADDRESS,
  start: '2023-10-31T15:00:00Z',
  end: '2023-10-31T17:00:00Z',
  chain: 'local',
};

function packed() {
  return {
    ok: true,
    readable: true,
    verify: true,
    chain: 'local',
    chain_id: 31337,
    events: 4,
    seal: 'ab',
    findings: ['Address currently has an EIP-7702 delegation to 0x89.'],
    timeline_md: '# Incident timeline\n',
    checklist_md: 'Revoke remaining approvals. Move any remaining assets.',
    later_md: 'More templates will come later.',
    pack: { schema: 'claw-incident-pack/0.1', chain_id: 31337 },
  };
}

test('only 1 and true turn the flag on', () => {
  assert.equal(FLAG, 'INCIDENT_PACK_ENABLED');
  assert.equal(flagOn({}), false);
  assert.equal(flagOn({ INCIDENT_PACK_ENABLED: 'yes' }), false);
  assert.equal(flagOn({ INCIDENT_PACK_ENABLED: 'on' }), false);
  assert.equal(flagOn({ INCIDENT_PACK_ENABLED: '0' }), false);
  assert.equal(flagOn({ INCIDENT_PACK_ENABLED: ' 1 ' }), true);
  assert.equal(flagOn({ INCIDENT_PACK_ENABLED: 'TRUE' }), true);
  const src = codeOnly(fs.readFileSync(path.join(APP, 'lib', 'incident_pack_flag.js'), 'utf8'));
  assert.equal(src.includes('function enabled'), false);
});

test('the route is authenticated and does not sign', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'incident_pack.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /accessFor/);
  assert.equal(src.includes('signTransaction'), false);
  assert.equal(src.includes('eth_sendRawTransaction'), false);
  assert.equal(src.includes('ETH_PRIVATE_KEY'), false);
  const serverSrc = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const at = serverSrc.indexOf("app.use('/api/incident-pack', require('./routes/incident_pack'))");
  const wide = serverSrc.indexOf("express.json({ limit: '1mb' })");
  assert.ok(at > 0 && wide > at, 'the pack route must run before the 1mb parser');
});

test('while the flag is off only an operator previews, and the runner stays idle otherwise', async () => {
  const calls = [];
  route.setRunner(async (body) => {
    calls.push(body);
    return packed();
  });
  delete process.env.INCIDENT_PACK_ENABLED;
  const server = await listen(app());
  try {
    const anon = await post(server, null, ASK);
    assert.equal(anon.status, 404);
    const stranger = await post(server, FREE, ASK);
    assert.equal(stranger.status, 404);
    assert.equal(calls.length, 0);
    const preview = await post(server, OP, ASK);
    assert.equal(preview.status, 200);
    assert.equal(preview.body.preview, true);
    assert.match(preview.body.banner, /Admin preview/);
    assert.equal(preview.body.chain_id, 31337);
    assert.match(preview.body.checklist_md, /Revoke/);
    assert.match(preview.body.later_md, /More templates will come later/);
    assert.equal(preview.cache, 'private, no-store');
    assert.deepEqual(calls, [ASK]);
  } finally {
    route.setRunner(null);
    await close(server);
  }
});

test('when the flag is on a non-operator is admitted and it is not a preview', async () => {
  const calls = [];
  route.setRunner(async () => {
    calls.push(1);
    return packed();
  });
  process.env.INCIDENT_PACK_ENABLED = '1';
  const server = await listen(app());
  try {
    const anon = await post(server, null, ASK);
    assert.equal(anon.status, 200);
    assert.equal(anon.body.preview, false);
    assert.equal(anon.body.banner, null);
    const stranger = await post(server, FREE, ASK);
    assert.equal(stranger.status, 200);
    assert.equal(stranger.body.preview, false);
    assert.equal(calls.length, 2);
  } finally {
    delete process.env.INCIDENT_PACK_ENABLED;
    route.setRunner(null);
    await close(server);
  }
});

test('a refused chain is 400 and an unreadable source is 503, with no event count', async () => {
  process.env.INCIDENT_PACK_ENABLED = 'true';
  route.setRunner(async (body) => {
    if (body.chain === 'eth') {
      return { ok: false, readable: false, refused: 'chain', error: 'IncidentChainRefused' };
    }
    return { ok: false, readable: false, error: 'IncidentSourceUnreadable' };
  });
  const server = await listen(app());
  try {
    const chain = await post(server, null, Object.assign({}, ASK, { chain: 'eth' }));
    assert.equal(chain.status, 400);
    assert.equal(chain.body.error, 'IncidentChainRefused');
    assert.equal(chain.body.readable, false);
    assert.equal(Object.prototype.hasOwnProperty.call(chain.body, 'events'), false);
    const down = await post(server, null, ASK);
    assert.equal(down.status, 503);
    assert.equal(down.body.readable, false);
    assert.equal(down.body.error, 'IncidentSourceUnreadable');
    assert.equal(Object.prototype.hasOwnProperty.call(down.body, 'events'), false);
  } finally {
    delete process.env.INCIDENT_PACK_ENABLED;
    route.setRunner(null);
    await close(server);
  }
});
