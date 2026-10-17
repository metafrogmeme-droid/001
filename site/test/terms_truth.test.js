/**
 * The terms describe the product, so the code is asked whether they are true.
 *
 * privacy_truth.test.js exists because a policy written for one product kept
 * being published over another. The terms make product claims too: the paper
 * balance, what a tip changes, that a tip cannot be refunded, how an account is
 * deleted, who may use it. Each test below asks the source the question one
 * of those sentences answers, and requires the built page to agree. When the
 * code moves, the sentence moves in the same commit.
 *
 * The rest of the page (no advice, no warranty, acceptable use) is a promise
 * asked of the reader, not a description of the code, and is held only to
 * being present and dated.
 */

import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.join(HERE, '..', '..')
const OUT = path.join(REPO, 'website')

function built(rel) {
  const p = path.join(OUT, rel, 'index.html')
  if (!fs.existsSync(p)) throw new Error(`${rel} is not built — run \`npm run build\` in site/`)
  return fs.readFileSync(p, 'utf8')
}

/** Visible text of the article, so a match cannot come from the nav or an attribute. */
function article(rel) {
  const m = built(rel).match(/<article[^>]*>([\s\S]*?)<\/article>/)
  assert.ok(m, `${rel}: no <article>`)
  return m[1].replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/g, ' ')
    .replace(/&#x27;|&#39;/g, "'").replace(/\s+/g, ' ')
}

const repo = (...p) => fs.readFileSync(path.join(REPO, ...p), 'utf8')
/** Comments out, so a sentence in a comment cannot stand in for the code. */
const code = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

test('the paper balance on the page is the one the arena starts every account with', () => {
  const m = repo('app', 'public', 'js', 'arena_engine.js').match(/const START_BALANCE = ([\d_]+);/)
  assert.ok(m, 'arena_engine.js no longer defines START_BALANCE')
  const start = Number(m[1].replace(/_/g, '')).toLocaleString('en-US')
  assert.match(article('terms'), new RegExp(`same ${start} vUSDT virtual balance`),
    `the arena starts every account with ${start} vUSDT; the terms must say that number`)
})

test('a tip unlocks nothing, because the tip code writes only its own table', () => {
  const sources = [repo('app', 'routes', 'pi_payments.js'), repo('app', 'lib', 'pi_payments.js')].map(code)
  for (const src of sources) {
    const tables = [...src.matchAll(/(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)/g)].map((t) => t[1])
    assert.ok(tables.length > 0 || !/pool\./.test(src), 'the tip code writes, but the parse found no table')
    for (const t of tables) {
      assert.strictEqual(t, 'pi_payments',
        `the tip code now writes ${t}: a tip changes something, and the terms say it changes nothing`)
    }
  }
  assert.match(article('terms'), /It unlocks nothing, buys no service, and changes nothing about your account or the Arena/)
})

test('there is no refund, because nothing in the code can pay a person', () => {
  // Pi's app-to-user payments are a separate API (`POST /v2/payments` from the
  // server, with a wallet seed). None of it exists here: the server can
  // approve and complete a payment a person makes, and nothing else.
  const lib = code(repo('app', 'lib', 'pi_payments.js'))
  const calls = [...lib.matchAll(/\/payments\/[^'"`]*['"`]|\/payments['"`]/g)].map((c) => c[0])
  for (const c of calls) {
    assert.match(c, /^\/payments\/\$\{[^}]+\}(\/approve|\/complete)?['"`]$/,
      `a Pi Platform call the terms do not know about: ${c}`)
  }
  assert.doesNotMatch(lib, /app_to_user|refund|PI_WALLET|seed/i,
    'an app-to-user payment path exists now: the terms say there is no refund')
  assert.match(article('terms'), /We cannot reverse it, and there is no refund/)
})

test('an account can be deleted by its owner, as the terms say', () => {
  assert.match(repo('app', 'auth.js'), /router\.delete\('\/account'/,
    'the account-deletion route is gone: the terms promise it')
  assert.match(article('terms'), /delete your account yourself at any time/)
})

test('the age line is the same on both pages and in the footer', () => {
  assert.match(article('terms'), /18 or older/)
  assert.match(article('privacy'), /not for anyone under 18/)
  assert.match(built('terms'), /18\+/, 'the footer disclaimer lost its age line')
})

test('the two pages point at each other, at the paths both hosts serve', () => {
  assert.match(built('terms').match(/<article[\s\S]*?<\/article>/)[0], /href="\/privacy"/)
  assert.match(built('privacy').match(/<article[\s\S]*?<\/article>/)[0], /href="\/terms"/)
})

test('the page says what it is, and when', () => {
  const t = article('terms')
  assert.match(t, /None of them is investment advice/)
  assert.match(t, /Pi Network is not a party to these terms/)
  assert.match(t, /Last updated \d{1,2} \w+ 20\d\d/)
  // The terms name no governing law because the operator has named none.
  // A jurisdiction appears here when one is decided, not before.
  assert.doesNotMatch(t, /governed by the laws of|exclusive jurisdiction/i)
})
