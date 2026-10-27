"""Plant credentials and Telegram chat ids in every way a log call can carry
them, then read the files the three audit channels wrote and grep.

`bot/utils/logger.py` runs every entry through `_redact_dict` / `_redact_string`
before it is written, and the string half reads `bot/utils/secret_shapes.py`.
Until the chat-id rows, that vocabulary covered credentials and not people: a
failed Telegram send logged `chat_id=123456789`, an unhandled handler error
logged `Chat(first_name=..., id=123456789, ...)`, and a structured
`data={"chat_id": 123456789}` was an int under a key no string row can see. The
anonymous Daily Duel bot keeps a salted hash instead of a Telegram id for exactly
this reason; the main bot's logs held the raw one.

THE TEST DRIVES THE REAL CHANNELS (`trade_log`, `risk_log`, `system_log`) with
their real formatter and reads the real `.jsonl` bytes they produce, so a
redaction that happens in a helper but is skipped by the formatter cannot pass.
Their own file handlers are swapped for ones in a temp directory for the
duration, so a planted value never lands in the repository's `logs/`.

BOTH ARMS. The decoys (a status word, a transaction hash, a symbol, an absent
id) must SURVIVE, or the "scrub" is a deletion. And a CONTROL run with the
chat-id rows removed proves the grep can see an id when one is there: a grep
that cannot fail acquits everything.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

import pytest

from bot.utils import logger as logmod
from bot.utils import secret_shapes
from bot.utils.logger import audit, risk_log, system_log, trade_log
from bot.utils.secret_shapes import REDACTED

CHANNELS = {"trade": trade_log, "risk": risk_log, "system": system_log}

# ── what is planted ──────────────────────────────────────────────────────────

BOT_TOKEN = "7123456789:AAHfSomeRealLookingTokenValue_abcdef12345"
PROVIDER_KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk"
BEARER_KEY = "sk-ant-api03-ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zyxwvuts"
JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
       ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
SIGNER_HEX = "4f3c2b1a" * 8
INLINE_KEY = "supersecretvalue99887766"
DICT_PASSWORD = "hunter2-correct-horse"
DISCORD_TOKEN = "tokentokentokentokentokentokentokentoken"
CREDENTIALS = [BOT_TOKEN, PROVIDER_KEY, BEARER_KEY, JWT, SIGNER_HEX, INLINE_KEY,
               DICT_PASSWORD, DISCORD_TOKEN]

# Distinct digit runs, so a hit names exactly which shape leaked.
CHAT_IDS = {
    "label in a message": "135790001",
    "label in a %-arg": "135790002",
    "int under chat_id": "135790003",
    "str under admin_chat_id": "135790004",
    "nested telegram_id": "135790005",
    "list under chat_ids (a)": "135790006",
    "list under chat_ids (b)": "135790007",
    "Chat() repr": "135790008",
    "chat dict repr": "135790009",
    "supergroup id": "-1001357900010",
    "private t.me link": "1357900011",
    "label in reasoning": "135790012",
    "label in result": "135790013",
    "bare id after the word chat": "135790014",
    "bare negative id after the word group": "-135790015",
}

DECOYS = [
    "chat_id=required",                 # a status word
    "BTC/USDT",                         # a symbol
    "0x9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a9d5f7b3a",   # a tx hash
    "drawdown -100.5%",                 # a negative percentage near the supergroup shape
    "https://t.me/runeclaw",            # a public link
    "chat 42",                          # a short number after the word
]


def plant(channel: logging.Logger) -> None:
    """Every way a call reaches the formatter, one entry each."""
    audit(channel, f"send failed: chat_id={CHAT_IDS['label in a message']} token {BOT_TOKEN}",
          action="send", result="ERROR")
    channel.warning("delivery to chat_id=%s failed with %s", CHAT_IDS["label in a %-arg"], PROVIDER_KEY)
    audit(channel, "structured", action="notify", data={
        "chat_id": int(CHAT_IDS["int under chat_id"]),
        "admin_chat_id": CHAT_IDS["str under admin_chat_id"],
        "user": {"telegram_id": int(CHAT_IDS["nested telegram_id"]), "note": "kept"},
        "chat_ids": [int(CHAT_IDS["list under chat_ids (a)"]), int(CHAT_IDS["list under chat_ids (b)"])],
        "api_key": INLINE_KEY,
        "creds": {"password": DICT_PASSWORD},
        "absent_chat_id": None,
        "symbol": "BTC/USDT",
    })
    audit(channel, "handler error: Chat(first_name='A', id=%s, type=<ChatType.PRIVATE>)" % CHAT_IDS["Chat() repr"],
          action="handler", result="ERROR")
    audit(channel, "update {'message': {'chat': {'id': %s, 'type': 'private'}}}" % CHAT_IDS["chat dict repr"],
          action="handler", result="ERROR")
    audit(channel, "forward to %s failed, see https://t.me/c/%s/55"
          % (CHAT_IDS["supergroup id"], CHAT_IDS["private t.me link"]), action="forward", result="ERROR")
    audit(channel, "headers", action="http", reasoning=f"Authorization: Bearer {BEARER_KEY}; "
          f"chat id: {CHAT_IDS['label in reasoning']}",
          result=f"chat_id={CHAT_IDS['label in result']} jwt {JWT}")
    audit(channel, f"signer WEB3_SIGNER_PRIVATE_KEY={SIGNER_HEX} and "
          f"hook https://discord.com/api/webhooks/123456789012345678/{DISCORD_TOKEN}",
          action="config", result="ERROR")
    audit(channel, "Proactive alerts enabled for chat %s" % CHAT_IDS["bare id after the word chat"],
          action="watch_on")
    audit(channel, "Marketing group %s set to teaser" % CHAT_IDS["bare negative id after the word group"],
          action="channel_teaser", result="OK")
    audit(channel, "decoys: " + " | ".join(DECOYS), action="decoy", result="OK")


@pytest.fixture
def files(tmp_path):
    """Swap each channel's handlers for one file in tmp_path, written by the
    channel's own formatter, and put the originals back afterwards."""
    saved = {name: list(lg.handlers) for name, lg in CHANNELS.items()}
    out: dict[str, Path] = {}
    try:
        for name, lg in CHANNELS.items():
            real = next(h for h in saved[name] if isinstance(h, logging.handlers.RotatingFileHandler))
            assert isinstance(real.formatter, logmod._JSONFormatter)
            assert Path(real.baseFilename).name == f"{name}.jsonl"
            for h in saved[name]:
                lg.removeHandler(h)
            path = tmp_path / f"{name}.jsonl"
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setFormatter(real.formatter)          # the REAL formatter instance
            lg.addHandler(fh)
            out[name] = path
        yield out
    finally:
        for name, lg in CHANNELS.items():
            for h in list(lg.handlers):
                lg.removeHandler(h)
                h.close()
            for h in saved[name]:
                lg.addHandler(h)


def written(files) -> dict[str, str]:
    for lg in CHANNELS.values():
        for h in lg.handlers:
            h.flush()
    return {name: p.read_text(encoding="utf-8") for name, p in files.items()}


def leaks(text: str) -> list[str]:
    found = [f"credential {c[:14]}..." for c in CREDENTIALS if c in text]
    found += [f"chat id ({why})" for why, v in CHAT_IDS.items() if v in text]
    return found


# ── the drive ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("channel", list(CHANNELS))
def test_no_credential_and_no_chat_id_reaches_the_file(files, channel):
    plant(CHANNELS[channel])
    text = written(files)[channel]
    assert text.strip(), f"{channel}.jsonl is empty, so this grep measures nothing"
    assert leaks(text) == []
    assert text.count(REDACTED) >= 12


@pytest.mark.parametrize("channel", list(CHANNELS))
def test_every_line_is_still_json_and_the_decoys_survive(files, channel):
    plant(CHANNELS[channel])
    text = written(files)[channel]
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) >= 8
    joined = "\n".join(text.splitlines())
    for decoy in DECOYS:
        assert decoy in joined, f"the scrub ate a decoy: {decoy!r}"
    structured = next(r for r in rows if r.get("action") == "notify")["data"]
    assert structured["symbol"] == "BTC/USDT"
    assert structured["user"]["note"] == "kept"
    assert structured["absent_chat_id"] is None          # an absent id is not an identifier
    assert structured["chat_id"] == REDACTED and structured["user"]["telegram_id"] == REDACTED
    assert structured["chat_ids"] == REDACTED


def test_all_three_channels_are_driven_by_this_file(files):
    for lg in CHANNELS.values():
        plant(lg)
    texts = written(files)
    assert set(texts) == {"trade", "risk", "system"}
    for name, text in texts.items():
        assert leaks(text) == [], name


def test_the_hash_chain_still_links_the_redacted_lines(files):
    """Redaction happens BEFORE the hash, so the chain is over what was written."""
    import hashlib
    plant(trade_log)
    lines = written(files)["trade"].splitlines()
    for prev, cur in zip(lines, lines[1:]):
        assert json.loads(cur)["prev_hash"] == hashlib.sha256(prev.encode()).hexdigest()


# ── the control: the grep can fail ───────────────────────────────────────────


def test_with_the_chat_id_rows_removed_the_same_grep_finds_the_ids(files, monkeypatch):
    """If this passed with the rows in place and ALSO with them removed, the
    drive above would prove nothing. Remove the string rows and the key rule and
    the planted ids must show up in the file."""
    kept = tuple(s for s in secret_shapes.SHAPES
                 if not s.name.startswith(("telegram_chat", "telegram_supergroup", "telegram_private")))
    assert len(kept) < len(secret_shapes.SHAPES)
    monkeypatch.setattr(secret_shapes, "SHAPES", kept)
    monkeypatch.setattr(logmod, "CHAT_ID_KEY_RE", __import__("re").compile(r"(?!x)x"))
    plant(system_log)
    found = leaks(written(files)["system"])
    chat_leaks = [f for f in found if f.startswith("chat id")]
    # Every shape that only a chat-id row or the key rule could have caught.
    assert len(chat_leaks) >= 10, found
    # And the credentials are still caught: the old rows were not what was removed.
    assert not [f for f in found if f.startswith("credential")]


def test_the_key_rule_alone_catches_the_structured_ids(files, monkeypatch):
    """Split the two mechanisms: with the STRING rows gone, the ints under
    chat-id keys are still redacted, because no string row could ever have seen
    them."""
    kept = tuple(s for s in secret_shapes.SHAPES if not s.name.startswith("telegram_chat"))
    monkeypatch.setattr(secret_shapes, "SHAPES", kept)
    audit(system_log, "structured", action="notify", data={
        "chat_id": 135790003, "nested": {"telegram_id": 135790005}, "chat_ids": [135790006]})
    text = written(files)["system"]
    for v in ("135790003", "135790005", "135790006"):
        assert v not in text


def test_the_string_rows_alone_catch_a_message(files, monkeypatch):
    """And the other half: with the KEY rule gone, a message line is still
    scrubbed by the string rows."""
    monkeypatch.setattr(logmod, "CHAT_ID_KEY_RE", __import__("re").compile(r"(?!x)x"))
    audit(system_log, "send failed chat_id=135790001", action="send")
    assert "135790001" not in written(files)["system"]


# ── the real call sites that used to print a bare id ─────────────────────────


def test_the_proactive_toggle_and_the_channel_forwarder_log_no_bare_id(files, tmp_path, monkeypatch):
    """`enable_chat` / `disable_chat` and the forwarder's `add_group`, `set_full`
    and `detect_group` put the id in the MESSAGE text, with no label and no
    `-100` prefix. They are driven for real and the bytes are grepped."""
    from bot.core.proactive_monitor import ProactiveMonitor
    from bot.marketing import channel_forwarder as cf

    monitor = ProactiveMonitor.__new__(ProactiveMonitor)
    monitor._enabled_chats = set()
    monitor._save_enabled_chats = lambda: True
    monitor.enable_chat("135790021")
    monitor.disable_chat("135790021")

    monkeypatch.setattr(cf, "_CONFIG_PATH", tmp_path / "channel_config.json")
    fwd = cf.ChannelForwarder()
    assert fwd.detect_group(-135790022, "group", "Ops room") is True
    fwd.set_full(-135790022, True)

    text = written(files)["system"]
    assert "Proactive alerts enabled" in text and "Marketing group" in text   # the lines were written
    assert "135790021" not in text and "135790022" not in text
    assert "Ops room" in text                                  # the title is not an address
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    added = next(r for r in rows if r.get("action") == "channel_add")
    assert added["data"]["chat_id"] == REDACTED               # said, redacted, not dropped


def test_the_bare_id_row_leaves_a_count_of_chats_alone():
    from bot.utils.secret_shapes import scrub_secrets
    assert scrub_secrets("3 chats 12345678 and chat 42") == "3 chats 12345678 and chat 42"
    assert "123456789" not in scrub_secrets("enabled for chat 123456789")
    assert "987654321" not in scrub_secrets("group -987654321")
