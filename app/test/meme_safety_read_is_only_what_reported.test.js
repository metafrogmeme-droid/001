'use strict';
/**
 * The meme radar reads three safety signals and totals one figure, and every
 * one of them was coerced at the NORMALIZER — the earliest place the
 * distinction between "the feed said zero" and "the feed said nothing" can be
 * lost. #179 fixed the sibling radar (`rwa.js`) and filed this one; driven, it
 * was worse than the note said.
 *
 * The headline, driven on a DEXScreener pair carrying buys and no sells:
 *
 *   sells UNREAD:          {tier: extreme, flags: [no-sells-yet, buys-only-skew]}
 *   sells a MEASURED zero: {tier: extreme, flags: [no-sells-yet, buys-only-skew]}
 *
 * Byte-identical. A count nobody read was indistinguishable from the real
 * "you cannot exit this" condition the flag exists for — on the read this
 * module's own header calls "the SAME liquidity/age/flow read a future
 * agent-buy will gate on".
 *
 * Every fixture here has at least one unreadable field. A fixture where every
 * row is readable cannot tell a coerced aggregate from an honest one, which is
 * how `rwa.test.js`'s own "volume-weighted" test passed over the same defect
 * for as long as it existed.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const meme = require('../lib/meme');
const { codeOnly } = require('./helpers/code_only');

const NOW = 1_700_000_000_000;
const HOUR = 3_600_000;

function pair(over) {
  return Object.assign({
    chainId: 'solana', dexId: 'raydium', url: 'https://dexscreener.com/x',
    baseToken: { address: 'Mint' + Math.random(), name: 'Doge Killer', symbol: 'DOGEK' },
    quoteToken: { symbol: 'SOL' },
    priceUsd: '0.0004', priceChange: { h24: 42 }, volume: { h24: 250000 },
    liquidity: { usd: 120000 }, fdv: 900000,
    txns: { h24: { buys: 300, sells: 210 } },
    pairCreatedAt: NOW - 10 * 24 * HOUR,
  }, over || {});
}

// ── the safety read ─────────────────────────────────────────────────────────

test('an unread sells count is not a measured zero', () => {
  const unread = meme.riskRead(120000, 200, 400, null);
  const zero = meme.riskRead(120000, 200, 400, 0);
  assert.notDeepStrictEqual(unread, zero,
    'a count the feed did not report reads the same as a measured zero');
  assert.deepEqual(unread.flags, [], 'a flow flag fired from a count nobody read');
  assert.equal(unread.tier, 'high', 'an unread count escalated the risk tier');
  assert.deepEqual(unread.unread, ['flow']);
  // The measured zero still says what it always said: this is the condition
  // the flag exists for and it must not be softened by the fix.
  assert.ok(zero.flags.includes('no-sells-yet'));
  assert.equal(zero.tier, 'extreme');
});

test('an unread buys count does not make the total a threshold over one side', () => {
  // `totalTx >= 20` used to be met by the readable side alone, and then
  // `sells === 0` was read as a measurement.
  const r = meme.riskRead(120000, 200, null, 400);
  assert.deepEqual(r.flags, []);
  assert.deepEqual(r.unread, ['flow']);
});

test('each unread signal is named, and a measured one still flags', () => {
  assert.deepEqual(meme.riskRead(null, 200, 300, 210).unread, ['liquidity']);
  assert.deepEqual(meme.riskRead(120000, null, 300, 210).unread, ['age']);
  assert.deepEqual(meme.riskRead(null, null, null, null).unread, meme.RISK_SUBJECTS);
  // An unread signal neither raises nor lowers the floor.
  assert.equal(meme.riskRead(null, null, null, null).tier, 'high');
  // and a measured one is unchanged
  assert.equal(meme.riskRead(5000, 200, 10, 10).tier, 'extreme');
  assert.equal(meme.riskRead(120000, 5, 30, 5).tier, 'extreme');
});

// ── the normalizer ──────────────────────────────────────────────────────────

test('the normalizer keeps what the feed did not report as null', () => {
  const t = meme.normalizePair(pair({ volume: undefined, txns: { h24: { buys: 400 } } }));
  assert.equal(t.volume_24h_usd, null, 'an unreported volume was stamped as a measured figure');
  assert.equal(t.sells_24h, null, 'an unreported sells count was stamped as zero');
  assert.equal(t.buys_24h, 400);
  // A READ zero is still a reading.
  const z = meme.normalizePair(pair({ volume: { h24: 0 }, txns: { h24: { buys: 0, sells: 0 } } }));
  assert.equal(z.volume_24h_usd, 0);
  assert.equal(z.sells_24h, 0);
});

// ── the aggregates ──────────────────────────────────────────────────────────

test('a volume total is over the rows that reported one, with its sample', () => {
  const v = meme.volumeTotal([{ volume_24h_usd: 100 }, { volume_24h_usd: null }, { volume_24h_usd: 50 }]);
  assert.deepEqual(v, { usd: 150, scored: 2, total: 3 });
  const none = meme.volumeTotal([{ volume_24h_usd: null }, { volume_24h_usd: null }]);
  assert.equal(none.usd, null, 'nothing read published as $0 — a market that traded nothing');
  assert.equal(none.scored, 0);
});

test('an unread volume is never ranked and is never the top by volume', () => {
  const r = meme.buildRadar([
    pair({ volume: undefined, baseToken: { address: 'U', name: 'u', symbol: 'UNREAD' } }),
    pair({ volume: { h24: 10 }, baseToken: { address: 'S', name: 's', symbol: 'SMALL' } }),
  ], NOW);
  assert.equal(r.tokens[0].symbol, 'SMALL', 'a row with no reported volume outranked a measured one');
  assert.equal(r.summary.top_by_volume.symbol, 'SMALL');
  assert.equal(r.summary.volume_24h_usd, 10);
  assert.equal(r.summary.volume_scored, 1);
  assert.equal(r.summary.volume_total, 2);

  // A MEASURED zero is the input that separates the ranking from a coercion:
  // with `(b.volume || 0) - (a.volume || 0)` an unread row and a row that
  // genuinely traded nothing are both 0 and tie on feed order, so the unread
  // one can be named the top by volume. The mutation round found this — every
  // other fixture here has a positive volume, where the coercion sorts nulls
  // last by accident and looks correct.
  const flat = meme.buildRadar([
    pair({ volume: undefined, baseToken: { address: 'U2', name: 'u', symbol: 'UNREAD' } }),
    pair({ volume: { h24: 0 }, baseToken: { address: 'Z', name: 'z', symbol: 'QUIET' } }),
  ], NOW);
  assert.equal(flat.tokens[0].symbol, 'QUIET',
    'a row with no reported volume tied with one that was measured at zero');
  assert.equal(flat.summary.top_by_volume.symbol, 'QUIET',
    'the row nobody could rank was named the top by volume');
  assert.equal(flat.summary.volume_24h_usd, 0, 'a measured zero total was reported as unread');
  assert.equal(flat.summary.volume_scored, 1);

  const dark = meme.buildRadar([pair({ volume: undefined })], NOW);
  assert.equal(dark.summary.top_by_volume, null,
    'a row nobody could rank was named the top by volume');
  assert.equal(dark.summary.volume_24h_usd, null);
});

test('a chain total carries its own sample and an unread chain is not ranked', () => {
  const r = meme.buildRadar([
    pair({ chainId: 'base', volume: undefined, baseToken: { address: 'X', name: 'x', symbol: 'X' } }),
    pair({ chainId: 'solana', volume: { h24: 5e6 }, baseToken: { address: 'Y', name: 'y', symbol: 'Y' } }),
  ], NOW);
  assert.equal(r.chains[0].chain, 'solana');
  assert.equal(r.chains[1].volume_24h_usd, null, 'a chain nobody could total published as $0');
  assert.equal(r.chains[1].volume_scored, 0);
});

test('the radar counts the rows whose safety read is short a signal', () => {
  const r = meme.buildRadar([
    pair({ liquidity: undefined, baseToken: { address: 'A', name: 'a', symbol: 'A' } }),
    pair({ baseToken: { address: 'B', name: 'b', symbol: 'B' } }),
  ], NOW);
  assert.equal(r.summary.risk_unread, 1);
  assert.equal(r.summary.risk_subjects, meme.RISK_SUBJECTS.length);
});

// ── the read that failed ────────────────────────────────────────────────────

test('a failed feed read is not an empty market', async () => {
  const failed = meme.buildRadar(null, NOW);
  assert.equal(failed.feed_read, false);
  assert.equal(failed.summary.tokens, 0);
  const read = meme.buildRadar([], NOW);
  assert.equal(read.feed_read, true, 'a read that carried nothing was reported as a failed read');

  meme.setPairFetcher(async () => null);
  const c1 = (await meme.memeChatCard()).reply_html;
  assert.match(c1, /could not be read/);
  assert.ok(!/refreshing/.test(c1), 'the card still guesses a cause for a failed read');

  meme.setPairFetcher(async () => []);
  const c2 = (await meme.memeChatCard()).reply_html;
  assert.match(c2, /answered and is carrying no trending/);
  assert.notEqual(c1, c2, 'a failed read and an empty market get one sentence');
  meme.setPairFetcher(null);
});

test('the default fetcher tells a failed read from a read that carried nothing', async () => {
  // Every other test here injects a fetcher, so the function that DECIDES the
  // four-way distinction was driven by nothing — the mutation round said so:
  // answering `[]` for a refused boost endpoint changed no verdict.
  const real = global.fetch;
  const plan = [];
  global.fetch = async () => {
    const next = plan.shift();
    if (next === 'throw') throw new Error('network');
    return { ok: next.ok, json: async () => next.body };
  };
  const fetchPairs = async () => {
    // `setPairFetcher(null)` restores the module's own default, which is the
    // function under test; `getRadar` is the only way to reach it.
    meme.setPairFetcher(null);
    return meme.getRadar();
  };
  try {
    plan.push({ ok: false, body: null });                       // boost refused
    assert.equal((await fetchPairs()).feed_read, false, 'a refused boost read reported as an empty market');

    plan.push({ ok: true, body: 'not an array' });              // 200, no rows
    assert.equal((await fetchPairs()).feed_read, false, 'a 200 carrying no rows reported as an empty market');

    plan.push({ ok: true, body: [] });                          // read, nothing boosted
    assert.equal((await fetchPairs()).feed_read, true, 'a real empty read reported as a failure');

    plan.push({ ok: true, body: [{ tokenAddress: 'A' }] });
    plan.push({ ok: false, body: null });                       // pairs refused
    assert.equal((await fetchPairs()).feed_read, false, 'a refused pairs read reported as an empty market');

    plan.push({ ok: true, body: [{ tokenAddress: 'A' }] });
    plan.push({ ok: true, body: { pairs: 'junk' } });            // 200, unusable
    assert.equal((await fetchPairs()).feed_read, false, 'an unusable pairs payload reported as an empty market');

    plan.push('throw');                                          // network fault
    assert.equal((await fetchPairs()).feed_read, false, 'a thrown fetch reported as an empty market');
  } finally {
    global.fetch = real;
    meme.setPairFetcher(null);
  }
});

// ── the card ────────────────────────────────────────────────────────────────

test('the card prints an em dash for what was not read, never $0', async () => {
  meme.setPairFetcher(async () => [
    pair({ volume: undefined, liquidity: undefined,
           baseToken: { address: 'W', name: 'w', symbol: 'WIF' } }),
    pair({ volume: { h24: 4.2e6 }, liquidity: { usd: 900000 },
           baseToken: { address: 'B', name: 'b', symbol: 'BONK' } }),
  ]);
  const html = (await meme.memeChatCard()).reply_html;
  meme.setPairFetcher(null);
  const wif = html.split('<br>').find((l) => l.includes('WIF'));
  assert.ok(!/\$0\b/.test(wif), `an unread figure printed as $0: ${wif}`);
  assert.match(wif, /— vol/);
  assert.match(wif, /— liq/);
  assert.match(html, /\$4\.2M vol/);          // the read row still prints
  assert.match(html, /\(1 of 2 reported one\)/);   // the total carries its sample
});

test('the card says how many rows are short a safety signal', async () => {
  meme.setPairFetcher(async () => [
    pair({ liquidity: undefined, baseToken: { address: 'A', name: 'a', symbol: 'A' } }),
    pair({ baseToken: { address: 'B', name: 'b', symbol: 'B' } }),
  ]);
  const html = (await meme.memeChatCard()).reply_html;
  meme.setPairFetcher(null);
  assert.match(html, /1 safety signal unread/);
  assert.match(html, /1 of 2 rows is missing at least one/);
  // The marker counts what is UNREAD, not what was read: `safety 2/3` on a
  // risk marker reads as two checks PASSED, which is the opposite claim.
  assert.ok(!/safety 2\/3/.test(html));
});

// ── one rendering, three cards ──────────────────────────────────────────────

test('the volume rendering is one definition, not a copy per card', () => {
  const nums = require('../lib/card_nums');
  assert.equal(nums.fmtVol(null), '—');
  assert.equal(nums.fmtVol(0), '$0');            // a READ zero is a reading
  assert.equal(nums.pct(null), '—');
  assert.equal(nums.cover(null, 0, 3), '',
    'a sample caveat was printed beside a figure that is not there');

  for (const f of ['meme.js', 'rwa.js', 'research.js']) {
    const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', f), 'utf8'));
    assert.ok(!/function\s+fmtVol\s*\(/.test(src),
      `${f} carries its own fmtVol again — three cards render one quantity and `
      + 'the copy that rotted was the one whose comment was missing');
    assert.match(src, /require\('\.\/card_nums'\)/);
  }
});
