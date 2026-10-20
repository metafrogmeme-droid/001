# RUNECLAW Arena in the Pi hackathon: the October 2026 round

*Written 20 October 2026. It joins the owner's submission pack (same date) with what the code now does. Facts about Pi's rules are quoted from the page named beside them and dated; anything not checked says so.*

## 1 · The round

| | |
|---|---|
| What | Pi's monthly hackathon, run through the Brainstorm app: "creative, utility-focused web apps that address a real user need, support the use of Pi cryptocurrency, and are intuitive to use and accessible to everyday people". Prize: "overall prize of 10,000 Pi". |
| Source | https://minepi.com/developers/pi-hackathon/, read 20 Oct 2026. The binding terms are inside Brainstorm (Pi Browser only) and **have not been read**. Read them before submitting. |
| Deadline | "by 11:59pm PST on the last day of each month": Sat 31 Oct, 23:59 Pacific, which is **Sun 1 Nov 07:59 CET** (08:59 CET if PST means UTC−8). **Submit by Fri 30 Oct.** |
| To submit | A link to a demo app on Testnet or Mainnet, a video of at most 3 minutes (YouTube, "Presentation video"), and a description. The app must be deployed on Testnet, outside the sandbox. |
| Gates | Pi SDK only for sign-in and payments; no other login; no non-Pi currency; never ask for a passphrase; collect only what the app needs; English name and description; a logo and preview images; a privacy policy; the app name may not start with "Pi". |

An existing app may enter, and may enter again in a later month after improving.

## 2 · The submission pack's list, and where each item stands

| # | Item from the pack | State in code | Left for the owner |
|---|---|---|---|
| 1 | "Position closed." toast right after opening a trade | **Fixed.** The note of the last action was kept across actions; every action now replaces it, and a tab change clears it. Tested in Chromium. | Re-record the clip. |
| 2 | Header says "FARCASTER · SEASON LIVE" | The label is the season's name, read from the database: the same season shows on /pi, /doge, /arena and the Farcaster app. | **Rename the season** on /arena (operator's Rename control). |
| 3 | Presale banner on pages a reviewer may reach | The Pi app links only to /privacy and /terms, both served script-free and presale-free; a test pins every link the app can draw. | None. |
| 4 | Testnet deployment | Nothing in code depends on the network: the SDK reads it. | Create or confirm the **Pi Testnet** app in the Developer Portal, outside the sandbox, URL verified (`/pi/validation-key.txt` is served from `PI_VALIDATION_KEY`). |
| 5 | Test Pi payments on Testnet | The server orders every payment, approves and completes it with Pi's API, and finishes an interrupted one on the next sign-in. Tips and Coach Plus share that flow. Tested against a stubbed SDK only. | **Pay 1 π on Testnet** end to end, once for a tip and once for Coach Plus, with `PI_NETWORK_API_KEY` set for the Testnet app. |
| 6 | Privacy policy covers Pi data | **Updated** (20 Oct): the Pi uid, the username (kept only if chosen as the board name), payment ids, the coach's daily count and what the coach sends to the AI provider. Truth tests read the built page. | Link https://www.humanoid-traders.com/privacy in the Developer Portal. |
| 7 | Developer Portal listing | | Logo with no Pi trademark, English description (§4), 3 to 5 screenshots: Duel, a receipt check, the coach, Signals, Leaders. |
| 8 | Scopes | The app asks for `username` and `payments` only. `username` offers a board name; `payments` is needed for tips and Coach Plus. | None. |
| 9 | Duel receipt verifiable in the Duel tab | **Built.** A tap on a round's receipt hashes the sealed payload with the browser's own SHA-256, compares it with the seal and replays the day's Merkle proof. | Record it for the video. |

## 3 · What the Pi app does now

- **First screen**: the owl, "Beat the Claw", four lines on what is inside, and Sign in with Pi.
- **Arena**: practice trading at live prices in **practice credits** (never vUSDT in the Pi app), with labelled fields and a one-line explainer of long, short and leverage.
- **Daily Duel**: the day's rounds; the Claw's call hidden until you call; every round sealed when dealt; receipts checked in the app.
- **Ask the Claw** (new tab): an AI coach answering from the reader's own facts (their practice trades, today's card, the card they asked about). A reply is checked before it is shown: no link, no token or presale, no sign-up elsewhere, no exchange keys. Twenty questions a day by default (`PI_COACH_DAILY`).
- **Coach Plus**: more coach questions for a payment in Pi, offered only when the operator sets a price (`PI_COACH_PLUS_PI`; days and daily allowance from `PI_COACH_PLUS_DAYS`, `PI_COACH_PLUS_DAILY`).
- **"Why?" buttons** on every signal card and Duel round open the coach on that card.
- **Signals**: the agent's calls with charts, now with a plain-language line ("BTC rises toward 67,000; it is given up if BTC falls to 64,000").
- **Leaders**: the season's standings and your name on them, chosen in the app. The Pi username is offered and used only if you save it.
- **About**: what the app is, the policies, the language list and the optional tip.
- **Seventeen languages**: the site's fourteen plus Vietnamese, Indonesian and Filipino. The server opens the app in the Pi Browser's language; About switches in place without ending the session. The coach answers in the language on screen.

Still English, and said here so nobody is surprised: the sentences the server sends back when it refuses something (for example a name already taken), and the coach's four fixed answers (a token question, no data feed, a withheld reply, coach unavailable).

## 4 · Brainstorm text (to paste)

**Project name:** RUNECLAW Arena

**One-line summary:** Call the market against an AI, prove every call was locked first, and learn why with an AI coach. Free, in seventeen languages.

**Description:**

> RUNECLAW Arena is a free practice app for Pioneers in the Pi Browser. Most people learn markets with real money and lose it while learning. Here everyone practises with credits, never money, at live prices.
>
> - **Daily Duel:** a few rounds a day. Call each one before the AI, "the Claw", reveals its own call. Every round is sealed with a SHA-256 commitment when it is dealt, and each day's seals are folded into one Merkle root. Tap a receipt to check it in your own browser.
> - **Ask the Claw:** an AI coach that explains a round, a signal or your own practice trades in plain words, in your language.
> - **Practice trading and signals:** open practice positions on live prices, or follow the agent's calls, each with its chart and a plain-language explanation.
> - **Season leaderboard:** ranked on percent return, under a name you choose.
>
> **Pi integration:** Sign in with Pi through the Pi SDK, with no other account and never a passphrase. Payments in Pi through the Pi SDK: Coach Plus (more coach questions a day) and optional tips that unlock nothing.
>
> Education and practice only. Not financial advice.

**App URL:** the Testnet app's URL from the Developer Portal. **GitHub repo:** empty (not PiOS).

## 5 · The video: clips to record again

The pack's 2:11 cut stands, with these swaps (record on a phone in the Pi Browser, Testnet app):

1. The sign-in, now landing on the new first screen.
2. A trade opened: the toast now says "Position opened".
3. A Duel call, then a tap on its receipt: "Verified in your browser".
4. "Why?" on a signal card and the coach's answer; one question in another language.
5. About → a language switch (Vietnamese or Indonesian).
6. A Testnet payment of 1 π for Coach Plus.

Leave out any screen with the presale, the token or staking. The pack's Telegram slot can be dropped.

## 6 · Owner checklist

| When | Task |
|---|---|
| Wed 21 Oct | Read the Brainstorm terms; create the Brainstorm proposal if missing; confirm the Testnet app in the Developer Portal. |
| After the deploy | Rename the season; set `PI_COACH_PLUS_PI` if Coach Plus is to be offered; run the two Testnet payments (§2 row 5). |
| Sat 24 Oct | Developer Portal listing: logo, description, screenshots, privacy policy link. |
| Sun 25 Oct | Record the clips in §5. |
| Tue 27 Oct | Approve the video; upload to YouTube. |
| Wed 28 Oct | Fill the Brainstorm fields; a second Pi account tests the whole flow. |
| **Fri 30 Oct** | **Submit.** |

## 7 · Season prizes in Pi

Not part of this entry. Paying winners from the app needs the app wallet's seed on our server, which the code does not hold and will not without the owner's decision and a legal review of free prize contests. The prize table, the payout plan and the off switch are in [SEASON_PRIZES_PLAN.md](SEASON_PRIZES_PLAN.md).
