"""Exchange credentials that will not decrypt are not credentials you don't have.

THIS IS THE FOLLOW-THROUGH ON A QUESTION THIS REPO ALREADY WROTE DOWN.
`test_unreadable_credentials_are_not_reported_present.py` fixed the LLM key
with exactly this design — `llm_key_state()` three-valued, `_decrypt_llm_key`
refusing to hand back ciphertext, a `llm_key_status` column recording which
state was seen — and its own docstring names the trigger as

    "the same event this repo already logs for the exchange vault"

The exchange vault was not then checked, and it holds the higher-stakes half:
the API keys that move real money. `ExchangeCredentialStore.get()` returned
None for a user who never connected AND for one whose stored record will not
decrypt, and said so in its docstring — "the caller treats that as 'not
connected'" — while `has()` and `list_venues()` read the record map without
decrypting anything and answered CONNECTED for that same user.

So one bot gave two answers, and four surfaces were wrong:

  /exchange              "Status: connected", above a `Key:` line that came out
                         EMPTY, because the fingerprint is built from `get()`.
  the engine's live gate "no linked Bitget account — use /connect to link one",
                         to a user who had linked one. A flat contradiction of
                         the card above, from the same record.
  venue routing          `active_venues()` asked `list_venues()`, a presence
                         test, so a venue whose keys stopped decrypting stayed
                         in the routing set and could never reach `dropped` —
                         the exact silence `venue_selection`'s own docstring is
                         written against, with every part of the reporting
                         machinery already built.
  the web live gate      `has_own_keys=True` into a FAIL-CLOSED gate, on
                         credentials the bot cannot read. Two callers of it.

None of this is exotic. `_load_or_create_master_key` GENERATES a new key when
RUNECLAW_SECRETS_KEY is unset and the data dir was wiped — its own warning says
so — and then every record stops decrypting at once, with the file itself
perfectly readable. `_load`'s docstring already says the sentence in capitals,
about the FILE: "AN UNREADABLE STORE IS NOT AN EMPTY STORE, AND THE DIFFERENCE
IS EVERY KEY IN IT." This is the record level.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.skills.account_commands import AccountCommands

BITGET = {"api_key": "a", "api_secret": "b", "passphrase": "c"}
BYBIT = {"api_key": "x", "api_secret": "y"}


def _store(tmp_path):
    return ExchangeCredentialStore(
        creds_file=str(tmp_path / "c.enc"), key_file=str(tmp_path / "k.key"))


def _rotate_master_key(tmp_path):
    """What a wiped data dir does: the ciphertext survives, the key does not.

    A NEW STORE OBJECT, because `_cipher` memoises the Fernet — reusing the old
    instance would keep decrypting and the test would be measuring the cache.
    """
    (tmp_path / "k.key").write_bytes(Fernet.generate_key())
    return _store(tmp_path)


# ── the reading that did not exist ────────────────────────────────────────

def test_the_three_states_are_three(tmp_path):
    s = _store(tmp_path)
    assert s.credential_state("1") == "absent"
    s.set_venue("1", "bitget", BITGET)
    assert s.credential_state("1") == "readable"
    s2 = _rotate_master_key(tmp_path)
    assert s2.credential_state("1") == "unreadable", (
        "a record this bot cannot decrypt reported as one of the other two")


def test_unreadable_is_not_absent_and_not_connected(tmp_path):
    """The two words the old API could produce, and neither is true."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    s2 = _rotate_master_key(tmp_path)
    # `has()` still says a record exists — correct, and deliberately unchanged:
    # it is the honest answer to "is there a row", which some callers want.
    assert s2.has("1") is True
    # …and `get()` still answers None, because the execution layer's question
    # is "can I trade with this" and the answer is no either way.
    assert s2.get("1") is None
    # The point is that a third question is now askable at all.
    assert s2.credential_state("1") == "unreadable"


def test_every_reading_agrees_about_one_record(tmp_path):
    """`get_for_venue` and `venue_states` route through ONE decrypt, so they
    cannot drift. The first draft probed a single field for speed, which would
    have called a record readable that `get_for_venue` refuses — reintroducing
    the disagreement this file is about, inside the fix for it."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    raw = json.loads((tmp_path / "c.enc").read_text())
    # A record whose FIRST field decrypts and whose last one is corrupt.
    raw["1"]["venues"]["bitget"]["passphrase"] = Fernet(
        Fernet.generate_key()).encrypt(b"nope").decode()
    (tmp_path / "c.enc").write_text(json.dumps(raw))
    s2 = _store(tmp_path)
    assert s2.get_for_venue("1", "bitget") is None
    assert s2.venue_states("1") == {"bitget": "unreadable"}
    assert s2.credential_state("1") == "unreadable"
    assert s2.readable_venues("1") == []


def test_venue_states_is_per_venue(tmp_path):
    """A user can hold one readable venue and one that is not, and a rollup
    that collapses them hides whichever it does not pick."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    s.set_venue("1", "bybit", BYBIT)
    raw = json.loads((tmp_path / "c.enc").read_text())
    raw["1"]["venues"]["bitget"]["api_key"] = Fernet(
        Fernet.generate_key()).encrypt(b"nope").decode()
    (tmp_path / "c.enc").write_text(json.dumps(raw))
    s2 = _store(tmp_path)
    assert s2.venue_states("1") == {"bitget": "unreadable", "bybit": "readable"}
    assert s2.readable_venues("1") == ["bybit"]
    assert s2.list_venues("1") == ["bitget", "bybit"], (
        "list_venues still answers 'which venues have a record' — the point is "
        "that routing stopped mistaking that for 'which venues work'")


def test_nothing_stored_is_an_empty_map_not_a_word(tmp_path):
    s = _store(tmp_path)
    assert s.venue_states("nobody") == {}
    assert s.readable_venues("nobody") == []
    assert s.credential_state("nobody") == "absent"


def test_a_readable_store_is_unchanged(tmp_path):
    """The control. A change that broke decryption outright would make every
    assertion above pass and the product useless."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    assert s.get("1") == BITGET
    assert s.venue_states("1") == {"bitget": "readable"}
    assert s.readable_venues("1") == ["bitget"]


# ── routing: the report that could never fire ─────────────────────────────

def test_a_venue_that_stopped_decrypting_drops_out_and_is_reported(tmp_path,
                                                                   monkeypatch):
    """`venue_selection`'s class docstring: "an active venue whose keys stopped
    decrypting must drop out of routing and be REPORTED, not silently skipped.
    Silently skipping is how somebody believes they are trading two venues
    while one has been dead for a week." Every part of that was built. The one
    question it asked — `list_venues()` — could not see the case."""
    from bot.core import venue_selection as vs

    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    s.set_venue("1", "bybit", BYBIT)
    raw = json.loads((tmp_path / "c.enc").read_text())
    raw["1"]["venues"]["bybit"]["api_secret"] = Fernet(
        Fernet.generate_key()).encrypt(b"nope").decode()
    (tmp_path / "c.enc").write_text(json.dumps(raw))
    s2 = _store(tmp_path)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: s2)

    store = vs.VenueSelectionStore(path=str(tmp_path / "sel.json"))
    ok, _ = store.set_selection("1", ["bitget"], connected=lambda _u: ["bitget", "bybit"])
    assert ok
    # Now select both through the injected view, then read routing for real.
    store._sel["1"] = ["bitget", "bybit"]
    live, gone = store.active_venues("1")
    assert live == ("bitget",)
    assert gone == ("bybit",), (
        "the undecryptable venue stayed in the routing set — the caller is told "
        "it is trading somewhere it cannot place an order")


def test_selecting_a_venue_you_cannot_decrypt_is_refused(tmp_path, monkeypatch):
    from bot.core import venue_selection as vs

    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    s2 = _rotate_master_key(tmp_path)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: s2)
    store = vs.VenueSelectionStore(path=str(tmp_path / "sel.json"))
    ok, reason = store.set_selection("1", ["bitget"])
    assert not ok and "connect" in reason.lower()


# ── the surfaces that said opposite things ────────────────────────────────

@pytest.fixture
def _live_flag():
    """Two attributes ON the real CONFIG, not a SimpleNamespace in its place.

    Replacing it wholesale breaks `RuneClawEngine.__init__`, which reads
    `CONFIG.analyzer` long before the gate is reached — the same shape as
    `test_per_user_eligibility`'s fixture, which is why that one is written
    this way.
    """
    from bot.core import engine as eng
    before = (eng.CONFIG.per_user_live_enabled,
              getattr(eng.CONFIG, "live_open_to_key_holders", False))
    object.__setattr__(eng.CONFIG, "per_user_live_enabled", True)
    object.__setattr__(eng.CONFIG, "live_open_to_key_holders", True)
    yield
    object.__setattr__(eng.CONFIG, "per_user_live_enabled", before[0])
    object.__setattr__(eng.CONFIG, "live_open_to_key_holders", before[1])


def _gate(monkeypatch, store, uid="777"):
    from bot.core.engine import RuneClawEngine
    e = RuneClawEngine()
    monkeypatch.setattr(e, "_is_operator_user", lambda _u: False)
    monkeypatch.setattr(e, "_human_confirmed", lambda _u: True, raising=False)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: store)
    return e.per_user_live_eligibility(uid)


def test_the_engine_live_gate_stops_calling_a_linked_user_unlinked(
        tmp_path, monkeypatch, _live_flag):
    """DRIVEN, and the first version of this test was a source scan that a
    mutation swapping the two sentences SURVIVED — the literal was still there
    and the branch was inverted. The gate said "no linked Bitget account — use
    /connect to link one" to a user who HAD linked one, while /exchange said
    "connected" about the same record."""
    s = _store(tmp_path)
    s.set_venue("777", "bitget", BITGET)
    ok, reason = _gate(monkeypatch, _rotate_master_key(tmp_path))
    assert ok is False, "an unreadable credential must still refuse"
    assert "no linked" not in reason.lower(), reason
    assert "decrypt" in reason.lower(), reason


def test_a_user_who_never_connected_still_gets_the_connect_invitation(
        tmp_path, monkeypatch, _live_flag):
    """The other half. The two sentences have to be different AND land on the
    right cases — swapping them passes any test that only asserts both exist."""
    ok, reason = _gate(monkeypatch, _store(tmp_path))
    assert ok is False
    assert "no linked" in reason.lower(), reason
    assert "decrypt" not in reason.lower(), reason


def test_the_web_live_gates_both_ask_for_readable_keys():
    """TWO callers feed `web_live_gate.evaluate`, and fixing one is how the
    previous defect in this area lasted an hour longer than it had to. A
    fail-closed gate satisfied by an unreadable credential fails open, which is
    the one direction it must never fail."""
    import inspect
    import re

    from bot.web import user_gateway, web_live_admin
    for mod, fn in ((web_live_admin, "_has_own_keys"),
                    (user_gateway, "_web_live_decision")):
        src = inspect.getsource(getattr(mod, fn))
        code = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
        assert "credential_state" in code, f"{mod.__name__}.{fn} still uses a presence test"
        assert '== "readable"' in code, f"{mod.__name__}.{fn} does not require readable"


@pytest.mark.asyncio
async def test_the_boot_preflight_reports_the_users_it_used_to_skip(
        tmp_path, monkeypatch):
    """Its docstring names a key that was "revoked/regenerated" as the thing it
    catches before a stop fails to place. A record this bot cannot decrypt
    builds no executor, so it fell into the `continue` as "no usable keys" and
    left no trace — and that is the case a wiped data dir produces for every
    linked user at once.

    DRIVEN. The first version asserted `result="UNDECRYPTABLE"` appeared in the
    source, and a mutation replacing the whole report block's condition with
    `if False:` SURVIVED it: the string was still there, in a branch nothing
    reached.
    """
    import types

    from bot import main as bmain

    s = _store(tmp_path)
    s.set_venue("777", "bitget", BITGET)
    s2 = _rotate_master_key(tmp_path)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: s2)
    monkeypatch.setattr(bmain, "CONFIG", types.SimpleNamespace(
        simulation_mode=False, live_trading_enabled=True,
        per_user_live_enabled=True,
        telegram=types.SimpleNamespace(chat_id="admin1", admin_ids="")))

    built = []

    def _never(uid):
        built.append(uid)
        raise AssertionError("an undecryptable record must not reach the probe")

    engine = types.SimpleNamespace(_executor_for=_never, live_executor=object(),
                                   set_live_auth_status=lambda *a, **k: None)
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text))

    await bmain._per_user_credential_preflight(engine, _Bot())

    assert built == [], "the probe tried to build an executor it cannot build"
    assert len(sent) == 1, f"the operator was told {len(sent)} times"
    _to, msg = sent[0]
    assert _to == "admin1"
    assert "777" in msg, "the account nobody can decrypt is not named"
    assert "decrypt" in msg.lower()
    # NOT a venue rejection: the venue was never asked, and saying it rejected
    # them would invent the very verdict this whole pass is about.
    assert "rejection" in msg.lower() and "not a rejection" in msg.lower()


@pytest.mark.asyncio
async def test_the_preflight_says_nothing_when_every_record_reads(
        tmp_path, monkeypatch):
    """The control: a clean boot must stay quiet, or the next warning is
    skimmed. Without this, "always alert" passes the test above."""
    import types

    from bot import main as bmain

    s = _store(tmp_path)
    s.set_venue("777", "bitget", BITGET)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: s)
    monkeypatch.setattr(bmain, "CONFIG", types.SimpleNamespace(
        simulation_mode=False, live_trading_enabled=True,
        per_user_live_enabled=True,
        telegram=types.SimpleNamespace(chat_id="admin1", admin_ids="")))
    _op = object()
    engine = types.SimpleNamespace(_executor_for=lambda uid: _op,
                                   live_executor=_op,
                                   set_live_auth_status=lambda *a, **k: None)
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, parse_mode=None):
            sent.append(text)

    await bmain._per_user_credential_preflight(engine, _Bot())
    assert sent == []


class _Card(AccountCommands):
    """The handler with its transport captured. Driven, not grepped: a source
    window over `_cmd_exchange` passes with all three sentences present and the
    branch that picks between them inverted, which is the whole defect."""

    def __init__(self, store):
        self.sent: list[str] = []
        self._store = store

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    async def _guard(self, update, command="", ctx=None) -> bool:
        return True

    def _get_tg_id(self, update) -> str:
        return "1"


async def _exchange_card(monkeypatch, store) -> str:
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: store)
    h = _Card(store)
    await h._cmd_exchange(object(), None)
    assert len(h.sent) == 1
    return h.sent[0]


@pytest.mark.asyncio
async def test_a_user_who_never_connected_is_told_to_connect(tmp_path, monkeypatch):
    out = await _exchange_card(monkeypatch, _store(tmp_path))
    assert "not connected" in out
    assert "/connect" in out


@pytest.mark.asyncio
async def test_a_record_that_will_not_decrypt_is_not_called_connected(
        tmp_path, monkeypatch):
    """The card printed "Status: connected" with an EMPTY Key line, because the
    fingerprint comes from `get()` and `get()` is None for exactly this record.
    A heading that announces itself and then says nothing, on the surface an
    operator reads to find out whether their account is linked."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    out = await _exchange_card(monkeypatch, _rotate_master_key(tmp_path))

    # Anchored to the Status line, not searched loose: "connected" is a
    # substring of "not connected", and asserting a short string is ABSENT is
    # the assertion that keeps misfiring in this repo.
    status = next(ln for ln in out.splitlines() if ln.startswith("Status:"))
    assert "cannot read them" in status, status
    assert "not connected" not in status, (
        "they DID connect — sending an operator to look for a user who never "
        "linked an account is a different hunt")
    assert "Key:" not in out, "a fingerprint line with nothing after it"
    assert "/connect" in out, "the remedy is still theirs to apply"


@pytest.mark.asyncio
async def test_a_readable_record_still_prints_connected_and_a_fingerprint(
        tmp_path, monkeypatch):
    """The control: the good case must not have been made unreachable by the
    branch added above it."""
    s = _store(tmp_path)
    s.set_venue("1", "bitget", BITGET)
    out = await _exchange_card(monkeypatch, s)
    status = next(ln for ln in out.splitlines() if ln.startswith("Status:"))
    assert "connected" in status and "cannot read" not in status
    assert "Key:" in out and "BG-" in out
