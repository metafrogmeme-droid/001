'use strict';
/**
 * Receipt-first sharing — the contract under test:
 * - What travels is the RECEIPT URL, which verifies in anyone's browser —
 *   never a screenshot, and no amounts ride in any share text.
 * - Warpcast joins Telegram and X wherever a share row exists.
 * - The receipt page's share script runs AFTER i18n loads, so the share
 *   text is translated, not permanently English.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

test('the receipt page shares itself: TG, X and Warpcast, text without a single number', () => {
  const page = read('public', 'call.html');
  assert.match(page, /warpcast\.com\/~\/compose/);
  assert.match(page, /twitter\.com\/intent\/tweet/);
  assert.match(page, /t\.me\/share\/url/);
  assert.match(page, /encodeURIComponent\(location\.href\)/, 'the receipt URL is the payload');
  // The default share text carries no digits — the receipt page shows the
  // sealed facts; the share text only invites verification.
  const txt = page.match(/'A cryptographically sealed AI trading call[^']*'/)[0];
  assert.doesNotMatch(txt, /\d/, 'no numbers ride in the share text');
  // The script must run AFTER i18n so the text translates.
  assert.ok(page.indexOf('/js/i18n-core.js') < page.indexOf("RCI18N.translate('cp.share_txt'"),
    'share links build after the dictionary loads');
});

test('arena history: receipts share themselves, and only sealed rows offer it', () => {
  const page = read('public', 'arena.html');
  assert.match(page, /data-share-key/, 'the share button exists');
  const row = page.slice(page.indexOf('t.seal && t.trade_key'), page.indexOf('data-share-key') + 200);
  assert.ok(row.includes('data-share-key'), 'the button renders only inside the sealed-row branch');
  const handler = page.slice(page.indexOf("closest('[data-share-key]')"));
  assert.match(handler.slice(0, 900), /\/call\/' \+ encodeURIComponent/, 'the receipt URL is what travels');
  assert.match(handler.slice(0, 900), /navigator\.share/);
  assert.match(handler.slice(0, 900), /warpcast\.com/, 'the no-share-sheet fallback is Warpcast');
  assert.doesNotMatch(handler.slice(0, 900), /\b(pnl|amounts?|vusdt)\b/i, 'no amounts ride in the share');
});

test('the referral row speaks Warpcast too', () => {
  const js = read('public', 'js', 'dashboard.js');
  const row = js.slice(js.indexOf('Share on Telegram'), js.indexOf('Share on Telegram') + 700);
  assert.match(row, /warpcast\.com\/~\/compose/);
  assert.match(row, /embeds\[\]=/);
});

test('the share keys cover 14 languages', () => {
  const i18n = require('../public/js/i18n.js');
  for (const k of ['cp.share', 'cp.share_txt', 'arena.share_t']) {
    for (const l of i18n.LANGS) {
      assert.ok(String(i18n.STRINGS[k][l.code] || '').trim().length, `${k} missing ${l.code}`);
    }
    assert.doesNotMatch(String(i18n.STRINGS[k].en), /\$|usd|vUSDT/i,
      'share strings never mention money');
  }
});
