'use strict';
/**
 * The in-repo ABI encoder against real `ethers` — a genuinely different
 * implementation, for the reason spelled out in keccak256.test.js.
 *
 * Every call this server writes for a user's wallet is here by signature, so
 * adding a fourth wallet-facing call without a vector is a visible omission
 * rather than a quiet one.
 */

const test = require('node:test');
const assert = require('node:assert');
const { ethers } = require('ethers');

const { encodeCall, selector, assertCalldata, canonicalSignature } = require('../lib/abi_call');

const Z = '0x' + '00'.repeat(20);

/** ethers' answer for the same call — the reference. */
function reference(sig, args) {
  const iface = new ethers.Interface([`function ${sig}`]);
  return iface.encodeFunctionData(sig.slice(0, sig.indexOf('(')), args);
}

// ── the three calls this server hands to a wallet ────────────────────────────

const WALLET_FACING = [
  // tool8257.js — the ERC-8257 registration
  ['registerTool(string,bytes32,address)',
    ['https://humanoid-traders.com/.well-known/ai-tool/runeclaw-intel.json',
      '0x7fe3e5ecbf1d4e566d5670d99e78264d1740bd0b025198bcc086c3067737b053', Z]],
  // allowances.js — the revoke
  ['approve(address,uint256)', ['0x1111111254EEB25477B68fb85Ed929f73A960582', 0n]],
  // nft.js — the mint voucher
  ['mint(bytes)', ['0x' + 'cd'.repeat(65)]],
];

test('every wallet-facing call encodes byte-identically to ethers', () => {
  for (const [sig, args] of WALLET_FACING) {
    assert.equal(encodeCall(sig, args), reference(sig, args), sig);
  }
});

test('read-path signatures encode identically too', () => {
  const cases = [
    ['allowance(address,address)', ['0x' + '11'.repeat(20), '0x' + '22'.repeat(20)]],
    ['tokenOf(address)', [Z]],
    ['tokenURI(uint256)', [12345]],
    ['totalMinted()', []],
  ];
  for (const [sig, args] of cases) {
    assert.equal(encodeCall(sig, args), reference(sig, args), sig);
  }
});

// ── the edges that move the tail offsets ─────────────────────────────────────

test('dynamic-argument edges: empty, one word short, exact, one word over', () => {
  const h = '0x' + '01'.repeat(32);
  for (const s of ['', 'x'.repeat(31), 'x'.repeat(32), 'x'.repeat(33), 'x'.repeat(200)]) {
    const sig = 'registerTool(string,bytes32,address)';
    assert.equal(encodeCall(sig, [s, h, Z]), reference(sig, [s, h, Z]),
      `string of length ${s.length}`);
  }
  for (const b of ['0x', '0xff', '0x' + 'ab'.repeat(32), '0x' + 'ab'.repeat(33)]) {
    assert.equal(encodeCall('mint(bytes)', [b]), reference('mint(bytes)', [b]),
      `bytes of length ${(b.length - 2) / 2}`);
  }
});

test('multi-byte UTF-8 in a string argument is length-prefixed in BYTES', () => {
  // The length word is a byte count, not a character count. Getting this
  // wrong shifts every following word and produces calldata that decodes to
  // garbage rather than failing.
  const sig = 'registerTool(string,bytes32,address)';
  const args = ['https://exämple.test/漢字/✓.json', '0x' + '01'.repeat(32), Z];
  assert.equal(encodeCall(sig, args), reference(sig, args));
});

test('uint256 range: zero, max, and decimal-string input', () => {
  const sp = '0x' + '33'.repeat(20);
  for (const v of [0n, 1n, (1n << 255n), (1n << 256n) - 1n]) {
    assert.equal(encodeCall('approve(address,uint256)', [sp, v]),
      reference('approve(address,uint256)', [sp, v]), String(v));
  }
  assert.equal(encodeCall('approve(address,uint256)', [sp, '42']),
    reference('approve(address,uint256)', [sp, 42n]));
  assert.throws(() => encodeCall('approve(address,uint256)', [sp, 1n << 256n]),
    /out of range/);
  assert.throws(() => encodeCall('approve(address,uint256)', [sp, -1]), /non-negative/);
});

test('selectors match ethers', () => {
  assert.equal(selector('approve(address,uint256)'), '0x095ea7b3'); // the famous one
  assert.equal(selector('transfer(address,uint256)'), '0xa9059cbb');
  for (const [sig] of WALLET_FACING) {
    assert.equal(selector(sig), reference(sig, WALLET_FACING.find(w => w[0] === sig)[1])
      .slice(0, 10));
  }
});

// ── refusing rather than approximating ───────────────────────────────────────

test('an unsupported type is refused, never encoded approximately', () => {
  // A partial encoder that guesses at uint8[] or a tuple would be the original
  // bug with extra steps: well-formed calldata that means something else.
  assert.throws(() => encodeCall('f(uint256[])', [[1]]), /unsupported type/);
  assert.throws(() => encodeCall('f(bool)', [true]), /unsupported type/);
  assert.throws(() => encodeCall('f((address,uint256))', [[Z, 1]]), /unsupported|parse/);
  assert.throws(() => encodeCall('f(uint8)', [1]), /unsupported type/);
});

test('bad arguments are refused', () => {
  assert.throws(() => encodeCall('approve(address,uint256)', ['not-an-address', 0n]),
    /not a 20-byte address/);
  assert.throws(() => encodeCall('approve(address,uint256)', ['0x' + '11'.repeat(20)]),
    /takes 2 args/);
  assert.throws(() => encodeCall('registerTool(string,bytes32,address)',
    ['u', '0xshort', Z]), /not a 32-byte value/);
  assert.throws(() => encodeCall('mint(bytes)', ['no-0x']), /even-length 0x hex/);
  assert.throws(() => encodeCall('mint(bytes)', [123]), /even-length 0x hex/);
});

test('canonicalSignature strips whitespace and rejects junk', () => {
  assert.equal(canonicalSignature(' approve ( address , uint256 ) ').canonical,
    'approve(address,uint256)');
  assert.throws(() => canonicalSignature('not a signature'), /cannot parse/);
  assert.throws(() => canonicalSignature(''), /cannot parse/);
});

// ── the guard, on somebody else's output ─────────────────────────────────────

test("assertCalldata rejects the stub's '0x' — the exact production symptom", () => {
  assert.throws(() => assertCalldata('0x', 'approve(address,uint256)'),
    /EMPTY/,
    "'0x' is a valid transaction that calls nothing and reports success");
  assert.throws(() => assertCalldata('0x095ea7b', 'approve(address,uint256)'), /truncated/);
});

test('assertCalldata rejects a correct call to the WRONG function', () => {
  const transfer = encodeCall('transfer(address,uint256)', ['0x' + '44'.repeat(20), 5n]);
  assert.throws(() => assertCalldata(transfer, 'approve(address,uint256)'),
    /is not approve\(address,uint256\)/);
});

test('assertCalldata rejects non-hex and half-word bodies', () => {
  assert.throws(() => assertCalldata('nonsense', 'totalMinted()'), /non-hex/);
  assert.throws(() => assertCalldata(undefined, 'totalMinted()'), /non-hex/);
  const ok = encodeCall('approve(address,uint256)', ['0x' + '44'.repeat(20), 5n]);
  assert.throws(() => assertCalldata(ok + 'ff', 'approve(address,uint256)'),
    /whole number of words/);
  assert.equal(assertCalldata(ok, 'approve(address,uint256)'), ok);
});

test('the encoder checks its OWN output before returning it', () => {
  // encodeCall calls assertCalldata on the way out. Cheap, and it means a
  // future edit that breaks head/tail layout fails here rather than in a
  // wallet.
  const d = encodeCall('registerTool(string,bytes32,address)',
    ['u', '0x' + '01'.repeat(32), Z]);
  assert.equal((d.length - 10) % 64, 0);
  assert.equal(d.slice(0, 10), selector('registerTool(string,bytes32,address)'));
});
