"""CGC_ENABLED and the Telegram reply for the conservation brake.

Unset is off. A non-admin is answered. The windows are the fields in
contracts/cgc/chains.json, not a second formula.
"""
from __future__ import annotations

import json
from pathlib import Path

from bot.cgc import BANNER, NOT_FOUND, UNREAD, flag_on, telegram_text
from bot.skills.market_commands import MarketCommands

ROOT = Path(__file__).resolve().parents[1]
CHAINS = ROOT / "contracts" / "cgc" / "chains.json"
SRC = (ROOT / "bot" / "cgc.py").read_text(encoding="utf-8")


def test_unset_is_off_and_the_on_words_match_the_website():
    assert flag_on({}) is False
    assert flag_on({"CGC_ENABLED": ""}) is False
    assert flag_on({"CGC_ENABLED": "0"}) is False
    assert flag_on({"CGC_ENABLED": "false"}) is False
    assert flag_on({"CGC_ENABLED": "  YES "}) is True
    for word in ("1", "true", "on", "yes"):
        assert flag_on({"CGC_ENABLED": word}) is True
    assert "def enabled" not in SRC
    assert "def flag_on" in SRC


def test_a_non_admin_is_answered_and_never_silent():
    assert telegram_text(False) == NOT_FOUND
    assert telegram_text(None) == NOT_FOUND
    assert telegram_text(False) is not None
    assert telegram_text(None) != ""


def test_an_admin_sees_the_banner_only_while_the_flag_is_off():
    off = telegram_text(True, {})
    assert off.startswith(BANNER)
    assert BANNER == "Admin preview. Not public."
    on = telegram_text(True, {"CGC_ENABLED": "on"})
    assert BANNER not in on
    assert "This bot does not sign." in on
    assert "gwrsETH is testnet only." in on
    doc = json.loads(CHAINS.read_text(encoding="utf-8"))
    for row in doc["chains"]:
        assert row["name"] in on
        assert f"brake {row['w_brake']}s" in on
        assert f"prove {row['w_prov']}s" in on
        assert f"final {row['w_final']}s" in on
        assert f"refund {row['w_final_max']}s" in on
        assert row["w_final_max"] > row["w_final"]


def test_an_unreadable_file_is_not_an_empty_list(tmp_path: Path):
    missing = telegram_text(True, {"CGC_ENABLED": "1"}, chains_path=tmp_path / "absent.json")
    assert UNREAD in missing
    assert "Ethereum Sepolia" not in missing
    assert "No chains were listed." not in missing
    empty = tmp_path / "empty.json"
    empty.write_text('{"chains": []}', encoding="utf-8")
    listed = telegram_text(True, {"CGC_ENABLED": "1"}, chains_path=empty)
    assert "No chains were listed." in listed
    assert UNREAD not in listed
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert UNREAD in telegram_text(True, {}, chains_path=broken)


def test_a_row_with_no_windows_does_not_become_zero(tmp_path: Path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps({
        "chains": [
            {"name": "Kept", "brake": True, "w_brake": 1, "w_prov": 2,
             "w_final": 3, "w_final_max": 4},
            {"name": "Dropped", "w_brake": None},
        ],
    }), encoding="utf-8")
    text = telegram_text(True, {"CGC_ENABLED": "1"}, chains_path=path)
    assert "Kept: brake 1s, prove 2s, final 3s, refund 4s" in text
    assert "Dropped: its windows could not be read." in text
    assert "0s" not in text


async def test_the_command_answers_not_found_for_a_non_admin():
    sent: list[str] = []

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append(text)

    cmd = MarketCommands()
    cmd._guard = _allow
    cmd._is_admin = lambda update: False
    cmd._send = _send
    await cmd._cmd_cgc(None, None)
    assert sent == [NOT_FOUND]

    sent.clear()
    cmd._is_admin = lambda update: None
    await cmd._cmd_cgc(None, None)
    assert sent == [NOT_FOUND]


async def _allow(update, command="", ctx=None):
    return True


def test_the_module_does_not_sign():
    assert "signTransaction" not in SRC
    assert "private_key" not in SRC
    assert "PRIVATE_KEY" not in SRC
