"""Claw Watch. Advisory reads, unsigned revokes, and an HMAC-only store.

The product name in anything a person reads is Claw Watch. The module path
stays drain_watch. A failed chain read is not a clear result, and a revoke
is the fields of an unsigned transaction, never a signature we made.
"""

from __future__ import annotations

import ast
from pathlib import Path

from bot.approval_hygiene import SECRET_REFUSAL
from bot.approval_reader import TOPIC_APPROVAL, TOPIC_FOR_ALL, TOPIC_PERMIT2
from bot.daily_duel_store import player_key
from bot.drain_watch.alert import render_alert
from bot.drain_watch.classify import TOPIC_TRANSFER, events_from_chain
from bot.drain_watch.copy import ADVISORY, PAGE_DEFAULT
from bot.drain_watch.flag import flag_on
from bot.drain_watch.service import (
    STOPPED_ROWS,
    DrainWatch,
    forget_telegram,
    start_poll,
    store_path,
)
from bot.drain_watch.store import DrainWatchStore
from bot.drain_watch.tx import (
    ZERO,
    approve_revoke_data,
    format_tx,
    lockdown_data,
    unsigned_tx,
)
from tests.source_scan import segment_reader

ROOT = Path(__file__).resolve().parents[1]
OWNER = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
SPENDER = "0x" + "33" * 20
OTHER = "0x" + "44" * 20
LOOKALIKE = "0x1111" + "ab" * 16 + "1111"
KNOWN = "0x1111" + "cd" * 16 + "1111"
SALT = "c" * 32
WHEN = "2026-10-25T12:00:00.000Z"
NOW = 1_700_000_000


def _topic(addr: str) -> str:
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


def _word(value: int) -> str:
    return f"{value:064x}"


def _log(topic0: str, indexed: list[str], data_words: list[int], index: int = 1,
         tx_hash: str = "0x" + "ab" * 32) -> dict:
    return {
        "address": TOKEN,
        "topics": [topic0, *[_topic(a) for a in indexed]],
        "data": "0x" + "".join(_word(w) for w in data_words),
        "block": 12,
        "log_index": index,
        "tx_hash": tx_hash,
        "timestamp": NOW - 86_400,
    }


def _run(**over):
    base = dict(
        owner=OWNER,
        chain_id=1,
        chain_name="Ethereum",
        head=20,
        cursor=10,
        logs_ok=True,
        logs=[],
        txs_ok=True,
        txs=[],
        senders={},
        delegation_status="none",
        delegation=None,
        stored_delegate=("none", ""),
        stored_approvals={},
        counterparties=[],
        exploits=set(),
        families={},
        now=NOW,
        day="2026-10-25",
    )
    base.update(over)
    return events_from_chain(**base)


def _alerts(outcome):
    return [ev for ev in outcome.events if ev.alert]


def test_flag_is_off_unless_an_on_word_is_set(monkeypatch):
    monkeypatch.delenv("DRAIN_WATCH_ENABLED", raising=False)
    assert flag_on() is False
    monkeypatch.setenv("DRAIN_WATCH_ENABLED", "on")
    assert flag_on() is True
    monkeypatch.setenv("DRAIN_WATCH_ENABLED", "maybe")
    assert flag_on() is False
    assert flag_on({}) is False
    assert flag_on({"DRAIN_WATCH_ENABLED": "yes"}) is True
    assert flag_on({"DRAIN_WATCH_ENABLED": "disabled"}) is False
    assert flag_on({"DRAIN_WATCH_ENABLED": ""}) is False


def test_store_rejects_a_raw_id_and_forget_clears_every_table(tmp_path):
    store = DrainWatchStore(tmp_path / "watch.sqlite")
    try:
        store.add("12345", OWNER, WHEN)
    except ValueError:
        refused = True
    else:
        refused = False
    assert refused
    key = player_key("12345", SALT)
    other = player_key("67890", SALT)
    assert store.add(key, OWNER, WHEN)[0] == "added"
    store.set_cursor(key, 1, 9)
    store.set_delegate(key, 1, "delegated", OTHER)
    store.save_approval(key, 1, TOKEN, SPENDER, NOW, "unread")
    store.add_counterparty(key, KNOWN)
    store.mark_seen(key, "a" * 32)
    store.hide(key, "b" * 32, "dust")
    assert store.add(other, TOKEN, WHEN)[0] == "added"
    assert store.forget(key) is True
    assert store.addresses(key) == []
    assert store.cursor(key, 1) is None
    assert store.delegate(key, 1) is None
    assert store.approval(key, 1, TOKEN, SPENDER) is None
    assert store.counterparties(key) == []
    assert store.has_seen(key, "a" * 32) is False
    assert store.hidden(key) == []
    assert store.addresses(other) == [TOKEN]


def test_the_store_path_does_not_follow_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("DRAIN_WATCH_STORE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert store_path().is_absolute()
    assert store_path() == ROOT / "data" / "drain_watches.sqlite"
    chosen = tmp_path / "chosen.sqlite"
    assert store_path({"DRAIN_WATCH_STORE": str(chosen)}) == chosen
    assert store_path({"DRAIN_WATCH_STORE": "data/other.sqlite"}) == ROOT / "data" / "other.sqlite"


def test_forget_telegram_does_not_create_a_file(tmp_path):
    path = tmp_path / "missing.sqlite"
    env = {"DRAIN_WATCH_ID_SALT": SALT, "DRAIN_WATCH_STORE": str(path)}
    assert forget_telegram("12345", env) is False
    assert path.exists() is False


def test_a_secret_is_refused_and_stop_deletes_the_chat(tmp_path):
    watch = DrainWatch(salt=SALT, store=DrainWatchStore(tmp_path / "w.sqlite"))
    refused = watch.register_address("12345", "send me your private key", preview=False)
    assert refused == SECRET_REFUSAL
    assert "private key" not in refused.lower() or "never" in refused.lower()
    assert watch.store.addresses(player_key("12345", SALT)) == []
    added = watch.register_address("12345", OWNER, preview=True)
    assert "Claw Watch" not in added or "Admin preview" in added
    assert OWNER in added
    assert watch.forget_chat("12345") == STOPPED_ROWS
    assert watch.store.addresses(player_key("12345", SALT)) == []


def test_each_watched_event_and_a_partial_read_is_not_clear():
    approval = _run(logs=[_log(TOPIC_APPROVAL, [OWNER, SPENDER], [5])])
    assert [ev.kind for ev in _alerts(approval)] == ["new_approval"]
    assert approval.events[0].signals["unlimited"] == "unread"
    assert approval.events[0].tx["data"].startswith("0x095ea7b3")
    forall = _run(logs=[_log(TOPIC_FOR_ALL, [OWNER, SPENDER], [1], index=2)])
    assert _alerts(forall)[0].kind == "new_approval_for_all"
    assert _alerts(forall)[0].signals["unlimited"] == "yes"
    permit = _run(logs=[_log(TOPIC_PERMIT2, [OWNER, TOKEN, SPENDER], [5, 0], index=3)])
    assert _alerts(permit)[0].kind == "new_permit2"
    assert _alerts(permit)[0].tx["data"] == lockdown_data(TOKEN, SPENDER)
    used = _run(
        logs=[_log(TOPIC_TRANSFER, [OWNER, OTHER], [20], index=4)],
        senders={"0x" + "ab" * 32: ("ok", SPENDER)},
        stored_approvals={(TOKEN, SPENDER): {"updated": NOW - 10 * 86400, "unlimited": "no"}},
    )
    assert _alerts(used)[0].kind == "approval_used"
    assert _alerts(used)[0].signals["approval_age_days"] == "10"
    assert _alerts(used)[0].signals["unlimited"] == "no"
    unread_sender = _run(logs=[_log(TOPIC_TRANSFER, [OWNER, OTHER], [20], index=5)])
    assert _alerts(unread_sender)[0].kind == "sender_unread"
    dust = _run(logs=[_log(TOPIC_TRANSFER, [OTHER, OWNER], [1], index=6)])
    assert dust.events[0].alert is False
    assert dust.events[0].kind == "dust"
    assert _alerts(dust) == []
    look = _run(
        logs=[_log(TOPIC_TRANSFER, [OWNER, LOOKALIKE], [20_000], index=7)],
        senders={"0x" + "ab" * 32: ("ok", OWNER)},
        counterparties=[KNOWN],
    )
    assert _alerts(look)[0].kind == "lookalike_outflow"
    native = _run(txs=[{
        "hash": "0x" + "cd" * 32, "sender": OWNER, "to": OTHER,
        "value_wei": 10 ** 18, "failed": False,
    }])
    assert _alerts(native)[0].kind == "large_outflow"
    unsized = _run(txs=[{
        "hash": "0x" + "ce" * 32, "sender": OWNER, "to": OTHER,
        "value_wei": None, "failed": False,
    }])
    assert _alerts(unsized)[0].kind == "native_unread"
    changed = _run(delegation_status="delegated", delegation=OTHER, stored_delegate=("none", ""))
    assert _alerts(changed)[0].kind == "delegation_changed"
    assert _alerts(changed)[0].tx["kind"] == "authorization"
    assert _alerts(changed)[0].tx["address"] == ZERO
    assert "r" not in _alerts(changed)[0].tx
    first = _run(cursor=None, delegation_status="delegated", delegation=OTHER)
    assert first.status == "caught_up"
    assert first.advance_to == 20
    assert "Earlier history is not alerted." in first.sentence
    assert _alerts(first)[0].kind == "delegation_changed"
    lag = _run(logs_ok=False, logs=[_log(TOPIC_APPROVAL, [OWNER, SPENDER], [5])])
    assert lag.status == "unreadable"
    assert lag.advance_to is None
    assert "not a clear result" in lag.sentence
    assert [ev.kind for ev in lag.events] == ["index_lag"]
    assert "clean" not in lag.sentence.lower()


def test_an_alert_carries_the_advisory_and_the_whole_unsigned_transaction():
    outcome = _run(logs=[_log(TOPIC_APPROVAL, [OWNER, SPENDER], [5])])
    event = _alerts(outcome)[0]
    messages = render_alert(event, preview=True)
    joined = "\n".join(messages)
    assert ADVISORY in joined
    assert PAGE_DEFAULT in joined
    assert "https://www.humanoid-traders.com/claw-watch" in joined
    assert "ethereum:" not in joined
    assert event.tx["data"] in joined
    assert event.tx["data"] == approve_revoke_data(SPENDER)
    assert "pre-signed" in joined
    assert "module" in joined.lower() or "cancel" in joined.lower()
    lowered = joined.lower()
    for banned in (" scam", " clean", "$"):
        assert banned not in lowered
    assert "not the same as safe" not in lowered
    auth = unsigned_tx(kind="delegation_7702", chain_id=1)
    text = format_tx(auth)
    assert "your wallet's current nonce" in text
    assert "0x" + "ab" * 32 not in text
    rendered = "\n".join(render_alert(_alerts(_run(
        delegation_status="delegated", delegation=OTHER, stored_delegate=("none", ""),
    ))[0]))
    assert "Drain Watch" not in rendered
    assert "Claw Watch" in rendered


def test_the_command_is_admin_gated_and_speaks_claw_watch():
    src = (ROOT / "bot/skills/drain_watch_commands.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seg = segment_reader(src)
    body = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_cmd_clawwatch":
            body = seg(node) or ""
    assert "_is_admin" in body
    assert "Not found." in (ROOT / "bot/drain_watch/copy.py").read_text(encoding="utf-8")
    assert "Claw Watch" in src or "Claw Watch" in body
    catalog = (ROOT / "bot/skills/command_catalog.py").read_text(encoding="utf-8")
    assert "Claw Watch — register an address" in catalog
    assert "Drain Watch" not in catalog
    page = (ROOT / "app/public/claw-watch.html").read_text(encoding="utf-8")
    assert "Drain Watch" not in page
    assert "Claw Watch" in page


def test_poll_stays_off_without_the_flag(monkeypatch):
    monkeypatch.delenv("DRAIN_WATCH_ENABLED", raising=False)
    assert start_poll(object()) is None
