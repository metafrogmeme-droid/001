'use strict';
// ONE marker, five prose copies, and the comment that said they were covered.
//
// `computesOnInput: true` on a tool in routes/mcp.js is the machine-readable
// answer to "does this evaluate what the CALLER sends, rather than serving what
// the public site publishes?". lib/tool8257.js derives `toolFamilies` from it,
// under a comment reading: "Derived from the registry, never hand-maintained:
// an agent can tell which tools answer from published data and which evaluate
// what IT sends, WITHOUT PARSING THE PROSE ABOVE. This is what stops the
// manifest from silently over-claiming again the next time a tool family is
// added."
//
// A tool WAS then added to the family — xray_transaction, the calldata decoder
// — and every piece of prose went stale exactly as that comment anticipated,
// while the comment's own reassurance reads as though the prose is covered. It
// covers the machine-readable half. Five hand-written copies said four: the
// manifest description, this page's dv.guardian_p in all fourteen languages,
// the block comment above TOOLS, docs/INCOME_MAP.md, and mcp_guardian_tools'
// own GUARDIAN list — so every safety property THAT file checks had never once
// been checked on xray_transaction.
//
// THE NAMES ARE LANGUAGE-INVARIANT AND THE COUNT IS NOT. That is the whole
// design. A numeral in fourteen languages is fourteen numeral WORDS, so
// guarding it needs a fourteen-row numeral table — itself the second copy this
// file exists to remove. The tool identifiers are CODE: `scan_transaction`
// appears verbatim in the Japanese and Arabic copy alike. So the prose names
// the family and states no size, and ONE rule drives all fourteen languages.
//
// It also checks the stronger claim. A count can be right while the list is
// wrong; naming every member and no non-member cannot be.
//
// WHAT THIS DOES NOT CHECK, stated because a guard whose coverage is
// overstated is the failure this repo is organised around. A numeral authored
// only inside a TRANSLATION — English says "the ones named here", a translator
// writes "four of them" — is invisible here, because the numeral-word check
// runs over the English sources alone. The list check still holds in that
// language, so the damage is bounded to a numeral disagreeing with a correct
// list beside it.

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { TOOLS } = require('../routes/mcp');
const t8257 = require('../lib/tool8257');
const i18n = require('../public/js/i18n');

const family = (tools = TOOLS) =>
  Object.keys(tools).filter((n) => tools[n].computesOnInput);
const published = (tools = TOOLS) =>
  Object.keys(tools).filter((n) => !tools[n].computesOnInput);

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

// The claim each copy makes is "these are the tools that evaluate what you
// send". `names` must hold every family member and no published-data one.
function namesTheFamily(text, tools = TOOLS) {
  return {
    missing: family(tools).filter((n) => !text.includes(n)),
    strays: published(tools).filter((n) => text.includes(n)),
  };
}

// The per-language walk, as a function so it can be driven with a planted
// dictionary. The mutation round could not otherwise reach its untranslated
// branch: nothing it can do to a healthy tree makes a translation absent.
function problemsPerLanguage(entry, langs, tools = TOOLS) {
  const out = [];
  for (const { code } of langs) {
    if (typeof entry[code] !== 'string') { out.push(`${code}: no translation`); continue; }
    const { missing, strays } = namesTheFamily(entry[code], tools);
    if (missing.length) out.push(`${code}: does not name ${missing.join(', ')}`);
    if (strays.length) out.push(`${code}: names ${strays.join(', ')} as caller-input`);
  }
  return out;
}

test('both families are registered, or everything below proves nothing', () => {
  assert.ok(family().length > 0, 'no caller-input tools registered');
  assert.ok(published().length > 0, 'no published-data tools registered');
});

test('the developers page names every caller-input tool, in every language', () => {
  const entry = i18n.STRINGS['dv.guardian_p'];
  assert.ok(entry, 'dv.guardian_p exists');
  // The languages come from i18n.LANGS, not from the entry's own keys. Reading
  // the entry would make a MISSING translation invisible — the sweep would
  // simply not visit it — and a hard-coded count here would be a second copy
  // of the threshold i18n.test.js already owns. STRINGS[key][lang] directly,
  // never translate(): a guard that resolves through the English fallback
  // cannot see a missing translation.
  assert.deepEqual(problemsPerLanguage(entry, i18n.LANGS), []);
});

test('the lede names Guardian rather than claiming everything serves published data', () => {
  // tool8257_families.test.js exists because the MANIFEST said "Every tool
  // serves data the public site already publishes" after the Guardian tools
  // shipped. That was fixed in buildManifest and left standing in the lede of
  // the page describing it, contradicted by its own next panel.
  //
  // `Guardian` is a product name and survives translation verbatim, so the
  // repair is checkable in all fourteen languages by one rule. What this
  // cannot see is a translation that names Guardian and still over-claims;
  // the English wording is pinned separately below.
  const entry = i18n.STRINGS['dv.lede'];
  assert.ok(entry, 'dv.lede exists');
  for (const { code: lang } of i18n.LANGS) {
    assert.ok(typeof entry[lang] === 'string', `${lang}: no dv.lede translation`);
    assert.ok(entry[lang].includes('Guardian'),
      `${lang}: the lede does not name the family that evaluates caller input`);
  }
  assert.doesNotMatch(
    entry.en, /Everything here is[^.]*and serves the same data/,
    'the English lede still claims everything serves published data');
});

test('the English sources state no family size', () => {
  // A count in prose is the part that rots first, and here it was written in
  // fourteen numeral words. The sources a numeral would be authored in are
  // English; the translations follow them.
  const numerals = /\b(two|three|four|five|six|seven)\b/i;
  const claims = [
    ['developers.html', read('public', 'developers.html'), /(?:Four|Five|Three) of them answer/i],
    ['dv.guardian_p (en)', i18n.STRINGS['dv.guardian_p'].en, /(?:Four|Five|Three) of them answer/i],
  ];
  for (const [what, text, re] of claims) {
    assert.doesNotMatch(text, re, `${what} states how many tools are in the family`);
  }
  // the block comment above TOOLS said "These four" four lines above five markers
  const mcp = read('routes', 'mcp.js');
  const block = mcp.slice(mcp.indexOf('const TOOLS = {'), mcp.indexOf('scan_transaction: {'));
  assert.doesNotMatch(block, new RegExp(`These ${numerals.source}`, 'i'),
    'the TOOLS block comment counts the family instead of naming the marker');
});

test('the manifest description names the family it derives', () => {
  const m = t8257.buildManifest({ tools: TOOLS });
  const { missing, strays } = namesTheFamily(m.description);
  assert.deepEqual(missing, [],
    `the manifest prose does not name ${missing.join(', ')}`);
  assert.deepEqual(strays, [],
    `the manifest prose names ${strays.join(', ')} as caller-input`);
  assert.deepEqual(m.toolFamilies.callerInput, [...family()].sort(),
    'toolFamilies disagrees with the marker');
});

test('the manifest prose and toolFamilies both derive from the registry', () => {
  // Planting a tool the real registry does not have is the only way to prove
  // the prose is derived rather than typed: a hand-written list agrees with
  // every real fixture and diverges the moment the registry gains a member.
  const planted = {
    ...TOOLS,
    zzz_planted_probe: { computesOnInput: true, description: 'x', inputSchema: {} },
  };
  const m = t8257.buildManifest({ tools: planted });
  assert.ok(m.description.includes('zzz_planted_probe'),
    'the manifest prose does not derive its family from the registry');
  assert.ok(m.toolFamilies.callerInput.includes('zzz_planted_probe'),
    'toolFamilies does not derive its family from the registry');
});

test('buildManifest computes the split ONCE', () => {
  // A SCAN, and the reason is worth stating: the mutation round gave
  // `toolFamilies` its own inline walk of the same marker and it SURVIVED
  // everything above, because two walks of one predicate over one argument
  // agree on every input. That is an equivalent mutant, so no drive can tell
  // them apart — and the test named "one walk, not two" was claiming a check
  // it could not make. The claim is about code SHAPE, so shape is what is
  // asserted, which is the narrow case where a source read is the honest
  // instrument rather than a substitute for behaviour.
  const src = read('lib', 'tool8257.js');
  const start = src.indexOf('function buildManifest(');
  assert.ok(start > 0, 'buildManifest is defined');
  const body = src.slice(start, src.indexOf('\nfunction ', start + 1));
  const walks = body.match(/computesOnInput/g) || [];
  assert.equal(walks.length, 2,
    `buildManifest reads the marker ${walks.length} times; it should split once `
    + '(one filter per family) and let both the prose and toolFamilies read that');
});

test('the rule can actually fail', () => {
  // Every rule above passes against the real tree, so a mutation of the rule
  // itself changes no verdict. Drive it where it is the only thing in play.
  const planted = {
    ...TOOLS,
    zzz_unadvertised: { computesOnInput: true, description: 'x', inputSchema: {} },
  };
  const { missing } = namesTheFamily(i18n.STRINGS['dv.guardian_p'].en, planted);
  assert.deepEqual(missing, ['zzz_unadvertised'],
    'a family member the page does not name goes unreported');

  const strayed = { ...TOOLS, get_gas: { ...TOOLS.get_gas } };
  const withStray = namesTheFamily('scan_transaction xray_transaction compile_intent '
    + 'stress_portfolio plan_escape get_gas', strayed);
  assert.deepEqual(withStray.strays, ['get_gas'],
    'a published-data tool named as caller-input goes unreported');

  // An ABSENT translation, which no healthy tree can produce: the sweep must
  // report it rather than skip the language it cannot read.
  const langs = [{ code: 'en' }, { code: 'zz' }];
  assert.deepEqual(
    problemsPerLanguage({ en: i18n.STRINGS['dv.guardian_p'].en }, langs),
    ['zz: no translation'],
    'a language with no translation is skipped instead of reported');
});
