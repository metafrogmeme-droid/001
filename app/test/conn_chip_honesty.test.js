'use strict';

/**
 * The topbar chip announced a trading outage that never happened.
 *
 *     const syncTime = cache.scan?.received_at || cache.scan?.timestamp;
 *     if (!syncTime) { el.textContent = '● ENGINE OFFLINE'; ... }
 *
 * cache.scan is null when the read FAILED, not only when there is no scan —
 * getScan() swallowed the result:
 *
 *     if (r.ok && r.data?.scan) { cache.scan = ...; }
 *     return cache.scan;                       // null on 503, null on error
 *
 * So during this week's database outages the site stayed up and every tab
 * announced ENGINE OFFLINE in the most prominent chip on the page, because
 * /api/bot/sync/scan was refused with a 503 by the not-ready gate. The engine
 * was running the whole time. That is a claim about the TRADING ENGINE
 * manufactured from the website being unable to read its own table, and it
 * costs an operator a trading-outage investigation that has no cause.
 *
 * The rule was already written three lines below the bug, on _haveTrades:
 * "absent is not zero". Same rule; the chip just never applied it.
 *
 * Four states, not two — and only one of them is "the engine is down".
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { engineChipState } = require('../public/js/engine-status-model.js');

// Still needed by the getScan tests below: THOSE assert how dashboard.js
// RECORDS the read outcome (cache.scanOk), which is not part of the verdict
// model and correctly stayed where it is.
const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

/**
 * The verdict now lives in a PURE model, so this harness calls it directly
 * instead of slicing updateConnChip's body out of dashboard.js and running it
 * in a VM. That workaround existed only because the logic had no seam — the
 * extraction is what removed the need for it. Every scenario below is
 * unchanged; only how the verdict is obtained changed.
 */
function chip(cacheState, nowMs = 1_700_000_000_000) {
  const c = Object.assign({ scan: null, scanAt: 0, scanOk: null }, cacheState);
  const syncTime = c.scan && (c.scan.received_at || c.scan.timestamp);
  const s = engineChipState(syncTime || null, c.scanOk, nowMs);
  return { textContent: s.text, className: `chip ${s.cls}`, title: s.title };
}

const iso = (msAgo, now = 1_700_000_000_000) => new Date(now - msAgo).toISOString();

// ── the bug ───────────────────────────────────────────────────────────────

test('an unreadable feed does not claim the engine is offline', () => {
  const el = chip({ scan: null, scanOk: false });
  assert.ok(!/OFFLINE/.test(el.textContent),
    `a failed read of our own database says nothing about the engine, got ${el.textContent}`);
  assert.match(el.textContent, /UNKNOWN/);
});

test('and it says whose problem it is', () => {
  const el = chip({ scan: null, scanOk: false });
  assert.match(el.title, /website problem/,
    'an operator must not spend the outage looking at the trading engine');
});

test('an unreadable feed is visually distinct from a dead engine', () => {
  const unknown = chip({ scan: null, scanOk: false });
  const dead = chip({ scan: { received_at: iso(3600_000) }, scanOk: true });
  assert.notEqual(unknown.className, dead.className,
    'two different facts must not render identically');
});

// ── the other three states ────────────────────────────────────────────────

test('before any read it says connecting, not offline', () => {
  const el = chip({ scan: null, scanOk: null });
  assert.match(el.textContent, /CONNECTING/);
  assert.ok(!/OFFLINE/.test(el.textContent));
});

test('a successful read holding no scan is not an outage either', () => {
  const el = chip({ scan: null, scanOk: true });
  assert.match(el.textContent, /NO ENGINE DATA/,
    'nothing recorded yet is a different fact from the engine being down');
  assert.match(el.title, /read successfully/);
});

test('a fresh scan still reads live', () => {
  const el = chip({ scan: { received_at: iso(60_000) }, scanOk: true });
  assert.match(el.textContent, /ENGINE LIVE/);
  assert.equal(el.className, 'chip chip--up');
});

test('the age thresholds are unchanged', () => {
  assert.match(chip({ scan: { received_at: iso(899_000) }, scanOk: true }).textContent, /LIVE/);
  assert.match(chip({ scan: { received_at: iso(901_000) }, scanOk: true }).textContent, /STALE/);
  assert.match(chip({ scan: { received_at: iso(1_801_000) }, scanOk: true }).textContent, /OFFLINE/);
});

test('the fallback timestamp field still works', () => {
  const el = chip({ scan: { timestamp: iso(60_000) }, scanOk: true });
  assert.match(el.textContent, /ENGINE LIVE/);
});

// ── what a payload plus a failed refresh means ────────────────────────────

test('a verdict backed by a payload stands, with the failure noted', () => {
  // Age is real evidence and only grows, so the verdict is still supportable.
  // What is NOT supportable is implying the figure is current.
  const el = chip({ scan: { received_at: iso(60_000) }, scanOk: false });
  assert.match(el.textContent, /ENGINE LIVE/, 'we do have a recent scan');
  assert.match(el.title, /refresh failed/, 'and must say we could not confirm it');
});

test('a good refresh carries no such caveat', () => {
  const el = chip({ scan: { received_at: iso(60_000) }, scanOk: true });
  assert.ok(!/refresh failed/.test(el.title));
  assert.match(el.title, /Last engine scan/);
});

// ── the read state is actually recorded ───────────────────────────────────

test('getScan records whether the read succeeded', () => {
  const block = SRC.slice(SRC.indexOf('async function getScan'),
                          SRC.indexOf('async function getTickers'));
  assert.match(block, /cache\.scanOk = !!\(r && r\.ok\)/,
    'the chip can only distinguish states the fetch bothered to record');
  assert.match(block, /\.catch\(\(\) => \(\{ ok: false/,
    'a thrown fetch is a failed read, not an absent scan');
});

test('a cache hit does not overwrite the read state', () => {
  const block = SRC.slice(SRC.indexOf('async function getScan'),
                          SRC.indexOf('async function getTickers'));
  const early = block.slice(0, block.indexOf('const r = await'));
  assert.match(early, /return cache\.scan;/, 'the early return still exists');
  assert.ok(!/scanOk/.test(early),
    'no read happened, so the read state must be left alone');
});

test('scanOk is tri-state, not a boolean', () => {
  const decl = SRC.slice(SRC.indexOf('const cache = {'), SRC.indexOf('async function getScan'));
  assert.match(decl, /scanOk: null/,
    '"never attempted" must be distinguishable from "attempted and failed"');
});
