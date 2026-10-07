"""An unpriced paper position's mark is absent, not 0.

The paper rows sent `current: 0` when the mark could not be read. The photo
card prints a falsy price as "—"; the text card tests `is not None` and
printed it as `$60,000.00 -> $0.000000`. The row now sends None and says
why (`price_unavailable`), the way the live rows already do.

Driven through the real `/positions` command in paper mode, with the
ticker fetch failing so no mark is read.
"""
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from bot.config import CONFIG
from bot.formatters import signal_card as sc
from bot.utils.models import Direction


def _paper_pos(asset="BTC/USDT"):
    return NS(asset=asset, direction=Direction.LONG, entry_price=60_000.0,
              stop_loss=58_000.0, take_profit=64_000.0, quantity=0.01, leverage=2,
              opened_at=datetime.now(timezone.utc) - timedelta(hours=2))


def _drive(monkeypatch, last_prices, sent=None):
    from bot.skills import telegram_handler as th
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)
    cards: list = []
    monkeypatch.setattr(sc, "render_position_card", lambda d: (cards.append(d), b"PNG")[1])
    pos = _paper_pos()
    portfolio = NS(open_positions=[pos], _positions={"P1": pos}, _lock=threading.RLock(),
                   _last_prices=dict(last_prices), mark_to_market=lambda fresh: None)

    async def _no_exchange():
        raise RuntimeError("venue down")

    h = object.__new__(th.TelegramHandler)
    h.engine = NS(user_portfolios={7: portfolio}, scanner=NS(_get_exchange=_no_exchange),
                  pending_ideas=[], position_watch=lambda: None)
    h._get_tg_id = lambda update: 7
    h._lang = lambda update: "en"

    async def _allow(update, command="", ctx=None):
        return True
    h._guard = _allow

    async def _send(update, text, **kw):
        if sent is not None:
            sent.append(text)
    h._send = _send

    async def _send_photo(update, png, caption, reply_markup=None):
        if sent is not None:
            sent.append(caption)
        return True
    h._send_photo = _send_photo
    asyncio.run(h._cmd_open_positions(None, None))
    return cards


def test_an_unread_mark_reaches_the_card_as_absent(monkeypatch):
    cards = _drive(monkeypatch, {})
    assert cards and cards[0]["now"] is None
    assert cards[0]["pnl_pct"] is None


def test_a_read_mark_still_reaches_the_card(monkeypatch):
    cards = _drive(monkeypatch, {"BTC/USDT": 61_000.0})
    assert cards and cards[0]["now"] == 61_000.0
    assert cards[0]["pnl_pct"] is not None


def test_the_book_header_names_the_unread_mark_as_the_cause(monkeypatch):
    # The flag is what the book's return reads to say WHY a row is left out:
    # a paper row has a margin, so without it the header named no cause.
    sent: list = []
    _drive(monkeypatch, {}, sent)
    text = "\n".join(str(x) for x in sent)
    assert "Covers 0 of 1 positions" in text, text
    assert "1 with no readable mark" in text, text
    priced: list = []
    _drive(monkeypatch, {"BTC/USDT": 61_000.0}, priced)
    assert "could not be priced" not in "\n".join(str(x) for x in priced)
