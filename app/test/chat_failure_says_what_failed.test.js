'use strict';
/**
 * A turn that produced no answer says WHY, and a refusal offers no Retry.
 *
 * `app/lib/gateway.js` writes `event: error` into the stream with the reason
 * it knows — "Timed out waiting for the bot", "Chat unavailable", "Bot
 * gateway error". The drawer's reader passed that frame to `onStreamEvent`,
 * which handles delta/attempt/tool and drops everything else; the turn then
 * ended with no `final`, took a synthetic 502, and the send path's 502 branch
 * printed:
 *
 *     "Chat isn't connected on this deployment yet — the operator is being
 *      notified. Please check back soon."
 *
 * A PAIRING DIAGNOSIS, manufactured from a timeout, on the one surface that
 * had been told the actual cause. Beside it, `!r.ok` printed the server's raw
 * code — "Error: skill_not_web_enabled" — under a Retry button that could
 * only ever earn the same line again.
 *
 * `chatFailure(r)` is the decision, pure, so it can be driven here: the two
 * 502s are told apart by `streamed`, a refusal is a sentence with no Retry,
 * and a rate limit keeps its cooldown.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
// STRIP THE COMMENTS BEFORE SCANNING, and this file is the sixth time that
// rule has had to be learned. The first draft asserted `/streamed: true/`
// against the raw source, and the comment two lines above the return QUOTES
// the flag it explains — so a mutation deleting the flag from the code left
// the assertion passing on the prose. `tests/source_scan.py` says this for
// Python; `app/test/helpers/code_only.js` is the same thing for JS and was
// already in the tree.
const CODE = codeOnly(SRC);

// Slice the two blocks out of the drawer's IIFE and run them alone: the
// refusal vocabulary and the decision that reads it.
function load() {
  const start = SRC.indexOf('  function chatFailure(r) {');
  const endRef = SRC.indexOf('  const REFUSALS = {');
  const refEnd = SRC.indexOf('  };', endRef) + 4;
  assert.ok(start > 0 && endRef > start, 'chatFailure and REFUSALS are both present');
  const fnEnd = SRC.indexOf('\n  }\n', start) + 4;
  // The BODIES are run, comments and all — only the scans below read `CODE`.
  const code = SRC.slice(endRef, refEnd) + '\n' + SRC.slice(start, fnEnd) + '\nchatFailure;';
  const ctx = { esc: (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) };
  vm.createContext(ctx);
  return vm.runInContext(code, ctx);
}

const chatFailure = load();

test('an answer is not a failure', () => {
  assert.equal(chatFailure({ ok: true, status: 200, data: { reply_html: 'hi' } }), null);
});

test('a mid-stream failure says what the SERVER said, not what the deployment is', () => {
  for (const reason of ['Timed out waiting for the bot', 'Chat unavailable', 'Bot gateway error']) {
    const f = chatFailure({ ok: false, status: 502, streamed: true, data: { error: reason } });
    assert.equal(f.kind, 'stream', reason);
    assert.equal(f.text, reason);
    assert.ok(!/connected on this deployment/.test(f.text), 'never the pairing diagnosis');
    assert.equal(f.retry, true, 'a timeout is exactly the case where retrying can work');
  }
});

test('a stream that ends with no answer and no reason says that, and no more', () => {
  const f = chatFailure({ ok: false, status: 502, streamed: true, data: { error: 'The stream ended before an answer arrived.' } });
  assert.equal(f.text, 'The stream ended before an answer arrived.');
  assert.ok(!/operator/.test(f.text));
});

test('an unpaired deployment IS diagnosed — that branch was right, for its own case', () => {
  for (const r of [{ ok: false, status: 502, data: {} },
                   { ok: false, status: 200, data: { error: 'gateway_disabled' } }]) {
    const f = chatFailure(r);
    assert.equal(f.kind, 'gateway');
    assert.match(f.text, /connected on this deployment/);
    assert.equal(f.retry, false, 'the visitor retrying cannot pair two deployments');
  }
});

test('a refusal is a sentence, never a raw code, and never offers Retry', () => {
  for (const code of ['forbidden', 'skill_not_web_enabled', 'live_not_enabled']) {
    const f = chatFailure({ ok: false, status: 403, data: { error: code } });
    assert.equal(f.kind, 'refusal', code);
    assert.equal(f.retry, false, code);
    assert.ok(!f.text.includes(code), `the code itself is never printed: ${f.text}`);
    assert.ok(/[a-z] [a-z]/.test(f.text), 'it is a sentence');
  }
});

test('an error nobody has a sentence for keeps its detail and its Retry', () => {
  const f = chatFailure({ ok: false, status: 500, data: { detail: 'upstream exploded' } });
  assert.equal(f.kind, 'error');
  assert.match(f.text, /upstream exploded/);
  assert.equal(f.retry, true);
});

test('the detail is escaped — it comes from a server, into innerHTML', () => {
  const f = chatFailure({ ok: false, status: 500, data: { detail: '<img src=x onerror=1>' } });
  assert.ok(!/<img/.test(f.text), f.text);
});

test('a rate limit keeps its countdown', () => {
  const f = chatFailure({ ok: false, status: 429, data: {} });
  assert.equal(f.kind, 'rate');
  assert.equal(f.retry, true);
  assert.equal(f.cooldown, 5000);
});

test('a 503 is the deployment, and offers no Retry', () => {
  const f = chatFailure({ ok: false, status: 503, data: {} });
  assert.equal(f.kind, 'unavailable');
  assert.equal(f.retry, false);
});

test('no response at all is a network error, and retryable', () => {
  const f = chatFailure(null);
  assert.equal(f.kind, 'error');
  assert.equal(f.retry, true);
});

// ── the reader that latches it ─────────────────────────────────────────────

test('the stream reader latches the error frame and hands the reason back', () => {
  assert.match(CODE, /else if \(event === 'error'\) streamError = \(data && data\.error\) \|\| null;/,
    'the `error` frame is read, not passed to the delta/tool handler');
  const tail = CODE.slice(CODE.indexOf('if (!final) {'));
  assert.match(tail.slice(0, 400), /streamed: true/, 'and the turn is marked as the STREAM\'s failure');
  assert.match(tail.slice(0, 400), /streamError \|\|/, 'with the server\'s reason preferred');
});

test('the send path renders through the one decision', () => {
  const send = CODE.slice(CODE.indexOf('async function send(retryText)'));
  assert.match(send, /const failure = chatFailure\(r\);/);
  assert.match(send, /if \(failure\.retry\) appendFailure\(failure\.text, text, failure\.cooldown\);/);
  assert.match(send, /else appendMsg\('bot', failure\.text\);/);
});
