"""/rclaw reads the one $RCLAW record, and says what it could not read.

The token was minted on Solana mainnet on 2026-09-30. Until then every
surface here said "No token exists", and the one place that described the
standard (`bot/token/tier_gate.py`) said Token-2022, which is what the plan
proposed and not what was minted. The facts now live in one file,
`token/config/rclaw.mainnet.json`, which the website's /token page reads as
a byte-identical copy in `app/content/`; the bot's /rclaw card renders it and
invents nothing
(/token stays the contract detective for any EVM address):

- a null authority is "none (revoked)", the fact a holder wants;
- a null presale field is "not announced yet", never a blank or a 0;
- a record that cannot be read names the exception class and shows NO
  address in its place, because a guessed mint is worse than none.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat, Message, Update, User

from bot.formatters import token_card
from bot.formatters.token_card import render_token_card
from bot.skills.telegram_handler import TelegramHandler
from bot.token import record as token_record
from bot.token.record import TokenRecordInvalid, load_record

ROOT = Path(__file__).resolve().parents[1]
MINT = "rupKpYsgk6em6xx4V9E4oGN9Bvo9FQWQd71qBK2CaNe"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _update(uid=555):
    chat = Chat(id=uid, type="private")
    user = User(id=uid, first_name="T", is_bot=False)
    msg = Message(message_id=1, date=datetime.datetime.now(datetime.timezone.utc),
                  chat=chat, from_user=user)
    return Update(update_id=42, message=msg)


def _handler(*, admin: bool):
    h = TelegramHandler.__new__(TelegramHandler)
    h._limiter = MagicMock()
    h._limiter.allow.return_value = True
    h._send = AsyncMock()
    seen = []

    def _is_admin(update):
        seen.append(update)
        return admin

    h._is_admin = _is_admin
    h._admin_asked = seen
    return h


def _sent(h) -> str:
    assert h._send.await_count == 1
    return h._send.await_args.args[1]


def _record(**over):
    data = load_record()
    data = json.loads(json.dumps(data))
    for k, v in over.items():
        data[k] = v
    return data


# ── the record ─────────────────────────────────────────────────────────────

def test_the_record_is_the_file_both_surfaces_read():
    assert token_record.RECORD_PATH == ROOT / "token" / "config" / "rclaw.mainnet.json"
    rec = load_record()
    assert rec["mint"] == MINT
    assert rec["token_program"] == SPL_TOKEN_PROGRAM
    assert rec["cluster"] == "mainnet-beta"
    assert rec["decimals"] == 9
    assert rec["supply_tokens"] == "1000000000"
    # supply in base units is the token supply scaled by the decimals
    assert int(rec["supply_base_units"]) == int(rec["supply_tokens"]) * 10 ** rec["decimals"]
    assert rec["mint_authority"] is None and rec["freeze_authority"] is None
    assert rec["explorer"] == f"https://solscan.io/token/{MINT}"
    # Nothing about the presale is announced, and the record says so with
    # null rather than with a placeholder somebody could read as a term.
    presale = rec["presale"]
    assert presale["status"] == "coming_soon"
    for field in ("date", "price", "venue", "allocation_tokens"):
        assert presale[field] is None, field


def test_the_devnet_draft_is_not_the_mainnet_record():
    draft = json.loads((ROOT / "token" / "config" / "token.config.json").read_text(encoding="utf-8"))
    assert draft["cluster"] == "devnet"
    assert "mint" not in draft


@pytest.mark.parametrize("payload, exc", [
    ("[]", TokenRecordInvalid),
    ('{"mint": "x"}', TokenRecordInvalid),
    ("{not json", json.JSONDecodeError),
])
def test_a_bad_record_raises(tmp_path, payload, exc):
    p = tmp_path / "r.json"
    p.write_text(payload, encoding="utf-8")
    with pytest.raises(exc):
        load_record(p)


def test_a_record_whose_mint_is_not_an_address_raises(tmp_path):
    good = load_record()
    for bad in ("", "0" * 44, "not-an-address", None, MINT + "x" * 10):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({**good, "mint": bad}), encoding="utf-8")
        with pytest.raises(TokenRecordInvalid):
            load_record(p)
    # and the real address is accepted by the same check
    p.write_text(json.dumps(good), encoding="utf-8")
    assert load_record(p)["mint"] == MINT


def test_a_presale_block_without_a_status_raises(tmp_path):
    good = load_record()
    p = tmp_path / "r.json"
    p.write_text(json.dumps({**good, "presale": {"date": None}}), encoding="utf-8")
    with pytest.raises(TokenRecordInvalid):
        load_record(p)


def test_a_missing_file_raises_as_itself(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_record(tmp_path / "absent.json")


# ── the card ───────────────────────────────────────────────────────────────

def test_the_card_names_the_mint_the_supply_and_the_revoked_authorities():
    card = render_token_card(load_record())
    assert f"<code>{MINT}</code>" in card
    assert f'href="https://solscan.io/token/{MINT}"' in card
    assert "Supply: 1,000,000,000 (9 decimals)" in card
    assert "mint authority none (revoked)" in card
    assert "Freeze authority: none (revoked)" in card
    assert "Solana (mainnet-beta) · SPL Token" in card
    assert "Created: 2026-09-30 (UTC)" in card


def test_an_unannounced_presale_reads_not_announced_for_every_term():
    card = render_token_card(load_record())
    assert "Presale: coming soon" in card
    line = next(ln for ln in card.splitlines() if ln.startswith("Date: "))
    assert line == ("Date: not announced yet · Price: not announced yet · "
                    "Venue: not announced yet")
    # No figure stands in for a term that has not been announced.
    assert not re.search(r"\$\s?\d", card)


def test_an_announced_term_is_printed_as_written():
    rec = _record(presale={"status": "announced", "date": "2026-11-01", "price": None,
                           "venue": "Metaplex Genesis"})
    line = next(ln for ln in render_token_card(rec).splitlines() if ln.startswith("Date: "))
    assert line == "Date: 2026-11-01 · Price: not announced yet · Venue: Metaplex Genesis"
    assert "Presale: announced" in render_token_card(rec)


def test_a_held_authority_is_named_not_called_revoked():
    holder = "EEoMVamYkEvZEDXe7cyMGCLg5BUGNAuvSXC1w2LWELVy"
    card = render_token_card(_record(freeze_authority=holder))
    assert f"Freeze authority: held by {holder}" in card
    assert "mint authority none (revoked)" in card


@pytest.mark.parametrize("value", ["", 0, ["x"]])
def test_an_authority_of_an_unknown_shape_is_unreadable(value):
    assert token_card.authority_text(value) == token_card.UNREADABLE
    assert token_card.authority_text(None) == token_card.AUTHORITY_NONE


def test_a_presale_status_that_is_missing_is_unreadable_not_coming_soon():
    card = render_token_card(_record(presale={"status": None, "date": None,
                                              "price": None, "venue": None}))
    assert "Presale: status unreadable" in card
    assert "coming soon" not in card


@pytest.mark.parametrize("supply", ["1e9", "", "-5", None, "1,000"])
def test_a_supply_that_is_not_a_whole_number_raises(supply):
    with pytest.raises(ValueError):
        render_token_card(_record(supply_tokens=supply))


def test_the_operator_line_is_three_values_and_users_never_see_it():
    rec = load_record()
    assert "RCLAW_MINT" not in render_token_card(rec)
    assert "is unset" in render_token_card(rec, operator=True, env_mint="")
    assert "is unset" in render_token_card(rec, operator=True, env_mint=None)
    assert "matches this mint" in render_token_card(rec, operator=True, env_mint=MINT)
    other = render_token_card(rec, operator=True, env_mint="So11111111111111111111111111111111111111112")
    assert "different address" in other and "matches" not in other


# ── the command ────────────────────────────────────────────────────────────

async def test_rclaw_sends_the_card_to_a_user():
    h = _handler(admin=False)
    await h._cmd_rclaw(_update(), MagicMock())
    text = _sent(h)
    assert f"<code>{MINT}</code>" in text
    assert "Presale: coming soon" in text
    assert "RCLAW_MINT" not in text
    assert len(h._admin_asked) == 1


async def test_rclaw_shows_the_operator_what_the_tier_gate_reads(monkeypatch):
    monkeypatch.setenv("RCLAW_MINT", MINT)
    h = _handler(admin=True)
    await h._cmd_rclaw(_update(), MagicMock())
    assert "RCLAW_MINT matches this mint." in _sent(h)
    monkeypatch.setenv("RCLAW_MINT", "So11111111111111111111111111111111111111112")
    h = _handler(admin=True)
    await h._cmd_rclaw(_update(), MagicMock())
    assert "different address" in _sent(h)


async def test_an_unreadable_record_names_the_class_and_shows_no_address():
    h = _handler(admin=False)
    with patch("bot.token.record.load_record", side_effect=FileNotFoundError("/secret/path")):
        await h._cmd_rclaw(_update(), MagicMock())
    text = _sent(h)
    assert "FileNotFoundError" in text
    assert "/secret/path" not in text
    assert not re.search(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text), "no address stands in"


async def test_a_rate_limited_caller_gets_nothing():
    h = _handler(admin=False)
    h._limiter.allow.return_value = False
    await h._cmd_rclaw(_update(), MagicMock())
    assert h._send.await_count == 0


def test_rclaw_is_in_the_help_catalogue_and_token_stays_the_detective():
    # Registration is held by tests/test_handler_mixins.py (every mixin
    # `_cmd_*` is registered in build_app) and the catalogue's exact match
    # by tests/test_command_catalog.py; this pins the entry by name.
    from bot.skills.command_catalog import all_entries
    entries = all_entries()
    assert "rclaw" in entries
    assert "contract detective" in entries["token"][2]
