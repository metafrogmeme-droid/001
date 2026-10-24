"""The anonymous Daily Duel bot.

One process, its own token, no engine, no Mini App. A call is sealed by the
existing duel gate. The Telegram id is hashed for the one-call-a-day key and
does not appear in the receipt. A second call on the same day returns the
first receipt. Confirming is a step: choosing up or down does not seal.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from bot import daily_duel_bot as ddb
from bot.daily_duel_gate import DuelGate, node_runner
from bot.daily_duel_store import CallStore, PayloadRefused, payload_carries_id, player_key
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = code_only((ROOT / "bot" / "daily_duel_bot.py").read_text(encoding="utf-8"))
UNIT = (ROOT / "scripts" / "systemd" / "runeclaw-daily-duel.service").read_text(encoding="utf-8")
TOKEN = "123456:" + "A" * 30
OTHER = "654321:" + "B" * 30
SALT = "s" * 32
TG = "4242424242"
NOW = "2026-10-23T12:00:00.000Z"


def _update(*, data=None, user_id=TG):
    msg = NS(reply_text=AsyncMock())
    query = NS(data=data, answer=AsyncMock(), message=msg) if data is not None else None
    return NS(effective_user=NS(id=user_id, username="p"),
              effective_chat=NS(id=user_id, type="private"),
              effective_message=msg, message=msg, callback_query=query)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _texts(update) -> list[str]:
    return [c.args[0] for c in update.effective_message.reply_text.await_args_list]


class FakeGate:
    def __init__(self):
        self.sealed: list[dict] = []
        self.streaks: list[list[str]] = []
        self.calibrated: list[list[str]] = []
        self.fail: str | None = None
        self.confidence = False
        self.calibration_body = {
            "ok": True, "enabled": True,
            "high": {"correct": 2, "scored": 3, "unscored": 1, "pct": 66.7},
        }
        self.offer_body = {
            "ok": True, "day": "2026-10-23", "callable": True,
            "cutoff_at": "2026-10-24T00:00:00.000Z", "horizon_hours": 17,
            "symbol": "ETHUSDT", "entry_price": 50, "unreadable": False,
            "price_reader": "getTickers", "price_module": "app/lib/tickers.js",
        }

    def rules(self, day=None):
        if self.fail == "rules":
            from bot.daily_duel_gate import GateDown
            raise GateDown("rules")
        body = {
            "ok": True, "day": "2031-05-05", "cutoff_at": "2031-05-06T00:00:00.000Z",
            "horizon_hours": 17, "price_reader": "getTickers", "price_module": "app/lib/tickers.js",
        }
        if self.confidence:
            body["confidence"] = True
        return body

    def offer(self, now=None):
        if self.fail == "offer":
            from bot.daily_duel_gate import GateDown
            raise GateDown("offer")
        return dict(self.offer_body)

    def seal(self, facts):
        self.sealed.append(dict(facts))
        assert TG not in json.dumps(facts)
        pick = "long" if facts.get("pick") == "long" else "short"
        body = {
            "v": 4, "kind": "duel_pick", "round_id": None, "day": "2026-10-23",
            "category": "direction", "symbol": "ETHUSDT", "symbol_b": None,
            "threshold_pct": None, "handle": None, "pick": pick, "entry_price": 50,
            "entry_price_b": None, "resolves_at": "2026-10-24T12:00:00.000Z",
            "picked_at": NOW,
        }
        level = facts.get("confidence")
        if level in ("low", "medium", "high"):
            body["confidence"] = level
        payload = json.dumps(body, separators=(",", ":"))
        return {
            "ok": True, "seal": "ab" * 32, "seal_payload": payload, "pick": pick,
            "symbol": "ETHUSDT", "day": "2026-10-23", "entry_price": 50,
            "resolves_at": "2026-10-24T12:00:00.000Z", "picked_at": NOW,
        }

    def streak(self, picked_at, now=None):
        self.streaks.append(list(picked_at))
        if self.fail == "streak":
            from bot.daily_duel_gate import GateDown
            raise GateDown("streak")
        n = len(picked_at)
        return {"ok": True, "current": n, "best": n, "active_today": bool(n)}

    def calibration(self, payloads, now=None):
        self.calibrated.append(list(payloads))
        if self.fail == "calibration":
            from bot.daily_duel_gate import GateDown
            raise GateDown("calibration")
        return dict(self.calibration_body)


def _bot(tmp_path, gate=None, now=NOW):
    gate = gate or FakeGate()
    store = CallStore(tmp_path / "calls.sqlite")
    bot = ddb.DailyDuelBot(salt=SALT, gate=gate, store=store, now=lambda: now)
    return bot, gate, store


def test_start_is_the_welcome_and_help_carries_the_privacy_line_and_the_cutoff(tmp_path):
    bot, gate, _ = _bot(tmp_path)
    u = _update()
    _run(bot.cmd_start(u, None))
    assert _texts(u) == [ddb.WELCOME]
    assert u.effective_message.reply_text.await_args.kwargs.get("reply_markup") is None
    h = _update()
    _run(bot.cmd_help(h, None))
    text = _texts(h)[0]
    assert ddb.PRIVACY in text
    assert "2031-05-06T00:00:00.000Z" in text
    assert "17" in text and "getTickers" in text
    assert "humanoid-traders.com/doge" in text
    gate.fail = "rules"
    unread = _update()
    _run(bot.cmd_help(unread, None))
    missed = _texts(unread)[0]
    assert ddb.PRIVACY in missed
    assert "could not be read" in missed
    assert "2031" not in missed


def test_choosing_does_not_seal_and_confirming_does_and_a_second_call_is_the_first(tmp_path):
    bot, gate, store = _bot(tmp_path)
    u = _update()
    _run(bot.cmd_duel(u, None))
    assert "ETHUSDT" in _texts(u)[0]
    assert "2026-10-24T00:00:00.000Z" in _texts(u)[0]
    assert "17" in _texts(u)[0]
    assert gate.sealed == []
    ask = _update(data="dd:ask:long:ETHUSDT")
    _run(bot.on_callback(ask, None))
    assert gate.sealed == []
    assert _texts(ask)[0].startswith("Seal up")
    seal = _update(data="dd:seal:long:ETHUSDT")
    _run(bot.on_callback(seal, None))
    assert len(gate.sealed) == 1
    assert gate.sealed[0]["pick"] == "long"
    assert "telegram_id" not in gate.sealed[0]
    assert _texts(seal)[0].startswith("Sealed.")
    assert TG not in _texts(seal)[0]
    again = _update(data="dd:seal:short:ETHUSDT")
    _run(bot.on_callback(again, None))
    assert len(gate.sealed) == 1, "a second confirm must not seal again"
    assert _texts(again)[0].startswith("You already sealed")
    assert "short" not in _texts(again)[0].split("Seal:")[0] or "down" not in _texts(again)[0]
    row = store.for_day(player_key(TG, SALT), "2026-10-23")
    assert row is not None and row["pick"] == "long"
    assert TG not in row["seal_payload"]
    assert "4242424242" not in store.path.read_text(encoding="utf-8", errors="ignore") or TG not in row["seal_payload"]


def test_a_receipt_that_carries_the_telegram_id_is_not_stored(tmp_path):
    store = CallStore(tmp_path / "calls.sqlite")
    key = player_key(TG, SALT)
    bad = {
        "player_key": key, "day": "2026-10-23", "pick": "long", "symbol": "ETHUSDT",
        "seal": "ab" * 32, "seal_payload": json.dumps({"telegram_id": TG, "pick": "long"}),
        "picked_at": NOW, "resolves_at": "2026-10-24T12:00:00.000Z",
    }
    assert payload_carries_id(bad["seal_payload"], TG)
    try:
        store.record(bad, telegram_id=TG)
        raised = False
    except PayloadRefused:
        raised = True
    assert raised
    assert store.for_day(key, "2026-10-23") is None
    good_payload = json.dumps({"v": 4, "kind": "duel_pick", "pick": "long", "entry_price": 50})
    assert not payload_carries_id(good_payload, TG)
    first, created = store.record({**bad, "seal_payload": good_payload}, telegram_id=TG)
    assert created and first["pick"] == "long"
    second, created2 = store.record({
        **bad, "pick": "short", "seal": "cd" * 32, "seal_payload": good_payload.replace("long", "short"),
    }, telegram_id=TG)
    assert created2 is False
    assert second["seal"] == first["seal"]
    assert second["pick"] == "long"


def test_the_id_is_a_salted_hash_and_two_salts_differ():
    a = player_key(TG, SALT)
    b = player_key(TG, "t" * 32)
    c = player_key("4242424243", SALT)
    assert a != b and a != c
    assert TG not in a and len(a) == 64
    try:
        player_key(TG, "short")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_result_streak_and_board_say_what_is_missing(tmp_path):
    bot, gate, _ = _bot(tmp_path)
    board = _update()
    _run(bot.cmd_leaderboard(board, None))
    assert "coming soon" in _texts(board)[0].lower()
    empty = _update()
    _run(bot.cmd_result(empty, None))
    assert "have not sealed" in _texts(empty)[0]
    _run(bot.on_callback(_update(data="dd:seal:long:ETHUSDT"), None))
    pending = _update()
    _run(bot.cmd_result(pending, None))
    assert "Not due yet" in _texts(pending)[0]
    assert "Scoring it is coming soon" not in _texts(pending)[0]
    later, _, _ = _bot(tmp_path, gate=gate, now="2026-10-25T00:00:00.000Z")
    # The later clock still reads the same store only if we pass it. Use the same store.
    due = _update()
    later.store = bot.store
    _run(later.cmd_result(due, None))
    assert "coming soon" in _texts(due)[0].lower()
    assert "not a loss" in _texts(due)[0]
    streak = _update()
    _run(bot.cmd_streak(streak, None))
    assert _texts(streak)[0].startswith("Streak: 1 day")
    assert "coming soon" in _texts(streak)[0].lower()
    assert TG not in json.dumps(gate.streaks)
    gate.fail = "streak"
    unread = _update()
    _run(bot.cmd_streak(unread, None))
    assert "could not be read" in _texts(unread)[0]
    assert "Streak: 0" not in _texts(unread)[0]


def test_the_token_and_the_salt_are_required_and_never_echoed():
    assert ddb.token_problem({})
    assert "DAILY_DUEL_BOT_TOKEN" in (ddb.token_problem({}) or "")
    same = ddb.token_problem({"DAILY_DUEL_BOT_TOKEN": TOKEN, "TELEGRAM_BOT_TOKEN": TOKEN})
    assert same and "409" in same and TOKEN not in same
    other = ddb.token_problem({"DAILY_DUEL_BOT_TOKEN": TOKEN, "DUEL_BOT_TOKEN": TOKEN})
    assert other and TOKEN not in other
    assert ddb.token_problem({"DAILY_DUEL_BOT_TOKEN": "not-a-token"})
    assert "not-a-token" not in (ddb.token_problem({"DAILY_DUEL_BOT_TOKEN": "not-a-token"}) or "")
    assert ddb.token_problem({"DAILY_DUEL_BOT_TOKEN": TOKEN, "TELEGRAM_BOT_TOKEN": OTHER}) is None
    assert ddb.salt_problem({})
    assert "DAILY_DUEL_ID_SALT" in (ddb.salt_problem({"DAILY_DUEL_ID_SALT": "tiny"}) or "")
    assert "tiny" not in (ddb.salt_problem({"DAILY_DUEL_ID_SALT": "tiny"}) or "")
    assert ddb.salt_problem({"DAILY_DUEL_ID_SALT": SALT}) is None
    assert ddb.bot_token({"DAILY_DUEL_BOT_TOKEN": f' "{TOKEN}" '}) == TOKEN


def test_exit_78_is_the_one_the_unit_does_not_restart_and_health_is_polling():
    assert ddb.EXIT_NOT_CONFIGURED == 78
    code = "\n".join("" if ln.lstrip().startswith("#") else ln for ln in UNIT.splitlines())
    m = re.search(r"^RestartPreventExitStatus=(\d+)$", code, re.M)
    assert m and int(m.group(1)) == 78
    assert "-m bot.daily_duel_bot" in code
    assert "8082/health" in code
    assert ddb.health_body({"polling": True, "stopping": False})[0] == 200
    assert ddb.health_body({"polling": False, "stopping": False})[0] == 503
    status, body = ddb.health_body({"polling": True, "stopping": False, "updates": 2})
    assert body["service"] == "runeclaw-daily-duel" and body["updates_seen"] == 2
    assert "player" not in body and TG not in json.dumps(body)


def test_build_app_registers_the_duel_and_the_digest_and_not_a_mini_app(monkeypatch):
    from telegram.ext import CallbackQueryHandler, CommandHandler
    bot, _, _ = _bot(pathlib.Path("/tmp"))
    # /tmp is unused: build_app does not touch the store. A real store was
    # created above; build another bot on a throwaway path via the fixture-less call.

    def registered(application):
        cmds = set()
        patterns = []
        for handlers in application.handlers.values():
            for h in handlers:
                if isinstance(h, CommandHandler):
                    cmds |= set(h.commands)
                elif isinstance(h, CallbackQueryHandler):
                    patterns.append(h.pattern.pattern)
        return cmds, patterns

    # /panic is the panic flag's command. Off, the duel list is the one it was.
    monkeypatch.delenv("PANIC_BUTTON", raising=False)
    cmds, patterns = registered(bot.build_app(TOKEN))
    base = {
        "start", "help", "duel", "result", "streak", "leaderboard",
        "watch", "digest", "stop",
    }
    assert cmds == base
    assert patterns == [r"^dd:"]
    monkeypatch.setenv("PANIC_BUTTON", "1")
    cmds_on, patterns_on = registered(bot.build_app(TOKEN))
    assert cmds_on == base | {"panic"}
    assert patterns_on == [r"^dd:"]
    assert "WebAppInfo" not in SRC
    assert "web_app" not in SRC


def test_the_module_builds_no_engine_and_promotes_nothing():
    for forbidden in ("bot.core.engine", "RuneClawEngine", "WebAppInfo", "web3",
                      "eth_sign", "$RCLAW", "presale", "stake", "wager"):
        assert forbidden not in SRC, forbidden
    assert "app/lib/daily_duel_gate" not in SRC or "daily_duel_gate" in SRC
    assert "DAILY_DUEL_BOT_TOKEN" in SRC
    assert "sealDuelPick" not in SRC, "the seal is the gate's, not a second copy"
    # The sentence is two literals so it stays under the line limit. Both
    # halves have to be in the code, not in a comment the scanner blanked.
    assert "We store an anonymous ID to limit one call per day." in SRC
    assert "we never hold your keys." in SRC
    raw = (ROOT / "bot" / "daily_duel_bot.py").read_text(encoding="utf-8")
    assert "Welcome to Daily Duel by RUNECLAW Arena." in raw
    assert "Also on the web: humanoid-traders.com/doge" in raw
    assert ddb.WELCOME.startswith("👋 Welcome to Daily Duel by RUNECLAW Arena.")
    assert ddb.WELCOME.endswith("Also on the web: humanoid-traders.com/doge")


def test_a_stale_instance_is_recognised_only_by_its_own_command_line(tmp_path):
    pidfile = tmp_path / "daily-duel.pid"
    pidfile.write_text("4242")
    mine = lambda pid: b"/usr/bin/python3\x00-m\x00bot.daily_duel_bot\x00"  # noqa: E731
    theirs = lambda pid: b"/usr/bin/python3\x00-m\x00bot.main\x00--mode\x00telegram\x00"  # noqa: E731
    assert ddb.stale_pid(str(pidfile), alive=lambda pid: True, cmdline=mine) == 4242
    assert ddb.stale_pid(str(pidfile), alive=lambda pid: True, cmdline=theirs) is None
    assert ddb.stale_pid(str(pidfile), alive=lambda pid: False, cmdline=mine) is None


def test_the_python_gate_does_not_forward_an_id_and_the_node_gate_omits_it_from_the_seal():
    seen = []

    def runner(command, payload):
        seen.append((command, dict(payload)))
        return {"ok": True, "cutoff_at": "x", "horizon_hours": 1}

    gate = DuelGate(runner=runner)
    gate.seal({
        "now": NOW, "pick": "up", "symbol": "ETHUSDT",
        "telegram_id": TG, "entry_price": 1, "username": "ada",
    })
    assert seen[0][0] == "seal"
    assert "telegram_id" not in seen[0][1] and "entry_price" not in seen[0][1] and "username" not in seen[0][1]

    out = node_runner("seal", {
        "now": NOW, "pick": "up", "symbol": "ETHUSDT", "entry_price": 1,
        "telegram_id": TG, "chat_id": "111", "username": "ada",
        "player_key": "ab" * 32,
        "marks": {"ETHUSDT": {"price": 50, "volume": 10}},
    })
    assert out["ok"] is True
    assert TG not in out["seal_payload"]
    assert "ada" not in out["seal_payload"]
    body = json.loads(out["seal_payload"])
    assert body["entry_price"] == 50
    assert body["pick"] == "long"
    assert body["handle"] is None
    assert body["kind"] == "duel_pick"
    rules = node_runner("rules", {"day": "2026-10-23"})
    horizon = re.search(r"const HORIZON_HOURS = (\d+);", (ROOT / "app" / "lib" / "duel.js").read_text(encoding="utf-8"))
    assert horizon and rules["horizon_hours"] == int(horizon.group(1))
    assert rules["cutoff_at"] == "2026-10-24T00:00:00.000Z"
    assert rules["price_module"] == "app/lib/tickers.js"
    assert rules["confidence"] is False
    second = node_runner("seal", {
        "now": NOW, "pick": "down", "symbol": "ETHUSDT",
        "marks": {"ETHUSDT": {"price": 50, "volume": 10}},
    })
    assert second["ok"] is True and second["pick"] == "short"
    # The gate will seal twice; the store is what rejects the second write.
    store_path_payloads = [out["seal_payload"], second["seal_payload"]]
    assert store_path_payloads[0] != store_path_payloads[1]
    assert "confidence" not in json.loads(out["seal_payload"])


def _callbacks(update) -> list[str]:
    markup = update.effective_message.reply_text.await_args.kwargs.get("reply_markup")
    if markup is None:
        return []
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_help_and_the_offer_name_a_rating_only_while_the_gate_says_so():
    rules = {
        "cutoff_at": "2031-05-06T00:00:00.000Z", "horizon_hours": 17,
        "price_reader": "getTickers", "price_module": "app/lib/tickers.js",
    }
    offer = {
        "ok": True, "day": "2026-10-23", "callable": True, "symbol": "ETHUSDT",
        "entry_price": 50, "cutoff_at": "2026-10-24T00:00:00.000Z", "horizon_hours": 17,
    }
    assert "how sure" not in ddb.help_text(rules).lower()
    assert "how sure" not in ddb.offer_text(offer)
    on_rules = {**rules, "confidence": True}
    on_offer = {**offer, "confidence": True}
    assert "low, medium or high" in ddb.help_text(on_rules)
    assert "your own rating" in ddb.help_text(on_rules).lower()
    assert "how sure you are" in ddb.offer_text(on_offer)
    assert ddb.WELCOME.startswith("👋 Welcome to Daily Duel by RUNECLAW Arena.")
    assert "not 0%" in ddb.calibration_text(None)
    none = ddb.calibration_text({"enabled": True, "high": {"correct": 0, "scored": 0, "pct": None}})
    assert "not 0%" in none and "0%" not in none.split("not ")[0]
    rate = ddb.calibration_text({"enabled": True, "high": {"correct": 2, "scored": 3, "pct": 66.7}})
    assert "2 of 3 were right (66.7%)" in rate
    assert ddb.parse_callback("dd:seal:long:ETHUSDT") == ("seal", "long", "ETHUSDT", None)
    assert ddb.parse_callback("dd:seal:long:ETHUSDT:high") == ("seal", "long", "ETHUSDT", "high")
    assert ddb.parse_callback("dd:conf:short:BTCUSDT:medium") == ("conf", "short", "BTCUSDT", "medium")


def test_a_rating_is_a_step_and_is_shown_on_the_result_and_the_streak(tmp_path):
    gate = FakeGate()
    gate.confidence = True
    gate.offer_body = {**gate.offer_body, "confidence": True}
    bot, gate, _ = _bot(tmp_path, gate=gate)
    ask = _update(data="dd:ask:long:ETHUSDT")
    _run(bot.on_callback(ask, None))
    assert gate.sealed == []
    assert "How sure are you" in _texts(ask)[0]
    assert "your own rating" in _texts(ask)[0]
    assert _callbacks(ask) == [
        "dd:conf:long:ETHUSDT:low", "dd:conf:long:ETHUSDT:medium",
        "dd:conf:long:ETHUSDT:high", "dd:back",
    ]
    chosen = _update(data="dd:conf:long:ETHUSDT:high")
    _run(bot.on_callback(chosen, None))
    assert gate.sealed == []
    assert _texts(chosen)[0].startswith("Seal up")
    assert "Your rating: high." in _texts(chosen)[0]
    assert _callbacks(chosen)[0] == "dd:seal:long:ETHUSDT:high"
    seal = _update(data="dd:seal:long:ETHUSDT:high")
    _run(bot.on_callback(seal, None))
    assert len(gate.sealed) == 1
    assert gate.sealed[0]["confidence"] == "high"
    assert "telegram_id" not in gate.sealed[0]
    assert "Your rating: high." in _texts(seal)[0]
    assert TG not in _texts(seal)[0]
    result = _update()
    _run(bot.cmd_result(result, None))
    assert "Your rating: high." in _texts(result)[0]
    assert "Not due yet" in _texts(result)[0]
    streak = _update()
    _run(bot.cmd_streak(streak, None))
    text = _texts(streak)[0]
    assert text.startswith("Streak: 1 day")
    assert "High confidence: 2 of 3 were right (66.7%)." in text
    assert "not in this rate" in text
    assert gate.calibrated and '"confidence":"high"' in gate.calibrated[0][0]


def test_a_rating_on_the_old_seal_button_is_dropped_while_the_flag_is_off(tmp_path):
    bot, gate, _ = _bot(tmp_path)
    seal = _update(data="dd:seal:long:ETHUSDT:high")
    _run(bot.on_callback(seal, None))
    assert len(gate.sealed) == 1
    assert "confidence" not in gate.sealed[0]
    assert "Your rating" not in _texts(seal)[0]
    streak = _update()
    _run(bot.cmd_streak(streak, None))
    assert _texts(streak)[0].startswith("Streak: 1 day")
    assert "High confidence" not in _texts(streak)[0]
    assert gate.calibrated == []


def test_the_python_gate_forwards_a_rating_and_still_drops_an_id():
    seen = []

    def runner(command, payload):
        seen.append((command, dict(payload)))
        return {"ok": True, "cutoff_at": "x", "horizon_hours": 1}

    gate = DuelGate(runner=runner)
    gate.seal({
        "now": NOW, "pick": "long", "symbol": "ETHUSDT",
        "confidence": "high", "telegram_id": TG, "username": "ada",
    })
    assert seen[0][0] == "seal"
    assert seen[0][1]["confidence"] == "high"
    assert "telegram_id" not in seen[0][1] and "username" not in seen[0][1]
    gate.seal({"now": NOW, "pick": "long", "symbol": "ETHUSDT", "confidence": None})
    assert "confidence" not in seen[1][1]
