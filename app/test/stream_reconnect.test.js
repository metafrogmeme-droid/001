'use strict';

/**
 * "It works and after a little time it doesn't, seems like it disconnects."
 *
 * EventSource auto-reconnects, but ONLY after a transport-level drop. Per the
 * HTML spec, a response whose status is not 200 — or whose content-type is not
 * text/event-stream — makes the browser "fail the connection": it fires error,
 * sets readyState to CLOSED, and never tries again.
 *
 * This app generates exactly that response. The not-ready gate in server.js
 * refuses every /api/* with a 503 JSON body while the schema is unmigrated,
 * and an expired token gets a 401. So a database blip — of which there were
 * several on 2026-07-28/29 — permanently killed the stream in every open tab.
 * The page stayed rendered and populated; nothing ever updated again.
 *
 * The old handler was a comment asserting the opposite:
 *
 *     es.onerror = () => { // browser auto-reconnects; polling covers gaps };
 *
 * Both halves were wrong for the failure this app actually produces. Same
 * family as everything else found this week: a recovery path that only covers
 * one failure mode, and a claim in a comment standing in for a check.
 *
 * The second defect is the honesty one. The topbar dot read
 * "Engine live — last event 40s ago" from stream silence alone. Silence on a
 * stream is not evidence the engine is running — the stream may simply be
 * dead. And before the first event ever arrived the refresh returned early on
 * `!_lastBeat`, so a page that had never received anything looked identical
 * to one receiving events.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const P = (...p) => path.join(__dirname, '..', 'public', 'js', ...p);
const APP = fs.readFileSync(P('app.js'), 'utf8');
const DASH = fs.readFileSync(P('dashboard.js'), 'utf8');

/**
 * Extract connectStream and run it against a fake EventSource, so the
 * reconnect behaviour is EXERCISED rather than pattern-matched.
 */
/**
 * Index just past the `}` that closes the function `decl` opens.
 *
 * Brace-matched over comment-blanked source, so a `{` inside a comment or a
 * string cannot move the boundary — `codeOnly` keeps line lengths, so offsets
 * computed on the blanked copy are valid in the original.
 *
 * THE PARAMETER LIST IS SKIPPED FIRST, and the first draft did not: the next
 * `{` after `function connectStream` is the DEFAULT VALUE in
 * `(handlers, opts = {})`, so the matcher closed on it immediately and handed
 * back a 42-character "function". Seven tests failed with
 * `Unexpected identifier 'module'`, which names the symptom and points nowhere
 * near the cause.
 */
function endOfFunction(source, decl) {
  const { codeOnly } = require('./helpers/code_only');
  const code = codeOnly(source);
  const at = code.indexOf(decl);
  if (at < 0) return -1;
  // Walk the parameter list to its matching ')', then take the body's '{'.
  let p = code.indexOf('(', at);
  if (p < 0) return -1;
  let pd = 0;
  for (; p < code.length; p += 1) {
    if (code[p] === '(') pd += 1;
    else if (code[p] === ')') { pd -= 1; if (pd === 0) { p += 1; break; } }
  }
  let i = code.indexOf('{', p);
  if (i < 0) return -1;
  let depth = 0;
  for (; i < code.length; i += 1) {
    if (code[i] === '{') depth += 1;
    else if (code[i] === '}') { depth -= 1; if (depth === 0) return i + 1; }
  }
  return -1;
}

function loadConnectStream() {
  // THE END OF THIS SLICE USED TO BE A COMMENT — `indexOf('// ── Modal a11y')`.
  //
  // Extracting a function body and running it in a VM is the right shape here
  // (CLAUDE.md keeps it as a worked example), and the extraction is what makes
  // these seven tests real rather than source-matched. But a window whose far
  // edge is a section heading measures DISTANCE: rewording that heading takes
  // all seven out at once with "connectStream block not found", and moving it
  // silently grows the window until the sandbox is evaluating unrelated code.
  //
  // Both edges are code now. The start is the constant the block opens with,
  // and the end is the brace that closes `function connectStream` — found by
  // matching, so nothing between here and the next section can shift it.
  const start = APP.indexOf('const STREAM_RETRY_MIN_MS');
  assert.ok(start > 0, 'the stream retry block is gone from app.js');
  const end = endOfFunction(APP, 'function connectStream');
  assert.ok(end > start, 'connectStream is no longer a function declaration here');
  const src = APP.slice(start, end);

  const made = [];
  let now = 0;
  const timers = [];   // {at, fn}

  class FakeES {
    constructor(url) {
      this.url = url;
      this.readyState = 0;            // CONNECTING
      this.listeners = {};
      this.closed = false;
      made.push(this);
    }
    addEventListener(evt, fn) { (this.listeners[evt] = this.listeners[evt] || []).push(fn); }
    close() { this.closed = true; this.readyState = 2; }
    // ── test drivers ──
    /** The server answered 200 text/event-stream. */
    accept() { this.readyState = 1; if (this.onopen) this.onopen(); }
    /** The server answered 503 JSON — the browser fails the connection. */
    reject() { this.readyState = 2; if (this.onerror) this.onerror(); }
    /** The socket dropped; the browser will retry on its own. */
    blip() { this.readyState = 0; if (this.onerror) this.onerror(); }
    emit(evt, data) { (this.listeners[evt] || []).forEach(fn => fn({ data })); }
  }

  const sandbox = {
    EventSource: FakeES,
    // Object.create, not a spread: Math's methods are non-enumerable.
    Math: Object.assign(Object.create(Math), { random: () => 0.5 }),  // jitter → 1.0x
    setTimeout: (fn, ms) => { const h = { at: now + ms, fn }; timers.push(h); return h; },
    clearTimeout: (h) => { const i = timers.indexOf(h); if (i >= 0) timers.splice(i, 1); },
    module: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(src + '\nmodule.exports = connectStream;', sandbox);

  return {
    connectStream: sandbox.module.exports,
    made,
    /** Advance virtual time, firing due timers. */
    tick(ms) {
      now += ms;
      for (const h of timers.filter(t => t.at <= now)) {
        const i = timers.indexOf(h);
        if (i >= 0) timers.splice(i, 1);
        h.fn();
      }
    },
    pending: () => timers.length,
  };
}

// ── the reconnect ─────────────────────────────────────────────────────────

test('a 503 from the not-ready gate does not end the stream forever', () => {
  const h = loadConnectStream();
  const states = [];
  h.connectStream({}, { onState: (s) => states.push(s) });
  assert.equal(h.made.length, 1);

  h.made[0].reject();                       // readyState CLOSED — browser gave up
  assert.equal(h.made.length, 1, 'must wait out the backoff, not retry instantly');
  h.tick(2000);
  assert.equal(h.made.length, 2, 'we reconnect where the browser will not');
  assert.ok(states.some(s => s.connected === false));
});

test('a transport blip is left to the browser, not raced', () => {
  const h = loadConnectStream();
  h.connectStream({});
  h.made[0].blip();                         // readyState CONNECTING
  h.tick(120000);
  assert.equal(h.made.length, 1,
    'opening a second stream while the browser retries would duplicate events');
});

test('backoff grows and is capped', () => {
  const h = loadConnectStream();
  h.connectStream({});
  const waits = [];
  for (let i = 0; i < 8; i++) {
    const before = h.made.length;
    h.made[h.made.length - 1].reject();
    // find the smallest advance that produces a new stream
    let waited = 0;
    while (h.made.length === before) { h.tick(250); waited += 250; }
    waits.push(waited);
  }
  assert.ok(waits[0] <= 2250, `first retry is prompt, got ${waits[0]}ms`);
  assert.ok(waits[3] > waits[0], 'backoff must grow');
  assert.ok(Math.max(...waits) <= 61000, 'and be capped — got ' + Math.max(...waits));
});

test('a successful reconnect resets the backoff', () => {
  const h = loadConnectStream();
  h.connectStream({});
  for (let i = 0; i < 5; i++) { h.made[h.made.length - 1].reject(); h.tick(61000); }
  h.made[h.made.length - 1].accept();
  const before = h.made.length;
  h.made[h.made.length - 1].reject();
  h.tick(2500);
  assert.equal(h.made.length, before + 1,
    'after a good connection the next outage must retry promptly again');
});

test('handlers are rebound on every reconnect', () => {
  const h = loadConnectStream();
  const seen = [];
  h.connectStream({ portfolio: (e) => seen.push(e.data) });
  h.made[0].accept();
  h.made[0].emit('portfolio', 'first');
  h.made[0].reject();
  h.tick(2000);
  h.made[1].accept();
  h.made[1].emit('portfolio', 'second');
  assert.deepEqual(seen, ['first', 'second'],
    'a reconnected stream with no listeners is a stream that delivers nothing');
});

test('close() stops the loop for good', () => {
  const h = loadConnectStream();
  const s = h.connectStream({});
  h.made[0].reject();
  s.close();
  h.tick(120000);
  assert.equal(h.made.length, 1, 'a closed stream must not keep reopening');
  assert.equal(h.pending(), 0, 'and must leave no timer behind');
});

test('reconnecting is reported, not hidden', () => {
  const h = loadConnectStream();
  const states = [];
  h.connectStream({}, { onState: (s) => states.push(s) });
  h.made[0].accept();
  assert.equal(states[states.length - 1].connected, true);
  h.made[0].reject();
  assert.equal(states[states.length - 1].connected, false);
  h.tick(2000);
  h.made[1].accept();
  assert.equal(states[states.length - 1].connected, true);
});

test('retries are jittered so tabs do not reconnect in lockstep', () => {
  // The server that 503s is a server under strain; N tabs retrying on the
  // same tick is how a recovering process gets knocked back down.
  const block = APP.slice(APP.indexOf('const STREAM_RETRY_MIN_MS'),
                          APP.indexOf('// ── Modal a11y'));
  assert.match(block, /Math\.random\(\)/);
});

// ── the honesty of the dot ────────────────────────────────────────────────

test('the dot no longer claims the engine is live from stream silence', () => {
  // Check what is RENDERED, not what is written about it: the comment above
  // renderPulse quotes the old string deliberately, and that is documentation
  // rather than a claim made to a user.
  const titles = [...DASH.matchAll(/dot\.title\s*=\s*([\s\S]*?);/g)].map(m => m[1]);
  assert.ok(titles.length >= 3, 'expected the three pulse states');
  for (const src of titles) {
    assert.ok(!/Engine live/.test(src),
      `silence on a stream is not evidence the engine is running: ${src.trim()}`);
  }
  assert.match(DASH, /Live updates disconnected/);
});

test('the dot distinguishes disconnected, connected-and-quiet, and live', () => {
  const block = DASH.slice(DASH.indexOf('function renderPulse'),
                           DASH.indexOf('function beat()'));
  assert.match(block, /if \(!_streamUp\)/, 'disconnected is its own state');
  assert.match(block, /if \(!_lastBeat\)/, 'connected-but-silent is its own state');
  assert.match(block, /last engine event/, 'and a live stream reports its age');
});

test('a page that never received an event does not look identical to one that did', () => {
  // The old refresh returned early on `!_lastBeat`, so the dot never dimmed.
  const block = DASH.slice(DASH.indexOf('function renderPulse'),
                           DASH.indexOf('function beat()'));
  const zeroBeat = block.slice(block.indexOf('if (!_lastBeat)'));
  assert.match(zeroBeat.split('}')[0] + '}', /classList\.add\('quiet'\)/);
});

test('a disconnected dot warns that what is on screen may be stale', () => {
  assert.match(DASH, /may be stale/,
    'a frozen page that looks live is faked liveness');
});

test('the dashboard subscribes to the connection state', () => {
  assert.match(DASH, /onState: \(s\) =>/);
  assert.match(DASH, /_streamUp = !!\(s && s\.connected\)/);
});

test('a reconnect invalidates the caches it may have missed updates for', () => {
  const block = DASH.slice(DASH.indexOf('onState: (s) =>'), DASH.indexOf('// Drill-down'));
  assert.match(block, /cache\.scan = null/);
  assert.match(block, /cache\.portfolio = null/);
});

test('the cache-busters move past the build that had the bug', () => {
  // A fix that ships but does not reach the browser is not a fix: every open
  // tab keeps executing the cached bundle with the dead-stream handler in it.
  // app.js was v6 and dashboard.js v124 when this defect was live.
  for (const page of ['index.html', 'dashboard.html', 'arena.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'public', page), 'utf8');
    const m = html.match(/app\.js\?v=(\d+)/);
    assert.ok(m && Number(m[1]) >= 7, `${page} still serves app.js v${m && m[1]}`);
  }
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const d = dash.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(d && Number(d[1]) >= 125, `dashboard.js still at v${d && d[1]}`);
});
