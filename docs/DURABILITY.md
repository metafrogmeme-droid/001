# Data Durability — backup & restore runbook (MH4)

## What is irreplaceable

| Path | Why it cannot be regenerated |
|---|---|
| `logs/audit_chain.jsonl` | the tamper-evident decision ledger — losing it breaks the hash-chain's history |
| `data/attestation_key.bin` | the Ed25519 signing identity; a new key STALES the on-chain anchor (by design) |
| `data/anchor_state.json` | confirmed on-chain anchor records |
| `data/proofofpnl_publication.json` | the latest sealed publication |
| `data/learning/*`, `data/portfolio_*`, `data/risk_state_*` | learned weights and live risk state |
| `data/runeclaw.db`, `data/secrets_vault.enc` | local DB + encrypted operator secrets |
| `data/exchange_creds.enc` | every linked user's exchange api_key/secret/passphrase and their Hyperliquid/Paradex agent keys |
| **`data/.exchange_secret.key`** | **the Fernet master key that opens BOTH encrypted stores — and it is NOT in the archive. See below.** |

### The key is not in the backup

`data/.exchange_secret.key` is one file serving two stores
(`exchange_credentials.py` `_KEY_FILE` and `secrets_vault.py`
`_MASTER_KEY_BASENAME`). It is deliberately **not** archived: putting a master
key beside the ciphertext it opens is a security trade-off, and this repo has
not made it.

The consequence is the part that used to be silent. An off-host restore —
which is the whole point of a backup, since "a backup on the same disk
protects against bad deploys, not dead disks" — yields a vault whose every
entry fails to decrypt and a bot that boots with **none** of its exchange
credentials. `create_backup()` now records this in the manifest under
`externally_managed`, so it is legible before the restore rather than after.

**Back it up separately, off-host, and confirm you can read it before you need
it.** If you would rather it were in the archive, that is a deliberate change:
add it to `_CRITICAL` and update the manifest note and
`tests/test_backup_reports_what_it_missed.py` in the same commit.

### If `RUNECLAW_STATE_DIR` is set

`secrets_vault` and `exchange_credentials` resolve through it; the backup's
critical set is written as literal `data/...` paths. Both locations are
searched now, and anything not found is listed in the manifest's `missing` and
logged as `BACKUP IS PARTIAL`. Before this, a state-dir deployment silently
archived neither credential store and reported success.

## Backups

- Automatic: one rotating archive per `BACKUP_INTERVAL_H` (default 24h),
  triggered opportunistically by the publish scheduler; `BACKUP_KEEP`
  (default 14) archives retained in `BACKUP_DIR` (default `data/backups/`).
- Manual: Telegram `/backup` (admin) — also `/backup list`, `/backup verify <name>`.
- Every archive has a sidecar manifest of per-file SHA-256 hashes;
  `verify` re-derives every hash from the archive bytes — same rule as
  Proof-of-PnL: re-derive, don't trust.
- **Copy archives off the host.** A backup on the same disk protects
  against bad deploys, not dead disks: `rsync data/backups/ <offhost>:...`

## Restore (manual, deliberate)

The bot never overwrites its own live state from an archive.

1. Stop the bot.
2. Verify first: `python -c "from bot.utils.backup import verify_backup; print(verify_backup('data/backups/<name>.tar.gz'))"` — expect `(True, [])`.
3. Extract over the repo root: `tar -xzf data/backups/<name>.tar.gz -C /path/to/RUNECLAW`
4. **Check the manifest before you trust the archive**: `complete` must be
   `true` and `missing` empty. A partial archive restores cleanly and leaves
   out exactly the files you came for.
5. **Restore `data/.exchange_secret.key` from wherever you keep it** — it is
   not in the archive, and without it steps 6's credential checks cannot pass.
6. Restart, then check: `/status` page components, `/anchor` (identity still
   VERIFIED — proves the attestation key survived), the audit chain tip via the
   flight-recorder view, and **`/livebalance`, which is the only one of these
   that exercises the Fernet key**. The previous version of this list probed
   the attestation key — which IS archived — and nothing needing the master
   key, so it could not have caught the omission above.

The web DB (MySQL/TiDB via `DATABASE_URL`) is external state — use your
provider's snapshot/PITR; this runbook covers the bot host only.
