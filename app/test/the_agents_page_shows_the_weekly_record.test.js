'use strict';
/**
 * The weekly public record, on the public website.
 *
 * `scripts/weekly_record.py` freezes a week into `benchmark/record/` on the
 * bot's server; the operator commits it; `app/scripts/sync_content.js` copies
 * it into `app/content/record/`, and GET /api/public/record serves it through
 * the allowlist in app/lib/weekly_record.js. These drive what the route
 * serves, what it refuses, and what the panel paints in each state.
 *
 * The fixture `fixtures/public_record_week.json` is what the Python builder
 * writes from fixed inputs (tests/test_the_weekly_public_record.py holds it
 * to the builder), so a field renamed on one side fails here.
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
const weekly = require('../lib/weekly_record');
const panel = require('../public/js/record-panel');

const APP = path.join(__dirname, '..');
const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'public_record_week.json'), 'utf8'));
const clone = (o) => JSON.parse(JSON.stringify(o));

function dirWith(weeks) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'record-'));
  for (const [name, content] of Object.entries(weeks)) {
    fs.writeFileSync(path.join(dir, name), typeof content === 'string' ? content : JSON.stringify(content));
  }
  return dir;
}

function week(name, patch) {
  const rec = clone(FIXTURE);
  rec.week = name;
  if (patch) patch(rec);
  return rec;
}

function serve(router) {
  const app = express();
  app.use('/api/public/record', router);
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

/** Drive the real route with the real reader pointed at `dir`. */
async function routeReading(dir, p) {
  const real = weekly.publicRecord;
  weekly.publicRecord = (opts) => real({ ...opts, dir });
  const routePath = require.resolve('../routes/public_record');
  delete require.cache[routePath];
  const server = await serve(require(routePath));
  try {
    return await get(server, p || '/api/public/record');
  } finally {
    server.close();
    weekly.publicRecord = real;
    delete require.cache[routePath];
  }
}

// ── the reader and the route ────────────────────────────────────────────

test('the newest committed week is served, with the list of weeks', async () => {
  const dir = dirWith({ '2026-W42.json': week('2026-W42'), '2026-W43.json': week('2026-W43'), 'README.md': '# not a week' });
  const r = await routeReading(dir);
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.body.weeks, ['2026-W43', '2026-W42']);
  assert.strictEqual(r.body.record.week, '2026-W43');
  const older = await routeReading(dir, '/api/public/record?week=2026-W42');
  assert.strictEqual(older.body.record.week, '2026-W42');
});

test('no week committed yet is a read that says so; an unknown or malformed week is refused', async () => {
  const none = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'record-')), 'record');
  let r = await routeReading(none);
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.body.weeks, []);
  assert.strictEqual(r.body.record, null);
  const dir = dirWith({ '2026-W43.json': week('2026-W43') });
  r = await routeReading(dir, '/api/public/record?week=2026-W01');
  assert.strictEqual(r.status, 404);
  assert.strictEqual(r.body.error, 'unknown_week');
  r = await routeReading(dir, '/api/public/record?week=../../etc');
  assert.strictEqual(r.status, 400);
});

test('an unreadable week is a 503 naming the class, never a 200 with no record', async () => {
  let r = await routeReading(dirWith({ '2026-W43.json': '{ not json' }));
  assert.strictEqual(r.status, 503);
  assert.deepStrictEqual(r.body, { error: 'record_unreadable', exception: 'SyntaxError' });
  r = await routeReading(dirWith({ '2026-W43.json': week('2026-W43', (x) => { x.format = 'other'; }) }));
  assert.deepStrictEqual(r.body, { error: 'record_unreadable', exception: 'WeeklyRecordInvalid' });
  // A file named for one week holding another is not that week.
  r = await routeReading(dirWith({ '2026-W43.json': week('2026-W40') }));
  assert.deepStrictEqual(r.body, { error: 'record_unreadable', exception: 'WeeklyRecordInvalid' });
  // A folder that exists and cannot be listed is not "no week yet".
  const notADir = path.join(dirWith({ 'x': 'y' }), 'x');
  assert.throws(() => weekly.listWeeks(notADir), (e) => e.code === 'ENOTDIR');
});

// ── the allowlist ───────────────────────────────────────────────────────

function paths(obj, prefix = '') {
  if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) return [prefix];
  return Object.keys(obj).flatMap((k) => paths(obj[k], prefix ? prefix + '.' + k : k));
}

test('every field the builder writes reaches the page, except the ones named as held back', () => {
  const served = new Set(paths(weekly.publicWeek(FIXTURE)));
  // Held back on purpose: a null is not a value (the panel says "not
  // recorded"), and the benchmark's provenance strings are for the operator's
  // /parity, not the public panel.
  const heldBack = new Set(['format', 'parity.benchmark.reason', 'parity.benchmark.recorded_at',
    'parity.benchmark.code_sha']);
  for (const p of paths(FIXTURE)) {
    const v = p.split('.').reduce((o, k) => o[k], FIXTURE);
    if (v === null || heldBack.has(p)) { assert.ok(!served.has(p), `${p} served`); continue; }
    assert.ok(served.has(p), `${p} was written by the builder and dropped by the allowlist`);
  }
});

test('a field the record gains, a dollar figure or a mistyped value is left out', () => {
  const rec = week('2026-W43', (x) => {
    x.equity.equity_usd = 1234.5;
    x.equity.sharpe = 'high';
    x.parity.net_pnl = 9.99;
    x.parity.verdict.edge_sentence = 'mean net +$1.20 per trade';
    x.sleeve.holds = [{ symbol: 'BTC', weight_pct: 12 }];
    x.sleeve.return_pct = 3.1;
  });
  const out = weekly.publicWeek(rec);
  const text = JSON.stringify(out);
  for (const bad of ['equity_usd', 'net_pnl', 'edge_sentence', 'holds', '1234', '$']) {
    assert.ok(!text.includes(bad), bad);
  }
  // The live book carries a return; the sleeve never does before its date.
  assert.ok(!('return_pct' in out.sleeve) && 'return_pct' in out.equity);
  assert.ok(!('sharpe' in out.equity), 'a mistyped value is left out, not printed');
  assert.strictEqual(out.equity.max_drawdown_pct, FIXTURE.equity.max_drawdown_pct);
});

test('a part unreadable when its week was written keeps its state and class, and nothing else', () => {
  const out = weekly.publicWeek(week('2026-W43', (x) => {
    x.equity = { state: 'unreadable', exception: 'RecordUnreadable', equity_usd: 5 };
    x.parity = { state: 'unreadable', exception: 'a message with /home/path in it' };
    x.sleeve = 'garbage';
  }));
  assert.deepStrictEqual(out.equity, { state: 'unreadable', exception: 'RecordUnreadable' });
  assert.deepStrictEqual(out.parity, { state: 'unreadable', exception: null });
  assert.deepStrictEqual(out.sleeve, { state: 'unreadable', exception: 'WeeklyRecordInvalid' });
});

// ── the panel ───────────────────────────────────────────────────────────

const payload = (rec) => ({ format: weekly.FORMAT, weeks: [rec.week], record: weekly.publicWeek(rec), source: 'https://example.test/r' });

function rowValue(html, label) {
  const m = html.match(new RegExp('<th scope="row">' + label.replace(/[()]/g, '\\$&') + '</th><td>(.*?)</td>'));
  return m ? m[1] : null;
}

test('a read week paints each part with its figures in the text colour', () => {
  const h = panel.render(payload(week('2026-W43')));
  assert.ok(h.includes('Week 2026-W43'));
  assert.strictEqual(rowValue(h, 'Change over the record'), '+8.76%');
  assert.strictEqual(rowValue(h, 'Largest fall from a high'), '−0.50%');
  assert.strictEqual(rowValue(h, 'Sharpe ratio (daily returns, annualised)'), '+6.27');
  assert.strictEqual(rowValue(h, 'Hit rate'), '66.7%');
  assert.strictEqual(rowValue(h, 'Profit factor'), '5.00');
  assert.strictEqual(rowValue(h, 'Live edge'), 'too few exits to judge (3 of 10)');
  assert.ok(h.includes('19 days recorded, 2026-10-01 to 2026-10-20 · 1 days with no reading'));
  assert.ok(h.includes('a deposit reads as a gain'), 'flows are said beside the figures');
  assert.ok(!/class="[^"]*(up|down|green|red|pos|neg)\b/.test(h), 'no figure is coloured');
});

test('an unrecorded value is a dash, never zero; a thin record waits for its Sharpe', () => {
  const h = panel.render(payload(week('2026-W43', (x) => {
    x.parity.fee_vs_model = null;
    delete x.equity.sharpe;
    x.equity.daily_returns = 12;
  })));
  assert.match(rowValue(h, 'Fees against the model'), /^<span class="rec-na" title="not recorded">—<\/span>$/);
  assert.strictEqual(rowValue(h, 'Sharpe ratio (daily returns, annualised)'),
    '<span class="rec-wait">after 30 daily returns (12 so far)</span>');
  // Both arms: with the returns there and no figure, it is "not recorded".
  const h2 = panel.render(payload(week('2026-W43', (x) => { delete x.equity.sharpe; })));
  assert.match(rowValue(h2, 'Sharpe ratio (daily returns, annualised)'), /rec-na/);
});

test('each verdict word is a sentence; a word the panel does not know is a dash, never printed raw', () => {
  for (const [w, [, en]] of Object.entries(panel.EDGE)) {
    const h = panel.render(payload(week('2026-W43', (x) => { x.parity.verdict.edge = w; })));
    assert.strictEqual(rowValue(h, 'Live edge'), en.replace('{n}', '3').replace('{min}', '10'));
  }
  for (const [w, [, en]] of Object.entries(panel.BALLPARK)) {
    const h = panel.render(payload(week('2026-W43', (x) => { x.parity.verdict.ballpark = w; })));
    assert.strictEqual(rowValue(h, 'Against the benchmark'), en);
  }
  const h = panel.render(payload(week('2026-W43', (x) => { x.parity.verdict.edge = 'constructor'; })));
  assert.match(rowValue(h, 'Live edge'), /rec-na/);
});

test('the sleeve shows that its record is kept and its sealed date, and nothing in a row', () => {
  const h = panel.render(payload(week('2026-W43')));
  const part = h.slice(h.indexOf('Daily trend sleeve (paper)'));
  assert.ok(part.includes('the first measurement for 2027-10-17'));
  assert.ok(part.includes('href="https://github.com/metafrogmeme-droid/001/blob/main/docs/research/daily_trend_sleeve_prereg.md"'));
  assert.ok(!part.includes('<table'), 'no figure table for the sleeve');
});

test('a part that could not be read says so in its place and the other parts stand', () => {
  const h = panel.render(payload(week('2026-W43', (x) => { x.equity = { state: 'unreadable', exception: 'RecordUnreadable' }; })));
  assert.ok(h.includes('This part could not be read when the week was written (RecordUnreadable). That is not a zero.'));
  assert.strictEqual(rowValue(h, 'Hit rate'), '66.7%');
  assert.strictEqual(rowValue(h, 'Change over the record'), null);
});

test('absent parts say what was not recorded, in their own words', () => {
  const h = panel.render(payload(week('2026-W43', (x) => {
    x.equity = { state: 'absent', days_recorded: 0, daily_returns: 0, sharpe: null };
    x.parity = { state: 'absent', trades: 0 };
  })));
  assert.ok(h.includes('No daily live equity has been recorded.'));
  assert.ok(h.includes('No live strategy exit is on record.'));
  assert.strictEqual(rowValue(h, 'Hit rate'), null, 'no hit rate over nothing');
});

test('no week yet, and an unreadable record, are sentences; a malformed 200 is unreadable', () => {
  assert.ok(panel.render({ weeks: [], record: null }).includes('No week has been committed yet.'));
  const u = panel.unreadable('SyntaxError');
  assert.ok(u.includes('could not be read just now') && u.includes('(SyntaxError)'));
  assert.ok(panel.render({ nope: true }).includes('could not be read just now'));
  assert.ok(panel.render(null).includes('could not be read just now'));
});

test('more than one week offers the others', () => {
  const p = payload(week('2026-W43'));
  p.weeks = ['2026-W43', '2026-W42'];
  const h = panel.render(p);
  assert.ok(h.includes('<select id="rec-week">') && h.includes('<option value="2026-W42">'));
  assert.ok(!panel.render(payload(week('2026-W43'))).includes('<select'));
});

// ── the page, the words, the deploy ─────────────────────────────────────

test('every word the panel prints is in all fourteen languages, with its placeholders', () => {
  const { STRINGS, LANGS } = require('../public/js/i18n.js');
  const src = codeOnly(fs.readFileSync(path.join(APP, 'public', 'js', 'record-panel.js'), 'utf8'));
  const page = fs.readFileSync(path.join(APP, 'public', 'agents.html'), 'utf8');
  const keys = new Set([...src.matchAll(/'(wkr\.[a-z_.]+)'/g)].map((m) => m[1]));
  for (const m of page.matchAll(/data-i18n="(wkr\.[a-z_.]+)"/g)) keys.add(m[1]);
  assert.ok(keys.size >= 40, `found ${keys.size} keys`);
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
  assert.ok(/<script src="\/js\/record-panel\.js\?v=\d+"><\/script>/.test(page));
  assert.ok(page.includes('id="rec-root"') && page.includes('id="record"'));
  const code = codeOnly(page);
  const start = code.indexOf("var url = '/api/public/record'");
  assert.ok(start !== -1);
  const loader = code.slice(start, code.indexOf('load(null);', start));
  assert.ok(loader.includes('RecordPanel.render(body, T)') && loader.includes('RecordPanel.unreadable('));
  assert.ok(!loader.includes('ag-root') && !loader.includes('st-root'), 'a failed record read touches no other panel');
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.ok(server.includes("app.use('/api/public/record', require('./routes/public_record'))"));
});

test('a deploy of app/ alone reads its copy of the committed weeks', () => {
  assert.strictEqual(weekly.RECORD_DIR, path.join(APP, 'content', 'record'));
  const { MIRRORS } = require('../scripts/sync_content');
  const m = MIRRORS.find(([from]) => from === 'benchmark/record');
  assert.ok(m, 'sync_content copies benchmark/record');
  assert.strictEqual(m[1], 'record');
  assert.ok(m[2].test('2026-W43.json') && !m[2].test('README.md'));
});
