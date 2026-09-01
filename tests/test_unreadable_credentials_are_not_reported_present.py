"""A credential nobody could read is not a credential that is there.

Two findings from the audit's confirmed-not-remediated tier.

1. "secrets: an undecryptable LLM key is returned as ciphertext, reported
   present (MEDIUM)". `_decrypt_llm_key` answered a failed decrypt by
   returning the stored value unchanged, under a comment naming two opposite
   situations at once:

       # Legacy plaintext (pre-encryption) or unreadable — pass through.

   A legacy plaintext key is a real, usable credential. Undecryptable
   ciphertext is not one at all — and handing it back makes the CIPHERTEXT the
   key: the status endpoint sees a non-empty string, answers
   `connected: True`, prints a fingerprint of it, and every call then 401s at
   the provider. A failed read rendered as a measurement, on the surface whose
   whole job is to say whether the credential works.

   Reachable whenever the Fernet master key rotates or is lost — the same
   event this repo already logs for the exchange vault ("could not decrypt …
   stale master key?").

2. "secrets: /connect and /setexchange echo a raw ccxt exception to the user
   (LOW)". The venue validators returned `str(exc)[:200]`, which is the raw
   driver message. A ccxt error carries the request URL and several venues put
   the API key in that URL's query string.

WHY THE TWO ARE ONE FILE: both are the boundary where a credential's state
becomes a sentence shown to its owner, and both were wrong in the same
direction — saying something definite about something unknown.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from bot.core.exchange_credentials import _safe_venue_detail
from bot.db.models import _decrypt_llm_key, llm_key_state

# ── the LLM key ───────────────────────────────────────────────────────────

def _unreadable_token() -> str:
    """Ciphertext this process cannot decrypt — a rotated master key."""
    return Fernet(Fernet.generate_key()).encrypt(b"sk-real-key").decode()


def test_an_undecryptable_key_is_not_handed_back_as_the_key():
    tok = _unreadable_token()
    assert _decrypt_llm_key(tok) == "", (
        "the ciphertext was returned as the key, so every truthiness check "
        "downstream reads it as a working credential"
    )


def test_an_undecryptable_key_is_distinguishable_from_never_having_one():
    """Empty alone cannot say WHY, and the two need different advice: one is a
    setup step, the other means re-enter the key you already have."""
    assert llm_key_state(_unreadable_token()) == "unreadable"
    assert llm_key_state("") == ""


@pytest.mark.parametrize("key", [
    "sk-proj-abc123XYZ",
    "sk-ant-api03-abcdef",
    "AIzaSyABC123def456",
    "gsk_abc123def456",
])
def test_a_legacy_plaintext_key_still_passes_through(key):
    """The behaviour the old catch-all existed for, and it must survive: a key
    stored before encryption is real and usable."""
    assert llm_key_state(key) == "plaintext"
    assert _decrypt_llm_key(key) == key


def test_a_key_this_process_CAN_decrypt_round_trips():
    """The control. A change that broke this would make every stored key
    unreadable and every test above still pass."""
    from bot.db import models
    real = models._llm_cipher().encrypt(b"sk-live-key").decode()
    assert llm_key_state(real) == "decrypted"
    assert _decrypt_llm_key(real) == "sk-live-key"


def test_the_settings_row_records_which_state_it_saw():
    """`llm_api_key` is empty for BOTH causes, so the state rides alongside."""
    from bot.db.models import UserSettings
    s = UserSettings(user_id=1)
    assert hasattr(s, "llm_key_status")
    assert s.llm_key_status == "", "the default must be the safe reading"


# ── the venue rejection detail ────────────────────────────────────────────

class _Ccxtish(Exception):
    pass


def test_a_venue_error_does_not_carry_the_key_to_the_user():
    exc = _Ccxtish(
        "bitget GET https://api.bitget.com/api/v2/account?"
        "apiKey=bg_LIVEKEY_SHOULD_NOT_APPEAR&sign=deadbeef rejected")
    out = _safe_venue_detail(exc)
    assert "bg_LIVEKEY_SHOULD_NOT_APPEAR" not in out, (
        "the API key rode out of the venue error and into the user's chat"
    )
    assert "REDACTED" in out


def test_the_reason_is_scrubbed_not_suppressed():
    """These strings are the only thing telling a user WHAT to fix. Replacing
    them with a class name would take the answer away to protect a secret that
    scrubbing already handles."""
    out = _safe_venue_detail(_Ccxtish("passphrase does not match"))
    assert "passphrase" in out, "the actionable reason was thrown away"
    assert "_Ccxtish" in out


def test_an_empty_message_still_names_the_class():
    assert _safe_venue_detail(_Ccxtish()) == "_Ccxtish"


def test_the_detail_is_length_bounded():
    out = _safe_venue_detail(_Ccxtish("x" * 5000))
    assert len(out) <= 200


def test_no_validator_returns_a_raw_exception_any_more():
    """Wiring, not behaviour: six call sites returned `str(exc)[:200]` and a
    seventh added later would be just as wrong. Comments are stripped because
    the helper's own docstring quotes the shape it replaced.
    """
    import inspect
    import io
    import tokenize

    from bot.core import exchange_credentials as xc
    src = inspect.getsource(xc)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.string.lstrip('rbuf').startswith(('"""', "'''")):
            continue                      # docstrings quote the old shape
        out.append(tok.string)
    code = " ".join(out)
    assert "str ( exc ) [ :200 ]" not in code, (
        "a venue validator is back to returning the raw driver message"
    )


# ── a denial must not spend what it denies ────────────────────────────────

def _callback_source(branch: str) -> str:
    """The confirm: or reject: branch of _handle_callback, comments stripped.

    Ordering is a property of the SOURCE — which statement runs first — and
    driving the real handler needs a Telegram Update, a query, an engine and a
    live-mode config. This reads the order directly instead, with comments
    removed because the fix's own note quotes both statements it reorders.

    LINE-BASED, not tokenize: the slice is a fragment of a method and tokenize
    needs syntactically valid input. The first draft used it and the `reject`
    branch — which runs to the end of the method — came back empty, so three
    assertions failed against correct code.
    """
    import inspect
    import re

    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._handle_callback)
    start = src.index(f'data.startswith("{branch}:")')
    rest = src[start:]
    nxt = re.search(r'\n\s{8}elif data\.startswith\(', rest)
    if nxt:
        rest = rest[:nxt.start()]
    code = "\n".join(re.sub(r"#.*$", "", ln) for ln in rest.splitlines())
    return " ".join(code.split())


@pytest.mark.parametrize("branch", ["confirm", "reject"])
def test_the_ownership_check_runs_before_the_id_is_consumed(branch):
    """The double-tap guard CONSUMES trade_id. It ran first, so a stranger's
    `confirm:<id>` was recorded and only then denied — and the owner tapping
    Confirm afterwards was told "Already confirmed" for a trade that never
    executed. Anyone who could guess a trade_id could burn it, and the message
    told the owner it had gone through.
    """
    code = _callback_source(branch)
    owner_at = code.find("_callback_owner_ok")
    consume_at = code.find("_confirmed_ids.add")
    assert owner_at != -1, f"{branch}: the ownership check is gone"
    assert consume_at != -1, f"{branch}: the double-tap guard is gone"
    assert owner_at < consume_at, (
        f"{branch}: the id is consumed at {consume_at} before ownership is "
        f"checked at {owner_at} — a denial that spends the thing it denies"
    )


@pytest.mark.parametrize("branch", ["confirm", "reject"])
def test_the_double_tap_guard_still_exists(branch):
    """The reorder must not have removed the protection it was moved past: a
    genuine double tap still has to be refused before anything executes."""
    code = _callback_source(branch)
    assert "_confirmed_ids.add" in code
    assert "in self._confirmed_ids" in code


def test_both_branches_share_one_consumed_set():
    """Why reject had to be fixed too: a stranger's reject burns the confirm."""
    for branch in ("confirm", "reject"):
        assert "_confirmed_ids" in _callback_source(branch)
