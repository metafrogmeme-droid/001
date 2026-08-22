# Deploy

**The code on the box is never the source. Every deploy fetches from git first.**

That is not a style preference. On 2026-08-20 a deploy ran `git fetch origin &&
git reset --hard origin/main`, reported success, and landed on a commit **255
commits stale** — `origin` on that box is a GitLab mirror and the real
repository is a remote named `backup`. Every other check passed, because each
was true of the stale tree: the pull "worked", the symlinks resolved, the user
store loaded, 18 users were present. The only thing wrong was *which code*, and
nothing asked.

So: fetch by **URL**, verify against the **URL**, and never reset to a
remote-tracking ref.

---

## The sequence

Run from the launcher, which lives **outside** the repo — anything inside it is
one `git reset --hard` away from reverting, and that produced ~15 consecutive
silent no-op redeploys on 2026-08-01.

### 1. Get the latest from git — by URL, not by remote name

```bash
git fetch https://github.com/metafrogmeme-droid/001 main
git reset --hard FETCH_HEAD
```

A remote *name* is a per-machine nickname that can point anywhere, so "use the
right remote" is advice, and advice is what failed. Fetching a URL writes
`FETCH_HEAD` and no `refs/remotes/*`, so there is no stale ref left to reset to
by mistake. It also sidesteps the trap that `git fetch origin main` updates
`FETCH_HEAD` while leaving `refs/remotes/origin/main` untouched — so even a
correct fetch can be followed by a reset onto stale code.

### 2. Verify the checkout is what the remote says it is

```bash
scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
```

Reads the URL with `git ls-remote` and consults nothing local. Four outcomes,
because *could not check* is not *passed*:

| exit | meaning |
|---|---|
| 0 | `HEAD` is exactly the branch tip at that URL |
| 1 | differs — behind, ahead, or diverged. **Do not deploy.** |
| 2 | usage error — **nothing was checked** |
| 3 | could not determine (no network, not a git repo, `ls-remote` refused) — **nothing was checked** |

A gate that reads an unreachable network as "up to date" ships stale code on
the one day the network is down.

### 3. Restore persistent state

```bash
./deploy.sh                      # or: PERSIST_DIR=/srv/rc ./deploy.sh
```

Symlinks the real `.env` and `data/` back in from a directory the redeploy never
touches. Idempotent and non-destructive. It exists because a 2026-07-14 redeploy
re-cloned the repo directory and wiped both — the keys were hand-re-entered with
quotes, Bitget returned 40006, and a live AMD position sat unprotected.

### 4. Dependencies — on Python 3.11

```bash
python3 -V                       # must be 3.11.x
pip install -r requirements.lock
```

On an older interpreter, `numpy 2.3.x` declares `requires-python >=3.11` and pip
cannot *see* those releases. It then reports the newest version visible to that
Python and the message reads as a fact about PyPI rather than about the local
interpreter. Following it downgrades a correct pin for everybody.

### 5. Start, and gate `DEPLOY_DONE` on the process still being alive

```bash
nohup python -m bot.main >> bot.log 2>&1 &
scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
echo "DEPLOY_DONE"
```

`python -m bot.main` defaults to `--mode telegram`. It used to default to `cli`,
which finds no TTY and **exits zero** — so a launcher that forgot the flag
printed `DEPLOY_DONE` and left nothing running.

Use `--pid`, not `pgrep -f`: the launcher knows what it started, and a pattern
match also matches the checking script's own command line — the first draft of
that script reported OK for a process that had never existed. It also treats a
**zombie as dead**: `kill -0` succeeds on a defunct process, and since the deploy
script is the unreaped parent, the naive check passes on exactly the failure it
exists to catch.

### 6. Confirm the web deploy actually landed

```bash
node -e "const v=require('./app/lib/version').buildInfo(); console.log(v.build, v.assets)"
```

| `build` | `assets` | means |
|---|---|---|
| moved | moved | full deploy landed |
| moved | unchanged | server-only change |
| unchanged | moved | client-only change |
| unchanged | unchanged | **nothing deployed**, whatever the log says |

A moved `assets` hash is still not a *fetched* file — browsers cache on the `?v=`
in the script tag. Any change to a bundle must bump that query in every page
referencing it; `app/test/cache_buster_ratchet.test.js` fails the build otherwise.

---

## One-liner

```bash
set -euo pipefail
git fetch https://github.com/metafrogmeme-droid/001 main
git reset --hard FETCH_HEAD
scripts/verify_deploy_source.sh   || { echo "WRONG CODE — not starting"; exit 1; }
./deploy.sh
pip install -r requirements.lock
nohup python -m bot.main >> bot.log 2>&1 &
scripts/verify_bot_alive.sh --pid $! || { echo "DEPLOY FAILED"; exit 1; }
echo "DEPLOY_DONE"
```

Keep this file's copy in the launcher outside the repo. The version in here is
the reference; the version that runs must not be reachable by `git reset`.
