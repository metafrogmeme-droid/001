# Pi Network

RUNECLAW reaches Pi Network two ways. **RUNECLAW Local** is a self-hosted package that Pi Node operators install from SoloHost, inside Pi Desktop. **The Arena in the Pi Browser** is the paper Arena as a Pi app, signed in with Pi. Neither carries the $RCLAW token or its presale.

## RUNECLAW Local on Pi Desktop (SoloHost)

SoloHost installs and runs self-hosted apps on a Pi Node operator's own computer. The package is two files in [`scripts/solohost/`](https://github.com/metafrogmeme-droid/001/tree/main/scripts/solohost): `docker-compose.yml` runs the bot from the release image, and `config_options.yml` is the install form.

The install is **practice only**. These are fixed values the form never shows, so an install cannot change them:

| Setting | Value | What it keeps |
|---|---|---|
| `SIMULATION_MODE` | `true` | No order reaches an exchange |
| `LIVE_TRADING_ENABLED` | `false` | Live trading stays off |
| `AUTO_CONFIRM_LIVE_ENABLED` | `false` | Nothing is confirmed without the owner |
| `PAPER_AUTO_ACCEPT` | `false` | Only the owner is let in; anyone else waits for `/approve` |
| `PAPER_SIM_OPT_IN_ENABLED` | `true` | The owner's `/paper on` puts their confirms on the practice book |

The form asks for a Telegram bot token from @BotFather, the owner's numeric Telegram ID, a dashboard token, and optionally an AI provider and key. No exchange key is asked for.

After installing, the owner opens the bot in Telegram, sends `/start`, then `/paper on`. The App tab shows the bot's dashboard and asks for the dashboard token in a form. A token Telegram rejects stops the bot at boot with one line, `Telegram rejected TELEGRAM_BOT_TOKEN (InvalidToken)`, and the token itself is never printed.

The image is published by `.github/workflows/image.yml` when a `v*` tag is pushed. It refuses to overwrite a tag that is already published. The package's README has the publishing steps.

## The Arena in the Pi Browser

`https://www.humanoid-traders.com/pi` is the paper Arena for Pi users. Everyone starts on the same 10,000 vUSDT virtual stake, trades live Bitget prices, and can climb the season board.

**How sign-in works.** The page waits for `Pi.init({ version: "2.0" })` to finish, then asks Pi's SDK to authenticate and gets back an access token. Our server exchanges that token with Pi's App Studio (`POST /pi/auth/v1/login`), the flow Pi requires of every app. App Studio checks the token against the Pi Platform, and the uid in its answer is the identity. A uid or username the page sends is ignored. One Pi account is one RUNECLAW account.

- **A token App Studio refuses** is a 401, and the page says Pi did not confirm the sign-in.
- **App Studio unreachable** is a 503, and the page says the fault is on our side. It is never reported as a refusal of the person.
- **Outside the Pi Browser** there is no SDK, and the page says to open it there.

**The session.** The Pi Browser shows apps inside a frame (Pi's SDK talks to its host with `window.parent.postMessage`). So the session is a bearer token kept in the page's memory: no cookie, no storage. Closing the app signs you out.

**The page's security policy** follows Pi's App Studio guidance:
- Pi's SDK is the only outside script, loaded in the page head.
- Connections go to the site itself and `https://socialchain.app`, which the SDK calls during sign-in.
- Inline styles are allowed (the SDK needs them); inline scripts are not.
- Only `'self' https://*.pinet.com https://*.minepi.com` and App Studio itself, `https://appstudio-u7cm9zhmha0ruwv8.piappengine.com`, may frame the page. App Studio's "Checking Pi Auth" step shows the page in a preview inside App Studio. A browser checks every frame around a page, so without App Studio's origin the preview fails with `net::ERR_BLOCKED_BY_RESPONSE`.

There is no wildcard anywhere: App Studio is listed by its exact origin, not `*.piappengine.com`, which would let every app App Studio hosts frame the page. The App Studio sign-in exchange runs on our server, so its origin is not in the page's `connect-src`.

**Signals and charts.** Signed in, the app shows the engine's latest calls the way a signed-in member sees them on the website: side, entry, stop, target, R:R and confidence. Each call has the site's TradingView chart with its levels drawn on it.

Each card offers one action at most, depending on where the call stands:

| The call | The card shows | What a tap does |
|---|---|---|
| Live (inside the window the bot states, five minutes by default) | **Paper 200 · 2×** and a countdown | Follows the call on the paper account at the live mark, the same as the website's signal stream. The position is the call's. |
| Window closed, but the call has not reached its target or stop | **Trade it yourself · 200 · 2×** and the time the window closed | Opens the call's side as your own trade at the live price. It is not credited to the call: the server records it as a manual position, with the same limits, season rules and armed envelope as a ticket you type. |
| Ended (target, stop, not filled, or any other final outcome) | The outcome, and no button | Nothing. |

- Either way the fill is the live mark, never the call's entry. The call's stop and target carry over only where they are still on the right side of the price, and the confirmation names any that did not. A fill far from the call's entry says how far.
- When a window closes while the app is open, the card changes in place. The chart and anything typed in the open form are left as they were.
- **The charts** take the website Arena's timeframes: **15m**, **1H**, **4H** and **1D**, chosen once for every chart on the panel and remembered on that device. Pinch a chart to zoom and drag it sideways to look back up to 200 bars. A vertical drag still scrolls the app, and a chart keeps its zoom when the panel refreshes.
- The list refreshes every minute while the app is open. A refresh that finds the same calls redraws nothing.
- A list that cannot be read says so. That message is never a claim that there are no signals.
- The chart library is the site's own copy, served from our domain, and the candles come from our relay. The page's security policy is unchanged.

**What is not on it.** No token, no presale and no link to the rest of the site. The page is built from the Farcaster Mini App's parts and loads none of the site's pages. Its one payment is an optional tip.

**Tips.** A signed-in Pi user can tip the team a small amount of Pi from the **Support RUNECLAW** buttons. A tip unlocks nothing and does not touch the arena account.

1. The page asks our server for one of the amounts it offers. The server records an order (who, how much) and returns the amount, memo and metadata the page hands to `Pi.createPayment`. The page never chooses the amount itself.
2. When Pi is ready, the page passes on Pi's payment id. The server reads that payment from the Pi Platform (`GET https://api.minepi.com/v2/payments/{id}`, with the server-only key) and checks it against the order: the same person, amount and order, user to app, not cancelled. Only then does it approve the payment.
3. After the person signs, the page passes on the payment id and txid. The server checks the txid against Pi's record of the payment, then completes it. Only a completion Pi accepts records the tip as received.

Each step saves what it relies on before asking Pi, so Pi's retries are safe. One Pi payment can settle one order, and one order one payment. A payment left unfinished, for example because the app was closed mid-tip, is reported by Pi's SDK at the next sign-in, and the server finishes it from Pi's own record.

Pi's helper packages for this (`pi-sdk-express`, `pi-sdk-js`) were withdrawn from npm in March 2026, so the page calls `Pi.createPayment` directly and the server calls Pi's documented Platform API. The SDK's own payment calls go to the same backend it uses for sign-in, so the page's security policy is unchanged.

**App Studio's validation file** is the static `app/public/pi/pi-app-validation.txt`, served at `/pi/pi-app-validation.txt`. It holds exactly App Studio's token, with no route or script of its own.

**Settings** (all optional):

| Variable | What it does |
|---|---|
| `PI_NETWORK_API_KEY` | The app's server API key, from the Developer Portal: this app, then **Settings**, **API key**, **Reveal**. Set it in the hosting provider's secret settings, on the website server only. Without it there are no tip buttons. |
| `PI_TIP_AMOUNTS` | The tip amounts in Pi, comma-separated, `1,3,5` by default (each at most 100) |
| `PI_OAUTH_CLIENT_ID` | The OAuth Client ID for **Sign in with Pi** on the website, from the Developer Portal: this app, then **Pi Sign-In**. It is public. Without it the login page shows no Pi button. |
| `PI_VALIDATION_KEY` | The key from the Developer Portal's **Verify App Domain** step. It is served at `/pi/validation-key.txt`, the path the portal checks for this app, and at `/validation-key.txt`; 404 when unset |
| `PI_FRAME_ANCESTORS` | Replaces Pi's frame list (for `/pi`, `/privacy` and `/terms`) if Pi's hosts change |
| `PI_AUTH_ORIGIN` | App Studio's backend, `https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com` by default |

**Listing it.** In Pi's App Studio, choose **Use External AI**, then **Convert App**, with `https://www.humanoid-traders.com/pi` as the App Link. That route is free. Step 2 gives a prompt for the validation file and Pi Auth; after the deploy, **Verify My App** checks both.

In the Developer Portal:
- **Verify App Domain** shows a key. Set it as `PI_VALIDATION_KEY` on the website server, restart, then press **Verify Domain**. The portal reads `https://www.humanoid-traders.com/pi/validation-key.txt`.
- **Ecosystem listing** asks for a privacy policy and terms of service. They are `https://www.humanoid-traders.com/privacy` and `https://www.humanoid-traders.com/terms`.

**The privacy policy and the terms** are written once, in the marketing site (`site/src/routes/privacy.tsx`, `site/src/routes/terms.tsx`). Tests there check what they say against the code. The website serves each built page's text at `/privacy` and `/terms` in a plain page: no script, no presale, and no link beyond the two policies, because Pi's listing links them. The web deploy ships the `app/` folder alone, so the website reads a copy of the built pages under `app/content/site/`. The site build refreshes the copy, and a test fails if it differs from the built pages.

## Sign in with Pi on the website

The website's login page offers **Continue with Pi** beside Google, Telegram, Discord, X and wallets, once `PI_OAUTH_CLIENT_ID` is set. It uses Pi Sign-In, Pi's OAuth for sites outside the Pi Browser. Pi issues a client ID and no secret, and returns the sign-in in the URL fragment.

1. The button goes to `/api/auth/oauth/pi/start`. The server records a single-use `state` for ten minutes and sends the person to `https://accounts.pinet.com/oauth/authorize`, asking only for the `username` scope.
2. Pi sends them back to `/auth/pi/callback` with an access token in the fragment. A browser never sends a fragment to a server, so that page's one script reads it, removes it from the address bar, and posts the token and `state` to `/api/auth/oauth/pi/token`.
3. The server checks the `state` was its own, for Pi, and unused. It then asks Pi who the token belongs to (`GET https://api.minepi.com/v2/me`) and keeps only the uid. A uid sent by the browser is ignored.
4. The person is signed in to the account that uid names, or a new one, the same way as every other sign-in. A signed-in person can also link Pi to their existing account from the account panel.

- **A token Pi refuses** is a 401, and nothing is created.
- **Pi unreachable** is a 503 and a sentence saying the fault is on our side. It is never reported as a refusal.
- **The token** is never logged or returned.

In the Developer Portal, register `https://www.humanoid-traders.com/auth/pi/callback` under **Pi Sign-In → Redirect URIs**. It must match exactly.

**Not yet confirmed:** whether this sign-in and the Pi Browser app give one person the same uid. Pi says a uid is stable per person within one app. Both store it in the same place, so if they match, a person has one account either way. Check with a real account before announcing the button.

## Not yet

| Pi feature | Where it would go | Status on 2026-10-17 |
|---|---|---|
| PiVerify | The one-person-one-share check for an airdrop: Pi's KYC-based answer to whether a user is a unique, verified human, paid in Pi per check | No public integration docs found |
| Distributed compute for SoloHost apps | Backtests run on Pi Nodes | Pi lists it as "coming soon" |
