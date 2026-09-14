'use strict';
/**
 * The metric cluster: equity, day P&L and open count, each figure carrying
 * where it came from — or why it could not be read.
 *
 * It fails if a figure this site merely STORED is printed as a reading of
 * now, if an unread figure is printed as a number or a colour, if a figure
 * the payload never carries gets a labelled dash (which claims we looked), or
 * if two different sources share one sentence. It DRIVES the model with the
 * payloads the route REALLY sends — through helpers/portfolio_route.js,
 * against the shipped routes/portfolio.js — and through the real readMode
 * sliced out of dashboard.js.
 *
 * WHAT WAS WRONG BEFORE THE ROUTE NAMED ITS SOURCES. The hero inferred
 * "memory" from `stale`, and `stale` answers a different question on every
 * branch: the equity row's age on the operator path, `false` by construction
 * over the gateway, and `false` stamped over a dbFallback payload on a site
 * with no gateway configured — so months-old stored rows printed as a reading
 * of now on a deployment whose gateway secret had been rotated away, while a
 * seconds-old scan-cache balance was called a memory whenever the snapshot
 * beside it happened to be old. `provenance` is per figure, and this model
 * reads nothing else to decide a cell's state.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { get, scanRow, OLD } = require('./helpers/portfolio_route');

const APP = path.join(__dirname, '..');
const M = require('../public/js/metric-cluster-model.js');
const i18n = require('../public/js/i18n');
const SRC = codeOnly(fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8'));

// The book line's verdict is readMode's, so drive readMode too.
const readMode = (() => {
  const s = SRC.indexOf('  function readMode(pf) {');
  assert.ok(s > 0, 'readMode is still in dashboard.js');
  return vm.runInNewContext(SRC.slice(s, SRC.indexOf('\n  }\n', s) + 4) + '\nreadMode;');
})();
const cluster = (pf) => M.clusterCells(pf, readMode(pf));
const cellOf = (cl, id) => cl.cells.find((c) => c.id === id) || null;

// ── the payloads the route really emits ─────────────────────────────────────

const AT = '2026-09-14T10:00:00.000Z';
const BOT = (extra) => ({
  mode: 'PAPER', equity: 10000, daily_pnl: 12.5, open_positions: [{ id: 1 }, { id: 2 }],
  updated_at: AT, ...extra,
});

const ROUTE = {
  // operator path: the scan cache, the snapshot row and the synced rows
  op_scan:        () => get({ scan: scanRow({ live_mode: true, equity: 8300, live_unavailable: false }), snapAt: OLD }),
  op_snapshot:    () => get({ scan: scanRow({ live_mode: false }) }),
  op_unavailable: () => get({ scan: scanRow({ live_mode: true, equity: null, live_unavailable: true }), snapAt: OLD }),
  op_never:       () => get({ scan: scanRow({ live_mode: false }), snapshots: false }),
  op_mode_unread: () => get({ scan: [] }),
  // per-user path: the gateway answered
  user_live:      () => get({ operator: false, gateway: { data: BOT({ mode: 'LIVE' }) } }),
  user_mixed:     () => get({ operator: false, gateway: { data: BOT({ mode: 'MIXED' }) } }),
  user_paper:     () => get({ operator: false, gateway: { data: BOT() } }),
  user_nulls:     () => get({ operator: false, gateway: { data: BOT({ equity: null, daily_pnl: null, open_positions: null }) } }),
  user_flat:      () => get({ operator: false, gateway: { data: BOT({ equity: 0, daily_pnl: 0, open_positions: [] }) } }),
  user_unstamped: () => get({ operator: false, gateway: { data: BOT({ updated_at: undefined }) } }),
  // per-user path: the gateway did not answer, so the site's own tables did
  user_503_stored:   () => get({ operator: false, gateway: { status: 503 } }),
  user_503_never:    () => get({ operator: false, gateway: { status: 503 }, snapshots: false, stored: false }),
  user_threw:        () => get({ operator: false, gateway: { throws: true } }),
  // no gateway configured at all: PAPER is the deployment's state, and the
  // route stamps `stale: false` — over stored rows
  user_nobot_stored: () => get({ operator: false, gateway: { configured: false } }),
  user_nobot_never:  () => get({ operator: false, gateway: { configured: false }, snapshots: false, stored: false }),
};

const bodies = {};
const cl = {};
test('the route answers every scenario with HTTP 200 and names a source for every figure', async () => {
  for (const [name, call] of Object.entries(ROUTE)) {
    const { status, body } = await call();
    assert.equal(status, 200, `${name} answered ${status}`);
    bodies[name] = body;
    cl[name] = cluster(body);
    assert.equal(cl[name].read, true, `${name}: the payload named its sources`);
  }
});

test('a figure the bot answered just now is READ, says where from, and raises no band', () => {
  const s = cl.op_scan;
  const eq = cellOf(s, 'equity');
  assert.equal(eq.state, 'read');
  assert.equal(eq.value, 8300);
  assert.equal(eq.src.key, 'dd.m_src_scan');
  assert.ok(eq.as_of && Number.isFinite(new Date(eq.as_of).getTime()), 'the scan carries its own time');
  // THE RED HERRING: the snapshot row beside it is two hours old. The old
  // client inferred the equity's age from that row; this one is told which
  // row the number came from.
  assert.equal(bodies.op_scan.stale, false);
  assert.equal(s.band, null, 'a reading of now raises no memory band');
  const open = cellOf(s, 'open');
  assert.equal(open.state, 'synced');
  assert.equal(open.src.key, 'dd.m_src_sync');
  assert.equal(open.as_of, null, 'the sync records no time per row, so none is printed');
  assert.equal(s.book.key, 'dd.m_book_sync');

  const u = cl.user_live;
  assert.equal(cellOf(u, 'equity').state, 'read');
  assert.equal(cellOf(u, 'equity').src.key, 'dd.m_src_bot');
  assert.equal(cellOf(u, 'equity').as_of, AT, 'the age is the bot\'s own stamp');
  assert.equal(cellOf(u, 'open').value, 2);
  assert.equal(cellOf(u, 'open').state, 'read');
  assert.equal(u.band, null);
  assert.equal(u.book.key, 'dd.m_book_live');
});

test('a figure this site merely STORED is a MEMORY: named as one, aged, and under the band', () => {
  for (const k of ['op_snapshot', 'user_503_stored', 'user_threw', 'user_nobot_stored']) {
    const eq = cellOf(cl[k], 'equity');
    assert.equal(eq.state, 'memory', k);
    assert.equal(eq.value, 8200.5, k);
    assert.equal(eq.src.key, 'dd.m_src_stored', k);
    assert.ok(eq.as_of && Number.isFinite(new Date(eq.as_of).getTime()), `${k}: the snapshot's own time travels`);
    assert.ok(cl[k].band, `${k}: a stored number without the band reads as a reading of now`);
    assert.equal(cl[k].band.key, 'dd.m_band');
  }
  // The stored OPEN rows are a memory too, and an empty stored book is a
  // measured, stored zero — not an unread.
  for (const k of ['user_503_stored', 'user_threw', 'user_nobot_stored']) {
    const open = cellOf(cl[k], 'open');
    assert.equal(open.state, 'memory', k);
    assert.equal(open.value, 0, k);
    assert.equal(open.src.key, 'dd.m_src_stored', k);
  }
  // THE DEFECT THE PROVENANCE EXISTS FOR: on the unconfigured branch the
  // route stamps `stale: false` (correctly — it describes the deployment),
  // so a client inferring "memory" from `stale` printed these as readings.
  assert.equal(bodies.user_nobot_stored.stale, false, 'the flag the hero reads says fresh');
  assert.equal(bodies.user_nobot_stored.unconfigured, true);
  assert.equal(cl.user_nobot_stored.book.key, 'dd.m_book_nobot');
  // And a fallback after a failed gateway read cannot say which book.
  assert.equal(cl.user_503_stored.book.key, 'dd.m_book_unknown');
  assert.equal(cl.user_threw.book.key, 'dd.m_book_unknown');
});

test('an unread figure is a dash with its reason — never a number, never a colour', () => {
  const check = (k, id, why) => {
    const c = cellOf(cl[k], id);
    assert.ok(c, `${k} has a ${id} cell`);
    assert.equal(c.state, 'unread', `${k}/${id}`);
    assert.equal(c.value, null, `${k}/${id} carries no number`);
    assert.equal(c.text, M.DASH, `${k}/${id} prints the dash`);
    assert.equal(c.verdict, false, `${k}/${id} earns no colour`);
    assert.equal(c.src, null, `${k}/${id} names no source — it has none`);
    assert.equal(c.as_of, null, `${k}/${id} carries no age`);
    assert.equal(c.why.key, why, `${k}/${id} says why`);
  };
  // The venue's balance could not be read: LIVE with nothing to show.
  check('op_unavailable', 'equity', 'dd.m_eq_live');
  assert.equal(cellOf(cl.op_unavailable, 'open').state, 'synced', 'the synced rows are still a reading');
  // Nothing was ever stored: three spellings of the same absence, one word.
  check('op_never', 'equity', 'dd.m_never');
  check('user_503_never', 'equity', 'dd.m_never');
  check('user_503_never', 'open', 'dd.m_never');
  check('user_nobot_never', 'equity', 'dd.m_never');
  check('user_nobot_never', 'open', 'dd.m_never');
  // The bot answered and carried no number: a source that claims a reading
  // with nothing in it is "not read", not zero.
  check('user_nulls', 'equity', 'dd.m_unread');
  check('user_nulls', 'daypnl', 'dd.m_unread');
  check('user_nulls', 'open', 'dd.m_unread');
  // Every cell unread is still a reading — of an account with nothing on
  // record — and the reasons are the answer. The renderer must not throw
  // here; it throws only for a payload that names no source at all.
  for (const k of ['user_nulls', 'user_503_never', 'user_nobot_never']) {
    assert.equal(cl[k].read, true, k);
    assert.equal(cl[k].band, null, `${k}: nothing stored is not a memory`);
  }
  assert.equal(cl.user_nobot_never.book.key, 'dd.m_book_nobot');
});

test('a measured zero is a measurement and renders as one', () => {
  const f = cl.user_flat;
  for (const id of ['equity', 'daypnl', 'open']) {
    const c = cellOf(f, id);
    assert.equal(c.state, 'read', id);
    assert.equal(c.value, 0, id);
    assert.equal(c.text, null, `${id}: a measured zero is not a dash`);
  }
  assert.equal(cellOf(f, 'daypnl').verdict, true, 'a read break-even earns its colour');
});

test('a figure the payload never carries gets NO cell — a labelled dash would claim we looked', () => {
  for (const k of ['op_scan', 'op_snapshot', 'op_unavailable', 'op_never', 'op_mode_unread',
                   'user_503_stored', 'user_503_never', 'user_threw', 'user_nobot_stored', 'user_nobot_never']) {
    assert.equal(cellOf(cl[k], 'daypnl'), null, `${k}: the sync path does not track a daily figure`);
    assert.equal(bodies[k].provenance.daily_pnl, 'absent', k);
  }
  for (const k of ['user_live', 'user_paper', 'user_mixed', 'user_flat']) {
    const d = cellOf(cl[k], 'daypnl');
    assert.ok(d && d.state === 'read', `${k}: the gateway tracks it`);
    assert.equal(d.why.key, 'dd.m_day_basis', `${k}: and the cell says what the figure is over`);
    assert.equal(d.as_of, AT, `${k}: stamped with the bot's own time`);
  }
});

test('colour is a claim: only a READ day P&L earns a verdict, and equity never does', () => {
  for (const [k, c] of Object.entries(cl)) {
    for (const cell of c.cells) {
      if (cell.verdict) {
        assert.equal(cell.id, 'daypnl', `${k}: only the day P&L is a verdict`);
        assert.equal(cell.state, 'read', `${k}: a memory's colour would be a verdict about the past painted as now`);
      }
    }
  }
});

test('the band fires exactly when a memory is on screen', () => {
  for (const [k, c] of Object.entries(cl)) {
    const memory = c.cells.some((x) => x.state === 'memory');
    assert.equal(!!c.band, memory, `${k}: band=${!!c.band} memory=${memory}`);
  }
});

test('MIXED keeps the LIVE chip and the cluster says the figures are the paper book', () => {
  const m = cl.user_mixed;
  assert.equal(readMode(bodies.user_mixed), 'LIVE', 'the chip is readMode\'s');
  assert.equal(m.book.key, 'dd.m_book_mixed');
  assert.equal(cl.user_paper.book.key, 'dd.m_book_paper');
  assert.notEqual(m.book.key, cl.user_live.book.key);
});

test('an unstamped gateway payload carries no age rather than a manufactured one', () => {
  const u = cl.user_unstamped;
  assert.equal(cellOf(u, 'equity').state, 'read');
  assert.equal(cellOf(u, 'equity').as_of, null);
  assert.equal(cellOf(u, 'daypnl').as_of, null);
  assert.equal(bodies.user_unstamped.as_of.equity, null, 'the route does not stamp "now" for the bot');
});

// A payload from an older server (no provenance) or a junk body is NOT READ:
// the renderer throws and renderPanel paints the error state, because a row
// of bare dashes would claim we looked.
test('a payload that names no source is not a reading, and a source word this model does not know is unread', () => {
  assert.equal(M.clusterCells(null, null).read, false);
  assert.equal(M.clusterCells({ equity: 5, open_positions: [] }, 'PAPER').read, false);
  assert.deepEqual(M.clusterCells({ equity: 5 }, 'PAPER').cells, []);
  const skew = M.clusterCells({
    provenance: { equity: 'oracle', open_positions: 'gateway', daily_pnl: 'absent' },
    equity: 5, open_positions: [],
  }, 'PAPER');
  assert.equal(skew.read, true);
  assert.equal(cellOf(skew, 'equity').state, 'unread', 'a sixth spelling added later cannot arrive as a reading');
  assert.equal(cellOf(skew, 'equity').value, null);
  // A number that is not one is not one: strings the serialiser might emit.
  const junk = M.clusterCells({
    provenance: { equity: 'gateway', open_positions: 'gateway', daily_pnl: 'gateway' },
    equity: '', daily_pnl: 'n/a', open_positions: 'two',
  }, 'PAPER');
  for (const id of ['equity', 'daypnl', 'open']) assert.equal(cellOf(junk, id).state, 'unread', id);
  // A time that is not one is not one either: the route sends ISO or null,
  // and a payload from elsewhere sending "yesterday" gets no age rather than
  // fmtAgo's "--" printed as though an age had been read.
  const oddTime = M.clusterCells({
    provenance: { equity: 'gateway', open_positions: 'gateway', daily_pnl: 'absent' },
    equity: 5, open_positions: [], as_of: { equity: 'yesterday', open_positions: 1726300000000 },
  }, 'PAPER');
  assert.equal(cellOf(oddTime, 'equity').as_of, null);
  assert.equal(cellOf(oddTime, 'open').as_of, null);
  // The vocabulary is the contract, not today's route: a day P&L that
  // arrives as a stored MEMORY (no branch sends one yet) is a memory —
  // under the band, with no colour, because its colour would be a verdict
  // about the past painted as now.
  const remembered = M.clusterCells({
    provenance: { equity: 'snapshot', open_positions: 'db_rows', daily_pnl: 'snapshot' },
    equity: 5, daily_pnl: -2, open_positions: [],
  }, null);
  assert.equal(cellOf(remembered, 'daypnl').state, 'memory');
  assert.equal(cellOf(remembered, 'daypnl').verdict, false, 'a memory earns no colour');
  assert.equal(cellOf(remembered, 'daypnl').src.key, 'dd.m_src_stored');
  assert.ok(remembered.band);
});

// THE MUTATION KILLER. Collapsing any two of these onto one sentence is one
// state being reported as another — the whole defect.
test('every distinct source gets a distinct sentence, and every sentence is reachable from the wire', () => {
  const srcs = ['op_scan', 'op_snapshot', 'user_live'].map((k) => cellOf(cl[k], 'equity').src.key)
    .concat([cellOf(cl.op_scan, 'open').src.key]);
  assert.equal(new Set(srcs).size, srcs.length, 'two sources share one sentence: ' + srcs.join(', '));
  const whys = [['op_unavailable', 'equity'], ['op_never', 'equity'], ['user_nulls', 'equity']]
    .map(([k, id]) => cellOf(cl[k], id).why.key);
  assert.equal(new Set(whys).size, whys.length, 'two reasons share one sentence: ' + whys.join(', '));
  const books = ['op_scan', 'user_live', 'user_paper', 'user_mixed', 'user_503_stored', 'user_nobot_stored']
    .map((k) => cl[k].book.key);
  assert.equal(new Set(books).size, books.length, 'two books share one sentence: ' + books.join(', '));

  const emitted = new Set();
  for (const [name, c] of Object.entries(cl)) {
    for (const part of [c.band, c.book].concat(c.cells.flatMap((x) => [x.src, x.why])).filter(Boolean)) {
      emitted.add(part.key);
      assert.ok(M.KEYS.includes(part.key), `${name} emits ${part.key}, which KEYS does not list`);
      assert.ok(i18n.STRINGS[part.key], `${name} emits ${part.key}, which the dictionary lacks`);
    }
  }
  // No sentence the model can print is unreachable from the route: a
  // sentence no payload produces is a door painted on a wall.
  assert.deepEqual(M.KEYS.filter((k) => !emitted.has(k)), [], 'unreachable sentences');
});

// dashboard.js calls T() with LITERAL keys inside the loader, and the i18n
// sweep proves each literal exists; this proves the set of literals is the
// set the model can emit. `say()` falls back to English for a key the WORDS
// map lacks, so a key emitted and not listed there prints English in
// thirteen languages unseen — translate()'s own blind spot, one layer up.
test('the renderer lists every key the model can emit, and each exists in all fourteen languages', () => {
  const body = loaderBody();
  const listed = new Set([...body.matchAll(/T\('(dd\.m_[a-z_]+)'/g)].map((m) => m[1]));
  for (const k of M.KEYS) assert.ok(listed.has(k), `${k} is not in the loader's WORDS map`);
  const labels = ['dd.m_equity', 'dd.m_daypnl', 'dd.m_open'];
  for (const k of labels) assert.ok(listed.has(k), `${k} label`);
  assert.deepEqual([...listed].filter((k) => !M.KEYS.includes(k) && !labels.includes(k)), [],
    'a key the loader names that the model cannot emit is dead text');
  const missing = [];
  for (const key of [...M.KEYS, ...labels, 'dp.metrics']) {
    assert.ok(i18n.STRINGS[key], `${key} is in the dictionary`);
    for (const { code } of i18n.LANGS) {
      const v = i18n.STRINGS[key][code];
      if (typeof v !== 'string' || !v.length) missing.push(key + '/' + code);
    }
  }
  assert.deepEqual(missing, [], 'dangling cluster keys');
  assert.equal(i18n.LANGS.length, 14);
});

// WIRING. The model can be perfect and never reached — #999's lesson.
function loaderBody() {
  const at = SRC.indexOf("renderPanel(C('metrics')");
  assert.ok(at > 0, 'the metric cluster is mounted');
  let depth = 0, i = SRC.indexOf('(', at);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '(') depth++;
    else if (SRC[i] === ')') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(at, i + 1);
}

test('the cluster guards its one source, reads the model, and throws on a payload that names no source', () => {
  const body = loaderBody();
  assert.match(body, /mustRead\(/, 'single source, so it GUARDS');
  assert.match(body, /readMode\(pf\)/, 'the book line\'s verdict comes from the one reading');
  assert.match(body, /MetricClusterModel/, 'the cells come from the model');
  assert.match(body, /clusterCells\(pf, readMode\(pf\)\)/);
  assert.match(body, /await portfolioRead/, 'the cluster reads the shared render read, not a fetch of its own');
  assert.match(body, /if \(!cl\.read\) throw/, 'no source named is an error state, not a row of dashes');
  assert.ok(!/cache\.portfolio/.test(body), 'a cached payload is a claim about an earlier read');
  assert.ok(!/\.ok\b[\s\S]{0,60}return null;/.test(body), 'a failed read is never rendered as an empty cluster');
  assert.ok(!/\|\|\s*0\b|\?\?\s*0\b/.test(body), 'no or-zero in the renderer');
  assert.ok(!/[<>]=?\s*0\s*\?/.test(body), 'no bare comparison verdict in the renderer — pnlClass decides');
  assert.match(body, /c\.verdict \? pnlClass\(c\.value\) : ''/, 'colour only where the model says a verdict was earned');
  assert.ok(!/['"](LIVE|PAPER|MODE \?)['"]/.test(body), 'no mode word is written in the renderer');
});

test('the cluster is mounted between the hero and the command bar, and its model loads first', () => {
  const home = SRC.slice(SRC.indexOf('async function renderHome()'));
  const hero = home.indexOf('id="p-hero"'), metrics = home.indexOf('id="p-metrics"'), cmd = home.indexOf('id="p-cmd"');
  assert.ok(hero > 0 && metrics > hero && cmd > metrics, 'hero, then the figures, then the command bar');
  const reads = home.match(/fetchJSON\('\/api\/portfolio'/g) || [];
  assert.equal(reads.length, 1, `renderHome fetches /api/portfolio ${reads.length} times`);
  const html = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('/js/metric-cluster-model.js');
  assert.ok(model > 0 && model < html.indexOf('/js/dashboard.js'), 'the model loads before dashboard.js');
});
