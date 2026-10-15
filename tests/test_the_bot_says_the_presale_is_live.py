"""While the $RCLAW presale is live, /start and the welcome carry one line: the
official sale link, the closing time and /rclaw.

The line is a reading of the record's sale window against the clock
(`token_card.presale_phase`, the reading the /rclaw card and the website take),
so it is driven at fixed instants: a test that asked the real clock would say
something else on each day of the sale. It is absent before the sale, after
it, with a window or a sale link the card would not print, and when the record
cannot be read; in every one of those the message it rides on is unchanged.
"""
from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.formatters import token_card
from bot.risk.portfolio import PortfolioTracker
from bot.token.record import load_record

URL = "https://tools.smithii.io/launch/solana/RUNECLAW"
# The real note, captured once: a stand-in built from whatever the module holds
# would wrap the previous drive's stand-in and keep its clock.
REAL_NOTE = token_card.presale_live_note


def _at(iso: str) -> datetime.datetime:
    return datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


LIVE = _at("2026-10-20T00:00:00Z")


def _record(**presale):
    rec = load_record()
    rec = {**rec, "presale": {**rec["presale"], **presale}}
    return rec


def test_the_line_names_the_official_link_the_close_and_the_card():
    line = token_card.presale_live_line(load_record(), LIVE)
    assert line == ("🟢 <b>The $RCLAW presale is live</b> until 2026-10-29 14:00 UTC, or until the "
                    f'hard cap. Official sale page: <a href="{URL}">{URL}</a> · terms and the mint: /rclaw')


@pytest.mark.parametrize("now", ["2026-10-15T13:59:00Z", "2026-10-29T14:00:00Z", "2027-01-01T00:00:00Z"])
def test_there_is_no_line_outside_the_sale(now):
    assert token_card.presale_live_line(load_record(), _at(now)) is None
    # Both arms: one minute either side of the open.
    assert token_card.presale_live_line(load_record(), _at("2026-10-15T14:00:00Z")) is not None


@pytest.mark.parametrize("presale", [
    {"closes_at": None},                                  # no closing time to state
    {"opens_at": "2026-10-15"},                           # a window that does not read
    {"sale_url": None},                                   # no official link to give
    {"sale_url": "https://tools.smithii.io.evil.example/launch"},  # a link the card refuses
    {"status": "coming_soon"},
])
def test_there_is_no_line_without_a_window_and_a_link_the_card_would_print(presale):
    assert token_card.presale_live_line(_record(**presale), LIVE) is None


def test_the_note_is_a_paragraph_or_nothing(monkeypatch):
    assert token_card.presale_live_note(LIVE) == "\n\n" + token_card.presale_live_line(load_record(), LIVE)
    assert token_card.presale_live_note(_at("2026-11-01T00:00:00Z")) == ""

    def _unreadable(*a, **k):
        raise OSError("record gone")
    monkeypatch.setattr("bot.token.record.load_record", _unreadable)
    assert token_card.presale_live_note(LIVE) == ""


# ── /start, driven ─────────────────────────────────────────────────────────

class _OperatorBook:
    @property
    def open_positions(self):
        raise AssertionError("read the operator's book")


def _start(monkeypatch, *, now, authorized=True):
    import bot.core.live_readiness as lr
    from bot.skills import chat_runtime
    from bot.skills import telegram_handler as th
    monkeypatch.setattr(token_card, "presale_live_note", lambda now_=None: REAL_NOTE(now))
    monkeypatch.setattr(lr, "mode_label", lambda: "PAPER")
    monkeypatch.setattr(chat_runtime, "live_account_absence", lambda _uid: "absent")
    book = PortfolioTracker(initial_balance=4321.0)
    rec = {"role": "paper", "authorized": authorized}
    h = object.__new__(th.TelegramHandler)
    h.engine = NS(user_portfolios=NS(get=lambda uid: book), live_executor=_OperatorBook(),
                  risk=NS(circuit_breaker_active=False), risk_for=lambda uid: NS(),
                  resolve_display_equity=AsyncMock(return_value=(None, "unavailable")))
    h.users = NS(get=lambda uid: rec, register=lambda uid, name="": rec,
                 tier_label=lambda uid: "Free", record_referrer=lambda *a: None)
    h._limiter = NS(allow=lambda uid: True)
    h._get_tg_id = lambda update: "555"
    h._access_state = lambda tg_id: "ok" if authorized else "needs_approval"
    h._request_operator_admission = AsyncMock(return_value=True)
    h._caller_executor = lambda update: None
    h._can_trade_live = lambda tg_id: False
    sent: list = []

    async def _send(update, text, **kw):
        sent.append(text)
    h._send = _send
    asyncio.run(h._cmd_start(NS(effective_user=NS(id=555, first_name="Sam")), NS(args=[])))
    return "\n".join(sent)


def test_start_carries_the_line_while_the_sale_is_live_and_not_after(monkeypatch):
    line = token_card.presale_live_line(load_record(), LIVE)
    said = _start(monkeypatch, now=LIVE)
    assert said.endswith(line), said[-300:]
    after = _start(monkeypatch, now=_at("2026-11-01T00:00:00Z"))
    assert "presale is live" not in after and URL not in after


def test_the_welcome_of_someone_awaiting_approval_carries_it_too(monkeypatch):
    # The sale is public: a person the operator has not admitted yet is told
    # where the official sale page is, and nothing of anybody's book.
    said = _start(monkeypatch, now=LIVE, authorized=False)
    assert said.endswith(token_card.presale_live_line(load_record(), LIVE)), said[-300:]
