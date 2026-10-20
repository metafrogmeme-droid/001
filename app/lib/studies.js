'use strict';
/**
 * The pre-registered studies, as the public route serves them.
 *
 * `benchmark/studies.json` is the registry: each study's question, data,
 * windows and pass rule were sealed (SHA-256 beside the pre-registration,
 * with the time) before its result was computed, and
 * `scripts/verify_evidence.py` recomputes every seal from the committed file.
 * The README's studies table is generated from the same registry. This reads
 * the website's COPY, `app/content/studies.json`, because the web deploy
 * ships `app/` alone (`app/scripts/sync_content.js` writes it, and a test
 * fails when it differs from the original).
 *
 * A registry that cannot be read THROWS, and the route answers 503 with the
 * class. An empty list would read as "no studies were run", which is a claim
 * about the research, not about the file.
 *
 * WHAT IS SERVED IS AN ALLOWLIST. The page is public, so percent, ratio and
 * count only. Each study keeps its named prose fields and each result row
 * keeps the measured fields named below: a field the registry gains later is
 * left out until it is named here, never forwarded on trust. A value of the
 * wrong type is left out too, so the row says "not recorded" for it rather
 * than printing whatever arrived. Prose that names a dollar amount refuses
 * the whole registry, because the registry's own rule is that its figures
 * are percent, ratio and count.
 */

const fs = require('node:fs');
const path = require('node:path');

const REGISTRY_PATH = path.join(__dirname, '..', 'content', 'studies.json');
const FORMAT = 'runeclaw.studies.v1';

/** Study prose, served as written. */
const TEXT_FIELDS = ['title', 'question', 'data', 'pass_rule', 'verdict', 'limits'];

/**
 * Measured fields a result row may carry. Sharpe ratios, percents and a count
 * of calendar years; none is an account's money.
 */
const NUMBER_FIELDS = [
  'dev_sharpe', 'holdout_sharpe', 'sharpe', 'holdout_window_sharpe',
  'cagr_pct', 'max_dd_pct', 'holdout_max_dd_pct',
  'funding_share_of_gross_profit_pct',
];

/** `years_up` is a count of calendar years as the registry writes it: "8/13". */
const YEARS_UP = /^\d{1,3}\/\d{1,3}$/;

/** A repository path the page links to: under the research tree, no `..`. */
const REPO_PATH = /^docs\/research\/prereg\/[A-Za-z0-9_][A-Za-z0-9_./-]*$/;
const SHA256 = /^[0-9a-f]{64}$/;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/** A dollar sign before a number, or a number named in dollars. */
const MONEY = /\$\s?\d|\b\d[\d,.]*\s?(?:USD|USDT|dollars?)\b/i;

class StudiesRegistryInvalid extends Error {
  constructor(message) {
    super(message);
    this.name = 'StudiesRegistryInvalid';
  }
}

const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const finite = (v) => typeof v === 'number' && Number.isFinite(v);
const count = (v) => (Number.isInteger(v) && v >= 0 ? v : null);

/** The registry, validated. A missing file or bad JSON throws as itself. */
function readRegistry(file) {
  const data = JSON.parse(fs.readFileSync(file || REGISTRY_PATH, 'utf8'));
  if (!isObject(data)) throw new StudiesRegistryInvalid('the registry is not an object');
  if (data.format !== FORMAT) throw new StudiesRegistryInvalid('the registry format is not ' + FORMAT);
  if (!Array.isArray(data.studies)) throw new StudiesRegistryInvalid('the registry has no studies list');
  for (const s of data.studies) {
    if (!isObject(s) || typeof s.id !== 'string' || !s.id) {
      throw new StudiesRegistryInvalid('a study has no id');
    }
  }
  return data;
}

function publicRow(row) {
  if (!isObject(row)) return null;
  const out = {};
  const name = typeof row.rule === 'string' ? row.rule : (typeof row.run === 'string' ? row.run : null);
  if (name === null) return null;
  out.name = name;
  for (const k of NUMBER_FIELDS) {
    if (own(row, k) && (finite(row[k]) || row[k] === null)) out[k] = row[k];
  }
  if (typeof row.years_up === 'string' && YEARS_UP.test(row.years_up)) out.years_up = row.years_up;
  const b = row.bootstrap90_full_sharpe;
  if (Array.isArray(b) && b.length === 2 && finite(b[0]) && finite(b[1])) {
    out.bootstrap90_full_sharpe = [b[0], b[1]];
  }
  // A row that is not a pass/fail test (a benchmark, or a re-costing run)
  // carries no `pass` or a null one; it stays absent, never false.
  if (typeof row.pass === 'boolean') out.pass = row.pass;
  return out;
}

function publicStudy(s) {
  const out = { id: s.id };
  for (const k of TEXT_FIELDS) {
    if (typeof s[k] !== 'string') continue;
    if (MONEY.test(s[k])) throw new StudiesRegistryInvalid(`study ${s.id} names a dollar amount in ${k}`);
    out[k] = s[k];
  }
  if (typeof s.sealed_at === 'string' && ISO_TIME.test(s.sealed_at)) out.sealed_at = s.sealed_at;
  if (Array.isArray(s.resealed_for_addenda_at)) {
    out.resealed_for_addenda_at = s.resealed_for_addenda_at.filter((t) => typeof t === 'string' && ISO_TIME.test(t));
  }
  for (const k of ['prereg', 'report']) {
    if (typeof s[k] === 'string' && REPO_PATH.test(s[k]) && !s[k].includes('..')) out[k] = s[k];
  }
  if (typeof s.prereg_sha256 === 'string' && SHA256.test(s.prereg_sha256)) out.prereg_sha256 = s.prereg_sha256;
  if (isObject(s.windows)) {
    const w = {};
    for (const k of ['development', 'holdout']) {
      if (typeof s.windows[k] === 'string') w[k] = s.windows[k];
    }
    out.windows = w;
  }
  // Counts: null when the registry's value is not a count, never 0.
  out.tests = own(s, 'tests') ? count(s.tests) : null;
  out.passes = own(s, 'passes') ? count(s.passes) : null;
  out.results = Array.isArray(s.results) ? s.results.map(publicRow).filter(Boolean) : [];
  return out;
}

/** What GET /api/public/studies answers. Throws on a dollar amount in prose. */
function publicRegistry(registry) {
  const note = typeof registry.note === 'string' ? registry.note : null;
  if (note !== null && MONEY.test(note)) throw new StudiesRegistryInvalid('the registry note names a dollar amount');
  return {
    format: registry.format,
    note,
    verify: 'python3 scripts/verify_evidence.py',
    studies: registry.studies.map(publicStudy),
  };
}

module.exports = {
  REGISTRY_PATH, FORMAT, TEXT_FIELDS, NUMBER_FIELDS, YEARS_UP, MONEY,
  StudiesRegistryInvalid, readRegistry, publicRegistry,
};
