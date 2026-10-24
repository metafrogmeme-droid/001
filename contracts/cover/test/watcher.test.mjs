/**
 * The userFills watcher reports an ADL notice and nothing else.
 * No socket is opened, no price is copied, and a URL that is not the
 * Hyperliquid testnet websocket is refused.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ADL_DIR,
  TESTNET_WS,
  assertTestnetUrl,
  readMessage,
  startUserFillsWatch,
  userAddress,
} from '../watcher/user_fills.ts';

const HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';
const USER = '0x' + 'ab'.repeat(20);
const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'watcher', 'user_fills.ts'), 'utf8');

function socket() {
  const sent = [];
  const sock = {
    sent,
    send(data) { sent.push(data); },
    close() { sock.closed = true; },
    closed: false,
    onmessage: null,
  };
  return sock;
}

function watch(env, extra = {}) {
  const calls = [];
  const sock = socket();
  const notices = [];
  let unreadable = 0;
  const handle = startUserFillsWatch({
    env,
    user: USER,
    connect(url) {
      calls.push(url);
      return sock;
    },
    onAdl(notice) { notices.push(notice); },
    onUnreadable() { unreadable += 1; },
    ...extra,
  });
  return { handle, calls, sock, notices, unreadable: () => unreadable };
}

test('the watcher states the hold and speaks only to the testnet websocket', () => {
  assert.ok(source.includes(HOLD));
  assert.equal((source.match(/wss:\/\/api\.hyperliquid-testnet\.xyz\/ws/g) || []).length, 1);
  assert.ok(!source.includes('api.hyperliquid.xyz'));
  assert.ok(!/sendTransaction|privateKey|eth_sign/i.test(source));
  assert.ok(!/guaranteed|risk-free|\$RCLAW/i.test(source));
});

test('the flag off does not connect, and the other flag does not start it', () => {
  const off = watch({});
  assert.equal(off.handle.opened, false);
  assert.deepEqual(off.calls, []);
  const other = watch({ COVER_STOPGAP_ENABLED: '1' });
  assert.equal(other.handle.opened, false);
  assert.deepEqual(other.calls, []);
  const missing = watch(null);
  assert.equal(missing.handle.opened, false);
});

test('a flag that is on subscribes once, and stop closes the socket', () => {
  for (const value of ['1', 'true', ' TRUE ']) {
    const w = watch({ COVER_ADL_ENABLED: value });
    assert.equal(w.handle.opened, true);
    assert.deepEqual(w.calls, [TESTNET_WS]);
    const sub = JSON.parse(w.sock.sent[0]);
    assert.deepEqual(sub, { method: 'subscribe', subscription: { type: 'userFills', user: USER } });
    w.handle.stop();
    assert.equal(w.sock.closed, true);
  }
});

test('anything other than the testnet websocket is refused before a socket exists', () => {
  const refused = [
    'https://api.hyperliquid-testnet.xyz/ws',
    'wss://api.hyperliquid.xyz/ws',
    'wss://api.hyperliquid-testnet.xyz/info',
    'wss://api.hyperliquid-testnet.xyz/ws?user=1',
    'wss://api.hyperliquid-testnet.xyz/ws#x',
    'wss://api.hyperliquid-testnet.xyz:444/ws',
    'wss://user:pw@api.hyperliquid-testnet.xyz/ws',
    'wss://api.hyperliquid-testnet.xyz.evil/ws',
  ];
  for (const url of refused) {
    assert.throws(() => assertTestnetUrl(url), /refused/, url);
    assert.throws(() => watch({ COVER_ADL_ENABLED: '1' }, { url }), /refused/, url);
  }
  assert.throws(() => userAddress('alice'), /refused/);
  assert.throws(() => watch({ COVER_ADL_ENABLED: '1' }, { user: '0x1234' }), /refused/);
});

test('an ADL fill is a notice with no price, a snapshot is not, and an unreadable message is not an empty book', () => {
  const w = watch({ COVER_ADL_ENABLED: 'true' });
  const otherUser = '0x' + '11'.repeat(20);
  w.sock.onmessage(JSON.stringify({
    data: {
      isSnapshot: true,
      fills: [{ dir: ADL_DIR, coin: 'BTC', time: 5, px: '100' }],
    },
  }));
  assert.equal(w.notices.length, 0);
  w.sock.onmessage(JSON.stringify({
    data: {
      isSnapshot: false,
      fills: [
        { dir: 'Close Long', coin: 'BTC', time: 1, px: '9' },
        { dir: ADL_DIR, coin: 'ETH', time: 0, px: '2500', user: otherUser },
        { dir: ADL_DIR, time: 'soon' },
      ],
    },
  }));
  assert.equal(w.notices.length, 2);
  assert.deepEqual(Object.keys(w.notices[0]).sort(), ['coin', 'time', 'user']);
  assert.deepEqual(w.notices[0], { user: USER, coin: 'ETH', time: 0 });
  assert.equal(w.notices[0].user, USER);
  assert.notEqual(w.notices[0].user, otherUser);
  assert.deepEqual(w.notices[1], { user: USER, coin: null, time: null });
  assert.equal(JSON.stringify(w.notices).includes('px'), false);
  assert.equal(JSON.stringify(w.notices).includes('2500'), false);

  for (const raw of ['{', 'null', '{"data":{}}', '{"data":{"isSnapshot":false}}',
    '{"data":{"isSnapshot":"no","fills":[]}}',
    '{"data":{"isSnapshot":false,"fills":[{"dir":1}]}}']) {
    assert.deepEqual(readMessage(raw), { ok: false, reason: 'unreadable' });
    w.sock.onmessage(raw);
  }
  assert.equal(w.unreadable(), 6);
  assert.equal(w.notices.length, 2);
});
