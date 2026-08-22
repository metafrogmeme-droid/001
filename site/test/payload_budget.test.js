/**
 * What a visitor downloads, and what merely sits in the publish.
 *
 * THE PREMISE OF THE "MEDIA DIET" TURNED OUT TO BE WRONG, AND THAT IS WORTH
 * WRITING DOWN RATHER THAN QUIETLY FIXING. `website/` is 29 MB, which reads
 * like a page-weight problem. It is not one: the built pages load in ~58 KB.
 * The site is a fresh build that references six files, the video is
 * `preload="metadata"` so none of it is fetched until somebody presses play,
 * and 20.5 MB of the directory is referenced by NOTHING — not by the built
 * output, not by `site/src`, not by `app/`, not by the docs, not by the README.
 *
 * So there are two different numbers and they answer different questions:
 *
 *   EAGER PAYLOAD  — what a first-time visitor pays. Small, and pinned below.
 *   PUBLISH WEIGHT — what has to be transferred to put the site live. Large,
 *                    and the reason a publish tarball is 17 MB.
 *
 * Conflating them would have produced a compression exercise that made the
 * page no faster, on files nobody fetches. This file measures both separately
 * and says which is which.
 *
 * WHY THE UNREFERENCED SET IS A BASELINE AND NOT A DELETION. Those files have
 * PUBLIC URLs. A tweet, a pitch deck, a GitHub README on another branch, a
 * Telegram post — none of that is visible from inside this repo, so "nothing
 * here links to it" is not "nothing links to it". Deleting them is the
 * operator's call, and the honest thing a test can do is stop the pile from
 * growing while naming exactly what is in it.
 */

import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.join(HERE, '..')
const REPO = path.join(SITE, '..')
const OUT = path.join(REPO, 'website')
const BASELINE = path.join(HERE, 'unreferenced_assets.txt')

const ASSET = /\.(png|jpe?g|webm|mp4|gif|webp|avif)$/i
const SKIP_DIR = /^(node_modules|\.git|target|dist|\.ssr)$/
const TEXT = /\.(html|css|js|mjs|ts|tsx|jsx|xml|txt|json|md|py|yml|yaml|sh|rs|sol)$/i

function walk(dir, pick, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIR.test(e.name)) continue
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p, pick, out)
    else if (pick.test(e.name)) out.push(p)
  }
  return out
}

/** Every asset under website/, relative, sorted. */
const assets = () =>
  walk(OUT, ASSET).map((p) => path.relative(REPO, p)).sort()

/**
 * Basenames mentioned anywhere in the repo's text.
 *
 * REPO-WIDE, and the first version was not. It read `website/` and `site/src`
 * only, which is the blind spot CLAUDE.md names outright: a reachability
 * checker that cannot see every caller manufactures exactly the accusation it
 * exists to prevent. Scanning the whole tree moved 0.4 MB out of the accused
 * pile — small, but it would have been four files deleted for no reason.
 */
function mentioned() {
  const hay = walk(REPO, TEXT)
    // THE BASELINE ITSELF IS EXCLUDED, and leaving it in was self-defeating in
    // the most literal way: it lists the filenames, it is a .txt, so every file
    // named in it counted as "referenced" and the ratchet immediately reported
    // its own contents as stale. A guard whose record of what it found is part
    // of what it searches can only ever find nothing.
    .filter((p) => p !== BASELINE)
    .map((p) => { try { return fs.readFileSync(p, 'utf8') } catch { return '' } })
    .join('\n')
  return (name) => hay.includes(name)
}

// ── the number a visitor actually pays ────────────────────────────────────

/** Bytes fetched eagerly for one built page: the html plus what it pulls in. */
function eagerBytes(page) {
  const html = fs.readFileSync(page, 'utf8')
  let total = Buffer.byteLength(html)
  const seen = new Set()
  for (const m of html.matchAll(/(?:src|href)="(\/[^"]+\.(?:css|js|png|jpe?g|webp|avif|svg|gif))"/g)) {
    if (seen.has(m[1])) continue
    seen.add(m[1])
    const f = path.join(OUT, m[1])
    if (fs.existsSync(f)) total += fs.statSync(f).size
  }
  return total
}

test('every built page loads well under budget', () => {
  const pages = walk(OUT, /^index\.html$/)
  assert.ok(pages.length >= 2, `only ${pages.length} built pages — run npm run build`)
  for (const p of pages) {
    const kb = eagerBytes(p) / 1024
    assert.ok(kb < 150,
      `${path.relative(OUT, p)} loads ${kb.toFixed(0)} KB eagerly (budget 150 KB)`)
  }
})

test('the video costs a visitor nothing until they ask for it', () => {
  // 7.5 MB of recordings sit in the publish, and the only thing that keeps
  // them off the critical path is this attribute. `preload="auto"` — or no
  // preload at all on some browsers — turns a 58 KB page into a multi-megabyte
  // one with no visible change to the markup.
  const html = fs.readFileSync(path.join(OUT, 'index.html'), 'utf8')
  const video = html.match(/<video[^>]*>/)
  assert.ok(video, 'the demo video is gone from the landing page')
  assert.match(video[0], /preload="metadata"/,
    'the demo video would now download on page load')
})

test('the smaller source is offered first', () => {
  // Browsers take the FIRST <source> they can play, and h.264 is universal, so
  // whichever mp4/webm comes first is the one essentially everybody gets. The
  // webm here is 2.6x LARGER than the mp4 (5.4 MB vs 2.1 MB), which is the
  // reverse of the usual reason to ship webm — so the order is load-bearing
  // rather than cosmetic, and flipping it would hand every visitor who plays
  // the demo an extra 3.3 MB.
  const html = fs.readFileSync(path.join(OUT, 'index.html'), 'utf8')
  const sources = [...html.matchAll(/<source src="([^"]+)"/g)].map((m) => m[1])
  assert.ok(sources.length >= 2, 'the demo no longer offers multiple formats')
  const size = (u) => fs.statSync(path.join(OUT, u.replace(/^\//, ''))).size
  const first = size(sources[0])
  for (const s of sources.slice(1)) {
    assert.ok(first <= size(s),
      `${sources[0]} (${(first / 1048576).toFixed(1)} MB) is offered before `
      + `${s} (${(size(s) / 1048576).toFixed(1)} MB) — the first playable source wins`)
  }
})

// ── the pile that does not reach a visitor at all ─────────────────────────

test('no NEW unreferenced asset joins the publish', () => {
  // A RATCHET, in both directions, like tests/unreachable_baseline.txt and
  // tests/known_failures.txt. A new entry means somebody added a megabyte
  // nothing links to; an entry that leaves must be deleted from the baseline
  // in the same commit, so the file cannot quietly describe a past that is no
  // longer true.
  const has = mentioned()
  const unref = assets().filter((a) => !has(path.basename(a)))
  const baseline = fs.readFileSync(BASELINE, 'utf8')
    .split('\n').map((l) => l.replace(/#.*/, '').trim()).filter(Boolean).sort()

  const added = unref.filter((a) => !baseline.includes(a))
  assert.deepStrictEqual(added, [],
    'these are published and nothing in the repo references them. If they are '
    + 'genuinely wanted, add them to site/test/unreferenced_assets.txt with a '
    + 'reason; if not, delete them.')

  const stale = baseline.filter((b) => !unref.includes(b))
  assert.deepStrictEqual(stale, [],
    'the baseline names files that are now referenced or gone — delete those '
    + 'lines in the same commit, or the list stops describing anything')
})

test('the baseline is a measurement, not a number typed into prose', () => {
  // The docstring above quotes 20.5 MB and ~58 KB. Both are pinned here, so a
  // change that moves either fails rather than leaving this file describing a
  // site that no longer exists — the defect the whole `site/test` suite was
  // written against.
  const baseline = fs.readFileSync(BASELINE, 'utf8')
    .split('\n').map((l) => l.replace(/#.*/, '').trim()).filter(Boolean)
  const mb = baseline.reduce((n, f) => n + fs.statSync(path.join(REPO, f)).size, 0) / 1048576
  assert.ok(mb > 19 && mb < 22,
    `the unreferenced pile is ${mb.toFixed(1)} MB; this file's prose says 20.5 MB`)

  const kb = eagerBytes(path.join(OUT, 'index.html')) / 1024
  assert.ok(kb > 40 && kb < 80,
    `the landing page loads ${kb.toFixed(0)} KB; this file's prose says ~58 KB`)
})
