// The allocation table of docs/TOKEN_ROADMAP.md section 4, as rows.
//
// One reader for it: the image test and the locks plan both need these numbers, and a second
// parser is a second answer that drifts from the first.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROADMAP = path.join(HERE, '..', '..', 'docs', 'TOKEN_ROADMAP.md');

/**
 * Rows { label, pct, tokens, vesting } of the table under "## 4. Tokenomics", and its Total row.
 * `vesting` is the table's own words for the schedule, with markdown emphasis removed.
 */
export function allocationFromRoadmap(md) {
  const start = md.indexOf('## 4. Tokenomics');
  const end = md.indexOf('\n## 5.', start);
  if (!(start >= 0 && end > start)) throw new Error('section 4 of the roadmap is gone');
  const lines = md.slice(start, end).split('\n');
  const head = lines.findIndex((l) => /^\|\s*Bucket\s*\|\s*%\s*\|\s*Tokens\s*\|\s*Vesting\s*\|/.test(l));
  if (head < 0) throw new Error('the allocation table header is gone');
  const rows = [];
  let total = null;
  for (const l of lines.slice(head + 2)) {
    if (!l.startsWith('|')) break;
    const cells = l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.replace(/[*`]/g, '').trim());
    const row = { label: cells[0], pct: Number(cells[1].replace('%', '')), tokens: Number(cells[2].replaceAll(',', '')), vesting: cells[3] };
    if (row.label === 'Total') total = row;
    else rows.push(row);
  }
  return { rows, total };
}

export function loadAllocation(file = ROADMAP) {
  return allocationFromRoadmap(fs.readFileSync(file, 'utf8'));
}
