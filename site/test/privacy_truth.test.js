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
  // Anchored on the SEAM, not on the sentence the prompt happens to use. The
  // first version matched the literal `saved agent profile`, which is the
  // label the system prompt printed — renaming that label failed this test
  // with the message "the profile note stopped reaching the prompt" while the
  // note reached it perfectly well. A wording anchor over a wiring question.
  assert.match(th, /resolve_profile_note\(profile_note, user_id\)/,
    'the user-context note stopped reaching the prompt — recheck the page')
  assert.match(th, /user_profile_store/,
    'the declared half stopped reaching the prompt')
  const t = text().toLowerCase()
  assert.ok(!/no (telegram )?user data is included/.test(t))
  assert.ok(!/market data only/.test(t))
  assert.match(t, /watchlist|risk appetite/,
    'the page must say what user context reaches a provider')
})

test('the page discloses the OBSERVED half, because that reaches a provider too', () => {
  // The profile is what a user typed. `user_memory_store` is what the agent
  // watched them do, and it lands in the same system prompt — a second kind of
  // personal data leaving for a third party, disclosed by nothing when it
  // shipped. Same ratchet as the four claims above: the code moved, so the
  // page has to move with it.
  const th = repo('bot', 'skills', 'telegram_handler.py')
  assert.match(th, /user_memory_store/,
    'observed history no longer reaches the prompt — this test and the page '
    + 'both need revisiting')
  const t = text().toLowerCase()
  assert.match(t, /assets you ask the agent about/,
    'the page must say that what you ask about is recorded')
  assert.match(t, /recently asked the agent about/,
    'the AI-providers section must name it as something that is sent')
})

// ── absences stated as absences ───────────────────────────────────────────

/**
 * THIS TEST RAN THE OTHER WAY ROUND UNTIL DELETION SHIPPED.
 *
 * It used to verify from the source that no route deleted an account, and then
 * require the page to say `no self-service account deletion`. That is what the
 * file's opening promises — "when the code changes back, when deletion ships,
 * the corresponding test fails and the page gets corrected in the same commit"
 * — and it is exactly what happened: `DELETE /api/auth/account` landed, this
 * test went red on a page that had become wrong, and both halves moved
 * together. The ratchet is the point, in both directions.
 */
test('the page describes the deletion path, because one exists', () => {
  const auth = repo('app', 'auth.js')
  assert.match(auth, /router\.delete\('\/account'/,
    'the account-deletion route is gone — the page now over-promises and this '
    + 'test must go back to asserting the absence')
  const t = text()
  assert.ok(!/no self-service account deletion/i.test(t),
    'the page still says deletion does not exist, and it does')
  assert.match(t, /delete your account/i,
    'a product with a deletion endpoint must say so on its privacy page')
})

test('the page states the order deletion happens in, because the order is the safety', () => {
  // The load-bearing property, and the one a reader has to be told: the bot
  // holds the exchange keys, so it is purged first and a failure there aborts
  // everything. A page that says "we delete your data" over a system that can
  // half-delete it is making a claim it cannot keep.
  const auth = repo('app', 'auth.js')
  const route = auth.slice(auth.indexOf("router.delete('/account'"))
  assert.ok(route.indexOf('/account/purge') < route.indexOf('erasurePlan('),
    'the web rows are now erased before the bot confirms — the page describes '
    + 'the opposite, and the code is the half that is wrong')
  const t = text().toLowerCase()
  assert.match(t, /bot first/,
    'the page must say which side is cleared first and why')
  assert.match(t, /nothing is deleted anywhere/,
    'the page must say what happens when the bot does not confirm')
})

test('the page admits the row that survives, because one does', () => {
  const lib = repo('app', 'lib', 'account_erasure.js')
  assert.match(lib, /UPDATE users SET email = \?/,
    'the users row is deleted outright now — the page says it is kept as an '
    + 'empty shell, which would be a false admission rather than a false promise')
  assert.ok(!/DELETE FROM users/.test(lib), 'the users row must not be deleted')
  const t = text().toLowerCase()
  assert.match(t, /one row survives/,
    'erasure keeps the account row; a page that describes deletion as total '
    + 'would be overstating it')
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
