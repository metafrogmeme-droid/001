# RUNECLAW repository upgrade plan

**Date:** 2026-09-27
**Repository:** https://github.com/metafrogmeme-droid/001
**Public site:** https://www.humanoid-traders.com
**Scope of this document:** a sequenced plan for aligning the website (`app/`, `site/`, `website/`) and the bot (`bot/`, `api_bridge.py`) with a 2026 AI trading platform. This file records what the tree already pins, what public support schedules say, and the order in which a later change may move those pins.

This document, as written on 2026-09-27, changes no dependency, no runtime, and no trading behaviour. A later phase that bumps a pin is a separate change, re-measured against the gates named below. Phase 3, recorded below, is that later change: it moves the resolved Express tarball in `app/package-lock.json` and leaves the declared range `^4.21.0`.

## How a claim is labelled

| Label | Meaning |
|---|---|
| **REPO-VERIFIED** | Read from a file in this checkout on 2026-09-27. |
| **REGISTRY-VERIFIED** | Read from the publisher named in the citation (PEP, nodejs.org, endoflife.date, the Express blog, the npm `latest` tag) on 2026-09-27. |
| **ASSUMPTION** | A reading that still needs a registry check or a drive before anyone treats it as a target. |

A version that was not looked up this session has no target number in this plan.

## What this file does not replace

These documents already exist. This plan does not restate them and does not supersede them.

| Document | What it owns |
|---|---|
| `docs/ROADMAP.md` | Product and protocol direction (venues, agent intelligence, horizons). |
| `docs/UPGRADES.md` | Execution reliability (idempotency, decimal money, cancellation, observability, the audit chain). |
| `docs/PRODUCT_AUDIT_2026.md` | A product audit dated 2026-06. Line citations and "not wired" claims in it are that date's reading. Later work in the tree has moved several of those items. Treat the June audit as dated. |
| `docs/ULTRA_AI.md` | Model routing already described as an operator addon (Fable / Sonnet behind an admin key). |
| `docs/RUNECLAW_LLM.md` | The LLM call path. |
| `docs/TOKEN_ROADMAP.md` | Token issuance. Value-holding deploy stays behind its own legal and audit gates. |
| `docs/INCOME_MAP.md` | What the product can do, and who may run each command. |
| `docs/DEEP_AUDIT_2026.md`, `docs/MASTER_BUILD_PLAN.md`, `docs/IMPLEMENTATION_BLUEPRINT.md`, `docs/IMPROVEMENT_ROADMAP.md`, `docs/RUNECLAW_v3.4.0_ROADMAP.md` | Existing planning documents. They were not re-audited for this plan. |

`docs/UPGRADES.md` Tier 3 still says tests are not run automatically. **REPO-VERIFIED:** `.github/workflows/ci.yml` runs the lint, type, honesty, test, red-team, and audit jobs described below. That sentence in `docs/UPGRADES.md` is stale relative to the workflow. This plan does not edit `docs/UPGRADES.md`.

## What already ships

**REPO-VERIFIED.** The product is a Python trading bot plus a Node website, with Solana staking, token tooling, and an EVM Rune NFT contract beside them. The license is BUSL-1.1.

Two processes serve production, and the deploy chapter requires both:

| Process | How it starts | Port |
|---|---|---|
| Telegram bot, engine, gateway | `python3 -m bot.main` (`--mode telegram`) | 8080 |
| `api_bridge.py` (uvicorn, one worker) | separate process | 8000 |

`docker-compose.yml` runs `bot`, `api_bridge`, `redis:7-alpine`, and `nginx:1.27-alpine`. The website tree mounted for nginx is `./website`, which the marketing site build writes. The bridge subnet is pinned `172.28.0.0/16` for `TRUSTED_PROXY`. The bot healthcheck reads `/proc/1/cmdline` for `bot[.]main`.

`api_bridge.py` is one worker on purpose: two workers erase the shared paper book.

The marketing site (`site/`) is React 19, Vite 7, TanStack Router, and Tailwind 4, and its build emits static HTML into `website/`. CI checks that the committed `website/` tree matches that build.

Token tooling (`token/`) and the staking program (`programs/rclaw_staking`) are marked draft and devnet-only in their own manifests. They stay out of any phase that puts value on a mainnet program.

## Evidence the gates already hold

**REPO-VERIFIED** from `.github/workflows/ci.yml` (workflow name `CI`; `pull_request`, `push` to `main`, `workflow_dispatch`; concurrency `ci-${{ github.ref }}`).

| Job | What a dependency change has to pass |
|---|---|
| `test` | ruff floors, `scripts/ruff_gate.py`, `scripts/mypy_gate.py`, strict mypy on the money modules, `scripts/honesty_gate.py`, bandit, `scripts/ci_test_gate.py`, `scripts/red_team.py`, `scripts/authority_red_team.py`, then `pip-audit -r requirements.lock`. Timeout 60 minutes. Python 3.11. |
| `staking` | `cargo test` for `rclaw_staking`, clippy `-D warnings`, release build, Anza installer for Solana 1.18.26 (SHA256 pinned), `cargo-build-sbf`, `scripts/build_provenance_gate.py`, cargo-audit 0.22.2 through `scripts/cargo_audit_gate.py`, then `scripts/guard_lint.py`. |
| `anchor-workspace` | `npm ci` at the repo root, `npm run typecheck`. This job does not run `anchor test`. |
| `site` | npm audit ratchet, Vite build, published-output honesty tests, and a clean `git status` for `website/`. Node 24.21.0. |
| `web-app` | `node --check` on every JS file under `app/` (floor 200 files), npm audit ratchet, `npm test`. Node 24.21.0. |
| `token-tooling` | script syntax, offline node tests, a local validator, an on-chain test that fails if it skips, `presale:plan`, npm audit ratchet. Excluded from local preflight because it installs a Solana validator. |
| `rune-nft` | `contracts/rune` tests and its npm audit ratchet. |
| `secrets` | gitleaks 8.28.0 over full history, plus the action on pull requests. |

Local preflight (`python3 scripts/preflight.py`) parses that workflow. A green subset is not a green preflight. Cargo, Solidity, gitleaks, and token tooling still need CI. The ruff and mypy ratchets measure the pins in `requirements-ci.txt` (ruff 0.11.13, mypy 1.15.0). On this class of machine those binaries live in `/usr/local/bin`, and a newer copy earlier on `PATH` makes the ratchet report that it could not check.

When a ratchet count goes down, the baseline is re-recorded in the same commit. Growth fails the gate. A step that could not check is not a pass.

## Current pins

### Python

**REPO-VERIFIED.**

| Source | Pin |
|---|---|
| `.python-version`, CI `python-version`, mypy `python_version`, ruff target | 3.11 |
| `requires-python` | `>=3.11` |
| Dockerfile base | `python:3.11-slim` with digest `sha256:8f64a67710a53a55b8baa3dd37e1a5461e34676deff7a4e6b0e389a8d2a5a4c3` (comment in the file: 2026-05 Debian bookworm) |
| `bot/requirements.txt` and `requirements-ci.txt` (exact) | python-dotenv 1.2.2, pydantic 2.13.3, ccxt 4.5.56, python-telegram-bot 22.7, openai 2.38.0, anthropic 0.104.1, numpy 2.3.5, aiohttp 3.14.3, websockets 16.0 |
| CI also exact | pandas 3.0.5, matplotlib 3.11.1, mplfinance 0.12.10b0, pytest 8.3.5, pytest-asyncio 0.24.0, hypothesis 6.155.7, ruff 0.11.13, mypy 1.15.0 |
| CI floors | fastapi `>=0.115`, uvicorn `>=0.30`, cryptography `>=50`, redis `>=5`, PyJWT `>=2.7`, Pillow `>=10.3` |
| `bot/requirements.txt` floors | cryptography `>=50.0.0`, Pillow `>=10.3.0`, redis `>=5.0.0` |
| `pyproject.toml` optional cryptography | `>=50.0.0` |
| Dockerfile pip floors | fastapi `>=0.115`, uvicorn[standard] `>=0.30` |

`requirements.lock` is the file `pip-audit` reads. Nothing installs it. The Dockerfile and the Makefile install `bot/requirements.txt`. CI installs `requirements-ci.txt`. The lock is a superset of what ships: the twelve bot packages plus fastapi, uvicorn, pandas, matplotlib, and mplfinance. Ten dev packages in `requirements-ci.txt` (pytest and friends, ruff, mypy, bandit, hypothesis, pip-audit, PyJWT) are installed in CI and are not in the lock. The lock header states that PyJWT has no import under `bot/`, `scripts/`, or `api_bridge.py`.

Exact lock pins that differ from the floors above:

| Package | Lock | Floors elsewhere |
|---|---|---|
| cryptography | 50.0.0 | CI `>=50`, bot `>=50.0.0`, optional extra `>=50.0.0` |
| fastapi | 0.141.1 | CI `>=0.115`, Dockerfile `>=0.115` |
| uvicorn | 0.52.3 | CI `>=0.30`, Dockerfile `>=0.30` |
| Pillow | 12.3.0 | `>=10.3` |
| redis | 8.1.0 | `>=5` |

The regenerate command recorded in the lock header is `pip install --upgrade -r bot/requirements.txt && pip freeze > requirements.lock`. A later phase that changes a shipping pin uses that command and re-runs `pip-audit` against the new lock.

**REGISTRY-VERIFIED.** [PEP 664](https://peps.python.org/pep-0664/) schedules Python 3.11 as security-only until approximately October 2027. The 3.11 release series lists 3.11.15 (2026-03-03). python.org describes 3.11 as security fixes only, source-only, with no binary installers after 3.11.9. endoflife.date lists security support through 31 Oct 2027.

A secondary schedule (HeroDevs, not the PEP) places 3.12 security-only through Oct 2028, 3.13 active bugfixes through Oct 2029, and 3.14 (released Oct 2025) active bugfixes through Oct 2030. Compatibility of this tree with 3.12, 3.13, or 3.14 was not measured. Those lines are not targets in this plan.

### Node

**REPO-VERIFIED.** CI `node-version` is `"24.21.0"` for the anchor workspace, the marketing site, the web app, token tooling, and the Rune NFT job. `token/package.json`, `app/package.json`, and `site/package.json` set `engines.node` to `>=24.21.0`.

**REGISTRY-VERIFIED** from the [Node.js release index](https://nodejs.org/en/blog/release) and endoflife.date, read 2026-09-27:

| Line | Status on that date | Security support through | Latest named that day |
|---|---|---|---|
| 20 | ended | 30 Apr 2026 | — |
| 22 | Maintenance LTS | 30 Apr 2027 | 22.23.2 (29 Jul 2026) |
| 24 | Active LTS | 30 Apr 2028 | 24.21.0 (8–9 Sep 2026); the release index called 24.21.0 Latest LTS |
| 26 | Current | 30 Apr 2029 | 26.8.2 Latest Release; LTS start listed around 28 Oct 2026 |

The release index was re-checked on 2026-09-27, the day of the bump. It named 24.21.0 as the Active LTS (released 8 Sep 2026). That is the version pinned in the workflow. Node 26 stays out until the index lists it as LTS.

### Website packages

**REPO-VERIFIED** from `app/package.json` and `site/package.json` (ranges). The `app/` lock was opened for the Express resolution in Phase 3; `site/package-lock.json` was not:

| Tree | Declaration |
|---|---|
| `app/` (`runeclaw-app`) | express `^4.21.0`, ethers `^6.17.0`, mysql2 `^3.11.0`, jsonwebtoken `^9.0.2`, bcryptjs `^2.4.3`, qrcode `^1.5.4`, web-push `^3.6.7`, playwright-core `1.56.1`, comlink `^4.4.2`, override `qs` `^6.16.0` |
| `site/` (`runeclaw-site`) | react and react-dom `^19.2.8`, `@tanstack/react-router` `^1.170.31`, vite `^7.3.6`, typescript `^5.9.3`, tailwindcss `^4.3.3`, `@vitejs/plugin-react` `^5.2.0` |

`site/package-lock.json` was not opened. `app/package-lock.json` was opened for Express only (Phase 3). A phase that bumps either remaining tree starts by reading the lock, not the range.

**REGISTRY-VERIFIED** for Express, read 2026-09-27:

- The npm `latest` tag is 5.2.1.
- An earlier reading of this plan was the GitHub tag v4.22.2 (11 May 2026). npm dist-tag `latest-4` is 4.22.3, published 2026-09-14 by UlisesGascon.
- The Express blog of 2025-03-31 says 5.1.0 became npm `latest`, that 4.x entered maintenance on 2025-04-01, and that 4.x end of life would be no sooner than 2026-10-01. The blog calls that a goal, not a commitment. 2026-10-01 is four days after this plan's date. That post was not re-read this session beyond the sentence already recorded. Re-read the blog before treating 4.x as ended.

Express 5 changes path matching, the query parser, and removed APIs. The migration list belongs to the official v5 guide, read at the time of that slice. This plan does not invent that list.

`site/` is already on the current-generation majors named above. No registry check this session produced a newer target for React, Vite, TanStack Router, or Tailwind, so this plan sets none.

### Bot model SDKs

**REPO-VERIFIED.** `openai==2.38.0` and `anthropic==0.104.1` are exact pins in `bot/requirements.txt`, `requirements-ci.txt`, and `requirements.lock`. `docs/ULTRA_AI.md` describes routing thesis and learning calls toward Claude Fable and scan and chat calls toward Claude Sonnet, behind an admin Anthropic key, with a documented rejection of the thinking parameter on Fable and a fallback when `stop_reason` is a refusal. Dollar prices in that document are that document's claim. They were not re-checked against a price page for this plan.

PyPI was not queried for a newer openai or anthropic release. A later SDK bump re-checks PyPI on the day it is proposed and names that pin in the same commit as the lock update.

### Images

**REPO-VERIFIED.** `redis:7-alpine` and `nginx:1.27-alpine` are tag pins with no digest in `docker-compose.yml`. The Python package `redis==8.1.0` in the lock is the client library. It is a different artifact from the Redis server image. No registry check this session compared `redis:7-alpine` or `nginx:1.27` with a current tag. This plan sets no image digest target.

`docker-compose.yml` starts the bot with `python -m bot.main --mode telegram`. The deploy notes in the repository record that Debian images in this family do not provide the unversioned `python` name. The launcher template uses `python3`.

### Solana, Anchor, Rust

**REPO-VERIFIED.**

| File | Pin |
|---|---|
| `rust-toolchain.toml` | channel 1.94.1, with rustfmt and clippy |
| `Anchor.toml` | Anchor 0.30.1, Solana 1.18.26, cluster devnet, program id `6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW` |
| `programs/rclaw_staking/Cargo.toml` | edition 2021, anchor-lang and anchor-spl 0.30.1, solana-program 1.18 |
| CI staking job | Anza installer for v1.18.26, SHA256 `cec72cde1cf36eb35cd8326245d23af0b6791fab68337c2953e2ca2a40af2c50` (installer entrypoint pin; the comment in CI says this does not pin tarball contents) |
| cargo-audit | 0.22.2 |

The staking job's own comment records the deploy constraint: the Solana 1.18.26 installer bundles cargo 1.75, `Cargo.lock` stays lockfile v3, and a `cargo update` can break SBF deployability while host `cargo test` stays green. The Anchor workspace comment that TypeScript test dependencies are uncommitted may be older than the root `package.json`, which now declares `@coral-xyz/anchor` `^0.30.1`. Re-read `Anchor.toml` against the root manifest before editing either. No registry check this session named a newer Anchor or Solana release as a target.

### Advisory baselines

**REPO-VERIFIED.** These files are ratchets. The date on each is when the count was recorded. A count is a floor the gate enforces, not a statement that the advisory is accepted forever. Where a comment in `ci.yml` disagrees with the JSON, the JSON is the gate.

| Baseline | Recorded | Counts in the file | Comment in `ci.yml` |
|---|---|---|---|
| `./.audit-baseline.json` (Anchor workspace) | 2026-09-09 | critical 0, high 7, moderate 8, low 0. Ids include 1103747, 1113686, 1119440, 1119441, 1130589, 1130736, 1164823, 1164824, 1164825 | says 6 high |
| `app/.audit-baseline.json` | 2026-09-02 | all zeros | says `app/` carries 1 low |
| `site/.audit-baseline.json` | 2026-08-27 | all zeros | agrees (clean) |
| `token/.audit-baseline.json` | 2026-09-04 | critical 0, high 12, moderate 14, low 11 (37). The id list in the file is shorter than 37 because one id can cover more than one severity row | agrees with "0 critical and 12 high (37 total)". An older sentence that said 1 critical and 15 high does not match this file |
| `contracts/rune/.audit-baseline.json` | 2026-08-27 | critical 0, high 1, moderate 0, low 1. Ids 1109537, 1120654 | agrees |
| `.cargo-audit-baseline.json` | 2026-08-18 | 9 advisories | says eight |

Shipped RustSec rows (reachable through non-dev dependencies of the staking program):

- RUSTSEC-2022-0093, ed25519-dalek 1.0.1
- RUSTSEC-2024-0344, curve25519-dalek 3.2.1

The other seven rows in that baseline are marked test-harness only (`ring`, `quinn-proto`, `rustls-webpki`, `h2`). The CI comment's "six test-harness only" is one short of the file (seven harness plus two shipped).

Clearing the two shipped RustSec rows is described in CI as a Solana major bump. Token's 12 high / 37 total is the backlog `docs/TOKEN_ROADMAP.md` requires cleared before a deployment that holds value. The CI comment attributes nearly all of that backlog to the Wormhole SDK.

## Phases

Each phase names the CI job it can move. A ratchet that improves is re-recorded in the same commit. A phase is finished when that job is green for the reason the phase states, including the case where the job could not check.

### Phase 0 — this document

Done when this file is in the tree and the existing CI gates still pass. No pin moves.

Non-goals, for every later phase as well as this one:

- No dependency bump, lockfile regeneration, or image digest change in the change that adds this plan.
- No refactor of `bot/`, `app/`, `site/`, or the contracts.
- No change to which model answers a chat or a thesis (`docs/ULTRA_AI.md` already owns that surface).
- No mainnet or value-holding deploy of `token/` or `programs/rclaw_staking`.
- No edit that plants an honesty-gate shape (an unread measurement coerced to zero) as recommended code.

### Phase 1 — make the floors describe one install

Those three alignments are the floors in the current-pins table: bot and optional cryptography `>=50.0.0`, Dockerfile fastapi `>=0.115` and uvicorn[standard] `>=0.30`, and `docker-compose.yml` `python3 -m bot.main --mode telegram`. The lock pins (cryptography 50.0.0, fastapi 0.141.1, uvicorn 0.52.3) stay until `pip-audit` says one of them must move.

Regenerate `requirements.lock` only if a floor alignment changes a resolved version. Use the command in the lock header. Re-run `pip-audit -r requirements.lock` (the `test` job). If ruff, mypy, or the honesty gate moves, re-record that baseline in the same commit.

**ASSUMPTION:** aligning floors to packages already in the lock does not change runtime behaviour. Confirm by the test job, not by reading the ranges.

### Phase 2 — runtimes, on the support window that is actually open

Python 3.11 remains in security support until about October 2027 (**REGISTRY-VERIFIED**, PEP 664). Staying on 3.11 through the next product year is consistent with that schedule. A move to 3.12 or newer is a measured port (mypy `python_version`, ruff target, wheel availability for the exact pins, the Docker digest), not an emergency. Do not start that port in the same change as Phase 1.

Node:

1. Node 22 still receives security releases through 30 Apr 2027. The move to 24 is the line this phase chose before that date, so CI does not sit on Maintenance LTS by default.
2. CI `node-version` is `"24.21.0"` for `web-app`, `site`, `anchor-workspace`, `token-tooling`, and `rune-nft`. The day-of-bump re-check is the paragraph under the Node table. The workflow pins that version, not the word "latest".
3. Node 26 stays out until the release index lists it as LTS (about 28 Oct 2026). The Node bump is not combined with the Express major.
4. `token/`, `app/`, and `site/` set `engines.node` to `>=24.21.0`, the line CI runs. That field is a manifest alignment. It does not by itself change the container.

**ASSUMPTION:** the app and site test suites pass on Node 24. That is what the phase measures. It is not granted by the support table.

### Phase 3 — the website, Express on its own slice

Executed on `cursor/phase3-express-4-22-3-1da4`, cut from `origin/main` at `a1195078` (Phase 1 merged). CI already uses the Node 24.21.0 line. Local verification runs on Node 24.21.0 / npm 11.19.0.

`site/` stays on the majors already declared (React 19, Vite 7, TanStack Router, Tailwind 4). No registry check this session named a reason to move them.

**REPO-VERIFIED** from `app/package.json` and `app/package-lock.json` after the bump:

- The declared range stays `"express": "^4.21.0"` in `app/package.json` and in the lock root `packages[""].dependencies`.
- `node_modules/express` resolves 4.22.2 → 4.22.3. Integrity `sha512-Bdcs4+3qlpVlx2NRn6fgX2Ue2/gGRaPeawebgclM0ERSCqDpA+owF1fdPwjJUTAJWMTuAaxjDf+hzb0/4eKvvw==`. Tarball `https://registry.npmjs.org/express/-/express-4.22.3.tgz`.
- Express 4.22.3 declares `path-to-regexp` `~0.1.13` (was `~0.1.12`) and `qs` `~6.16.0` (was `~6.15.1`). The installed versions were already `path-to-regexp` 0.1.13 (CVE-2026-4867) and `qs` 6.16.0.
- `qs` 6.16.0 matches `app/package.json` `overrides.qs` `^6.16.0`. The lock has no top-level `overrides` key. `body-parser` 1.20.6 still declares `qs` `~6.15.1`. The override is unchanged.
- Express 5.2.1 remains npm `latest` and stays deferred: the official migration guide, then `app/routes` and `app/lib`, the web-app parse gate, `npm test`, and the public-surface suites (`app/test/public_no_dollars.test.js` and the honesty ratchet). A dependency bump that changes a rendered dollar figure fails those on purpose.
- The 4.x EOL goal "no sooner than 2026-10-01" is a prompt to re-check the maintenance post, not a date this phase treats as ended.
- Public routes stay percent, ratio, and count.

### Phase 4 — bot SDKs, behind the call path that already exists

Re-check PyPI for `openai` and `anthropic` on the day of the bump. The current pins are 2.38.0 and 0.104.1. This plan does not name a newer pin.

The change that bumps either SDK re-runs the tests that cover:

- a thinking parameter the Fable path rejects,
- a refusal `stop_reason` falling back to the rule engine,
- secret redaction on provider errors (`bot/utils/secret_shapes.py` and the generated website vocabulary).

Which model is the default stays where `docs/ULTRA_AI.md` and the config leave it. An SDK bump is not a model change.

ccxt 4.5.56, pydantic 2.13.3, python-telegram-bot 22.7, aiohttp 3.14.3, and websockets 16.0 stay at their exact pins until a separate note names a CVE or a venue bug and re-checks PyPI. They were not queried for this plan.

### Phase 5 — staking, token, NFT, held until they are the subject

These stay draft and devnet. They are not a 2026 production phase for the trading site.

| Gate | Why it waits |
|---|---|
| Staking SBF build | Solana 1.18.26 / cargo 1.75 / lockfile v3. A casual `cargo update` can go green on the host and fail `cargo-build-sbf`. |
| Two shipped RustSec rows | ed25519-dalek and curve25519-dalek. CI describes the fix as a Solana major. |
| Token npm ratchet | 12 high, 37 total, largely the Wormhole SDK. Cleared before any deploy that holds value. |
| `contracts/rune` | 1 high and 1 low in its baseline. Compile and test only; no network in CI. |
| Anchor 0.30.1 / Solana 1.18.26 | No newer target was registry-checked for this plan. A program upgrade is its own slice, with a new installer SHA256 written into `ci.yml` at the same time as the version. |

The placeholder program id is expected until a value-bearing deploy, and the staking job fails that grep only when `RCLAW_PINNED_MINT` is set.

## Deploy notes that a later phase has to keep true

**REPO-VERIFIED** from the compose file and the deploy docs:

- Start both processes. A deploy that starts the bot and leaves `api_bridge.py` down leaves the insight, patterns, and lab panels on 502 while the bot looks healthy.
- Keep `api_bridge` at one uvicorn worker.
- Publish a generated web env. The bot's `BOT_GATEWAY_URL` is loopback on the bot box and is the wrong value inside the web container. `scripts/verify_deploy.sh` asks `/api/public/status` and wants both `bot_gateway` and `api_bridge` reachable.
- `/api/version` reports `build` and `assets` hashes. Both unchanged means the deploy served the previous tree. Bump the `?v=` on every page that references a bundle whose bytes changed.
- Reset to a fetched URL (`FETCH_HEAD`), not to a remote-tracking name. `scripts/verify_deploy_source.sh` distinguishes "could not check" from "stale".
- The Python image digest moves only when the base image is rebuilt on purpose, with the new digest written into the Dockerfile in that commit.

## Order

```text
Phase 0   this plan                          (no pin moves)
Phase 1   cryptography / FastAPI / uvicorn floors, compose python3
Phase 2   Node 24.21.0 in CI and package engines; Python 3.11 stays
Phase 3   Express lock 4.22.2 → 4.22.3 inside ^4.21.0; 5.x stays its own migration
Phase 4   openai / anthropic SDKs after a fresh PyPI read
Phase 5   staking, token, NFT — not in the trading-site year
```

Phases 1 and 2 can be sequenced back to back. Phase 3 is the Express lock bump above (declared range `^4.21.0`, resolved 4.22.3, path-to-regexp ~0.1.13, qs ~6.16.0; Express 5 stays its own migration), cut from `origin/main` at `a1195078`. CI already uses the Node 24.21.0 line. Local tests of Phase 3 run on Node 24.21.0 / npm 11.19.0. Phase 4 waits on its own PyPI read and does not share a commit with Phase 3. Phase 5 waits on a decision to hold value, which this plan does not make.
