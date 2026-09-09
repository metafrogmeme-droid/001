"""The on-chain signing key had no door but a plaintext file.

`WEB3_SIGNER_PRIVATE_KEY` has been in `secrets_vault._DEFAULT_MANAGED` since
the WEB3-LIVE-EXEC slice, so it IS encrypted at rest once the vault holds it.
The gap was the intake: the ONLY way to get it there was to write the key in
the clear into `.env` and wait for a boot to mirror it. Every other managed
secret has `/setexchange`, `/setgateway` or `/setllm`; `/vault` printed
``WEB3_SIGNER_PRIVATE_KEY → .env`` beside the one that signs transactions.

And `/vault`'s own footnote says what that costs: *"a key that only ever came
from .env stays in the clear there"*. So the most sensitive single value in
the repo was the one value that had to transit an unencrypted file on disk,
and stay there until somebody remembered to delete the line.

THE ADDRESS IS THE CONFIRMATION, and it is why this is not `/setgateway` with
a different key name. A private key cannot be echoed back, so a typo in a
64-character paste is invisible until a transaction is signed by an account
the operator did not mean. `check_signing_key` derives the address and the
card shows THAT — the one thing safe to print, and the only thing that answers
"did I paste the right key?".

THREE OUTCOMES, not two, because `eth-account` is optional in this repo (it is
NOT installed in CI). Well-formed-and-confirmed, well-formed-but-unconfirmed,
and rejected are different events, and the middle one must not be dressed as
the first.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.skills.account_commands import signer_stored_card
from bot.skills.telegram_handler import TelegramHandler
from bot.web.web3_signer import check_signing_key, normalise_signing_key

#: A syntactically valid secp256k1 scalar. Not a key to anything — it is a
#: literal in a public test file, which is the whole reason it must never be
#: the kind of value that could matter.
KEY = "0x4c0883a69102937d6231471b5dbb6204fe512961708279f2e3e8a5d4b8e3c7a1"


def _src() -> str:
    return inspect.getsource(TelegramHandler._cmd_setsigner)


# ── 1. the validator ──────────────────────────────────────────────────────

class TestCheckSigningKey:
    def test_a_well_formed_key_is_accepted(self):
        assert check_signing_key(KEY)["ok"] is True

    def test_the_prefix_is_optional_and_case_is_not_significant(self):
        assert check_signing_key(KEY[2:])["ok"] is True
        assert check_signing_key("  " + KEY.upper() + " ")["ok"] is True

    def test_a_wrong_length_is_rejected_with_the_length(self):
        out = check_signing_key("0x1234")
        assert out["ok"] is False
        assert "64 hex" in out["reason"] and "4" in out["reason"]

    def test_sixty_four_non_hex_characters_are_rejected(self):
        out = check_signing_key("0x" + "z" * 64)
        assert out["ok"] is False and "hexadecimal" in out["reason"]

    def test_all_zeros_is_not_a_key(self):
        """What an all-zero or cleared paste gives. Some libraries take it."""
        out = check_signing_key("0x" + "0" * 64)
        assert out["ok"] is False and "secp256k1" in out["reason"]

    def test_a_scalar_at_or_above_the_curve_order_is_not_a_key(self):
        n = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141"
        assert check_signing_key("0x" + n)["ok"] is False
        assert check_signing_key("0x" + "F" * 64)["ok"] is False

    def test_the_largest_valid_scalar_is_accepted(self):
        """n-1 is a key; n is not. The boundary, driven from both sides."""
        n_minus_1 = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364140"
        assert check_signing_key("0x" + n_minus_1)["ok"] is True

    def test_nothing_is_not_a_key(self):
        for empty in ("", "   ", None):
            assert check_signing_key(empty)["ok"] is False

    def test_it_never_returns_or_quotes_key_material(self):
        """A rejection message about a private key is a place one can leak."""
        for candidate in (KEY, KEY[2:], "0x" + "0" * 64, "0x" + "z" * 64,
                          "0xdeadbeef", "notakeyatall"):
            out = check_signing_key(candidate)
            blob = repr(out)
            body = candidate[2:] if candidate[:2] == "0x" else candidate
            assert body.lower() not in blob.lower(), (
                f"check_signing_key echoed its input for {candidate[:6]}…")
            assert set(out) == {"ok", "reason", "address"}, (
                "the validator grew a field — is it key material?")

    def test_the_address_is_none_when_the_library_is_absent(self, monkeypatch):
        """The third outcome, and CI's normal one: well-formed, unconfirmed."""
        import bot.web.web3_signer as ws
        monkeypatch.setattr(ws, "_signing_lib", lambda: None)
        out = check_signing_key(KEY)
        assert out["ok"] is True
        assert out["address"] is None
        assert "eth-account" in out["reason"]
        assert "not confirmed" in out["reason"] or "could not be derived" in out["reason"]

    def test_the_address_is_derived_when_the_library_is_present(self, monkeypatch):
        import bot.web.web3_signer as ws
        monkeypatch.setattr(ws, "_signing_lib", lambda: SimpleNamespace(
            from_key=lambda k: SimpleNamespace(address="0xAbCd")))
        out = check_signing_key(KEY)
        assert out["ok"] is True and out["address"] == "0xAbCd"
        assert out["reason"] == ""

    def test_a_library_refusal_is_a_rejection_that_quotes_nothing(self, monkeypatch):
        """The arithmetic passed and the parser still said no."""
        import bot.web.web3_signer as ws

        def _boom(k):
            raise ValueError(f"bad key: {k}")     # a real parser does this

        monkeypatch.setattr(ws, "_signing_lib",
                            lambda: SimpleNamespace(from_key=_boom))
        out = check_signing_key(KEY)
        assert out["ok"] is False
        assert KEY[2:] not in repr(out), "the parser's message carried the key"


class TestNormalise:
    def test_it_lowercases_strips_and_prefixes(self):
        assert normalise_signing_key("  " + KEY[2:].upper() + "\n") == KEY.lower()

    def test_an_already_normal_key_is_unchanged(self):
        assert normalise_signing_key(KEY.lower()) == KEY.lower()


# ── 2. the card ───────────────────────────────────────────────────────────

class TestSignerStoredCard:
    def test_it_shows_the_address_when_there_is_one(self):
        out = signer_stored_card("0xAbCdEf0000000000000000000000000000000001")
        assert "0xAbCdEf0000000000000000000000000000000001" in out
        assert "against the wallet you meant" in out

    def test_no_address_says_the_confirmation_did_not_happen(self):
        out = signer_stored_card(None)
        assert "could not be derived" in out
        assert "eth-account" in out
        assert "nothing has confirmed" in out
        # And it must NOT read as a clean success.
        assert "against the wallet you meant" not in out

    def test_both_states_tell_the_operator_to_clear_the_env_line(self):
        """The whole point: the vault copy is only an improvement once the
        plaintext line is gone."""
        for out in (signer_stored_card("0xAb"), signer_stored_card(None)):
            assert "WEB3_SIGNER_PRIVATE_KEY" in out and ".env" in out

    def test_the_card_never_carries_a_key(self):
        for out in (signer_stored_card("0xAb"), signer_stored_card(None)):
            assert KEY[2:] not in out

    def test_an_address_is_escaped(self):
        """Assert the POSITIVE rendering, not the absence of `<b>`.

        The first draft sliced 40 characters after "It controls" and asserted
        no `<b>` in them — and failed, on the card's own
        `<b>Check that address…</b>` two lines down. The escaping was working
        the whole time. That is the trap CLAUDE.md counts six instances of: a
        short string asserted ABSENT matches the surrounding prose.
        """
        out = signer_stored_card("<b>x</b>")
        assert "&lt;b&gt;x&lt;/b&gt;" in out, "the address was not escaped"
        assert "<code><b>x</b></code>" not in out


# ── 3. the command ────────────────────────────────────────────────────────

def _host(sent, admin=True):
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    h._send = _send
    h._is_admin = lambda update: admin
    return h


def _update(chat_type="private"):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type=chat_type),
        message=SimpleNamespace(delete=AsyncMock()), callback_query=None)


@pytest.fixture
def stored(monkeypatch):
    """Capture what reaches the vault, and stub the library out so the address
    branch is deterministic rather than dependent on whether CI installed
    eth-account."""
    seen = []
    import bot.core.secrets_vault as sv
    import bot.web.web3_signer as ws
    monkeypatch.setattr(sv, "store_secrets", lambda d: seen.append(dict(d)))
    monkeypatch.setattr(ws, "_signing_lib", lambda: SimpleNamespace(
        from_key=lambda k: SimpleNamespace(address="0xFEED")))
    return seen


class TestSetSignerCommand:
    @pytest.mark.asyncio
    async def test_it_stores_the_normalised_key(self, stored):
        sent = []
        await _host(sent)._cmd_setsigner(
            _update(), SimpleNamespace(args=[KEY[2:].upper()]))
        assert stored == [{"WEB3_SIGNER_PRIVATE_KEY": KEY.lower()}]
        assert "0xFEED" in sent[0]

    @pytest.mark.asyncio
    async def test_it_deletes_the_message_before_anything_else(self, stored):
        upd = _update()
        await _host([], admin=False)._cmd_setsigner(
            upd, SimpleNamespace(args=[KEY]))
        upd.message.delete.assert_awaited()
        assert stored == [], "a non-admin stored a key"

    @pytest.mark.asyncio
    async def test_a_group_chat_is_refused(self, stored):
        sent = []
        await _host(sent)._cmd_setsigner(
            _update(chat_type="group"), SimpleNamespace(args=[KEY]))
        assert stored == []
        assert "private chat" in sent[0]

    @pytest.mark.asyncio
    async def test_no_argument_prints_usage_and_stores_nothing(self, stored):
        sent = []
        await _host(sent)._cmd_setsigner(_update(), SimpleNamespace(args=[]))
        assert stored == []
        assert "/setsigner" in sent[0] and "64 hex" in sent[0]

    @pytest.mark.asyncio
    async def test_an_invalid_key_is_refused_without_being_echoed(self, stored):
        sent = []
        bad = "0x" + "0" * 64
        await _host(sent)._cmd_setsigner(_update(), SimpleNamespace(args=[bad]))
        assert stored == [], "an invalid key reached the vault"
        assert "Not stored" in sent[0]
        assert "0" * 64 not in sent[0], "the refusal echoed the input"

    @pytest.mark.asyncio
    async def test_a_vault_failure_is_reported_not_swallowed(self, monkeypatch):
        import bot.core.secrets_vault as sv
        import bot.web.web3_signer as ws

        def _boom(_d):
            raise RuntimeError("disk full")

        monkeypatch.setattr(sv, "store_secrets", _boom)
        monkeypatch.setattr(ws, "_signing_lib", lambda: None)
        sent = []
        await _host(sent)._cmd_setsigner(_update(), SimpleNamespace(args=[KEY]))
        assert "Could not store" in sent[0]
        assert KEY[2:] not in sent[0]

    @pytest.mark.asyncio
    async def test_no_reply_on_any_path_contains_the_key(self, stored):
        for args in ([], [KEY], ["0x1234"], [KEY.upper()]):
            sent = []
            await _host(sent)._cmd_setsigner(_update(), SimpleNamespace(args=args))
            for msg in sent:
                assert KEY[2:].lower() not in msg.lower(), args


# ── 4. wiring ─────────────────────────────────────────────────────────────

class TestWiring:
    def test_the_command_is_registered(self):
        assert '("setsigner", self._cmd_setsigner)' in inspect.getsource(TelegramHandler)

    def test_it_is_in_the_command_catalog(self):
        # `GROUPS`, not `COMMAND_GROUPS` — the first draft imported a name I
        # had invented and got an ImportError. Loud, and caught in seconds;
        # the same mistake against a *grep* is silent, which is why CLAUDE.md
        # keeps a paragraph about it.
        from bot.skills.command_catalog import GROUPS
        names = {c for _key, _title, cmds in GROUPS for c, _d in cmds}
        assert "setsigner" in names

    def test_the_delete_precedes_the_admin_gate(self):
        src = _src()
        assert src.index("delete()") < src.index("_is_admin"), (
            "an early return would leave a private key in the chat history")

    def test_it_validates_before_it_stores(self):
        src = _src()
        assert src.index("check_signing_key(") < src.index("store_secrets(")

    def test_vault_points_at_the_command_not_at_env(self):
        """`/vault` printed `→ .env` for this key — the whole defect."""
        src = inspect.getsource(TelegramHandler._cmd_vault)
        assert '"WEB3_SIGNER_PRIVATE_KEY": "/setsigner"' in src

    def test_the_key_is_vault_managed(self):
        from bot.core.secrets_vault import _DEFAULT_MANAGED
        assert "WEB3_SIGNER_PRIVATE_KEY" in _DEFAULT_MANAGED
