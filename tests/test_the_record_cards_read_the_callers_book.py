"""Four record cards described the OPERATOR's account to whoever asked.

CLAUDE.md records this cure five times — `check_risk`, `playbook`,
`GetPortfolioSkill`, `/positions`, the chat prompt, and the `pro_scan` header —
each time as "a fix that lands on the card and not on its sibling". Four
siblings were still uncured, every one of them reachable by a non-admin role:

    portfolio_commands.py  _cmd_performance   @guard("portfolio")
    portfolio_commands.py  _cmd_daily_report  @guard("journal")
    portfolio_commands.py  _cmd_classpf       @guard("portfolio")
    portfolio_commands.py  _cmd_portfolio     the equity line was already the
                                              caller's; the positions were not

`_cmd_performance`'s own docstring says "per-user" and its live branch opened
with `executor = self.engine.live_executor`; `user_id` was read at the top and
used only on the PAPER branch. So in live mode every caller was shown the
operator's win rate, all-time net P&L in dollars, today's and this week's.
`/portfolio` is the sharpest of the four because it mixes: `resolve_display_
equity(user_id)` three lines above `self.engine.live_executor` — one card, two
accounts, which is the shape the status card was cured of.

THE FIXTURE IS ASYMMETRIC OR IT PROVES NOTHING. The operator's executor here
RAISES if anything reads its book, so a card that takes the wrong one fails
loudly rather than agreeing with a symmetric stub.
"""
from __future__ import annotations

import pytest

from bot.skills.chat_runtime import live_account_absence, no_live_account_line
from bot.skills.portfolio_commands import PortfolioCommands


class OperatorBook:
    """The book no caller may be shown. Touching it is the defect."""

    @property
    def closed_positions(self):
        raise AssertionError("the card read the OPERATOR's closed trades")

    @property
    def open_positions(self):
        raise AssertionError("the card read the OPERATOR's open positions")


class Stand:
    """A stand-in `self` carrying the attributes the early branches reach for."""

    def __init__(self, view):
        self.engine = _Engine(view)
        self.sent: list[str] = []

    def _get_tg_id(self, update):
        return "caller-1"

    def _lang(self, update):
        return "en"

    async def _guard(self, *a, **kw):
        # The role gate is not this slice's subject: it is driven by the
        # permission suites, and a refused caller never reaches the read.
        return True

    async def _send(self, update, text, **kw):
        self.sent.append(text)
        return True


class _PaperBook:
    """The paper tracker `/portfolio` builds before it reaches the live
    branch. Empty on purpose: the live branch is the subject, and a paper
    figure standing in for a live one is a defect of its own."""

    open_positions: list = []
    trade_history: list = []

    def snapshot(self):
        return {}

    def mark_to_market(self, prices):  # pragma: no cover - never reached here
        raise AssertionError("no positions to mark")


class _Portfolios:
    def has_user(self, user_id):
        return True

    def get(self, user_id):
        return _PaperBook()


class _Engine:
    def __init__(self, view):
        self._view = view
        self.live_executor = OperatorBook()
        self.user_portfolios = _Portfolios()

    async def resolve_display_equity(self, user_id=""):
        # Already per-caller before this slice, and unreadable here: the card
        # under test is the POSITIONS half, which took the operator's book
        # three lines below a figure that was this caller's.
        assert user_id == "caller-1"
        return None, "unavailable"

    def live_view(self, user_id="", max_age_s=900.0):
        assert user_id == "caller-1", f"the card asked about {user_id!r}"
        return self._view


COMMANDS = [
    ("performance", PortfolioCommands._cmd_performance),
    ("daily_report", PortfolioCommands._cmd_daily_report),
    ("classpf", PortfolioCommands._cmd_classpf),
    ("portfolio", PortfolioCommands._cmd_portfolio),
]


@pytest.mark.parametrize("name,fn", COMMANDS, ids=lambda x: x if isinstance(x, str) else "")
@pytest.mark.parametrize("absence,must_say", [
    ("absent", "No exchange account is linked to you"),
    ("unreadable", "could not be decrypted"),
    ("unresolved", "could not be asked"),
])
@pytest.mark.asyncio
async def test_a_caller_with_no_book_is_told_which_absence(monkeypatch, name, fn, absence, must_say):
    """Not "none", not "$0.00", and never the operator's book."""
    import bot.skills.portfolio_commands as pc

    # CONFIG is a FROZEN dataclass, so the flag cannot be set on it — the trap
    # this repo records about `object.__setattr__` and monkeypatch's
    # bookkeeping. The module ATTRIBUTE is the seam: a stand-in that answers
    # live and delegates everything else to the real config.
    class _Live:
        def is_live(self):
            return True

        def __getattr__(self, name):
            return getattr(pc.CONFIG, name)

    monkeypatch.setattr(pc, "CONFIG", _Live())
    monkeypatch.setattr(pc, "live_account_absence", lambda _uid: absence)
    me = Stand({"scope": "none", "executor": None, "balance": None,
                "total": None, "age_s": None})
    await fn(me, object(), object())
    said = "\n".join(me.sent)
    assert must_say in said, f"{name} said: {said[:200]}"
    assert "Nothing was measured." in said
    for forbidden in ("none right now", "$0.00", "0 trades"):
        assert forbidden not in said, f"{name} manufactured a measurement: {said[:200]}"


def test_the_absence_sentence_claims_least_for_a_word_it_does_not_know():
    assert "could not be asked" in no_live_account_line("something new")


def test_the_absence_reading_is_the_one_the_prompt_uses():
    import bot.skills.telegram_handler as th
    assert th._live_account_absence is live_account_absence


def test_no_record_card_reaches_for_the_operator_executor():
    """The four methods' own source, comments stripped: `live_executor` appears
    in none of their bodies. A scan cannot see reachability — the drives above
    do that — but it CAN see a fifth site added tomorrow."""
    import inspect

    from tests.source_scan import code_only

    for name, fn in COMMANDS:
        body = code_only(inspect.getsource(fn))
        assert "engine.live_executor" not in body, f"{name} reads the operator's book"
        assert "live_view(" in body, f"{name} does not take the viewer reading"


class _Closed:
    """A closed trade, the fields the record cards read."""

    def __init__(self, pnl, trade_id="T-1", symbol="BTC/USDT"):
        self.trade_id = trade_id
        self.symbol = symbol
        self.pnl_usd = pnl
        self.close_reason = "TP HIT"
        self.closed_at = None
        self.entry_price = 100.0
        self.exit_price = 110.0
        self.side = "long"
        self.qty = 1.0
        self.status = "closed"


class CallerBook:
    """This caller's own book — one win nobody else has."""

    def __init__(self):
        self.closed_positions = [_Closed(137.42, "T-CALLER")]
        self.open_positions = []


@pytest.mark.asyncio
async def test_the_performance_card_is_built_from_the_callers_own_closes(monkeypatch):
    """The drive the source pin cannot do: the operator's book RAISES if read,
    so a card that takes the wrong one fails here and not in production."""
    import bot.skills.portfolio_commands as pc

    class _Live:
        def is_live(self):
            return True

        def __getattr__(self, name):
            return getattr(pc.CONFIG, name)

    monkeypatch.setattr(pc, "CONFIG", _Live())
    me = Stand({"scope": "own", "executor": CallerBook(), "balance": None,
                "total": None, "age_s": None})
    await PortfolioCommands._cmd_performance(me, object(), object())
    said = "\n".join(me.sent)
    assert "137.42" in said, f"the caller's own close is not on the card: {said[:300]}"


def test_a_store_that_raises_is_unresolved_and_never_absent(monkeypatch):
    """An exception is not a clean bill: "you never linked" is a claim about
    the person, and the store failing says nothing about them."""
    import bot.core.exchange_credentials as ec

    def _boom():
        raise RuntimeError("store down")

    monkeypatch.setattr(ec, "get_credential_store", _boom)
    assert live_account_absence("caller-1") == "unresolved"


def test_a_store_word_the_reading_does_not_know_is_unresolved(monkeypatch):
    import bot.core.exchange_credentials as ec

    class _Store:
        def credential_state(self, uid):
            return "something-new"

    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())
    assert live_account_absence("caller-1") == "unresolved"
