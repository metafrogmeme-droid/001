/**
 * Derive the site's design tokens FROM the live platform's stylesheet.
 *
 * WHY THIS IS GENERATED AND NOT TYPED OUT.
 *
 * `website/index.html` carried this comment for its whole life:
 *
 *     Tokens mirror app/public/styles.css so the whole product reads as one
 *     surface.
 *
 * It did not. Every token matched except the one that defines the brand:
 * `--gold` was `#cbb06a` (actual gold) on the marketing page and `#3fb6ff`
 * (electric rune-blue) on the platform. Ninety percent right is what let it
 * survive — nobody scrolls a palette looking for the one hex that drifted, and
 * the comment asserting the mirror is exactly what stops them checking.
 *
 * A copy that CLAIMS to be a mirror and is not is this repo's signature defect,
 * arriving through a stylesheet. So the copy is not written by hand any more.
 * This reads the platform's `:root` and emits the subset the site uses. Drift
 * stops being something a test detects and becomes something that cannot happen.
 *
 * IT FAILS RATHER THAN GUESSES. An unreadable stylesheet, a missing `:root`, or
 * a token that has been renamed away all raise. There is no fallback palette,
 * because a fallback palette is precisely the hand-written copy this exists to
 * abolish — it would render a plausible site in the wrong brand and nothing
 * would say so. `--gold` being blue is the whole reason: a wrong colour looks
 * like a design choice, not like an error.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const PLATFORM_CSS = join(REPO, 'app', 'public', 'styles.css')
const OUT = join(HERE, '..', 'src', 'tokens.css')

/**
 * The tokens the marketing site is allowed to use.
 *
 * Deliberately a SHORT list. The site is not the platform and must not inherit
 * its whole component vocabulary — but every value it does share has to be the
 * same value, not an equal-looking one.
 */
const WANTED = [
  '--bg', '--surface', '--surface-2', '--surface-3',
  '--line', '--line-2',
  '--gold', '--gold-bright', '--gold-dim', '--steel', '--glow',
  '--up', '--up-dim', '--down', '--down-dim', '--warn', '--warn-dim',
  '--text', '--text-2', '--text-3',
]

/**
 * Blank CSS comments, preserving length so offsets in errors still mean
 * something.
 *
 * THIS RUNS BEFORE ANYTHING ELSE LOOKS AT THE FILE, and the first draft of this
 * module is why. It stripped comments only from the `:root` body it had already
 * sliced — so the slice itself was located on RAW text, and
 * `app/public/styles.css:5` reads:
 *
 *     no page may re-declare its own :root ramp.
 *
 * `indexOf(':root')` found that sentence, brace-matched forward from the next
 * `{` — the `@font-face` block three lines down — and extracted a region
 * containing no tokens at all. It failed loudly, which was luck: the same
 * mistake against a file whose stray `:root` sat above a real declaration block
 * would have emitted a PARTIAL palette and built a plausible site in half the
 * wrong colours.
 *
 * CLAUDE.md states the rule — "Strip comments first. A comment that quotes the
 * string it forbids is indistinguishable from the code doing it" — and this
 * module exists to enforce a sibling of it. Getting caught by it here, in the
 * generator, is the argument for doing it at the boundary rather than at each
 * point of use.
 */
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

export function extractTokens(rawCss) {
  const css = stripComments(rawCss)
  // The FIRST `:root` block only. A later one (a theme override, a print block)
  // is not the brand definition, and silently merging them would let a media
  // query's value win in a file that has no media queries in it. The platform
  // has a `:root[dir="rtl"]` further down; it is an RTL layout fix, not a
  // palette, and must not be mistaken for one.
  const start = css.indexOf(':root')
  if (start === -1) throw new Error(`no :root block in ${PLATFORM_CSS}`)
  const open = css.indexOf('{', start)
  if (open === -1) throw new Error(`malformed :root in ${PLATFORM_CSS}`)
  let depth = 0
  let end = -1
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}') { depth--; if (depth === 0) { end = i; break } }
  }
  if (end === -1) throw new Error(`unterminated :root in ${PLATFORM_CSS}`)
  const body = css.slice(open + 1, end)

  const found = new Map()
  // Comments stripped first — the platform's :root is heavily commented and one
  // of those comments discusses `--gold` by name. CLAUDE.md's oldest rule about
  // source scanning, and the reason it is stated there.
  const code = body.replace(/\/\*[\s\S]*?\*\//g, ' ')
  for (const m of code.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
    found.set(m[1], m[2].trim())
  }

  const missing = WANTED.filter((t) => !found.has(t))
  if (missing.length) {
    throw new Error(
      `these tokens are gone from the platform stylesheet: ${missing.join(', ')}\n` +
      `They were renamed or removed. Update WANTED in site/scripts/tokens.mjs to ` +
      `match — do NOT hand-write a replacement value, which is the drift this ` +
      `generator exists to prevent.`)
  }
  return WANTED.map((t) => [t, found.get(t)])
}

export function render(pairs) {
  const lines = pairs.map(([k, v]) => `  ${k}: ${v};`).join('\n')
  return `/* GENERATED by site/scripts/tokens.mjs — do not edit.
 *
 * Source of truth: app/public/styles.css :root
 * Regenerate: cd site && npm run build   (or: node scripts/tokens.mjs)
 *
 * The platform's accent token is named --gold for historical reasons and its
 * value is electric rune-blue. That is not a bug and must not be "corrected":
 * app/public/styles.css states it outright — "the token names keep their
 * historical --gold spelling so the entire site re-themes from these three
 * values alone".
 */
:root {
${lines}
}
`
}

function main() {
  let css
  try {
    css = readFileSync(PLATFORM_CSS, 'utf8')
  } catch (err) {
    // No fallback palette, on purpose. See the module comment.
    throw new Error(
      `cannot read the platform stylesheet at ${PLATFORM_CSS}: ${err.message}\n` +
      `The site's brand is DERIVED from it. Refusing to build a site in an ` +
      `invented palette.`)
  }
  const pairs = extractTokens(css)
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, render(pairs), 'utf8')
  const accent = pairs.find(([k]) => k === '--gold')[1]
  console.log(`tokens: ${pairs.length} derived from app/public/styles.css (accent ${accent})`)
}

if (import.meta.url === `file://${process.argv[1]}`) main()
