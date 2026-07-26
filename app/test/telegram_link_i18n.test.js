'use strict';
// Telegram linking is the PREREQUISITE for the two panels already translated:
// exchange keys and live-trading controls are both gated behind it. Leaving the
// gateway in English while its destinations are localized means a non-English
// user cannot reach the parts we translated.
//
// Two things here are deliberately NOT translated. The bot handle
// (@HTRUNECLAW_bot) and the /link command are typed verbatim into Telegram —
// translating either would produce an instruction that does not work.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

const TG = ['dd.tg_linked', 'dd.tg_intro', 'dd.tg_step1', 'dd.tg_step2', 'dd.tg_step3',
  'dd.tg_gen', 'dd.tg_open_bot', 'dd.tg_tok_fail', 'dd.tg_tok_label', 'dd.tg_send'];

test('every Telegram string exists in all 14 languages', () => {
  for (const k of TG) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the bot handle and the /link command stay verbatim', () => {
  // Interpolated, never translated: a localized handle or command would be an
  // instruction that silently fails when pasted into Telegram.
  assert.match(dash, /TF\('dd\.tg_step1', 'Open \{bot\} on Telegram', \{ bot: botLink \}\)/);
  assert.match(dash, /cmd: '<code>\/link &lt;token&gt;<\/code>'/);
  for (const k of ['dd.tg_step1', 'dd.tg_open_bot']) {
    for (const c of codes) {
      assert.match(String(i18n.STRINGS[k][c]), /\{bot\}/, `${k}:${c} dropped the {bot} slot`);
      assert.doesNotMatch(String(i18n.STRINGS[k][c]), /HTRUNECLAW/,
        `${k}:${c} hardcodes the handle instead of interpolating it`);
    }
  }
  for (const c of codes) {
    assert.match(String(i18n.STRINGS['dd.tg_step3'][c]), /\{cmd\}/,
      `dd.tg_step3:${c} dropped the {cmd} slot — the step would name no command`);
  }
});

test('the operator-approval caveat survives translation', () => {
  // Linking Telegram unlocks the CONTROLS, not live trading itself. Every
  // language has to keep that distinction or the panel over-promises.
  const e = i18n.STRINGS['dd.tg_linked'];
  const approval = {
    en: /operator approval/i, hi: /ऑपरेटर की स्वीकृति/, it: /approvazione dell’operatore/i,
    es: /aprobación del operador/i, zh: /營運方核准/, pt: /aprovação do operador/i,
    fr: /approbation de l’opérateur/i, de: /Freigabe des Betreibers/i,
    nl: /goedkeuring van de operator/i, ja: /運営者の承認/,
    ko: /운영자 승인/, ru: /одобрение оператора/i,
    tr: /operatör onayı/i, ar: /موافقة المشغّل/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), approval[c],
      `dd.tg_linked:${c} dropped "still needs operator approval" — linking would `
        + 'read as having enabled live trading');
  }
});

test('the intro keeps its markup in every language', () => {
  // data-i18n-html is not involved here (this is script-built), so the <b> tags
  // are part of the string and a language that loses them loses the emphasis.
  const e = i18n.STRINGS['dd.tg_intro'];
  const tags = (v) => (String(v).match(/<\/?[a-z]+>/g) || []).join(',');
  for (const c of codes) {
    assert.equal(tags(e[c]), tags(e.en), `dd.tg_intro:${c} changed its markup`);
  }
});

test('no Telegram string is left as a bare English literal', () => {
  for (const g of ['>Generate link token<', 'Your link token (valid 10 min)',
    'Could not generate a token — try again.']) {
    const bare = dash.split(g).length - 1;
    const inT = (dash.match(new RegExp("T\\('dd\\.tg_[a-z_]+', '?" + g.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
    assert.equal(bare - inT, 0, `${g} still appears as a bare literal`);
  }
});
