/**
 * THE THEATRE'S WIRING — one read feeding two charts, and every "Max
 * drawdown" on the site naming its book.
 *
 * What a scan CAN prove is that the five renderings pass a kind and that the
 * renderer holds no vocabulary of its own. What only a DRIVE can prove is the
 * half that cost the most: that a failed equity read now CLEARS the drawdown
 * chart instead of leaving the previous one standing under a caption that
 * quotes a depth nothing measured.
 *
 * `#p-underwater` had exactly three touchers in the whole tree — the markup
 * and two reads inside a function called as a SIDE EFFECT of a sibling
 * panel's loader, wrapped in a catch that swallowed everything. So it had no
 * failure state at all.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { codeOnly } = require(path.join(__dirname, 'helpers', 'code_only.js'));
const M = require(path.join(__dirname, '..', 'public', 'js', 'equity-theatre-model.js'));

const DASH = path.join(__dirname, '..', 'public', 'js', 'dashboard.js');
const RAW = fs.readFileSync(DASH, 'utf8');
const src = () => codeOnly(RAW);

/** The theatre renderer block, executed with the helpers it reads. */
function renderer(over) {
  const a = RAW.indexOf('// ── the equity theatre: one reading, both charts ─');
  const b = RAW.indexOf('// ── the equity theatre: renderer end ─');
  assert.ok(a > 0 && b > a,
    'the equity theatre renderer lost its markers; this harness slices between them');
  const boxes = {};
  const charts = [];
  // The canvas is created by the renderer's own write, so the harness has to
  // answer for it AFTER that write — the first draft returned null for it,
  // which took `paintUnderwater`'s early return and left the whole try/catch
  // (the chart-library branch) undriven. A mutation that cleared the box
  // inside that try survived a green suite.
  const ctx = Object.assign({
    window: { EquityTheatreModel: M, RCCharts: { underwater: () => ({ update: (d) => { charts.push(d); } }) } },
    document: {
      getElementById: (id) => {
        if (id === 'underwaterCanvas') {
          const box = boxes['c-underwater'];
          return (box && /underwaterCanvas/.test(box.innerHTML)) ? { _canvas: true } : null;
        }
        return boxes[id] === undefined ? null : boxes[id];
      },
    },
    _charts: [],
    Object, String, Number, Array, isFinite, Math,
    T: (k, en) => en,
    esc: (s) => String(s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
  }, over || {});
  vm.runInNewContext(RAW.slice(a, b)
    + '\n;globalThis.__x = { paintUnderwater, ddLabel, etFootHtml, etSay, etWords };', ctx);
  return { fn: ctx.__x, boxes, ctx, charts };
}

const snap = (v) => ({ equity: v, snapshot_at: '2026-09-15T00:00:00Z' });
const payload = (vals, ce) => ({ snapshots: vals.map(snap), capital_events: ce });

test('a FAILED equity read CLEARS the drawdown chart and its caption', () => {
  const r = renderer();
  r.boxes.c_underwater = null;
  r.boxes['c-underwater'] = { innerHTML: '' };
  // A good read paints the chart and the sentence.
  r.fn.paintUnderwater(M.theatre(payload([1000, 1200, 900], 0)));
  const painted = r.boxes['c-underwater'].innerHTML;
  assert.match(painted, /underwaterCanvas/, 'a read record draws: ' + painted);
  assert.match(painted, /not the limit the engine enforces/, 'the book is named');
  assert.match(painted, /The limit the engine halts on is a separate reading/,
    'and the pairing sentence — without it a reader takes the history for the limit');
  // Counting the CALL is not reading its argument: the first draft asserted
  // only that `update` ran, so handing the chart an empty series survived.
  assert.equal(r.charts.length, 1, 'the chart was drawn once');
  // `Array.from` because the renderer builds its argument inside the VM:
  // a cross-realm object has a different prototype and `assert/strict`'s
  // deepEqual can never match one against a host literal, so the assertion
  // would fail for a reason unrelated to what it is guarding.
  assert.deepEqual(Array.from(r.charts[0].points), [1000, 1200, 900],
    'and handed the series the footnote counted, not an empty one');

  // The next read FAILS. Nothing about the last one may survive.
  r.fn.paintUnderwater(null);
  const after = r.boxes['c-underwater'].innerHTML;
  assert.ok(!/underwaterCanvas/.test(after), 'the old chart is still there: ' + after);
  assert.match(after, /could not be read/, 'and it says what happened: ' + after);
});

test('an empty history and a failed read get DIFFERENT sentences', () => {
  const r = renderer();
  r.boxes['c-underwater'] = { innerHTML: '' };
  r.fn.paintUnderwater(M.theatre(payload([], 0)));
  const none = r.boxes['c-underwater'].innerHTML;
  r.fn.paintUnderwater(null);
  const unread = r.boxes['c-underwater'].innerHTML;
  assert.notEqual(none, unread);
  assert.match(none, /No equity snapshots/);
  assert.match(unread, /could not be read/);
});

test('a chart that will not draw leaves the WORDS, not a blank panel', () => {
  // RCCharts absent is not a flat record.
  const r = renderer();
  r.boxes['c-underwater'] = { innerHTML: '' };
  r.ctx.window.RCCharts = null;
  r.fn.paintUnderwater(M.theatre(payload([1000, 1200, 900], 0)));
  assert.match(r.boxes['c-underwater'].innerHTML, /not the limit the engine enforces/);

  // And a charting library that throws does not take the sentence with it.
  const r2 = renderer();
  r2.boxes['c-underwater'] = { innerHTML: '' };
  r2.ctx.window.RCCharts = { underwater: () => { throw new Error('boom'); } };
  r2.fn.paintUnderwater(M.theatre(payload([1000, 1200, 900], 0)));
  assert.match(r2.boxes['c-underwater'].innerHTML, /not the limit the engine enforces/);
});

test('an absent model clears rather than keeps, and an absent box is no error', () => {
  const r = renderer();
  r.boxes['c-underwater'] = { innerHTML: '<canvas id="underwaterCanvas"></canvas>' };
  r.ctx.window.EquityTheatreModel = null;
  r.fn.paintUnderwater(M.theatre(payload([1000, 900], 0)));
  assert.equal(r.boxes['c-underwater'].innerHTML, '');

  const r2 = renderer();
  assert.doesNotThrow(() => r2.fn.paintUnderwater(M.theatre(payload([1000, 900], 0))));
});

test('the footnote renders the sample, and nothing when there is none', () => {
  const r = renderer();
  const html = r.fn.etFootHtml(M.theatre(payload([1000, 1200, 900], 2)));
  assert.match(html, /3 snapshots/);
  assert.match(html, /deepest 25\.0% below peak/);
  assert.match(html, /capital basis changed 2/);
  assert.equal(r.fn.etFootHtml(M.theatre(null)), '');
  assert.equal(r.fn.etFootHtml(null), '');
});

test('ddLabel names the book, and an unknown kind RAISES rather than printing bare', () => {
  const r = renderer();
  assert.match(r.fn.ddLabel('yourClosed'), /your closed trades/);
  assert.match(r.fn.ddLabel('copyLeader'), /this leader/);
  assert.notEqual(r.fn.ddLabel('agentRecord'), r.fn.ddLabel('yourEquity'));
  assert.throws(() => r.fn.ddLabel('maxDrawdown'), /no such drawdown kind/);
});

test('the qualifier is ESCAPED — a translation is a string somebody edits', () => {
  // The words come from DD_KINDS or from the dictionary, so this is defence
  // in depth rather than a live hole; it is pinned because the renderer must
  // not decide, per call site, whether the table it read can be trusted.
  const r = renderer();
  r.ctx.window.EquityTheatreModel = Object.assign({}, M, {
    kind: () => ({ short: { key: 'dd.et_k_x', en: '<img src=x onerror=1>' }, long: M.DD_KINDS.yourEquity.long }),
  });
  const html = r.fn.ddLabel('yourEquity');
  assert.ok(!html.includes('<img src=x'), 'it became a tag: ' + html);
  assert.ok(html.includes('&lt;img src=x'), 'it is shown as the text it is: ' + html);
});

test('EVERY rendering of a MEASURED drawdown names its book', () => {
  const s = src();
  // Eight quantities on this site are called "drawdown". Six were rendered as
  // a bare "Max drawdown"/"Max DD" from six endpoints. The sixth — the
  // backtest Lab's tile — was found by THIS guard after the other five were
  // labelled: a measurement you remember is not a measurement.
  //
  // Two usages are deliberately NOT renderings and carry no book: the
  // strategy builder's `sDd` INPUT and `_RULE_LABEL.max_drawdown_pct`, which
  // echoes it back as a chip. Those prompt for a limit the reader is SETTING;
  // there is no book to name, and labelling them would miscast a rule as a
  // reading.
  const ruleMapAt = s.indexOf('const _RULE_LABEL = {');
  const ruleMap = s.slice(ruleMapAt, s.indexOf('};', ruleMapAt));
  assert.ok(ruleMapAt > 0 && /max_drawdown_pct/.test(ruleMap),
    'the rule-label map moved; this exclusion must move with it');
  const lines = s.split('\n').filter((l) => !ruleMap.includes(l));
  const printers = lines.filter((l) =>
    /Max drawdown|Max realized drawdown|'Max DD'/.test(l)
    && !/<input|<label|function |ddLabel\(kindName/.test(l));
  assert.ok(printers.length >= 6,
    'expected at least six renderings, found ' + printers.length
    + ' — if one left, this guard must be re-measured, not relaxed');
  for (const l of printers) {
    assert.match(l, /ddLabel\('/, 'a drawdown printed with no book named: ' + l.trim());
  }
  // and the kinds they name are distinct books, not one repeated
  const named = [...s.matchAll(/ddLabel\('(\w+)'\)/g)].map((m) => m[1]);
  assert.ok(new Set(named).size >= 6, 'the same kind is reused: ' + named.join(', '));
  for (const n of named) assert.doesNotThrow(() => M.kind(n), n + ' is not in DD_KINDS');
});

test('a label passed as a tile ROW is rendered by the map that consumes it', () => {
  // The reachability claim, and the one this slice got wrong twice. A label
  // handed to a three-argument destructuring is passed on every branch and
  // read by nobody — the fifth granularity, and invisible from the call site.
  //
  // Two shapes carry a label: `${ddLabel(...)}` interpolated directly into
  // rendered markup, which renders by construction, and `, ddLabel(...)]` as
  // the fourth element of a tiles row, which renders only if its own consumer
  // binds a fourth name AND writes it.
  const s = src();
  const rows = [...s.matchAll(/, ddLabel\('\w+'\)\],/g)];
  assert.ok(rows.length >= 2, 'expected tile-row labels, found ' + rows.length);
  for (const m of rows) {
    // the consumer is the next `tiles.map((` after this row's array
    const at = s.indexOf('tiles.map((', m.index);
    assert.ok(at > 0, 'a tile row label with no consumer after it');
    const consumer = s.slice(at, s.indexOf(".join('')", at));
    const bound = /tiles\.map\(\(\[\s*\w+,\s*\w+,\s*\w+,\s*(\w+)\s*\]\)/.exec(consumer);
    assert.ok(bound, 'a tile row carries a label its consumer never binds: '
      + consumer.slice(0, 80));
    assert.ok(new RegExp('\\b' + bound[1] + '\\b').test(consumer.slice(bound[0].length)),
      'the consumer binds `' + bound[1] + '` and never renders it — zero times: '
      + consumer.slice(0, 80));
  }
  // The track-record tile is a function rather than a map, and needs the same.
  assert.match(s, /const tile = \(k, v, cls, note\)/,
    'the track-record tile must accept and render the note');
  assert.match(s, /\$\{k\}<\/div><div class="v num \$\{cls \|\| ''\}">\$\{v\}<\/div>`\s*\n?\s*\+ \(note \?/,
    'the tile must actually render `note`');
});

test('ONE read feeds both charts, and the underwater half is always told', () => {
  const s = src();
  assert.equal((s.match(/\/api\/trades\/equity-curve/g) || []).length, 1,
    'two fetches of one endpoint are two answers');
  // Told on the good path AND on the throw — the old code told it only on
  // success, from inside a catch that swallowed everything.
  assert.match(s, /paintUnderwater\(null\);[\s\S]{0,40}throw e;/,
    'a failed read must clear the underwater panel before the curve throws');
  assert.match(s, /paintUnderwater\(read\);/);
  assert.equal((s.match(/function paintUnderwater\(/g) || []).length, 1);
  // The old side-effect mount and its swallow are gone.
  assert.ok(!s.includes('mountUnderwater'), 'the side-effect mount is still here');
});

test('the panel is no longer hidden-by-default, and has a body to fail into', () => {
  const s = RAW;
  const i = s.indexOf('id="p-underwater"');
  assert.ok(i > 0);
  const tag = s.slice(i, s.indexOf('>', i));
  assert.ok(!/hidden/.test(tag),
    'a hidden-by-default panel can only be shown, never told a read failed');
  assert.match(s.slice(i, i + 500), /id="c-underwater"/);
});

test('the renderer spells NO key of its own — one vocabulary, the model\'s', () => {
  const s = src();
  const a = s.indexOf('function etSay(');
  const b = s.indexOf('function dsBase(') > a ? s.indexOf('function dsBase(') : s.length;
  assert.ok(a > 0, 'the theatre renderer moved; this guard must move with it');
  // Bound to the end of the block by its last function, found structurally.
  const after = /\n  (?:async )?function \w+\(/.exec(s.slice(s.indexOf('function paintUnderwater(') + 10));
  assert.ok(after, 'nothing follows the renderer; the boundary cannot be found');
  const block = s.slice(a, s.indexOf('function paintUnderwater(') + 10 + after.index);
  const keys = [...block.matchAll(/T\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(keys, [],
    'keys spelled in the renderer are a second vocabulary: ' + keys.join(', '));
  assert.ok(b >= 0);
});

test('dashboard.html loads the model before the script that reads it', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('equity-theatre-model.js');
  const dash = html.indexOf('<script src="/js/dashboard.js');
  assert.ok(model > 0, 'the equity theatre model is not loaded at all');
  assert.ok(model < dash, 'dashboard.js reads EquityTheatreModel at paint time');
});
