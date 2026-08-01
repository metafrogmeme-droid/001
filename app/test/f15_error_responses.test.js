'use strict';
/**
 * F-15 on the website: an error body a stranger may read.
 *
 * The bot enforces this with `_safe_exc_text`, and #1038/#1039 found thirteen
 * places still sending raw exceptions to Telegram users. The website had no
 * equivalent rule and no check, and one route was doing the same thing on a
 * PUBLIC, unauthenticated endpoint:
 *
 *     POST /api/tool/invoke        (routes/tool8257.js)
 *     error: `Tool failed: ${String(e.message || e).slice(0, 200)}`
 *
 * That handler runs MCP tools, which do `pool.execute` and `getGateway`
 * calls. A database error carries connection and schema detail; a gateway
 * error carries the internal URL it tried. Slicing to 200 characters bounds
 * the SIZE of a leak, not whether one happens.
 *
 *     ONE IN 657 IS STILL ONE, AND IT WAS ON THE UNAUTHENTICATED ONE.
 *
 * WHY THIS WALKS TO THE BALANCED PAREN
 *
 * The offending line sits three lines below its `res.status(502).json({`, so
 * a single-line scan finds nothing and reports the file clean — the same
 * shape that nearly made the bot-side guard toothless.
 *
 * And it counts lines on the ORIGINAL source. The first version of this scan
 * stripped comments with a plain replace, which deleted the newlines inside
 * block comments and shifted every line number after them; it pointed at
 * tool8257.js:45 for a leak that lives at :57.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROUTES = path.join(__dirname, '..', 'routes');
const { safeErrorText } = require('../lib/safe_error');

const SEND = /res\.(?:status\(\s*\d+\s*\)\.)?(?:json|send)\s*\(/g;
const LEAK = /\b(?:err|e|error|exc)\.(?:message|stack)\b|String\(\s*(?:err|e|error)\s*\)/;

const { codeOnly } = require('./helpers/code_only');

function responseCalls(src) {
  const out = [];
  SEND.lastIndex = 0;
  let m;
  while ((m = SEND.exec(src)) !== null) {
    let depth = 1;
    let j = m.index + m[0].length;
    while (j < src.length && depth) {
      if (src[j] === '(') depth += 1;
      else if (src[j] === ')') depth -= 1;
      j += 1;
    }
    out.push({
      line: src.slice(0, m.index).split('\n').length,
      text: src.slice(m.index, j),
    });
  }
  return out;
}

/**
 * Every server file that can write a response — NOT just app/routes.
 *
 * The first version scanned app/routes only, which is the same "covered the
 * files someone thought of" shape this guard exists to close: server.js,
 * auth.js, proxy.js and app/lib all send responses too, and 144 response
 * calls live outside app/routes. They are clean today; nothing was checking
 * them, so a future leak there would have passed.
 *
 * public/ is excluded deliberately — it is browser code, where `res` is not
 * an Express response and a match would be a false positive.
 */
function serverFiles() {
  const out = [];
  const APP = path.join(__dirname, '..');
  for (const f of fs.readdirSync(APP)) {
    if (f.endsWith('.js')) out.push(path.join(APP, f));
  }
  for (const dir of ['routes', 'lib']) {
    const d = path.join(APP, dir);
    if (!fs.existsSync(d)) continue;
    for (const f of fs.readdirSync(d)) {
      if (f.endsWith('.js')) out.push(path.join(d, f));
    }
  }
  return out;
}

function rel(p) {
  return path.relative(path.join(__dirname, '..'), p);
}

test('the scan reaches a real number of response calls', () => {
  // A check that inspects nothing passes everything.
  let n = 0;
  for (const f of serverFiles()) {
    n += responseCalls(codeOnly(fs.readFileSync(f, 'utf8'))).length;
  }
  assert.ok(n > 700, `only ${n} res.json/send calls found — scan is broken`);
});

test('the scan spans multiple lines', () => {
  // The offending site sat three lines below its own res.status(502).json({.
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'tool8257.js'), 'utf8'));
  assert.ok(responseCalls(src).some((c) => c.text.includes('\n')));
});

test('no route returns a raw exception to a caller', () => {
  const offenders = [];
  for (const f of serverFiles()) {
    const src = codeOnly(fs.readFileSync(f, 'utf8'));
    for (const c of responseCalls(src)) {
      if (LEAK.test(c.text)) offenders.push(`${rel(f)}:${c.line}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'F-15: these responses put a raw error in front of a caller — use '
    + `safeErrorText(e). Sites:\n  ${offenders.join('\n  ')}`);
});

test('console.error is not flagged — only responses', () => {
  // Server logs are not user-facing. Flagging them would produce noise that
  // gets silenced by exemptions until the check protects nothing.
  const src = 'console.error("x", e.stack || e.message);';
  assert.equal(responseCalls(src).length, 0);
});

test('the line numbers are the real ones', () => {
  // The first version shifted them by deleting newlines inside block
  // comments, and pointed at :45 for a leak that lives at :57.
  const raw = fs.readFileSync(path.join(ROUTES, 'tool8257.js'), 'utf8');
  assert.equal(codeOnly(raw).split('\n').length, raw.split('\n').length);
});

test('the pattern can fail', () => {
  assert.ok(LEAK.test('res.json({ error: e.message })'));
  assert.ok(LEAK.test('res.json({ error: err.stack })'));
  assert.ok(LEAK.test('res.json({ error: String(e) })'));
  assert.ok(!LEAK.test('res.json({ error: safeErrorText(e) })'));
  assert.ok(!LEAK.test('res.json({ error: "Tool failed" })'));
});

test('tool invoke specifically is scrubbed', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROUTES, 'tool8257.js'), 'utf8'));
  const i = src.indexOf('Tool failed');
  assert.ok(i > 0);
  assert.ok(src.slice(i - 100, i + 100).includes('safeErrorText('));
});

test('the scrubber removes secrets and keeps the diagnostic', () => {
  const out = safeErrorText(new Error(
    'connect failed apiKey=SECRET123 at https://internal.svc/q?token=abc'));
  assert.ok(!out.includes('SECRET123'));
  assert.ok(!out.includes('abc'));
  assert.ok(out.includes('REDACTED'));
  assert.ok(out.includes('internal.svc'), 'which host is the diagnostic');
});

test('it redacts a connection string whole', () => {
  const out = safeErrorText(new Error('mysql://user:pw@db.internal:3306/runeclaw down'));
  assert.ok(!out.includes('pw@'));
  assert.ok(out.includes('REDACTED'));
});

test('it hides absolute deployment paths', () => {
  const out = safeErrorText(new Error('ENOENT /home/mulerun/.env missing'));
  assert.ok(!out.includes('/home/mulerun'));
});

test('it redacts BEFORE truncating', () => {
  // A 200-char cut through a secret would leave the first half in place.
  const pad = 'x'.repeat(190);
  const out = safeErrorText(new Error(`${pad} apiKey=SUPERSECRETVALUE`));
  assert.ok(!out.includes('SUPERSECRET'));
});

test('it keeps a genuine validation message', () => {
  // Tools throw errors a caller needs to read. Blanking them would trade a
  // leak for a support burden.
  assert.equal(safeErrorText(new Error('text is required')), 'text is required');
});

test('it never throws', () => {
  const hostile = { get message() { throw new Error('nope'); } };
  assert.equal(safeErrorText(hostile), '');
  assert.equal(safeErrorText(null), '');
  assert.equal(safeErrorText(undefined), '');
});

test('the scan reaches beyond app/routes', () => {
  // It covered app/routes only — the same "files someone thought of" shape
  // this guard closes. server.js, auth.js, proxy.js and app/lib send
  // responses too; 144 response calls live outside app/routes.
  const outside = serverFiles().filter((f) => !f.includes(`${path.sep}routes${path.sep}`));
  assert.ok(outside.length >= 3, 'the widening did not take');
  const names = outside.map((f) => path.basename(f));
  assert.ok(names.includes('server.js'), 'server.js writes responses');
});

test('browser code is not scanned', () => {
  // app/public is client-side; `res` there is not an Express response and a
  // match would be a false positive that gets silenced by an exemption.
  assert.ok(!serverFiles().some((f) => f.includes(`${path.sep}public${path.sep}`)));
});

test('an unclosed comment marker in a string does not blank the file', () => {
  // THE #1040 BUG, reproduced exactly.
  //
  // A first draft of this test used `'/**/*.js'` — self-closing, so the old
  // scanner handled it and the test passed against the very code it was
  // meant to indict. The damage needs an UNMATCHED `/*` in one string
  // closing against a `*/` somewhere later: everything between them,
  // including real response calls, was blanked.
  //
  // Verified directly: the #1040 scanner loses the call below; this one
  // keeps it. That is why server.js retained 32% and reported clean.
  const src = [
    "const open = '/*';",
    'res.status(500).json({ error: err.message });',
    "const close = '*/';",
  ].join('\n');
  const calls = responseCalls(codeOnly(src));
  assert.equal(calls.length, 1, 'the response call was swallowed');
  assert.ok(LEAK.test(calls[0].text));
});
test('a real block comment is still blanked', () => {
  const src = '/* res.json({ error: e.message }) */\nres.json({ ok: true });';
  const calls = responseCalls(codeOnly(src));
  assert.equal(calls.length, 1, 'commented-out code must not be flagged');
  assert.ok(!LEAK.test(calls[0].text));
});

// A retained-density FLOOR was tried here and removed. It cannot do the job:
// the broken scanner kept 32% of server.js, and lib/arena_discipline.js —
// which is 52% comment LINES and perfectly fine — retains 37%. The two
// numbers are not separable by a threshold, and tuning one until it passed
// would be silencing a check rather than fixing it.
//
// The two tests above test the actual defect instead: a comment marker
// inside a string must not blank the file, and a real block comment must
// still be blanked. Both were verified to fail against the #1040 scanner.
