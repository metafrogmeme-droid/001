'use strict';
/**
 * The Mini App splash handshake, driven against REAL comlink.
 *
 * The card rendered, the button was tappable, and tapping it did nothing —
 * because `sdk.actions.ready()` was never called and the splash never lifted.
 * From the outside that is indistinguishable from a dead server. Our silence,
 * rendering as their failure.
 *
 * The first attempt at fixing it was worse than the bug. It shipped
 *
 *     var sdk = window.farcaster?.sdk;
 *     if (sdk?.actions?.ready) sdk.actions.ready();
 *
 * onto a page whose CSP is `default-src 'none'; script-src 'self'` and which
 * loads no SDK at all. `window.farcaster` is undefined there and always would
 * be, so the guard was permanently false: code that is PRESENT and never
 * REACHED, which is #999 in CLAUDE.md and which no source scan can tell from
 * code that works. It never merged, and this file exists so the replacement
 * cannot fail the same way silently.
 *
 * WHY THESE TESTS ARE NOT UNIT TESTS OF MY OWN BELIEF. Asserting that
 * `readyFrame()` returns `{type:'APPLY', path:['ready']}` proves only that I
 * wrote down what I already thought. The question is whether a comlink host
 * ACCEPTS it — so the central test stands up comlink's own `expose()`, the
 * exact counterpart the Farcaster host runs (its endpoint.js does
 * `wrap(windowEndpoint(window.parent))`), and asserts the host's `ready`
 * actually executes. Get the frame wrong and comlink ignores it; the host
 * function never runs; this fails.
 *
 * WHAT IS STILL NOT PROVEN. That a live Warpcast host dismisses the splash.
 * That cannot be reached from CI, and no test here should be read as claiming
 * it. What is proven is that the frame is well-formed comlink and that the call
 * is reached — which is precisely the gap that made the first attempt worthless.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const FR = require('../public/js/farcaster-ready');

// ── the one that matters: a real comlink host runs our frame ──────────────

/**
 * Stand up a real comlink host and run one signalReady against it.
 *
 * The ports are closed in `finally`, and that is not tidiness. Written with the
 * close after the assertions, a FAILING assertion skips it, the open
 * MessageChannel holds the event loop, and `node --test` hangs instead of
 * failing — found by running the mutations below, where the first one turned a
 * two-second failure into a wedged test run. A test that hangs on the defect it
 * exists to catch is worse than no test: CI reports a timeout, which names
 * nothing.
 */
async function withComlinkHost(ready, run) {
  const Comlink = require('comlink');
  const { port1, port2 } = new MessageChannel();
  Comlink.expose({ ready }, port1);
  port2.start();
  try {
    // port2 stands in for `window.parent`: what the page posts to and what the
    // reply arrives on — the same shape comlink's windowEndpoint provides.
    return await run({
      parent: { postMessage: (msg) => port2.postMessage(msg) },
      addEventListener: (t, fn) => port2.addEventListener(t, fn),
      removeEventListener: (t, fn) => port2.removeEventListener(t, fn),
      setTimeout: setTimeout,
    });
  } finally {
    port1.close();
    port2.close();
  }
}

test('a real comlink host executes ready() from the frame we build', async () => {
  let calledWith = 'NEVER CALLED';

  const out = await withComlinkHost(
    async (opts) => { calledWith = opts; return 'dismissed'; },
    (win) => FR.signalReady({ disableNativeGestures: false },
      { window: win, document: null, waitMs: 2000 }));

  assert.notEqual(calledWith, 'NEVER CALLED',
    'comlink did not accept the frame — the host never ran ready(), which is '
    + 'the splash staying up in production with nothing on screen to say why');
  assert.deepEqual(calledWith, { disableNativeGestures: false },
    'the options did not survive the wire encoding');
  assert.equal(out.status, 'acknowledged');
  assert.equal(out.transport, 'iframe');
});

test('the argument really does cross the wire, not just the call', async () => {
  // A frame with a mangled argumentList can still invoke ready() with
  // undefined, which would pass a test that only checked "was it called".
  let seen = null;
  await withComlinkHost(
    async (o) => { seen = o; return 1; },
    (win) => FR.signalReady({ marker: 'carried-through', n: 42 },
      { window: win, document: null, waitMs: 2000 }));

  assert.deepEqual(seen, { marker: 'carried-through', n: 42 });
});

// ── composeCast: the same transport, and a cancel is not a failure ────────

/** As above, but exposing whatever `composeCast` the case needs. */
async function withComposeHost(composeCast, run) {
  const Comlink = require('comlink');
  const { port1, port2 } = new MessageChannel();
  Comlink.expose({ composeCast }, port1);
  port2.start();
  try {
    return await run({
      parent: { postMessage: (msg) => port2.postMessage(msg) },
      addEventListener: (t, fn) => port2.addEventListener(t, fn),
      removeEventListener: (t, fn) => port2.removeEventListener(t, fn),
      setTimeout: setTimeout,
    });
  } finally {
    port1.close();
    port2.close();
  }
}

test('a real comlink host receives the cast we asked it to compose', async () => {
  let seen = null;
  const out = await withComposeHost(
    async (opts) => { seen = opts; return { cast: { hash: '0xabc', text: opts.text } }; },
    (win) => FR.composeCast(
      { text: 'SHORT GME · 76% conf', embeds: ['https://example.test/embed/signals'] },
      { window: win, document: null, waitMs: 2000 }));

  assert.equal(seen && seen.text, 'SHORT GME · 76% conf',
    'the composer text never reached the host');
  assert.deepEqual(seen.embeds, ['https://example.test/embed/signals']);
  assert.equal(out.status, 'posted');
  assert.equal(out.hash, '0xabc');
});

test('a cancelled composer is "cancelled", not an error', async () => {
  // `{cast: null}` is the host reporting a DECISION: the composer opened and
  // the person chose not to send. Rendering that as a failure would put an
  // error on a screen where nothing went wrong.
  const out = await withComposeHost(
    async () => ({ cast: null }),
    (win) => FR.composeCast({ text: 'x' },
      { window: win, document: null, waitMs: 2000 }));

  assert.equal(out.status, 'cancelled');
  assert.equal(out.hash, null);
});

test('a host that answers without a hash is not reported as posted', async () => {
  // A cast we cannot identify is not one we can claim was published.
  const out = await withComposeHost(
    async () => ({ cast: {} }),
    (win) => FR.composeCast({}, { window: win, document: null, waitMs: 2000 }));
  assert.equal(out.status, 'cancelled');
});

test('no reply inside the window is "unknown", never "posted" or "cancelled"', async () => {
  // The composer is probably open and being typed into. We do not know the
  // outcome, and both other words would state one.
  const out = await FR.composeCast({}, {
    window: {
      parent: { postMessage: () => {} },
      addEventListener: () => {},
      removeEventListener: () => {},
      setTimeout: setTimeout,
    },
    document: null,
    waitMs: 20,
  });
  assert.equal(out.status, 'unknown');
});

test('composeCast outside a Mini App reports no-host', async () => {
  const win = {};
  win.parent = win;
  const out = await FR.composeCast({}, { window: win, document: null, waitMs: 10 });
  assert.equal(out.status, 'no-host');
});

test('ready and composeCast build the same frame with different paths', () => {
  // The generalisation is the point: one transport, proven once. A second
  // hand-written frame would be a second thing to get wrong.
  const r = FR.actionFrame('id-1', 'ready', { a: 1 });
  const c = FR.actionFrame('id-1', 'composeCast', { a: 1 });
  assert.equal(r.type, 'APPLY');
  assert.equal(c.type, 'APPLY');
  assert.deepEqual(r.path, ['ready']);
  assert.deepEqual(c.path, ['composeCast']);
  assert.deepEqual(r.argumentList, c.argumentList);
  assert.deepEqual(FR.readyFrame('id-1', { a: 1 }), r,
    'readyFrame drifted from the general one it is meant to be an alias of');
});

// ── signIn: the same transport again, and a decline is not a failure ──────

test('a real comlink host returns the signed SIWF message', async () => {
  const Comlink = require('comlink');
  let asked = null;
  const { port1, port2 } = new MessageChannel();
  Comlink.expose({
    signIn: async (opts) => {
      asked = opts;
      return { result: { message: 'domain wants you to...', signature: '0xsig' } };
    },
  }, port1);
  port2.start();
  try {
    const out = await FR.signIn({ nonce: 'server-issued-nonce' }, {
      window: {
        parent: { postMessage: (m) => port2.postMessage(m) },
        addEventListener: (t, f) => port2.addEventListener(t, f),
        removeEventListener: (t, f) => port2.removeEventListener(t, f),
        setTimeout: setTimeout,
      },
      document: null,
      waitMs: 2000,
    });
    assert.equal(asked && asked.nonce, 'server-issued-nonce',
      'the server-issued nonce never reached the host — the signature would be bound to nothing');
    assert.equal(out.status, 'signed');
    assert.equal(out.signature, '0xsig');
    assert.ok(out.message);
  } finally {
    port1.close();
    port2.close();
  }
});

test('a declined sign-in is "rejected", not an error', async () => {
  // The person was shown a prompt and said no. Rendering that as a fault would
  // put an error on a screen where nothing went wrong.
  const Comlink = require('comlink');
  const { port1, port2 } = new MessageChannel();
  Comlink.expose({ signIn: async () => ({ error: { type: 'rejected_by_user' } }) }, port1);
  port2.start();
  try {
    const out = await FR.signIn({ nonce: 'n' }, {
      window: {
        parent: { postMessage: (m) => port2.postMessage(m) },
        addEventListener: (t, f) => port2.addEventListener(t, f),
        removeEventListener: (t, f) => port2.removeEventListener(t, f),
        setTimeout: setTimeout,
      },
      document: null,
      waitMs: 2000,
    });
    assert.equal(out.status, 'rejected');
    assert.equal(out.signature, null);
  } finally {
    port1.close();
    port2.close();
  }
});

test('a result missing its signature is not reported as signed', async () => {
  // Half an answer is not an answer: without both halves there is nothing to
  // POST, and calling it a success would send an empty sign-in to the server.
  const Comlink = require('comlink');
  const { port1, port2 } = new MessageChannel();
  Comlink.expose({ signIn: async () => ({ result: { message: 'm' } }) }, port1);
  port2.start();
  try {
    const out = await FR.signIn({ nonce: 'n' }, {
      window: {
        parent: { postMessage: (m) => port2.postMessage(m) },
        addEventListener: (t, f) => port2.addEventListener(t, f),
        removeEventListener: (t, f) => port2.removeEventListener(t, f),
        setTimeout: setTimeout,
      },
      document: null,
      waitMs: 2000,
    });
    assert.equal(out.status, 'rejected');
  } finally {
    port1.close();
    port2.close();
  }
});

test('signIn outside a Mini App reports no-host', async () => {
  const win = {};
  win.parent = win;
  const out = await FR.signIn({ nonce: 'n' }, { window: win, document: null, waitMs: 10 });
  assert.equal(out.status, 'no-host');
});

test('the page never invents its own nonce', () => {
  // A nonce the client generates is one an attacker can generate too, which
  // binds the signature to nothing and makes replay free. It must come from
  // the server. Pinned because the mistake looks like a simplification.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'farcaster-ready.js'), 'utf8'));
  const fn = src.slice(src.indexOf('function signIn'));
  const body = fn.slice(0, fn.indexOf('\n  }\n'));
  assert.doesNotMatch(body, /nonce\s*[:=]\s*(frameId|Math\.random|Date\.now)/,
    'signIn generates its own nonce — the signature would be bound to a value '
    + 'the caller chose, which is the same as being bound to nothing');
});

// ── three states, and none of them is the confident one ───────────────────

test('a plain tab reports no-host rather than pretending it announced itself', async () => {
  // window.parent === window is a normal browser tab. Posting to ourselves
  // would "succeed" and mean nothing; reporting that as sent would be a claim
  // about a host that was never there.
  const win = {};
  win.parent = win;
  const out = await FR.signalReady({}, { window: win, document: null, waitMs: 10 });
  assert.equal(out.status, 'no-host');
  assert.equal(out.transport, null);
});

test('posted-but-unanswered is "sent", never "acknowledged"', async () => {
  // The common real case: a host that lifts the splash without replying. We
  // know we posted. We do NOT know it arrived, and the two words say so.
  const out = await FR.signalReady({}, {
    window: {
      parent: { postMessage: () => {} },
      addEventListener: () => {},
      removeEventListener: () => {},
      setTimeout: setTimeout,
    },
    document: null,
    waitMs: 20,
  });
  assert.equal(out.status, 'sent');
});

test('a reply for someone else is not read as our acknowledgement', async () => {
  // Frames from other comlink traffic share the channel. Matching on anything
  // looser than the id would report acknowledged on somebody else's reply.
  let deliver = null;
  const out = await FR.signalReady({}, {
    window: {
      parent: { postMessage: () => { setTimeout(() => deliver({ data: { id: 'someone-else', value: 'x' } }), 0); } },
      addEventListener: (t, fn) => { deliver = fn; },
      removeEventListener: () => {},
      setTimeout: setTimeout,
    },
    document: null,
    waitMs: 40,
  });
  assert.equal(out.status, 'sent', 'a foreign frame was accepted as our reply');
});

test('a host that throws on postMessage does not take the board down', async () => {
  // The board is the product; the splash call is a courtesy to one host.
  const out = await FR.signalReady({}, {
    window: {
      parent: { postMessage: () => { throw new Error('refused'); } },
      addEventListener: () => {},
      removeEventListener: () => {},
      setTimeout: setTimeout,
    },
    document: null,
    waitMs: 10,
  });
  assert.equal(out.status, 'no-host');
});

// ── the react-native transport, which the SDK picks first ─────────────────

test('a ReactNativeWebView host gets a JSON string on the bridge', async () => {
  // endpoint.js checks window.ReactNativeWebView BEFORE the iframe path and
  // sends a STRING, not an object. Picking the wrong branch here posts an
  // object the native bridge drops silently.
  let posted = null;
  const win = {
    ReactNativeWebView: { postMessage: (s) => { posted = s; } },
    parent: null,
    setTimeout: setTimeout,
  };
  const doc = { addEventListener: () => {}, removeEventListener: () => {} };

  const out = await FR.signalReady({ a: 1 }, { window: win, document: doc, waitMs: 10 });

  assert.equal(out.transport, 'reactnative');
  assert.equal(typeof posted, 'string', 'the native bridge takes a JSON string');
  const frame = JSON.parse(posted);
  assert.equal(frame.type, 'APPLY');
  assert.deepEqual(frame.path, ['ready']);
});

// ── reachability, because that is how the first attempt failed ────────────

test('the embed page loads this module and calls it', () => {
  // The whole point. A perfect handshake in a file nobody includes is the
  // previous bug with extra steps.
  const { codeOnly } = require('./helpers/code_only');
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'embed.js'), 'utf8');
  assert.match(route, /farcaster-ready\.js/,
    'routes/embed.js does not serve farcaster-ready.js, so RCFarcasterReady is undefined in the page');

  const page = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'embed-signals.js'), 'utf8'));
  assert.match(page, /RCFarcasterReady/,
    'the page never calls signalReady — the splash stays up');
});

test('the announcement survives every way the board can end', () => {
  // A SOURCE SCAN, deliberately, and the narrow legitimate kind: this locks
  // WIRING that behaviour tests elsewhere cannot see, because the call site is
  // top-level in an IIFE that runs on page load. The three properties are the
  // three ways this has a mouth and no voice:
  //
  //   both handlers   announcing only on success leaves a Farcaster user
  //                   staring at a splash whenever /api/signals is down —
  //                   our silence rendering as a broken app.
  //   a timer         `fetch` has no timeout, so a hung connection leaves
  //                   load() pending forever and the splash with it.
  //   idempotence     load() re-runs every 30s; re-announcing on a timer is
  //                   noise the host never asked for.
  const { codeOnly } = require('./helpers/code_only');
  const page = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'embed-signals.js'), 'utf8'));

  assert.match(page, /load\(\)\s*\.then\(\s*announceReady\s*,\s*announceReady\s*\)/,
    'announceReady is not on BOTH promise paths — an error state would hold the splash');
  assert.match(page, /setTimeout\(\s*announceReady\s*,/,
    'nothing announces readiness if load() never settles');
  assert.match(page, /announced\s*=\s*true/,
    'no idempotence guard — the host gets a ready frame every refresh');
  assert.doesNotMatch(page, /setInterval\([^)]*announceReady/,
    'readiness is being re-announced on the refresh timer');
});

test('nothing reaches for window.farcaster, which does not exist here', () => {
  // The exact expression that shipped and could never fire. Pinned so a future
  // "restore the official call" edit fails loudly instead of going quiet.
  const { codeOnly } = require('./helpers/code_only');
  for (const f of ['embed-signals.js', 'farcaster-ready.js']) {
    const src = codeOnly(fs.readFileSync(
      path.join(__dirname, '..', 'public', 'js', f), 'utf8'));
    assert.doesNotMatch(src, /window\.farcaster/,
      `${f} reads window.farcaster, which the embed CSP guarantees is undefined`);
  }
});

test('the embed CSP still forbids loading an SDK from anywhere else', () => {
  // If a later change adds a CDN <script> for the real SDK, this page stops
  // being safe to frame on the terms embed.js argues for. The shim exists
  // partly so that trade never has to be made.
  const { embedCsp } = require('../routes/embed');
  const csp = embedCsp();
  assert.match(csp, /script-src 'self'/);
  assert.match(csp, /default-src 'none'/);
});
