'use strict';
/**
 * SPL delegate scan — the Allowance X-ray's Solana half. A delegate on a
 * token account is Solana's ERC-20 allowance, and approval drains work
 * exactly this way. Pins:
 * - ALL token accounts are scanned (a delegate on an unknown mint matters
 *   as much as one on a curated mint — unknown mints show honestly by
 *   truncated mint address);
 * - u64-scale delegation (>= 2^63) flags effectively-unlimited;
 * - the note is honest in BOTH directions: complete for token delegates,
 *   but blind to other program powers (PDAs, program ownership);
 * - unreadable answers unknown-not-clean and is never cached;
 * - the revoke plan is a command the OWNER runs — no signing path exists.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { readDelegates, setRpcCall } = require('../lib/solana');

const OWNER = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU';
const acct = (pubkey, mint, delegate, amount, decimals) => ({
  pubkey,
  account: { data: { parsed: { info: {
    mint,
    ...(delegate ? { delegate, delegatedAmount: { amount, decimals } } : {}),
    tokenAmount: { decimals },
  } } } },
});

test.after(() => setRpcCall(null));

test('delegates surface with honest labels; clean accounts count as zero', async () => {
  const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';
  setRpcCall(async (method) => {
    assert.strictEqual(method, 'getTokenAccountsByOwner');
    return { value: [
      acct('AcctOne111111111111111111111111111111111111', USDC_MINT,
        'Dele111111111111111111111111111111111111111', '18446744073709551615', 6),
      acct('AcctTwo111111111111111111111111111111111111', 'Unk1nMint1111111111111111111111111111111111',
        'Dele222222222222222222222222222222222222222', '5000000', 9),
      acct('AcctClean11111111111111111111111111111111111', USDC_MINT, null, '0', 6),
    ] };
  });
  const r = await readDelegates(OWNER);
  assert.strictEqual(r.findings.length, 2);
  assert.strictEqual(r.zero_pairs, 1, 'the clean account counts');
  const [a, b] = r.findings;
  assert.strictEqual(a.token, 'USDC', 'curated mints get their symbol');
  assert.strictEqual(a.unlimited, true, 'u64-max delegation flags unlimited');
  assert.match(b.token, /^Unk1…1111$/, 'unknown mints show truncated, never invented');
  assert.strictEqual(b.unlimited, false);
  assert.match(a.revoke_plan.command, /^spl-token revoke AcctOne/);
  assert.match(r.note, /this scan does not see/, 'honest about program powers');
  assert.match(r.note, /never\s+signs and never sends/);
});

test('unreadable answers unknown-not-clean', async () => {
  setRpcCall(async () => { throw new Error('rpc down'); });
  const r = await readDelegates(OWNER);
  assert.strictEqual(r.findings.length, 0);
  assert.strictEqual(r.unreadable_pairs, 1);
  assert.match(r.note, /unknown, not\s+.?clean/i);
});

test('bad addresses refuse before any RPC', async () => {
  let called = false;
  setRpcCall(async () => { called = true; return { value: [] }; });
  const r = await readDelegates('0x020A4366595292aa16184536bAB2E9949d0D925F');
  assert.strictEqual(r.error, 'bad address', 'an EVM address is not a Solana owner');
  assert.strictEqual(called, false);
});

test('the page speaks both address dialects', () => {
  const page = require('fs').readFileSync(
    require('path').join(__dirname, '..', 'public', 'approvals.html'), 'utf8');
  assert.match(page, /'solana'\];/, 'solana joins the chain list');
  assert.match(page, /\[1-9A-HJ-NP-Za-km-z\]\{32,44\}/, 'base58 validation for solana');
  assert.match(page, /validFor\(sel\.value, qa\)/, 'the deep link validates per-chain');
  assert.match(page, /f\.revoke_plan\.command/, 'command plans render');
});
