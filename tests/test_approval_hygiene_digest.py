"""Approval hygiene digest on the anonymous Daily Duel bot.

Opt-in only. The store keeps the address and a salted chat key. /stop deletes
both the addresses and the schedule. Counts follow revoke.cash: an ERC-20 is
unlimited only when the allowance is greater than a known non-zero supply, an
ApprovalForAll is unlimited, and 180 days is a strict cut.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from bot import daily_duel_bot as ddb
from bot.approval_digest import ApprovalDigest
from bot.approval_hygiene import (
    DIGEST_HELP,
    EXPLOIT_UNREAD,
    HEADER,
    INVALID_ADDRESS,
    MESSAGE_MAX,
    NOT_OPTED,
    SECRET_REFUSAL,
    SECRET_REQUEST,
    STALE_AFTER_SECONDS,
    STOPPED,
    STOPPED_EMPTY,
    ChainRead,
    OpenApproval,
    digest_interval_sec,
    digest_poll_sec,
    format_iso,
    looks_like_secret,
    render_digest,
    revoke_url,
    shift_iso,
    stale_of,
    summarize_address,
    unlimited_of,
)
from bot.approval_reader import (
    EXPLOIT_INDEX,
    TOPIC_APPROVAL,
    TOPIC_FOR_ALL,
    ApprovalReader,
    ExploitDirectory,
)
from bot.approval_watch_store import ApprovalWatchStore
from bot.daily_duel_store import CallStore, player_key
from tests.source_scan import code_only

SALT = "s" * 32
TG = "4242424242"
OTHER = "7777777777"
NOW = 1_762_000_000
DAY = 86400
WALLET = "0x" + "11" * 20
OTHER_WALLET = "0x" + "22" * 20
TOKEN_A = "0x" + "aa" * 20
TOKEN_B = "0x" + "bb" * 20
TOKEN_C = "0x" + "cc" * 20
TOKEN_D = "0x" + "dd" * 20
TOKEN_E = "0x" + "ee" * 20
SPEND_A = "0x" + "a1" * 20
SPEND_B = "0x" + "b2" * 20
SPEND_C = "0x" + "c3" * 20
SPEND_D = "0x" + "d4" * 20
SPEND_E = "0x" + "e5" * 20
MAX_UINT = 2 ** 256 - 1
SUPPLY_A = 10 ** 24
SEED = " ".join(["alpha"] * 12)
ROOT_BOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _iso(ts: int) -> str:
    return format_iso(datetime.fromtimestamp(ts, timezone.utc))


def _stored_rows(watches) -> int:
    import sqlite3
    conn = sqlite3.connect(watches.path)
    try:
        addresses = conn.execute("SELECT COUNT(*) FROM approval_watches").fetchone()[0]
        schedule = conn.execute("SELECT COUNT(*) FROM approval_schedule").fetchone()[0]
    finally:
        conn.close()
    return int(addresses) + int(schedule)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _update(text: str = "", user_id: str = TG):
    msg = NS(reply_text=AsyncMock(), text=text)
    return NS(
        effective_user=NS(id=user_id, username="p"),
        effective_chat=NS(id=user_id, type="private"),
        effective_message=msg, message=msg, callback_query=None,
    )


def _texts(update) -> list[str]:
    return [c.args[0] for c in update.effective_message.reply_text.await_args_list]


def _word(n: int) -> str:
    return "0x" + f"{n:064x}"


def _topic(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0")


def _log(token: str, topics: list[str], data: str, ts: int, block: int, index: int) -> dict:
    return {
        "address": token,
        "topics": topics,
        "data": data,
        "timeStamp": hex(ts),
        "blockNumber": hex(block),
        "logIndex": hex(index),
    }


def _approval_logs() -> list[dict]:
    old = NOW - 200 * DAY
    fresh = NOW - 10 * DAY
    return [
        _log(TOKEN_A, [TOPIC_APPROVAL, _topic(WALLET), _topic(SPEND_A)], _word(MAX_UINT), old, 10, 1),
        _log(TOKEN_B, [TOPIC_APPROVAL, _topic(WALLET), _topic(SPEND_B)], _word(50), fresh, 11, 1),
        _log(TOKEN_D, [TOPIC_APPROVAL, _topic(WALLET), _topic(SPEND_D)], _word(7), old, 12, 1),
        _log(TOKEN_E, [TOPIC_APPROVAL, _topic(WALLET), _topic(SPEND_E)], _word(MAX_UINT), old, 13, 1),
    ]


def _forall_logs() -> list[dict]:
    # One day past the cutoff, so this operator approval stays in the stale count.
    past = NOW - STALE_AFTER_SECONDS - DAY
    return [_log(TOKEN_C, [TOPIC_FOR_ALL, _topic(WALLET), _topic(SPEND_C)], _word(1), past, 14, 1)]


def _call_result(to: str, data: str) -> dict:
    sel = data[:10]
    if sel == "0x01ffc9a7":
        return {"jsonrpc": "2.0", "result": _word(1 if to in (TOKEN_C, TOKEN_D) else 0)}
    if sel == "0x18160ddd":
        supply = {TOKEN_A: SUPPLY_A, TOKEN_B: 1000, TOKEN_E: 1000}.get(to)
        if supply is None:
            return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}
        return {"jsonrpc": "2.0", "result": _word(supply)}
    if sel == "0xdd62ed3e":
        spender = "0x" + data[-40:]
        amounts = {(TOKEN_A, SPEND_A): MAX_UINT, (TOKEN_B, SPEND_B): 50, (TOKEN_E, SPEND_E): 0}
        amount = amounts.get((to, spender))
        if amount is None:
            return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}
        return {"jsonrpc": "2.0", "result": _word(amount)}
    if sel == "0xe985e9c5":
        return {"jsonrpc": "2.0", "result": _word(1 if to == TOKEN_C else 0)}
    if sel == "0x081812fc":
        if to == TOKEN_D:
            return {"jsonrpc": "2.0", "result": _word(int(SPEND_D, 16))}
        return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}
    return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}


def _transport(url: str) -> dict:
    query = parse_qs(urlparse(url).query)
    on_eth = "/evm/1/" in url or query.get("chainid") == ["1"]
    module = query.get("module", [""])[0]
    if module == "logs":
        if not on_eth or int(query.get("page", ["1"])[0]) > 1:
            return {"status": "0", "message": "No records found", "result": []}
        topic = query.get("topic0", [""])[0].lower()
        if topic == TOPIC_APPROVAL:
            return {"status": "1", "message": "OK", "result": _approval_logs()}
        if topic == TOPIC_FOR_ALL:
            return {"status": "1", "message": "OK", "result": _forall_logs()}
        return {"status": "0", "message": "No records found", "result": []}
    if module == "proxy":
        if not on_eth:
            return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}
        return _call_result(query["to"][0].lower(), query["data"][0].lower())
    raise AssertionError(module)


def _reader() -> ApprovalReader:
    return ApprovalReader(api_key="", transport=_transport, now=lambda: NOW)


def _exploits() -> set[tuple[int, str]]:
    return {(1, SPEND_B)}


class _List:
    def __init__(self, known):
        self.known = known

    def get(self):
        return None if self.known is None else set(self.known)


def _bot(tmp_path, reader=None, exploits=None, now: int = NOW):
    watches = ApprovalWatchStore(tmp_path / "approval_watches.sqlite")
    calls = CallStore(tmp_path / "calls.sqlite")
    bot = ddb.DailyDuelBot(
        salt=SALT, store=calls, watches=watches,
        reader=reader if reader is not None else _reader(),
        exploits=_List(exploits if exploits is not None else _exploits()),
        now=lambda: _iso(now), interval_sec=7 * DAY, poll_sec=3600,
    )
    return bot, watches, calls


def _watch(bot, address: str = WALLET, user_id: str = TG):
    update = _update(f"/watch {address}", user_id=user_id)
    _run(bot.cmd_watch(update, None))
    return update


def test_unlimited_matches_revoke_cash_and_one_hundred_eighty_days_is_strict():
    old = OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", MAX_UINT, SUPPLY_A, True, NOW - 200 * DAY)
    equal = OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", 1000, 1000, True, NOW)
    just_over = OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", 1001, 1000, True, NOW)
    zero_supply = OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", MAX_UINT, 0, True, NOW)
    missing = OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", MAX_UINT, None, False, NOW)
    forall = OpenApproval(1, "Ethereum", TOKEN_C, SPEND_C, "erc721_all", None, None, False, NOW)
    single = OpenApproval(1, "Ethereum", TOKEN_D, SPEND_D, "erc721_single", None, None, False, NOW)
    assert unlimited_of(old) is True
    assert unlimited_of(equal) is False
    assert unlimited_of(just_over) is True
    assert unlimited_of(zero_supply) is False
    assert unlimited_of(missing) is None
    assert unlimited_of(forall) is True
    assert unlimited_of(single) is False
    assert STALE_AFTER_SECONDS == 180 * DAY
    assert stale_of(NOW - 100 * DAY, NOW) is False
    assert stale_of(NOW - STALE_AFTER_SECONDS, NOW) is False
    assert stale_of(NOW - STALE_AFTER_SECONDS - 1, NOW) is True
    assert stale_of(None, NOW) is None


def test_digest_for_a_test_wallet_matches_revoke_cash():
    chains = _reader().read(WALLET)
    eth = next(c for c in chains if c.chain_id == 1)
    assert eth.status == "ok"
    standards = sorted((row.standard, row.spender) for row in eth.approvals)
    assert ( "erc20", SPEND_E) not in standards
    summary = summarize_address(WALLET, chains, NOW, _exploits())
    assert summary.unlimited == 2
    assert summary.stale == 3
    assert summary.scam == 1
    assert summary.unlimited_complete and summary.stale_complete
    assert summary.gaps == ()
    texts = render_digest([summary], scam_known=True)
    assert len(texts) == 1 and len(texts[0]) <= MESSAGE_MAX
    text = texts[0]
    assert "You have 2 unlimited approvals." in text
    assert "You have 3 approvals older than 180 days." in text
    assert "90 days" not in text
    assert "You have 1 approval to a spender on the public exploit list." in text
    assert revoke_url(WALLET) in text
    assert "revoke.cash/address/" in text
    assert "$" not in text and "RCLAW" not in text.upper()
    for chain in chains:
        if chain.chain_id != 1:
            assert chain.status == "ok" and chain.approvals == ()


def test_the_digest_object_runs_without_the_duel_bot(tmp_path):
    digest = ApprovalDigest(
        salt=SALT,
        store=ApprovalWatchStore(tmp_path / "approval_watches.sqlite"),
        reader=_reader(),
        exploits=_List(_exploits()),
        now=lambda: _iso(NOW),
        interval_sec=7 * DAY,
        poll_sec=3600,
    )
    reply = _run(digest.on_watch(TG, WALLET))
    assert reply.startswith("Watching")
    texts = _run(digest.on_digest(TG))
    assert texts and "You have 3 approvals older than 180 days." in texts[0]
    assert _run(digest.on_stop(TG)) == STOPPED
    assert _stored_rows(digest.watches) == 0


def test_an_unreadable_chain_is_not_reported_as_zero_and_a_missing_list_is_not_clean():
    chains = []
    for cid, name in ((1, "Ethereum"), (8453, "Base"), (43114, "Avalanche C-Chain"), (42161, "Arbitrum")):
        status = "unreadable" if cid == 1 else "ok"
        chains.append(ChainRead(cid, name, status, ()))
    summary = summarize_address(WALLET, chains, NOW, _exploits())
    text = render_digest([summary], scam_known=True)[0]
    assert "Ethereum could not be read in full" in text
    assert "You have no unlimited approvals." not in text
    assert "on the chains that were read" in text
    missing = render_digest([summary], scam_known=False)[0]
    assert EXPLOIT_UNREAD in missing
    assert "You have no approvals to a spender on the public exploit list." not in missing


def test_five_addresses_stay_under_a_thousand_characters_and_name_no_price():
    rows = []
    for i in range(5):
        addr = "0x" + f"{i:040x}"
        rows.append(summarize_address(addr, (
            ChainRead(1, "Ethereum", "ok", (
                OpenApproval(1, "Ethereum", TOKEN_A, SPEND_A, "erc20", MAX_UINT, SUPPLY_A, True, NOW - 200 * DAY),
            )),
            ChainRead(8453, "Base", "truncated", ()),
            ChainRead(43114, "Avalanche C-Chain", "ok", ()),
            ChainRead(42161, "Arbitrum", "ok", ()),
        ), NOW, _exploits()))
    texts = render_digest(rows, scam_known=True)
    assert texts
    assert all(len(t) <= MESSAGE_MAX for t in texts)
    blob = "\n".join(texts)
    assert "$" not in blob and "RCLAW" not in blob.upper() and "usd" not in blob.lower()
    for row in rows:
        assert revoke_url(row.address) in blob


def test_stop_deletes_addresses_and_the_schedule_and_leaves_the_duel_call(tmp_path):
    bot, watches, calls = _bot(tmp_path)
    key = player_key(TG, SALT)
    other = player_key(OTHER, SALT)
    calls.record({
        "player_key": key, "day": "2026-10-23", "pick": "long", "symbol": "ETHUSDT",
        "seal": "ab" * 32, "seal_payload": json.dumps({"pick": "long"}),
        "picked_at": _iso(NOW), "resolves_at": _iso(NOW + DAY),
    }, telegram_id=TG)
    added = _watch(bot)
    assert "Watching" in _texts(added)[0]
    assert watches.addresses(key) == [WALLET]
    assert watches.next_digest_at(key) == shift_iso(_iso(NOW), 7 * DAY)
    assert bot.delivery[key] == TG
    watches.add(other, OTHER_WALLET, _iso(NOW), shift_iso(_iso(NOW), 7 * DAY))
    stopped = _update("/stop")
    _run(bot.cmd_stop(stopped, None))
    assert _texts(stopped) == [STOPPED]
    assert watches.addresses(key) == []
    assert watches.next_digest_at(key) is None
    assert key not in bot.delivery
    assert watches.addresses(other) == [OTHER_WALLET]
    assert calls.latest(key) is not None
    assert calls.latest(key)["pick"] == "long"
    raw = (tmp_path / "approval_watches.sqlite").read_bytes()
    assert b"telegram" not in raw.lower()
    assert WALLET.encode() not in raw
    assert OTHER_WALLET.encode() in raw
    again = _update("/stop")
    _run(bot.cmd_stop(again, None))
    assert _texts(again) == [STOPPED_EMPTY]


def test_watch_is_opt_in_capped_and_refuses_a_secret_without_echoing_it(tmp_path):
    bot, watches, _calls = _bot(tmp_path)
    key = player_key(TG, SALT)
    early = _update("/digest")
    _run(bot.cmd_digest(early, None))
    assert _texts(early) == [NOT_OPTED]
    assert _stored_rows(watches) == 0
    bad = _watch(bot, "not-an-address")
    assert _texts(bad) == [INVALID_ADDRESS]
    secret = _update(f"/watch {SEED}")
    _run(bot.cmd_watch(secret, None))
    assert _texts(secret) == [SECRET_REFUSAL]
    assert SEED not in _texts(secret)[0]
    assert "alpha" not in _texts(secret)[0]
    assert _stored_rows(watches) == 0
    key_like = _update("/watch " + "ab" * 32)
    _run(bot.cmd_watch(key_like, None))
    assert _texts(key_like) == [SECRET_REFUSAL]
    assert "ab" * 32 not in _texts(key_like)[0]
    for i in range(5):
        _watch(bot, "0x" + f"{i + 1:040x}")
    assert len(watches.addresses(key)) == 5
    sixth = _watch(bot, "0x" + "ff" * 20)
    assert "maximum" in _texts(sixth)[0]
    assert len(watches.addresses(key)) == 5
    again = _watch(bot, "0x" + f"{1:040x}")
    assert "already watched" in _texts(again)[0]
    assert _stored_rows(watches) == 6  # 5 addresses and one schedule
    try:
        watches.add(TG, WALLET, _iso(NOW))
    except ValueError:
        pass
    else:
        raise AssertionError("a raw chat id was accepted as a key")


def test_weekly_send_uses_the_schedule_and_a_restart_needs_a_new_binding(tmp_path):
    bot, watches, _calls = _bot(tmp_path)
    _watch(bot)
    key = player_key(TG, SALT)
    sent: list[tuple[str, str]] = []

    async def _send(chat_id: str, text: str) -> None:
        sent.append((chat_id, text))

    assert _run(bot.dispatch_due(_send)) == 0
    assert sent == []
    due_bot, _, _ = _bot(tmp_path, now=NOW + 7 * DAY)
    due_bot._watches = watches
    assert _run(due_bot.dispatch_due(_send)) == 0
    due_bot.delivery[key] = TG
    assert _run(due_bot.dispatch_due(_send)) == 1
    assert sent[0][0] == TG
    assert "You have 2 unlimited approvals." in sent[0][1]
    assert revoke_url(WALLET) in sent[0][1]
    assert _run(due_bot.dispatch_due(_send)) == 0
    assert watches.next_digest_at(key) == shift_iso(_iso(NOW + 7 * DAY), 7 * DAY)


def test_a_message_after_restart_delivers_a_due_digest(tmp_path):
    bot, watches, _calls = _bot(tmp_path)
    _watch(bot)
    key = player_key(TG, SALT)
    restarted, _, _ = _bot(tmp_path, now=NOW + 7 * DAY)
    restarted._watches = watches
    assert restarted.delivery == {}

    class _Sender:
        def __init__(self):
            self.sent: list[tuple[int, str]] = []

        async def send_message(self, chat_id, text, **_kwargs):
            self.sent.append((chat_id, text))

    sender = _Sender()
    _run(restarted.on_any_update(_update("hello"), NS(bot=sender)))
    assert sender.sent
    assert sender.sent[0][0] == int(TG)
    assert "You have 2 unlimited approvals." in sender.sent[0][1]
    assert restarted.delivery[key] == TG
    assert watches.next_digest_at(key) == shift_iso(_iso(NOW + 7 * DAY), 7 * DAY)


def test_the_bot_never_asks_for_a_key_and_the_sources_are_named():
    files = [
        ROOT_BOT / "bot" / "approval_digest.py",
        ROOT_BOT / "bot" / "approval_hygiene.py",
        ROOT_BOT / "bot" / "approval_reader.py",
        ROOT_BOT / "bot" / "approval_watch_store.py",
        ROOT_BOT / "bot" / "daily_duel_bot.py",
    ]
    for path in files:
        src = code_only(path.read_text(encoding="utf-8"))
        assert SECRET_REQUEST.search(src) is None, path.name
        assert "eth_sign" not in src
        assert "$RCLAW" not in src
    reader = (ROOT_BOT / "bot" / "approval_reader.py").read_text(encoding="utf-8")
    assert EXPLOIT_INDEX in reader
    assert "https://api.etherscan.io/v2/api" in reader
    assert "api.routescan.io" in reader
    assert "ETHERSCAN_API_KEY" in reader
    assert not re.search(r"apikey=[A-Za-z0-9]{8,}", reader)
    assert looks_like_secret(SEED) and looks_like_secret("ab" * 32)
    assert digest_interval_sec({}) == 7 * DAY
    assert digest_interval_sec({"APPROVAL_DIGEST_INTERVAL_SEC": "nope"}) == 7 * DAY
    assert digest_poll_sec({}) == 3600
    assert "180 days" in DIGEST_HELP and "90 days" not in DIGEST_HELP
    assert "revoke.cash" in DIGEST_HELP
    digest_src = code_only((ROOT_BOT / "bot" / "approval_digest.py").read_text(encoding="utf-8"))
    assert "daily_duel_bot" not in digest_src
    assert "telegram_handler" not in digest_src
    assert "bot.core.engine" not in digest_src
    assert SECRET_REQUEST.search(DIGEST_HELP) is None
    assert SECRET_REQUEST.search(HEADER) is None
    assert SECRET_REQUEST.search(SECRET_REFUSAL) is None


def test_help_names_the_digest_commands(tmp_path):
    bot, _watches, _calls = _bot(tmp_path)

    class _Gate:
        def rules(self, day=None):
            return {
                "ok": True, "cutoff_at": "2031-05-06T00:00:00.000Z", "horizon_hours": 17,
                "price_reader": "getTickers", "price_module": "app/lib/tickers.js",
            }

    bot.gate = _Gate()
    update = _update("/help")
    _run(bot.cmd_help(update, None))
    text = _texts(update)[0]
    assert "/watch" in text and "/digest" in text and "/stop" in text
    assert "revoke.cash" in text
    assert ddb.PRIVACY in text
    assert SECRET_REQUEST.search(text) is None


def test_store_columns_are_the_address_and_the_salted_key(tmp_path):
    watches = ApprovalWatchStore(tmp_path / "approval_watches.sqlite")
    import sqlite3
    conn = sqlite3.connect(tmp_path / "approval_watches.sqlite")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"approval_watches", "approval_schedule"}
    watch_cols = [r[1] for r in conn.execute("PRAGMA table_info(approval_watches)")]
    sched_cols = [r[1] for r in conn.execute("PRAGMA table_info(approval_schedule)")]
    assert watch_cols == ["chat_key", "address", "added_at"]
    assert sched_cols == ["chat_key", "next_digest_at"]
    conn.close()
    key = player_key(TG, SALT)
    watches.add(key, WALLET, _iso(NOW))
    assert watches.addresses(key) == [WALLET]


def test_a_truncated_log_page_is_left_out_of_the_counts():
    def transport(url: str) -> dict:
        query = parse_qs(urlparse(url).query)
        if query.get("module", [""])[0] != "logs":
            return {"jsonrpc": "2.0", "error": {"code": 3, "message": "reverted"}}
        if "/evm/1/" not in url:
            return {"status": "0", "message": "No records found", "result": []}
        if query.get("topic0", [""])[0].lower() != TOPIC_APPROVAL:
            return {"status": "0", "message": "No records found", "result": []}
        row = _approval_logs()[0]
        return {"status": "1", "message": "OK", "result": [row]}

    reader = ApprovalReader(api_key="", transport=transport, now=lambda: NOW, max_pages=1, page_size=1)
    eth = next(c for c in reader.read(WALLET) if c.chain_id == 1)
    assert eth.status == "truncated" and eth.approvals == ()
    summary = summarize_address(WALLET, reader.read(WALLET), NOW, set())
    text = render_digest([summary], scam_known=True)[0]
    assert "Ethereum could not be read in full" in text
    assert "You have 1 unlimited approval." not in text


def test_exploit_directory_keeps_addresses_only():
    pages = {
        EXPLOIT_INDEX: ["sample-exploit"],
        "https://raw.githubusercontent.com/RevokeCash/approval-exploit-list/main/exploits/sample-exploit.json": {
            "name": "Sample",
            "amount": 1500000,
            "addresses": [
                {"chainId": 1, "address": SPEND_B},
                {"chainId": 8453, "address": SPEND_B.upper()},
            ],
        },
    }

    def transport(url: str):
        if url not in pages:
            raise RuntimeError("missing")
        return pages[url]

    found = ExploitDirectory(transport=transport).get()
    assert found == {(1, SPEND_B), (8453, SPEND_B)}
    assert 1500000 not in (found or set())

    def down(_url: str):
        raise RuntimeError("down")

    assert ExploitDirectory(transport=down).get() is None
