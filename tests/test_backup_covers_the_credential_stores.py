"""What the backup set CONTAINS, not just that it round-trips.

`tests/test_backup_durability.py` covers create/verify round-trip, tamper
detection, an honest missing manifest, rotation, the daily throttle and that
restore stays manual. Every one of those passes over whatever `_CRITICAL`
happens to list, so none of them notices a file missing from it.

`data/exchange_creds.enc` was missing. It is written by
`ExchangeCredentialStore._save` on every `/connect` and on every website
credential pull (`bot/utils/credential_pull.py`), and holds each linked user's
exchange `api_key`/`api_secret`/`passphrase` plus Hyperliquid and Paradex agent
private keys. `data/secrets_vault.enc` — the operator's own encrypted secrets,
the same shape of file — has been in the set all along, so this was an
oversight rather than a policy about ciphertext.

`docs/DURABILITY.md` says a backup exists because "a backup on the same disk
protects against bad deploys, not dead disks". A restore that comes back with
zero user credentials satisfies neither.
"""

from __future__ import annotations

from bot.utils import backup


def test_the_per_user_exchange_credential_store_is_backed_up():
    assert "data/exchange_creds.enc" in backup._CRITICAL, (
        "the store holding every linked user's exchange API keys and agent "
        "private keys is not in the backup set; a restore returns a bot with "
        "no user credentials and no way to recover them"
    )


def test_the_operator_secrets_vault_is_still_backed_up():
    # The control. Adding one path must not have displaced another.
    assert "data/secrets_vault.enc" in backup._CRITICAL


def test_critical_status_picks_the_credential_store_up_when_it_exists(tmp_path):
    """Listing a path is not collecting it — `critical_status` filters on
    `is_file()`, so a name in the list that never resolves is indistinguishable
    from one that was never added."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "exchange_creds.enc").write_bytes(b"ciphertext")

    found = {p.name for p in backup.critical_status(str(tmp_path))[0]}
    assert "exchange_creds.enc" in found


def test_an_absent_credential_store_is_skipped_not_an_error(tmp_path):
    """A fresh box has no credentials yet, and that must not break the backup."""
    (tmp_path / "data").mkdir()
    assert backup.critical_status(str(tmp_path))[0] == []
