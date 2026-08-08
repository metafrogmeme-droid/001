#!/usr/bin/env bash
# Inspect and selectively restore from a GPG-encrypted state archive.
#
# The 03:17 job writes ~/backups/runeclaw-state-<date>.tar.gz.gpg — a
# GPG-symmetric AES256 archive of the whole persist directory. On 2026-08-07
# those archives turned out to be the only full-fidelity copy of the runtime
# state a pytest run had deleted through the data/ symlink, and nobody had
# ever opened one. A backup nobody has restored is a hypothesis.
#
# DEFAULT IS READ-ONLY. Listing requires no target and writes nothing. An
# extract goes to a probe directory, never over live state; installing is a
# separate, deliberate act the operator performs by hand after looking at what
# came out. Reconstructed state silently replacing real state is the failure
# this whole exercise came from.
#
# The passphrase is read from a FILE (default ~/.backup-pass) and never
# appears in argv, where `ps` would show it to every user on the box.
#
# Usage:
#   ./scripts/restore_from_backup.sh --list                  # newest archive
#   ./scripts/restore_from_backup.sh --list  --archive PATH
#   ./scripts/restore_from_backup.sh --extract data/closed_trades.json
#   ./scripts/restore_from_backup.sh --extract-all           # whole tree to the probe
#
# Options:
#   --archive PATH     which archive (default: newest in $BACKUP_GPG_DIR)
#   --passfile PATH    default ~/.backup-pass
#   --probe PATH       where to extract (default: a fresh mktemp -d)
set -euo pipefail

BACKUP_GPG_DIR="${BACKUP_GPG_DIR:-$HOME/backups}"
PASSFILE="${PASSFILE:-$HOME/.backup-pass}"
ARCHIVE=""
PROBE=""
MODE="list"
WANTED=()

while [ $# -gt 0 ]; do
    case "$1" in
        --list)        MODE="list" ;;
        --extract)     MODE="extract"; shift; WANTED+=("$1") ;;
        --extract-all) MODE="extract-all" ;;
        --archive)     shift; ARCHIVE="$1" ;;
        --passfile)    shift; PASSFILE="$1" ;;
        --probe)       shift; PROBE="$1" ;;
        -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
        *)             echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$ARCHIVE" ]; then
    # No pipe into an early-exiting reader: `ls | head -1` makes ls take
    # SIGPIPE, and `set -o pipefail` then reports the pipeline as failed on the
    # healthy path. Same trap that inverted three guards in backup_data.sh.
    mapfile -t _found < <(ls -1t "$BACKUP_GPG_DIR"/*.gpg 2>/dev/null || true)
    if [ "${#_found[@]}" -eq 0 ]; then
        echo "[restore] no .gpg archive in $BACKUP_GPG_DIR"
        echo "[restore] that is the finding, not an error — there is nothing to restore from."
        exit 1
    fi
    ARCHIVE="${_found[0]}"
fi

[ -f "$ARCHIVE" ]  || { echo "[restore] no such archive: $ARCHIVE" >&2; exit 1; }
[ -f "$PASSFILE" ] || { echo "[restore] no passphrase file: $PASSFILE" >&2; exit 1; }

echo "[restore] archive:    $ARCHIVE"
echo "[restore] size:       $(du -h "$ARCHIVE" | cut -f1)"
echo "[restore] modified:   $(date -r "$ARCHIVE" -u '+%Y-%m-%d %H:%M:%S UTC')"

# --passphrase-file, never --passphrase: an argv passphrase is visible in `ps`
# to every user on the box for the lifetime of the process.
_decrypt() {
    gpg --batch --quiet --yes --passphrase-file "$PASSFILE" \
        --pinentry-mode loopback --decrypt "$ARCHIVE"
}

# Member paths are NOT always "data/...". The 03:17 job archives the persist
# directory by name, so its members are "runeclaw-persist/data/...", and a
# --extract of "data/closed_trades.json" matched nothing at all. The --list
# summary appeared to work because it substring-matches, so it printed PRESENT
# for files that could not then be extracted — a check that agreed with the
# thing it was supposed to verify independently, which is the defect this whole
# tool exists to avoid. Detected from the listing rather than assumed.
_detect_prefix() {
    local listing="$1"
    local line
    while IFS= read -r line; do
        case "$line" in
            data/*|logs/*) printf '%s' ""; return ;;
            */data/*|*/logs/*)
                printf '%s' "${line%%/*}/"
                return ;;
        esac
    done <<< "$listing"
    printf '%s' ""
}

if [ "$MODE" = "list" ]; then
    echo "[restore] contents (decrypting to a pipe; nothing is written):"
    echo
    LISTING="$(_decrypt | tar -tzf - )"
    PREFIX="$(_detect_prefix "$LISTING")"
    [ -n "$PREFIX" ] && echo "[restore] member prefix: ${PREFIX} (paths below are relative to it)"
    printf '%s\n' "$LISTING" | sed 's/^/  /' | head -60
    _total=$(printf '%s\n' "$LISTING" | grep -c . || true)
    [ "$_total" -gt 60 ] && echo "  … $((_total - 60)) more"
    echo
    echo "[restore] $_total member(s). Files of interest:"
    for f in data/closed_trades.json data/live_positions.json \
             data/combined_state.json data/risk_state.json data/users.json \
             data/secrets_vault.enc logs/audit_chain.jsonl; do
        # Substring match on a captured string — no pipe, no early exit.
        case "$LISTING" in
            *"$f"*) echo "  PRESENT  $f" ;;
            *)      echo "  absent   $f" ;;
        esac
    done
    _learning="$(printf '%s\n' "$LISTING" | grep -c 'data/learning/' || true)"
    echo "  $_learning file(s) under data/learning/"
    exit 0
fi

[ -n "$PROBE" ] || PROBE="$(mktemp -d)"
mkdir -p "$PROBE"
echo "[restore] probe:      $PROBE   (live state is NOT touched)"
echo

PREFIX="$(_detect_prefix "$(_decrypt | tar -tzf - )")"
[ -n "$PREFIX" ] && echo "[restore] member prefix: $PREFIX"

if [ "$MODE" = "extract-all" ]; then
    _decrypt | tar -xzf - -C "$PROBE"
else
    # --strip-components drops the prefix so the probe always looks like
    # data/... regardless of how the archive was rooted. Callers then need one
    # spelling, not one per backup script.
    PREFIXED=()
    for w in "${WANTED[@]}"; do PREFIXED+=("${PREFIX}${w}"); done
    _strip=0
    [ -n "$PREFIX" ] && _strip=1
    _decrypt | tar -xzf - -C "$PROBE" --strip-components="$_strip" "${PREFIXED[@]}"
fi

echo "[restore] extracted:"
find "$PROBE" -type f | sed "s|^$PROBE/|  |" | head -40
echo
echo "[restore] Nothing has been installed. Review the files above, then copy"
echo "[restore] what you want into place BY HAND, with the bot stopped — a"
echo "[restore] running executor rewrites closed_trades.json on the next close"
echo "[restore] and would overwrite a restore mid-flight."
