'use strict';
/**
 * SIX FACTS, AND NOT ONE OF THEM NAMES THE GATE.
 *
 * The setups panel had one empty state and one sentence for it: "No qualifying
 * setups in the last scan — the gate is doing its job." That named a control
 * that never ran (the filter is the SCANNER's score; the risk gate runs at
 * confirm time, which the panel's own footer already says), it fired when the
 * bot's cycle summary had wiped the last scan's cards, and it reported a FAILED
 * READ — a candidate whose ATR the loop could not read, which before
 * `record_atr` kept significant digits was every sub-cent asset — as
 * discipline.
 *
 * So the producer says WHY and this model turns it into one of six facts. It
 * derives no sentence the producer already states and picks no colour.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const EC = require('../public/js/entry-cards-model.js');

/** The reading a real scan publishes, whose counts close over `considered`. */
function reading(over) {
  return { results: 40, above_floor: 3, considered: 3, cards: 1,
           no_atr: 2, no_direction: 0, floor: 0.4, shown_max: 8, ...over };
}

const CARD = { symbol: 'BTC', direction: 'LONG', entry: '62970',
               stop_loss: '62750', tp1: '63300', rr: '1.5' };

const AT = '2026-09-26T12:00:00.000Z';
const NOW = Date.parse('2026-09-26T12:30:00.000Z');

test('cards on the payload are cards, whatever the counts say', () => {
  const scan = { entry_cards: [CARD], entry_cards_read: reading(), scan_at: AT };
  assert.equal(EC.state(scan), 'cards');
  assert.equal(EC.why(scan), null);
});

test('no scan on record is not a scan that found nothing', () => {
  // Neither block: this build pushes both together, so a store that has only
  // ever carried cycle summaries has neither.
  for (const scan of [null, undefined, {}, { circuit_breaker: {} }]) {
    assert.equal(EC.state(scan), 'no_scan', JSON.stringify(scan));
    assert.equal(EC.why(scan).w.key, 'dd.ec_no_scan');
  }
});

test('a scan that read no symbols says so', () => {
  const scan = { entry_cards: [], entry_cards_read: reading({ results: 0, above_floor: 0,
                                                              considered: 0, cards: 0,
                                                              no_atr: 0 }) };
  assert.equal(EC.state(scan), 'nothing_read');
  assert.equal(EC.why(scan).w.key, 'dd.ec_nothing_read');
});

test('nothing above the floor names the scanner, not the gate', () => {
  const scan = { entry_cards: [], entry_cards_read: reading({ above_floor: 0, considered: 0,
                                                              cards: 0, no_atr: 0 }) };
  assert.equal(EC.state(scan), 'below_floor');
  const w = EC.why(scan);
  assert.equal(w.w.key, 'dd.ec_below_floor');
  assert.equal(w.params.results, 40);
  assert.equal(w.params.floor, 0.4, 'the floor travels; a copy here is a second answer');
  assert.match(w.w.en, /scanner/);
  assert.equal(/risk gate is|gate is doing/.test(w.w.en), false);
});

test('candidates that could not be priced is a failed read, said as one', () => {
  const scan = { entry_cards: [], entry_cards_read: reading({ cards: 0, no_atr: 2,
                                                              no_direction: 1,
                                                              above_floor: 3 }) };
  assert.equal(EC.state(scan), 'unpriced');
  const w = EC.why(scan);
  assert.equal(w.w.key, 'dd.ec_unpriced');
  assert.equal(w.params.above, 3);
  assert.equal(w.params.noAtr, 2);
  assert.equal(w.params.noDir, 1);
  assert.match(w.w.en, /failed read, not a gate/);
});

test('an older bot that sent a list and no reading says it is not on record', () => {
  // It always sent `entry_cards` and never sent why, so its empty list cannot
  // be told from a scan that ran and found nothing. The panel says exactly that
  // rather than guessing, which is the honest half of the deploy window:
  // `app/` and `bot/` are different deploy targets.
  const scan = { entry_cards: [] };
  assert.equal(EC.state(scan), 'unknown');
  assert.equal(EC.why(scan).w.key, 'dd.ec_unknown');
});

test('a reading whose counts are unreadable is unknown, never a market claim', () => {
  for (const bad of [{ results: null }, { results: '40' }, { above_floor: undefined },
                     { results: NaN }, { above_floor: 'three' }]) {
    const scan = { entry_cards: [], entry_cards_read: reading(bad) };
    assert.equal(EC.state(scan), 'unknown', JSON.stringify(bad));
  }
});

test('a reading that is not an object is no reading', () => {
  for (const bad of [[], 'x', 7, null, true]) {
    assert.equal(EC.reading({ entry_cards_read: bad }), null, JSON.stringify(bad));
  }
});

test('no state names the gate, in any of the six', () => {
  const scans = [
    { entry_cards: [CARD], entry_cards_read: reading(), scan_at: AT },
    {},
    { entry_cards: [], entry_cards_read: reading({ results: 0, above_floor: 0 }) },
    { entry_cards: [], entry_cards_read: reading({ above_floor: 0, cards: 0 }) },
    { entry_cards: [], entry_cards_read: reading({ cards: 0, no_atr: 3 }) },
    { entry_cards: [] },
  ];
  const seen = new Set();
  for (const scan of scans) {
    seen.add(EC.state(scan));
    const w = EC.why(scan);
    if (!w) continue;
    assert.equal(/the gate is doing its job/.test(w.w.en), false, w.w.key);
  }
  assert.equal(seen.size, 6, 'six facts, and the table reaches all of them');
});

// ── the note under the cards ──────────────────────────────────────────────

test('a fresh scan says when it ran and claims nothing about age', () => {
  const scan = { entry_cards: [CARD], entry_cards_read: reading({ above_floor: 1, cards: 1,
                                                                  no_atr: 0, considered: 1 }),
                 scan_at: AT };
  const notes = EC.note(scan, NOW);
  assert.equal(notes[0].w.key, 'dd.ec_from_scan');
  assert.equal(notes[0].stale, false);
  assert.equal(notes.length, 1, 'no bound and no dropped row when neither bites');
});

test('a scan past the page\'s own bound says the prices have moved', () => {
  const scan = { entry_cards: [CARD],
                 entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0 }),
                 scan_at: AT };
  const notes = EC.note(scan, Date.parse(AT) + EC.STALE_MS + 1);
  assert.equal(notes[0].w.key, 'dd.ec_old');
  assert.equal(notes[0].stale, true, 'a stale reading is a warning, not a muted aside');
  assert.equal(notes[0].params.ago, EC.STALE_MS + 1);
});

test('the bound is this page\'s own three hours, not a second answer', () => {
  assert.equal(EC.STALE_MS, 3 * 3600 * 1000);
});

test('an undated scan says it is undated, never "just now"', () => {
  for (const at of [undefined, null, '', 'not-a-date', 7]) {
    const scan = { entry_cards: [CARD], entry_cards_read: reading({ above_floor: 1, cards: 1,
                                                                    no_atr: 0 }),
                   scan_at: at };
    assert.equal(EC.note(scan, NOW)[0].w.key, 'dd.ec_undated', JSON.stringify(at));
    assert.equal(EC.ageMs(scan, NOW), null);
  }
});

test('a stamp in the future is a clock disagreement, not an age', () => {
  const scan = { entry_cards: [CARD], scan_at: AT };
  assert.equal(EC.ageMs(scan, Date.parse(AT) - 60000), null);
  assert.equal(EC.note(scan, Date.parse(AT) - 60000)[0].w.key, 'dd.ec_undated');
});

test('a bounded list states its total, and only when it bites', () => {
  const partial = { entry_cards: [CARD], scan_at: AT,
                    entry_cards_read: reading({ above_floor: 6, cards: 1, no_atr: 0,
                                                no_direction: 0, considered: 6 }) };
  const keys = EC.note(partial, NOW).map((n) => n.w.key);
  assert.ok(keys.includes('dd.ec_partial'));
  const exact = { entry_cards: [CARD], scan_at: AT,
                  entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                              considered: 1 }) };
  assert.equal(EC.note(exact, NOW).map((n) => n.w.key).includes('dd.ec_partial'), false,
    'a permanent "showing 1 of 1" trains a reader to stop reading the line');
});

test('what could not be priced is said beside the cards that could', () => {
  const scan = { entry_cards: [CARD], scan_at: AT,
                 entry_cards_read: reading({ above_floor: 4, cards: 1, no_atr: 2,
                                             no_direction: 1, considered: 4 }) };
  const dropped = EC.note(scan, NOW).find((n) => n.w.key === 'dd.ec_dropped');
  assert.ok(dropped, 'the two read failures are counted, not swallowed');
  assert.equal(dropped.params.n, 3);
});

test('nothing dropped prints no dropped row', () => {
  const scan = { entry_cards: [CARD], scan_at: AT,
                 entry_cards_read: reading({ above_floor: 1, cards: 1, no_atr: 0,
                                             no_direction: 0, considered: 1 }) };
  assert.equal(EC.note(scan, NOW).some((n) => n.w.key === 'dd.ec_dropped'), false);
});

test('a note is only for a payload that HAS cards', () => {
  for (const scan of [{}, { entry_cards: [] },
                      { entry_cards: [], entry_cards_read: reading() }]) {
    assert.deepEqual(EC.note(scan, NOW), [], JSON.stringify(scan));
  }
});

test('a card list that is not an array is no list', () => {
  for (const bad of [null, undefined, {}, 'x', 3]) {
    assert.equal(EC.cardList({ entry_cards: bad }), null, JSON.stringify(bad));
  }
});

test('every key the model can emit is declared once', () => {
  const keys = EC.KEYS;
  assert.equal(new Set(keys).size, keys.length, 'a duplicate key is two answers');
  for (const k of keys) assert.match(k, /^dd\.ec_/);
  assert.equal(keys.length, Object.keys(EC.W).length);
});

test('the footer still says where the gate runs', () => {
  // The panel's own footer has always said confirmations run through the risk
  // gate. That sentence is TRUE and is the reason the empty state must not
  // claim the gate screened anything out.
  assert.match(EC.W.footer.en, /risk gate/);
});
