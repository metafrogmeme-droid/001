'use strict';
/**
 * Panic Button v0, web half.
 *
 * The flag off answers "not available yet". Prepared transactions decode,
 * with our own decoder, to the one revoke they name, with no value and no
 * other call. The route does not keep the address.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const { ethers } = require('ethers');

const flag = require('../lib/panic_flag');
const tx = require('../lib/panic_tx');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
delete process.env.PANIC_BUTTON;
delete process.env.CLAW_GUARD_V01;

const TOKEN = '0x' + 'aa'.repeat(20);
const SPENDER = '0x' + 'b2'.repeat(20);
const WALLET = '0x' + '11'.repeat(20);

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

test('the flag is off unless it is 1 or true', () => {
  assert.equal(flag.enabled({}), false);
  assert.equal(flag.enabled({ PANIC_BUTTON: '0' }), false);
  assert.equal(flag.enabled({ PANIC_BUTTON: 'false' }), false);
  assert.equal(flag.enabled({ PANIC_BUTTON: 'yes' }), false);
  assert.equal(flag.enabled({ PANIC_BUTTON: '1' }), true);
  assert.equal(flag.enabled({ PANIC_BUTTON: 'true' }), true);
  assert.equal(flag.enabled({ PANIC_BUTTON: 'TRUE' }), true);
  assert.equal(flag.NOT_AVAILABLE, 'not available yet');
  assert.ok(!flag.unavailableHtml().includes('seed phrase'));
});

test('each prepared revoke decodes to that call alone, with no value', () => {
  const erc20 = tx.buildRevoke({ kind: 'erc20', chain_id: 1, token: TOKEN, spender: SPENDER });
  const forall = tx.buildRevoke({ kind: 'approval_for_all', chain_id: 8453, token: TOKEN, spender: SPENDER });
  const permit = tx.buildRevoke({ kind: 'permit2', chain_id: 42161, token: TOKEN, spender: SPENDER });
  const clear = tx.buildRevoke({ kind: 'delegation_7702', chain_id: 1, token: '', spender: SPENDER });
  const nft = tx.buildRevoke({ kind: 'erc721_single', chain_id: 1, token: TOKEN, spender: SPENDER, nft_id: '7' });

  for (const prepared of [erc20, forall, permit, clear, nft]) {
    assert.equal(prepared.value, '0x0');
  }
  assert.equal(clear.data, '0x');

  const approve = tx.decodePrepared(erc20);
  assert.equal(approve.actions.length, 1);
  assert.equal(approve.actions[0].tid, 'txr.a_approve');
  assert.equal(approve.actions[0].params.amount, '0');
  assert.equal(approve.actions[0].params.spender, SPENDER);
  assert.deepEqual(approve.flags, []);

  const off = tx.decodePrepared(forall);
  assert.equal(off.actions.length, 1);
  assert.equal(off.actions[0].tid, 'txr.a_forall_off');
  assert.equal(off.actions[0].params.operator, SPENDER);
  assert.deepEqual(off.flags, []);

  const lock = tx.decodePrepared(permit);
  assert.equal(lock.actions.length, 1);
  assert.equal(lock.actions[0].tid, 'txr.a_lockdown');
  assert.equal(lock.actions[0].params.n, 1);
  assert.deepEqual(lock.actions[0].params.pairs, [{ token: TOKEN, spender: SPENDER }]);
  assert.equal(permit.to.toLowerCase(), tx.PERMIT2.toLowerCase());
  assert.notEqual(lock.unknown, true);

  const auth = tx.decodePrepared(clear);
  assert.equal(auth.actions.length, 1);
  assert.equal(auth.actions[0].tid, 'sgr.a_7702_clear');
  assert.deepEqual(auth.flags, []);

  const one = tx.decodePrepared(nft);
  assert.equal(one.actions.length, 1);
  assert.equal(one.actions[0].tid, 'txr.a_approve');
  assert.equal(one.actions[0].params.spender, tx.ZERO);
  assert.equal(one.actions[0].params.amount, '7');

  const iface = new ethers.Interface([
    'function lockdown((address token, address spender)[])',
    'function setApprovalForAll(address,bool)',
    'function approve(address,uint256)',
  ]);
  assert.equal(
    tx.encodeLockdown([{ token: TOKEN, spender: SPENDER }]),
    iface.encodeFunctionData('lockdown', [[[TOKEN, SPENDER]]]),
  );
  assert.equal(forall.data, iface.encodeFunctionData('setApprovalForAll', [SPENDER, false]));
  assert.equal(erc20.data, iface.encodeFunctionData('approve', [SPENDER, 0]));
});

test('the page and the API say not available yet while the flag is off', async () => {
  const calls = [];
  const route = require('../routes/panic');
  route.setRunner(async (body) => { calls.push(body); return { available: true, items: [] }; });
  const app = express();
  app.use(express.json());
  app.use((req, res, next) => {
    if (req.path !== '/panic' && req.path !== '/panic.html') return next();
    if (flag.enabled()) return next();
    res.setHeader('Cache-Control', 'no-store');
    res.type('html').send(flag.unavailableHtml());
  });
  app.use('/api/panic', route);
  app.get('/panic', (req, res) => {
    res.sendFile(path.join(APP, 'public', 'panic.html'));
  });
  const server = await listen(app);
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const page = await fetch(base + '/panic');
    const html = await page.text();
    assert.equal(page.status, 200);
    assert.ok(html.includes('not available yet'));
    assert.ok(!html.includes('seed phrase'));
    const file = await fetch(base + '/panic.html');
    assert.ok((await file.text()).includes('not available yet'));
    const api = await fetch(base + '/api/panic', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: WALLET }),
    });
    assert.equal(await api.text(), 'not available yet');
    assert.equal(calls.length, 0);
  } finally {
    route.setRunner(null);
    server.close();
  }
});

test('a flagged read returns the runner order with revokes and keeps no address', async () => {
  process.env.PANIC_BUTTON = '1';
  const seen = [];
  const route = require('../routes/panic');
  route.setRunner(async (body) => {
    if (body.op === 'receipt') {
      return { cleaned_at: '2026-10-23T00:00:00Z', hashes: body.hashes };
    }
    seen.push(body.address);
    if (body.address !== WALLET) {
      return { available: true, items: [], unread_chains: ['Arbitrum'], exploit_list_read: true,
        warning: 'If you entered your seed phrase anywhere, revoking is not enough. Move your funds to a new wallet.',
        never_ask: 'We never ask for your seed phrase or private key.',
        read_line: 'These are the approvals we could read.' };
    }
    return {
      available: true,
      exploit_list_read: true,
      unread_chains: ['Base'],
      warning: 'If you entered your seed phrase anywhere, revoking is not enough. Move your funds to a new wallet.',
      never_ask: 'We never ask for your seed phrase or private key.',
      read_line: 'These are the approvals we could read.',
      items: [
        { rank: 1, chain_id: 1, chain: 'Ethereum', kind: 'erc20', token: TOKEN, spender: SPENDER,
          why: 'on the public exploit list', updated: '2026-10-01', permit2_expires: null, nft_id: null },
        { rank: 2, chain_id: 1, chain: 'Ethereum', kind: 'delegation_7702', token: '', spender: '0x' + 'd0'.repeat(20),
          why: 'active 7702 delegation to a contract that is not on the known-good list', updated: null,
          permit2_expires: null, nft_id: null },
        { rank: 4, chain_id: 1, chain: 'Ethereum', kind: 'erc20', token: '0x' + 'bb'.repeat(20),
          spender: '0x' + 'c3'.repeat(20), why: 'unlimited allowance', updated: '2026-09-01',
          permit2_expires: null, nft_id: '99' },
      ],
    };
  });
  const app = express();
  app.use(express.json());
  app.use('/api/panic', route);
  const server = await listen(app);
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    const res = await fetch(base + '/api/panic', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: WALLET }),
    });
    const body = await res.json();
    assert.deepEqual(body.items.map((i) => i.rank), [1, 2, 4]);
    assert.equal(body.items[0].tx.value, '0x0');
    assert.equal(tx.decodePrepared(body.items[0].tx).actions[0].params.amount, '0');
    assert.equal(tx.decodePrepared(body.items[1].tx).actions[0].tid, 'sgr.a_7702_clear');
    assert.deepEqual(body.unread_chains, ['Base']);
    const blob = JSON.stringify(body);
    assert.ok(!blob.includes('nft_id'));
    assert.ok(!blob.includes('"amount"'));
    assert.ok(!blob.includes('telegram'));
    assert.ok(!blob.includes('$'));
    assert.ok(!/rclaw/i.test(blob));
    assert.ok(blob.includes('These are the approvals we could read.'));
    assert.ok(!/you'?re safe/i.test(blob));
    const other = '0x' + '22'.repeat(20);
    const second = await fetch(base + '/api/panic', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: other }),
    });
    const again = await second.json();
    assert.equal(again.items.length, 0);
    assert.deepEqual(again.unread_chains, ['Arbitrum']);
    assert.deepEqual(seen, [WALLET, other]);
    const receipt = await fetch(base + '/api/panic/receipt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hashes: ['0x' + 'ab'.repeat(32)] }),
    });
    const sealed = await receipt.json();
    assert.ok(sealed.cleaned_at);
    assert.deepEqual(Object.keys(sealed).sort(), ['cleaned_at', 'hashes']);
    assert.ok(!JSON.stringify(sealed).includes(WALLET));
  } finally {
    delete process.env.PANIC_BUTTON;
    route.setRunner(null);
    server.close();
  }
});

test('each IP has its own panic allowance', async () => {
  delete process.env.PANIC_BUTTON;
  const route = require('../routes/panic');
  const app = express();
  app.set('trust proxy', true);
  app.use('/api/panic', route);
  const server = await listen(app);
  const base = `http://127.0.0.1:${server.address().port}`;
  async function post(ip) {
    const res = await fetch(base + '/api/panic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': ip },
      body: JSON.stringify({ address: WALLET }),
    });
    return res.status;
  }
  try {
    const first = [];
    for (let i = 0; i < 20; i++) first.push(await post('198.51.100.10'));
    assert.deepEqual(first, Array(20).fill(200));
    assert.equal(await post('198.51.100.10'), 429);
    assert.equal(await post('198.51.100.11'), 200);
  } finally {
    server.close();
  }
});

test('a body over the route cap is refused and one inside it is read', async () => {
  process.env.PANIC_BUTTON = '1';
  const route = require('../routes/panic');
  const seen = [];
  route.setRunner(async (body) => {
    seen.push(body && body.address);
    return {
      available: true, items: [], unread_chains: [], exploit_list_read: true,
      warning: 'If you entered your seed phrase anywhere, revoking is not enough. Move your funds to a new wallet.',
      never_ask: 'We never ask for your seed phrase or private key.',
      read_line: 'These are the approvals we could read.',
    };
  });
  const serverSrc = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const capAt = serverSrc.indexOf("app.use('/api/panic', panicApi.capBody)");
  const wideAt = serverSrc.indexOf("express.json({ limit: '1mb' })");
  assert.ok(capAt > 0 && wideAt > capAt, 'the panic cap must run before the 1mb parser');
  const app = express();
  app.set('trust proxy', true);
  app.use('/api/panic', route);
  const server = await listen(app);
  const base = `http://127.0.0.1:${server.address().port}`;
  const headers = {
    'Content-Type': 'application/json',
    'X-Forwarded-For': '198.51.100.20',
  };
  try {
    const over = await fetch(base + '/api/panic', {
      method: 'POST', headers,
      body: JSON.stringify({ address: WALLET, pad: 'x'.repeat(route.bodyLimitBytes + 64) }),
    });
    assert.equal(over.status, 413);
    assert.deepEqual(seen, []);
    const under = await fetch(base + '/api/panic', {
      method: 'POST', headers,
      body: JSON.stringify({ address: WALLET }),
    });
    assert.equal(under.status, 200);
    assert.deepEqual(seen, [WALLET]);
  } finally {
    delete process.env.PANIC_BUTTON;
    route.setRunner(null);
    server.close();
  }
});

test('the page copy leads with the seed warning and makes no safety promise', () => {
  const html = fs.readFileSync(path.join(APP, 'public', 'panic.html'), 'utf8');
  const js = fs.readFileSync(path.join(APP, 'public', 'js', 'panic.js'), 'utf8');
  assert.ok(html.indexOf('If you entered your seed phrase anywhere') < html.indexOf('id="form"'));
  assert.ok(html.includes('We never ask for your seed phrase or private key.'));
  assert.ok(html.includes('These are the approvals we could read'));
  assert.ok(!/you'?re safe/i.test(html + js));
  assert.ok(!html.includes('$RCLAW') && !js.includes('$RCLAW'));
  const server = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const route = fs.readFileSync(path.join(APP, 'routes', 'panic.js'), 'utf8');
  const flag = fs.readFileSync(path.join(APP, 'lib', 'panic_flag.js'), 'utf8');
  assert.ok(server.includes("app.use('/api/panic'"));
  assert.ok(server.includes("app.get('/panic'"));
  // The existing Claw Guard comment in server.js stays. Panic must not turn
  // that flag on, and its own route and flag module must not name it.
  const mounts = server.split('\n').filter((line) => (
    line.includes("'/api/panic'") || line.includes("'/panic'") || line.includes("'/panic.html'")
  ));
  assert.ok(mounts.length >= 2);
  assert.ok(mounts.every((line) => !line.includes('CLAW_GUARD_V01')));
  assert.ok(!route.includes('CLAW_GUARD_V01'));
  assert.ok(!flag.includes('CLAW_GUARD_V01'));
});

test('no sign or broadcast path and no private-key variable', () => {
  const banned = [
    'eth_sign', 'personal_sign', 'signTransaction', 'sign_transaction',
    'eth_sendTransaction', 'eth_sendRawTransaction', 'sendRawTransaction',
    'broadcastTransaction', 'private_key', 'privateKey', 'PRIVATE_KEY',
    'relayer_key', 'relayerKey', 'mnemonic', '.sign(', 'new Wallet',
  ];
  const files = [
    'lib/panic_tx.js', 'lib/panic_flag.js', 'lib/panic_read.js', 'routes/panic.js',
    'public/js/panic.js', 'public/panic.html', 'lib/allowances.js', 'server.js',
  ];
  for (const rel of files) {
    const full = path.join(APP, rel);
    if (!fs.existsSync(full)) continue;
    const src = codeOnly(fs.readFileSync(full, 'utf8'));
    for (const word of banned) {
      assert.ok(!src.includes(word), `${rel} contains ${word}`);
    }
  }
});
