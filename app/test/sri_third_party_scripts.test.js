'use strict';
/**
 * A third-party script on a page this app serves, or on the operator
 * dashboard, must carry Subresource Integrity and crossorigin.
 *
 * Two scripts cannot. Each is named in full. A third name fails this file.
 *
 *   https://telegram.org/js/telegram-widget.js
 *     Telegram rewrites this file in place and publishes no digest. The
 *     login button is the script. A pinned hash removes the button the next
 *     time they publish it. Measured 2026-10-30: Access-Control-Allow-Origin
 *     is `*`, so CORS is not the blocker — the bytes are. last-modified
 *     2026-06-29, Cache-Control max-age=345600.
 *
 *   https://sdk.minepi.com/pi-sdk.js
 *     The platform requires this exact URL (routes/piapp.js). The host sends
 *     no Access-Control-Allow-Origin (CloudFront, OPTIONS 403, measured
 *     2026-10-30). crossorigin would fail the load, and Pi sign-in would not
 *     start. The bytes can be hashed; the browser is not allowed to see them
 *     as a CORS response, which is what SRI requires.
 *
 * telegram-web-app.js is not on this list: that host does send
 * Access-Control-Allow-Origin: *, and routes/tgapp.js pins its digest.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const ROOT = path.join(APP, '..');

const SRI_EXEMPT = Object.freeze({
  'https://telegram.org/js/telegram-widget.js': 'Telegram rewrites the login widget in place',
  'https://sdk.minepi.com/pi-sdk.js': 'sdk.minepi.com sends no CORS header, so SRI cannot be checked',
});

function canonical(url) {
  return String(url).split('#')[0].split('?')[0];
}

function isExempt(url) {
  return Object.prototype.hasOwnProperty.call(SRI_EXEMPT, canonical(url));
}

function walk(dir, ext, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name === 'vendor') continue;
      walk(full, ext, out);
    } else if (e.name.endsWith(ext)) out.push(full);
  }
  return out;
}

function constsOf(src, re) {
  const map = {};
  for (const m of src.matchAll(re)) map[m[1]] = m[2];
  return map;
}

function resolved(raw, map) {
  const sub = /^\$\{([A-Za-z0-9_]+)\}$/.exec(raw);
  if (sub && map[sub[1]]) return map[sub[1]];
  return raw;
}

/** Third-party script URLs in one source file that lack SRI. */
function violations(file) {
  const raw = fs.readFileSync(file, 'utf8');
  const code = codeOnly(raw).replace(/<!--[\s\S]*?-->/g, ' ');
  const urls = constsOf(code, /(?:const|let|var)\s+([A-Z0-9_]+)\s*=\s*['"](https?:\/\/[^'"]+)['"]/g);
  const digests = constsOf(code, /(?:const|let|var)\s+([A-Z0-9_]+)\s*=\s*['"](sha(?:256|384|512)-[^'"]+)['"]/g);
  const bad = [];

  for (const m of code.matchAll(/<script\b[^>]*>/gi)) {
    const tag = m[0];
    const src = /\ssrc\s*=\s*['"]([^'"]+)['"]/i.exec(tag);
    if (!src) continue;
    const url = resolved(src[1], urls);
    if (!/^https?:\/\//i.test(url)) continue;
    if (isExempt(url)) continue;
    const integAttr = /\sintegrity\s*=\s*['"]([^'"]+)['"]/i.exec(tag);
    const integ = integAttr ? resolved(integAttr[1], digests) : '';
    const cross = /\scrossorigin\s*=\s*['"](?:anonymous|use-credentials)['"]/i.test(tag);
    if (!/^sha(?:256|384|512)-/.test(integ) || !cross) bad.push(url);
  }

  const mapsBareScripts = /<script src="\$\{[^}]+\}"><\/script>/.test(code);
  if (mapsBareScripts) {
    for (const [name, url] of Object.entries(urls)) {
      if (!new RegExp(`\\[[^\\]]*\\b${name}\\b`).test(code)) continue;
      if (!isExempt(url)) bad.push(url);
    }
  }

  for (const m of code.matchAll(/\.src\s*=\s*['"](https?:\/\/[^'"]+)['"]/g)) {
    const before = code.slice(Math.max(0, m.index - 500), m.index);
    if (!/createElement\(\s*['"]script['"]\s*\)/.test(before)) continue;
    if (isExempt(m[1])) continue;
    const after = code.slice(m.index, m.index + 600);
    const setsIntegrity = /\.integrity\s*=|\.setAttribute\(\s*['"]integrity['"]/.test(after);
    const setsCross = /\.crossOrigin\s*=|\.setAttribute\(\s*['"]crossorigin['"]/.test(after);
    if (!setsIntegrity || !setsCross) bad.push(m[1]);
  }

  return bad.map((url) => `${path.relative(ROOT, file)} → ${url}`);
}

function pages() {
  return [
    ...walk(path.join(APP, 'public'), '.html'),
    ...walk(path.join(APP, 'public', 'js'), '.js'),
    ...walk(path.join(APP, 'routes'), '.js'),
    ...walk(path.join(APP, 'templates'), '.html'),
    ...walk(path.join(ROOT, 'bot', 'web'), '.html'),
  ];
}

test('the exemption is those two scripts and no others', () => {
  assert.deepStrictEqual(Object.keys(SRI_EXEMPT).sort(), [
    'https://sdk.minepi.com/pi-sdk.js',
    'https://telegram.org/js/telegram-widget.js',
  ]);
  for (const url of Object.keys(SRI_EXEMPT)) {
    assert.ok(!url.includes('*'), url);
    assert.ok(!url.endsWith('/'), url);
  }
});

test('every third-party script on a served page has integrity and crossorigin', () => {
  const bad = [];
  for (const file of pages()) bad.push(...violations(file));
  assert.deepStrictEqual(bad, [],
    'a third-party script is loaded without SRI:\n  ' + bad.join('\n  '));
});
