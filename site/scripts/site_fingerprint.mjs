/**
 * A content fingerprint for the marketing site, so "did the deploy land?" is a
 * measurement instead of a guess.
 *
 * THE GAP THIS FILLS. The platform has `/api/version`, whose `build`/`assets`
 * pair diagnoses a deploy in one line, and the bot has
 * `verify_deploy_source.sh`, which refuses to start on the wrong commit. The
 * marketing site had neither. `api_bridge.py` serves `website/` straight out of
 * the checkout, so "is the box on the right commit" ANSWERS it for that host —
 * but the site is also published elsewhere, and for that copy nothing could
 * answer it at all. The symptom was exactly the one this repo keeps writing
 * guards about: "we deployed but still don't see any changes to website", with
 * no way to tell a stale host from a stale browser cache from a build that
 * never contained the change.
 *
 * Two hashes, the same shape and the same diagnosis table as `/api/version`:
 *
 *     pages   assets   means
 *     moved   moved    full publish landed
 *     moved   same     content/markup change only
 *     same    moved    bundle change only
 *     same    same     NOTHING PUBLISHED, whatever the log says
 *
 * DERIVED FROM CONTENT, NEVER FROM THE COMMIT. A SHA-stamped value would
 * change on every commit, so the built output would never match the committed
 * one and CI's "the committed site is the built site" gate would fail forever.
 * Content-derived means identical content hashes identically, on any machine,
 * in any order — which is also what makes it comparable across hosts.
 *
 * WHAT IT DOES NOT COVER, said plainly because a fingerprint that silently
 * ignores half the site is worse than none: `pages` covers every `.html` served
 * and `assets` covers everything under `assets/`. The media files at the root
 * (images, the demo video) are in NEITHER. A publish that changed only an image
 * moves neither hash, and this file must not be read as saying otherwise. Those
 * are not emitted by the build, and folding them in would churn the fingerprint
 * on commits that changed no page.
 */

import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const SITE = join(HERE, '..', '..', 'website')

/** The file the fingerprint is written to — excluded from its own inputs. */
export const STAMP = 'version.json'

function walk(dir, out = []) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return out // an absent directory is an empty one, not a crash
  }
  for (const e of entries) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.isFile()) out.push(p)
  }
  return out
}

/**
 * Hash a set of files as (path, content) pairs.
 *
 * The PATH is hashed alongside the bytes on purpose: renaming a bundle without
 * touching its contents is a real change to what a browser fetches, and a hash
 * over contents alone would call that publish a no-op.
 */
function digest(root, files) {
  const h = createHash('sha256')
  for (const f of files.slice().sort()) {
    // POSIX separators so a Windows checkout produces the same value.
    h.update(relative(root, f).split(sep).join('/'))
    h.update('\0')
    h.update(createHash('sha256').update(readFileSync(f)).digest())
  }
  return h.digest('hex').slice(0, 12)
}

/**
 * @param {string} [root] the served directory (defaults to <repo>/website)
 * @returns {{pages: string, assets: string, counts: {pages: number, assets: number}}}
 */
export function fingerprint(root = SITE) {
  const all = walk(root)
  const pages = all.filter((f) => f.endsWith('.html'))
  const assetDir = join(root, 'assets') + sep
  const assets = all.filter((f) => f.startsWith(assetDir) && !f.endsWith(STAMP))
  return {
    pages: digest(root, pages),
    assets: digest(root, assets),
    counts: { pages: pages.length, assets: assets.length },
  }
}

/** The exact bytes written to website/version.json. Deterministic. */
export function stampText(root = SITE) {
  const fp = fingerprint(root)
  return `${JSON.stringify(fp, null, 2)}\n`
}

// `node site/scripts/site_fingerprint.mjs` — the local half of the check.
if (process.argv[1] && process.argv[1].endsWith('site_fingerprint.mjs')) {
  const fp = fingerprint()
  if (fp.counts.pages === 0) {
    // Zero pages would hash to a stable value and compare equal to another
    // empty build — a confident "nothing changed" derived from nothing read.
    console.error('site_fingerprint: no HTML found under website/ — run `npm run build`')
    process.exit(2)
  }
  console.log(`${fp.pages} ${fp.assets}`)
}
