/**
 * THE DECISION COURT — every state DRIVEN.
 *
 * The dossier is read to decide whether to trust the engine, so the cases
 * that matter are the ABSENCES: a section the seal does not carry, a
 * confidence the producer defaulted to 0, a gate sealed UNKNOWN, a decision
 * that has aged out of the published window. Each has to be a different
 * answer, and the fixtures below are what tells them apart.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const M = require(path.join(__dirname, '..', 'public', 'js', 'decision-court-model.js'));
const LOG = require(path.join(__dirname, '..', 'public', 'js', 'decision-log-model.js'));
const i18n = require(path.join(__dirname, '..', 'public', 'js', 'i18n.js'));

const FULL = {
  decision_id: 'D-1',
  symbol: 'ETH/USDT',
  timestamp: '2026-09-15T09:56:40Z',
  outcome: 'EXECUTED_LIVE',
  is_paper: false,
  idea: {
    direction: 'LONG', confidence: 0.82,
    entry: 2475.93, sl: 2420, tp: 2600, rr: 2.1,
    strategy_type: 'swing', signal_type: 'breakout',
    reasoning: 'R1 range compression into the London open; R2 volume expansion.',
    votes: [{ name: 'rsi' }, { name: 'macd' }],
    explain: { top_bullish: ['higher lows', 'OI rising'], top_bearish: [] },
    provenance: { model_provider: 'grok', analysis_version: 'v4', prompt_hash: 'abc123def456', data_thin: false },
  },
  risk: { verdict: 'APPROVED', checks_failed: [], reason: '' },
  macro: { state: 'NORMAL', blackout: false },
  compliance: { consent: true },
  result: { pnl_usd: 137.42, exit_price: 2560.1, close_reason: 'TP HIT (exchange)' },
  chain: { sequence: 4212, entry_hash: 'de'.repeat(32), prev_hash: 'ca'.repeat(32) },
};

const sec = (court, id) => court.sections.find((s) => s.id === id);
const read = (rec, status) => M.decisionCourt({ record: rec }, status === undefined ? 200 : status);

test('a full record reads every section, and none of them is a word', () => {
  const c = read(FULL);
  assert.equal(c.id, 'D-1');
  assert.equal(c.head.side, 'long');
  assert.equal(c.head.book, 'live');
  assert.equal(c.head.conf, 0.82);
  for (const id of ['chain', 'thesis', 'plan', 'setup', 'evidence', 'macro', 'compliance', 'decided', 'execution']) {
    assert.equal(sec(c, id).word, null, id + ' should have rows');
    assert.ok(sec(c, id).rows.length, id + ' should have rows');
  }
});

test('the CHAIN is the half a log row never shows, and it is all three fields', () => {
  const rows = sec(read(FULL), 'chain').rows;
  assert.deepEqual(rows.map((r) => r.label.key),
    ['dd.dc_l_seq', 'dd.dc_l_entry', 'dd.dc_l_prev']);
  assert.equal(rows[0].value, '4212');
  assert.ok(rows.every((r) => r.mono === true), 'hashes are mono or they cannot be compared by eye');
});

test('a record with no chain says the WINDOW did not carry it — not that the chain broke', () => {
  const c = read(Object.assign({}, FULL, { chain: undefined }));
  const s = sec(c, 'chain');
  assert.equal(s.rows, null);
  assert.equal(s.word.key, 'dd.dc_no_chain');
  assert.match(s.word.en, /not the same as a broken chain/);
});

test('CONFIDENCE sealed as 0 is an absence, because the producer defaults it that way', () => {
  // scan_skill.py seals `float(cp.get("confidence", 0) or 0)`, so 0 is what an
  // UNREAD confidence arrives as. "acted at 0% confidence" is a claim no
  // record here can support.
  assert.equal(M.confidence(0), null);
  assert.equal(M.confidence(0.4), 0.4);
  assert.equal(M.confidence(1), 1);
  assert.equal(M.confidence(1.4), null, 'out of range is not a measurement');
  assert.equal(M.confidence(-0.2), null);
  assert.equal(M.confidence('0.8'), null, 'a numeric string in a sealed float is junk');

  const c = read({ idea: { confidence: 0 } });
  assert.equal(c.head.conf, null);
  assert.equal(c.head.confWord.key, 'dd.dc_no_conf');
});

test('a sealed price of 0 is the absence, and the rule is the SIBLING\'s', () => {
  const c = read({ idea: { entry: 0, sl: 0, tp: 0, rr: 0 } });
  const s = sec(c, 'plan');
  assert.equal(s.rows, null, 'three zeroed prices are three absences, not a plan');
  assert.equal(s.word.key, 'dd.dc_no_plan');
  // Not a private copy: the Court must answer whatever the log model answers.
  assert.equal(LOG.price(0), null);
  assert.equal(LOG.price(2475.93), 2475.93);
});

test('NO risk block is not a pass, and an UNKNOWN verdict is not a pass either', () => {
  const none = read({});
  assert.equal(none.gate.state, 'none');
  assert.equal(sec(none, 'gate').word.key, 'dd.dc_no_gate');
  assert.match(sec(none, 'gate').word.en, /not a pass/);

  const unknown = read({ risk: { verdict: 'UNKNOWN' } });
  assert.equal(unknown.gate.state, 'unread');
  assert.equal(sec(unknown, 'gate').word.key, 'dd.dc_gate_unread');
  assert.match(sec(unknown, 'gate').word.en, /not a pass/);
});

test('a REJECTED gate names the checks the log row could only count', () => {
  const c = read({
    outcome: 'REJECTED',
    risk: { verdict: 'REJECTED', checks_failed: ['max_slots', 'daily_dd_halt_pct'], reason: 'slot cap reached' },
  });
  assert.equal(c.gate.state, 'block');
  const rows = sec(c, 'gate').rows;
  assert.equal(rows[0].label.key, 'dd.dc_l_failed_chk');
  assert.equal(rows[0].value, 'max_slots · daily_dd_halt_pct');
  assert.equal(rows[0].bad, true);
  assert.equal(rows[1].value, 'slot cap reached');
});

test('a sealed verdict with no named checks says so, rather than showing nothing', () => {
  const s = sec(read({ risk: { verdict: 'APPROVED' } }), 'gate');
  assert.equal(s.rows, null);
  assert.equal(s.word.key, 'dd.dc_no_checks');
});

test('a block that is ABSENT and a block that carries nothing showable are two answers', () => {
  assert.equal(sec(read({}), 'macro').word.key, 'dd.dc_no_macro');
  assert.equal(sec(read({ macro: { nested: { a: 1 } } }), 'macro').word.key, 'dd.dc_nothing');
  assert.equal(sec(read({}), 'compliance').word.key, 'dd.dc_no_compl');
  assert.equal(sec(read({ compliance: { nested: [{}] } }), 'compliance').word.key, 'dd.dc_nothing');
});

test('an opaque block prints only scalars — never "[object Object]"', () => {
  assert.equal(M.scalar('x'), 'x');
  assert.equal(M.scalar(3), '3');
  assert.equal(M.scalar(true), 'yes');
  assert.equal(M.scalar(false), 'no');
  assert.equal(M.scalar(['a', 'b']), 'a · b');
  assert.equal(M.scalar({ a: 1 }), null);
  assert.equal(M.scalar([{ a: 1 }]), null, 'an array of objects yields no strings');
  assert.equal(M.scalar(NaN), null);
  assert.equal(M.scalar(Infinity), null);
  const rows = M.blockRows({ ok: 'yes', nested: { a: 1 }, n: 2 }, 12);
  assert.deepEqual(rows.map((r) => r.label), ['ok', 'n']);
});

test('nothing executed means there is no execution to report — not a zero fill', () => {
  const s = sec(read({ outcome: 'REJECTED_ON_RECHECK', risk: { verdict: 'REJECTED' } }), 'execution');
  // The disposition IS on record (it was stopped on re-check), so the section
  // shows it; what must not appear is a fill row.
  assert.ok(s.rows, 'a sealed disposition is still worth showing');
  assert.equal(s.rows.filter((r) => r.fill).length, 0, 'no fill row when nothing filled');

  const nothing = sec(read({}), 'execution');
  assert.equal(nothing.rows, null);
  assert.equal(nothing.word.key, 'dd.dc_no_exec');
});

test('the FILL keeps the log model\'s six states, including the anonymous pair', () => {
  const priced = { record: Object.assign({}, FULL), disclosure: '' };
  assert.equal(M.decisionCourt(priced, 200).anonymous, false);

  // The anonymous scrub drops pnl_usd; `fill_priced` is the ONLY thing that
  // says whether a number was ever there.
  const hidden = M.decisionCourt({
    record: Object.assign({}, FULL, { result: { fill_priced: true, exit_price: 2560.1 } }),
    disclosure: 'Anonymous view — percent/ratio only.',
  }, 200);
  assert.equal(hidden.anonymous, true);
  assert.equal(hidden.anonWord.key, 'dd.dc_anon');
  const hf = sec(hidden, 'execution').rows.find((r) => r.fill);
  assert.equal(hf.fill.state, 'hidden');

  const never = M.decisionCourt({
    record: Object.assign({}, FULL, { result: { fill_priced: false, exit_price: 2560.1 } }),
    disclosure: 'Anonymous view — percent/ratio only.',
  }, 200);
  assert.equal(sec(never, 'execution').rows.find((r) => r.fill).fill.state, 'unpriced');

  const unshown = M.decisionCourt({
    record: Object.assign({}, FULL, { result: { exit_price: 2560.1 } }),
    disclosure: 'Anonymous view — percent/ratio only.',
  }, 200);
  assert.equal(sec(unshown, 'execution').rows.find((r) => r.fill).fill.state, 'unshown');
});

test('a 404 is a fact about the WINDOW, and never a failed read', () => {
  const c = M.decisionCourt(null, 404);
  assert.equal(c.notFound, true);
  assert.equal(c.word.key, 'dd.dc_not_found');
  assert.match(c.word.en, /still exists in the chain/);
});

test('a 200 that did not parse THROWS — an empty dossier would assert the chain holds nothing', () => {
  assert.throws(() => M.decisionCourt(null, 200), /no body/);
  assert.throws(() => M.decisionCourt('<html>', 200), /no body/);
  assert.throws(() => M.decisionCourt({ chain: {} }, 200), /carried no record/);
  assert.throws(() => M.decisionCourt({ record: 'nope' }, 200), /carried no record/);
});

test('the absence words for time, symbol and direction are the LOG model\'s own', () => {
  // Not a copy: one string, or the two cards drift the first time either is
  // reworded.
  const c = read({});
  assert.equal(c.head.whenWord, LOG.W.noTime);
  assert.equal(c.head.symWord, LOG.W.noSym);
  assert.equal(c.head.sideWord, LOG.W.noDir);
  assert.ok(!M.KEYS.includes('dd.dl_no_time'), 'the borrowed keys are not claimed as this model\'s own');
});

test('every word this model can emit carries all fourteen languages', () => {
  // Read STRINGS[key][code] directly: translate() falls back to English, so a
  // guard that resolves through it cannot see a missing translation.
  const langs = i18n.LANGS.map((l) => l.code);
  const missing = [];
  for (const key of M.KEYS) {
    const entry = i18n.STRINGS[key];
    if (!entry) { missing.push(key + ':ABSENT'); continue; }
    for (const code of langs) {
      if (typeof entry[code] !== 'string' || !entry[code].length) missing.push(key + ':' + code);
    }
  }
  assert.deepEqual(missing, [], 'missing translations: ' + missing.join(', '));
});

test('every word in the table is reachable as a key, and the table has no duplicates', () => {
  const keys = Object.keys(M.W).map((k) => M.W[k].key);
  const own = keys.filter((k) => k.indexOf('dd.dc_') === 0);
  assert.deepEqual([...new Set(own)].sort(), [...own].sort(), 'a key spelled twice is two answers');
  assert.deepEqual([...M.KEYS].sort(), [...own].sort());
});
