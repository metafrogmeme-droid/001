'use strict';
// A panel must not abandon a request its own loader asked to wait longer for.
//
// renderPanel(el, loader, opts) runs its own timer on opts.timeoutMs, default
// 8000 (app.js). If the loader inside asks fetchJSON for MORE than that, the
// panel gives up first and paints "Couldn't load this panel" while the request
// is still in flight — a failure the user is shown for work that was going to
// succeed. The author wrote 14000 and silently got 8000.
//
// This is the same shape as the Arena bug: a client budget shorter than the
// work behind it, where an abort is indistinguishable from a real error.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const APP = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function panelDefault() {
  const m = APP.match(/const \{ timeoutMs = (\d+), empty = \{\} \} = opts;/);
  assert.ok(m, 'renderPanel declares a default timeout');
  return Number(m[1]);
}

// Slice each renderPanel(...) call: its loader body plus its opts object.
function panels(src) {
  const out = [];
  const re = /renderPanel\(([^,]+),\s*async \(\) => \{/g;
  let m;
  while ((m = re.exec(src))) {
    const start = m.index;
    // Walk to the matching close of the renderPanel( call.
    let depth = 0, i = src.indexOf('(', start);
    for (; i < src.length; i++) {
      if (src[i] === '(') depth++;
      else if (src[i] === ')') { depth--; if (depth === 0) break; }
    }
    out.push({ target: m[1].trim(), body: src.slice(start, i + 1), line: src.slice(0, start).split('\n').length });
  }
  return out;
}

test('no panel gives up sooner than the fetch inside it asks for', () => {
  const dflt = panelDefault();
  const offenders = [];
  for (const p of panels(DASH)) {
    // The panel's own budget: explicit opts.timeoutMs, else the default.
    const own = p.body.match(/\}, \{[^}]*timeoutMs: (\d+)/);
    const budget = own ? Number(own[1]) : dflt;
    // Every fetch budget requested inside the loader.
    for (const f of p.body.matchAll(/fetchJSON\([^)]*timeoutMs: (\d+)/g)) {
      const want = Number(f[1]);
      if (want > budget) {
        offenders.push(`dashboard.js:${p.line} ${p.target} — loader asks ${want}ms, panel aborts at ${budget}ms`);
      }
    }
  }
  assert.deepStrictEqual(offenders, [],
    'these panels abort a request they asked to wait longer for:\n  ' + offenders.join('\n  '));
});

test('the readiness score bounds each axis instead of one slow axis costing all of them', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'guardian_readiness.js'), 'utf8');
  // buildHoldings alone allowed its upstream 35s (lib/holdings.js) behind a
  // client that waits 8-14s. Each nullable axis now carries its own deadline.
  assert.match(src, /withDeadline\(/, 'the handler bounds its slow axes');
  const bounded = (src.match(/withDeadline\(/g) || []).length;
  assert.ok(bounded >= 3, `expected the three upstream axes bounded, found ${bounded}`);
  const m = src.match(/const AXIS_MS = (\d+);/);
  assert.ok(m && Number(m[1]) <= 3000, 'the per-axis budget leaves room for the rest of the handler');
});

test('withDeadline degrades, and never leaks an unhandled rejection', async () => {
  const { withDeadline } = require('../lib/deadline');
  assert.equal(await withDeadline(new Promise(() => {}), 40, 'fallback'), 'fallback',
    'a hang resolves to the fallback');
  assert.equal(await withDeadline(Promise.resolve('v'), 500), 'v', 'a fast result passes through');
  assert.equal(await withDeadline(Promise.reject(new Error('x')), 500, null), null,
    'a rejection degrades rather than throwing');
  // A rejection that lands AFTER the deadline must not crash the process.
  const late = new Promise((_, rej) => setTimeout(() => rej(new Error('late')), 30));
  assert.equal(await withDeadline(late, 5, 'fb'), 'fb');
  await new Promise((r) => setTimeout(r, 60));   // let the late rejection land
});
