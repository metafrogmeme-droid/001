'use strict';
/**
 * The conservation-brake status, read once from contracts/cgc/chains.json.
 *
 * The windows are the file's fields. This module does not recompute them
 * and it does not invent a base gap. A file that cannot be read is an
 * unreadable status, not an empty chain list.
 *
 * Bond sizes are not in the file and they are not added here. The Kelp
 * figures are a labelled scenario, not a measurement.
 */

const fs = require('node:fs');
const path = require('node:path');
const { BANNER } = require('./cgc_flag');

const CHAINS_FILE = path.join(__dirname, '..', '..', 'contracts', 'cgc', 'chains.json');
const WINDOW_KEYS = ['w_brake', 'w_prov', 'w_final', 'w_final_max'];

const KELP_SCENARIO = Object.freeze({
  kind: 'scenario',
  forged_release_units: 116500,
  dest_nonce: 308,
  source_outbound_nonce: 307,
  supplied_units: 89567,
  first_window_units: 11650,
  note: 'A scenario, not a measurement. It holds only when the base gap equals the forged release and the floor factor is 1/10.',
});

const PROOF_KINDS = Object.freeze({
  accepted: ['posted root', 'dispute game', 'watcher vote'],
  rejected: ['groth16', 'plonk', 'bridge verifier', 'unimplemented hash proof'],
});

function loadDocument(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!data || typeof data !== 'object' || !Array.isArray(data.chains)) return null;
    return data;
  } catch (err) {
    return null;
  }
}

/** True only for a chain id that chains.json lists as a testnet id. */
function isTestnetChainId(id, chains) {
  if (typeof id !== 'number' || !Number.isInteger(id)) return false;
  if (!Array.isArray(chains)) return false;
  return chains.some((row) => row && row.chain_id === id);
}

function windowsOf(row) {
  const out = {};
  for (const key of WINDOW_KEYS) {
    const value = row[key];
    if (!Number.isInteger(value)) return null;
    out[key] = value;
  }
  if (!(out.w_final_max > out.w_final)) return null;
  return out;
}

function publicChain(row) {
  if (!row || typeof row !== 'object') return null;
  const windows = windowsOf(row);
  if (!windows) return { key: row.key || null, name: row.name || null, windows: null };
  const chain = {
    key: typeof row.key === 'string' ? row.key : null,
    name: typeof row.name === 'string' ? row.name : null,
    family: typeof row.family === 'string' ? row.family : null,
    mode: typeof row.mode === 'string' ? row.mode : null,
    brake: typeof row.brake === 'boolean' ? row.brake : null,
    chain_id: Number.isInteger(row.chain_id) ? row.chain_id : null,
    windows,
  };
  if (Number.isInteger(row.game_type)) chain.game_type = row.game_type;
  if (typeof row.game_type_name === 'string') chain.game_type_name = row.game_type_name;
  return chain;
}

function inflowRows(doc) {
  const table = doc && doc.inflow_factor;
  if (!table || typeof table !== 'object') return null;
  const rows = [];
  for (const key of Object.keys(table)) {
    const row = table[key];
    if (!row || !Number.isInteger(row.num) || !Number.isInteger(row.den) || row.den === 0) {
      return null;
    }
    const floor = Number(key);
    if (!Number.isInteger(floor)) return null;
    rows.push({ floor, num: row.num, den: row.den });
  }
  rows.sort((a, b) => a.floor - b.floor);
  return rows;
}

/**
 * The status an admin is allowed to see. `preview` selects the banner.
 * An unreadable file omits the chain list rather than presenting [].
 */
function statusPayload({ preview, file } = {}) {
  const onPreview = preview === true;
  const body = {
    preview: onPreview,
    banner: onPreview ? BANNER : null,
    server_signs: false,
    wrapper: { symbol: 'gwrsETH', testnet_only: true, public: false },
    proof_kinds: PROOF_KINDS,
    kelp: KELP_SCENARIO,
  };
  const doc = loadDocument(file || CHAINS_FILE);
  if (!doc) {
    body.unreadable = true;
    body.chains = null;
    body.inflow = null;
    body.note = 'The chain list could not be read.';
    return body;
  }
  body.unreadable = false;
  body.dated = typeof doc.dated === 'string' ? doc.dated : null;
  body.chains = doc.chains.map(publicChain);
  body.inflow = inflowRows(doc);
  body.inflow_unread = 'An unreadable floor is not a row in this table. It refuses inflow.';
  return body;
}

module.exports = {
  CHAINS_FILE,
  KELP_SCENARIO,
  PROOF_KINDS,
  loadDocument,
  isTestnetChainId,
  statusPayload,
};
