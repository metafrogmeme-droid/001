"""Data durability (MH4) — rotating, verifiable backups of irreplaceable state.

What cannot be regenerated if lost: the audit hash-chain (tamper-evidence),
the Ed25519 attestation key (identity — a new key stales the on-chain
anchor), sealed publications, anchor records, learning state, and the local
DB/vault files. Everything else (market data, caches) rebuilds itself.

Design:
- ``create_backup()`` tars the critical set into ``data/backups/`` with a
  sidecar manifest of per-file SHA-256 hashes, then rotates (oldest beyond
  BACKUP_KEEP deleted). Fail-soft: a missing file is recorded as absent,
  never fabricated.
- ``verify_backup()`` re-hashes every member against the manifest — the same
  "re-derive, don't trust" rule as Proof-of-PnL.
- Restore is deliberately MANUAL (docs/DURABILITY.md): the bot never
  overwrites its own live state from an archive.
- ``maybe_daily_backup()`` is called opportunistically (publish scheduler);
  throttled to one per BACKUP_INTERVAL_H via a stamp file.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import tarfile
import time
from pathlib import Path
from typing import Optional

from bot.utils.paths import REPO_ROOT, env_state_path

_CRITICAL = [
    "logs/audit_chain.jsonl",
    "data/attestation_key.bin",
    "data/anchor_state.json",
    "data/proofofpnl_publication.json",
    "data/runeclaw.db",
    "data/secrets_vault.enc",
    # Every linked user's exchange api_key/api_secret/passphrase and their
    # Hyperliquid/Paradex agent private keys (bot/core/exchange_credentials.py
    # `_CREDS_FILE`). Written on every /connect and every website credential
    # pull. It was absent while `secrets_vault.enc` — the operator's own
    # encrypted secrets, the same shape of file — was present, so a restore
    # came back with the operator's keys and none of the users'.
    #
    # NOT SUFFICIENT ON ITS OWN, and deliberately left that way: the Fernet
    # master key that opens BOTH files is `data/.exchange_secret.key`
    # (exchange_credentials.py `_KEY_FILE`, and secrets_vault.py
    # `_MASTER_KEY_BASENAME` — one key, both stores), and it is still not
    # archived. An off-host restore therefore yields ciphertext nothing can
    # read. Putting the key in the same archive as the data it opens is a
    # security trade-off for a human to make, not an audit fix — see
    # audit/verified_findings.md RC-2026-008.
    "data/exchange_creds.enc",
    "data/shadow_book.json",
    "data/proactive_watch.json",
    "data/venue_override.json",
    "data/catalog_seen.json",
]
_CRITICAL_GLOBS = ["data/learning/*", "data/portfolio_*", "data/risk_state_*"]


#: Same anchor as bot/db/models.py, for the same reason. Every path in
#: `_CRITICAL` is relative, `critical_paths` defaults `root` to ".", and
#: `_backup_dir` defaulted to a relative "data/backups" — so a process started
#: from the wrong working directory found none of the critical files,
#: `p.is_file()` was False for all of them, and it wrote a valid archive with
#: an EMPTY manifest into a directory nobody looks in, reporting success.
#:
#: The per-file honesty was already there ("a missing file is recorded as
#: absent, never fabricated"); what was missing is that all-absent is not a
#: backup, and the whole set going absent at once is a configuration fault
#: rather than a run with nothing to save.
logger = logging.getLogger(__name__)


def rootp_of(root: str = "") -> Path:
    """The directory the critical set is resolved against. One definition, so
    the message naming it and the code reading it cannot disagree."""
    return Path(root).expanduser() if root else REPO_ROOT


def _backup_dir() -> Path:
    return env_state_path("BACKUP_DIR", "data/backups")


def _keep() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_KEEP", "14")))
    except ValueError:
        return 14


_ENV_OVERRIDES = {
    "data/anchor_state.json": "ANCHOR_STATE_PATH",
    "data/proofofpnl_publication.json": "PROOFOFPNL_PUBLICATION_PATH",
}

#: Critical files this backup deliberately does NOT archive, and why.
#:
#: RC-2026-008(b). `data/.exchange_secret.key` is the Fernet master key, and it
#: opens BOTH encrypted stores -- `exchange_credentials._KEY_FILE` and
#: `secrets_vault._MASTER_KEY_BASENAME` are the same file. Without it an
#: off-host restore yields ciphertext nothing can read: a vault whose every
#: entry fails to decrypt, and a bot that boots with none of its exchange
#: credentials.
#:
#: Archiving a key beside the data it opens is a security trade-off for a
#: human to make, so it is NOT made here. What IS fixed is that the dependency
#: was SILENT: the manifest now says the archive is incomplete without it, so
#: whoever runs the restore learns it before the decrypt fails rather than
#: after. `data/attestation_key.bin` is already archived, so key material in
#: the archive is established practice here rather than a new precedent --
#: noted for whoever decides.
_EXTERNALLY_MANAGED = {
    "data/.exchange_secret.key":
        "Fernet master key for secrets_vault.enc AND exchange_creds.enc. NOT "
        "in this archive by design. A restore without it cannot decrypt "
        "either store. See audit/verified_findings.md RC-2026-008(b).",
}


def _state_dir_twin(rel: str) -> Optional[str]:
    """The `RUNECLAW_STATE_DIR` location of a `data/...` entry, if that is set.

    RC-2026-008(c). `secrets_vault` and `exchange_credentials` both resolve
    through `RUNECLAW_STATE_DIR`, while every entry in `_CRITICAL` is a literal
    `data/...`. On a deployment that sets it, `critical_paths` looked for
    `data/secrets_vault.enc`, found nothing, and -- filtering on `is_file()` --
    skipped it WITHOUT COMPLAINT. Reproduced: both credential stores dropped
    out of the archive and the run reported success.

    Both locations are searched rather than one redirected, because not every
    `data/` writer honours the variable, and a redirect would trade one silent
    miss for another.
    """
    sd = (os.environ.get("RUNECLAW_STATE_DIR") or "").strip()
    if not sd or not rel.startswith("data/"):
        return None
    return os.path.join(sd, rel[len("data/"):])


def critical_status(root: str = "") -> tuple[list[Path], list[str]]:
    """(found, missing). One resolution, so the archive and the manifest agree.

    `missing` is the honest half: a critical file that is not there is a fact
    about this backup, and it used to be recorded nowhere. Only the ALL-absent
    case was reported, so an archive missing exactly the two credential stores
    came back as an unqualified success.
    """
    rootp = rootp_of(root)
    found: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for rel in _CRITICAL:
        env_key = _ENV_OVERRIDES.get(rel)
        actual = os.environ.get(env_key, rel) if env_key else rel
        cands = [actual]
        twin = _state_dir_twin(rel)
        if twin:
            cands.append(twin)
        hit = False
        for cand in cands:
            p = Path(cand) if os.path.isabs(cand) else rootp / cand
            if p.is_file() and str(p) not in seen:
                seen.add(str(p))
                found.append(p)
                hit = True
        if not hit:
            missing.append(rel)
    for pat in _CRITICAL_GLOBS:
        for m in sorted(glob.glob(str(rootp / pat))):
            if Path(m).is_file() and m not in seen:
                seen.add(m)
                found.append(Path(m))
    return found, missing


def critical_paths(root: str = "") -> list[Path]:
    """Resolve the critical set. `root` defaults to the REPO ROOT, not "." —
    a cwd-relative default meant the backup contents depended on who launched
    the process."""
    return critical_status(root)[0]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(root: str = "", now: Optional[float] = None) -> tuple[Path, dict]:
    """Snapshot the critical set. Returns (archive_path, manifest)."""
    ts = int(now if now is not None else time.time())
    dest = _backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    name = f"runeclaw-backup-{ts}"
    archive = dest / f"{name}.tar.gz"
    files, missing = critical_status(root)
    manifest = {
        "created_at": ts,
        "files": {},
        # RC-2026-008. What is NOT in here is part of the record. A restore
        # operator reading only `files` cannot tell a complete archive from
        # one that quietly skipped the credential stores, and that is the
        # difference between a working restore and a bot with no keys.
        "missing": missing,
        "complete": not missing,
        "externally_managed": _EXTERNALLY_MANAGED,
        "note": "verify with bot.utils.backup.verify_backup — hashes are "
                "re-derived from the archive, never trusted from this file. "
                "`missing` lists critical paths that were not found; "
                "`externally_managed` lists what this archive deliberately "
                "does not contain and cannot be restored without.",
    }
    with tarfile.open(archive, "w:gz") as tar:
        for p in files:
            # tarfile strips a leading "/" from member names on write — use
            # the stripped form everywhere so verify's member.name matches.
            rel = str(p).lstrip("/")
            manifest["files"][rel] = _sha256(p)
            tar.add(p, arcname=rel)
    # AN EMPTY BACKUP IS NOT A BACKUP. Per-file absence is recorded honestly
    # and always has been, but the whole critical set going absent at once is
    # not a quiet run with nothing to save — it is the process looking in the
    # wrong place, and it used to return an archive and a success.
    if not files:
        logger.error(
            "BACKUP CAPTURED NOTHING — none of the %d critical paths exist "
            "under %s. This archive is empty; nothing has been backed up.",
            len(_CRITICAL), rootp_of(root))
    elif missing:
        # PARTIAL IS NOT COMPLETE, and only the all-absent case was reported.
        # An archive missing exactly `secrets_vault.enc` and
        # `exchange_creds.enc` -- which is what a RUNECLAW_STATE_DIR
        # deployment produced -- came back as an unqualified success.
        logger.warning(
            "BACKUP IS PARTIAL — %d of %d critical paths were not found under "
            "%s and are NOT in this archive: %s. If RUNECLAW_STATE_DIR is set, "
            "check it points where the stores actually write.",
            len(missing), len(_CRITICAL), rootp_of(root), ", ".join(missing))
    (dest / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    _rotate(dest)
    return archive, manifest


def _rotate(dest: Path) -> None:
    archives = sorted(dest.glob("runeclaw-backup-*.tar.gz"))
    excess = len(archives) - _keep()
    for old in archives[:max(0, excess)]:
        try:
            old.unlink()
            side = old.with_name(old.name.replace(".tar.gz", ".manifest.json"))
            if side.exists():
                side.unlink()
        except OSError:
            pass


def list_backups() -> list[dict]:
    dest = _backup_dir()
    out = []
    for a in sorted(dest.glob("runeclaw-backup-*.tar.gz"), reverse=True):
        side = a.with_name(a.name.replace(".tar.gz", ".manifest.json"))
        n_files = None
        try:
            n_files = len(json.loads(side.read_text()).get("files", {}))
        except Exception:
            pass
        out.append({"name": a.name, "size_bytes": a.stat().st_size, "files": n_files})
    return out


def verify_backup(archive: Path | str) -> tuple[bool, list[str]]:
    """Re-hash every archive member against the manifest. (ok, problems)."""
    archive = Path(archive)
    problems: list[str] = []
    side = archive.with_name(archive.name.replace(".tar.gz", ".manifest.json"))
    try:
        manifest = json.loads(side.read_text())
    except Exception:
        return False, ["manifest missing or unreadable — cannot verify"]
    want: dict = manifest.get("files", {})
    seen = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                seen.add(member.name)
                f = tar.extractfile(member)
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
                if member.name not in want:
                    problems.append(f"unexpected member not in manifest: {member.name}")
                elif h.hexdigest() != want[member.name]:
                    problems.append(f"HASH MISMATCH: {member.name}")
    except Exception as exc:
        return False, [f"archive unreadable: {exc}"]
    for missing in sorted(set(want) - seen):
        problems.append(f"missing from archive: {missing}")
    return (len(problems) == 0), problems


def maybe_daily_backup(root: str = "", now: Optional[float] = None) -> Optional[Path]:
    """Opportunistic throttled backup (called from the publish scheduler).
    Fail-soft by contract: callers wrap in try/except."""
    try:
        interval_h = float(os.environ.get("BACKUP_INTERVAL_H", "24"))
    except ValueError:
        interval_h = 24.0
    if interval_h <= 0:
        return None
    ts = now if now is not None else time.time()
    stamp = _backup_dir() / ".last_backup"
    try:
        last = float(stamp.read_text().strip())
    except Exception:
        last = 0.0
    if ts - last < interval_h * 3600.0:
        return None
    archive, _ = create_backup(root, now=ts)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(ts))
    return archive
