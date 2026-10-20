'use strict';
/**
 * The pre-registered studies, on the public website.
 *
 * `benchmark/studies.json` names every study whose question, data, windows
 * and pass rule were sealed before the result was computed. It was read by
 * the README and by `scripts/verify_evidence.py` only, so the agents page,
 * where a visitor decides whether a strategy card means anything, never said
 * which rules had been tested and failed.
 *
 * GET /api/public/studies serves the website's copy of the registry
 * (`app/content/studies.json`, written by `app/scripts/sync_content.js`;
 * `the_website_carries_what_it_serves.test.js` holds the copy to the
 * original). These drive what the route serves, what it refuses, and what
 * the page paints in each state.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const { codeOnly } = require('./helpers/code_only');
const studies = require('../lib/studies');
const panel = require('../public/js/studies-panel');

const APP = path.join(__dirname, '..');
const REPO = path.join(APP, '..');
const ORIGINAL = JSON.parse(fs.readFileSync(path.join(REPO, 'benchmark', 'studies.json'), 'utf8'));

function tmpFile(content) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'studies-'));
  const f = path.join(dir, 'studies.json');
  fs.writeFileSync(f, typeof content === 'string' ? content : JSON.stringify(content));
  return f;
}

function clone(o) { return JSON.parse(JSON.stringify(o)); }

function serve(router) {
  const app = express();
  app.use('/api/public/studies', router);
  const server = http.createServer(app);
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function get(server, p) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: server.address().port, path: p }, (res) => {
      let b = ''; res.on('data', (c) => { b += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: b ? JSON.parse(b) : {} }));
    }).on('error', reject);
  });
}

/** Drive the real route with the real reader pointed at `file`. */
async function routeReading(file) {
  const real = studies.readRegistry;
  studies.readRegistry = () => real(file);
  const routePath = require.resolve('../routes/public_studies');
  delete require.cache[routePath];
  const server = await serve(require(routePath));
  try {
    return await get(server, '/api/public/studies');
  } finally {
    server.close();
    studies.readRegistry = real;
    delete require.cache[routePath];
  }
}

// ── the route ─────────────────────────────────────────────────────────────

test('the route serves every registered study, its counts and its measured rows as recorded', async () => {
  const r = await routeReading(studies.REGISTRY_PATH);
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.body.studies.map((s) => s.id), ORIGINAL.studies.map((s) => s.id));
  assert.strictEqual(r.body.verify, 'python3 scripts/verify_evidence.py');
  for (const [i, want] of ORIGINAL.studies.entries()) {
    const got = r.body.studies[i];
    assert.strictEqual(got.tests, want.tests, want.id);
    assert.strictEqual(got.passes, want.passes, want.id);
    assert.strictEqual(got.prereg_sha256, want.prereg_sha256, want.id);
    assert.strictEqual(got.prereg, want.prereg, want.id);
    assert.strictEqual(got.verdict, want.verdict, want.id);
    assert.strictEqual(got.results.length, want.results.length, want.id);
    for (const [j, row] of want.results.entries()) {
      const out = got.results[j];
      assert.strictEqual(out.name, row.rule || row.run);
      for (const k of studies.NUMBER_FIELDS) {
        if (k in row) assert.strictEqual(out[k], row[k], `${want.id} ${out.name} ${k}`);
      }
      if ('years_up' in row) assert.strictEqual(out.years_up, row.years_up, `${want.id} ${out.name} years_up`);
      if (Array.isArray(row.bootstrap90_full_sharpe)) {
        assert.deepStrictEqual(out.bootstrap90_full_sharpe, row.bootstrap90_full_sharpe);
      }
      if (typeof row.pass === 'boolean') assert.strictEqual(out.pass, row.pass, `${want.id} ${out.name}`);
      else assert.ok(!('pass' in out), `${want.id} ${out.name}: no verdict is served as no verdict, not false`);
    }
  }
});

test('the registry as committed has a pass and a failure, and a benchmark row with no verdict', async () => {
  // The fixture the panel tests below need is the real one: both arms exist.
  const body = studies.publicRegistry(studies.readRegistry());
  const rows = body.studies.flatMap((s) => s.results);
  assert.ok(rows.some((r) => r.pass === true));
  assert.ok(rows.some((r) => r.pass === false));
  assert.ok(rows.some((r) => /benchmark/.test(r.name) && !('pass' in r)));
});

test('an unreadable registry is a 503 naming the class, never a 200 with no studies', async () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'studies-')), 'absent.json');
  let r = await routeReading(missing);
  assert.strictEqual(r.status, 503);
  assert.deepStrictEqual(r.body, { error: 'studies_unreadable', exception: 'Error' });
  r = await routeReading(tmpFile('{ not json'));
  assert.deepStrictEqual(r.body, { error: 'studies_unreadable', exception: 'SyntaxError' });
  r = await routeReading(tmpFile({ format: 'something.else', studies: [] }));
  assert.deepStrictEqual(r.body, { error: 'studies_unreadable', exception: 'StudiesRegistryInvalid' });
  r = await routeReading(tmpFile({ format: studies.FORMAT }));
  assert.deepStrictEqual(r.body, { error: 'studies_unreadable', exception: 'StudiesRegistryInvalid' });
  // A registry that read and holds no study is a read, and says so.
  r = await routeReading(tmpFile({ format: studies.FORMAT, studies: [] }));
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.body.studies, []);
});

test('only the named fields are served: an account figure or a mistyped value is left out', () => {
  const reg = clone(ORIGINAL);
  const row = reg.studies[0].results[0];
  row.pnl_usd = 1234.5;
  row.equity = 99999;
  row.holdout_sharpe = '0.24';
  row.years_up = '12 of 13';
  reg.studies[0].balance_usd = 10;
  reg.studies[0].prereg = '../../.env';
  const out = studies.publicRegistry(reg).studies[0];
  const served = out.results[0];
  assert.ok(!('pnl_usd' in served) && !('equity' in served));
  assert.ok(!('holdout_sharpe' in served), 'a string where a ratio belongs is not a ratio');
  assert.ok(!('years_up' in served), 'a count is "k/n" as the registry writes it');
  assert.ok(!('balance_usd' in out));
  assert.ok(!('prereg' in out), 'a link leaves the research tree only by not being served');
  // The other arm: the measured fields of the same row are still there.
  assert.strictEqual(served.dev_sharpe, ORIGINAL.studies[0].results[0].dev_sharpe);
  assert.strictEqual(served.holdout_max_dd_pct, ORIGINAL.studies[0].results[0].holdout_max_dd_pct);
});

test('prose that names a dollar amount refuses the registry; percents and venue names do not', () => {
  for (const words of ['risked $500 a trade', 'a 1,000 USD account', 'made 40 dollars', '$ 25 per side']) {
    const reg = clone(ORIGINAL);
    reg.studies[1].limits = words;
    assert.throws(() => studies.publicRegistry(reg), (e) => e.name === 'StudiesRegistryInvalid', words);
  }
  const reg = clone(ORIGINAL);
  reg.note = 'costs $10 to verify';
  assert.throws(() => studies.publicRegistry(reg), (e) => e.name === 'StudiesRegistryInvalid');
  // The committed prose is percents, ratios, counts and "USDT-M perpetual".
  assert.ok(ORIGINAL.studies.some((s) => /USDT/.test(s.data)));
  assert.doesNotThrow(() => studies.publicRegistry(ORIGINAL));
});

test('a pass count the registry did not record is null, and the panel says so instead of 0 of n', () => {
  const reg = clone(ORIGINAL);
  delete reg.studies[0].passes;
  reg.studies[1].passes = 'none';
  const out = studies.publicRegistry(reg).studies;
  assert.strictEqual(out[0].passes, null);
  assert.strictEqual(out[1].passes, null);
  assert.strictEqual(out[2].passes, ORIGINAL.studies[2].passes, 'the recorded count is untouched');
  const html = panel.render({ studies: [out[0]] });
  assert.ok(html.includes('pass count not recorded'));
  assert.ok(!html.includes(`0 of ${ORIGINAL.studies[0].tests} passed`));
});

// ── the panel ─────────────────────────────────────────────────────────────

const BODY = studies.publicRegistry(ORIGINAL);

test('the panel names every study, its tally, and each rule with its result', () => {
  const html = panel.render(BODY);
  for (const s of ORIGINAL.studies) {
    assert.ok(html.includes(s.title.replace(/&/g, '&amp;')), s.id);
    assert.ok(html.includes(`${s.passes} of ${s.tests} passed`), s.id);
    for (const r of s.results) assert.ok(html.includes(r.rule || r.run), r.rule || r.run);
  }
  assert.ok(html.includes('python3 scripts/verify_evidence.py'));
  assert.ok(html.includes('https://github.com/metafrogmeme-droid/001/blob/main/' + ORIGINAL.studies[0].prereg));
});

test('a pass is the one coloured word; a failure is said in words; a benchmark has no verdict', () => {
  const kraken = BODY.studies.find((s) => s.id === 'kraken_spot_history');
  const html = panel.render({ studies: [kraken] });
  assert.strictEqual((html.match(/class="st-pass"/g) || []).length, 1, 'one rule passed');
  assert.strictEqual((html.match(/class="st-fail"/g) || []).length, 4, 'four did not');
  // Anchored to the row's own header: the verdict prose names the rule too.
  const h1b = html.slice(html.indexOf('<th scope="row">H1b trend long-only</th>'));
  assert.ok(h1b.slice(0, h1b.indexOf('</tr>')).includes('st-pass'));
  const bitget = panel.render({ studies: [BODY.studies.find((s) => s.id === 'bitget_slow_rules')] });
  const bench = bitget.slice(bitget.indexOf('<th scope="row">BTC buy-and-hold'));
  const benchRow = bench.slice(0, bench.indexOf('</tr>'));
  assert.ok(benchRow.includes('not a pass/fail test') && !benchRow.includes('st-pass') && !benchRow.includes('st-fail'));
  // The numbers carry no colour of their own.
  assert.ok(!/class="[^"]*\b(up|down)\b/.test(html));
});

test('a study shows only the columns its rows carry, and an unrecorded value is a dash, never zero', () => {
  const recost = BODY.studies.find((s) => s.id === 'funding_recost');
  const html = panel.render({ studies: [recost] });
  assert.ok(!html.includes('Development Sharpe'), 'a re-costing run had no development window');
  assert.ok(html.includes('Holdout-window Sharpe') && html.includes('Funding share of gross profit'));
  const bench = html.slice(html.indexOf('<th scope="row">BTC buy-and-hold'));
  const benchRow = bench.slice(0, bench.indexOf('</tr>'));
  assert.ok(benchRow.includes('title="not recorded">—'), 'the benchmark has no funding share');
  assert.ok(!benchRow.includes('0.0%'));
  // Both arms: a recorded 0.0 prints as 0.0%.
  const none = html.slice(html.indexOf('<th scope="row">no funding</th>'));
  assert.ok(none.slice(0, none.indexOf('</tr>')).includes('<td>0.0%</td>'));
  // A value recorded as null is a dash too, never 0.00.
  const nulled = clone(recost);
  nulled.results[0].sharpe = null;
  const nh = panel.render({ studies: [nulled] });
  const nrow = nh.slice(nh.indexOf('<th scope="row">no funding</th>'));
  const cells = nrow.slice(0, nrow.indexOf('</tr>'));
  assert.ok(cells.includes('title="not recorded">—') && !cells.includes('<td>0.00</td>'));
  // Negative ratios read with a minus sign, at the precision recorded.
  const funding = panel.render({ studies: [BODY.studies.find((s) => s.id === 'funding_rules')] });
  assert.ok(funding.includes('<td>−1.38</td>') && funding.includes('[−1.98, 0.06]'));
});

test('an unreadable registry paints a sentence and no table; a malformed 200 is unreadable too', () => {
  for (const html of [panel.unreadable('SyntaxError'), panel.render(null), panel.render({ format: 'x' })]) {
    assert.ok(html.includes('could not be read'));
    assert.ok(!html.includes('<table'));
    assert.ok(!html.includes('No study has been registered'));
  }
  assert.ok(panel.unreadable('SyntaxError').includes('(SyntaxError)'));
  // A registry that read with no studies says that instead.
  const empty = panel.render({ studies: [], verify: 'x' });
  assert.ok(empty.includes('No study has been registered yet.') && !empty.includes('could not be read'));
});

test('every word the panel prints is in all fourteen languages, with its placeholders', () => {
  const { STRINGS, LANGS } = require('../public/js/i18n.js');
  const src = codeOnly(fs.readFileSync(path.join(APP, 'public', 'js', 'studies-panel.js'), 'utf8'));
  const page = fs.readFileSync(path.join(APP, 'public', 'agents.html'), 'utf8');
  const keys = new Set([...src.matchAll(/'(studies\.[a-z_]+)'/g)].map((m) => m[1]));
  for (const m of page.matchAll(/data-i18n="(studies\.[a-z_]+)"/g)) keys.add(m[1]);
  assert.ok(keys.size >= 30, `found ${keys.size} keys`);
  const codes = LANGS.map((l) => l.code);
  assert.strictEqual(codes.length, 14);
  for (const k of keys) {
    const entry = STRINGS[k];
    assert.ok(entry, `${k} is not in i18n.js`);
    const ph = (entry.en.match(/\{\w+\}/g) || []).sort().join();
    for (const c of codes) {
      assert.ok(typeof entry[c] === 'string' && entry[c], `${k} has no ${c}`);
      assert.strictEqual((entry[c].match(/\{\w+\}/g) || []).sort().join(), ph, `${k} ${c} placeholders`);
    }
  }
});

test('the agents page loads the panel, asks the route, and keeps it apart from the catalogue', () => {
  const page = fs.readFileSync(path.join(APP, 'public', 'agents.html'), 'utf8');
  assert.ok(/<script src="\/js\/studies-panel\.js\?v=\d+"><\/script>/.test(page));
  assert.ok(page.includes('id="st-root"'));
  const code = codeOnly(page);
  const start = code.indexOf("fetch('/api/public/studies'");
  assert.ok(start !== -1);
  const loader = code.slice(start, code.indexOf('})();', start));
  assert.ok(loader.includes('StudiesPanel.render(body, T)') && loader.includes('StudiesPanel.unreadable('));
  assert.ok(!loader.includes('ag-root'), 'a failed study read does not touch the catalogue');
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.ok(server.includes("app.use('/api/public/studies', require('./routes/public_studies'))"));
});

test('a deploy of app/ alone reads the registry; without its copy it refuses', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'app-only-studies-'));
  fs.mkdirSync(path.join(root, 'app', 'lib'), { recursive: true });
  fs.copyFileSync(path.join(APP, 'lib', 'studies.js'), path.join(root, 'app', 'lib', 'studies.js'));
  const bare = require(path.join(root, 'app', 'lib', 'studies.js'));
  assert.throws(() => bare.readRegistry(), (e) => e.code === 'ENOENT');
  fs.mkdirSync(path.join(root, 'app', 'content'));
  fs.copyFileSync(path.join(APP, 'content', 'studies.json'), path.join(root, 'app', 'content', 'studies.json'));
  assert.deepStrictEqual(bare.readRegistry().studies.map((s) => s.id), ORIGINAL.studies.map((s) => s.id));
});
