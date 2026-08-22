/**
 * The live posture chip: three outcomes, and the third is the reason it exists.
 *
 * The site's central promise — "paper is the default and live trading stays off
 * until you switch it on" — is a claim about a RUNNING system baked into static
 * HTML. That is the same shape as `$72,669`, which sat on the old site as a
 * current price for three months. The chip asks the engine instead.
 *
 * Which makes its failure behaviour the whole design. Simulation on, live
 * armed, and NOBODY COULD TELL are three different facts, and the tempting
 * default — assume simulation when the read fails — would print the reassuring
 * answer at exactly the moment nothing was measured, on the claim that matters
 * most on the page.
 *
 * Every branch is driven here. `postureOf` and `renderPosture` are pure and
 * exported for that reason: a widget whose failure paths can only be reached by
 * unplugging a server is a widget whose failure paths are never tested.
 */

import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.join(HERE, '..')
const REPO = path.join(SITE, '..')

/**
 * Load live.ts through the REAL compiler.
 *
 * The first version stripped the types with a stack of regexes and died on
 * `postureOf(d: Health | null | undefined)` — `Unexpected token '|'`. Parsing a
 * language with regular expressions is the trap this repo has now hit from
 * both ends in one week: a scan that read a comment as code, and this, a scan
 * that read code as nothing.
 *
 * esbuild is already a vite dependency, so the module under test is compiled
 * exactly the way the shipped bundle compiles it. The DOM half is left in and
 * simply never called — there is no document here, and no test touches it.
 */
const { postureOf, renderPosture } = await (async () => {
  const esbuild = await import('esbuild')
  const src = fs.readFileSync(path.join(SITE, 'src', 'live.ts'), 'utf8')
  const { code } = esbuild.transformSync(src, { loader: 'ts', format: 'cjs' })
  const module = { exports: {} }
  new Function('module', 'exports', code)(module, module.exports)
  return module.exports
})()

// ── the three outcomes ────────────────────────────────────────────────────

test('a healthy engine in simulation reads as paper', () => {
  const p = postureOf({ engine: 'ready', simulation_mode: true, trading_blocked_by: '', trading_gate_unknown: false })
  assert.strictEqual(p.kind, 'paper')
  assert.strictEqual(renderPosture(p).tone, 'up')
  assert.match(renderPosture(p).text, /Simulation mode is ON/)
})

test('a healthy engine with simulation off reads as live, and is not green', () => {
  const p = postureOf({ engine: 'ready', simulation_mode: false, trading_blocked_by: '', trading_gate_unknown: false })
  assert.strictEqual(p.kind, 'live')
  const r = renderPosture(p)
  assert.strictEqual(r.tone, 'warn', 'live trading armed must not render as reassuring')
  assert.match(r.text, /live trading is armed/i)
})

test('every unreadable case is unknown, and none of them are green', () => {
  // The list is the point. Each of these is a way the read can fail, and every
  // one of them used to have an obvious wrong answer available.
  const cases = [
    [null, 'no answer at all'],
    [undefined, 'undefined body'],
    [{}, 'empty object'],
    [{ engine: 'absent' }, 'bridge up, engine missing'],
    [{ engine: 'ready' }, 'engine ready but simulation_mode omitted'],
    [{ engine: 'ready', simulation_mode: null }, 'null is not false'],
    [{ engine: 'ready', simulation_mode: 'true' }, 'a string is not a boolean'],
    [{ engine: 'ready', simulation_mode: 0 }, 'a falsy number is not false'],
  ]
  for (const [body, why] of cases) {
    const p = postureOf(body)
    assert.strictEqual(p.kind, 'unknown', `${why} was read as a posture`)
    const r = renderPosture(p)
    assert.strictEqual(r.tone, 'muted',
      `${why} rendered ${r.tone} — colour is a claim, and unknown gets a muted one`)
    assert.match(r.text, /unavailable/i)
  }
})

test('an omitted simulation_mode is never treated as live either', () => {
  // Symmetry matters: guessing "live" on a failed read is not the safe error,
  // it is a different false statement. The only honest answer is neither.
  const p = postureOf({ engine: 'ready' })
  assert.strictEqual(p.kind, 'unknown')
  assert.doesNotMatch(renderPosture(p).text, /simulation mode is/i)
})

// ── the gate, which fails separately ──────────────────────────────────────

test('an unread trade gate is stated, not rendered as all-clear', () => {
  // `trading_blocked_by: ''` means "nothing is blocking". `trading_gate_unknown`
  // means the gate could not be read at all. Passing the empty string through
  // in the second case turns an unread gate into an all-clear — the same defect
  // one field over, and api_bridge's own comments say it bit there first.
  const unread = postureOf({ engine: 'ready', simulation_mode: true, trading_blocked_by: '', trading_gate_unknown: true })
  assert.strictEqual(unread.gate, null)
  assert.match(renderPosture(unread).text, /gate could not be read/i)

  const clear = postureOf({ engine: 'ready', simulation_mode: true, trading_blocked_by: '', trading_gate_unknown: false })
  assert.strictEqual(clear.gate, '')
  assert.doesNotMatch(renderPosture(clear).text, /could not be read/i)
})

test('a named block is quoted back rather than summarised', () => {
  const p = postureOf({
    engine: 'ready', simulation_mode: false, trading_gate_unknown: false,
    trading_blocked_by: 'kill switch; venue auth halt',
  })
  assert.match(renderPosture(p).text, /kill switch; venue auth halt/)
})

// ── the wiring, which is what makes any of it reach a reader ──────────────

test('the widget reads /health, and reads it same-origin', () => {
  // NOT AN AESTHETIC CHOICE. api_bridge serves this site and /health on one
  // origin; the trading platform is a different origin and sets no CORS
  // headers, while api_bridge's own allow-list defaults to empty. A widget
  // pointed at the platform works in a dev tab and is blocked by the browser
  // in production — the worst place to find out.
  const src = fs.readFileSync(path.join(SITE, 'src', 'live.ts'), 'utf8')
  assert.match(src, /fetch\('\/health'/, 'the widget no longer reads /health')
  assert.doesNotMatch(src, /fetch\(\s*[`'"]https?:/,
    'the widget fetches an absolute URL — that is cross-origin and will be blocked')

  const express = fs.readFileSync(path.join(REPO, 'app', 'server.js'), 'utf8')
  assert.ok(!/Access-Control-Allow-Origin/.test(express),
    'app/server.js now sets CORS — if the platform is reachable cross-origin, '
    + 'this widget could read richer data and this constraint should be revisited')
})

test('a non-200 is a failed read, not an absent engine', () => {
  const src = fs.readFileSync(path.join(SITE, 'src', 'live.ts'), 'utf8')
  assert.match(src, /r\.ok \?/, 'the response status is no longer checked')
  assert.match(src, /HTTP \$\{r\.status\}/,
    'a non-200 must say which, so a 503 and a 404 are distinguishable')
})

test('the chip is wired into the entry point and is opt-in per page', () => {
  // #999: a card that is built and never reached renders zero times, and no
  // source scan tells that apart from one that works.
  const main = fs.readFileSync(path.join(SITE, 'src', 'main.tsx'), 'utf8')
  assert.match(main, /wireLivePosture\(\)/, 'the widget is never called')
  const live = fs.readFileSync(path.join(SITE, 'src', 'live.ts'), 'utf8')
  assert.match(live, /getElementById\('live-posture'\)/)
  assert.match(live, /if \(!host\) return/,
    'a page without the placeholder must not pay for a fetch')
})

test('the built page ships the placeholder, hidden, with a no-JS note', () => {
  const proof = path.join(REPO, 'website', 'proof', 'index.html')
  assert.ok(fs.existsSync(proof), 'the proof page is not built — run npm run build')
  const html = fs.readFileSync(proof, 'utf8')
  assert.match(html, /id="live-posture"/, 'the placeholder never reached the page')
  assert.match(html, /opacity-0/,
    'the chip starts visible, so a reader with JS off sees "Reading the engine…" forever')
  assert.match(html, /<noscript>/, 'no-JS readers are not told why the chip is absent')
})
