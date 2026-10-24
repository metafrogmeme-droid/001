'use strict';
/**
 * Published ranking. Rows are basis points. A file that is missing is
 * not an empty ranking, and a row with no haircut is counted and left out.
 */

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT = path.join(__dirname, '..', '..', '..', 'bot', 'xnav', 'fixtures', 'snapshot_sample.json');

function snapshotPath() {
  if (process.env.XNAV_SNAPSHOT_PATH) return process.env.XNAV_SNAPSHOT_PATH;
  return DEFAULT;
}

function loadSnapshot(file) {
  const src = file || snapshotPath();
  let raw;
  try {
    raw = fs.readFileSync(src, 'utf8');
  } catch (err) {
    if (err && err.code === 'ENOENT') return { readable: false, reason: 'not_published', doc: null };
    return { readable: false, reason: 'unreadable', doc: null };
  }
  try {
    const doc = JSON.parse(raw);
    if (!doc || typeof doc !== 'object' || Array.isArray(doc) || !Array.isArray(doc.vaults)) {
      return { readable: false, reason: 'unreadable', doc: null };
    }
    return { readable: true, reason: null, doc };
  } catch (err) {
    return { readable: false, reason: 'unreadable', doc: null };
  }
}

function isAddress(value) {
  return typeof value === 'string' && /^0x[0-9a-fA-F]{40}$/.test(value);
}

function publicRow(row) {
  const haircut = row && Number.isInteger(row.haircut_bps) ? row.haircut_bps : null;
  return {
    address: isAddress(row && row.address) ? row.address.toLowerCase() : null,
    name: typeof (row && row.name) === 'string' ? row.name : null,
    haircut_bps: haircut,
    unfilled_bps: row && Number.isInteger(row.unfilled_bps) ? row.unfilled_bps : null,
    stress_bps: row && Number.isInteger(row.stress_bps) ? row.stress_bps : null,
    readable: haircut !== null,
  };
}

function rank(doc) {
  if (!doc || !Array.isArray(doc.vaults)) {
    return { readable: false, reason: 'unreadable', ranked: null, unreadable_vaults: null, caveat: null, as_of: null };
  }
  const ranked = [];
  let unread = 0;
  for (const row of doc.vaults) {
    const pub = publicRow(row);
    if (pub.readable !== true || pub.address == null) {
      unread += 1;
      continue;
    }
    ranked.push(pub);
  }
  ranked.sort((a, b) => {
    if (a.haircut_bps !== b.haircut_bps) return b.haircut_bps - a.haircut_bps;
    return a.address < b.address ? -1 : a.address > b.address ? 1 : 0;
  });
  return {
    readable: true,
    reason: null,
    ranked,
    unreadable_vaults: unread,
    caveat: typeof doc.caveat === 'string' ? doc.caveat : null,
    as_of: typeof doc.as_of === 'string' ? doc.as_of : null,
  };
}

function findVault(doc, vault) {
  if (!isAddress(vault) || !doc || !Array.isArray(doc.vaults)) return null;
  const want = vault.toLowerCase();
  for (const row of doc.vaults) {
    if (row && typeof row.address === 'string' && row.address.toLowerCase() === want) return row;
  }
  return null;
}

function detail(doc, vault) {
  const row = findVault(doc, vault);
  if (!row) return { readable: false, reason: 'not_in_snapshot', vault: isAddress(vault) ? vault.toLowerCase() : null };
  const pub = publicRow(row);
  const positions = [];
  if (Array.isArray(row.positions)) {
    for (const pos of row.positions) {
      if (!pos || typeof pos.coin !== 'string') continue;
      positions.push({
        coin: pos.coin,
        side: pos.side === 'long' || pos.side === 'short' ? pos.side : null,
        cost_bps: Number.isInteger(pos.cost_bps) ? pos.cost_bps : null,
      });
    }
  }
  return { ...pub, positions, caveat: typeof doc.caveat === 'string' ? doc.caveat : null, as_of: doc.as_of || null };
}

module.exports = { snapshotPath, loadSnapshot, isAddress, publicRow, rank, findVault, detail };
