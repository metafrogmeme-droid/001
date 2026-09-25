'use strict';
/**
 * The Allowance X-ray printed "✅ No live grants found" over grants it never
 * read, three ways at once.
 *
 * 1. THE READ WAS ENCODED BY ETHERS. Production resolves `ethers` to a stub
 *    whose `Interface.encodeFunctionData` returns '0x'
 *    (`ethers_stub_calldata.test.js` reproduces it). The revoke calldata had
 *    already been moved to `lib/abi_call` for exactly that reason; the
 *    allowance READ had not, so in production every eth_call asked the token
 *    nothing and every pair came back unreadable.
 * 2. ONE SPENDER'S CHECKSUM WAS WRONG. Uniswap SwapRouter02 was written
 *    `...E4C7bd8665...`; EIP-55 says `bD`. Real ethers refuses that at encode
 *    time, so the router was never read on any of its four chains -- and every
 *    fixture used Base, where the router is excluded, so no test reached it.
 * 3. THE PAGE PRINTED THE ✅ WHENEVER NO GRANT WAS FOUND. A pair that could not
 *    be read found nothing too, so "0 of 40 pairs read" rendered as "No live
 *    grants found among 0 checked pairs", on a public security tool telling a
 *    wallet owner there is nothing to revoke.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { ethers } = require('ethers');

const allow = require('../lib/allowances');
const { encodeCall } = require('../lib/abi_call');
const { activeChains } = require('../lib/wallet');

const OWNER = '0x' + '12'.repeat(20);
const ZERO = '0x' + '0'.repeat(64);
const MAX = '0x' + 'f'.repeat(64);

test('every spender address is EIP-55 correct, so real ethers can encode it', () => {
  for (const s of allow.SPENDERS) {
    assert.equal(ethers.getAddress(s.address), s.address,
      `${s.label} carries a bad checksum; real ethers refuses it at encode time`);
  }
});

test('every spender is READ on every chain it is scoped to', async () => {
  for (const chain of activeChains()) {
    if (!Array.isArray(chain.tokens) || !chain.tokens.length) continue;
    const spenders = allow.spendersFor(chain.key);
    const seen = new Set();
    allow.setEthCaller(async (_c, to, data) => {
      seen.add(`${to}|${data}`);
      return ZERO;
    });
    try {
      const r = await allow.readAllowances(OWNER, chain.key);
      assert.equal(r.unreadable_pairs, 0, `${chain.key}: pairs went unread`);
      assert.equal(r.zero_pairs, chain.tokens.length * spenders.length);
      for (const t of chain.tokens) {
        for (const s of spenders) {
          const data = encodeCall('allowance(address,address)', [OWNER, s.address]);
          assert.ok(seen.has(`${t.address}|${data}`), `${chain.key}: ${t.symbol} x ${s.label} never asked`);
        }
      }
    } finally { allow.setEthCaller(null); }
  }
});

test('an unlimited grant to SwapRouter02 on Ethereum is FOUND', async () => {
  const router = allow.SPENDERS.find((s) => /SwapRouter02/.test(s.label));
  const tail = router.address.slice(2).toLowerCase();
  allow.setEthCaller(async (_c, _to, data) => (data.toLowerCase().endsWith(tail) ? MAX : ZERO));
  try {
    const r = await allow.readAllowances(OWNER, 'ethereum');
    const hits = r.findings.filter((f) => f.spender === router.address);
    assert.ok(hits.length > 0, 'the router\'s unlimited approval was invisible');
    assert.ok(hits.every((f) => f.unlimited));
    assert.equal(r.unreadable_pairs, 0);
  } finally { allow.setEthCaller(null); }
});

test('the read is the ABI call, not whatever ethers answers', async () => {
  const sent = [];
  allow.setEthCaller(async (_c, _to, data) => { sent.push(data); return ZERO; });
  try {
    await allow.readAllowances(OWNER, 'ethereum');
  } finally { allow.setEthCaller(null); }
  assert.ok(sent.length > 0);
  for (const d of sent) {
    assert.match(d, /^0xdd62ed3e[0-9a-f]{128}$/i, `a read sent ${d}`);
  }
});

// ── the page ──────────────────────────────────────────────────────────────

function renderer() {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'approvals.html'), 'utf8');
  const script = page.match(/<script>\n([\s\S]*?)<\/script>/)[1];
  const start = script.indexOf('function renderBody(d)');
  let depth = 0, end = -1;
  for (let i = script.indexOf('{', start); i < script.length; i++) {
    if (script[i] === '{') depth++;
    else if (script[i] === '}' && --depth === 0) { end = i + 1; break; }
  }
  const helpers = `
    var T = function (k, en) { return en; };
    function fill(tpl, map) { return String(tpl).replace(/\\{(\\w+)\\}/g, function (_, k) { return String(map[k]); }); }
    function esc(s) { return String(s); }
    function human(raw) { return raw; }
  `;
  const ctx = {};
  vm.runInNewContext(`${helpers}\n${script.slice(start, end)}\nthis.renderBody = renderBody;`, ctx);
  return ctx.renderBody;
}

const base = { label: 'Ethereum', spenders_checked: ['Permit2 (Uniswap)'], findings: [] };

test('the ✅ is printed only when every pair was read', () => {
  const render = renderer();
  assert.match(render({ ...base, zero_pairs: 40, unreadable_pairs: 0 }), /✅ No live grants found among 40/);
});

test('a chain that answered nothing is never clean', () => {
  const out = renderer()({ ...base, zero_pairs: 0, unreadable_pairs: 40 });
  assert.doesNotMatch(out, /✅/, 'nothing was read and the page said clean');
  assert.match(out, /unknown, not zero/);
});

test('a partly read chain says it is not a clean result', () => {
  const out = renderer()({ ...base, zero_pairs: 32, unreadable_pairs: 8 });
  assert.doesNotMatch(out, /✅/);
  assert.match(out, /8 of 40 pairs on Ethereum could not be read/);
  assert.match(out, /among the 32 that were read/);
});

test('a payload that does not carry the unread count earns no ✅', () => {
  const out = renderer()({ ...base, zero_pairs: 40 });
  assert.doesNotMatch(out, /✅/);
});
