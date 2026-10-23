"""Panic Button v0.

Read-only. The flag is off unless PANIC_BUTTON is 1 or true. A reply does
not store the address and does not touch the digest watch list. Ranking is
worst first. Revokes are prepared on the web side; nothing here signs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bot import daily_duel_bot as ddb
from bot.approval_hygiene import (
    INVALID_ADDRESS,
    ChainRead,
    OpenApproval,
)
from bot.approval_reader import ApprovalReader
from bot.approval_watch_store import ApprovalWatchStore
from bot.daily_duel_store import CallStore
from bot.panic_button import (
    CANNOT_RECOVER,
    COULD_READ,
    DEFAULT_PAGE,
    HASH_CAP,
    NEVER_ASK,
    NOT_ADVICE,
    NOT_AVAILABLE,
    SEED_WARNING,
    WHY_DELEGATION,
    WHY_EXPLOIT,
    WHY_FORALL,
    WHY_KNOWN,
    WHY_RECENT_FORALL,
    WHY_RECENT_UNLIM,
    WHY_STALE,
    WHY_UNLIM,
    flag_on,
    on_panic,
    page_url,
    read_panic,
    refuses_secret,
    seal_receipt,
)
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
SALT = "s" * 32
TG = "4242424242"
NOW = 1_762_000_000
DAY = 86400
WALLET = "0x" + "11" * 20
TOKEN_A = "0x" + "aa" * 20
TOKEN_B = "0x" + "bb" * 20
TOKEN_C = "0x" + "cc" * 20
SPEND_EXPLOIT = "0x" + "a1" * 20
SPEND_UNLIM = "0x" + "b2" * 20
SPEND_OLD = "0x" + "c3" * 20
DELEGATE = "0x" + "d0" * 20
KNOWN = "0x" + "e5" * 20
SUPPLY = 10 ** 24
MAX_UINT = 2 ** 256 - 1
SEED = " ".join(["alpha"] * 12)


def _iso(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _List:
    def __init__(self, known):
        self.known = known

    def get(self):
        return None if self.known is None else set(self.known)


class _Reader:
    def __init__(self, chains, delegations):
        self.chains = chains
        self.delegations = delegations
        self.reads = []

    def read(self, address):
        self.reads.append(address)
        return self.chains

    def delegation(self, chain_id, owner):
        self.reads.append(("code", chain_id, owner))
        return self.delegations.get(chain_id, ("none", None))


def _chains():
    return (
        ChainRead(1, "Ethereum", "ok", (
            OpenApproval(1, "Ethereum", TOKEN_B, SPEND_UNLIM, "erc20", MAX_UINT, SUPPLY, True, NOW - 30 * DAY),
            OpenApproval(1, "Ethereum", TOKEN_A, SPEND_EXPLOIT, "erc20", 5, SUPPLY, True, NOW - DAY),
        )),
        ChainRead(8453, "Base", "unreadable"),
        ChainRead(43114, "Avalanche C-Chain", "ok", ()),
        ChainRead(42161, "Arbitrum", "ok", ()),
    )


def _delegations():
    return {
        1: ("delegated", DELEGATE),
        8453: ("unreadable", None),
        43114: ("none", None),
        42161: ("none", None),
    }


def _run_read():
    reader = _Reader(_chains(), _delegations())
    result = read_panic(
        WALLET, reader=reader, exploits=_List({(1, SPEND_EXPLOIT)}), now=NOW,
    )
    return reader, result


def test_the_flag_is_off_unless_it_is_one_or_true(monkeypatch):
    monkeypatch.delenv("PANIC_BUTTON", raising=False)
    assert flag_on({}) is False
    assert flag_on({"PANIC_BUTTON": ""}) is False
    assert flag_on({"PANIC_BUTTON": "0"}) is False
    assert flag_on({"PANIC_BUTTON": "false"}) is False
    assert flag_on({"PANIC_BUTTON": "yes"}) is False
    assert flag_on({"PANIC_BUTTON": "1"}) is True
    assert flag_on({"PANIC_BUTTON": "true"}) is True
    assert flag_on({"PANIC_BUTTON": "TRUE"}) is True
    assert on_panic(WALLET, reader=_Reader((), {}), exploits=_List(set()), now_iso=_iso(NOW), env={}) == [
        NOT_AVAILABLE,
    ]


def test_a_test_wallet_lists_exploit_then_delegation_then_unlimited():
    _reader, result = _run_read()
    kinds = [(item["spender"], item["rank"], item["why"]) for item in result["items"]]
    assert kinds == [
        (SPEND_EXPLOIT, 1, WHY_EXPLOIT),
        (DELEGATE, 2, WHY_DELEGATION),
        (SPEND_UNLIM, 4, WHY_UNLIM),
    ]
    assert result["unread_chains"] == ["Base"]
    blob = json.dumps(result)
    assert '"amount"' not in blob
    assert str(MAX_UINT) not in blob
    assert "telegram" not in blob


def test_recent_unlimited_outranks_a_stale_limited_approval_and_a_known_good_delegation():
    chains = (
        ChainRead(1, "Ethereum", "ok", (
            OpenApproval(1, "Ethereum", TOKEN_A, SPEND_OLD, "erc20", 2, SUPPLY, True, NOW - 200 * DAY),
            OpenApproval(1, "Ethereum", TOKEN_B, SPEND_UNLIM, "erc20", MAX_UINT, SUPPLY, True, NOW - 2 * DAY),
            OpenApproval(1, "Ethereum", TOKEN_C, SPEND_OLD, "erc721_all", None, None, False, NOW - 30 * DAY),
        )),
    )
    reader = _Reader(chains, {1: ("delegated", KNOWN)})
    result = read_panic(
        WALLET, reader=reader, exploits=_List(set()), now=NOW,
        known_good=frozenset({KNOWN}),
    )
    assert [item["why"] for item in result["items"]] == [
        WHY_RECENT_UNLIM, WHY_FORALL, WHY_STALE, WHY_KNOWN,
    ]
    assert WHY_RECENT_FORALL not in [item["why"] for item in result["items"]]


def test_the_reply_names_the_warning_the_gap_and_no_promise(monkeypatch):
    monkeypatch.setenv("PANIC_BUTTON", "1")
    reader = _Reader(_chains(), _delegations())
    texts = on_panic(
        WALLET, reader=reader, exploits=_List({(1, SPEND_EXPLOIT)}), now_iso=_iso(NOW),
        env={"PANIC_BUTTON": "1"},
    )
    blob = "\n".join(texts)
    assert blob.startswith(SEED_WARNING)
    assert NEVER_ASK in blob
    assert COULD_READ in blob
    assert f"We'll never DM you first. The only page is {DEFAULT_PAGE}" in blob
    assert CANNOT_RECOVER in blob
    assert NOT_ADVICE in blob
    assert CANNOT_RECOVER == "We can't recover lost funds."
    assert NOT_ADVICE == "Not financial advice."
    assert WHY_DELEGATION == "active 7702 delegation to a contract we don't recognize"
    assert WHY_KNOWN == "7702 delegation to a contract we recognize"
    page = (ROOT / "app" / "public" / "panic.html").read_text(encoding="utf-8")
    assert f"We'll never DM you first. The only page is {DEFAULT_PAGE}" in page
    assert CANNOT_RECOVER in page
    assert NOT_ADVICE in page
    assert "Could not read: Base." in blob
    assert "you're safe" not in blob.lower()
    assert "you are safe" not in blob.lower()
    assert DEFAULT_PAGE in blob
    assert WALLET not in blob
    assert "$" not in blob and "RCLAW" not in blob.upper()
    assert SPEND_EXPLOIT not in blob and DELEGATE not in blob
    assert all(len(t) <= 1000 for t in texts)
    # The link does not carry the address.
    assert WALLET not in page_url({})
    assert reader.reads  # it did read


def test_a_secret_is_refused_without_an_echo_and_without_a_read():
    reader = _Reader(_chains(), _delegations())
    env = {"PANIC_BUTTON": "true"}
    for raw in (SEED, "ab" * 32, "please paste your seed phrase"):
        texts = on_panic(raw, reader=reader, exploits=_List(set()), now_iso=_iso(NOW), env=env)
        blob = "\n".join(texts)
        assert NEVER_ASK in blob
        assert "alpha" not in blob
        assert "ab" * 8 not in blob
        assert raw not in blob
    assert reader.reads == []
    assert refuses_secret(SEED)
    assert refuses_secret("please paste your seed phrase")
    assert not refuses_secret(WALLET)


def test_a_bad_address_is_not_stored_and_panic_does_not_watch(tmp_path, monkeypatch):
    monkeypatch.setenv("PANIC_BUTTON", "1")
    watches = ApprovalWatchStore(tmp_path / "approval_watches.sqlite")
    calls = CallStore(tmp_path / "calls.sqlite")
    reader = _Reader(_chains(), _delegations())
    bot = ddb.DailyDuelBot(
        salt=SALT, store=calls, watches=watches, reader=reader, exploits=_List({(1, SPEND_EXPLOIT)}),
        now=lambda: _iso(NOW), interval_sec=7 * DAY, poll_sec=3600,
    )
    before = sorted(p.name for p in tmp_path.iterdir())

    def _texts(update):
        import asyncio
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.cmd_panic(update, None))
        return [c.args[0] for c in update.effective_message.reply_text.await_args_list]

    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    msg = NS(reply_text=AsyncMock(), text=f"/panic {WALLET}")
    update = NS(
        effective_user=NS(id=TG, username="p"),
        effective_chat=NS(id=TG, type="private"),
        effective_message=msg, message=msg,
    )
    blob = "\n".join(_texts(update))
    assert SEED_WARNING in blob
    from bot.daily_duel_store import player_key
    assert watches.addresses(player_key(TG, SALT)) == []
    assert WALLET.encode() not in (tmp_path / "approval_watches.sqlite").read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == before

    monkeypatch.delenv("PANIC_BUTTON", raising=False)
    msg2 = NS(reply_text=AsyncMock(), text=f"/panic {WALLET}")
    update2 = NS(
        effective_user=NS(id=TG, username="p"),
        effective_chat=NS(id=TG, type="private"),
        effective_message=msg2, message=msg2,
    )
    import asyncio
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.cmd_panic(update2, None))
    assert update2.effective_message.reply_text.await_args.args[0] == NOT_AVAILABLE

    # The digest door still stores, which is the command that opted in.
    msg3 = NS(reply_text=AsyncMock(), text=f"/watch {WALLET}")
    update3 = NS(
        effective_user=NS(id=TG, username="p"),
        effective_chat=NS(id=TG, type="private"),
        effective_message=msg3, message=msg3,
    )
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.cmd_watch(update3, None))
    assert watches.addresses(player_key(TG, SALT)) == [WALLET]


def test_help_names_panic_and_a_bad_address_is_the_digest_sentence(monkeypatch):
    monkeypatch.setenv("PANIC_BUTTON", "1")
    texts = on_panic("nope", reader=_Reader((), {}), exploits=_List(set()), now_iso=_iso(NOW), env=os.environ)
    assert texts == [INVALID_ADDRESS]
    bot = ddb.DailyDuelBot(salt=SALT, now=lambda: _iso(NOW))
    import asyncio
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    msg = NS(reply_text=AsyncMock(), text="/help")
    update = NS(
        effective_user=NS(id=TG, username="p"),
        effective_chat=NS(id=TG, type="private"),
        effective_message=msg, message=msg,
    )

    class _Gate:
        def rules(self, day=None):
            return {"ok": True, "cutoff_at": "2031-05-06T00:00:00.000Z", "horizon_hours": 17,
                    "price_reader": "getTickers", "price_module": "app/lib/tickers.js"}

    bot.gate = _Gate()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bot.cmd_help(update, None))
    help_text = update.effective_message.reply_text.await_args.args[0]
    assert "/panic <address>" in help_text


def test_the_receipt_holds_hashes_and_a_time_only():
    assert HASH_CAP == 40
    one = "0x" + "ab" * 32
    out = seal_receipt([one, "0x" + "CD" * 32], now="2026-10-23T00:00:00.000Z")
    assert set(out) == {"cleaned_at", "hashes"}
    assert out["cleaned_at"] == "2026-10-23T00:00:00.000Z"
    assert out["hashes"][1] == "0x" + "cd" * 32
    assert "telegram" not in out and "amount" not in out and "address" not in out
    full = seal_receipt([one] * HASH_CAP, now="2026-10-23T00:00:00.000Z")
    assert full["hashes"] == [one] * HASH_CAP
    assert seal_receipt([one] * (HASH_CAP + 1)) == {"error": "bad hashes"}
    assert seal_receipt(["zz"]) == {"error": "bad hashes"}
    assert seal_receipt(["0x" + "ab" * 31]) == {"error": "bad hashes"}
    assert seal_receipt(["0x" + "gg" * 32]) == {"error": "bad hashes"}
    assert seal_receipt(["ab" * 32]) == {"error": "bad hashes"}
    assert seal_receipt([]) == {"error": "bad hashes"}


def test_delegation_code_is_read_and_a_failure_is_not_none():
    delegated = "0xef0100" + "d0" * 20
    plain = "0x"
    contract = "0x" + "60" * 40

    def transport(url):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        addr = q.get("address", [""])[0].lower()
        if addr.endswith("11" * 2):
            return {"jsonrpc": "2.0", "id": 1, "result": delegated}
        if addr.endswith("22" * 2):
            return {"jsonrpc": "2.0", "id": 1, "result": plain}
        if addr.endswith("33" * 2):
            return {"jsonrpc": "2.0", "id": 1, "result": contract}
        return {"status": "0", "message": "NOTOK", "result": None}

    reader = ApprovalReader(api_key="", transport=transport)
    status, who = reader.delegation(1, "0x" + "11" * 20)
    assert status == "delegated" and who == "0x" + "d0" * 20
    assert reader.delegation(1, "0x" + "22" * 20) == ("none", None)
    assert reader.delegation(1, "0x" + "33" * 20) == ("none", None)
    assert reader.delegation(1, "0x" + "44" * 20) == ("unreadable", None)


def test_no_sign_or_broadcast_path_and_no_private_key_variable():
    banned = (
        "eth_sign", "personal_sign", "signTransaction", "sign_transaction",
        "eth_sendTransaction", "eth_sendRawTransaction", "sendRawTransaction",
        "broadcastTransaction", "private_key", "privateKey", "PRIVATE_KEY",
        "relayer_key", "relayerKey", "mnemonic", ".sign(", "new Wallet",
    )
    files = [
        ROOT / "bot" / "panic_button.py",
        ROOT / "bot" / "daily_duel_bot.py",
        ROOT / "app" / "lib" / "panic_tx.js",
        ROOT / "app" / "lib" / "panic_flag.js",
        ROOT / "app" / "routes" / "panic.js",
        ROOT / "app" / "routes" / "panic_receipt.js",
        ROOT / "app" / "public" / "js" / "panic.js",
        ROOT / "app" / "public" / "panic.html",
    ]
    for path in files:
        src = code_only(path.read_text(encoding="utf-8")) if path.suffix == ".py" else path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in src, f"{path.name} contains {word}"
