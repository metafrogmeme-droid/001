/**
 * The privacy policy described a different product.
 *
 * `website/privacy.html`, dated 31 May 2026, was written for a self-hosted
 * single-operator Telegram bot. By August it was describing a multi-user web
 * platform with accounts, OAuth, 2FA, wallets and a server-side key vault — and
 * every negative claim it made had become false:
 *
 *   "We do not collect your real name, email address, or phone number"
 *     → app/auth.js INSERTs email into users
 *   "We do not use cookies or web tracking of any kind"
 *     → app/lib/session_cookie.js sets rc_auth / rc_session / rc_jwt
 *   "all keys remain in the operator's local environment variables"
 *     → bot/core/exchange_credentials.py is a per-user Fernet vault
 *   "No Telegram user data is included in LLM requests"
 *     → bot/skills/telegram_handler.py appends the saved agent profile
 *
 * None of it was a lie when written. That is the point, and it is why these
 * tests check the POLICY AGAINST THE CODE rather than checking that the policy
 * says something. A privacy page cannot be pinned by asserting its wording:
 * the wording was fine, and the system moved.
 *
 * So each test below asks a question of the source and requires the page to
 * agree with the answer. When the code changes back — when deletion ships, when
 * a cookie is dropped — the corresponding test fails and the page gets
 * corrected in the same commit.
 */

import test from 'node:test'
import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.join(HERE, '..')
const REPO = path.join(SITE, '..')
const PAGE = path.join(REPO, 'website', 'privacy', 'index.html')

function page() {
  if (!fs.existsSync(PAGE)) {
    throw new Error('privacy page not built — run `npm run build` in site/')
  }
  return fs.readFileSync(PAGE, 'utf8')
}

const repo = (...p) => fs.readFileSync(path.join(REPO, ...p), 'utf8')

/** Visible text only, so a match cannot come from a class name or an attribute. */
function text() {
  const m = page().match(/<div id="root">([\s\S]*)<\/div>\s*<\/body>/)
  assert.ok(m, 'no #root content')
  return m[1].replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
}

// ── the four claims that went false ───────────────────────────────────────

test('it does not deny collecting an email, because sign-up inserts one', () => {
  const auth = repo('app', 'auth.js')
  assert.match(auth, /INSERT INTO users \(email/,
    'sign-up no longer stores an email — this test and the page both need revisiting')
  const t = text().toLowerCase()
  assert.ok(!/do not collect .{0,40}email/.test(t),
    'the page denies collecting an email while auth.js inserts one')
  assert.match(t, /email/, 'a policy for an email-signup product must mention email')
})

test('it does not deny cookies, because the app sets three', () => {
  const src = repo('app', 'lib', 'session_cookie.js')
  assert.match(src, /Set-Cookie/, 'the session cookie module stopped setting cookies')
  const t = text().toLowerCase()
  assert.ok(!/(do not|don't|no) use cookies/.test(t),
    'the page denies using cookies while session_cookie.js sets them')
  // Named, not hand-waved: a reader can check these in their own browser.
  for (const name of ['rc_auth', 'rc_session', 'rc_jwt']) {
    assert.ok(text().includes(name), `the page should name the ${name} cookie`)
  }
})

test('it does not claim keys stay in an operator env var, because a vault holds them', () => {
  const store = repo('bot', 'core', 'exchange_credentials.py')
  assert.match(store, /encrypted at rest/i,
    'the credential store changed shape — recheck what the page says about keys')
  const t = text().toLowerCase()
  assert.ok(!/remain in the operator's local environment variables/.test(t))
  assert.ok(!/never leave your (device|browser)/.test(t),
    'keys are held server-side; the page must not say otherwise')
  assert.match(t, /encrypted/, 'the page must say how keys are held')
})

test('it does not deny sending user data to AI providers, because the bot sends it', () => {
  const th = repo('bot', 'skills', 'telegram_handler.py')
  assert.match(th, /saved agent profile/,
    'the profile note stopped reaching the prompt — recheck the page')
  const t = text().toLowerCase()
  assert.ok(!/no (telegram )?user data is included/.test(t))
  assert.ok(!/market data only/.test(t))
  assert.match(t, /watchlist|risk appetite/,
    'the page must say what user context reaches a provider')
})

// ── absences stated as absences ───────────────────────────────────────────

test('it does not imply a deletion path that does not exist', () => {
  // Verified from the source, not assumed: no route deletes an account.
  const files = ['auth.js', ...fs.readdirSync(path.join(REPO, 'app', 'routes'))
    .filter((f) => f.endsWith('.js')).map((f) => path.join('routes', f))]
  const deletes = files.filter((f) => {
    const s = repo('app', ...f.split(path.sep))
    return /DELETE FROM users|delete-?account|account\/delete/i.test(s)
  })
  assert.deepStrictEqual(deletes, [],
    'an account-deletion path now exists — say so on the page and drop this test')
  const t = text()
  assert.match(t, /no self-service account deletion/i,
    'with no deletion endpoint, the page must say so rather than stay silent')
})

test('it does not promise a retention limit nothing enforces', () => {
  // QUOTED SPANS REMOVED FIRST. The page names the sentence it refuses to
  // write — "we keep data only as long as necessary" — in order to reject it,
  // and the first version of this test matched that quotation and failed on
  // correct prose. Same trap as a comment quoting the string it forbids, which
  // CLAUDE.md calls the assertion that keeps misfiring; the fix there is the
  // fix here. The page was right and the test was wrong.
  const unquoted = text().toLowerCase().replace(/[“"][^”"]*[”"]/g, ' ')
  assert.ok(!/only as long as (is )?necessary/.test(unquoted),
    'that phrase describes a purge policy, and nothing in the product purges')
  // The positive rendering is the load-bearing half: an absence has to be
  // stated, not merely left un-promised.
  assert.match(text(), /no automatic retention limit/i)
})

test('it does not claim 2FA covers every sign-in route', () => {
  // Google / Telegram / wallet sign-in do not prompt for a code.
  const t = text().toLowerCase()
  assert.match(t, /not.{0,30}prompted when you sign in with/,
    'the 2FA carve-out must be stated, not left for the reader to discover')
})

// ── the page is reachable and the old one points at it ────────────────────

test('the retired page redirects rather than lingering', () => {
  const old = repo('website', 'privacy.html')
  assert.match(old, /url=\/privacy/, 'privacy.html must forward to the new route')
  assert.match(old, /noindex/,
    'two indexable privacy pages is how the stale one keeps being found')
  assert.ok(old.length < 1200,
    'privacy.html should be a stub; the policy lives in the built route')
})

test('the policy carries a date a reader can act on', () => {
  assert.match(text(), /Last updated \d{1,2} \w+ 20\d\d/,
    'a policy with no date gives a reader no way to tell it is stale')
})
