'use strict';
/**
 * Publishing a Mini App makes two claims at one URL, and only one of them is
 * ours to make.
 *
 *   `miniapp`            what the app is. We know this.
 *   `accountAssociation` that the Farcaster account owner vouches for this
 *                        domain. Signed by the FID's custody key. NOTHING on
 *                        this server can produce it — that is the entire point
 *                        of a signature.
 *
 * So the tests that matter are about the second one being absent honestly. A
 * `/.well-known/farcaster.json` that returns 200 with a well-formed-looking
 * association reads as "configured" to the operator, to a partner, and to a
 * later reader of this repo, while Warpcast rejects it with an error none of
 * them ever sees.
 *
 * Two bugs were caught by running the thing rather than reading it, and both
 * are pinned below:
 *
 *   1. `publicOrigin.resolve()` returns `{origin}` or `{error}`, NOT a string.
 *      `String(...)` of it produced `homeUrl: "[object Object]/embed/signals"`
 *      — a manifest that parses, passes every length limit, and points at
 *      nothing. It is the wallet-QR failure exactly.
 *
 *   2. The embed card and the app icon are DIFFERENT assets with different
 *      rules — 3:2 between 600x400 and 3000x2000 for the card, square 1024
 *      without alpha for the icon. Pointing `imageUrl` at the icon is the
 *      obvious shortcut and publishes a card Farcaster refuses.
 *
 * Constraints are from miniapps.farcaster.xyz/docs/guides/{publishing,sharing},
 * read rather than recalled.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const fc = require('../lib/farcaster_manifest');

const ORIGIN = 'https://www.humanoid-traders.com';
const REQ = { get: () => 'www.humanoid-traders.com', protocol: 'https' };

/** Run `fn` with env vars set, then put them back exactly as they were. */
function withEnv(vars, fn) {
  const prev = {};
  for (const k of Object.keys(vars)) prev[k] = process.env[k];
  try {
    for (const [k, v] of Object.entries(vars)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    return fn();
  } finally {
    for (const [k, v] of Object.entries(prev)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

const base = (extra) => withEnv(
  Object.assign({ APP_BASE_URL: ORIGIN }, extra || {}), () => ({
    manifest: fc.manifest(REQ),
    status: fc.status(REQ),
  }));

// ── the unsigned state, told honestly ────────────────────────────────────

test('an UNSIGNED manifest omits accountAssociation entirely', () => {
  const { manifest, status } = base({
    FARCASTER_ACCOUNT_HEADER: undefined,
    FARCASTER_ACCOUNT_PAYLOAD: undefined,
    FARCASTER_ACCOUNT_SIGNATURE: undefined,
  });
  assert.ok(manifest, 'the manifest should still be servable while unsigned');
  assert.ok(!('accountAssociation' in manifest),
    'an empty or placeholder association was emitted — that turns "not set up '
    + 'yet" into "set up and broken", which is strictly harder to diagnose');
  assert.equal(status.signed, false);
  assert.match(status.unsigned_reason, /Warpcast/);
});

test('a PARTIAL association is treated as no association', () => {
  // Two of three parts is not a weaker proof; it is a malformed one.
  for (const partial of [
    { FARCASTER_ACCOUNT_HEADER: 'aGVhZGVy' },
    { FARCASTER_ACCOUNT_HEADER: 'aGVhZGVy', FARCASTER_ACCOUNT_PAYLOAD: 'cGF5' },
    { FARCASTER_ACCOUNT_PAYLOAD: 'cGF5', FARCASTER_ACCOUNT_SIGNATURE: '0xabc' },
  ]) {
    const { manifest, status } = base(Object.assign({
      FARCASTER_ACCOUNT_HEADER: undefined,
      FARCASTER_ACCOUNT_PAYLOAD: undefined,
      FARCASTER_ACCOUNT_SIGNATURE: undefined,
    }, partial));
    assert.ok(!('accountAssociation' in manifest),
      `a partial association shipped: ${JSON.stringify(partial)}`);
    assert.equal(status.signed, false);
  }
});

test('a COMPLETE association FOR THIS DOMAIN is published verbatim', () => {
  // The payload has to encode the serving domain. The first version of this
  // test used 'cGF5bG9hZA' — base64 for the word "payload" — which was fine
  // while "complete" meant "three non-empty strings" and became wrong the
  // moment the domain started being checked. The fixture was updated, not the
  // rule: a payload that decodes to nothing is not an association for anything.
  const PAYLOAD = 'eyJkb21haW4iOiJ3d3cuaHVtYW5vaWQtdHJhZGVycy5jb20ifQ';
  const { manifest, status } = base({
    FARCASTER_ACCOUNT_HEADER: 'aGVhZGVy',
    FARCASTER_ACCOUNT_PAYLOAD: PAYLOAD,
    FARCASTER_ACCOUNT_SIGNATURE: '0xdeadbeef',
  });
  assert.deepEqual(manifest.accountAssociation, {
    header: 'aGVhZGVy', payload: PAYLOAD, signature: '0xdeadbeef',
  });
  assert.equal(status.signed, true);
  assert.equal(status.unsigned_reason, null);
});

test('signed and ready are separate questions', () => {
  // An unsigned manifest is SERVABLE and useful — it is how the app describes
  // itself. Collapsing the two would either hide a working manifest or claim a
  // proof nobody made.
  const { status } = base({ FARCASTER_ACCOUNT_HEADER: undefined });
  assert.equal(status.ready, true);
  assert.equal(status.signed, false);
});

// ── the origin bug ───────────────────────────────────────────────────────

test('every URL is absolute and real, never "[object Object]"', () => {
  const { manifest } = base();
  for (const key of ['homeUrl', 'iconUrl', 'splashImageUrl']) {
    assert.match(manifest.miniapp[key], /^https:\/\/www\.humanoid-traders\.com\//,
      `${key} is not an absolute URL on the public origin: ${manifest.miniapp[key]}`);
    assert.doesNotMatch(manifest.miniapp[key], /\[object/,
      `${key} stringified the {origin} wrapper — resolve() is not a string`);
  }
});

test('NO public origin means NO manifest, not a manifest full of undefined', () => {
  const out = withEnv({ APP_BASE_URL: undefined, PUBLIC_ORIGIN: undefined }, () => ({
    manifest: fc.manifest(null),
    status: fc.status(null),
  }));
  assert.equal(out.manifest, null,
    'a manifest was served without knowing the public origin, so every URL in '
    + 'it would be wrong or internal');
  assert.equal(out.status.ready, false);
  assert.ok(out.status.problems.some((p) => /public origin/.test(p)));
});

// ── the spec's own constraints ───────────────────────────────────────────

test('the required fields are all present', () => {
  const { manifest } = base();
  for (const k of ['version', 'name', 'homeUrl', 'iconUrl']) {
    assert.ok(manifest.miniapp[k], `required field ${k} is missing`);
  }
  assert.equal(manifest.miniapp.version, '1');
});

test('an over-long name is DROPPED and reported, never truncated', () => {
  // Truncating publishes a name nobody chose, in a public directory, under our
  // own branding.
  const long = 'R'.repeat(fc.LIMITS.name + 8);
  const { manifest, status } = base({ FARCASTER_APP_NAME: long });
  assert.ok(!manifest, 'name is required, so losing it must make the manifest unservable');
  assert.ok(status.problems.some((p) => /name is \d+ characters/.test(p)));
  assert.ok(status.problems.some((p) => /truncated/.test(p)),
    'the reason should say why it was dropped rather than cut');
});

test('an emoji in subtitle or description is refused, not passed through', () => {
  const withEmoji = base({ FARCASTER_APP_SUBTITLE: 'Live signals 🚀' });
  assert.ok(!('subtitle' in withEmoji.manifest.miniapp));
  assert.ok(withEmoji.status.problems.some((p) => /subtitle contains an emoji/.test(p)));

  const d = base({ FARCASTER_APP_DESCRIPTION: 'Trading 📈 intelligence' });
  assert.ok(!('description' in d.manifest.miniapp));
  assert.ok(d.status.problems.some((p) => /description contains an emoji/.test(p)));
});

test('the defaults fit inside every published limit', () => {
  // The shipped values are the ones that matter — a limit checked only against
  // a test fixture says nothing about what this deployment publishes.
  const { manifest, status } = base();
  const m = manifest.miniapp;
  assert.ok(m.name.length <= fc.LIMITS.name);
  assert.ok(m.subtitle.length <= fc.LIMITS.subtitle, `subtitle: ${m.subtitle.length}`);
  assert.ok(m.description.length <= fc.LIMITS.description, `description: ${m.description.length}`);
  assert.ok(m.tags.length <= fc.LIMITS.tags);
  for (const t of m.tags) assert.ok(t.length <= fc.LIMITS.tag, `tag ${t}`);
  assert.deepEqual(status.problems, [], `the shipped defaults are invalid: ${status.problems}`);
});

test('too many tags are capped and the drop is reported', () => {
  const { manifest, status } = base({ FARCASTER_APP_TAGS: 'a,b,c,d,e,f,g' });
  assert.equal(manifest.miniapp.tags.length, fc.LIMITS.tags);
  assert.ok(status.problems.some((p) => /over the 5 limit/.test(p)));
});

// ── the assets, checked as bytes rather than as URLs ─────────────────────

const PUB = path.join(__dirname, '..', 'public');
function png(file) {
  const d = fs.readFileSync(path.join(PUB, file));
  return { w: d.readUInt32BE(16), h: d.readUInt32BE(20), colourType: d[25], bytes: d.length };
}

test('the app icon is 1024x1024 PNG with NO alpha, as the spec demands', () => {
  // Checked as bytes. The two icons already in the repo are 256 and 512 with an
  // alpha channel, so "point iconUrl at the existing icon" was never available
  // and a URL-only test would not have noticed.
  const i = png(fc.ICON_PATH.replace(/^\//, ''));
  assert.equal(i.w, 1024, `icon is ${i.w}px wide`);
  assert.equal(i.h, 1024);
  assert.equal(i.colourType, 2,
    `icon PNG colour type is ${i.colourType}; 2 is RGB, and 6 (RGBA) is refused`);
});

test('the embed card is 3:2 within the published size window', () => {
  const c = png(fc.CARD_PATH.replace(/^\//, ''));
  assert.ok(Math.abs(c.w / c.h - 1.5) < 0.01, `card ratio is ${(c.w / c.h).toFixed(3)}, need 1.5`);
  assert.ok(c.w >= 600 && c.w <= 3000, `card width ${c.w} outside 600..3000`);
  assert.ok(c.h >= 400 && c.h <= 2000, `card height ${c.h} outside 400..2000`);
  assert.ok(c.bytes < 10 * 1024 * 1024, `card is ${c.bytes} bytes, over the 10MB limit`);
});

test('the card and the icon are DIFFERENT assets', () => {
  // The bug this pins: `imageUrl` pointed at the square icon, which is the
  // obvious shortcut and publishes a card Farcaster refuses.
  assert.notEqual(fc.ICON_PATH, fc.CARD_PATH);
  const tags = fc.embedTags(ORIGIN);
  assert.ok(tags.includes(fc.CARD_PATH), 'the embed card does not use the 3:2 asset');
});

// ── the embed tag ────────────────────────────────────────────────────────

function parseTag(tags, name) {
  const m = tags.match(new RegExp(`name="${name}" content="([^"]+)"`));
  if (!m) return null;
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&#39;/g, "'"));
}

test('fc:miniapp carries the shape the spec requires', () => {
  const j = parseTag(fc.embedTags(ORIGIN), 'fc:miniapp');
  assert.equal(j.version, '1');
  assert.match(j.imageUrl, /^https:\/\//);
  assert.ok(j.button.title);
  assert.equal(j.button.action.type, 'launch_miniapp');
  assert.match(j.button.action.url, /^https:\/\//);
});

test('fc:frame carries the same payload with the legacy action type', () => {
  const tags = fc.embedTags(ORIGIN);
  const mini = parseTag(tags, 'fc:miniapp');
  const frame = parseTag(tags, 'fc:frame');
  assert.equal(frame.button.action.type, 'launch_frame');
  assert.equal(frame.imageUrl, mini.imageUrl,
    'the two tags disagree about the image, so which one a client reads changes '
    + 'what it shows');
  assert.equal(frame.button.action.url, mini.button.action.url);
});

test('no embed tags at all without a public origin', () => {
  // Better a plain link than a card whose every URL points somewhere the
  // outside world cannot reach.
  assert.equal(fc.embedTags(''), '');
  assert.equal(fc.embedTags(null), '');
});

test('the tag content is attribute-escaped', () => {
  const tags = fc.embedTags(ORIGIN);
  assert.ok(!/content="[^"]*"[^>]*"/.test(tags),
    'an unescaped quote inside the JSON would end the attribute early');
  assert.ok(tags.includes('&quot;'), 'the JSON is not attribute-escaped at all');
});

// ── the signature is bound to ONE domain ─────────────────────────────────
//
// Serving it from another is not a weaker proof. It is a false claim that
// reads as configured, which is worse than an absent one, and it is easy to
// reach by accident: a staging deploy, a preview URL, or the apex-versus-www
// mistake — humanoid-traders.com 301s to www.humanoid-traders.com, and
// Farcaster treats them as different domains regardless.
//
// The real signed payload for this account decodes to
// {"domain":"www.humanoid-traders.com"}, so these use it verbatim rather than
// a fixture: a check that only ever sees a hand-made payload has never been
// pointed at the thing it guards.

const REAL = {
  FARCASTER_ACCOUNT_HEADER:
    'eyJmaWQiOjMzNDc5NzgsInR5cGUiOiJjdXN0b2R5Iiwia2V5IjoiMHgyNjYyOGEzMTZmZkY1NzI5YjIwMWRhRDczYzk2MDQxRkVjNzM2Njk1In0',
  FARCASTER_ACCOUNT_PAYLOAD: 'eyJkb21haW4iOiJ3d3cuaHVtYW5vaWQtdHJhZGVycy5jb20ifQ',
  FARCASTER_ACCOUNT_SIGNATURE:
    'crNlfLtqS2vr5GrdqCC4dLVstEpZZk97oA/f32pqp5Q0992558WLKJTkxqLHpNn1kMn9nmWz7LROO/THuR7ZFxs=',
};

const signedAt = (origin) => withEnv(
  Object.assign({ APP_BASE_URL: origin }, REAL),
  () => ({ manifest: fc.manifest(null), status: fc.status(null) }));

test('the association payload is decoded, not trusted', () => {
  withEnv(REAL, () => {
    assert.equal(fc.associationDomain(), 'www.humanoid-traders.com');
  });
});

test('a matching domain publishes the association and reports signed', () => {
  const { manifest, status } = signedAt('https://www.humanoid-traders.com');
  assert.ok('accountAssociation' in manifest);
  assert.equal(status.signed, true);
  assert.equal(status.unsigned_reason, null);
});

test('the APEX is a different domain from www, and is refused', () => {
  // The mistake this account came within one form field of making.
  const { manifest, status } = signedAt('https://humanoid-traders.com');
  assert.ok(!('accountAssociation' in manifest),
    'a signature for www was published from the apex — it verifies as nothing '
    + 'and reads as configured');
  assert.equal(status.signed, false);
  assert.match(status.unsigned_reason, /www\.humanoid-traders\.com/);
  assert.match(status.unsigned_reason, /humanoid-traders\.com/);
});

test('a staging or preview host is refused too', () => {
  const { status } = signedAt('https://staging.humanoid-traders.com');
  assert.equal(status.signed, false);
  assert.match(status.unsigned_reason, /staging\.humanoid-traders\.com/);
});

test('CONFIGURED and SIGNED are reported separately', () => {
  // The diagnostic that makes a mismatch findable: the operator set three env
  // vars, can see them in the process, and needs to know why Warpcast still
  // refuses the domain.
  const { status } = signedAt('https://humanoid-traders.com');
  assert.equal(status.association_configured, true, 'the env vars ARE set');
  assert.equal(status.signed, false, 'and they do not authorise this host');
  assert.equal(status.association_domain, 'www.humanoid-traders.com');
  assert.equal(status.serving_host, 'humanoid-traders.com');
});

test('an undecodable payload is not a match', () => {
  const { manifest, status } = withEnv(
    Object.assign({}, REAL, {
      APP_BASE_URL: 'https://www.humanoid-traders.com',
      FARCASTER_ACCOUNT_PAYLOAD: 'not-base64-at-all!!',
    }),
    () => ({ manifest: fc.manifest(null), status: fc.status(null) }));
  assert.ok(!('accountAssociation' in manifest));
  assert.equal(status.signed, false);
  assert.equal(status.association_domain, null);
});

test('an unknown serving origin is not a match either', () => {
  // Both sides must be KNOWN and equal. An unknown on either side is not a
  // match, because the entire point is that a mismatch is invisible otherwise.
  const st = withEnv(
    Object.assign({}, REAL, { APP_BASE_URL: undefined, PUBLIC_ORIGIN: undefined }),
    () => fc.status(null));
  assert.equal(st.signed, false);
});
