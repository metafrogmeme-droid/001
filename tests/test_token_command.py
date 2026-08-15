"""`/token` — the command that makes the detective answerable.

Four scorers, a composer and an orchestrator existed and no human could reach
any of them: `token_dossier` and `presale_claims` were imported by zero
non-test modules, and `token_research.investigate` by nothing at all. A scorer
nobody can invoke is indistinguishable from one that does not work.

These tests DRIVE the handler body rather than grepping it. The distinction is
the one CLAUDE.md records from #999: a Telegram card was built inline, source-
scanned, shipped — and rendered zero times in production, because the callback
received prose where the lookup expected symbols. The code was present. It was
never reached, and no scan can tell those apart.

So the handler is called with a stub `self` carrying only what it touches, and
the assertions are about what the user is actually sent.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.skills.telegram_handler import TelegramHandler


class _Stub:
    """The four members `_cmd_token` (and its @guard wrapper) reach for."""

    _EVM_ADDR_RE = TelegramHandler._EVM_ADDR_RE

    def __init__(self, allowed=True):
        self.sent: list[str] = []
        self.errors: list[tuple] = []
        self._allowed = allowed

    async def _guard(self, update, command="", ctx=None):
        self.guarded = command
        return self._allowed

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    async def _send_error(self, update, command_name, exc):
        self.errors.append((command_name, exc))


class _Ctx:
    def __init__(self, *args):
        self.args = list(args)


def _run(stub, *args):
    return asyncio.run(TelegramHandler._cmd_token(stub, object(), _Ctx(*args)))


@pytest.fixture
def spy(monkeypatch):
    """Replace `investigate` with a recorder returning a planted dossier."""
    calls = {}

    async def fake_investigate(address, chain="eth", **kw):
        calls["address"] = address
        calls["chain"] = chain
        return {"planted": True}

    monkeypatch.setattr("bot.core.token_research.investigate", fake_investigate)
    monkeypatch.setattr("bot.core.token_research.human_readable",
                        lambda r: "VERDICT: unproven\n   deployer: 0xabc")
    return calls


GOOD = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


# ── the gate runs first ───────────────────────────────────────────────────

def test_the_auth_gate_runs_before_anything_is_investigated(spy):
    stub = _Stub(allowed=False)
    _run(stub, GOOD)
    assert stub.sent == [], "a refused caller still got a reply"
    assert "address" not in spy, "a refused caller still triggered a lookup"


def test_the_command_is_registered_under_its_own_name():
    import inspect
    src = inspect.getsource(TelegramHandler)
    assert '("token", self._cmd_token)' in src, "/token is not registered"
    # /research was already taken by the symbol research card — a different
    # feature. Registering over it would have silently replaced that command.
    assert '("research", self._cmd_research)' in src


# ── input handling: nothing checked must never read as nothing found ──────

def test_no_argument_explains_itself_and_investigates_nothing(spy):
    stub = _Stub()
    _run(stub)
    assert "address" not in spy
    assert "Usage" in stub.sent[0]
    assert "/token" in stub.sent[0]


def test_a_malformed_address_says_nothing_was_checked(spy):
    stub = _Stub()
    _run(stub, "not-an-address")
    assert "address" not in spy, "a typo sent a request"
    assert "Nothing was checked" in stub.sent[0], (
        "a rejected input must not read like a completed clean scan")


@pytest.mark.parametrize("bad", [
    "0x123",                                     # too short
    "0x" + "g" * 40,                             # not hex
    "dAC17F958D2ee523a2206206994597C13D831ec7",  # missing 0x
])
def test_near_miss_addresses_are_rejected(bad, spy):
    stub = _Stub()
    _run(stub, bad)
    assert "address" not in spy, f"{bad!r} was sent to an explorer"


# ── the happy path actually reaches the orchestrator ──────────────────────

def test_a_valid_address_reaches_investigate_and_renders_its_dossier(spy):
    stub = _Stub()
    _run(stub, GOOD)
    assert spy["address"] == GOOD, "the handler never called the detective"
    assert spy["chain"] == "eth", "default chain"
    assert "VERDICT: unproven" in stub.sent[0]
    assert "0xabc" in stub.sent[0], "the deployer must reach the user"


def test_the_chain_argument_is_passed_through(spy):
    stub = _Stub()
    _run(stub, GOOD, "BASE")
    assert spy["chain"] == "base", "chain must be normalised and forwarded"


def test_the_dossier_is_escaped_before_it_is_sent(monkeypatch):
    """The reply is sent as HTML. Unescaped `<` truncates the message."""
    async def fake_investigate(address, chain="eth", **kw):
        return {}
    monkeypatch.setattr("bot.core.token_research.investigate", fake_investigate)
    monkeypatch.setattr("bot.core.token_research.human_readable",
                        lambda r: "owner <script> & co")
    stub = _Stub()
    _run(stub, GOOD)
    assert "&lt;script&gt;" in stub.sent[0] and "&amp;" in stub.sent[0]
    assert "<script>" not in stub.sent[0]


# ── a crash is not a clean bill of health ─────────────────────────────────

def test_a_failed_investigation_is_an_error_not_a_verdict(monkeypatch):
    async def boom(address, chain="eth", **kw):
        raise RuntimeError("explorer exploded")
    monkeypatch.setattr("bot.core.token_research.investigate", boom)
    stub = _Stub()
    _run(stub, GOOD)
    assert stub.errors and stub.errors[0][0] == "token"
    assert stub.sent == [], (
        "a crashed investigation must not also send a dossier-shaped reply")


# ── the address pattern itself ────────────────────────────────────────────

def test_the_address_pattern_is_anchored():
    """An unanchored pattern matches an address buried in arbitrary text."""
    rx = TelegramHandler._EVM_ADDR_RE
    assert rx.match(GOOD)
    assert not rx.match(f"send to {GOOD} now")
    assert not rx.match(GOOD + "00")
    assert not rx.match(GOOD + " ")
