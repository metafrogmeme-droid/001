/**
 * THE COURT'S WIRING — that the dossier is REACHED, and that the renderer
 * holds no second vocabulary.
 *
 * This is a SCAN and says so. `DecisionCourtModel`'s own suite drives every
 * reading; what a drive cannot cheaply prove is that the renderer did not
 * grow a `T('...')` of its own beside the model's table, or a colour literal
 * the model never asked for. Both are the shape the risk-backstop panel's
 * guard already forbids: a key the renderer spells and the model does not is
 * two vocabularies, and a colour chosen in the renderer is a verdict the
 * model did not reach.
 *
 * The one thing here that is DRIVEN is the door: a row with no decision_id
 * cannot be fetched, so no button may be painted for it — naming a door that
 * leads nowhere is the shape `/vault`'s command hint was cured of.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require(path.join(__dirname, 'helpers', 'code_only.js'));
const LOG = require(path.join(__dirname, '..', 'public', 'js', 'decision-log-model.js'));
const M = require(path.join(__dirname, '..', 'public', 'js', 'decision-court-model.js'));

const DASH = path.join(__dirname, '..', 'public', 'js', 'dashboard.js');
const src = () => codeOnly(fs.readFileSync(DASH, 'utf8'));

/** The Court's renderer block, comments already stripped. */
function courtBlock() {
  const s = src();
  const a = s.indexOf('function dcSay(');
  const b = s.indexOf('async function openDecisionCourt(');
  assert.ok(a > 0 && b > a, 'the Court renderers moved; this guard must move with them');
  // `openDecisionCourt` is the LAST of the Court's functions, so the block
  // ends at the next top-level declaration after it. Slicing to the end of
  // the file instead swept six thousand lines of unrelated code into the
  // "second vocabulary" verdict — a false accusation manufactured by the
  // guard's own boundary, which is the shape `indexOf`-slicing always has.
  const after = /\n  (?:async )?function \w+\(/.exec(s.slice(b + 10));
  assert.ok(after, 'nothing follows the door; the boundary cannot be found');
  return s.slice(a, b + 10 + after.index);
}

test('the renderer spells NO key of its own — one vocabulary, the model\'s', () => {
  const block = courtBlock();
  const keys = [...block.matchAll(/T\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(keys, [],
    'these keys are spelled in the renderer and not in DecisionCourtModel.W, '
    + 'so they are a second vocabulary: ' + keys.join(', '));
});

test('the renderer decides NO colour — every class it emits is structural', () => {
  const block = courtBlock();
  // `--up`, `--down`, `--warn`, `chip--*` and `dl-unread` are verdict colours.
  // The Court gets them only by rendering a chip the MODEL built (dlChip /
  // dlFillHtml), never by choosing one itself.
  const colour = [...block.matchAll(/'[^']*(chip--(?:up|down|warn|info)|rb-(?:up|down|warn|cap))[^']*'/g)]
    .map((m) => m[0]);
  assert.deepEqual(colour, [],
    'the renderer picks a verdict colour itself: ' + colour.join(', '));
});

test('the DOOR is painted only for a row that can be opened', () => {
  // Driven through the model rather than read off the renderer: reachability
  // is the whole claim, and a scan cannot see it.
  assert.equal(LOG.decisionRow({ decision_id: 'D-7' }, false).id, 'D-7');
  assert.equal(LOG.decisionRow({}, false).id, null);
  assert.equal(LOG.decisionRow({ decision_id: '   ' }, false).id, null,
    'a blank id is no id — it would fetch /flight/%20%20%20');
  // An INCIDENT is not a decision and has no dossier route at all.
  assert.equal('id' in LOG.incidentRow({ kind: 'block' }), false);

  const block = src().slice(src().indexOf('function dlSeqHtml('));
  assert.match(block.slice(0, 900), /if\s*\(!row\.id\)\s*return seq;/,
    'dlSeqHtml must return the bare sequence — no button — when there is no id');
  // The door's only CONTENT is "#4212", which announces a number rather than
  // what pressing it does. The accessible name is the whole difference for a
  // screen reader, and a mutation removing it survived the first round.
  assert.match(block.slice(0, 900), /aria-label="' \+ esc\(dlSay\(WORDS, \{ key: 'dd\.dl_open_dossier'/,
    'the door must carry an accessible name, and it must come from the words map');
  assert.ok(LOG.KEYS.includes('dd.dl_open_dossier'),
    'and that name is a key the LOG model owns, so the i18n sweep sees it');
});

test('the door is delegated on the PANEL, not bound per row', () => {
  // renderPanel replaces the panel's innerHTML on every refresh, so a handler
  // bound to a row is bound to an element the next paint has replaced.
  const s = src();
  assert.match(s, /C\('declog'\)\.addEventListener\('click'/,
    'the door must be delegated on the decision-log panel');
  assert.match(s, /closest\('\[data-dc-id\]'\)/);
  assert.match(s, /openDecisionCourt\(id, btn\)/);
});

test('a 404 goes to the MODEL, not to mustRead', () => {
  // mustRead would paint "could not load" over a decision that simply aged
  // out of the published window — a failed read manufactured from a fact
  // about the window.
  const s = src();
  const open = s.slice(s.indexOf('async function openDecisionCourt('));
  const body = open.slice(0, open.indexOf('\n  }\n') + 5);
  assert.ok(body.includes('decisionCourt(r.data, r.status)'),
    'the status must reach the model, which is the only thing that knows a 404 is not a failure');
  assert.ok(!/mustRead\s*\(/.test(body), 'mustRead must not stand between the 404 and its sentence');
});

test('dashboard.html loads the model before the script that reads it', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const court = html.indexOf('decision-court-model.js');
  const log = html.indexOf('decision-log-model.js');
  const dash = html.indexOf('<script src="/js/dashboard.js');
  assert.ok(court > 0, 'the Court model is not loaded at all — the door would throw');
  assert.ok(log < court, 'the Court reads the log model, so the log model loads first');
  assert.ok(court < dash, 'dashboard.js reads DecisionCourtModel at click time');
});

test('the spacing ramp defines every step it uses', () => {
  // `--s5` was referenced six times and defined nowhere; an undefined custom
  // property is invalid at computed-value time, so `padding: var(--s5)` was
  // no padding at all. Same shape as `--font-display`, found when a new rule
  // reached for the step that was not there.
  const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
  const used = new Set([...css.matchAll(/var\(\s*(--[a-z0-9-]+)\s*[,)]/g)].map((m) => m[1]));
  const defined = new Set([...css.matchAll(/(?:^|[;{])\s*(--[a-z0-9-]+)\s*:/gm)].map((m) => m[1]));
  const dead = [...used].filter((t) => /^--s\d+$/.test(t) && !defined.has(t));
  assert.deepEqual(dead, [], 'spacing tokens used but never defined: ' + dead.join(', '));
});

test('the Court model is a leaf over the log model — it holds no private copy', () => {
  const court = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'decision-court-model.js'), 'utf8'));
  for (const name of ['function gate(', 'function fill(', 'function price(', 'function disposition(']) {
    assert.ok(!court.includes(name),
      'the Court defines its own ' + name + ' — that reading belongs to DecisionLogModel, '
      + 'and a byte-identical copy agrees on every fixture until one of them is edited');
  }
  // And it really CALLS them, which the absence above cannot show.
  for (const call of ['LOG.gate(', 'LOG.fill(', 'LOG.price(', 'LOG.disposition(', 'LOG.checks(', 'LOG.side(', 'LOG.time(']) {
    assert.ok(court.includes(call), 'the Court must read ' + call + ' from the log model');
  }
  assert.ok(M.KEYS.every((k) => k.indexOf('dd.dc_') === 0));
});
