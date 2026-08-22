
/**
 * The marketing site cannot say things the product does not back.
 *
 * WHAT WENT WRONG BEFORE. The site this replaces was three hand-written HTML
 * files that disagreed with each other and with the code:
 *
 *   - `submission.html` said the engine ran 19 pre-trade checks. README.md says
 *     23, in three places, with a breakdown. Nobody lied; the number was typed
 *     into markup in two files at two times, and markup cannot notice that it
 *     disagrees with itself.
 *   - `$72,669` sat on a page as a current BTC price from June 2026 onward. A
 *     price is true for seconds. A static build is true for weeks.
 *   - `og:image` was a RELATIVE url, so every social scraper resolved it
 *     against its own origin and every share of the site rendered blank. That
 *     defect is invisible in the markup, in review, and in a browser — it is
 *     only visible in the one place it is used.
 *   - `index.html` carried a comment saying its tokens mirrored
 *     `app/public/styles.css`. Every token matched except the one that defines
 *     the brand: `--gold` was `#cbb06a` on the marketing page and `#3fb6ff`
 *     (electric rune-blue) on the platform. Ninety percent right is what let it
 *     survive for a year — nobody scrolls a palette hunting the one hex that
 *     drifted, and a comment asserting the mirror is what stops them looking.
 *
 * Every test here pins one of those, as a property of the BUILT OUTPUT rather
 * than of the source, because the built output is what a visitor gets.
 *
 * Run against `website/` after `npm run build`.
 */

// ESM, not CommonJS: site/package.json is `"type": "module"`, so a `.js` file
// here has no `require` and no `__dirname`. The rest of the repo's suites are
// CommonJS because their packages are.
import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.join(HERE, '..')
const REPO = path.join(SITE, '..')
const OUT = path.join(REPO, 'website')

/** Pages this build owns. The archive is deliberately NOT one of them. */
const PAGES = ['index.html', path.join('privacy', 'index.html')]

function built(rel) {
  const p = path.join(OUT, rel)
  if (!fs.existsSync(p)) {
    throw new Error(`${rel} is not built. Run \`npm run build\` in site/ first.`)
  }
  return fs.readFileSync(p, 'utf8')
}

// ── the brand cannot drift from the platform ──────────────────────────────

test('the committed tokens are what the platform actually declares', () => {
  // A ratchet, not a mirror-check: regenerate and compare. A stale committed
  // tokens.css is the exact failure mode the generator exists to remove, and
  // it would otherwise survive until someone happened to look at a colour.
  const before = fs.readFileSync(path.join(SITE, 'src', 'tokens.css'), 'utf8')
  execFileSync(process.execPath, [path.join(SITE, 'scripts', 'tokens.mjs')],
    { stdio: 'pipe' })
  const after = fs.readFileSync(path.join(SITE, 'src', 'tokens.css'), 'utf8')
  assert.strictEqual(before, after,
    'site/src/tokens.css is stale — the platform stylesheet moved. Rebuild and '
    + 'commit the regenerated file in the same change.')
})

test('the accent is the platform accent, and it is blue', () => {
  const platform = fs.readFileSync(
    path.join(REPO, 'app', 'public', 'styles.css'), 'utf8')
  const tokens = fs.readFileSync(path.join(SITE, 'src', 'tokens.css'), 'utf8')
  const grab = (src) => {
    // Comments stripped first: the platform's :root discusses --gold by name,
    // and its file header says "no page may re-declare its own :root ramp" —
    // which is a sentence containing ":root" that broke the first version of
    // the generator. CLAUDE.md's oldest source-scanning rule.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, ' ')
    const m = code.match(/--gold:\s*([^;]+);/)
    return m ? m[1].trim() : null
  }
  const a = grab(platform)
  const b = grab(tokens)
  assert.ok(a, 'the platform no longer declares --gold')
  assert.strictEqual(b, a, `accent drifted: site ${b} vs platform ${a}`)
  assert.notStrictEqual(a, '#cbb06a',
    'the accent is back to the old marketing gold — the platform is blue and '
    + '"keep the blue" is the standing call')
})

// ── a share link that renders blank is the defect nobody sees ─────────────

for (const page of PAGES) {
  test(`${page}: every share url is absolute`, () => {
    const html = built(page)
    const urls = [...html.matchAll(
      /<meta[^>]+(?:property="og:(?:image|url)"|name="twitter:image")[^>]*content="([^"]*)"/g)]
      .map((m) => m[1])
    assert.ok(urls.length >= 2, 'the page should carry og:image and og:url')
    for (const u of urls) {
      assert.match(u, /^https?:\/\//,
        `"${u}" is relative — a social scraper resolves that against its own `
        + 'origin and renders nothing')
    }
  })

  test(`${page}: is real HTML, not a shell`, () => {
    // The #999 shape at page scale: markup being PRESENT and markup being
    // RENDERED are different facts, and a client-rendered SPA ships an empty
    // div to every crawler while looking perfect in a browser.
    const html = built(page)
    // Ends at </body>, not at a following <script>: vite hoists the module
    // script into <head>, so the first draft's `</div>\s*<script` anchor never
    // matched and this test failed on correct output.
    const m = html.match(/<div id="root">([\s\S]*)<\/div>\s*<\/body>/)
    assert.ok(m, 'no #root content block found')
    const text = m[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
    assert.ok(text.length > 400,
      `only ${text.length} chars of visible text — a crawler and a first paint `
      + 'both see this, and a prerender that silently produced nothing would '
      + 'still deploy cleanly')
    assert.match(html, /<h1[\s>]/, 'a page with no h1 has no subject')
  })

  test(`${page}: every local reference resolves to a file`, () => {
    const html = built(page)
    const refs = [...html.matchAll(/(?:href|src)="(\/[^"]*)"/g)]
      .map((m) => m[1])
      .filter((u) => !u.startsWith('//'))
    const missing = [...new Set(refs)].filter(
      (u) => !fs.existsSync(path.join(OUT, decodeURIComponent(u))))
    assert.deepStrictEqual(missing, [],
      `these link to nothing:\n  ${missing.join('\n  ')}\n`
      + 'A nav is a promise about what is behind it.')
  })

  test(`${page}: no market price is baked into the build`, () => {
    // The $72,669 ratchet. Any currency-formatted figure with thousands is a
    // price or a balance, and neither is true for as long as a static build
    // lives. Market data belongs on the platform, read live.
    const html = built(page)
    const hits = [...html.matchAll(/\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?/g)].map((m) => m[0])
    assert.deepStrictEqual(hits, [],
      `these read as live figures on a page rebuilt weekly: ${hits.join(', ')}`)
  })

  test(`${page}: loads no third-party script`, () => {
    const html = built(page)
    const ext = [...html.matchAll(/<script[^>]+src="(https?:\/\/[^"]+)"/g)].map((m) => m[1])
    assert.deepStrictEqual(ext, [],
      `CDN scripts are back: ${ext.join(', ')} — the old site pulled Tailwind, `
      + 'GSAP and ECharts at runtime with no SRI')
  })
}

// ── the archive is an archive ─────────────────────────────────────────────

test('the hackathon page is frozen, labelled, and out of the index', () => {
  const html = built(path.join('archive', 'hackathon', 'index.html'))
  assert.match(html, /name="robots"[^>]+noindex/,
    'stale figures competing with current ones in search results is the same '
    + 'defect as printing them on the live page')
  assert.match(html, /Archived/,
    'an archive that does not say it is an archive is just an out-of-date page')
  assert.match(html, /not current/i, 'it must say the figures are not current')
})

test('the archive keeps its numbers rather than being quietly corrected', () => {
  // The brief asked for 19 -> 23. Refused on purpose: this is a record of what
  // was submitted in June 2026, and editing its figures falsifies the record
  // rather than correcting it. The banner is the honest fix — it says the
  // numbers are frozen instead of pretending they were always current.
  const html = built(path.join('archive', 'hackathon', 'index.html'))
  assert.match(html, /19[ -]check|19 check/,
    'the submission said 19; that is what it said')
})

test('robots keeps the archive out and points at the sitemap', () => {
  const robots = built('robots.txt')
  assert.match(robots, /Disallow: \/archive\//)
  assert.match(robots, /^Sitemap: https?:\/\//m)
})

// ── the performance argument has to survive contact with the build ────────

test('the client ships a tiny script, not a framework', () => {
  // Every page is prerendered and none is interactive, so hydration would
  // download React and TanStack Router to re-render markup already on screen —
  // the same objection the brief raises against 90KB of GSAP, made by the site
  // that raises it. Measured at 1.23KB raw when this was written.
  const dir = path.join(OUT, 'assets')
  const js = fs.readdirSync(dir).filter((f) => f.endsWith('.js'))
  const total = js.reduce((n, f) => n + fs.statSync(path.join(dir, f)).size, 0)
  assert.ok(total < 25_000,
    `${(total / 1024).toFixed(1)}KB of JS across ${js.length} file(s). `
    + 'Something re-introduced runtime hydration; the pages are static.')
})

// ── a claim needs a citation to exist ─────────────────────────────────────

test('every published figure carries a source', () => {
  // `facts.ts` types `source` as required, so this catches the case types
  // cannot: an entry added with an empty string to satisfy the compiler.
  const src = fs.readFileSync(path.join(SITE, 'src', 'facts.ts'), 'utf8')
  const body = src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')
  // `value:` alone also matches the `type Stat = { readonly value: string }`
  // DECLARATION, which of course carries no source string — so the first draft
  // failed on an empty STATS array, reporting a violation that did not exist.
  // Anchored to the STATS literal instead.
  const arr = body.match(/STATS[^=]*=\s*\[([\s\S]*?)\]/)
  const blocks = arr ? [...arr[1].matchAll(/\{[^{}]*\}/g)].map((m) => m[0]) : []
  for (const b of blocks) {
    const m = b.match(/source\s*:\s*'([^']*)'/)
    assert.ok(m && m[1].trim().length > 3,
      `a STATS entry has no usable source:\n${b}\n`
      + 'A figure with no citation is how 19 and 23 both came to be published.')
  }
})

/**
 * THE FIX WAS APPLIED TO THE PAGE AND MISSED EVERY MACHINE-READABLE COPY.
 *
 * `facts.ts` documents "human-confirmed" as false against
 * `bot/config.py:2188-2190` — `auto_confirm_live_enabled` defaults to True, so
 * a signal clearing the 0.85 bar places a real-money order with nobody
 * pressing anything. PR #129 removed the phrase from the visible homepage.
 *
 * It survived in three places, all in `prerender.js`:
 *
 *   - `<meta name="description">`, which also feeds og: and twitter: — the
 *     surface with the WIDEST reach, since it is what a search result and a
 *     link preview show;
 *   - the JSON-LD `SoftwareApplication.description`, ingested as structured
 *     data rather than read;
 *   - `llms.txt`, which exists SPECIFICALLY to tell language models what this
 *     product is, so a false line there is repeated by every model that reads
 *     it.
 *
 * A visible correction that leaves the machine-readable copies alone corrects
 * the smallest audience. Checked over the BUILT OUTPUT so it holds for
 * whatever surface the claim reappears on.
 */
test('no published surface claims a human confirms live trades', () => {
  const forbidden = /human[- ]confirm/i
  const surfaces = [...PAGES.map((p) => [p, built(p)])]
  for (const extraFile of ['llms.txt', path.join('proof', 'index.html')]) {
    const f = path.join(OUT, extraFile)
    if (fs.existsSync(f)) surfaces.push([extraFile, fs.readFileSync(f, 'utf8')])
  }
  assert.ok(surfaces.length >= 3, 'too few surfaces checked — is the build stale?')
  for (const [name, text] of surfaces) {
    const hit = text.match(forbidden)
    assert.ok(!hit, `${name} claims ${JSON.stringify(hit && hit[0])} — `
      + 'auto_confirm_live_enabled defaults True (bot/config.py:2188-2190), so a '
      + 'live order can be placed with nobody pressing anything')
  }
})

test('the true version of that promise IS still stated', () => {
  // THE CONTROL. Deleting the claim outright would be the other failure: the
  // simulation-first default is real, it is the product's central safety
  // property, and "we removed the false half" must not become "we stopped
  // saying what the defaults are".
  assert.match(built('index.html'), /paper is the default|Simulation-first/i,
    'the homepage no longer states the simulation-first default at all')
  assert.match(fs.readFileSync(path.join(OUT, 'llms.txt'), 'utf8'),
    /live trading is off until/i,
    'llms.txt no longer tells a summariser what the live-trading default is')
})

/**
 * THE SITE MUST NOT BECOME THE FOURTEENTH SURFACE TO STATE A CHECK COUNT.
 *
 * `tests/test_no_hardcoded_risk_check_count.py` bans a number in front of the
 * word "check" on thirteen files. `_TOTAL_RISK_CHECKS = 23` was maintained by
 * hand against a file that changes, drifted DOWNWARD while the engine grew to
 * emit 36 labels, and ended up asserted across a dozen surfaces at three
 * different values — each one looking like a specific, confident measurement.
 *
 * A marketing page is exactly the surface that acquires such a number next,
 * and the Python guard does not scan `website/`. This is the same rule, on the
 * side of the tree it cannot see.
 *
 * The lookbehind is inherited from that guard along with its scars: `0.85 gate`
 * is a confidence THRESHOLD and `risk%20gate` is percent-encoding, and both
 * matched a naive pattern.
 */
test('no published page states a risk-check count', () => {
  const COUNTED = /(?<![.\d%])\b\d{1,3}[\s‑-]*(?:pre-trade |risk )?(?:check|checks|gates?)\b/i
  const files = [...PAGES]
  for (const extra_ of ['proof/index.html', 'risk/index.html', 'llms.txt']) {
    if (fs.existsSync(path.join(OUT, extra_))) files.push(extra_)
  }
  assert.ok(files.length >= 4, 'too few pages checked — is the build stale?')
  for (const f of files) {
    const visible = fs.readFileSync(path.join(OUT, f), 'utf8')
      .replace(/<script[\s\S]*?<\/script>/g, ' ')
      .replace(/<[^>]+>/g, ' ')
    const hit = visible.match(COUNTED)
    assert.ok(!hit, `${f} states a risk-check count (${JSON.stringify(hit && hit[0])}). `
      + 'There is no total to state: the number that matters is per-trade and is '
      + 'already reported where it is measured, on the decision record.')
  }
})

test('the fail-closed property IS still claimed, since it is the real one', () => {
  // THE CONTROL, same shape as the Python guard's. Removing the count must not
  // become removing the claim: fail-closed is the product's central safety
  // property and it is true.
  const risk = path.join(OUT, 'risk', 'index.html')
  assert.ok(fs.existsSync(risk), 'the risk page is not built')
  const html = fs.readFileSync(risk, 'utf8')
  assert.match(html, /fail-closed/i, 'the page no longer states the contract at all')
  assert.match(html, /cannot be evaluated/i,
    'the page no longer says what happens to an unanswerable check, which is '
    + 'the entire property')
})

