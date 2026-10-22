'use strict';
/**
 * Each completed day's seal root, timestamped in Bitcoin through
 * OpenTimestamps (lib/opentimestamps.js), kept in `seal_root_ots`.
 *
 * Every ten minutes one instance (lib/shared_schedule.js):
 *   1. submits each recent day that has a root and no proof yet: an HTTP POST
 *      of SHA-256("RCROOT1:<day>:<root>") to the public calendars, which
 *      answer a pending proof. No key, no wallet, no transaction from here.
 *   2. asks the calendars to complete each proof not yet in a Bitcoin block.
 *      A calendar that has reached Bitcoin answers the rest of the path; the
 *      block's header is then read from Bitcoin header APIs and compared, and
 *      only a proof that leads to that block's Merkle root, six blocks deep,
 *      gets its height written.
 *
 * The row is the proof file; GET /api/roots/ots/:day re-checks it live, as
 * the anchor cells do, so a page never paints Bitcoin from our column alone.
 */
const { pool } = require('../db');
const ots = require('./opentimestamps');
const { listRoots } = require('./seal_roots');
const { requiredOf } = require('./reader_agreement');

const JOB = 'seal_root_ots';
const health = require('./watch_health').register(JOB);
const INTERVAL_MS = 10 * 60 * 1000;
/** Days back the job stamps; older roots stay as they are. */
const RECENT_DAYS = 7;
/** Bitcoin's customary six confirmations before a block is called settled. */
const BITCOIN_DEPTH = 6;

const b64 = (buf) => Buffer.from(buf).toString('base64');

/** The row for `day`, or null. Throws when the database does not answer. */
async function rowFor(day) {
  const [rows] = await pool.execute(
    'SELECT day, root, proof, calendars, bitcoin_height, block_time, submitted_at, checked_at FROM seal_root_ots WHERE day = ?', [day]);
  return rows && rows[0] ? rows[0] : null;
}

/** Submit `day`'s root. → true when a proof was written, false when one already was. */
async function stampDay(day, root, { fetchImpl, now = new Date() } = {}) {
  const digest = ots.digestFor(day, root);
  const { timestamp, calendars } = await ots.submit(digest, { fetchImpl });
  const [res] = await pool.execute(
    'INSERT IGNORE INTO seal_root_ots (day, root, proof, calendars, submitted_at) VALUES (?, ?, ?, ?, ?)',
    [day, root, b64(ots.writeProofFile(digest, timestamp)), calendars.join(','), now]);
  return Boolean(res && res.affectedRows === 1);
}

/**
 * The Bitcoin tip height, read from every header source: the LOWEST of the
 * answers, and null unless as many sources answered as a verdict needs
 * (lib/reader_agreement.js). One source that reports a higher tip than the
 * chain has would otherwise make a shallow block look six deep.
 */
async function bitcoinTip(fetchImpl, timeoutMs = 15000) {
  timeoutMs = timeoutMs || 15000;
  const f = fetchImpl || global.fetch;
  const apis = ots.BITCOIN_HEADER_APIS;
  const tips = (await Promise.all(apis.map(async (api) => {
    try {
      // Bounded, like every other read in this job. Unbounded, one header
      // source that accepted the connection and never answered held the pass
      // open forever, and the schedule never starts a pass while one runs.
      const r = await ots.fetchBytes(f, `${api}/blocks/tip/height`, { method: 'GET' }, timeoutMs);
      if (r.status !== 200) return null;
      const n = Number(r.body.toString('utf8').trim());
      return Number.isInteger(n) && n > 0 ? n : null;
    } catch (e) { return null; }
  }))).filter((n) => n !== null);
  return tips.length >= requiredOf(apis.length) ? Math.min(...tips) : null;
}

/**
 * Check a stored proof against Bitcoin, live.
 * → { status: 'verified', height, block_time }   in a block at least six deep
 * → { status: 'pending', reason? }               not in Bitcoin yet, or not six deep yet
 * → { status: 'mismatch', reason }               the proof does not lead to its block, or is not this root's
 * → { status: 'unknown', reason }                unreadable proof or headers — not a verdict
 */
async function checkProof(row, { fetchImpl, timeoutMs } = {}) {
  let file;
  try { file = ots.parseProofFile(Buffer.from(String(row.proof || ''), 'base64')); } catch (e) {
    return { status: 'unknown', reason: `the stored proof could not be read (${e.message || e.name})` };
  }
  if (!file.digest.equals(ots.digestFor(row.day, row.root))) {
    return { status: 'mismatch', reason: 'the stored proof is for another statement' };
  }
  const v = await ots.verifyBitcoin(file.timestamp, { fetchImpl, ...(timeoutMs ? { timeoutMs } : {}) });
  if (v.status !== 'verified') return v;
  const tip = await bitcoinTip(fetchImpl, timeoutMs);
  if (tip === null) return { status: 'unknown', reason: 'the Bitcoin tip height could not be read' };
  const deep = tip - v.height + 1;
  if (deep < BITCOIN_DEPTH) {
    return { status: 'pending', reason: `${Math.max(deep, 0)} of the ${BITCOIN_DEPTH} confirmations Bitcoin needs` };
  }
  return v;
}

/** Ask the calendars to complete `row`'s proof; store it, and its height once checked. → the check's verdict. */
async function upgradeDay(row, { fetchImpl, now = new Date(), timeoutMs } = {}) {
  let file;
  try { file = ots.parseProofFile(Buffer.from(String(row.proof || ''), 'base64')); } catch (e) {
    return { status: 'unknown', reason: 'the stored proof could not be read' };
  }
  const grew = await ots.upgrade(file.timestamp, { fetchImpl });
  const proof = grew ? b64(ots.writeProofFile(file.digest, file.timestamp)) : row.proof;
  // A grown proof is kept before it is checked: the calendars' answer is the
  // part that cannot be fetched again for free, and a check that fails or
  // stalls must not throw it away.
  // (The job only upgrades rows not yet in a block, so the height it writes
  // here is the NULL it read.)
  if (grew) {
    await pool.execute(
      'UPDATE seal_root_ots SET proof = ?, bitcoin_height = ?, block_time = ?, checked_at = ? WHERE day = ? AND root = ?',
      [proof, null, null, now, row.day, row.root]);
  }
  const v = await checkProof({ ...row, proof }, { fetchImpl, timeoutMs });
  const height = v.status === 'verified' ? v.height : null;
  const blockTime = v.status === 'verified' ? new Date(v.block_time) : null;
  if (grew || height !== null) {
    await pool.execute(
      'UPDATE seal_root_ots SET proof = ?, bitcoin_height = ?, block_time = ?, checked_at = ? WHERE day = ? AND root = ?',
      [proof, height, blockTime, now, row.day, row.root]);
  }
  return v;
}

/** One pass of the job. → { stamped, upgraded } counts, for the log and the tests. */
async function runOnce(opts = {}) {
  try {
    const out = await pass(opts);
    health.ok();
    return out;
  } catch (e) {
    health.failed(e);
    throw e;
  }
}

async function pass({ fetchImpl, now = new Date() } = {}) {
  const out = { stamped: 0, upgraded: 0 };
  const roots = (await listRoots(RECENT_DAYS)) || [];
  for (const r of roots) {
    try {
      if (await rowFor(r.day)) continue;
      if (await stampDay(r.day, r.root, { fetchImpl, now })) out.stamped += 1;
    } catch (e) { /* that day is tried again next pass */ }
  }
  let pending = [];
  try {
    [pending] = await pool.execute(
      'SELECT day, root, proof, calendars, bitcoin_height FROM seal_root_ots WHERE bitcoin_height IS NULL ORDER BY day DESC LIMIT 14');
  } catch (e) { pending = []; }
  for (const row of pending || []) {
    try {
      const v = await upgradeDay(row, { fetchImpl, now });
      if (v.status === 'verified') out.upgraded += 1;
    } catch (e) { /* next pass */ }
  }
  return out;
}

let stop = null;
function startRootOts(intervalMs = INTERVAL_MS) {
  if (stop) return;
  stop = require('./shared_schedule').every(JOB, intervalMs, () => runOnce());
}

module.exports = { JOB, INTERVAL_MS, RECENT_DAYS, BITCOIN_DEPTH, rowFor, stampDay, upgradeDay, checkProof, bitcoinTip, runOnce, startRootOts };
