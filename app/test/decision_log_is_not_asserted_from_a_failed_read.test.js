'use strict';
/**
 * The decision log: the engine's sealed trail — event, thesis, GATE,
 * disposition, fill — rendered without a verdict the seal never carried, a
 * number nobody recorded, or an all-clear assembled from a read that failed.
 *
 * DRIVEN, not scanned: the two defects this panel exists to avoid — a gate
 * verdict the recorder sealed as UNKNOWN painted in the colour of a
 * rejection, and an incident stream that could not be read rendered as
 * "nothing to stop" — are reachability and wording, which no scan can see.
 * The model is driven directly; the renderers are sliced from dashboard.js
 * between two sentinel comments and RUN in a VM with the real pnlClass; the
 * loader is checked for its wiring.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { blockBetween } = require('./helpers/block.js');

const APP = path.join(__dirname, '..');
const M = require('../public/js/decision-log-model.js');
const IR = require('../public/js/instrument-row-model.js');
const i18n = require('../public/js/i18n');
const RAW = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
const SRC = codeOnly(RAW);
const APPJS = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(APP, 'public', 'styles.css'), 'utf8');

const rec = (extra) => ({ decision_id: 'd', symbol: 'BTC/USDT:USDT', timestamp: '2026-09-14T10:00:00Z', outcome: 'EXECUTED_LIVE',
  idea: { direction: 'LONG', reasoning: 'trend up' }, risk: { verdict: 'APPROVED', passed: 11, failed: 0, checks_failed: [] },
  result: { pnl_usd: 12.5, exit_price: 60100, close_reason: 'tp' }, chain: { sequence: 41 }, ...extra });

// ── the gate, four ways ─────────────────────────────────────────────────────

test('the gate has four verdict states and only two of them wear a colour', () => {
  const none = M.gate({ risk: null });
  assert.equal(none.state, 'none'); assert.equal(none.word.key, 'dd.dl_gate_none'); assert.match(none.cls, /dl-unread/);
  assert.ok(!/chip--(up|down)/.test(none.cls));
  const missing = M.gate({});
  assert.equal(missing.state, 'none');
  // THE LIVE DEFECT IN flightCard: the recorder's own except branch seals the
  // literal UNKNOWN when it could not read the risk object, and the card
  // paints it in the colour of a rejection.
  const unknown = M.gate({ risk: { verdict: 'UNKNOWN' } });
  assert.equal(unknown.state, 'unread'); assert.equal(unknown.word.key, 'dd.dl_gate_unread');
  assert.ok(!/chip--down/.test(unknown.cls), 'a verdict nobody read is not a rejection');
  assert.ok(!/chip--up/.test(unknown.cls));
  assert.equal(M.gate({ risk: { verdict: '' } }).state, 'unread');
  assert.equal(M.gate({ risk: {} }).state, 'unread');
  const pass = M.gate({ risk: { verdict: 'APPROVED' } });
  assert.equal(pass.state, 'pass'); assert.match(pass.cls, /chip--up/);
  const block = M.gate({ risk: { verdict: 'REJECTED', checks_failed: ['MAX_DRAWDOWN'] } });
  assert.equal(block.state, 'block'); assert.match(block.cls, /chip--down/); assert.deepEqual(block.why.names, ['MAX_DRAWDOWN']);
  assert.equal(M.gate({ outcome: 'REJECTED_ON_RECHECK', risk: { verdict: 'approved' } }).state, 'pass', 'the sealed verdict wins over the outcome prefix when it is a verdict');
  assert.equal(M.gate({ outcome: 'REJECTED', risk: { verdict: '' } }).state, 'block', 'a REJECTED outcome with no verdict word is still a block');
  const other = M.gate({ risk: { verdict: 'DEFERRED' } });
  assert.equal(other.state, 'other'); assert.equal(other.literal, 'DEFERRED'); assert.ok(!/chip--(up|down)/.test(other.cls));
});

test('no count is invented: the seal\'s thin except shape carries no "0 checks failed"', () => {
  const why = M.gate({ risk: { verdict: 'REJECTED' } }).why;
  assert.deepEqual(why.names, []); assert.equal(why.reason, ''); assert.equal(why.failed, null);
  assert.equal(M.checks({ failed: 0 }).failed, null, 'zero is the all-clear, not a count to print');
  assert.equal(M.checks({ failed: 2 }).failed, 2);
  assert.equal(M.checks({ failed: '2' }).failed, null, 'a string is not a count');
  assert.deepEqual(M.checks({ checks_failed: ['A', '', 3, 'B'] }).names, ['A', 'B']);
});

// ── the fill, six ways ──────────────────────────────────────────────────────

test('an absent P&L is never a break-even, and which absence it is depends on the record, not the viewer', () => {
  const closed = { outcome: 'EXECUTED_LIVE', result: { pnl_usd: null, exit_price: 0 } };
  const signedIn = M.fill(closed, false);
  assert.equal(signedIn.state, 'unpriced'); assert.equal(signedIn.pnl, null); assert.equal(signedIn.price, null, 'exit_price 0 is the absence');
  assert.equal(signedIn.word.key, 'dd.dl_pnl_unrec');
  // The anonymous scrub drops the key. The server's marker says which absence.
  const hidden = M.fill({ outcome: 'EXECUTED_LIVE', result: { exit_price: 60100, fill_priced: true } }, true);
  assert.equal(hidden.state, 'hidden'); assert.equal(hidden.word.key, 'dd.dl_hidden');
  const neverPriced = M.fill({ outcome: 'EXECUTED_LIVE', result: { exit_price: 60100, fill_priced: false } }, true);
  assert.equal(neverPriced.state, 'unpriced', 'redacted is not unrecorded: a close the engine never priced says so for every viewer');
  // An older server sends no marker: promise nothing.
  const unshown = M.fill({ outcome: 'EXECUTED_LIVE', result: { exit_price: 60100 } }, true);
  assert.equal(unshown.state, 'unshown'); assert.equal(unshown.word.key, 'dd.dl_unshown');
  assert.ok(!/sign in/i.test(unshown.word.en), 'no promise about what signing in would reveal');
  // A number is a number, and a measured zero stays one.
  assert.equal(M.fill(rec(), false).state, 'pnl'); assert.equal(M.fill(rec(), false).pnl, 12.5); assert.equal(M.fill(rec(), false).price, 60100);
  assert.equal(M.fill({ result: { pnl_usd: 0 } }, false).pnl, 0);
  assert.equal(M.fill({ result: { pnl_usd: '12.5' } }, false).state, 'unpriced', 'a string is not an amount');
  // No result: open if executed, otherwise no cell at all.
  assert.equal(M.fill({ outcome: 'EXECUTED_LIVE' }, false).state, 'open');
  assert.equal(M.fill({ outcome: 'REJECTED' }, false).state, 'omitted');
  assert.equal(M.fill({ outcome: 'REJECTED', result: null }, true).state, 'omitted');
});

test('a zero price is not a level, a numeric string is not a price, and no time is not 1970', () => {
  assert.equal(M.price(0), null); assert.equal(M.price(null), null); assert.equal(M.price(NaN), null);
  assert.equal(M.price('63000'), null); assert.equal(M.price(63000), 63000);
  assert.equal(M.pnl(0), 0); assert.equal(M.pnl('0'), null);
  assert.equal(M.time(''), null); assert.equal(M.time(undefined), null); assert.equal(M.time('not a date'), null);
  assert.equal(M.time(0), null, 'a numeric zero is not a timestamp');
  assert.equal(M.time('2026-09-14T10:00:00Z'), Date.parse('2026-09-14T10:00:00Z'));
  const row = M.decisionRow(rec({ timestamp: '' }), false);
  assert.equal(row.ms, null); assert.equal(row.when.key, 'dd.dl_no_time'); assert.equal(row.timestamp, null);
});

test('the direction vocabulary is the instrument row\'s and dirChip\'s: one answer per word on all three', () => {
  const start = APPJS.indexOf('  function dirChip(direction) {');
  assert.ok(start > 0);
  const dirChip = vm.runInNewContext(APPJS.slice(start, APPJS.indexOf('\n  }\n', start) + 4) + '\ndirChip;');
  for (const d of ['LONG', 'long', 'BUY', 'SHORT', 'SELL', 'sell', 'UNKNOWN', '', null, undefined, 'HOLD', 'Long', 'LONGSHORT']) {
    const html = dirChip(d);
    const chipSide = /chip--up/.test(html) ? 'long' : /chip--down/.test(html) ? 'short' : null;
    assert.equal(M.side(d), chipSide, `dirChip: ${JSON.stringify(d)}`);
    assert.equal(M.side(d), IR.side(d), `instrument row: ${JSON.stringify(d)}`);
  }
  const row = M.decisionRow(rec({ idea: { direction: 'UNKNOWN', reasoning: 'x' } }), false);
  assert.equal(row.side, null); assert.equal(row.sideWord.key, 'dd.dl_no_dir');
});

test('two absences, two words: a decision with no symbol is unread, an incident with no symbol is normal', () => {
  assert.equal(M.decisionRow(rec({ symbol: '' }), false).symWord.key, 'dd.dl_no_sym');
  assert.equal(M.decisionRow(rec(), false).sym, 'BTC');
  assert.equal(M.incidentRow({ kind: 'block', ts: '2026-09-14T10:00:00Z', symbol: '' }).symWord.key, 'dd.dl_no_market');
  assert.equal(M.incidentRow({ kind: 'weird' }).chip.word.key, 'dd.dl_k_unread');
  assert.ok(!/chip--(up|down|warn|info)/.test(M.incidentRow({ kind: 'weird' }).chip.cls));
  assert.equal(M.incidentRow({ kind: 'recovery' }).chip.word.key, 'dd.dl_k_rec');
  assert.equal(M.incidentRow({ kind: 'block', detail: '' }).detailWord.key, 'dd.dl_no_detail');
});

test('the thesis says when it was shortened; the disposition is the engine\'s act, not the venue\'s book', () => {
  const long = 'x'.repeat(M.THESIS_MAX + 1), short = 'y'.repeat(M.THESIS_MAX);
  assert.equal(M.decisionRow(rec({ idea: { direction: 'LONG', reasoning: long } }), false).thesisCut, true);
  assert.equal(M.decisionRow(rec({ idea: { direction: 'LONG', reasoning: short } }), false).thesisCut, false);
  assert.equal(M.decisionRow(rec({ idea: {} }), false).thesisWord.key, 'dd.dl_no_thesis');
  assert.equal(M.disposition({ outcome: 'EXECUTED_LIVE' }).word.key, 'dd.dl_d_exec');
  assert.equal(M.disposition({ outcome: 'EXECUTION_FAILED' }).word.key, 'dd.dl_d_fail');
  assert.equal(M.disposition({ outcome: 'REJECTED_ON_RECHECK' }).word.key, 'dd.dl_d_rej');
  assert.equal(M.disposition({}).word.key, 'dd.dl_d_unread');
  const other = M.disposition({ outcome: 'REJECTED' });
  assert.equal(other.state, 'other'); assert.equal(other.literal, 'REJECTED'); assert.ok(!/chip--(up|down|warn)/.test(other.cls));
});

// ── the reading: what the two fetch results mean ────────────────────────────

test('a 200 whose body did not parse is NOT a reading, and a 404 keeps the empty doctrine', () => {
  assert.throws(() => M.reading({ ok: true, status: 200, data: null }, null), /unreadable body/);
  assert.throws(() => M.reading({ ok: true, status: 200, data: 'html' }, null), /unreadable body/);
  assert.deepEqual(M.reading({ ok: false, status: 404, data: null }, null), { flight: null, incidents: null });
  const r = M.reading({ ok: true, status: 200, data: { records: [] } }, { ok: true, status: 200, data: { incidents: [] } });
  assert.deepEqual(r.flight, { records: [] }); assert.deepEqual(r.incidents, { incidents: [] });
  // The incident stream is a reading only when it answered 200 with an object.
  assert.equal(M.reading({ ok: true, data: {} }, null).incidents, null);
  assert.equal(M.reading({ ok: true, data: {} }, { ok: false, status: 503, data: { error: 'x' } }).incidents, null);
  assert.equal(M.reading({ ok: true, data: {} }, { ok: true, status: 200, data: null }).incidents, null, 'a 200 the incidents route could not fill is not a read either');
});

test('the incident stream\'s states are named, and omit may not degenerate into a confident negative', () => {
  const one = { records: [rec()] };
  const unread = M.decisionLog(one, null);
  assert.equal(unread.notes.length, 1); assert.equal(unread.notes[0].word.key, 'dd.dl_inc_unread'); assert.equal(unread.notes[0].loud, true);
  const empty = M.decisionLog(one, { incidents: [], derived: false });
  assert.equal(empty.notes[0].word.key, 'dd.dl_inc_none'); assert.equal(empty.notes[0].loud, false);
  const derived = M.decisionLog(one, { incidents: [], derived: true });
  assert.equal(derived.notes[0].word.key, 'dd.dl_inc_derived'); assert.equal(derived.notes[0].loud, true);
  const full = M.decisionLog(one, { incidents: [{ kind: 'block', ts: '2026-09-14T11:00:00Z' }], derived: false });
  assert.equal(full.notes.length, 0); assert.equal(full.rows.length, 2);
  // Empty ledger + unread incidents THROWS: there is nothing to show AND half
  // the evidence was never read, so "nothing happened" would be assembled
  // from a read that failed.
  assert.throws(() => M.decisionLog({ records: [] }, null), /incident stream unreadable/);
  // Empty ledger + READ incidents is the empty state.
  assert.equal(M.decisionLog({ records: [] }, { incidents: [] }), null);
  // Empty ledger + incidents to show is a log of incidents.
  assert.equal(M.decisionLog({ records: [] }, { incidents: [{ kind: 'flag', ts: '' }] }).rows.length, 1);
});

test('rows are newest first and undated rows sink, never reordered into a chronology they do not have', () => {
  const log = M.decisionLog({ records: [rec({ decision_id: 'a', timestamp: '2026-09-14T10:00:00Z' }), rec({ decision_id: 'b', timestamp: '' }), rec({ decision_id: 'c', timestamp: '2026-09-14T12:00:00Z' })] },
    { incidents: [{ kind: 'block', ts: '2026-09-14T11:00:00Z' }, { kind: 'flag' }] });
  assert.deepEqual(log.rows.map((r) => r.ms === null ? 'undated' : r.ms), [Date.parse('2026-09-14T12:00:00Z'), Date.parse('2026-09-14T11:00:00Z'), Date.parse('2026-09-14T10:00:00Z'), 'undated', 'undated']);
  assert.deepEqual(log.rows.map((r) => r.kind), ['decision', 'incident', 'decision', 'decision', 'incident'], 'a stable sort keeps the undated rows in arrival order');
});

test('the footer names the book and the ledger\'s age, with words for an age it does not have', () => {
  const log = M.decisionLog({ records: [rec()], updated_at: '2026-09-14T10:30:00Z' }, { incidents: [] });
  assert.equal(log.footer.scope.key, 'dd.dl_scope'); assert.equal(log.footer.when, '2026-09-14T10:30:00Z'); assert.equal(log.footer.whenWord, null);
  const undated = M.decisionLog({ records: [rec()] }, { incidents: [] });
  assert.equal(undated.footer.when, null); assert.equal(undated.footer.whenWord.key, 'dd.dl_no_time');
  assert.equal(M.decisionLog({ records: [rec()], disclosure: 'Anonymous view' }, { incidents: [] }).anonymous, true);
});

// ── the renderers, sliced out of dashboard.js and RUN ──────────────────────

function renderers() {
  const block = blockBetween(RAW, '// ── decision log: renderers ─', '// ── decision log: renderers end ─', { pad: 60, min: 2000 });
  const pc = APPJS.indexOf('  function pnlClass(n) {');
  assert.ok(pc > 0, 'pnlClass is still in app.js');
  const pnlClass = APPJS.slice(pc, APPJS.indexOf('\n  }\n', pc) + 4);
  const ctx = {
    self: { DecisionLogModel: M }, DecisionLogModel: M, Number, String, Array, Object, isFinite, Math, JSON,
    T: (k, en) => `[${k}]`, TF: (k, en, map) => `[${k}]`,
    esc: (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
    fmtPrice: (n) => '$' + Number(n).toFixed(2), signed: (n) => (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(2),
    fmtAgo: (iso) => `ago(${iso})`,
  };
  vm.runInNewContext(pnlClass + '\n' + block + '\n;globalThis.__x = { decisionLogHtml, dlWords, dlFillHtml, dlWhy };', ctx);
  return ctx.__x;
}

test('the renderer lists exactly the keys the model can emit, each as a literal the dictionary sweep can see', () => {
  const R = renderers();
  assert.deepEqual(Object.keys(R.dlWords()).sort(), [...M.KEYS].sort());
  const block = blockBetween(RAW, '// ── decision log: renderers ─', '// ── decision log: renderers end ─', { min: 2000 });
  for (const k of M.KEYS) assert.ok(block.includes(`T('${k}'`), `${k} is a literal T() in the renderer`);
});

test('no colour without a verdict, no number without a reading, and the footer wears no colour', () => {
  const R = renderers();
  const W = R.dlWords();
  const inc = { incidents: [{ kind: 'block', ts: '2026-09-14T11:00:00Z', category: 'Firewall', detail: 'injection' }] };
  for (const bad of [rec({ risk: null }), rec({ risk: { verdict: 'UNKNOWN' } }), rec({ risk: { verdict: '' } }), rec({ risk: { verdict: 'DEFERRED' } })]) {
    const html = R.decisionLogHtml(M.decisionLog({ records: [bad] }, { incidents: [] }), W);
    // The GATE cell, not the whole row: the disposition chip and the fill are
    // sealed facts of their own (the venue took the order; the close was
    // priced) and may wear their colours beside an unread gate. Asserting no
    // green anywhere on the row is the assertion CLAUDE.md records failing
    // on a direction glyph that was telling the truth.
    const gateCell = html.slice(html.indexOf('class="dl-gate"'), html.indexOf('class="dl-out"'));
    assert.ok(!/chip--(up|down)/.test(gateCell), `no verdict colour on an unread gate: ${gateCell}`);
    assert.match(gateCell, /dl-unread/);
  }
  const html = R.decisionLogHtml(M.decisionLog({ records: [rec({ result: { pnl_usd: null, exit_price: 0 } }), rec({ decision_id: 'e', risk: { verdict: 'REJECTED' }, outcome: 'REJECTED', result: null })], updated_at: '2026-09-14T10:30:00Z' }, inc), W);
  assert.ok(!/\$0\.00|\+0\.00/.test(html), 'an absent P&L is not printed as a break-even');
  assert.match(html, /dl-fill">\[dd\.dl_pnl_unrec\]</);
  // The thin except shape renders NO why-span at all: the harness's T() stub
  // returns the key rather than the template, so "0 check(s) failed" would
  // be invisible to a digit scan — the property is that nothing is printed.
  const thinGate = html.slice(html.lastIndexOf('class="dl-gate"'), html.lastIndexOf('class="dl-out"'));
  assert.ok(!/dl-gate-why/.test(thinGate), `no "0 checks failed" from the thin except shape: ${thinGate}`);
  const counted = R.decisionLogHtml(M.decisionLog({ records: [rec({ risk: { verdict: 'REJECTED', failed: 2 }, outcome: 'REJECTED', result: null })] }, { incidents: [] }), W);
  assert.match(counted, /dl-gate-why">\[dd\.dl_nfailed\]</, 'a count the seal carried above zero is printed');
  const foot = html.slice(html.indexOf('<p class="dl-foot">'));
  assert.ok(!/chip--|\bpos\b|\bneg\b/.test(foot), 'the ledger\'s age is never a verdict');
  assert.match(foot, /\[dd\.dl_scope\]/, 'the footer names the book');
  assert.match(foot, /\[dd\.dl_written\]/); assert.match(foot, /\[dd\.dl_written_why\]/);
  assert.match(html, /dl-row--incident/); assert.match(html, /chip chip--down">\[dd\.dl_k_block\]/);
  // A read P&L is coloured through the real pnlClass, and a measured zero is green.
  const ok = R.decisionLogHtml(M.decisionLog({ records: [rec()] }, { incidents: [] }), W);
  assert.match(ok, /dl-fill pos">\+12\.50 @ \$60100\.00 · tp/);
  assert.match(ok, /chip chip--up">\[dd\.dl_gate_pass\]/); assert.match(ok, /chip chip--up">\[dd\.dl_d_exec\]/);
  const flat = R.decisionLogHtml(M.decisionLog({ records: [rec({ result: { pnl_usd: 0 } })] }, { incidents: [] }), W);
  assert.match(flat, /dl-fill pos">\+0\.00</, 'a measured zero keeps its number and its colour');
  // The three incident notes, and the thesis marker.
  assert.match(R.decisionLogHtml(M.decisionLog({ records: [rec()] }, null), W), /dl-note">\[dd\.dl_inc_unread\]/);
  assert.match(R.decisionLogHtml(M.decisionLog({ records: [rec()] }, { incidents: [], derived: true }), W), /dl-note">\[dd\.dl_inc_derived\]/);
  assert.match(R.decisionLogHtml(M.decisionLog({ records: [rec()] }, { incidents: [] }), W), /dl-note dl-note--quiet">\[dd\.dl_inc_none\]/);
  const cut = R.decisionLogHtml(M.decisionLog({ records: [rec({ idea: { direction: 'LONG', reasoning: 'z'.repeat(M.THESIS_MAX + 5) } })] }, { incidents: [] }), W);
  assert.match(cut, /dl-thesis-cut">… \[dd\.dl_thesis_cut\]/); assert.match(cut, /class="dl-thesis" title="z{185}"/);
  // Undated rows print words, never an age.
  const undated = R.decisionLogHtml(M.decisionLog({ records: [rec({ timestamp: '' })] }, { incidents: [] }), W);
  assert.match(undated, /dl-when dl-when--unread num">\[dd\.dl_no_time\]/); assert.ok(!/ago\(/.test(undated.slice(undated.indexOf('<ul'), undated.indexOf('</ul>'))));
});

test('every sentence the model can emit is listed and in all fourteen languages, with its slots', () => {
  const missing = [];
  for (const key of [...M.KEYS, 'dd.dl_empty', 'dp.declog']) {
    assert.ok(i18n.STRINGS[key], `${key} is in the dictionary`);
    for (const { code } of i18n.LANGS) {
      const v = i18n.STRINGS[key][code];
      if (typeof v !== 'string' || !v.trim().length) missing.push(key + '/' + code);
    }
  }
  assert.deepEqual(missing, []);
  for (const { code } of i18n.LANGS) {
    assert.match(i18n.STRINGS['dd.dl_written'][code], /\{when\}/, code);
    assert.match(i18n.STRINGS['dd.dl_nfailed'][code], /\{n\}/, code);
  }
});

// ── wiring ──────────────────────────────────────────────────────────────────

function loaderBody(target) {
  const at = SRC.indexOf(`renderPanel(C('${target}')`);
  assert.ok(at > 0, `the ${target} panel is mounted`);
  let depth = 0, i = SRC.indexOf('(', at);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '(') depth++;
    else if (SRC[i] === ')') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(at, i + 1);
}

test('the loader guards the ledger, sequences the two reads, classifies through the model, and states its budget', () => {
  const body = loaderBody('declog');
  assert.match(body, /const fr = await fetchJSON\('\/api\/guardian\/flight\?limit=40', \{ auth: false, timeoutMs: 12000 \}\);\s*mustRead\(fr\);/);
  assert.match(body, /const ir = await fetchJSON\('\/api\/guardian\/incidents\?limit=40', \{ auth: false, timeoutMs: 8000 \}\)\s*\.catch\(\(\) => null\);/);
  assert.ok(body.indexOf("'/api/guardian/flight") < body.indexOf("'/api/guardian/incidents"), 'flight first');
  assert.ok(!/Promise\.all/.test(body), 'sequenced, not raced: both handlers share one cold cache and one flag');
  assert.match(body, /M\.reading\(fr, ir\)/); assert.match(body, /M\.decisionLog\(read\.flight, read\.incidents\)/);
  assert.match(body, /decisionLogHtml\(log, dlWords\(\)\)/);
  assert.match(body, /timeoutMs: 21000/);
  const raw = RAW.slice(RAW.indexOf("renderPanel(C('declog')") - 1200, RAW.indexOf("renderPanel(C('declog')"));
  assert.match(raw, /12000 \+ 8000 = 20000ms/);
  assert.match(body, /T\('dd\.dl_empty'/, 'the empty state is the one sentence');
});

test('the old pair says the one sentence too, and the panel is on the Engine view with its model loaded first', () => {
  const guardianBlock = SRC.slice(SRC.indexOf('  function guardianBlock(data) {'), SRC.indexOf("async function renderGuardian()"));
  assert.match(guardianBlock, /T\('dd\.dl_empty'/, 'guardianBlock\'s empty sentence is the shared one');
  assert.ok(!/No decisions have been recorded yet/.test(SRC), 'the old sentence is gone');
  assert.ok(!/decision ledger is unavailable right now/.test(SRC), 'and so is its contradiction');
  assert.equal((SRC.match(/T\('dd\.dl_empty'/g) || []).length, 3, 'the block, the flight panel and the decision log');
  const engine = SRC.slice(SRC.indexOf('async function renderEngine()'), SRC.indexOf("renderPanel(C('declog')"));
  assert.ok(engine.indexOf('id="p-ecards"') > 0 && engine.indexOf('id="p-declog"') > engine.indexOf('id="p-ecards"'), 'mounted after the setups panel');
  const html = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  assert.ok(html.indexOf('/js/decision-log-model.js') > 0 && html.indexOf('/js/decision-log-model.js') < html.indexOf('/js/dashboard.js'));
});

test('the styles give the unread chip its own muted pair, colour no footer, and clamp the thesis with a marker class', () => {
  // Bounded at the next banner, not the end of the sheet: the block after
  // this one is not this one's, and a slice that ran to EOF would read it as
  // such — the instrument-row guard broke exactly that way on this block.
  const start = CSS.indexOf('/* ---- Decision log');
  const next = CSS.indexOf('/* ---- ', start + 10);
  const block = CSS.slice(start, next === -1 ? CSS.length : next).replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(start > -1, 'the decision-log block is in the sheet');
  assert.ok(block.length > 800);
  assert.match(block, /\.dl-unread \{[^}]*color: var\(--text-3\)[^}]*background: var\(--surface-2\)/);
  assert.match(block, /\.dl-fill:not\(\.pos\):not\(\.neg\) \{ color: var\(--text-3\); \}/);
  assert.match(block, /\.dl-thesis-cut \{/);
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(block), 'tokens only');
  assert.match(block, /\.dl-foot \{[^}]*color: var\(--text-3\)/);
});
