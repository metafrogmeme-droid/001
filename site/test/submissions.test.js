/**
 * The submissions index and its pages say only what the evidence shows.
 *
 * Driven against the BUILT pages in website/ (after `npm run build`) and the
 * one data file they come from, src/submissions.json. What is pinned:
 *
 *   - every entry has a page, the index links every page, the sitemap lists
 *     them, and every page's footer links the index;
 *   - a status is one of four, and only a confirmed entry carries a
 *     submission link;
 *   - a PRIVATE entry's page carries its name, its status and the fixed note,
 *     and nothing else: no description, no link out, no address;
 *   - the only repositories linked are the public ones, and a private
 *     repository's name never appears as a link;
 *   - the one approved video is the one linked, and the videos nobody
 *     approved for these pages appear nowhere on them;
 *   - Celo is not listed, and Bohr is not a second entry beside BotChainSeal;
 *   - external links open in a new tab and say so, and nothing is embedded
 *     (the site's CSP is default-src 'self').
 */
import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.join(HERE, '..')
const OUT = path.join(SITE, '..', 'website')
const DATA = JSON.parse(fs.readFileSync(path.join(SITE, 'src', 'submissions.json'), 'utf8'))

/** Repositories that are public, checked on the data's `checked` date. */
const PUBLIC_REPOS = new Set([
  'metafrogmeme-droid/001',
  'metafrogmeme-droid/claw-pay-paypal',
  'metafrogmeme-droid/claw-guard-near',
  'metafrogmeme-droid/keylease',
  'metafrogmeme-droid/keylease-wasm',
  'metafrogmeme-droid/Playbooks-BITGET',
  'metafrogmeme-droid/claw-guard-perps-public',
])

/** Private repositories behind some entries. None may be linked. */
const PRIVATE_REPOS = [
  'Claw-Pay', 'claw-guard-near-dev', 'botchain-seal', 'claw-guard-perps',
  'QIE', 'proof-of-call', 'ARB', 'ASCON', 'claw-reconcile', 'claw-meter',
  'agent-passport', 'paid-lock',
]

const APPROVED_VIDEO = 'pAAhR8rAJas'
/** Videos found in the repositories that nobody approved for these pages. */
const UNAPPROVED_VIDEOS = ['7YqZqlEq_gA', 'LDJDGLHcAAM']

function built(rel) {
  const p = path.join(OUT, rel)
  if (!fs.existsSync(p)) {
    throw new Error(`${rel} is not built. Run \`npm run build\` in site/ first.`)
  }
  return fs.readFileSync(p, 'utf8')
}

const page = (slug) => built(path.join('submissions', slug, 'index.html'))

/** The page's own content: the <article>, without the shared nav and footer. */
function article(html) {
  const m = html.match(/<article[\s\S]*<\/article>/)
  assert.ok(m, 'no <article> on the page')
  return m[0]
}

const hrefs = (html) => [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, '&'))
const external = (html) => hrefs(html).filter((h) => /^https?:\/\//.test(h))

// ── the data ──────────────────────────────────────────────────────────────

test('the data names the fifteen entries the owner asked for, once each', () => {
  const names = DATA.entries.map((e) => e.name)
  assert.deepStrictEqual([...names].sort(), [
    'ASCON', 'Agent Passport', 'Bitget AI S2', 'BotChainSeal', 'Claw Guard NEAR',
    'Claw Guard Perps', 'Claw Guard on Arbitrum', 'Claw Meter', 'Claw Pay',
    'Claw Reconcile', 'HashKey Seal', 'KeyLease', 'QIE Proof of Call',
    'RUNECLAW Arena (Pi)', 'paid.lock',
  ].sort())
  const slugs = DATA.entries.map((e) => e.slug)
  assert.equal(new Set(slugs).size, slugs.length, 'a slug is used twice')
  for (const s of slugs) assert.match(s, /^[a-z0-9]+(?:-[a-z0-9]+)*$/, `bad slug ${s}`)
})

test('Celo is excluded, and Bohr is BotChainSeal\'s history, not a second entry', () => {
  const raw = JSON.stringify(DATA)
  assert.doesNotMatch(raw, /celo/i)
  assert.ok(!DATA.entries.some((e) => /bohr/i.test(e.name) || /bohr/i.test(e.slug)),
    'Bohr is listed as an entry of its own')
  const bot = DATA.entries.find((e) => e.slug === 'botchainseal')
  assert.ok(bot.body.some((p) => /Bohr/.test(p)), 'the BotChainSeal page no longer explains Bohr')
})

test('a status is one of four, and only a confirmed entry carries a submission link', () => {
  assert.deepStrictEqual(Object.keys(DATA.statuses).sort(),
    ['confirmed', 'live', 'owner-reported', 'wip'])
  for (const e of DATA.entries) {
    assert.ok(e.status in DATA.statuses, `${e.slug}: unknown status ${e.status}`)
    const hasReceipt = e.links.some((l) => l.kind === 'submission')
    assert.equal(hasReceipt, e.status === 'confirmed',
      `${e.slug}: a ${e.status} entry ${hasReceipt ? 'carries' : 'lacks'} a submission link`)
  }
  assert.equal(DATA.entries.find((e) => e.slug === 'claw-pay').status, 'confirmed')
})

test('owner-confirmed submissions say only that the receipt is missing', () => {
  // Pi Arena and Bitget AI S2 were confirmed submitted by the owner on
  // 2026-11-08. Their pages must not read as doubt about the submission: the
  // one thing missing is a retrieved public receipt, and that is all they say.
  for (const slug of ['runeclaw-arena-pi', 'bitget-ai-s2', 'claw-guard-near']) {
    const e = DATA.entries.find((x) => x.slug === slug)
    assert.equal(e.status, 'owner-reported', `${slug} is ${e.status}`)
    assert.ok(e.notShown.some((t) => /^Submission receipt: not retrieved yet/.test(t)),
      `${slug}: the missing receipt is not said`)
    const text = JSON.stringify(e)
    for (const doubt of [/claims none/i, /no submission/i, /\bplanned\b/i, /no record/i, /unconfirmed/i]) {
      assert.doesNotMatch(text, doubt, `${slug}: reads as doubt about the submission (${doubt})`)
    }
    assert.ok(article(page(slug)).includes(DATA.statuses['owner-reported'].label))
  }
  // The other arm: an entry with no submission on record still says so.
  const kl = DATA.entries.find((x) => x.slug === 'keylease')
  assert.equal(kl.status, 'wip')
  assert.ok(kl.notShown.some((t) => /claims none/.test(t)))
})

test('Claw Guard NEAR links the public tag that marks its submitted state', () => {
  const a = article(page('claw-guard-near'))
  assert.ok(a.includes('https://github.com/metafrogmeme-droid/claw-guard-near/tree/hackathon-submission'))
})

test('every link is https, or a path on this site', () => {
  for (const e of DATA.entries) {
    for (const l of e.links) {
      assert.ok(/^https:\/\//.test(l.href) || /^\/[^/]/.test(l.href),
        `${e.slug}: ${l.href} is neither https nor a site path`)
    }
  }
})

// ── the built pages ───────────────────────────────────────────────────────

test('every entry has a page, the index links it, and the sitemap lists it', () => {
  const index = built(path.join('submissions', 'index.html'))
  const sitemap = built('sitemap.xml')
  assert.match(sitemap, /\/submissions<\/loc>/)
  for (const e of DATA.entries) {
    const html = page(e.slug)
    assert.ok(article(html).includes(`>${e.name.replace(/&/g, '&amp;')}<`), `${e.slug}: name not on its page`)
    assert.ok(index.includes(`href="/submissions/${e.slug}"`), `${e.slug}: the index does not link it`)
    assert.match(sitemap, new RegExp(`/submissions/${e.slug}</loc>`), `${e.slug}: not in the sitemap`)
    assert.ok(article(html).includes(DATA.statuses[e.status].label), `${e.slug}: status not said in words`)
  }
})

test('every page, old and new, links the submissions index from its footer', () => {
  for (const rel of ['index.html', 'proof/index.html', 'privacy/index.html',
    'submissions/index.html', 'submissions/claw-pay/index.html', '404.html']) {
    const footer = built(rel).match(/<footer[\s\S]*<\/footer>/)
    assert.ok(footer && footer[0].includes('href="/submissions"'), `${rel}: no footer link to /submissions`)
  }
})

test('a private entry carries its name and status and nothing else', () => {
  const priv = DATA.entries.filter((e) => e.private)
  assert.equal(priv.length, 7)
  for (const e of priv) {
    assert.equal(e.status, 'wip', `${e.slug}: a private entry claims ${e.status}`)
    assert.equal(e.summary, null)
    assert.equal(e.event, null)
    assert.deepStrictEqual([e.body, e.links, e.notShown], [[], [], []], `${e.slug}: private data on the entry`)
    const a = article(page(e.slug))
    assert.deepStrictEqual(external(a), [], `${e.slug}: a private page links out`)
    assert.doesNotMatch(a, /0x[0-9a-fA-F]{8,}/, `${e.slug}: an address or hash on a private page`)
    assert.match(a, /a public description has not been approved yet/)
  }
  // The other arm: a public entry does describe itself and link out.
  const pub = article(page('claw-guard-near'))
  assert.ok(external(pub).length > 0)
  assert.match(pub, /signing gate/i)
})

test('only public repositories are linked, and no private one by name', () => {
  for (const e of DATA.entries) {
    for (const h of external(article(page(e.slug)))) {
      const m = h.match(/^https:\/\/github\.com\/([^/]+\/[^/#?]+)/)
      if (m) assert.ok(PUBLIC_REPOS.has(m[1]), `${e.slug}: links ${m[1]}, not on the public list`)
      for (const r of PRIVATE_REPOS) {
        assert.ok(!new RegExp(`github\\.com/metafrogmeme-droid/${r}(?:[/"#?]|$)`, 'i').test(h),
          `${e.slug}: links the private repository ${r}`)
      }
    }
  }
})

test('the approved video is linked, and no unapproved one appears', () => {
  const pay = article(page('claw-pay'))
  assert.ok(pay.includes(`https://youtu.be/${APPROVED_VIDEO}`), 'the approved Claw Pay video is not linked')
  assert.ok(pay.includes('https://devpost.com/software/claw-pay'), 'the Devpost submission is not linked')
  for (const e of DATA.entries) {
    const html = page(e.slug)
    for (const v of UNAPPROVED_VIDEOS) {
      assert.ok(!html.includes(v), `${e.slug}: carries the unapproved video ${v}`)
    }
  }
  const videos = DATA.entries.flatMap((e) => e.links.filter((l) => l.kind === 'video'))
  assert.deepStrictEqual(videos.map((l) => l.href), [`https://youtu.be/${APPROVED_VIDEO}`])
})

test('external links open in a new tab, say so, and nothing is embedded', () => {
  for (const e of DATA.entries) {
    const a = article(page(e.slug))
    assert.doesNotMatch(a, /<iframe|<embed|<object/i, `${e.slug}: embeds something`)
    for (const m of a.matchAll(/<a\b[^>]*href="(https?:[^"]+)"[^>]*>([\s\S]*?)<\/a>/g)) {
      assert.match(m[0], /target="_blank"/, `${e.slug}: ${m[1]} does not open in a new tab`)
      assert.match(m[0], /rel="noopener noreferrer"/, `${e.slug}: ${m[1]} lacks noopener noreferrer`)
      assert.match(m[2], /opens in a new tab/, `${e.slug}: ${m[1]} does not say it opens a new tab`)
    }
  }
})

test('each page has one h1 and a breadcrumb that marks the current page', () => {
  for (const e of DATA.entries) {
    const a = article(page(e.slug))
    assert.equal((a.match(/<h1\b/g) || []).length, 1, `${e.slug}: not exactly one h1`)
    assert.match(a, /<nav aria-label="Breadcrumb"/)
    assert.match(a, /aria-current="page"/)
  }
  const index = article(built(path.join('submissions', 'index.html')))
  assert.equal((index.match(/<h1\b/g) || []).length, 1)
})

test('a page that is not an entry is the 404, not a blank submission page', () => {
  assert.ok(!fs.existsSync(path.join(OUT, 'submissions', 'celo')), 'a Celo page was built')
})
