"""Scam or Safe? Daily, on the anonymous Daily Duel bot.

The flag off leaves /duel and its callbacks as they are. On, /scam is a
separate command: choosing safe or risky does not seal, confirming does, and
a receipt that still carries the Telegram id is not shown as sealed. The
percent is printed only when it was measured.
"""
from __future__ import annotations

import json
import pathlib

from bot import daily_duel_bot as ddb
from bot import scam_or_safe as sos
from bot.daily_duel_gate import DuelGate, node_runner
from bot.daily_duel_store import CallStore, player_key
from tests.source_scan import code_only
from tests.test_the_daily_duel_bot import NOW, SALT, TG, TOKEN, FakeGate, _run, _texts, _update

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOT_SRC = code_only((ROOT / "bot" / "daily_duel_bot.py").read_text(encoding="utf-8"))
SOS_SRC = code_only((ROOT / "bot" / "scam_or_safe.py").read_text(encoding="utf-8"))
PUZZLE = {
    "id": "sos-aa",
    "chain": "ethereum",
    "chain_label": "Ethereum",
    "method": "approve",
    "fields": [
        {"k": "Spender", "v": "0x12ab\u20269f"},
        {"k": "Amount", "v": "\u2592\u2592\u2592"},
    ],
}


def _commands(app):
    from telegram.ext import CallbackQueryHandler, CommandHandler

    cmds: set[str] = set()
    patterns: list[str] = []
    for handlers in app.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                cmds |= set(handler.commands)
            elif isinstance(handler, CallbackQueryHandler):
                patterns.append(handler.pattern.pattern)
    return cmds, patterns


class ScamGate(FakeGate):
    def __init__(self):
        super().__init__()
        self.sos_sealed: list[dict] = []
        self.puzzle = dict(PUZZLE)
        self.puzzle["fields"] = list(PUZZLE["fields"])

    def sos_offer(self, now=None, player_key=None):
        self.offered = (now, player_key)
        return {
            "ok": True,
            "sealed": False,
            "day": "2026-10-23",
            "cutoff_at": "2026-10-24T00:00:00.000Z",
            "puzzle": self.puzzle,
        }

    def sos_seal(self, facts):
        self.sos_sealed.append(dict(facts))
        assert "telegram_id" not in facts
        assert TG not in json.dumps(facts)
        return {
            "ok": True,
            "sealed": True,
            "verified": True,
            "pick": facts.get("pick"),
            "verdict": "risky",
            "correct": False,
            "explanation": "First sentence here. Second sentence here.",
            "ai_label": sos.AI_LABEL,
            "ai_note": sos.AI_NOTE,
            "right": {"correct": 0, "total": 1, "pct": 0, "unreadable": 0},
            "streak": {"current": 0, "best": 0, "active_today": True, "unreadable": False},
            "rank": {"place": 1, "players": 1},
            "seal": "cd" * 32,
            "seal_payload": json.dumps({"kind": "duel_pick", "category": "scam_or_safe", "pick": facts.get("pick")}),
            "day": "2026-10-23",
            "puzzle": self.puzzle,
        }


def _bot(tmp_path, gate=None):
    gate = gate or ScamGate()
    store = CallStore(tmp_path / "calls.sqlite")
    bot = ddb.DailyDuelBot(salt=SALT, gate=gate, store=store, now=lambda: NOW)
    return bot, gate, store


def test_flag_off_leaves_the_duel_commands_and_the_help_text_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAM_OR_SAFE_V0", raising=False)
    assert sos.enabled() is False
    bot, gate, _ = _bot(tmp_path)
    cmds, patterns = _commands(bot.build_app(TOKEN))
    assert "scam" not in cmds
    assert cmds == {
        "start", "help", "duel", "result", "streak", "leaderboard",
        "watch", "digest", "stop",
    }
    assert patterns == [r"^dd:"]
    help_view = _update()
    _run(bot.cmd_help(help_view, None))
    assert sos.SCAM_HELP not in _texts(help_view)[0]
    assert "/duel — today's call" in _texts(help_view)[0]
    start = _update()
    _run(bot.cmd_start(start, None))
    assert _texts(start) == [ddb.WELCOME]
    closed = _update()
    _run(bot.cmd_scam(closed, None))
    assert _texts(closed) == [sos.NOT_OPEN]
    assert gate.sos_sealed == []
    assert not hasattr(gate, "offered")


def test_flag_on_registers_scam_and_still_serves_the_duel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")
    bot, gate, _ = _bot(tmp_path)
    cmds, patterns = _commands(bot.build_app(TOKEN))
    assert "scam" in cmds
    assert "duel" in cmds
    assert r"^dd:" in patterns and r"^sos:" in patterns
    start = _update()
    _run(bot.cmd_start(start, None))
    assert _texts(start) == [ddb.WELCOME, sos.SCAM_WELCOME]
    help_view = _update()
    _run(bot.cmd_help(help_view, None))
    assert sos.SCAM_HELP in _texts(help_view)[0]
    duel = _update()
    _run(bot.cmd_duel(duel, None))
    assert "ETHUSDT" in _texts(duel)[0]
    assert gate.sos_sealed == []
    for word in ("$RCLAW", "presale", "stake", "wager", "eth_sign", "sealDuelPick", "WebAppInfo"):
        assert word not in BOT_SRC, word
    for word in ("$RCLAW", "presale", "stake", "wager", "eth_sign", "private_key"):
        assert word not in SOS_SRC, word


def test_choosing_does_not_seal_and_confirming_does(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")
    bot, gate, store = _bot(tmp_path)
    shown = _update()
    _run(bot.cmd_scam(shown, None))
    text = _texts(shown)[0]
    assert "0x12ab\u20269f" in text
    assert "\u2592\u2592\u2592" in text
    assert "0x" + "ab" * 20 not in text
    assert gate.sos_sealed == []
    markup = shown.effective_message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "sos:ask:safe:sos-aa"
    assert markup.inline_keyboard[0][1].callback_data == "sos:ask:risky:sos-aa"
    ask = _update(data="sos:ask:safe:sos-aa")
    _run(bot.on_sos(ask, None))
    assert gate.sos_sealed == []
    assert _texts(ask)[0].startswith("Seal safe on sos-aa")
    assert "Verdict" not in _texts(ask)[0]
    seal = _update(data="sos:seal:safe:sos-aa")
    _run(bot.on_sos(seal, None))
    assert len(gate.sos_sealed) == 1
    sent = gate.sos_sealed[0]
    assert sent["pick"] == "safe"
    assert sent["puzzle_id"] == "sos-aa"
    assert sent["player_key"] == player_key(TG, SALT)
    assert "telegram_id" not in sent
    assert TG not in sent["player_key"]
    revealed = _texts(seal)[0]
    assert "AI-written explanation" in revealed
    assert "0% of 1 players called it right." in revealed
    assert "Verdict: risky." in revealed
    assert store.for_day(player_key(TG, SALT), "2026-10-23") is None


def test_a_receipt_that_carries_the_telegram_id_is_not_shown_as_sealed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")

    class Leaky(ScamGate):
        def sos_seal(self, facts):
            out = super().sos_seal(facts)
            out["seal_payload"] = json.dumps({"telegram_id": TG, "pick": "safe"})
            return out

    bot, _, store = _bot(tmp_path, gate=Leaky())
    leaked = _update(data="sos:seal:risky:sos-aa")
    _run(bot.on_sos(leaked, None))
    assert "would have carried your id" in _texts(leaked)[0]
    assert "AI-written explanation" not in _texts(leaked)[0]
    assert store.for_day(player_key(TG, SALT), "2026-10-23") is None


def test_a_moved_puzzle_is_not_sealed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")
    bot, gate, _ = _bot(tmp_path)
    gate.puzzle = dict(PUZZLE)
    gate.puzzle["id"] = "sos-bb"
    ask = _update(data="sos:ask:safe:sos-aa")
    _run(bot.on_sos(ask, None))
    assert gate.sos_sealed == []
    assert not _texts(ask)[0].startswith("Seal")
    markup = ask.effective_message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "sos:ask:safe:sos-bb"
    assert markup.inline_keyboard[0][1].callback_data == "sos:ask:risky:sos-bb"


def test_the_gate_forwards_the_salted_key_and_not_the_telegram_id():
    seen = []

    def runner(command, payload):
        seen.append((command, dict(payload)))
        return {"ok": True}

    gate = DuelGate(runner=runner)
    gate.sos_seal({
        "now": NOW, "pick": "safe", "player_key": "ab" * 32, "puzzle_id": "sos-aa",
        "telegram_id": TG, "username": "ada", "entry_price": 1,
    })
    assert seen[0][0] == "sos-seal"
    assert seen[0][1].keys() <= {"now", "pick", "player_key", "puzzle_id"}
    assert "telegram_id" not in seen[0][1]
    gate.sos_offer(NOW, "ab" * 32)
    assert seen[1] == ("sos-offer", {"now": NOW, "player_key": "ab" * 32})


def test_an_unreadable_percent_is_not_printed_as_zero():
    assert sos.parse_callback("sos:seal:safe:sos-aa") == ("seal", "safe", "sos-aa")
    assert sos.parse_callback("sos:back") == ("back", None, None)
    assert sos.parse_callback("dd:seal:long:ETHUSDT") is None
    withheld = sos.show_field("0x" + "ab" * 20)
    assert withheld == "[withheld]"
    assert sos.show_field("see https://sign.example") == "[withheld]"
    assert sos.show_field("0x12ab\u20269f") == "0x12ab\u20269f"
    body = {
        "pick": "safe", "verdict": "risky", "correct": False,
        "ai_label": sos.AI_LABEL, "explanation": "Hidden words.",
        "ai_note": sos.AI_NOTE,
        "right": {"pct": None, "total": 0},
        "streak": {"current": None},
        "rank": {"place": None, "players": None},
    }
    text = sos.reveal_text(body)
    assert "could not be read" in text
    assert "0%" not in text
    assert "Streak: 0" not in text
    assert "Hidden words." in text
    assert sos.AI_LABEL in text
    measured = dict(body)
    measured["right"] = {"pct": 0, "total": 4, "unreadable": 2}
    measured["streak"] = {"current": 0, "best": 3}
    measured["rank"] = {"place": 2, "players": 5}
    zero = sos.reveal_text(measured)
    assert "0% of 4 players called it right." in zero
    assert "2 sealed calls could not be read" in zero
    assert "Streak: 0 days" in zero
    bogus = dict(body)
    bogus["right"] = {"pct": True, "total": 1}
    bogus["streak"] = {"current": True}
    bogus["ai_label"] = ""
    bogus["explanation"] = "Should stay hidden."
    unread = sos.reveal_text(bogus)
    assert "True%" not in unread
    assert "Should stay hidden." not in unread
    assert "withheld until it carries its AI label" in unread
    for value, want in (("1", True), ("yes", True), ("off", False), ("", False)):
        assert sos.enabled({"SCAM_OR_SAFE_V0": value}) is want


def test_a_real_sos_seal_round_trip_stores_one_call_and_a_second_pick_is_the_first(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAM_OR_SAFE_V0", "1")
    monkeypatch.setenv("SCAM_OR_SAFE_STATE_DIR", str(tmp_path))
    key = "ab" * 32
    offer = node_runner("sos-offer", {"now": NOW, "player_key": key, "telegram_id": TG})
    assert offer["ok"] is True
    assert offer["sealed"] is False
    assert "verdict" not in offer and "explanation" not in offer
    assert TG not in json.dumps(offer)
    pid = offer["puzzle"]["id"]
    sealed = node_runner("sos-seal", {
        "now": NOW, "pick": "safe", "player_key": key, "puzzle_id": pid, "telegram_id": TG,
    })
    assert sealed["ok"] is True
    assert sealed["verified"] is True
    assert sealed["ai_label"] == "AI-written explanation"
    assert TG not in sealed["seal_payload"]
    body = json.loads(sealed["seal_payload"])
    assert body["kind"] == "duel_pick"
    assert body["category"] == "scam_or_safe"
    assert body["pick"] == "safe"
    assert body["entry_price"] is None
    assert body["entry_price_b"] is None
    assert body["round_id"] is None
    assert "$" not in sealed["seal_payload"]
    second = node_runner("sos-seal", {
        "now": NOW, "pick": "risky", "player_key": key, "puzzle_id": pid,
    })
    assert second["pick"] == "safe"
    assert second["seal"] == sealed["seal"]
    monkeypatch.delenv("SCAM_OR_SAFE_V0")
    quiet = tmp_path / "off"
    quiet.mkdir()
    monkeypatch.setenv("SCAM_OR_SAFE_STATE_DIR", str(quiet))
    closed = node_runner("sos-offer", {"now": NOW, "player_key": key})
    assert closed["error"] == "off"
    assert not (quiet / "picks.sqlite").exists()


def test_offer_text_withholds_a_full_address_and_a_url():
    poisoned = {
        "ok": True, "day": "2026-10-23", "cutoff_at": "2026-10-24T00:00:00.000Z",
        "puzzle": {
            "id": "sos-aa", "chain_label": "Ethereum", "method": "approve",
            "fields": [
                {"k": "Spender", "v": "0x" + "ab" * 20},
                {"k": "Note", "v": "https://sign.example/go"},
                {"k": "Amount", "v": "\u2592\u2592\u2592"},
            ],
        },
    }
    text = sos.offer_text(poisoned)
    assert "[withheld]" in text
    assert "0x" + "ab" * 20 not in text
    assert "https://" not in text
    assert "Verdict" not in text
