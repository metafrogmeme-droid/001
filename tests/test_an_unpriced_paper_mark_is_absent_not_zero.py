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


# ── A priced paper row carries its dollars, so the card shows its P&L ──────
#
# 2026-10-18, after `/golive OFF`: each paper position card printed "price
# unavailable" beside its own NOW price, while the header summed the book to
# +16.64%. The row carried a percent and no `pnl_usd`, and the card shows a
# P&L only when it has both.

def test_a_read_mark_gives_the_card_its_dollars_rr_and_distances(monkeypatch):
    sent: list = []
    cards = _drive(monkeypatch, {"BTC/USDT": 61_000.0}, sent)
    card = cards[0]
    # margin = qty * entry / lev = 0.01 * 60,000 / 2 = 300; ROE = 1/60 * 2.
    assert card["pnl_usd"] is not None and abs(card["pnl_usd"] - 10.0) < 1e-6
    assert card["rr"] is not None
    text = "\n".join(str(x) for x in sent)
    assert "price unavailable" not in text, text


def test_an_unread_mark_still_has_no_dollars(monkeypatch):
    sent: list = []
    cards = _drive(monkeypatch, {}, sent)
    assert cards[0]["pnl_usd"] is None and cards[0]["rr"] is None
    assert "price unavailable" in "\n".join(str(x) for x in sent)


# ── The /portfolio card's exposure is the risk gate's reading ──────────────

def _book_with_two_positions(mark=True):
    from bot.risk.portfolio import PortfolioTracker
    from bot.utils.models import TradeIdea
    book = PortfolioTracker(initial_balance=10_000.0)
    for asset, entry in (("TRUMP/USDT", 1.7468), ("B/USDT", 0.12479)):
        idea = TradeIdea(asset=asset, direction=Direction.SHORT, entry_price=entry,
                         stop_loss=entry * 1.05, take_profit=entry * 0.93,
                         confidence=0.8, reasoning="t", signals_used=["t"])
        book.open_position(idea, size_usd=400.0, leverage=5)
    if mark:
        book.mark_to_market({"TRUMP/USDT": 1.684, "B/USDT": 0.12126})
    return book


def test_two_open_paper_positions_are_not_zero_exposure():
    from bot.skills.portfolio_commands import paper_exposure_pct
    book = _book_with_two_positions()
    state = book.snapshot()
    pct = paper_exposure_pct(book, state)
    assert pct is not None and pct > 0
    assert abs(pct - book.get_position_value() / state.equity_usd * 100) < 1e-9


def test_an_empty_book_is_a_measured_zero_and_an_unread_one_is_none():
    from bot.risk.portfolio import PortfolioTracker
    from bot.skills.portfolio_commands import paper_exposure_pct
    empty = PortfolioTracker(initial_balance=10_000.0)
    assert paper_exposure_pct(empty, empty.snapshot()) == 0.0
    unpriced = _book_with_two_positions(mark=False)
    st = unpriced.snapshot()
    if st.unpriced_positions:
        assert paper_exposure_pct(unpriced, st) is None
    assert paper_exposure_pct(empty, NS(equity_usd=0.0, unpriced_positions=0)) is None
