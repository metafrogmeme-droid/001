#!/usr/bin/env bash
# RUNECLAW deploy helper — make .env and data/ survive redeploys.
#
# The 2026-07-14 incident: a redeploy re-cloned the repo directory, which
# wiped BOTH the .env (secrets) and ./data (position state, shadow book).
# The keys were hand-re-entered with quotes → Bitget 40006 → a live AMD
# position sat unprotected. Root cause: mutable state living inside the
# redeploy path.
#
# This script keeps the real .env and data/ OUTSIDE the repo (in a
# persistent directory the redeploy never touches) and symlinks them back
# in on every deploy. Run it once after each redeploy, before starting the
# stack, or wire it into your platform's deploy/start hook.
#
#   ./deploy.sh              # uses ~/runeclaw-persist
#   PERSIST_DIR=/srv/rc ./deploy.sh
#
# Idempotent and non-destructive: an existing real .env/data is MOVED into
# the persistent store on first run (never overwritten if already there),
# then symlinked. A symlink already pointing at the store is left alone.
set -euo pipefail

PERSIST_DIR="${PERSIST_DIR:-$HOME/runeclaw-persist}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── The interpreter must match .python-version ──────────────────────────────
#
# Observed 2026-08-17. `pip install -r requirements.lock` on the box failed
# with "no matching distribution" for numpy==2.3.5, and the operator was told
# the pin was wrong and that PyPI's latest was 2.2.6. Neither is true: 2.3.5
# exists, the latest is 2.5.2, and the lock file is correct.
#
# What actually happened is that numpy 2.3.x declares `requires-python >=3.11`,
# so on an older interpreter pip cannot SEE those releases. It reports the
# newest version visible to THAT Python — 2.2.6 — and the message reads as a
# fact about PyPI rather than a fact about the local interpreter. An operator
# following it would have downgraded a correct pin for everybody.
#
# CI validates on 3.11, `.python-version` says 3.11, and pyproject declares
# `requires-python = ">=3.11"`. All three were already right; nothing checked
# them at the moment it mattered. This does, before pip is ever called.
if [ -f "$REPO_DIR/.python-version" ]; then
  want="$(tr -d ' \t\r\n' < "$REPO_DIR/.python-version")"
  have="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
  want_major="${want%%.*}"; want_minor="${want#*.}"; want_minor="${want_minor%%.*}"
  have_major="${have%%.*}"; have_minor="${have#*.}"
  if [ "$have" = "?" ]; then
    echo "  ⚠  python3 not found — cannot verify the interpreter."
  elif [ "$have_major" -lt "$want_major" ] 2>/dev/null \
    || { [ "$have_major" -eq "$want_major" ] && [ "$have_minor" -lt "$want_minor" ]; } 2>/dev/null; then
    echo ""
    echo "  ✗  Python $have is older than the $want this repo requires."
    echo ""
    echo "     Do NOT edit requirements.lock to make the install succeed. pip on"
    echo "     an old interpreter reports the newest version IT can see, which"
    echo "     looks like the pin is wrong when the interpreter is."
    echo ""
    echo "     Install Python $want (pyenv/uv) and re-run this script."
    exit 1
  fi
  echo "  Python $have  (>= $want required)"
fi

echo "RUNECLAW deploy — persisting state to: $PERSIST_DIR"
mkdir -p "$PERSIST_DIR"

link_persistent() {
  # $1 = name of the path under the repo (e.g. ".env" or "data")
  local name="$1"
  local repo_path="$REPO_DIR/$name"
  local store_path="$PERSIST_DIR/$name"

  if [ -L "$repo_path" ]; then
    # Already a symlink — repoint it at the store to be safe and return.
    ln -sfn "$store_path" "$repo_path"
    echo "  $name -> already linked"
    return
  fi

  if [ -e "$repo_path" ] && [ ! -e "$store_path" ]; then
    # First run with a real file/dir in the repo: move it into the store.
    mv "$repo_path" "$store_path"
    echo "  $name -> moved into persistent store"
  elif [ -e "$repo_path" ] && [ -e "$store_path" ]; then
    # Both exist. Normally this is a fresh clone laying its committed copy over
    # a populated store, and the store is authoritative — discard the clone's.
    #
    # But an EMPTY store directory next to a POPULATED repo directory is not
    # that. It is the signature of a store that was created rather than
    # migrated, and discarding the repo copy there destroys the only copy.
    if [ -d "$repo_path" ] && [ -d "$store_path" ] \
       && [ -z "$(ls -A "$store_path" 2>/dev/null)" ] \
       && [ -n "$(ls -A "$repo_path" 2>/dev/null)" ]; then
      ( shopt -s dotglob nullglob; mv "$repo_path"/* "$store_path"/ )
      rm -rf "$repo_path"
      echo "  $name -> store was empty; repo contents migrated into it"
    else
      rm -rf "$repo_path"
      echo "  $name -> repo copy discarded; persistent store kept"
    fi
  fi

  ln -sfn "$store_path" "$repo_path"
  echo "  $name -> linked to persistent store"
}

link_persistent "data"
mkdir -p "$PERSIST_DIR/data"     # AFTER the move, never before
link_persistent ".env"

# logs/ persists for the same reason data/ does, and it is NOT optional.
# logs/audit_chain.jsonl is a TAMPER-EVIDENT chain: losing it does not just
# lose history, it breaks the chain's continuity, which is unrecoverable and
# indistinguishable from tampering. logs/trade.jsonl is the forensic record of
# every decision the bot made.
#
# These were protected only by accident until 2026-08-06 — they were tracked in
# git, so a re-clone restored them. Untracking them (correct: runtime state does
# not belong in the redeploy path) removed that accident without replacing it.
# Same ordering rule as above: link BEFORE the mkdir, or the migrate branch is
# unreachable and the first run deletes what it claims to preserve.
link_persistent "logs"
mkdir -p "$PERSIST_DIR/logs"

if [ ! -e "$PERSIST_DIR/.env" ]; then
  echo ""
  echo "  ⚠  No .env in the persistent store yet."
  echo "     cp $REPO_DIR/.env.example $PERSIST_DIR/.env  and fill in your keys."
  echo "     (No quotes/spaces around values — a quoted key fails auth: 40006.)"
fi

echo "Done. .env and data/ now survive redeploys."
echo ""
echo "  ✔  Secrets vault active: the bot mirrors operator keys (encrypted) into"
echo "     data/secrets_vault.enc and restores them if .env is ever wiped — so"
echo "     long as data/ persists (which it now does), a wiped .env self-heals."
