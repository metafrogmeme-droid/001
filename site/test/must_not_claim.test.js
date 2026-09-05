/**
 * `facts.ts` carries MUST_NOT_CLAIM — the list of things the site must never
 * assert — and until now nothing read it. A list nobody consults while writing
 * a headline is a doc, and the file's own comment explains why it is code
 * instead. This closes the loop: every published page, the 404, and llms.txt
 * are scanned for the vocabulary each forbidden claim would need, over the
 * BUILT OUTPUT, so a claim cannot arrive through a component either.
 *
 * Vocabulary, not sentences: "$RCLAW" and "staking" cannot appear at all on a
 * site that says no token exists; a negation ("no token exists") is allowed
 * because the words that would assert the thing — the ticker, "staking",
 * "vault", "DAO" — are what is banned, and the negation does not need them.
 */
import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.join(HERE, '..');
const REPO = path.join(SITE, '..');
const OUT = path.join(REPO, 'website');

/** Each MUST_NOT_CLAIM entry, as the vocabulary that would be needed to make it. */
const VOCAB = {
  '$RCLAW token, staking, fee discounts, or any token-dependent reward': [
    /\$RCLAW/i, /\bstak(?:e|ing)\b/i, /fee discount/i,
  ],
  'agent vaults (ERC-4626) or any deposit-taking product': [
    /ERC-?4626/i, /\bvaults?\b/i, /deposit (?:your|funds|stablecoins)/i,
  ],
  'DAO governance or on-chain performance-fee splits': [/\bDAO\b/, /performance[- ]fee/i],
  'copy-trading revenue share, marketplace billing, or creator earnings': [
    /revenue share/i, /creator earnings/i,
  ],
  'idle-margin yield as anything but a read-only rate display': [
    /park(?:ed|ing)? (?:your )?(?:idle )?(?:margin|funds|stablecoins)/i,
  ],
  'a completed independent security audit': [
    /independently audited/i, /audited by/i, /(?:security|smart-contract) audit (?:is )?complete/i,
  ],
  'any live market price baked into the page': [/\$\d{1,3}(?:,\d{3})+(?:\.\d+)?/],
};

function pages() {
  const out = [];
  const walk = (dir, rel) => {
    for (const name of fs.readdirSync(dir)) {
      if (name === 'archive' || name === 'assets') continue;   // the archive is frozen and labelled
      const p = path.join(dir, name);
      const r = rel ? `${rel}/${name}` : name;
      if (fs.statSync(p).isDirectory()) walk(p, r);
      else if (name.endsWith('.html') || name === 'llms.txt') out.push(r);
    }
  };
  walk(OUT, '');
  return out;
}

function visible(file) {
  return fs.readFileSync(path.join(OUT, file), 'utf8')
    .replace(/<script[\s\S]*?<\/script>/g, ' ')
    .replace(/<[^>]+>/g, ' ');
}

test('the forbidden-claims list in facts.ts is the list this test reads', () => {
  const src = fs.readFileSync(path.join(SITE, 'src', 'facts.ts'), 'utf8');
  const arr = src.match(/MUST_NOT_CLAIM[^=]*=\s*\[([\s\S]*?)\]/);
  assert.ok(arr, 'MUST_NOT_CLAIM is gone from facts.ts');
  const entries = [...arr[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
  assert.deepStrictEqual(entries.sort(), Object.keys(VOCAB).sort(),
    'facts.ts and this test disagree about what is forbidden — add the vocabulary for the new entry here');
});

const PAGES = pages();

test('there are pages to scan', () => {
  assert.ok(PAGES.length >= 6, `only ${PAGES.length} built pages — is the build stale?`);
  assert.ok(PAGES.includes('404.html'), 'the 404 page is built');
});

for (const file of PAGES) {
  test(`${file} makes none of the forbidden claims`, () => {
    const text = visible(file);
    for (const [claim, patterns] of Object.entries(VOCAB)) {
      for (const re of patterns) {
        const hit = text.match(re);
        assert.ok(!hit, `${file} says ${JSON.stringify(hit && hit[0])} — that is the vocabulary of "${claim}", which facts.ts forbids`);
      }
    }
  });
}

test('the control: the vocabulary scan catches a planted claim', () => {
  const planted = visible('index.html') + ' Stake $RCLAW for fee discounts.';
  assert.ok(VOCAB['$RCLAW token, staking, fee discounts, or any token-dependent reward']
    .some((re) => re.test(planted)));
});
