"""The secrets vault used to ERASE the entries it could not decrypt.

`_load_vault` dropped any entry `cipher.decrypt` refused — logging "stale
master key?" and leaving it out of the returned map — and BOTH write paths then
saved that map WHOLESALE:

    store_secrets      reached by /setexchange, /setgateway and /setllm, which
                       is what an operator runs WHEN A SECRET HAS GONE MISSING.
                       So the destructive path is the recovery path.
    seed_and_restore   runs at boot (bot/config.py, right after load_dotenv) and
                       saves whenever any managed env value differs from the
                       vault's copy — which after a key change is all of them.

`data/secrets_vault.enc` has no .bak anywhere in that module, so one boot or one
admin command was enough to make the loss permanent.

THIS IS THE SAME FAILURE `exchange_credentials._load` WAS HARDENED AGAINST, in
the module that shares its master key through the same loader. That docstring
says it in capitals:

    AN UNREADABLE STORE IS NOT AN EMPTY STORE, AND THE DIFFERENCE IS EVERY KEY
    IN IT.

…and records that `_save` writing `self._enc` wholesale "replaced the real file
with an empty one". The vault never got the equivalent, and it is reached by the
same event: `_load_or_create_master_key` GENERATES a new key when
RUNECLAW_SECRETS_KEY is unset and the data dir was wiped, so every entry stops
decrypting at once with the file itself perfectly readable.

The fix is deliberately NOT the same shape. There the whole file failed to
parse, so a `_load_failed` flag blocking every save was right. Here the file
parses and individual entries fail, so blocking the save would take the vault
offline over one stale key. The opaque ciphertext is carried through instead:
readable entries keep working, unreadable ones stay recoverable, and `/vault`
gains the fourth bucket that says which is which.
"""
from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet

import bot.core.secrets_vault as sv

VAULT = "secrets_vault.enc"
KEYFILE = ".exchange_secret.key"


def _isolate(monkeypatch, tmp_path, enabled="true"):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SECRETS_VAULT_ENABLED", enabled)
    monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
    for k in sv._managed_keys():
        monkeypatch.delenv(k, raising=False)


def _rotate_master_key(tmp_path):
    """What a wiped data dir does: the ciphertext survives, the key does not."""
    (tmp_path / KEYFILE).write_bytes(Fernet.generate_key())


def _raw(tmp_path) -> dict:
    return json.loads((tmp_path / VAULT).read_text(encoding="utf-8"))


def _seed(monkeypatch, tmp_path, **secrets):
    """Write a real vault holding these secrets, then forget the master key."""
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    sv.seed_and_restore()
    assert (tmp_path / VAULT).exists()
    before = _raw(tmp_path)
    for k in secrets:
        monkeypatch.delenv(k, raising=False)
    return before


# ── the erasure ───────────────────────────────────────────────────────────

def test_a_boot_after_a_key_change_does_not_erase_the_vault(tmp_path, monkeypatch):
    """`seed_and_restore` runs at boot and saves whenever any env value differs.
    After a key change every managed value differs, so the very first boot wrote
    back a vault containing only what happened to decrypt — which was nothing."""
    _isolate(monkeypatch, tmp_path)
    before = _seed(monkeypatch, tmp_path,
                   BITGET_API_KEY="AKEY", BITGET_API_SECRET="ASEC")
    _rotate_master_key(tmp_path)

    # A boot with ONE managed value in the environment: enough to make
    # `changed` true and trigger the save.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    sv.seed_and_restore()

    after = _raw(tmp_path)
    for k in ("BITGET_API_KEY", "BITGET_API_SECRET"):
        assert k in after, f"{k} was erased from the vault by a boot"
        assert after[k] == before[k], f"{k}'s ciphertext was rewritten"


def test_the_recovery_command_does_not_erase_the_rest(tmp_path, monkeypatch):
    """/setexchange is what an operator runs BECAUSE secrets went missing, so
    this was the destructive path and the recovery path at once."""
    _isolate(monkeypatch, tmp_path)
    before = _seed(monkeypatch, tmp_path,
                   BITGET_API_KEY="AKEY", TELEGRAM_BOT_TOKEN="tok")
    _rotate_master_key(tmp_path)

    sv.store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})

    after = _raw(tmp_path)
    assert "WEB_GATEWAY_SECRET" in after, "the new secret was not stored"
    for k in ("BITGET_API_KEY", "TELEGRAM_BOT_TOKEN"):
        assert k in after and after[k] == before[k], (
            f"{k} was erased by storing an unrelated secret")


def test_the_old_key_still_opens_what_was_kept(tmp_path, monkeypatch):
    """Keeping the bytes is only worth anything if they still decrypt. This is
    the property the whole fix is for: restore the key, get the secrets back."""
    _isolate(monkeypatch, tmp_path)
    _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
    old_key = (tmp_path / KEYFILE).read_bytes()
    _rotate_master_key(tmp_path)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    sv.seed_and_restore()                      # the save that used to erase

    (tmp_path / KEYFILE).write_bytes(old_key)  # operator restores the key
    monkeypatch.delenv("BITGET_API_KEY", raising=False)
    restored = sv.seed_and_restore()["restored"]
    assert "BITGET_API_KEY" in restored
    assert os.environ["BITGET_API_KEY"] == "AKEY"


def test_re_entering_a_secret_replaces_the_copy_nobody_can_read(tmp_path,
                                                                monkeypatch):
    """The other precedence, and getting it backwards would make the fix
    undoable: an operator setting the secret again must WIN over the kept
    ciphertext, or the vault would hold the dead copy for ever."""
    _isolate(monkeypatch, tmp_path)
    _seed(monkeypatch, tmp_path, BITGET_API_KEY="OLD")
    _rotate_master_key(tmp_path)

    sv.store_secrets({"BITGET_API_KEY": "NEW"})
    monkeypatch.delenv("BITGET_API_KEY", raising=False)
    sv.seed_and_restore()
    assert os.environ["BITGET_API_KEY"] == "NEW", (
        "the unreadable copy outlived the value the operator just set")


def test_a_readable_vault_round_trips_untouched(tmp_path, monkeypatch):
    """The control. A change that made every entry opaque would satisfy every
    assertion above and break the vault completely."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("BITGET_API_KEY", "AKEY")
    sv.seed_and_restore()
    monkeypatch.delenv("BITGET_API_KEY")
    assert sv.seed_and_restore()["restored"] == ["BITGET_API_KEY"]
    assert os.environ["BITGET_API_KEY"] == "AKEY"


# ── saying so ─────────────────────────────────────────────────────────────

def test_boot_reports_what_it_could_not_restore(tmp_path, monkeypatch, caplog):
    """Silence here makes a boot that recovered NOTHING look exactly like a boot
    with nothing to recover — on the module whose whole promise is that a wiped
    .env self-heals."""
    _isolate(monkeypatch, tmp_path)
    _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY", BITGET_API_SECRET="ASEC")
    _rotate_master_key(tmp_path)

    with caplog.at_level("CRITICAL", logger="runeclaw.secrets_vault"):
        s = sv.seed_and_restore()
    assert sorted(s["unreadable"]) == ["BITGET_API_KEY", "BITGET_API_SECRET"]
    assert s["restored"] == [], "nothing could be restored, and it must not claim to"
    blob = caplog.text
    assert "CANNOT DECRYPT" in blob
    assert "not erased" in blob, "the operator is not told the bytes are still there"
    assert "AKEY" not in blob and "ASEC" not in blob, "a secret reached the log"


def test_a_clean_boot_says_nothing(tmp_path, monkeypatch, caplog):
    """The control for the alert: a warning that fires on a healthy boot is one
    the next reader skims."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("BITGET_API_KEY", "AKEY")
    with caplog.at_level("CRITICAL", logger="runeclaw.secrets_vault"):
        s = sv.seed_and_restore()
    assert s["unreadable"] == []
    assert "CANNOT DECRYPT" not in caplog.text


def test_status_tells_the_three_apart(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
    _rotate_master_key(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    st = sv.vault_status()
    assert st["BITGET_API_KEY"]["state"] == "unreadable"
    assert st["BITGET_API_KEY"]["vault"] is False, (
        "`vault` means 'a copy I can READ' and must stay that — it is `state` "
        "that carries the third answer")
    assert st["GROQ_API_KEY"]["state"] == "absent"
    assert "AKEY" not in repr(st)


@pytest.mark.asyncio
async def test_the_vault_card_files_it_under_its_own_heading(tmp_path,
                                                             monkeypatch):
    """Driven, because the bug was WHICH BUCKET a key fell into and every
    literal was already on the card. Unreadable used to render as "Env-only …
    mirrored to the vault on next boot" — a promise about a copy that is
    already there — or, with nothing in .env, as "Missing"."""
    from bot.skills.account_commands import AccountCommands

    _isolate(monkeypatch, tmp_path)
    _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
    _rotate_master_key(tmp_path)

    class _Card(AccountCommands):
        def __init__(self):
            self.sent: list[str] = []

        async def _send(self, update, text, reply_markup=None, edit=False):
            self.sent.append(text)

        def _is_admin(self, update) -> bool:
            return True

    h = _Card()
    await h._cmd_vault(object(), None)
    out = "\n".join(h.sent)

    line = next((ln for ln in out.splitlines() if "BITGET_API_KEY" in ln), "")
    assert line, "the key vanished from the card entirely"
    assert "UNREADABLE" in out
    assert "master key changed" in out
    assert "not erased" in out, (
        "an operator told a secret is unreadable and not told the bytes were "
        "kept will assume it is gone and stop looking for the old key")
    # Anchored on the section, not searched loose: "Missing" and "Env-only" are
    # legitimate headings elsewhere on this card for other keys.
    unread_at = out.index("UNREADABLE")
    assert out.index("BITGET_API_KEY") > unread_at
    assert "AKEY" not in out
