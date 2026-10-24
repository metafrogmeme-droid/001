'use strict';
/**
 * GET /api/roots — Provable Calls v3, the daily seal roots feed (public).
 *
 * One Merkle root per completed UTC day, covering EVERY seal minted that day
 * (engine calls, arena paper trades, agent claims, scan seals, the Daily
 * Duel's rounds, calls and settlements). Mirror this feed anywhere — a tweet,
 * a git repo, a chain memo — and you hold an independent timestamp: no call
 * can be back-inserted into a mirrored day without changing its root.
 * Days with zero seals are omitted, never invented.
 *
 * ANCHORS, ON MORE THAN ONE CHAIN (2026-10-19). A completed day's root may be
 * anchored on Base and on the DogeOS testnet, each by anyone's own wallet:
 * the server hands out the exact zero-value transaction (dry run), never
 * signs, and records a hash only after the chain itself confirmed the
 * calldata. `?chain=` names the chain on the three anchor routes; the
 * registry (`/chains`) is lib/root_anchor.js's one reading of each chain's
 * id, RPCs and explorer, so a page or an app asks here rather than carrying
 * literals of its own.
 */
const express = require('express');
const { listRoots, rootForDay, anchorsFor, recordAnchor } = require('../lib/seal_roots');
const { buildAnchorPlan, verifyAnchor, publicChains, chainInfo, avalancheEnabled, DEFAULT_CHAIN, easStampOf, caseSensitiveTx } = require('../lib/root_anchor');
const { flag_on_for } = require('../lib/admin_preview');
const { authMiddleware } = require('../auth');
const rootOts = require('../lib/root_ots');
const ots = require('../lib/opentimestamps');
const { rateLimit, userKey, ipKey } = require('../lib/rate_limit');
const { anchorCheck, bitcoinCheck, dayQuorum } = require('../lib/day_quorum');

const router = express.Router();

let cache = { at: 0, body: null };

const AVALANCHE_PREVIEW_REFUSAL = {
  error: 'Admin preview does not sign or move funds on Avalanche mainnet. The chain is listed so it can be read. A signable plan is issued only after AVALANCHE_ANCHOR_ENABLED is on for everyone.',
  admin_preview: true,
  chain: 'avalanche',
};

/** Public registry, plus Avalanche for an admin while the public flag is off.
 * The admin copy is not cached: a shared cache would hand the chain to everyone. */
async function chainsView(req) {
  const pub = publicChains();
  if (avalancheEnabled()) return { chains: pub, preview: false };
  if (!(await flag_on_for(req, 'AVALANCHE_ANCHOR_ENABLED'))) return { chains: pub, preview: false };
  const opened = publicChains(Object.assign({}, process.env, { AVALANCHE_ANCHOR_ENABLED: '1' }));
  const extra = opened.find((c) => c.key === 'avalanche');
  if (!extra) return { chains: pub, preview: false };
  return {
    chains: pub.concat([Object.assign({}, extra, { admin_preview: true, signing: false })]),
    preview: true,
  };
}

/** The chain a request names. Avalanche while the public flag is off is visible
 * to an admin (`preview`) and unknown to everyone else. */
async function chainFor(req) {
  const raw = (req.query && req.query.chain) || (req.body && req.body.chain) || DEFAULT_CHAIN;
  const key = String(raw);
  const known = chainInfo(key);
  if (known) return { chain: known, preview: false };
  if (key !== 'avalanche') return { chain: null, preview: false };
  if (!(await flag_on_for(req, 'AVALANCHE_ANCHOR_ENABLED'))) return { chain: null, preview: false };
  const env = Object.assign({}, process.env, { AVALANCHE_ANCHOR_ENABLED: '1' });
  return { chain: chainInfo('avalanche', env), preview: true };
}

function refuseMainnetPreview(res) {
  return res.status(403).json(AVALANCHE_PREVIEW_REFUSAL);
}

router.get('/', async (req, res) => {
  try {
    if (!cache.body || Date.now() - cache.at > 60000) {
      const roots = await listRoots(30);
      // Bitcoin (OpenTimestamps), from our own row: whether the day was
      // submitted and the height this server checked. A page re-checks it
      // live (/ots/:day) before painting it as in Bitcoin.
      for (const r of roots) r.ots = await otsSummary(r.day);
      cache = { at: Date.now(), body: {
        construction: require('../lib/sealroot').CONSTRUCTION,
        chains: publicChains(),
        roots,
      } };
    }
    const view = await chainsView(req);
    const body = Object.assign({}, cache.body, { chains: view.chains });
    res.set('Cache-Control', view.preview ? 'private, no-store' : 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Seal roots error:', err.stack || err.message);
    res.status(500).json({ error: 'Roots feed unavailable' });
  }
});

// GET /api/roots/chains — the chains a root may be anchored on: id, name,
// RPCs, explorer, native currency. What a wallet is asked to add, read from
// the registry the verifier uses, so the two cannot name different chains.
router.get('/chains', async (req, res) => {
  try {
    const view = await chainsView(req);
    res.set('Cache-Control', view.preview ? 'private, no-store' : 'public, max-age=300');
    res.json({ default: DEFAULT_CHAIN, chains: view.chains });
  } catch (e) {
    res.status(500).json({ error: 'Chains unavailable' });
  }
});

/** The feed's Bitcoin line for a day: null when the row could not be read. */
async function otsSummary(day) {
  try {
    const row = await rootOts.rowFor(day);
    return row ? { submitted: true, bitcoin_height: row.bitcoin_height == null ? null : Number(row.bitcoin_height) }
      : { submitted: false, bitcoin_height: null };
  } catch (e) { return null; }
}

// GET /api/roots/ots/:day — the day's root in Bitcoin, through OpenTimestamps,
// re-checked live against Bitcoin block headers (lib/root_ots.js checkProof).
// The proof and the statement it timestamps download beside it, for
// `ots verify RCROOT1-<day>.txt` against your own Bitcoin node.
router.get('/ots/:day', async (req, res) => {
  try {
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const body = await bitcoinCheck(row);
    res.set('Cache-Control', body.status === 'verified' ? 'public, max-age=300' : 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Bitcoin timestamp check error:', err.name || 'Error');
    res.status(500).json({ error: 'Bitcoin timestamp check unavailable' });
  }
});

// GET /api/roots/ots/:day/proof — the detached OpenTimestamps proof file.
router.get('/ots/:day/proof', async (req, res) => {
  try {
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const rec = await rootOts.rowFor(row.day);
    if (!rec || rec.root !== row.root) return res.status(404).json({ error: 'No Bitcoin timestamp for that day yet' });
    res.set('Content-Type', 'application/octet-stream');
    res.set('Content-Disposition', `attachment; filename="RCROOT1-${row.day}.txt.ots"`);
    res.send(Buffer.from(String(rec.proof), 'base64'));
  } catch (err) {
    console.error('Bitcoin proof download error:', err.name || 'Error');
    res.status(500).json({ error: 'Proof unavailable' });
  }
});

// GET /api/roots/ots/:day/statement — the exact bytes the proof timestamps.
router.get('/ots/:day/statement', async (req, res) => {
  try {
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    res.set('Content-Type', 'text/plain; charset=utf-8');
    res.set('Content-Disposition', `attachment; filename="RCROOT1-${row.day}.txt"`);
    res.send(ots.statementFor(row.day, row.root));
  } catch (err) {
    console.error('Bitcoin statement download error:', err.name || 'Error');
    res.status(500).json({ error: 'Statement unavailable' });
  }
});

// GET /api/roots/anchor-plan/:day?chain= — the exact zero-value transaction
// that anchors a completed day's root on that chain. DRY RUN: the server
// never signs.
router.get('/anchor-plan/:day', async (req, res) => {
  try {
    const found = await chainFor(req);
    if (!found.chain) return res.status(400).json({ error: 'Unknown chain' });
    if (found.preview) return refuseMainnetPreview(res);
    const chain = found.chain;
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day (still open, empty, or malformed)' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (have) {
      return res.json({ already_anchored: true, day: row.day, root: row.root, chain: chain.key, anchor_tx: have.tx });
    }
    if (chain.method === 'eas_timestamp') {
      // EAS keeps one stamp per root: a wallet asked to stamp a root that is
      // already stamped would pay for a transaction that reverts. Ask first.
      const s = await easStampOf(row.root, undefined, chain.key);
      if (s.status === 'stamped') {
        return res.json({ already_stamped: true, day: row.day, root: row.root, chain: chain.key, stamped_at: s.stamped_at });
      }
      if (s.status !== 'unstamped') {
        return res.status(503).json({ error: `Could not read ${chain.name} to see whether the root is stamped — try again shortly`,
          reason: s.reason });
      }
    }
    res.json(buildAnchorPlan(row.day, row.root, chain.key));
  } catch (err) {
    console.error('Anchor plan error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor plan unavailable' });
  }
});

// GET /api/roots/verify/:day?chain= — re-verify a recorded anchor against the
// chain, LIVE. Public and cached: this is the self-audit surface — anyone can
// ask the server to re-check its own claim, and the server reports exactly
// what the chain said, unknown included.
router.get('/verify/:day', async (req, res) => {
  try {
    const found = await chainFor(req);
    if (!found.chain) return res.status(400).json({ error: 'Unknown chain' });
    const chain = found.chain;
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (!have) {
      return res.json({ day: row.day, root: row.root, chain: chain.key, chain_name: chain.name, status: 'unanchored' });
    }
    const body = await anchorCheck(row, chain, have);
    res.set('Cache-Control', 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Anchor verify error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor verify unavailable' });
  }
});

// GET /api/roots/quorum/:day — the day's Anchor Quorum (lib/anchor_quorum.js):
// every recorded anchor and the Bitcoin timestamp, each checked live by the
// same checks /verify and /ots answer, and whether at least k INDEPENDENT
// chains hold the root. One request per day for a page or an app; time-
// proven, not performance.
router.get('/quorum/:day', rateLimit({ name: 'roots.quorum', windowMs: 60000, max: 30, key: ipKey }), async (req, res) => {
  try {
    const row = await rootForDay(String(req.params.day || ''));
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const body = await dayQuorum(row);
    res.set('Cache-Control', 'public, max-age=60');
    res.json(body);
  } catch (err) {
    console.error('Anchor quorum error:', err.name || 'Error');
    res.status(500).json({ error: 'Anchor quorum unavailable', exception: err.name || 'Error' });
  }
});

// POST /api/roots/anchor { day, tx_hash, chain } — record an anchor AFTER
// verifying it against the chain. The chain is the gatekeeper: a hash whose
// calldata does not equal the tagged root payload is refused, whoever
// submits it. On an unreadable chain the answer is "try again", never a
// guess. First anchor per chain wins — immutable like the root itself.
router.post('/anchor', authMiddleware, rateLimit({ name: 'roots.anchor', windowMs: 60000, max: 6, key: userKey }), async (req, res) => {
  try {
    const found = await chainFor(req);
    if (!found.chain) return res.status(400).json({ error: 'Unknown chain' });
    if (found.preview) return refuseMainnetPreview(res);
    const chain = found.chain;
    const day = String((req.body || {}).day || '');
    // A hex hash is lower-cased; a Solana signature is base58, where case is
    // the value, so it is kept as given (lib/root_anchor.js caseSensitiveTx).
    const rawTx = String((req.body || {}).tx_hash || '').trim();
    const txHash = caseSensitiveTx(chain) ? rawTx : rawTx.toLowerCase();
    const row = await rootForDay(day);
    if (!row) return res.status(404).json({ error: 'No root for that day' });
    const have = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
    if (have) {
      return res.status(409).json({ error: 'Already anchored', chain: chain.key, anchor_tx: have.tx });
    }
    const v = await verifyAnchor(txHash, row.day, row.root, undefined, chain.key);
    if (v.status === 'unknown') {
      return res.status(503).json({ error: `Could not read ${chain.name} to verify — try again shortly`, reason: v.reason });
    }
    if (v.status !== 'verified') {
      return res.status(400).json({ error: 'That transaction does not anchor this root', reason: v.reason });
    }
    const recorded = await recordAnchor(day, chain.key, txHash, v);
    if (!recorded) {
      // Somebody's anchor landed between the check above and this write.
      const now = (await anchorsFor(row.day, row)).find((a) => a.chain === chain.key);
      return res.status(409).json({ error: 'Already anchored', chain: chain.key, anchor_tx: now ? now.tx : null });
    }
    cache = { at: 0, body: null };            // feed shows the anchor next read
    res.json({ ok: true, day, chain: chain.key, chain_name: chain.name, anchor_tx: txHash,
      explorer: chain.explorer_tx + txHash, block_time: v.block_time, from: v.from });
  } catch (err) {
    console.error('Anchor submit error:', err.stack || err.message);
    res.status(500).json({ error: 'Anchor submit failed' });
  }
});

module.exports = router;
