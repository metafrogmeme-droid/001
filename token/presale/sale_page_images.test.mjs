// The images the Smithii pages show, held against the repo's own numbers.
//
// Three files in docs/assets/presale/ are second copies of numbers that live elsewhere: the allocation table
// in docs/TOKEN_ROADMAP.md section 4, the sale terms in smithii.config.json, the three schedules in
// locks.plan.json and the token record.
//   rclaw_tokenomics_1500x750.png   the sale form's "Tokenomics Image (1500x750)" (a URL)
//   rclaw_roadmap_1500x750.png      the sale form's roadmap image (a URL)
//   rclaw_tokenomics_1000x1000.png  the vesting tool's "Tokenomics Image (optional)", 1000x1000 (an upload)
// A second copy of a number drifts, and these are public. The generator wrote the exact figures it drew into
// each PNG (a tEXt chunk named `runeclaw-tokenomics` or `runeclaw-roadmap`); this test reads them back and
// holds them to the sources, so changing a source without redrawing the image fails here.
//
// The roadmap image also makes claims about today (a tick means done). A tick is a claim, so every tick
// must be one the token record supports; the open circles claim nothing and are not checked.
//
// It checks what an image SAYS it was drawn from, not its pixels. That is the limit: it catches a source that
// moved under an unchanged image, which is how this kind of drift happens, and it does not catch someone
// retouching a picture by hand.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadLocksPlan, shortNote } from './locks_lib.mjs';
import { allocationFromRoadmap } from './roadmap_table.mjs';
import { deriveSmithiiSale, loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, '..', '..');
const ASSETS = path.join(REPO, 'docs', 'assets', 'presale');
const TOKENOMICS = { file: 'rclaw_tokenomics_1500x750.png', keyword: 'runeclaw-tokenomics', size: [1500, 750], asked: 'the sale form says "(1500x750)"' };
const ROADMAP = { file: 'rclaw_roadmap_1500x750.png', keyword: 'runeclaw-roadmap', size: [1500, 750], asked: 'the sale form says "(1500x750)"' };
const CERTIFICATE = { file: 'rclaw_tokenomics_1000x1000.png', keyword: 'runeclaw-tokenomics', size: [1000, 1000], asked: 'the vesting tool says ".png · .jpg · 1000×1000 px"' };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Width, height and every tEXt chunk of a PNG, read from the bytes. */
export function readPng(buf) {
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.ok(buf.subarray(0, 8).equals(sig), 'not a PNG');
  const out = { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20), text: {} };
  let off = 8;
  while (off + 12 <= buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString('latin1', off + 4, off + 8);
    if (type === 'tEXt') {
      const data = buf.subarray(off + 8, off + 8 + len);
      const nul = data.indexOf(0);
      out.text[data.toString('latin1', 0, nul)] = data.toString('latin1', nul + 1);
    }
    off += 12 + len;
    if (type === 'IEND') break;
  }
  return out;
}

/** "15–29 Oct 2026": the sale window as the roadmap image words it, from the config's two UTC instants. */
export function windowShort(cfg) {
  const a = new Date(cfg.schedule.startUtc);
  const b = new Date(cfg.schedule.endUtc);
  const m = (d) => MONTHS[d.getUTCMonth()];
  return a.getUTCMonth() === b.getUTCMonth() && a.getUTCFullYear() === b.getUTCFullYear()
    ? `${a.getUTCDate()}–${b.getUTCDate()} ${m(b)} ${b.getUTCFullYear()}`
    : `${a.getUTCDate()} ${m(a)} – ${b.getUTCDate()} ${m(b)} ${b.getUTCFullYear()}`;
}

/** The stored rate as the image prints it, without the thousands comma: 30000.3. */
const storedRate = (cfg) => String(Math.round(deriveSmithiiSale(cfg).price.tokensPerSol * 10) / 10);

/** Everything the tokenomics image claims that the repo no longer says; empty means it still matches. */
export function tokenomicsDrift(claim, table, cfg, record, plan) {
  const out = [];
  const supply = Number(record.supply_tokens);
  if (claim.v !== 2) out.push(`the record is version ${claim.v}; this test reads version 2`);
  if (claim.supply !== supply) out.push(`the image says a supply of ${claim.supply}; the token record says ${supply}`);
  if (!table.total || table.total.tokens !== supply || table.total.pct !== 100) out.push(`the roadmap's Total row is not 100% of ${supply}`);
  if (claim.buckets.length !== table.rows.length) out.push(`the image draws ${claim.buckets.length} buckets; the roadmap table has ${table.rows.length}`);
  for (const b of claim.buckets) {
    const hits = table.rows.filter((r) => r.label.startsWith(b.key));
    if (hits.length !== 1) { out.push(`bucket "${b.key}" matches ${hits.length} roadmap rows`); continue; }
    if (hits[0].pct !== b.pct) out.push(`"${b.key}": the image says ${b.pct}%, the roadmap says ${hits[0].pct}%`);
    if (hits[0].tokens !== b.tokens) out.push(`"${b.key}": the image says ${b.tokens} tokens, the roadmap says ${hits[0].tokens}`);
  }
  const s = claim.sale ?? {};
  const want = [
    ['rate per SOL', s.ratePerSol, storedRate(cfg), 'the stored price makes it'],
    ['hard cap (SOL)', s.hardCapSol, Number(cfg.sale.hardCapSol), 'the config says'],
    ['soft cap (SOL)', s.softCapSol, Number(cfg.sale.softCapSol), 'the config says'],
    ['minimum buy (SOL)', s.minBuySol, String(cfg.sale.minContributionSol), 'the config says'],
    ['maximum buy (SOL)', s.maxBuySol, String(cfg.sale.maxContributionSol), 'the config says'],
    ['sale start', s.startUtc, cfg.schedule.startUtc, 'the config says'],
    ['sale end', s.endUtc, cfg.schedule.endUtc, 'the config says'],
    ['sale hours', s.hours, cfg.publicPhaseHours, 'the config says'],
    ['pool share of the raise (%)', s.poolPercentOfRaise, cfg.liquidity.intendedPercentOfGrossRaise, 'the config says'],
  ];
  for (const [label, got, expected, how] of want) if (got !== expected) out.push(`the image says ${label} ${JSON.stringify(got)}; ${how} ${JSON.stringify(expected)}`);
  const drawn = Object.fromEntries((claim.locks ?? []).map((l) => [l.bucket, l.note]));
  for (const lock of plan.locks) {
    if (drawn[lock.bucket] !== shortNote(lock)) out.push(`"${lock.bucket}": the image says "${drawn[lock.bucket]}"; the locks plan says "${shortNote(lock)}"`);
  }
  if (Object.keys(drawn).length !== plan.locks.length) out.push(`the image draws ${Object.keys(drawn).length} locks; the plan has ${plan.locks.length}`);
  return out;
}

/** What a tick on the roadmap image may claim, each with the one reading that supports it. */
const TICKS = {
  '1B fixed supply minted': (record) => record.supply_tokens === '1000000000',
  'Mint and freeze authority revoked': (record) => record.mint_authority === null && record.freeze_authority === null,
};

/** Everything the roadmap image claims that the repo no longer says; empty means it still matches. */
export function roadmapDrift(claim, cfg, record) {
  const out = [];
  if (claim.v !== 1) out.push(`the record is version ${claim.v}; this test reads version 1`);
  const f = claim.facts ?? {};
  if (f.supplyTokens !== Number(record.supply_tokens)) out.push(`the image says a supply of ${f.supplyTokens}; the token record says ${record.supply_tokens}`);
  if (f.mintAuthorityRevoked !== (record.mint_authority === null)) out.push('the image and the token record disagree on whether the mint authority is revoked');
  if (f.freezeAuthorityRevoked !== (record.freeze_authority === null)) out.push('the image and the token record disagree on whether the freeze authority is revoked');
  const s = claim.sale ?? {};
  for (const [label, got, expected] of [['sale start', s.startUtc, cfg.schedule.startUtc], ['sale end', s.endUtc, cfg.schedule.endUtc],
    ['sale hours', s.hours, cfg.publicPhaseHours], ['hard cap (SOL)', s.hardCapSol, Number(cfg.sale.hardCapSol)]]) {
    if (got !== expected) out.push(`the image says ${label} ${JSON.stringify(got)}; the config says ${JSON.stringify(expected)}`);
  }
  const items = (claim.phases ?? []).flatMap((p) => p.items);
  for (const i of items.filter((x) => x.mark === 'done')) {
    const holds = TICKS[i.text];
    if (!holds) out.push(`the image ticks "${i.text}" as done and nothing in the repo shows it`);
    else if (!holds(record)) out.push(`the image ticks "${i.text}" as done, but the token record says otherwise`);
  }
  const sale = items.find((i) => /^Public sale /.test(i.text));
  if (!sale) out.push('the roadmap image has no public-sale line');
  else {
    if (!sale.text.includes(windowShort(cfg))) out.push(`the image's public-sale line does not say ${windowShort(cfg)}: "${sale.text}"`);
    if (!sale.text.includes(`${Number(cfg.sale.hardCapSol).toLocaleString('en-US')} SOL hard cap`)) out.push(`the image's public-sale line does not state the ${cfg.sale.hardCapSol} SOL hard cap: "${sale.text}"`);
  }
  return out;
}

const load = ({ file, keyword }) => {
  const png = readPng(fs.readFileSync(path.join(ASSETS, file)));
  return { png, claim: JSON.parse(png.text[keyword] ?? 'null') };
};
const tok = load(TOKENOMICS);
const road = load(ROADMAP);
const cert = load(CERTIFICATE);
const table = allocationFromRoadmap(fs.readFileSync(path.join(REPO, 'docs', 'TOKEN_ROADMAP.md'), 'utf8'));
const cfg = loadSmithiiConfig();
const record = loadTokenRecord();
const plan = loadLocksPlan();
const clone = (x) => structuredClone(x);

for (const [name, { file, keyword, size, asked }, { png, claim }] of [['tokenomics', TOKENOMICS, tok], ['roadmap', ROADMAP, road], ['vesting-certificate', CERTIFICATE, cert]]) {
  test(`the ${name} image is exactly the size the Smithii form asks for`, () => {
    assert.deepEqual([png.width, png.height], size, asked);
  });

  test(`the ${name} image carries the record of what it was drawn from`, () => {
    assert.ok(claim, `${file} has no "${keyword}" text chunk: it was not drawn by the generator, or the chunk was stripped (re-saving through an image host or editor strips it; host a copy, do not replace this file)`);
    assert.match(claim.generatedAt, /^\d{4}-\d{2}-\d{2}$/);
  });
}

for (const [name, { file }, { claim }] of [['tokenomics', TOKENOMICS, tok], ['vesting-certificate', CERTIFICATE, cert]]) {
  test(`the ${name} image: its figures are the roadmap table, the sale config and the locks plan as they stand`, () => {
    assert.deepEqual(tokenomicsDrift(claim, table, cfg, record, plan), [],
      `a source moved: redraw docs/assets/presale/${file}; the sale page's copy also has to be re-pointed`);
  });
}

test('the roadmap image: its window and caps are the config, and every tick is one the token record supports', () => {
  assert.deepEqual(roadmapDrift(road.claim, cfg, record), [],
    'a source moved, or a tick is claimed that nothing shows: redraw docs/assets/presale/rclaw_roadmap_1500x750.png');
});

test('the tokenomics comparison does fail when a figure moves (both arms)', () => {
  assert.deepEqual(tokenomicsDrift(tok.claim, table, cfg, record, plan), [], 'the clean claim passes, or every case below passes for the wrong reason');
  const drift = (mutate, { table: t = table, cfg: c = cfg, plan: p = plan } = {}) => {
    const claim = clone(tok.claim);
    mutate(claim);
    return tokenomicsDrift(claim, t, c, record, p).join('\n');
  };
  assert.match(drift((c) => { c.buckets[0].pct += 1; }), /the image says .*%, the roadmap says/);
  assert.match(drift((c) => { c.buckets.pop(); }), /draws \d+ buckets/);
  assert.match(drift((c) => { c.sale.ratePerSol = '30000'; }), /rate per SOL "30000"; the stored price makes it "30000.3"/);
  assert.match(drift((c) => { c.sale.hardCapSol = 6000; }), /hard cap \(SOL\) 6000/);
  assert.match(drift((c) => { c.sale.softCapSol = 900; }), /soft cap \(SOL\) 900/);
  assert.match(drift((c) => { c.sale.minBuySol = '0.5'; }), /minimum buy \(SOL\) "0\.5"/);
  assert.match(drift((c) => { c.sale.maxBuySol = '30'; }), /maximum buy \(SOL\) "30"/);
  assert.match(drift((c) => { c.sale.startUtc = '2026-10-16T14:00:00Z'; }), /sale start "2026-10-16T14:00:00Z"/);
  assert.match(drift((c) => { c.sale.endUtc = '2026-10-30T14:00:00Z'; }), /sale end "2026-10-30T14:00:00Z"/);
  assert.match(drift((c) => { c.sale.hours = 72; }), /sale hours 72/);
  assert.match(drift((c) => { c.sale.poolPercentOfRaise = 50; }), /pool share of the raise/);
  assert.match(drift((c) => { c.locks[1].note = '12-mo cliff, then 24-mo linear'; }), /the image says "12-mo cliff, then 24-mo linear"; the locks plan says/);
  assert.match(drift((c) => { c.locks.pop(); }), /the image says "undefined"|draws 2 locks/);
  assert.match(drift((c) => { c.locks.push({ bucket: 'Treasury / DAO', note: 'locked for 12 months' }); }), /draws 4 locks; the plan has 3/);
  assert.match(drift((c) => { c.v = 3; }), /the record is version 3/);
  assert.match(drift((c) => { c.supply = 2_000_000_000; }), /the image says a supply of 2000000000/);
  assert.match(drift((c) => { c.buckets[0].key = 'T'; }), /bucket "T" matches 2 roadmap rows/);
  assert.match(drift((c) => { c.buckets[0].key = 'Marketing'; }), /bucket "Marketing" matches 0 roadmap rows/);
  const tableMoved = clone(table);
  tableMoved.rows[1].tokens += 1;
  assert.match(drift(() => {}, { table: tableMoved }), /tokens, the roadmap says/);
  const cfgMoved = clone(cfg);
  cfgMoved.schedule.endUtc = '2026-10-30T14:00:00Z';
  assert.match(drift(() => {}, { cfg: cfgMoved }), /sale end/);
  const planMoved = clone(plan);
  planMoved.locks[0].steps = planMoved.locks[0].steps.slice(0, 4);
  assert.match(drift(() => {}, { plan: planMoved }), /"Community & ecosystem": the image says/);
});

test('the roadmap comparison does fail when a figure moves or a tick is not supported (both arms)', () => {
  assert.deepEqual(roadmapDrift(road.claim, cfg, record), [], 'the clean claim passes, or every case below passes for the wrong reason');
  const drift = (mutate, { cfg: c = cfg, record: r = record } = {}) => {
    const claim = clone(road.claim);
    mutate(claim);
    return roadmapDrift(claim, c, r).join('\n');
  };
  const sale = (c) => c.phases.flatMap((p) => p.items).find((i) => /^Public sale /.test(i.text));
  assert.match(drift((c) => { c.sale.startUtc = '2026-10-16T14:00:00Z'; }), /sale start/);
  assert.match(drift((c) => { c.sale.hours = 72; }), /sale hours 72/);
  assert.match(drift((c) => { c.sale.hardCapSol = 6000; }), /hard cap \(SOL\) 6000/);
  assert.match(drift((c) => { sale(c).text = 'Public sale 16–30 Oct 2026, 5,000 SOL hard cap'; }), /does not say 15–29 Oct 2026/);
  assert.match(drift((c) => { sale(c).text = 'Public sale 15–29 Oct 2026, 6,000 SOL hard cap'; }), /does not state the 5000 SOL hard cap/);
  assert.match(drift((c) => { c.phases[0].items[2].mark = 'done'; }), /ticks "Legal review, audit and published disclosures" as done and nothing in the repo shows it/);
  assert.match(drift((c) => { c.phases[0].items[0].text = '2B fixed supply minted'; }), /ticks "2B fixed supply minted" as done and nothing in the repo shows it/);
  const authorityBack = { ...record, mint_authority: 'So11111111111111111111111111111111111111112' };
  assert.match(drift(() => {}, { record: authorityBack }), /ticks "Mint and freeze authority revoked" as done, but the token record says otherwise/);
  assert.match(drift((c) => { c.facts.supplyTokens = 2_000_000_000; }), /the image says a supply of 2000000000/);
  assert.match(drift((c) => { c.facts.mintAuthorityRevoked = false; }), /disagree on whether the mint authority is revoked/);
  assert.match(drift((c) => { c.facts.freezeAuthorityRevoked = false; }), /disagree on whether the freeze authority is revoked/);
  assert.match(drift((c) => { c.v = 2; }), /the record is version 2/);
  const cfgMoved = clone(cfg);
  cfgMoved.schedule.endUtc = '2026-10-30T14:00:00Z';
  assert.match(drift(() => {}, { cfg: cfgMoved }), /sale end/);
});

test('windowShort words the window the way the image does, and across a month boundary', () => {
  assert.equal(windowShort(cfg), '15–29 Oct 2026');
  assert.equal(windowShort({ schedule: { startUtc: '2026-10-28T14:00:00Z', endUtc: '2026-11-11T14:00:00Z' } }), '28 Oct – 11 Nov 2026');
});
