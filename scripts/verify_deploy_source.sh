#!/usr/bin/env bash
# Is the checkout the code you think it is? — a pre-launch gate for deploys.
#
# THE FAILURE THIS EXISTS FOR
#
# On 2026-08-20 a deploy ran
#
#     git fetch origin && git reset --hard origin/main
#
# and reported "Deploy-pull completed successfully". It had reset to a commit
# 255 COMMITS STALE. `origin` on that box points at a GitLab mirror; the real
# repository is a remote named `backup` on GitHub. Every check the deploy ran
# passed, because each was true of the stale tree: the pull "worked", the
# symlinks were right, the user store loaded, 18 users were present. The only
# thing wrong was WHICH CODE, and nothing asked.
#
# Restarting on it would have produced the worst kind of outcome — the new
# configuration applied to the old binary, so seven merged fixes looked
# deployed and none were, with the operator's own verification steps agreeing.
#
# WHY IT CANNOT BE FIXED BY NAMING THE RIGHT REMOTE
#
# "use `backup` instead of `origin`" is advice, and advice is what failed. A
# remote NAME is a local nickname: it is per-machine, silently editable, and
# means nothing outside the box you are standing on. So this check never reads
# a remote name at all. It asks the URL directly with `git ls-remote`, which
# consults no local ref, so there is no stale-ref path to fall down.
#
# That also sidesteps the trap CLAUDE.md already records: `git fetch origin
# main` updates FETCH_HEAD but leaves refs/remotes/origin/main untouched, so
# even a correct fetch can be followed by a reset to a stale ref.
#
# FOUR OUTCOMES, BECAUSE "COULD NOT CHECK" IS NOT "PASSED"
#
#   0  HEAD is exactly the branch tip at that URL
#   1  HEAD differs — behind, ahead, or diverged. Do not deploy.
#   2  usage error: NOTHING WAS CHECKED
#   3  could not determine: no network, not a git repo, ls-remote refused.
#      NOTHING WAS CHECKED. This is not a pass and it is not a failure, and
#      collapsing it into either is the defect this repo spends most of its
#      guard tests preventing.
#
# USAGE
#
#   scripts/verify_deploy_source.sh                     # canonical repo, main
#   scripts/verify_deploy_source.sh --branch release
#   scripts/verify_deploy_source.sh --url git@github.com:me/fork.git
#   DEPLOY_SOURCE_URL=... scripts/verify_deploy_source.sh
#   scripts/verify_deploy_source.sh --allow-ahead       # local dev commits ok
#
# In a launcher, gate on it the way DEPLOY_DONE is gated on verify_bot_alive:
#
#   scripts/verify_deploy_source.sh || { echo "WRONG CODE — not starting"; exit 1; }
#
# Dependency-free beyond git itself, so it runs on a deploy host with no Python
# environment, and safe to call from a launcher living OUTSIDE the repo — which
# is where a launcher belongs, since `git reset --hard` overwrites anything
# inside it.
set -uo pipefail

#: The repository this code is FROM. A public clone URL, not configuration:
#: it is the one fact a stale mirror cannot lie about, and hardcoding it is the
#: point — a value read from the box is a value the box can get wrong.
CANONICAL_URL="https://github.com/metafrogmeme-droid/001"

URL="${DEPLOY_SOURCE_URL:-$CANONICAL_URL}"
BRANCH="main"
ALLOW_AHEAD=0
# Default 0: a dirty tree is a real difference between the commit id this
# script reports and the code that will run. Opt out deliberately on a dev box.
ALLOW_DIRTY=0

# `shift 2` with only one token left fails and shifts nothing, so the loop
# spins forever — the hang that bit verify_bot_alive.sh. Check the count first.
while [ $# -gt 0 ]; do
  case "$1" in
    --url|--branch)
      if [ $# -lt 2 ]; then
        echo "SOURCE USAGE: $1 needs a value. Nothing was checked." >&2
        exit 2
      fi
      case "$1" in --url) URL="$2" ;; *) BRANCH="$2" ;; esac
      shift 2
      ;;
    --allow-ahead) ALLOW_AHEAD=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) sed -n '2,60p' "$0"; exit 0 ;;
    *) echo "SOURCE USAGE: unknown argument '$1'. Nothing was checked." >&2; exit 2 ;;
  esac
done

[ -n "$URL" ] || { echo "SOURCE USAGE: empty URL. Nothing was checked." >&2; exit 2; }

# THE CHECKOUT THIS SCRIPT IS PART OF — not whatever the caller's shell happens
# to be standing in.
#
# The first version of this file ran a bare `git rev-parse HEAD`, so git
# resolved the repository from the CALLER'S CWD. That is fatal for the one
# usage this script documents: a launcher living OUTSIDE the repo, which is
# where a launcher belongs. Called that way it answered "not a git repository"
# and exited 3 — a gate that never checks anything is a gate nobody keeps.
#
# And the dangerous half is quieter. If the caller's cwd is a DIFFERENT git
# repo — a dotfiles checkout in $HOME is enough — it would read that repo's
# HEAD, compare it to this project's branch tip, and could return 0. The guard
# written to prevent a false pass could produce one.
#
# deploy.sh:24 already had the pattern and this file did not use it. Found by
# an audit of the deploy path, not by the tests written alongside it, because
# every one of those ran with cwd inside the repo.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
if [ -z "$SELF_DIR" ] \
   || ! REPO_DIR="$(git -C "$SELF_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  echo "SOURCE UNKNOWN: $0 is not inside a git repository." >&2
  echo "  This check reports on the checkout it SHIPS WITH, so it cannot" >&2
  echo "  answer from here." >&2
  echo "  NOTHING WAS CHECKED — this is not a pass." >&2
  exit 3
fi

if ! HEAD_SHA="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null)"; then
  echo "SOURCE UNKNOWN: no commits in $REPO_DIR." >&2
  echo "  NOTHING WAS CHECKED — this is not a pass." >&2
  exit 3
fi

# `git ls-remote <url>` reads the URL and NOTHING local: no remote name, no
# refs/remotes/*, no FETCH_HEAD. That is the entire reason this check exists.
REMOTE_LINE="$(git -C "$REPO_DIR" ls-remote "$URL" "refs/heads/$BRANCH" 2>/dev/null)"
if [ -z "$REMOTE_LINE" ]; then
  echo "SOURCE UNKNOWN: could not read '$BRANCH' from $URL" >&2
  echo "  No network, no access, or the branch does not exist." >&2
  echo "  NOTHING WAS CHECKED — do not read this as up to date." >&2
  exit 3
fi
REMOTE_SHA="$(printf '%s\n' "$REMOTE_LINE" | awk 'NR==1{print $1}')"

if [ "$HEAD_SHA" = "$REMOTE_SHA" ]; then
  # A COMMIT ID IS NOT THE WORKING TREE.
  #
  # This asks "is the checkout the code you think it is?" and answered it
  # purely from commit ids — which uncommitted edits do not move. A box
  # patched by hand therefore reported SOURCE OK while running code that is in
  # no commit anywhere, which is the same false provenance the sha was added
  # to prevent, one layer down.
  #
  # bot/utils/build_info.py already knows this and says so: it made `dirty`
  # tri-state because "patching a box by hand is a thing that happens on this
  # project, and a bot reporting 0449bc7 with three hand-edited files is false
  # provenance dressed as precision." The pre-launch gate had not learned it.
  #
  # Tracked modifications AND untracked .py under bot/ both count: a stray
  # module shadowing an import changes what runs without touching any tracked
  # file. `--allow-dirty` exists for a dev box that means it.
  dirty=""
  if ! git -C "$REPO_DIR" diff-index --quiet HEAD -- 2>/dev/null; then
    dirty="$(git -C "$REPO_DIR" diff-index --name-only HEAD -- 2>/dev/null | head -20)"
  fi
  untracked="$(git -C "$REPO_DIR" ls-files --others --exclude-standard -- 'bot/*.py' 'bot/**/*.py' 2>/dev/null | head -20)"
  if [ -n "$dirty$untracked" ] && [ "$ALLOW_DIRTY" -eq 0 ]; then
    echo "SOURCE DIRTY: HEAD matches $BRANCH, but the working tree does not match HEAD." >&2
    echo "  The commit id is right and the CODE IS NOT. What differs:" >&2
    printf '    %s\n' $dirty $untracked >&2
    echo "  Commit or discard these, or pass --allow-dirty if you mean it." >&2
    exit 1
  fi
  if [ -n "$dirty$untracked" ]; then
    echo "SOURCE OK (DIRTY, allowed): HEAD is ${HEAD_SHA%${HEAD_SHA#???????}} — the tip of $BRANCH at $URL"
    echo "  Uncommitted changes are present and were allowed by --allow-dirty."
    exit 0
  fi
  echo "SOURCE OK: HEAD is ${HEAD_SHA%${HEAD_SHA#???????}} — the tip of $BRANCH at $URL"
  exit 0
fi

# Distance is a BONUS, never the verdict. A stale clone usually does not have
# the newer objects, so the honest answer without them is only "these differ" —
# which is all a gate needs, but not what makes a deploy obviously wrong to a
# human at 3am. "255 commits behind" is what made the real incident legible.
#
# So fetch the objects, and fetch them the same way this script tells the
# operator to: BY URL. That writes FETCH_HEAD and no refs/remotes/*, so it
# cannot create the stale ref the whole check exists to defeat. The working
# tree is untouched.
#
# The verdict above does NOT depend on this succeeding. If the fetch fails the
# answer stays "differs", because a failed fetch is not evidence of anything.
if ! git -C "$REPO_DIR" cat-file -e "$REMOTE_SHA" 2>/dev/null; then
  git -C "$REPO_DIR" fetch --quiet "$URL" "$BRANCH" 2>/dev/null || true
fi

DETAIL=""
if git -C "$REPO_DIR" cat-file -e "$REMOTE_SHA" 2>/dev/null; then
  if git -C "$REPO_DIR" merge-base --is-ancestor "$HEAD_SHA" "$REMOTE_SHA" 2>/dev/null; then
    N="$(git -C "$REPO_DIR" rev-list --count "$HEAD_SHA..$REMOTE_SHA" 2>/dev/null || echo '?')"
    DETAIL="BEHIND the branch tip by $N commit(s) — this is STALE code"
  elif git -C "$REPO_DIR" merge-base --is-ancestor "$REMOTE_SHA" "$HEAD_SHA" 2>/dev/null; then
    N="$(git -C "$REPO_DIR" rev-list --count "$REMOTE_SHA..$HEAD_SHA" 2>/dev/null || echo '?')"
    DETAIL="AHEAD of the branch tip by $N commit(s)"
    if [ "$ALLOW_AHEAD" = "1" ]; then
      echo "SOURCE OK (ahead): HEAD is $N commit(s) ahead of $BRANCH at $URL."
      echo "  --allow-ahead was given, so local commits are expected here."
      exit 0
    fi
  else
    DETAIL="DIVERGED from the branch tip"
  fi
else
  DETAIL="differs from the branch tip (objects not present locally)"
fi

echo "SOURCE MISMATCH: this checkout is not $BRANCH at $URL." >&2
echo "  HEAD:   $HEAD_SHA" >&2
echo "  remote: $REMOTE_SHA" >&2
echo "  status: $DETAIL." >&2
echo "" >&2
echo "  A remote NAME is a local nickname and can point anywhere — on the box" >&2
echo "  where this check was written, 'origin' was a mirror 255 commits stale." >&2
echo "  Reset to the URL, not to a name:" >&2
echo "" >&2
echo "      git fetch $URL $BRANCH" >&2
echo "      git reset --hard FETCH_HEAD" >&2
echo "" >&2
echo "  (fetching a URL updates FETCH_HEAD only — no refs/remotes/* is" >&2
echo "  written, so there is no stale ref left to reset to by mistake.)" >&2
exit 1
