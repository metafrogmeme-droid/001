// The tokenomics image the Smithii sale page shows, held against the table it was drawn from.
//
// docs/assets/presale/rclaw_tokenomics_1500x750.png is a second copy of the allocation table in
// docs/TOKEN_ROADMAP.md section 4 and of three sale terms in smithii.config.json. A second copy
// of a number drifts, and this one is public. The generator wrote the exact figures it drew into
// the PNG itself (a tEXt chunk named `runeclaw-tokenomics`); this test reads them back and holds
// them to the table and the config, so changing either without redrawing the image fails here.
//
// It checks what the image SAYS it was drawn from, not its pixels. That is the limit: it catches
// a table that moved under an unchanged image, which is how this kind of drift happens, and it
// does not catch someone retouching the picture by hand.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, '..', '..');
const IMAGE = path.join(REPO, 'docs', 'assets', 'presale', 'rclaw_tokenomics_1500x750.png');
const KEYWORD = 'runeclaw-tokenomics';

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

/** The allocation table in the roadmap's section 4, as rows. */
export function allocationFromRoadmap(md) {
  const start = md.indexOf('## 4. Tokenomics');
  const end = md.indexOf('\n## 5.', start);
  assert.ok(start >= 0 && end > start, 'section 4 of the roadmap is gone');
  const lines = md.slice(start, end).split('\n');
  const head = lines.findIndex((l) => /^\|\s*Bucket\s*\|\s*%\s*\|\s*Tokens\s*\|\s*Vesting\s*\|/.test(l));
  assert.ok(head >= 0, 'the allocation table header is gone');
  const rows = [];
  let total = null;
  for (const l of lines.slice(head + 2)) {
    if (!l.startsWith('|')) break;
    const cells = l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.replace(/[*`]/g, '').trim());
    const row = { label: cells[0], pct: Number(cells[1].replace('%', '')), tokens: Number(cells[2].replaceAll(',', '')) };
    if (row.label === 'Total') total = row;
    else rows.push(row);
  }
  return { rows, total };
}

/** Everything the image claims that the repo no longer says; empty means it still matches. */
export function driftBetween(claim, table, cfg, record) {
  const out = [];
  const supply = Number(record.supply_tokens);
  if (claim.supply !== supply) out.push(`the image says a supply of ${claim.supply}; the token record says ${supply}`);
  if (!table.total || table.total.tokens !== supply || table.total.pct !== 100) out.push(`the roadmap's Total row is not 100% of ${supply}`);
  if (claim.buckets.length !== table.rows.length) {
    out.push(`the image draws ${claim.buckets.length} buckets; the roadmap table has ${table.rows.length}`);
  }
  for (const b of claim.buckets) {
    const hits = table.rows.filter((r) => r.label.startsWith(b.key));
    if (hits.length !== 1) { out.push(`bucket "${b.key}" matches ${hits.length} roadmap rows`); continue; }
    if (hits[0].pct !== b.pct) out.push(`"${b.key}": the image says ${b.pct}%, the roadmap says ${hits[0].pct}%`);
    if (hits[0].tokens !== b.tokens) out.push(`"${b.key}": the image says ${b.tokens} tokens, the roadmap says ${hits[0].tokens}`);
  }
  const perSol = Math.round(1 / Number(cfg.sale.priceSol));
  if (claim.sale.perSol !== perSol) out.push(`the image says ${claim.sale.perSol} per SOL; the sale price makes it ${perSol}`);
  if (claim.sale.hardCapSol !== Number(cfg.sale.hardCapSol)) out.push(`the image says a ${claim.sale.hardCapSol} SOL hard cap; the config says ${cfg.sale.hardCapSol}`);
  if (claim.sale.hours !== cfg.publicPhaseHours) out.push(`the image says ${claim.sale.hours} hours; the config says ${cfg.publicPhaseHours}`);
  return out;
}

const png = readPng(fs.readFileSync(IMAGE));
const claim = JSON.parse(png.text[KEYWORD] ?? 'null');
const table = allocationFromRoadmap(fs.readFileSync(path.join(REPO, 'docs', 'TOKEN_ROADMAP.md'), 'utf8'));
const cfg = loadSmithiiConfig();
const record = loadTokenRecord();

test('the image is exactly the size the Smithii form asks for', () => {
  assert.deepEqual([png.width, png.height], [1500, 750], 'the form says "Tokenomics Image (1500x750)"');
});

test('the image carries the record of what it was drawn from', () => {
  assert.ok(claim, `the PNG has no "${KEYWORD}" text chunk: it was not drawn by the generator, or the chunk was stripped (re-saving through an image host or editor strips it; host a copy, do not replace this file)`);
  assert.equal(claim.v, 1);
  assert.match(claim.generatedAt, /^\d{4}-\d{2}-\d{2}$/);
});

test('the figures the image was drawn from are the roadmap table and the sale config as they stand', () => {
  assert.deepEqual(driftBetween(claim, table, cfg, record), [],
    'the table or the sale config moved: redraw docs/assets/presale/rclaw_tokenomics_1500x750.png and re-upload it where the sale page links it');
});

test('the comparison does fail when a figure moves (both arms)', () => {
  const moved = structuredClone(claim);
  moved.buckets[0].pct += 1;
  assert.match(driftBetween(moved, table, cfg, record).join('\n'), /the image says .*%, the roadmap says/);
  const fewer = structuredClone(claim);
  fewer.buckets.pop();
  assert.match(driftBetween(fewer, table, cfg, record).join('\n'), /draws \d+ buckets/);
  const sale = structuredClone(claim);
  sale.sale.perSol = 31000;
  assert.match(driftBetween(sale, table, cfg, record).join('\n'), /per SOL/);
  const cfgMoved = structuredClone(cfg);
  cfgMoved.sale.hardCapSol = 6000;
  assert.match(driftBetween(claim, table, cfgMoved, record).join('\n'), /hard cap/);
  const tableMoved = structuredClone(table);
  tableMoved.rows[1].tokens += 1;
  assert.match(driftBetween(claim, tableMoved, cfg, record).join('\n'), /tokens, the roadmap says/);
});
