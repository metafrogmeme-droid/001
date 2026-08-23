# WalletConnect v2 — measured, and not adopted (2026-08-23)

**Decision: do not integrate WalletConnect.** Recorded here with the numbers so
it is settled rather than re-argued, and with the conditions that would reverse
it.

This is a decision record, not a rejection of the technology. WalletConnect is
the right answer to a problem this app turns out not to have much of.

---

## 1. What it would actually buy

One thing: **signing from a phone wallet while the user is on a desktop
browser.**

Everything adjacent is already covered, and finding that out is most of why the
answer is no.

| case | already works via | notes |
|---|---|---|
| Mobile wallet's in-app browser (MetaMask Mobile, Trust, Rainbow, Coinbase) | **EIP-6963** in `app/public/js/wallet_picker.js` | those browsers inject `window.ethereum`; the picker's legacy fallback handles wallets predating 6963 |
| Desktop browser extension | EIP-6963 | the picker's primary path |
| Desktop user linking a phone wallet | **`/wallet-link?code=`** (`app/auth.js`) | QR on desktop, phone opens it *inside a wallet app's browser*, proves ownership with a SIWE-style signature. Single-use code, 10-minute TTL, server-side user binding — the phone never sees the desktop's JWT |

> **A correction, recorded because it drove the original enthusiasm.** The first
> assessment in this session claimed WalletConnect would make RUNECLAW
> "reachable from every mobile wallet's built-in dApp browser". That was wrong.
> Those browsers inject a provider and the existing picker already handles them.
> The real gap is narrower than stated, and it narrowed again on reading
> `auth.js`: the desktop→phone *linking* handoff exists too.

The residual gap is a phone wallet that **stays on the phone** while the user
drives a desktop session and signs repeatedly. Real, but not the common path
for this product, and not one any user has asked for.

---

## 2. What it costs, measured

Every figure below was measured on 2026-08-23 against
`@walletconnect/ethereum-provider@2.23.10`, not estimated.

| cost | measured |
|---|---|
| Browser bundle | **2.0 MB** minified IIFE, **578 KB** gzipped (esbuild, `--minify --target=es2020`) |
| Dependency tree | **149 packages**, 419 MB `node_modules` |
| Build step | **required** — see §3 |
| External hosts | **5** (see §4) |
| External account | a WalletConnect Cloud `projectId` |

### The 2 MB is not the objection on its own

It would be lazy-loaded — fetched only when a user actually picks WalletConnect
in the picker, never on a dashboard load. A 578 KB gzipped download at the
moment someone chooses to use a feature is defensible.

---

## 3. The build step is the real cost

`app/public/` has **no build step**. Every script there is hand-written,
dependency-free, and served as-is; `lib/csp.js` computes script hashes from the
exact bytes `express.static` serves. That is a deliberate property, and it is
why the CSP can forbid inline script at all.

WalletConnect cannot be vendored as a single file. Its published UMD build is
not self-contained — the global branch of
`@walletconnect/ethereum-provider/dist/index.umd.js` expects **eleven** separate
globals to already exist:

```
bs58, viem, vanilla (valtio), utils$1, lit, decorators_js,
ifDefined_js, QRCodeUtil, ref_js, staticHtml_js, Big
```

So shipping it means one of:

1. **A bundler** (esbuild devDependency, a build script, and the artifact
   committed with a provenance test — or a new CI job). Adds the first build
   step to browser code that has never had one.
2. **Vendoring eleven peer libraries** and hoping their own UMD globals match
   those names. Fragile, and `viem` and `lit` are large in their own right.
3. **A CDN `<script>`** — refused outright: `script-src` is `'self'` plus
   hashes, and the repo vendors everything for exactly that reason.

Option 1 is the only workable one, and a build step is load-bearing once added:
every future contributor inherits it, and "the committed bundle is the built
bundle" becomes a gate somebody has to maintain.

---

## 4. The CSP widening

`app/server.js` currently sets:

```
connect-src 'self' blob:
frame-src   https://oauth.telegram.org
```

The SDK hardcodes these hosts (extracted from the installed package, not from
documentation):

| host | purpose | needed in |
|---|---|---|
| `wss://relay.walletconnect.org` | the relay — the actual mechanism | `connect-src` |
| `verify.walletconnect.org` | Verify API | `frame-src` (it is an iframe) |
| `secure.walletconnect.org` | secure-site surface | `frame-src` |
| `rpc.walletconnect.org` | blockchain RPC proxy | `connect-src` |
| `pulse.walletconnect.org` | **analytics / telemetry** | `connect-src` |

Two of the five are **iframes**, so `frame-src` widens as well as `connect-src`
— a fact the first assessment missed by thinking only about the relay.

The last one is telemetry. The containment plan was to leave `pulse` off the
allowlist so the browser blocks it — enforcement by CSP rather than by a config
flag that can change in a minor version. That is a sound approach, and it is
worth noting that it makes a paid dependency's analytics an adversarial
relationship from day one.

**If this is ever adopted, the widening must be conditional**: applied only when
`WALLETCONNECT_PROJECT_ID` is configured, so a deployment without the feature
keeps today's policy byte-for-byte. A CSP that widens for a feature nobody
enabled is a permanent cost for an occasional benefit.

---

## 5. What would reverse this

Any one of these:

- **Users ask for it.** Nobody has. Volume of "I want to sign from my phone
  while on my laptop" is currently zero, and this whole analysis exists because
  the feature sounded desirable rather than because it was requested.
- **A partner integration requires it** — a listing, a wallet directory, or a
  counterparty that speaks only WalletConnect.
- **The bundle stops needing a bundler.** If a genuinely self-contained browser
  build ships, cost item §3 disappears and the calculus changes materially.
- **`app/public/` acquires a build step for some other reason.** The marginal
  cost then drops to the bundle size and the CSP hosts.

None of these hold today.

---

## 6. What was NOT verified

Stated rather than left implicit, because an unverified claim in a decision
record is how a decision gets re-litigated badly:

- **No browser test was run.** The bundle was built and measured; it was never
  loaded in a page, and no wallet was paired. The 2.0 MB and the eleven globals
  are facts about the artifact; "it would work" is not something this analysis
  established.
- **Blocking `pulse` was never exercised.** The claim that the SDK still
  functions with its analytics host CSP-blocked is reasoning, not observation.
  Anyone adopting this must confirm it rather than inherit it from here.
- **Host list is from the installed package**, by extracting every
  `walletconnect.(com|org)` URL from `node_modules`. That finds hardcoded
  endpoints; it would not find one assembled at runtime from parts.

---

## 7. See also

- `app/public/js/wallet_picker.js` — EIP-6963 discovery, dependency-free, and
  its own note on why there is no SDK
- `app/auth.js` — the `/wallet-link?code=` phone handoff and the public-origin
  guard that a bad QR taught it
- `app/lib/public_origin.js` — one question, one answer, and why it refuses to
  guess
