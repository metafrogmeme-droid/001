"""Public signals for one bot name.

The flag ships off. With it off, /botcheck is not registered and the page is
not this module's concern (the website suite covers the 404). With it on, the
reply is the catalog, a submission is a review line, and a Telegram id is not
in that line.
"""

from __future__ import annotations

import json
import pathlib
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from bot import daily_duel_bot as ddb
from bot.botcheck import enqueue, lookup, public_document, reply_for
from bot.botcheck.check import (
    SIGNAL_IDS,
    enabled,
    list_text,
    render_text,
)
from bot.daily_duel_store import CallStore, player_key

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / "bot" / "botcheck" / "catalog.json"
HTML = ROOT / "app" / "content" / "botcheck.html"
CLIENT = ROOT / "app" / "public" / "js" / "botcheck.js"
ROUTE = ROOT / "app" / "routes" / "botcheck.js"
BANNED = re.compile(r"\b(?:safe|scam|verified|trusted)\b", re.I)
MONEY = re.compile(r"\$|RCLAW|%|\bstake\b", re.I)
SALT = "s" * 32
TG = "9988776655"
NOW = "2026-10-23T12:00:00.000Z"


def _clear(monkeypatch) -> None:
    monkeypatch.delenv("BOTCHECK_ENABLED", raising=False)


def test_the_flag_is_on_only_for_four_words(monkeypatch):
    _clear(monkeypatch)
    assert enabled({}) is False
    assert enabled({"BOTCHECK_ENABLED": ""}) is False
    for off in ("0", "false", "no", "off", "enabled", "yes please", " true enough"):
        assert enabled({"BOTCHECK_ENABLED": off}) is False, off
    for on in ("1", "true", "yes", "on", "ON", " Yes ", "\ttrue\n"):
        assert enabled({"BOTCHECK_ENABLED": on}) is True, on


def test_every_bot_has_five_sourced_signals_and_a_labelled_summary():
    doc = public_document()
    assert doc["as_of"] == "2026-10-23"
    assert "Unknown stays unknown" in doc["disclaimer"]
    assert len(doc["bots"]) >= 20
    for bot in doc["bots"]:
        assert [row["id"] for row in bot["signals"]] == list(SIGNAL_IDS)
        for row in bot["signals"]:
            assert row["source_url"].startswith("https://")
            assert " " not in row["source_url"]
            assert row["source_label"]
            assert row["detail"]
            assert row["as_of"] == "2026-10-23"
            if row["answer"] == "unknown":
                assert row["answer_text"] == "unknown"
        notice = bot["summary"]["notice"]
        assert notice.startswith("AI-written") and "Art. 50" in notice
        assert bot["summary"]["label"] == "AI-written"
        assert notice in render_text(lookup(bot["name"]))
        assert bot["summary"]["text"] in render_text(lookup(bot["name"]))


def test_unknown_is_not_rewritten_and_the_first_example_matches_its_module():
    mevx = lookup("mevx")
    assert mevx["found"] is True
    assert {row["id"]: row["answer"] for row in mevx["bot"]["signals"]} == {sid: "unknown" for sid in SIGNAL_IDS}
    duel = lookup("@runeclaw daily duel")
    assert duel["found"] is True
    answers = {row["id"]: row["answer"] for row in duel["bot"]["signals"]}
    assert answers["creates_wallets"] == "no"
    assert answers["asks_seed"] == "no"
    assert answers["code_public"] == "yes"
    assert answers["signs_from"] == "user_wallet"
    assert answers["public_incident"] == "unknown"
    assert duel["bot"]["usernames"] == []
    text = render_text(duel)
    assert "the user's own wallet" in text
    assert "as of 2026-10-23" in text


def test_output_and_catalog_do_not_say_the_forbidden_words():
    chunks = [
        CATALOG.read_text(encoding="utf-8"),
        HTML.read_text(encoding="utf-8"),
        CLIENT.read_text(encoding="utf-8"),
        ROUTE.read_text(encoding="utf-8"),
        json.dumps(public_document()),
        list_text(),
        render_text(lookup("not a curated bot")),
        render_text(lookup("totally safe")),
        reply_for("submit totally safe"),
        reply_for(""),
    ]
    for bot in public_document()["bots"]:
        chunks.append(render_text(lookup(bot["name"])))
        for handle in bot["usernames"]:
            chunks.append(render_text(lookup("@" + handle)))
    blob = "\n".join(chunks)
    assert BANNED.search(blob) is None
    assert MONEY.search(blob) is None


def test_a_submission_is_a_review_line_and_never_an_id(tmp_path):
    queue = tmp_path / "review.jsonl"
    fresh = enqueue("Fresh Bot", note="a public page", via="telegram", queue=queue, now=NOW)
    assert fresh == {"ok": True, "queued": True}
    line = json.loads(queue.read_text(encoding="utf-8"))
    assert list(line) == ["submitted_at", "query", "via", "note"]
    assert line["query"] == "Fresh Bot"
    assert line["via"] == "telegram"
    assert "telegram_id" not in line and "user_id" not in line and TG not in queue.read_text(encoding="utf-8")

    again = enqueue("Fresh Bot", via="web", queue=queue, now=NOW)
    assert again["queued"] is True
    listed = enqueue("Maestro", queue=queue, now=NOW)
    assert listed["queued"] is False
    assert listed["error"] == "That name is already in the curated list. Nothing was added."
    assert queue.read_text(encoding="utf-8").count("\n") == 2

    refused = enqueue("totally safe name", queue=queue, now=NOW)
    assert refused["queued"] is False
    assert "safe" not in refused["error"]
    numbered = enqueue("9988776655", note="please", queue=queue, now=NOW)
    assert numbered["queued"] is False
    assert TG not in numbered["error"]
    noted = enqueue("Another Bot", note="id 9988776655", queue=queue, now=NOW)
    assert noted["queued"] is False
    assert queue.read_text(encoding="utf-8").count("\n") == 2
    assert TG not in queue.read_text(encoding="utf-8")


def test_reply_for_submit_does_not_publish_and_does_not_echo(tmp_path):
    queue = tmp_path / "review.jsonl"
    assert reply_for("submit Brand New", queue=queue, now=NOW) == "Queued for review. It is not published."
    assert json.loads(queue.read_text(encoding="utf-8"))["via"] == "telegram"
    missed = reply_for("no such bot", queue=queue, now=NOW)
    assert "No curated entry" in missed
    assert "unknown" in missed
    assert "no such bot" not in missed
    assert reply_for("submit 9988776655", queue=queue, now=NOW).startswith("Send a bot name")
    assert TG not in queue.read_text(encoding="utf-8")


def _bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_DUEL_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BOTCHECK_QUEUE_PATH", str(tmp_path / "review.jsonl"))
    store = CallStore(tmp_path / "calls.sqlite")
    return ddb.DailyDuelBot(salt=SALT, gate=object(), store=store, now=lambda: NOW)


def _update(text: str):
    msg = NS(reply_text=AsyncMock(), text=text)
    return NS(
        effective_user=NS(id=TG, username="p"),
        effective_chat=NS(id=TG, type="private"),
        effective_message=msg,
        message=msg,
        callback_query=None,
    )


def _run(coro):
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _commands(bot) -> set[str]:
    from telegram.ext import CommandHandler

    app = bot.build_app("123456:" + "A" * 30)
    found: set[str] = set()
    for handlers in app.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                found |= set(handler.commands)
    return found


def test_flag_off_leaves_the_command_unregistered_and_help_unchanged(tmp_path, monkeypatch):
    _clear(monkeypatch)
    bot = _bot(tmp_path, monkeypatch)
    assert "botcheck" not in _commands(bot)
    assert "/botcheck" not in ddb.help_text({"cutoff_at": "2031-05-06T00:00:00.000Z", "horizon_hours": 17})
    update = _update("/botcheck Maestro")
    _run(bot.cmd_botcheck(update, None))
    assert update.effective_message.reply_text.await_args_list == []
    assert not (tmp_path / "review.jsonl").exists()


def test_flag_on_registers_botcheck_and_stores_no_telegram_id(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTCHECK_ENABLED", "on")
    bot = _bot(tmp_path, monkeypatch)
    assert "botcheck" in _commands(bot)
    help_on = ddb.help_text(None, {"BOTCHECK_ENABLED": "yes"})
    assert "/botcheck <name>" in help_on
    assert "Unknown stays unknown" in help_on
    update = _update("/botcheck RUNECLAW Daily Duel")
    _run(bot.cmd_botcheck(update, None))
    text = update.effective_message.reply_text.await_args.args[0]
    assert "RUNECLAW Daily Duel" in text
    assert "AI-written" in text
    assert "as of 2026-10-23" in text
    assert TG not in text
    assert list(bot.commands._hits) == ["bc:" + player_key(TG, SALT)]
    submit = _update("/botcheck submit Plain Name")
    _run(bot.cmd_botcheck(submit, None))
    body = (tmp_path / "review.jsonl").read_text(encoding="utf-8")
    assert "Plain Name" in body
    assert TG not in body
    assert "telegram_id" not in body
    monkeypatch.setenv("BOTCHECK_ENABLED", "enabled")
    assert "botcheck" not in _commands(_bot(tmp_path, monkeypatch))
