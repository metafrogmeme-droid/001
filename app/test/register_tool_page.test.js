'use strict';
/**
 * The operator's registration page: does its Send button REFUSE?
 *
 * The page exists because no wallet has a UI for a raw contract call, so
 * something must build the transaction. What makes it worth having rather than
 * typing three fields into a block explorer is that it recomputes the manifest
 * hash in the browser, from the published preimage, and gates the send on the
 * result. This endpoint served plain SHA-256 in a field labelled keccak256 for
 * a while; both are well-formed 32-byte hex, and the wrong one is permanent.
 *
 * So the tests that matter are the refusals. A page that sends when the hash is
 * wrong is worse than no page at all — it lends the wrong value a ceremony.
 *
 * The page logic is driven directly, in a VM with a stub DOM, the way
 * engine_status_scenarios does. Source-scanning it would pass with the guard
 * present and unreached, which is #999 exactly.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const keccak = require('../public/js/keccak256.js');
const t8257 = require('../lib/tool8257');

const PAGE_JS = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'register-tool.js'), 'utf8');
const PAGE_HTML = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'register-tool.html'), 'utf8');

/** Minimal DOM: every id the page touches, recording what it was told. */
function makeDom() {
  const els = {};
  const mk = (id) => (els[id] = {
    id, textContent: '', className: '', disabled: false, hidden: true,
    _handlers: {},
    addEventListener(ev, fn) { this._handlers[ev] = fn; },
  });
  ['vFetch', 'vHash', 'vCalldata', 'vReady', 'vDrift', 'fHash', 'fUri',
    'fRegistry', 'fPredicate', 'fCalldata', 'fCreator', 'fEndpoint',
    'send', 'why', 'status', 'reload', 'afterSend', 'recordCmd'].forEach(mk);
  return {
    els,
    document: {
      getElementById: (id) => els[id] || null,
      addEventListener(ev, fn) { this._ready = fn; },
      _fire() { if (this._ready) return this._ready(); },
    },
  };
}

/** Run the page against a given plan payload; resolve once it has rendered. */
async function run(plan, { fetchFails = false } = {}) {
  const dom = makeDom();
  const ctx = {
    document: dom.document,
    window: { ethereum: null },
    Keccak256: keccak,
    console,
    fetch: async () => {
      if (fetchFails) throw new Error('network down');
      return { ok: true, json: async () => plan };
    },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(PAGE_JS, ctx);
  await dom.document._fire();
  // let the async load() settle
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  return dom.els;
}

/** A genuinely correct plan, built by the real module. */
function goodPlan() {
  const saved = { ...process.env };
  process.env.APP_BASE_URL = 'https://www.runeclaw.test';
  process.env.TOOL_CREATOR_ADDRESS = '0x' + 'ab'.repeat(20);
  delete process.env.REGISTERED_MANIFEST_HASH;
  try {
    return t8257.buildRegistrationPlan({ tools: require('../routes/mcp').TOOLS });
  } finally {
    for (const k of ['APP_BASE_URL', 'TOOL_CREATOR_ADDRESS', 'REGISTERED_MANIFEST_HASH']) {
      if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k];
    }
  }
}

// ── it sends only when everything checks out ─────────────────────────────────

test('a correct plan enables the send', async () => {
  const els = await run(goodPlan());
  assert.equal(els.send.disabled, false, els.why.textContent);
  assert.match(els.vHash.textContent, /matches/);
  assert.match(els.vCalldata.textContent, /registerTool/);
});

// ── the refusals, which are the point ────────────────────────────────────────

test('REFUSES a hash that is not keccak256 of the preimage', async () => {
  // The actual incident: sha256 of the canonical bytes, served in a field
  // named manifest_hash. Well-formed, plausible, and permanent once sent.
  const p = goodPlan();
  p.manifest_hash = '0x' + require('node:crypto')
    .createHash('sha256').update(p.manifest_canonical).digest('hex');
  const els = await run(p);
  assert.equal(els.send.disabled, true);
  assert.match(els.vHash.textContent, /MISMATCH/);
  assert.match(els.why.textContent, /not keccak256 of the preimage/);
});

test("REFUSES the stub's '0x' calldata", async () => {
  const p = goodPlan();
  p.calldata = '0x';
  const els = await run(p);
  assert.equal(els.send.disabled, true);
  assert.match(els.vCalldata.textContent, /EMPTY/);
});

test('REFUSES calldata whose selector is a different function', async () => {
  const p = goodPlan();
  p.calldata = '0xdeadbeef' + p.calldata.slice(10);
  const els = await run(p);
  assert.equal(els.send.disabled, true);
  assert.match(els.vCalldata.textContent, /not registerTool/);
});

test('REFUSES when the calldata and the displayed hash disagree', async () => {
  // Two fields that must agree; a plan where they do not is worse than either
  // being wrong alone, because the page shows one and sends the other.
  const p = goodPlan();
  p.manifest_hash = keccak.keccak256Utf8('something else entirely');
  p.manifest_canonical = 'something else entirely';
  const els = await run(p);
  assert.equal(els.send.disabled, true);
  assert.match(els.vCalldata.textContent, /does not carry the hash/);
});

test('REFUSES a plan the server itself calls not ready', async () => {
  const p = goodPlan();
  p.ready = false;
  p.not_ready_reasons = ['set TOOL_CREATOR_ADDRESS'];
  const els = await run(p);
  assert.equal(els.send.disabled, true);
  assert.match(els.vReady.textContent, /TOOL_CREATOR_ADDRESS/);
});

test('a failed fetch renders an error, never an empty page with a live button', async () => {
  // Unreadable is never zero. A blank panel beside an enabled Send is the
  // shape this whole repo exists to prevent.
  const els = await run(null, { fetchFails: true });
  assert.equal(els.send.disabled, true,
    'the button must stay disabled when nothing could be checked');
  assert.match(els.vFetch.textContent, /could not read the plan/);
  assert.match(els.why.textContent, /Nothing was checked/);
});

test('a missing keccak module fails the check rather than skipping it', async () => {
  // If the verifier does not load, the honest outcome is refusal — not a page
  // that silently drops the one check it exists for.
  const dom = makeDom();
  const ctx = {
    document: dom.document, window: { ethereum: null }, console,
    fetch: async () => ({ ok: true, json: async () => goodPlan() }),
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(PAGE_JS, ctx);
  await dom.document._fire();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(dom.els.send.disabled, true);
  assert.match(dom.els.vHash.textContent, /did not load/);
});

// ── drift is three-valued here too ───────────────────────────────────────────

test('prior registration: matches / drifted / unknown stay distinguishable', async () => {
  const base = goodPlan();
  let els = await run({ ...base, registration_check: 'matches' });
  assert.match(els.vDrift.textContent, /matches the recorded registration/);

  els = await run({ ...base, registration_check: 'drifted' });
  assert.match(els.vDrift.textContent, /ALREADY REGISTERED at a different hash/);

  els = await run({ ...base, registration_check: 'not_recorded' });
  assert.match(els.vDrift.textContent, /cannot be detected/);
  assert.equal(els.send.disabled, false,
    'an unrecorded prior registration is unknown, not a reason to block a first send');
});

// ── it is reachable, and the browser can actually load what it calls ─────────

test('the page ships both scripts it depends on', () => {
  // #999: a guard that is present and never reached renders zero times while
  // every test above passes.
  assert.match(PAGE_HTML, /src="\/js\/keccak256\.js\?v=\d+"/,
    'the verifier never loads, so the hash check can never run in a browser');
  assert.match(PAGE_HTML, /src="\/js\/register-tool\.js\?v=\d+"/);
  for (const id of ['send', 'vHash', 'vCalldata', 'vReady', 'vDrift', 'why']) {
    assert.ok(PAGE_HTML.includes(`id="${id}"`), `the page has no #${id} to write to`);
  }
});

test('the button starts disabled in the markup, not only in script', () => {
  // If the script fails to load at all, the page must not offer a live button.
  const btn = PAGE_HTML.slice(PAGE_HTML.indexOf('id="send"'));
  assert.match(btn.slice(0, 60), /disabled/);
});

test('no inline script — the CSP hashes them and this page is not registered', () => {
  const inline = /<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/i.exec(PAGE_HTML);
  assert.equal(inline, null,
    'an inline script here would be blocked by script-src, and the failure '
    + 'mode is a dead page with a disabled button rather than a visible error');
});

test('one keccak implementation, shared — not a browser copy', () => {
  // The defect this file guards against, one level over: two implementations
  // of the same digest, drifting, indistinguishable from their output.
  const libSrc = fs.readFileSync(
    path.join(__dirname, '..', 'lib', 'keccak256.js'), 'utf8');
  assert.match(libSrc, /require\('\.\.\/public\/js\/keccak256\.js'\)/,
    'lib/keccak256.js no longer re-exports the shared module — if it grew its '
    + 'own copy, the server and the browser can now disagree about a hash');
  assert.equal(keccak.keccak256Utf8(''),
    '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470');
});

test('a failed RE-CHECK discards the plan it can no longer verify', async () => {
  // The bug the tautological version of the test above was hiding. `load()` is
  // also the Re-check button: a good load then a failed one used to leave
  // `plan` and `checksPassed` from the previous attempt, so Send stayed live
  // wired to data the page had just declared unreadable. The markup's
  // `disabled` attribute covers the FIRST load only.
  const dom = makeDom();
  let fail = false;
  const ctx = {
    document: dom.document, window: { ethereum: null }, Keccak256: keccak, console,
    fetch: async () => {
      if (fail) throw new Error('network down');
      return { ok: true, json: async () => goodPlan() };
    },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(PAGE_JS, ctx);
  await dom.document._fire();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(dom.els.send.disabled, false, 'first load should have enabled it');

  fail = true;
  await dom.els.reload._handlers.click();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(dom.els.send.disabled, true,
    'Send survived a failed re-check, still wired to the stale plan');
  assert.match(dom.els.why.textContent, /Nothing was checked/);
});
