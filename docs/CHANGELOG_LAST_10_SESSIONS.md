# What changed — 13 to 22 August 2026

Ten working days, **#31 → #133**. Derived from `git log --first-parent` and the
title of each merged branch, not from memory: every line below is a PR that is
on `main`, and the dates are its merge date.

One theme runs through nearly all of it, and it is the rule in `CLAUDE.md`:

> **Unreadable is never zero, and absent is never a measurement.**

Most entries here are one instance of that — a failed read rendered as a
number, a colour, a verdict, or a clean bill of health. The rest are the guards
that were supposed to catch it and could not see far enough.

---

## 22 August — public claims, and two auth defects

| PR | |
|---|---|
| **#133** *(open, draft)* | **There was no way to delete an account.** No route, no SQL, no deactivate flag. `DELETE /api/auth/account` purges the bot **first** and aborts with 502 if it does not confirm every store — the keys that move money live in the bot's vault, so the obvious ordering leaves them behind a message saying the account is gone. Added `UserStore.forget`, `/gateway/account/purge` (per-store outcomes), and a delete form on the account card. |
| **#132** | **A spent backup code came back.** The 2FA backup-code path was a read-modify-write with no transaction, and `consumeBackupCode` returns a *copy* — so two racing logins didn't merely both succeed, the second write **restored the first one's code**. Now a CAS pinned to the bytes that were read, 409 on a lost update. Also: raising the margin cap asked for nothing, under a comment claiming only *lowering* was frictionless. |
| **#131** | **A per-tick cap bounds the width of a burst, not its rate.** Anomaly cards were capped per tick and still produced ~360/hour. Now budgeted per hour: 8 severe cards, worst hour 10 messages total. |
| **#130** | **The privacy policy described a different product.** Written for a single-operator Telegram bot; by August it was describing a multi-user platform with accounts, OAuth, 2FA, wallets and a server-side key vault. Every negative claim it made had become false. Rewritten to state absences *as* absences, and its tests check the page against the **code**, not its wording. |
| **#129** | **The homepage said "human-confirmed" and the code says otherwise** — `auto_confirm_live_enabled` defaults to True. Also retired a stale "23 pre-trade checks". |

## 21 August — a landscape of colour and count defects

| PR | |
|---|---|
| **#128** | A referral perk was a promise the product had no way to keep. Each perk now carries `state: live/planned`. |
| **#127** | The A/B harness crashed on the state it will meet first. |
| **#126** | The agent knew you in the browser and was a stranger on Telegram. |
| **#125** | The agent surface had no front door. |
| **#124** | The landscape had live defects in it — **including on the card just cured**. |
| **#123** | A third of one module's public API is referenced by nothing. |
| **#122** | Three cards folded unreadable closes in as break-even. |
| **#121** | A green build printed over two tests that timed out. |
| **#120** | `[dev-only]` printed for a crate cargo refused to classify. |
| **#119** | The restore drill certified an archive it never opened a file from. |
| **#118** | The cleanup list was an allowlist that a feature outgrew. |
| **#117** | The colour-ternary audit — **all five remaining candidates were defensive**. Not every match is a defect. |
| **#116** | The volume half of the same map, and a safety flag raised from nothing. |
| **#115** | Nine anomaly pages in three minutes, mostly about symbols nobody trades. |
| **#114** | The shared ticker map handed twelve modules a measured zero. |

## 20 August — the deploy that was 255 commits stale

| PR | |
|---|---|
| **#113** | **"Orders fetch not critical"** — true of the listing, false of every claim built on it. One failed `fetch_open_orders` rendered **SL None** ("unprotected") for every orphan position at once. `sl_order` is three-valued now. |
| **#112** | A crashed escape planner told the operator the book was flat — an all-clear on the emergency-exit screen, assembled from a failure. |
| **#111** | An unreadable 24h move became a published SHORT trade plan. |
| **#110** | Bump the strengthmap bundle for the hover fix. |
| **#109** | Unreadable market data became a green LONG, and a calm market. |
| **#108** | "already linked", while `data/` changed which store it points at. |
| **#107** | The deploy gate resolved the repo from the caller's shell. |
| **#106** | **255 commits stale, and every check passed.** `origin` on that box is a GitLab mirror. Produced `verify_deploy_source.sh` and the rule: **fetch by URL, never reset to a remote-tracking ref.** |
| **#105** | `pct(null)` was `+0.00%`, in green. |
| **#104** | A volume spike with no measured move was scored as a bearish one. |
| **#103** | The section that must never be blank could be blanked by any error. |
| **#102** | The test guarding against unfounded verdicts was **requiring** one. |
| **#101** | The users are in `users.json`, and the guard written to protect it could not see it. |
| **#100** | The users were never deleted; the bot was reading a different file. |

## 19 August — provenance in the reasoning chain

| PR | |
|---|---|
| **#99** | Probe the self-hosted model endpoint, and page when it goes away. |
| **#98** | Chat quoted prices from the model's **weights**; now it quotes the feed. |
| **#97** | Two operator switches, landed at the level they belong at. |
| **#96** | The seal kept the conclusion and threw away the derivation. |
| **#95** | **Three different risk-check counts, none derived from anything.** `_TOTAL_RISK_CHECKS = 23` drifted downward while the engine emits 36 labels, and was asserted on eleven-plus surfaces. The number is gone; the per-trade count that is actually measured stays. |
| **#94** | 59.3% of a decision, attributed to nothing, sealed into the hash chain. |
| **#93** | Everything scored 1.00, so the digest never saw anything. |
| **#92** | The memory said "executed successfully" and the model filled in the rest. |
| **#91** | The retry was an order of magnitude shorter than the outage it retried through. |
| **#90** | The bot said why, and the panel said "Couldn't load this panel." |

## 18 August — navigation, and features nobody could find

| PR | |
|---|---|
| **#89** | The suppression key was built out of the churn it was meant to survive. |
| **#88** | A void opened between the nav rail and the content, and grew with the screen. |
| **#87** | One market event, dozens of pages — the cluster covered one type of four. |
| **#85** | The reason for a call was the one part you still had to take on trust. |
| **#84** | **Nine built features looked missing** because fourteen pages lived in a footer wall. |
| **#86** | Record RUSTSEC-2026-0258 (h2, dev-only) — no reachable upgrade exists. |
| **#83** | A bento above the scroll, pointing at things that already worked. |
| **#82** | Two guards caught the fix, and **one of them had been defending the error**. |

## 17 August — accessibility, and the signal eating deploys

| PR | |
|---|---|
| **#81** | Fifty-six form controls were announced as "edit text". |
| **#80** | The gate called a real failure "flaky" because the source moved under it. |
| **#79** | Charts, a 20x fill kept open, a 90s silence, and a stop that was never hit. |
| **#77** | Split `/explore` off the landing page. |
| **#76** | Three weights instead of fifteen equals. |
| **#75** | Depth, a nav that fits, and the signal that has been eating deploys. |
| **#74** | A practice sandbox that is honest about not being the Arena. |
| **#72** | Give the landing page a table of contents, and keep it honest. |
| **#71** | Two deploy-path fixes: a TLS request silently ignored, and a gate that could not say "I don't know". |

## 16 August — reachability, and the Arena over MCP

| PR | |
|---|---|
| **#70** | Publish the engineering standard, and link both documents from a page. |
| **#69** | Let agents compete: the paper Arena over MCP. |
| **#68** | Make the red team runnable, and gate CI on it. |
| **#67** | **The reachability detector was falsely accusing ten live modules** — it scanned only `bot/` and `scripts/`, and `api_bridge.py` at the repo root was never read. A reachability checker with a blind spot manufactures the accusation it exists to prevent. |
| **#66** | Five doors on the landing page — added, not traded for. |
| **#65** | Fix the logged-in dashboard that says log in, and ratchet the cause. |
| **#64 / #63** | Signing slice 2 — wire the path; "simulate" was describing the wrong thing. |
| **#62** | **The ratchet caught its own author.** |
| **#61** | There is no meme execution path — wire the preflight that was actually there. |
| **#60** | The cookie migration broke six surfaces I never checked, and my test missed it. |
| **#59** | Wire the integrity veto in shadow, and stop `clear` meaning "nothing checked". |
| **#58** | **A module nothing calls is indistinguishable from one that does not work.** `token_dossier`, `presale_claims`, `deployer_history` — pure, correct, seventy-seven tests, and imported by **zero** non-test modules. Produced `test_no_new_unreachable_modules.py` and its baseline. |
| **#57 / #56** | Detective slices 3–4: curated-list checks, and both lists shipped empty. |

## 15 August — CSP, sessions, and the detective

| PR | |
|---|---|
| **#55 / #54** | M14: retire `'unsafe-inline'` from `script-src`; the token stops being written where a script can read it. |
| **#53** | Six read failures that rendered as measurements, and two ranked books nobody could price. |
| **#52** | Wire the detective to real data, and name every gap it cannot fill. |
| **#51** | A session that cannot be ended, and a short token that made itself long. |
| **#50** | Halt must still mean halt once the order is already being prepared. |
| **#49** | `/users` printed an id the operator could not use. |
| **#48** | Refuse to migrate under a live bot, and read the result back off disk. |
| **#47** | A web account's missing `admitted_by` is evidence, not a shrug. |
| **#46** | Migrate the accounts the **door** admitted, not the ones a human vouched for. |
| **#45–#42** | Detective slices 0–3: a verdict travels with the basis it rests on; a claim nobody checked is not a claim that passed. |

## 14 August — the admission door

| PR | |
|---|---|
| **#41** | M1+M3: the module the image runs by default was never scanned. |
| **#40** | `/health` reported a healthy, flat, unblocked bot **with no engine at all**. |
| **#39** | H1: rotating one header bought a fresh rate-limit and lockout bucket. |
| **#38** | H3: typing three words reached the kill switch H4 had just gated. |
| **#37** | H4 (second half): a vouched-for trader could still stop the whole bot. |
| **#36** | H4: a stranger's first message bought them the operator's kill switch. |
| **#35** | docs: re-verify `db.js` findings against current main. |
| **#34** | M18: the test shim answered questions it did not understand, with "nothing". |
| **#33** | M10: a production deployment that quietly became an amnesiac. |
| **#32** | M12: **"log out" forgot the token and revoked nothing** — no logout route existed anywhere in `app/`; the client dropped the token and called it done. |

## 13 August

| PR | |
|---|---|
| **#31** | H7: the engine started a dashboard generation nothing deploys. |

---

## What is now permanently guarded

Machinery added over these ten days, and what each one refuses:

| gate | refuses |
|---|---|
| `scripts/preflight.py` | Runs what CI runs by **parsing `ci.yml`**, so a new CI step is a new local step for free. 14 gates, ~14 min. Names the jobs it *cannot* run rather than implying coverage. |
| `scripts/ci_test_gate.py` + `tests/known_failures.txt` | A baseline entry that starts **passing** is a hard failure — stale entries cannot hide real bugs. |
| `tests/test_no_new_unreachable_modules.py` | A new module nobody imports. Ratchets both ways: an entry that leaves must be deleted in the same commit. **14 modules** in the baseline today. |
| `app/test/panel_failure_honesty.test.js` | Structurally, across every `renderPanel` loader: guard or omit, never neither. |
| `app/test/cache_buster_ratchet.test.js` | A changed bundle whose `?v=` did not move — the file would never reach a returning browser. |
| `scripts/verify_deploy_source.sh` | A deploy on code that is not what the remote says. Four outcomes, because *could not check* is not *passed*. |
| `scripts/verify_bot_alive.sh` | `DEPLOY_DONE` over a process that is not running. Treats a zombie as dead. |
| `tests/test_claude_md_accuracy.py` | The gate count in `CLAUDE.md`'s own prose. It has fired three times. |
| `tests/test_no_hardcoded_risk_check_count.py` | Any of 13 surfaces stating a risk-check total. Paired with a control that the fail-closed claim is still **made**. |
| Red team | 30 adversarial scenarios against the live risk engine, in CI. |

## Practices these ten days produced

- **Ask which OTHER surface makes the same claim** before calling a fix done. Five of ten PRs in one sweep came from auditing the previous one.
- **Write the assertion, then re-run the search.** Three times a test written for known sites failed on sites the original grep could not reach.
- **Then check reachability before fixing.** `#117` audited five colour ternaries and all five were correct. Removing a true statement to satisfy a rule about false ones is the more expensive mistake.
- **Strip comments before scanning source.** A comment that quotes the string it forbids is indistinguishable from the code doing it. Hit repeatedly, most recently while parsing `db.js`, whose `describeSql` docstring quotes `'CREATE TABLE IF NOT EXISTS users (…'`.
- **Asserting a short string is ABSENT is the assertion that keeps misfiring** — `"to liq"` matched "sits **to liq**uidation"; `"0.0%"` matched "(default 1**0.0%**)".
- **When there is no seam, make one** — and then *exercise* it. `#113`'s row builder was covered only by grep; extracting it caught four defects the same afternoon.
- **Mutation-test, and commit first.** Reintroduce the defect and confirm a *named* test fails.
