'use strict';
/**
 * Panic Button receipt. Off unless PANIC_BUTTON is 1 or true.
 *
 * The caller reports transaction hashes their own wallet already broadcast.
 * This seals a time and those hashes, and nothing else. It does not sign,
 * does not broadcast, and does not store an address.
 *
 * Public, and not the read-only lookup. Three limits, each tested:
 * RECEIPT_PER_MINUTE per IP, at most MAX_HASHES hashes, and each hash is
 * 0x plus 64 hex digits.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { enabled, NOT_AVAILABLE } = require('../lib/panic_flag');
const panic = require('./panic');

const MAX_HASHES = 40;
const RECEIPT_PER_MINUTE = 10;
const HASH_RE = /^0x[0-9a-fA-F]{64}$/;

const router = express.Router();
router.use(panic.capBody);
router.use(rateLimit({
  name: 'panic.receipt',
  windowMs: 60000,
  max: RECEIPT_PER_MINUTE,
  key: ipKey,
}));

function cleanHashes(body) {
  const hashes = body && body.hashes;
  if (!Array.isArray(hashes) || hashes.length < 1 || hashes.length > MAX_HASHES) {
    return null;
  }
  const clean = [];
  for (const item of hashes) {
    if (typeof item !== 'string' || !HASH_RE.test(item)) return null;
    clean.push(item.toLowerCase());
  }
  return clean;
}

let runner = null;
function setRunner(fn) { runner = typeof fn === 'function' ? fn : null; }

function off(res) {
  res.set('Cache-Control', 'no-store');
  return res.type('text/plain').send(NOT_AVAILABLE);
}

router.post('/', async (req, res) => {
  if (!enabled()) return off(res);
  const hashes = cleanHashes(req.body);
  if (!hashes) {
    res.set('Cache-Control', 'no-store');
    return res.status(400).json({ message: 'bad hashes' });
  }
  try {
    const run = runner || panic.run;
    const result = await run({ op: 'receipt', hashes: hashes });
    if (!result || result.error || !result.cleaned_at || !Array.isArray(result.hashes)) {
      res.set('Cache-Control', 'no-store');
      return res.status(400).json({ message: 'bad hashes' });
    }
    res.set('Cache-Control', 'no-store');
    res.json({ cleaned_at: result.cleaned_at, hashes: result.hashes });
  } catch (e) {
    console.error('Panic receipt error:', e && e.name ? e.name : 'Error');
    res.status(500).json({ message: 'Panic read unavailable' });
  }
});

module.exports = router;
module.exports.setRunner = setRunner;
module.exports.maxHashes = MAX_HASHES;
module.exports.perMinute = RECEIPT_PER_MINUTE;
