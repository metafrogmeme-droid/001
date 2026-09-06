/**
 * Cross-language envelope encryption for pending exchange credentials.
 *
 * Exchange API keys submitted on the website are encrypted AT REST here before
 * touching MySQL, and decrypted by the PYTHON bot when it pulls them (the bot
 * then re-stores them in its own Fernet store keyed by telegram_id and the
 * pending row is deleted). AES-256-GCM is used because both Node (crypto) and
 * Python (cryptography.AESGCM) implement it natively — no Fernet dep in Node.
 *
 * TWO ENVELOPES, AND `v: 2` IS THE ONE TO REACH FOR.
 *
 * `v: 1` is the shared-key envelope: `{ v, iv, tag, ct }` under WEB_CREDS_KEY,
 * a 32-byte secret an operator had to generate and then set IDENTICALLY in two
 * deployments' environments. Until both were set the connect form answered 503
 * — which is what "I typed my keys in and nothing saved" turned out to be. It
 * also gave the website a key it never needed: this app could read back every
 * submission it stored.
 *
 * `v: 2` is SEALED to the bot's own key and needs nothing configured by hand.
 * The bot generates an RSA keypair on first use and publishes the public half
 * over the already-authenticated sync channel (POST /api/bot/sync/credentials/
 * sealing-key); `sealJSON` wraps a fresh AES-256-GCM content key with
 * RSA-OAEP(SHA-256) so only the bot can open the result:
 *
 *     { v: 2, alg: "rsa-oaep-sha256+aes-256-gcm", kid, ek, iv, tag, ct }
 *
 * `kid` is SHA-256 over the SPKI DER, first 16 hex characters — computed the
 * same way by `bot/utils/creds_sealing.py`, so a submission sealed to a key
 * the bot no longer holds fails with a message naming that, not a bare
 * padding error. `tag` is split out of `ct` in both versions because Node's
 * GCM API hands it back separately; Python re-joins them.
 *
 * WEB_CREDS_KEY is still read — `lib/totp.js` seals 2FA secrets at rest with
 * it, and rows an older website wrote are still openable — so nothing here is
 * removed. It is simply no longer what the connect form waits for.
 */

const crypto = require('crypto');

/** Named in the envelope, so a change on either side is a loud mismatch. */
const SEAL_ALG = 'rsa-oaep-sha256+aes-256-gcm';

/** Refuse to seal to anything weaker than the bot's own 3072-bit key class. */
const MIN_RSA_BITS = 2048;

function loadKey() {
  const raw = process.env.WEB_CREDS_KEY || '';
  if (!raw) return null;
  // Accept standard or url-safe base64.
  const b64 = raw.replace(/-/g, '+').replace(/_/g, '/');
  let key;
  try { key = Buffer.from(b64, 'base64'); } catch (e) { return null; }
  if (key.length !== 32) return null;
  return key;
}

function isConfigured() {
  return loadKey() !== null;
}

/** Encrypt a JS object to a JSON envelope string. Throws if WEB_CREDS_KEY unusable. */
function encryptJSON(obj) {
  const key = loadKey();
  if (!key) throw new Error('WEB_CREDS_KEY missing or not a 32-byte base64 key');
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const pt = Buffer.from(JSON.stringify(obj), 'utf8');
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const tag = cipher.getAuthTag();
  return JSON.stringify({
    v: 1,
    iv: iv.toString('base64'),
    tag: tag.toString('base64'),
    ct: ct.toString('base64'),
  });
}

/** Decrypt a JSON envelope string back to the object (used by tests / Node-side). */
function decryptJSON(envelope) {
  const key = loadKey();
  if (!key) throw new Error('WEB_CREDS_KEY missing or not a 32-byte base64 key');
  const e = typeof envelope === 'string' ? JSON.parse(envelope) : envelope;
  const iv = Buffer.from(e.iv, 'base64');
  const tag = Buffer.from(e.tag, 'base64');
  const ct = Buffer.from(e.ct, 'base64');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  const pt = Buffer.concat([decipher.update(ct), decipher.final()]);
  return JSON.parse(pt.toString('utf8'));
}

/**
 * A public key's fingerprint: SHA-256 over its SPKI DER, first 16 hex chars.
 *
 * Computed from the DER and never from the PEM text, because PEM is not
 * canonical — a re-wrapped line length or a trailing newline is the same key
 * and would be a different fingerprint. `bot/utils/creds_sealing.kid()` hashes
 * the same bytes, which is what lets the two runtimes agree.
 *
 * Throws on anything that is not a usable RSA public key, so a malformed or
 * downgraded key is refused at the door rather than sealed to.
 */
function kidFor(pem) {
  const key = publicKeyFrom(pem);
  const der = key.export({ type: 'spki', format: 'der' });
  return crypto.createHash('sha256').update(der).digest('hex').slice(0, 16);
}

/** Parse + VET a published key. Throws with a reason; never returns null. */
function publicKeyFrom(pem) {
  const key = crypto.createPublicKey(String(pem || ''));
  if (key.asymmetricKeyType !== 'rsa') {
    throw new Error(`sealing key must be RSA, got ${key.asymmetricKeyType}`);
  }
  // A key small enough to factor would turn the seal into decoration. The
  // channel that publishes it is bot-secret authed, so this is depth rather
  // than the only line — but a silent downgrade is exactly the failure that
  // is invisible afterwards.
  const bits = (key.asymmetricKeyDetails || {}).modulusLength || 0;
  if (bits < MIN_RSA_BITS) {
    throw new Error(`sealing key is ${bits}-bit; ${MIN_RSA_BITS} is the minimum`);
  }
  return key;
}

/**
 * Seal an object to the BOT's public key — the `v: 2` envelope.
 *
 * `kid` is optional and, when given, must match the fingerprint computed from
 * the PEM. That is not belt-and-braces: the two travel together in one stored
 * record, and a record whose halves disagree means something rewrote one of
 * them. Sealing to the PEM anyway and stamping the sent `kid` would produce an
 * envelope the bot rejects, at submit time, on the user's screen.
 *
 * The content key is fresh per submission and never leaves this function, so
 * this app holds nothing that can reopen what it just stored.
 */
function sealJSON(obj, opts = {}) {
  const pub = publicKeyFrom(opts.pem);
  const computed = kidFor(opts.pem);
  if (opts.kid && String(opts.kid) !== computed) {
    throw new Error(`sealing key record disagrees with itself: kid ${opts.kid} `
      + `for a key whose fingerprint is ${computed}`);
  }
  const contentKey = crypto.randomBytes(32);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', contentKey, iv);
  const pt = Buffer.from(JSON.stringify(obj), 'utf8');
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const tag = cipher.getAuthTag();
  const ek = crypto.publicEncrypt(
    { key: pub, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: 'sha256' },
    contentKey);
  return JSON.stringify({
    v: 2,
    alg: SEAL_ALG,
    kid: computed,
    ek: ek.toString('base64'),
    iv: iv.toString('base64'),
    tag: tag.toString('base64'),
    ct: ct.toString('base64'),
  });
}

module.exports = {
  isConfigured, encryptJSON, decryptJSON,
  sealJSON, kidFor, publicKeyFrom, SEAL_ALG, MIN_RSA_BITS,
};
