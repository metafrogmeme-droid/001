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

**What is not on it.** No token, no presale, no link to the rest of the site, and no payments. The page is built from the Farcaster Mini App's parts and loads none of the site's pages.

**App Studio's validation file** is the static `app/public/pi/pi-app-validation.txt`, served at `/pi/pi-app-validation.txt`. It holds exactly App Studio's token, with no route or script of its own.

**Settings** (all optional):

| Variable | What it does |
|---|---|
| `PI_VALIDATION_KEY` | A key the Developer Portal asks the domain to serve at `/validation-key.txt`; 404 when unset |
| `PI_FRAME_ANCESTORS` | Replaces Pi's frame list if Pi's hosts change |
| `PI_AUTH_ORIGIN` | App Studio's backend, `https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com` by default |

**Listing it.** In Pi's App Studio, choose **Use External AI**, then **Convert App**, with `https://www.humanoid-traders.com/pi` as the App Link. That route is free. Step 2 gives a prompt for the validation file and Pi Auth; after the deploy, **Verify My App** checks both.

## Not yet

| Pi feature | Where it would go | Status on 2026-10-16 |
|---|---|---|
| Pi Sign-in on the website itself | Another sign-in method beside Telegram, Google and Farcaster | Announced at Pi2Day 2026 for supported external sites; no public integration docs found |
| PiVerify | The one-person-one-share check for an airdrop: Pi's KYC-based answer to whether a user is a unique, verified human, paid in Pi per check | No public integration docs found |
| Distributed compute for SoloHost apps | Backtests run on Pi Nodes | Pi lists it as "coming soon" |
