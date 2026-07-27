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
#   ./scripts/backup_data.sh [backup_dir]     # default: ./backups
#
# Cron example (daily at 04:10, keep the retention below):
#   10 4 * * * cd /path/to/RUNECLAW && ./scripts/backup_data.sh >> backups/backup.log 2>&1
#
# Restore:
#   tar -xzf backups/runeclaw-data-<stamp>.tar.gz     # extracts data/
#
# Ship the backup directory offsite (rsync/rclone/object storage) — a backup
# on the same disk as the bot only protects against fat fingers, not the host.
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${1:-backups}"
KEEP=14                      # retain this many most-recent archives

if [ ! -d data ]; then
    echo "[backup] no data/ directory — nothing to back up"
    exit 0
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/runeclaw-data-$STAMP.tar.gz"

# --ignore-failed-read: a file mid-rotation must not kill the backup.
# umask BEFORE tar, not chmod after it. `chmod 600` on the following line
# closes the permissions but not the window: tar creates the archive with
# the ambient umask (0644 on a default system) and it sits readable until
# the chmod lands. The archive contains data/ wholesale — the Fernet
# key, .jwt_secret, the encrypted credential blobs, and
# data/attestation_key.bin, the Ed25519 signing key that is 0600 in place
# and would be world-readable inside a 0644 tarball.
(umask 077
 tar --ignore-failed-read -czf "$ARCHIVE" data/)
chmod 600 "$ARCHIVE"   # belt and braces; the umask above already did it

SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "[backup] wrote $ARCHIVE ($SIZE)"

# Retention: drop archives beyond the newest $KEEP.
ls -1t "$BACKUP_DIR"/runeclaw-data-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
    echo "[backup] pruned $old"
done
