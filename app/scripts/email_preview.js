#!/usr/bin/env node
'use strict';
/**
 * Render every website email to .html and .txt, for review.
 *
 *   node app/scripts/email_preview.js --out <dir>
 *
 * Writes one .html and one .txt per render, and an index.html that links
 * them all. It renders through the same code the routes send with
 * (lib/email_templates), so what a reviewer reads is what a member gets,
 * except for three stand-ins:
 *
 *  - the origin is https://www.humanoid-traders.com (live mail reads
 *    PUBLIC_ORIGIN, lib/public_origin.js);
 *  - tokens are fixed placeholders (preview-verify-token, …), never a real
 *    or random value. They are low-entropy on purpose: the committed copies
 *    must not look like a secret to the repository's secret scanners;
 *  - a plan notice lists Marketing's example bullets, each marked
 *    "Example:". Live mail lists what the bot's plan ladder says at send
 *    time (lib/plan_features.js).
 *
 * It reads no environment, no database and no secret, and sends nothing.
 *
 * The reviewed copies are committed under app/test/fixtures/email_previews/,
 * and app/test/email_templates.test.js fails when one no longer matches what
 * this renders, so the copy that was approved is the copy that ships.
 * Re-run it after any wording change:
 *
 *   node app/scripts/email_preview.js --out app/test/fixtures/email_previews
 */

const fs = require('fs');
const path = require('path');
const T = require('../lib/email_templates');
// The plan names the tier notice uses, from the one place they are kept.
const { PLAN_NAMES } = require('../lib/account_mail');

const PREVIEW_ORIGIN = 'https://www.humanoid-traders.com';
// A fixed moment, so a re-render is byte-identical.
const PREVIEW_AT = new Date('2026-11-23T15:32:00Z');

/** Every render a reviewer should read, as { name, id, vars }. */
function previews(base = PREVIEW_ORIGIN) {
  const when = T.whenText(PREVIEW_AT);
  const unsubscribe_url = `${base}/email-preferences?token=preview-unsubscribe-token`;
  const verify = {
    verify_url: `${base}/verify?token=preview-verify-token`,
    expires_in: '24 hours',
  };
  const out = [
    { name: 'welcome_verify', id: 'welcome_verify', vars: { ...verify } },
    { name: 'verify', id: 'verify', vars: { ...verify } },
    { name: 'password_reset_link', id: 'password_reset_link',
      vars: { reset_url: `${base}/reset?token=preview-reset-token`, expires_in: '30 minutes' } },
    { name: 'password_changed', id: 'password_changed', vars: { when } },
    { name: 'password_set', id: 'password_set', vars: { when } },
    { name: 'password_reset', id: 'password_reset', vars: { when } },
    { name: 'totp_enabled', id: 'totp_enabled', vars: { when } },
    { name: 'totp_disabled', id: 'totp_disabled', vars: { when } },
    { name: 'account_deleted', id: 'account_deleted', vars: { when } },
  ];
  for (const dir of ['upgrade', 'downgrade']) {
    for (const tier of Object.keys(PLAN_NAMES)) {
      out.push({
        name: `tier_${dir}_${tier}`,
        id: `tier_${dir}`,
        vars: { tier_name: PLAN_NAMES[tier], features: T.COPY.PREVIEW_FEATURES[tier], unsubscribe_url },
      });
    }
  }
  out.push({ name: 'onboarding_verified', id: 'onboarding', vars: { unverified: false, unsubscribe_url } });
  out.push({ name: 'onboarding_unverified', id: 'onboarding', vars: { unverified: true, unsubscribe_url } });
  return out.map((p) => ({ ...p, vars: { base, ...p.vars } }));
}

/** previews() rendered: [{ name, id, rendered }]. */
function renders(base = PREVIEW_ORIGIN) {
  return previews(base).map((p) => ({ name: p.name, id: p.id, rendered: T.render(p.id, p.vars) }));
}

function indexHtml(list) {
  const rows = list.map((r) => '<tr>'
    + `<td><a href="${T.escapeHtml(r.name)}.html">${T.escapeHtml(r.name)}</a></td>`
    + `<td><a href="${T.escapeHtml(r.name)}.txt">text</a></td>`
    + `<td>${r.rendered.security ? 'security' : 'notice (unsubscribe)'}</td>`
    + `<td>${T.escapeHtml(r.rendered.subject)}</td></tr>`).join('\n');
  return '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    + '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + '<meta name="robots" content="noindex">\n'
    + '<title>RUNECLAW email previews</title>\n</head>\n'
    + '<body style="font-family:Arial,Helvetica,sans-serif;margin:24px;">\n'
    + '<h1>RUNECLAW email previews</h1>\n'
    + '<p>Rendered by app/scripts/email_preview.js with a stand-in origin, placeholder '
    + 'tokens and example plan bullets. Live mail reads the public origin and the bot\'s plan ladder.</p>\n'
    + '<table cellpadding="6" border="1" style="border-collapse:collapse;">\n'
    + '<tr><th>render</th><th>plain text</th><th>kind</th><th>subject</th></tr>\n'
    + `${rows}\n</table>\n</body>\n</html>\n`;
}

/** Every file the preview writes, as { relative name: contents }. */
function files(base = PREVIEW_ORIGIN) {
  const list = renders(base);
  const out = {};
  for (const r of list) {
    out[`${r.name}.html`] = r.rendered.html;
    out[`${r.name}.txt`] = `Subject: ${r.rendered.subject}\nPreheader: ${r.rendered.preheader}\n\n${r.rendered.text}`;
  }
  out['index.html'] = indexHtml(list);
  return out;
}

function main(argv = process.argv.slice(2)) {
  const i = argv.indexOf('--out');
  const dir = i >= 0 ? argv[i + 1] : '';
  if (!dir) {
    console.error('usage: node app/scripts/email_preview.js --out <dir>');
    return 2;
  }
  fs.mkdirSync(dir, { recursive: true });
  const all = files();
  for (const [name, body] of Object.entries(all)) fs.writeFileSync(path.join(dir, name), body);
  console.log(`wrote ${Object.keys(all).length} files to ${dir}`);
  return 0;
}

if (require.main === module) process.exit(main());

module.exports = { previews, renders, files, PREVIEW_ORIGIN, PREVIEW_AT };
