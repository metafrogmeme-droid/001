'use strict';
/**
 * The Farcaster Mini App manifest — `/.well-known/farcaster.json`.
 *
 * Publishing a Mini App is two claims made at one URL, and only one of them is
 * ours to make.
 *
 *   `miniapp`            what the app is: name, icon, home URL. We know this.
 *   `accountAssociation` that humanoid-traders.farcaster.xyz's owner vouches
 *                        for this domain. Signed by the FID's custody key in
 *                        Warpcast → Settings → Developer → Domains. NOTHING
 *                        here can produce it, and nothing here should pretend
 *                        to — the whole point of the signature is that a
 *                        server cannot mint it.
 *
 * SO AN UNSIGNED MANIFEST MUST NOT LOOK SIGNED. When the three association
 * parts are not all configured the key is OMITTED, never emitted with empty
 * strings or a placeholder. A `/.well-known/farcaster.json` that returns 200
 * with a well-formed-looking association reads as "configured" to everyone who
 * checks it — the operator, a partner, a later reader of this repo — while
 * Warpcast rejects it with an error none of them will see. That is the same
 * shape as every other defect this codebase keeps finding: correct-looking,
 * well-formed, and wrong.
 *
 * A FIELD THAT VIOLATES ITS CONSTRAINT IS DROPPED AND REPORTED, NOT TRUNCATED.
 * `name` is capped at 32 characters by the spec. Silently cutting a 40-character
 * name to 32 publishes a name nobody chose, in a directory, under our own
 * branding. The field is left out and `status().problems` says why.
 *
 * A missing REQUIRED field means there is no manifest to serve. The route
 * answers 503 with a reason rather than 200 with a document Farcaster will
 * reject opaquely — `ready` / `not_ready_reasons`, the same contract
 * `lib/tool8257.js` already publishes for the ERC-8257 registration.
 *
 * Constraints below are from miniapps.farcaster.xyz/docs/guides/publishing,
 * read rather than recalled.
 */

const publicOrigin = require('./public_origin');

/** Spec limits. Named so a violation reads as a rule, not a magic number. */
const LIMITS = {
  name: 32,
  homeUrl: 1024,
  iconUrl: 1024,
  subtitle: 30,
  description: 170,
  tag: 20,
  tags: 5,
};

/** The icon the spec demands: 1024x1024 PNG, no alpha channel. */
const ICON_PATH = '/app_icon_1024.png';

/**
 * The embed CARD, which is a different asset with a different rule: 3:2,
 * between 600x400 and 3000x2000, under 10MB. Pointing `imageUrl` at the square
 * icon — the obvious shortcut, and the first thing this file did — publishes a
 * card Farcaster refuses, so the two paths are named separately rather than
 * sharing one "the image" constant.
 */
const CARD_PATH = '/farcaster_card.png';

/** Emoji are refused in `subtitle` and `description` by the spec. */
const EMOJI = /[‼-㊙\u{1F000}-\u{1FAFF}\u{FE0F}\u{200D}]/u;

function env(name) {
  return String(process.env[name] || '').trim();
}

/**
 * The signed proof that this domain belongs to the Farcaster account.
 *
 * All three parts or nothing. A partial association is not a weaker proof, it
 * is a malformed one, and emitting it would turn "not set up yet" into "set up
 * and broken" — a strictly harder thing to diagnose.
 */
function accountAssociation() {
  const header = env('FARCASTER_ACCOUNT_HEADER');
  const payload = env('FARCASTER_ACCOUNT_PAYLOAD');
  const signature = env('FARCASTER_ACCOUNT_SIGNATURE');
  if (!header || !payload || !signature) return null;
  return { header, payload, signature };
}

/** base64url -> utf8, or null. Never throws — a malformed part is a fact to
 *  report, not an exception to take down the manifest route. */
function b64urlJson(s) {
  try {
    const norm = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(Buffer.from(norm, 'base64').toString('utf8'));
  } catch (_) {
    return null;
  }
}

/**
 * The domain this signature actually authorises, read from its own payload.
 *
 * The signature is bound to ONE domain. Serving it from another is not a
 * weaker proof — it is a false claim that looks configured, which is worse
 * than an absent one, and it is easy to reach by accident: a staging deploy, a
 * preview URL, or the apex-versus-`www` mistake that this exact account nearly
 * made. `www.humanoid-traders.com` and `humanoid-traders.com` are different
 * domains to Farcaster even though one 301s to the other.
 *
 * So the payload is decoded and compared rather than trusted. `null` when it
 * cannot be read, which `status()` reports as its own reason: a payload we
 * cannot parse is not a payload for the right domain.
 */
function associationDomain() {
  const a = accountAssociation();
  if (!a) return null;
  const parsed = b64urlJson(a.payload);
  const d = parsed && parsed.domain;
  return d ? String(d).toLowerCase().replace(/\/+$/, '') : null;
}

/** The host the manifest is actually being served from, lowercased. */
function servingHost(req) {
  const base = baseUrl(req);
  if (!base) return null;
  try {
    return new URL(base).host.toLowerCase();
  } catch (_) {
    return null;
  }
}

/**
 * Does the signature authorise the domain we are serving from?
 *
 * Returns `{ ok, reason }`. `ok: true` only when both sides are known AND
 * equal — an unknown on either side is not a match, because the whole point of
 * the check is that a mismatch is invisible without it.
 */
function domainMatches(req) {
  const signed = associationDomain();
  const serving = servingHost(req);
  if (!signed) {
    return { ok: false, reason: 'the association payload does not decode to a domain' };
  }
  if (!serving) {
    return { ok: false, reason: 'this deployment does not know its own public origin, '
      + 'so it cannot check the signature is for the right domain' };
  }
  if (signed !== serving) {
    return { ok: false, reason: `the signature authorises "${signed}" but this is being `
      + `served from "${serving}". Farcaster treats those as different domains even when `
      + 'one redirects to the other, so the association is omitted rather than published '
      + 'as a claim about a domain it does not cover.' };
  }
  return { ok: true, reason: null };
}

/**
 * The public origin, or '' when this deployment does not know it.
 *
 * `resolve` returns `{origin}` or `{error}` — NOT a string. The first draft
 * here did `String(resolve(...))` and produced `[object Object]/embed/signals`
 * as a homeUrl: a manifest that parses, validates against every length limit,
 * and points at nothing. It is the wallet-QR failure exactly — a link that
 * renders perfectly and is unreachable by everyone who follows it — which is
 * why `lib/public_origin` exists and why it refuses to guess.
 */
function baseUrl(req) {
  try {
    const r = publicOrigin.resolve(req, process.env) || {};
    return String(r.origin || '').replace(/\/+$/, '');
  } catch (_) {
    return '';
  }
}

/**
 * Build the `miniapp` object and the list of everything that had to be left out.
 *
 * Returns `{ miniapp, problems }`. `problems` is the honest half: a caller that
 * only reads `miniapp` sees a valid document and learns nothing about the
 * subtitle that was thrown away for containing an emoji.
 */
function buildMiniapp(req) {
  const base = baseUrl(req);
  const problems = [];
  const miniapp = { version: '1' };

  if (!base) {
    problems.push('no public origin: APP_BASE_URL is unset and the request '
      + 'carries no trustworthy host, so every URL in the manifest would be '
      + 'wrong or internal');
    return { miniapp, problems };
  }

  const name = env('FARCASTER_APP_NAME') || 'RUNECLAW';
  if (name.length > LIMITS.name) {
    problems.push(`name is ${name.length} characters, over the ${LIMITS.name} limit — `
      + 'left out rather than truncated, because a cut name is a name nobody chose');
  } else {
    miniapp.name = name;
  }

  miniapp.homeUrl = `${base}/embed/signals`;
  miniapp.iconUrl = `${base}${ICON_PATH}`;
  for (const key of ['homeUrl', 'iconUrl']) {
    if (miniapp[key].length > LIMITS[key]) {
      problems.push(`${key} is ${miniapp[key].length} characters, over ${LIMITS[key]}`);
      delete miniapp[key];
    }
  }

  const subtitle = env('FARCASTER_APP_SUBTITLE') || 'Live trading signals';
  if (subtitle.length > LIMITS.subtitle) {
    problems.push(`subtitle is ${subtitle.length} characters, over ${LIMITS.subtitle}`);
  } else if (EMOJI.test(subtitle)) {
    problems.push('subtitle contains an emoji, which the spec refuses');
  } else {
    miniapp.subtitle = subtitle;
  }

  const description = env('FARCASTER_APP_DESCRIPTION')
    || 'Every setup the engine generates, taken or not — with the entry, stop '
     + 'and target drawn on live price.';
  if (description.length > LIMITS.description) {
    problems.push(`description is ${description.length} characters, over ${LIMITS.description}`);
  } else if (EMOJI.test(description)) {
    problems.push('description contains an emoji, which the spec refuses');
  } else {
    miniapp.description = description;
  }

  miniapp.splashImageUrl = `${base}/app_icon_256.png`;
  miniapp.splashBackgroundColor = '#0b0d12';
  miniapp.primaryCategory = 'finance';

  const tags = (env('FARCASTER_APP_TAGS') || 'trading,signals,defi,agents')
    .split(',').map((t) => t.trim()).filter(Boolean);
  const kept = tags.filter((t) => t.length <= LIMITS.tag);
  for (const t of tags) {
    if (t.length > LIMITS.tag) problems.push(`tag "${t}" is over ${LIMITS.tag} characters`);
  }
  if (kept.length > LIMITS.tags) {
    problems.push(`${kept.length} tags, over the ${LIMITS.tags} limit — extras dropped`);
  }
  miniapp.tags = kept.slice(0, LIMITS.tags);

  return { miniapp, problems };
}

/** The document served at /.well-known/farcaster.json, or null if not servable. */
function manifest(req) {
  const { miniapp } = buildMiniapp(req);
  const st = status(req);
  if (!st.ready) return null;
  const out = {};
  const assoc = accountAssociation();
  // Published only when it is a claim about THIS domain. A signature for
  // another one reads as configured and verifies as nothing.
  if (assoc && domainMatches(req).ok) out.accountAssociation = assoc;
  out.miniapp = miniapp;
  // `frame` is the legacy key name for the same object. Emitted alongside so
  // clients that predate the rename still resolve the app; it is the same
  // reference, not a second source of truth that could drift from it.
  out.frame = miniapp;
  return out;
}

/**
 * Whether the manifest can be served, and what a signed one still needs.
 *
 * `signed` is deliberately separate from `ready`. An unsigned manifest is
 * SERVABLE and useful — it is how the app is described — it simply is not yet
 * verified as belonging to this domain. Collapsing the two would either hide a
 * working manifest or claim a proof nobody made.
 */
function status(req) {
  const { miniapp, problems } = buildMiniapp(req);
  const required = ['version', 'name', 'homeUrl', 'iconUrl'];
  const missing = required.filter((k) => !miniapp[k]);
  return {
    ready: missing.length === 0,
    not_ready_reasons: missing.map((k) => `required field \`${k}\` could not be built`),
    signed: Boolean(accountAssociation()) && domainMatches(req).ok,
    // `configured` and `signed` are different questions, and conflating them is
    // how a domain mismatch hides: the operator set three env vars, sees them
    // in the process, and cannot tell why Warpcast still refuses the domain.
    association_configured: Boolean(accountAssociation()),
    association_domain: associationDomain(),
    serving_host: servingHost(req),
    unsigned_reason: !accountAssociation()
      ? 'FARCASTER_ACCOUNT_HEADER / _PAYLOAD / _SIGNATURE are not all set. Only '
        + 'the Farcaster account owner can produce them — Warpcast → Settings → '
        + 'Developer → Domains, sign this exact origin. Until then the manifest '
        + 'is served WITHOUT an accountAssociation rather than with a fake one.'
      : (domainMatches(req).ok ? null : domainMatches(req).reason),
    problems,
  };
}

/**
 * The `fc:miniapp` embed tag, so a link to a RUNECLAW page renders as a
 * launchable card in a cast rather than a plain URL.
 *
 * `fc:frame` carries the same JSON with `launch_frame` for clients that predate
 * the rename — built from the same object so the two cannot disagree.
 */
function embedTags(base, opts) {
  opts = opts || {};
  const origin = String(base || '').replace(/\/+$/, '');
  if (!origin) return '';
  const imageUrl = opts.imageUrl || `${origin}${CARD_PATH}`;
  const url = opts.url || `${origin}/embed/signals`;
  const body = (type) => JSON.stringify({
    version: '1',
    imageUrl,
    button: {
      title: opts.buttonTitle || 'Open live signals',
      action: {
        type,
        url,
        name: 'RUNECLAW',
        splashImageUrl: `${origin}/app_icon_256.png`,
        splashBackgroundColor: '#0b0d12',
      },
    },
  });
  const attr = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
  return [
    `<meta name="fc:miniapp" content="${attr(body('launch_miniapp'))}">`,
    `<meta name="fc:frame" content="${attr(body('launch_frame'))}">`,
  ].join('\n');
}

module.exports = {
  manifest, status, accountAssociation, buildMiniapp, embedTags,
  associationDomain, servingHost, domainMatches,
  LIMITS, ICON_PATH, CARD_PATH,
};
