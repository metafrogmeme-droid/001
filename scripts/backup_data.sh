#!/usr/bin/env bash
# RUNECLAW data backup (ops tip #9).
#
# Everything the bot cannot regenerate lives in data/: open/closed positions,
# risk state, the learning store (decisions, outcomes, calibration curves,
# voter weights, recorded LLM/order-flow snapshots) and the web auth secret.
# The learning loop's value COMPOUNDS in these files — losing them resets
# months of accumulated evidence.
#
# Usage:
#   ./scripts/backup_data.sh [backup_dir]      # default: ~/runeclaw-backups
#   ./scripts/backup_data.sh --verify-restore  # drill: prove an archive restores
#
# Cron example (daily at 04:10):
#   10 4 * * * cd /path/to/RUNECLAW && ./scripts/backup_data.sh >> ~/runeclaw-backups/backup.log 2>&1
#
# Restore:
#   tar -xzf ~/runeclaw-backups/runeclaw-data-<stamp>.tar.gz    # extracts data/
#
# The default lives OUTSIDE the repo, and that is deliberate. It used to be
# ./backups — inside a working tree whose deploy path runs `git reset --hard`,
# one `git clean -fdx` from gone, and on a box where data/ is itself a symlink
# into a persistent store the repo does not own. On 2026-08-07 the operator's
# box was found with the entire runtime state deleted and NO archive on any
# path: not ./backups, not the persist dir. The only recovery mechanism did
# not exist, which is worse than the "never restored" the runbook recorded.
#
# Still ship it offsite (rsync/rclone/object storage) — an archive on the same
# disk as the bot only protects against fat fingers, not the host.
set -euo pipefail

cd "$(dirname "$0")/.."

# --verify-restore: the drill. Extracts the newest archive into a temp dir and
# proves the files that matter come back, WITHOUT touching live state. A backup
# that has never been restored is a hypothesis, not a recovery plan.
if [ "${1:-}" = "--verify-restore" ]; then
    BACKUP_DIR="${RUNECLAW_BACKUP_DIR:-$HOME/runeclaw-backups}"
    ARCHIVE="$(ls -1t "$BACKUP_DIR"/runeclaw-data-*.tar.gz 2>/dev/null | head -1 || true)"
    if [ -z "$ARCHIVE" ]; then
        echo "[verify] no archive in $BACKUP_DIR — nothing to restore FROM."
        echo "[verify] that is the finding, not an error: run a backup first."
        exit 1
    fi
    PROBE="$(mktemp -d)"
    trap 'rm -rf "$PROBE"' EXIT
    echo "[verify] restoring $ARCHIVE into $PROBE"
    tar -xzf "$ARCHIVE" -C "$PROBE"
    rc=0
    for f in data/secrets_vault.enc data/runeclaw.db logs/audit_chain.jsonl; do
        if [ ! -e "$f" ]; then continue; fi
        if [ -s "$PROBE/$f" ]; then
            echo "[verify]   OK      $f ($(wc -c < "$PROBE/$f") bytes)"
        else
            echo "[verify]   MISSING $f"
            rc=1
        fi
    done
    # Byte-identity, not just presence: a restore that returns a truncated
    # vault looks like a success until the day it is needed.
    for f in data/secrets_vault.enc data/runeclaw.db logs/audit_chain.jsonl; do
        if [ -f "$f" ] && [ -f "$PROBE/$f" ]; then
            if cmp -s "$f" "$PROBE/$f"; then
                echo "[verify]   IDENTICAL to live: $f"
            else
                echo "[verify]   differs from live (expected if changed since backup): $f"
            fi
        fi
    done
    [ "$rc" -eq 0 ] && echo "[verify] restore drill PASSED" || echo "[verify] restore drill FAILED"
    exit "$rc"
fi

BACKUP_DIR="${1:-${RUNECLAW_BACKUP_DIR:-$HOME/runeclaw-backups}}"
KEEP=14                      # retain this many most-recent archives

if [ ! -d data ]; then
    echo "[backup] no data/ directory — nothing to back up"
    exit 0
fi

# The audit chain rides along. It lives under logs/ (also symlinked), is
# hash-chained from genesis, and does NOT rotate — which made it the only
# surviving record of 119 closes once data/closed_trades.json was deleted. A
# backup that omits the one file capable of reconstructing the others is not a
# backup of the state that matters. trade.jsonl is deliberately NOT included:
# it rotates at 10MB x 5 and would dominate every archive.
CHAIN_ARG=""
if [ -e logs/audit_chain.jsonl ]; then
    CHAIN_ARG="logs/audit_chain.jsonl"
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/runeclaw-data-$STAMP.tar.gz"

# -h (--dereference) is LOAD-BEARING, not a tweak. On a deployed box data/ is
# a symlink into ~/runeclaw-persist/, and `tar -czf ... data/` archives the
# SYMLINK — a 138-byte archive containing one pointer and no data. It exits 0.
# Observed on the operator's box 2026-08-07: a backup that reported success and
# held nothing, on the same day its only recovery mechanism was needed.
#
# The vault check below caught it (the archive listed `data` and not
# `data/secrets_vault.enc`) and failed the run, which is why this is a fix
# rather than a second silent loss. But refusing is not backing up.
#
# --ignore-failed-read: a file mid-rotation must not kill the backup.
# umask BEFORE tar, not chmod after it. `chmod 600` on the following line
# closes the permissions but not the window: tar creates the archive with
# the ambient umask (0644 on a default system) and it sits readable until
# the chmod lands. The archive contains data/ wholesale — the Fernet
# key, .jwt_secret, the encrypted credential blobs, and
# data/attestation_key.bin, the Ed25519 signing key that is 0600 in place
# and would be world-readable inside a 0644 tarball.
(umask 077
 tar -h --ignore-failed-read -czf "$ARCHIVE" data/ $CHAIN_ARG)
chmod 600 "$ARCHIVE"   # belt and braces; the umask above already did it

SIZE=$(du -h "$ARCHIVE" | cut -f1)

# Verify the archive READS BACK before reporting success and before retention
# prunes an older one. A tar that wrote without error but cannot be listed is a
# backup in name only, and the pruning below would trade a good archive for it.
if ! tar -tzf "$ARCHIVE" >/dev/null 2>&1; then
    echo "[backup] FAILED: $ARCHIVE was written but does not read back — keeping"
    echo "[backup] older archives; not pruning."
    exit 1
fi
# Content checks, not just "tar exited 0". A symlink-only archive lists fine.
if [ -e data/secrets_vault.enc ] && ! tar -tzf "$ARCHIVE" | grep -q 'data/secrets_vault.enc'; then
    echo "[backup] FAILED: vault exists on disk but is absent from $ARCHIVE"
    echo "[backup] (a symlinked data/ archived without -h produces exactly this)"
    exit 1
fi
if [ -n "$CHAIN_ARG" ] && ! tar -tzf "$ARCHIVE" | grep -q 'audit_chain.jsonl'; then
    echo "[backup] FAILED: audit chain exists but is absent from $ARCHIVE"
    exit 1
fi

echo "[backup] wrote $ARCHIVE ($SIZE), verified readable"

# Retention: drop archives beyond the newest $KEEP.
ls -1t "$BACKUP_DIR"/runeclaw-data-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
    echo "[backup] pruned $old"
done
