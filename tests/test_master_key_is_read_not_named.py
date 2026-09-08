"""Four surfaces explained the master key. None of them read it.

`/vault` printed

    Fernet under the master key (RUNECLAW_SECRETS_KEY / data/.exchange_secret.key)

as though those were one thing, on the command whose own docstring says it "is
how you verify nothing is left unprotected". They are not one thing, and the
difference is the whole durability story:

    set    the key is in the environment AND mirrored to disk — a wiped .env
           rebuilds from the file, a wiped data/ rebuilds from the environment
    unset  `data/.exchange_secret.key` is the ONLY copy. A wiped data/ loses
           every linked account, the secrets vault and the llm_api_key column,
           permanently

The boot preflight's undecryptable-accounts alert had the same shape one level
up — "the encryption key changed (a wiped data dir with RUNECLAW_SECRETS_KEY
unset does it)" is a hypothesis, offered at the moment the operator most needs
the fact, on a box where the fact is one file read away.

And the one warning that DID measure something fired exactly once, on the boot
that GENERATED the key:

    p = Path(key_file)
    if p.exists():
        return p.read_bytes().strip()      # ← silent, forever after

so a box that has been one `rm -rf data/` from losing everything since March
said so once, in a container log, in March.

`master_key_state()` is the reading, and it is the ONLY one: the card, the boot
report and the undecryptable alert all derive from it, so no two can drift —
the same rule `_decrypt_fields` established for `credential_state`.

THE LAST DEFECT HERE WAS IN THE FIX. Driving the five states showed that
`diverged` is transient: the loader overwrites the file on the first boot that
sees it, after which the state reads `pinned` — the healthiest word available —
at the exact moment every existing ciphertext stopped opening. So the loader
now keeps a `.bak` of the key it replaces (`secrets_vault`'s rule, applied to
the key that reads the vault) and `prior_backup` rides on the reading, because
it is the one trace that outlives the state that produced it.
"""

from pathlib import Path

import pytest

from bot.core.exchange_credentials import _fingerprint, _load_or_create_master_key, master_key_state
from bot.skills.account_commands import master_key_line
from tests.dep_policy import require

# `importorskip` here would be the house rule broken in its own file: the
# master key IS Fernet, `cryptography` is pinned in requirements.lock, and a
# green run over a silent skip would hide every assertion below.
require("cryptography", "the master key is a Fernet key")

from cryptography.fernet import Fernet  # noqa: E402


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
    return str(tmp_path / ".exchange_secret.key")


def _write(path, key: bytes):
    Path(path).write_bytes(key)


class TestTheStateIsRead:
    def test_no_key_anywhere_is_absent_not_a_verdict(self, keyfile):
        st = master_key_state(keyfile)
        assert st["state"] == "absent"
        assert st["fingerprint"] is None
        # Neither wipe question has an answer yet, and inventing one either way
        # would be the claim this whole area exists to stop.
        assert st["survives_data_wipe"] is None
        assert st["survives_env_wipe"] is None

    def test_a_file_with_no_env_var_dies_with_the_data_dir(self, keyfile):
        k = Fernet.generate_key()
        _write(keyfile, k)
        st = master_key_state(keyfile)
        assert st["state"] == "file_only"
        assert st["survives_env_wipe"] is True
        assert st["survives_data_wipe"] is False, (
            "the file is the only copy, and this is the state the generated-key "
            "warning named once and then never again")
        assert st["fingerprint"] == _fingerprint(k)

    def test_env_and_file_agreeing_is_pinned(self, keyfile, monkeypatch):
        k = Fernet.generate_key()
        _write(keyfile, k)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", k.decode())
        st = master_key_state(keyfile)
        assert st["state"] == "pinned"
        assert st["survives_env_wipe"] is True and st["survives_data_wipe"] is True

    def test_env_with_no_file_yet_is_still_pinned(self, keyfile, monkeypatch):
        # The loader writes the file on first use, so the environment alone is
        # not a degraded state — it is the good one, one write early.
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
        assert master_key_state(keyfile)["state"] == "pinned"

    def test_two_different_keys_is_diverged_and_says_which_is_orphaned(
            self, keyfile, monkeypatch):
        old, new = Fernet.generate_key(), Fernet.generate_key()
        _write(keyfile, old)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", new.decode())
        st = master_key_state(keyfile)
        assert st["state"] == "diverged"
        assert st["fingerprint"] == _fingerprint(new), "the key in force is the env one"
        assert _fingerprint(old) in st["detail"], (
            "the operator cannot tell whether the orphaned key is the one their "
            "data needs without seeing its fingerprint")

    def test_a_malformed_env_key_is_unreadable_not_absent(self, keyfile, monkeypatch):
        _write(keyfile, Fernet.generate_key())
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", "not-a-fernet-key")
        st = master_key_state(keyfile)
        assert st["state"] == "unreadable"
        assert st["survives_data_wipe"] is None
        assert "not a valid Fernet key" in st["detail"]

    def test_it_never_returns_key_material(self, keyfile, monkeypatch):
        k = Fernet.generate_key()
        _write(keyfile, k)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", k.decode())
        blob = repr(master_key_state(keyfile))
        assert k.decode() not in blob
        assert k.decode()[:16] not in blob

    def test_it_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
        # A directory where a key file should be: read_bytes raises OSError.
        d = tmp_path / "notafile"
        d.mkdir()
        st = master_key_state(str(d))
        assert st["state"] == "unreadable"
        assert st["fingerprint"] is None


class TestTheReplacedKeyIsKept:
    def test_a_different_env_key_backs_up_the_one_it_destroys(
            self, keyfile, monkeypatch):
        old, new = Fernet.generate_key(), Fernet.generate_key()
        _write(keyfile, old)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", new.decode())

        assert _load_or_create_master_key(keyfile) == new
        assert Path(keyfile).read_bytes().strip() == new
        bak = Path(keyfile + ".bak")
        assert bak.exists(), (
            "the only copy of the key that still opens the existing data was "
            "overwritten, and unsetting the variable does not bring it back")
        assert bak.read_bytes().strip() == old

    def test_the_backup_rides_on_the_reading_after_the_state_goes_quiet(
            self, keyfile, monkeypatch):
        # `diverged` lasts one boot. `pinned` afterwards is TRUE and is not the
        # whole truth, so the trace has to survive the transition.
        old, new = Fernet.generate_key(), Fernet.generate_key()
        _write(keyfile, old)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", new.decode())
        _load_or_create_master_key(keyfile)

        st = master_key_state(keyfile)
        assert st["state"] == "pinned"
        assert st["prior_backup"] is not None
        assert st["prior_backup"]["fingerprint"] == _fingerprint(old)

    def test_an_unchanged_key_writes_no_backup(self, keyfile, monkeypatch):
        # Re-running with the SAME key is the ordinary boot; it must not
        # accumulate copies of the key on disk.
        k = Fernet.generate_key()
        _write(keyfile, k)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", k.decode())
        _load_or_create_master_key(keyfile)
        assert not Path(keyfile + ".bak").exists()
        assert master_key_state(keyfile)["prior_backup"] is None

    def test_a_first_write_has_nothing_to_back_up(self, keyfile, monkeypatch):
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", Fernet.generate_key().decode())
        _load_or_create_master_key(keyfile)
        assert Path(keyfile).exists()
        assert not Path(keyfile + ".bak").exists()

    def test_it_refuses_to_destroy_what_it_could_not_copy(
            self, keyfile, monkeypatch):
        old, new = Fernet.generate_key(), Fernet.generate_key()
        _write(keyfile, old)
        monkeypatch.setenv("RUNECLAW_SECRETS_KEY", new.decode())

        real_write = Path.write_bytes

        def _fail_on_bak(self, data):
            if str(self).endswith(".bak"):
                raise OSError("no space left on device")
            return real_write(self, data)

        monkeypatch.setattr(Path, "write_bytes", _fail_on_bak)
        assert _load_or_create_master_key(keyfile) == new, (
            "the env key is still the one in force this run")
        assert Path(keyfile).read_bytes().strip() == old, (
            "the previous key was destroyed even though it could not be copied "
            "— the failure that makes the backup worth having is exactly the "
            "one where it is skipped")


class TestTheCardSaysIt:
    def test_file_only_is_not_rendered_as_healthy(self):
        out = master_key_line({"state": "file_only", "fingerprint": "abc123def456",
                               "survives_data_wipe": False})
        assert "file-only" in out
        assert "🟢" not in out, "colour is a claim, and this state is not the good one"
        assert "only copy" in out.lower() or "ONLY copy" in out
        assert "abc123def456" in out

    def test_pinned_says_both_wipes_are_survivable(self):
        out = master_key_line({"state": "pinned", "fingerprint": "abc123def456"})
        assert "🟢" in out
        assert ".env" in out and "data/" in out

    def test_diverged_is_red_and_carries_its_detail(self):
        out = master_key_line({"state": "diverged", "fingerprint": "n3wn3wn3wn3w",
                               "detail": "the key file holds a DIFFERENT key (fingerprint 0ld0ld0ld0ld)"})
        assert "🔴" in out
        assert "0ld0ld0ld0ld" in out, "the orphaned key's fingerprint never reached the card"

    def test_an_unreadable_state_is_not_silence(self):
        out = master_key_line({"state": "unreadable", "detail": "boom"})
        assert "could not be read" in out
        assert "🟢" not in out

    def test_an_empty_or_junk_reading_still_renders_a_negative(self):
        for junk in ({}, None, {"state": "banana"}):
            out = master_key_line(junk)
            assert "could not be read" in out, junk
            assert "🟢" not in out

    def test_the_backup_is_named_in_every_state_it_exists_in(self):
        for state in ("pinned", "file_only", "diverged", "unreadable"):
            out = master_key_line({
                "state": state, "fingerprint": "aaaaaaaaaaaa", "detail": "d",
                "prior_backup": {"path": "data/.exchange_secret.key.bak",
                                 "fingerprint": "0ld0ld0ld0ld"}})
            assert ".bak" in out and "0ld0ld0ld0ld" in out, state

    def test_it_never_prints_a_key(self):
        k = Fernet.generate_key().decode()
        out = master_key_line({"state": "pinned", "fingerprint": _fingerprint(k.encode()),
                               "detail": k})
        assert k not in out


class TestTheCardActuallyCallsIt:
    """A fix that lands in the reading and not the card has not landed."""

    @staticmethod
    def _render(monkeypatch, state):
        import bot.core.exchange_credentials as ec
        import bot.core.secrets_vault as sv
        # `state` is either a planted reading or a callable that raises, so
        # the card's own except-branch can be driven and not just its happy one.
        monkeypatch.setattr(
            ec, "master_key_state",
            state if callable(state) else (lambda *a, **k: state))
        # A vault with one readable entry: enough to reach the "What encrypts
        # what" block, which is where the master-key line lives.
        monkeypatch.setattr(sv, "vault_status", lambda: {
            "BITGET_API_KEY": {"env": True, "vault": True, "state": "readable"}})
        sent = {}

        class _Stub:
            users = None
            engine = None

            def _is_admin(self, update):
                return True

            async def _send(self, update, text, **k):
                sent["text"] = text

        import asyncio as _aio

        from bot.skills.account_commands import AccountCommands
        _aio.run(AccountCommands._cmd_vault(_Stub(), None, None))
        return sent.get("text", "")

    def test_the_vault_card_carries_the_master_key_state(self, monkeypatch):
        out = self._render(monkeypatch, {
            "state": "file_only", "fingerprint": "abc123def456",
            "survives_data_wipe": False, "detail": "", "prior_backup": None})
        assert "The master key itself" in out
        assert "file-only" in out, (
            "the reading exists and the card does not print it — the defect "
            "this repo names 'a fix that lands in the assessor and not the "
            "renderer'")

    def test_a_pinned_box_says_so_on_the_card(self, monkeypatch):
        out = self._render(monkeypatch, {
            "state": "pinned", "fingerprint": "abc123def456", "detail": "",
            "prior_backup": None})
        assert "The master key itself" in out and "pinned" in out

    def test_a_read_that_raises_is_not_rendered_as_pinned(self, monkeypatch):
        # The card's own `except` decides what a failed read looks like, and
        # the healthiest word in the vocabulary is the wrong default. A
        # mutation putting "pinned" there survived the round that should have
        # killed it — nothing drove the failing path at all.
        def _boom(*a, **k):
            raise RuntimeError("key file vanished mid-read")

        out = self._render(monkeypatch, _boom)
        assert "could not be read" in out
        assert "pinned" not in out
        assert "🟢 pinned" not in out
