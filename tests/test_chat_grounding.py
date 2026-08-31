"""
Chat must never invent a position or a current market price it wasn't
given.

Real incident: a user with ZERO open live positions asked a general
"what's moving markets" question and the chat reply confidently stated
"HYPE (your open short)" -- a position that did not exist -- alongside a
BTC price level that was simply wrong (chat has no live price feed).

Root cause (bot/skills/telegram_handler.py _build_chat_system_prompt):
when in LIVE mode with zero open positions, `if is_live and executor:
if executor.open_positions: ...` left positions_detail as the empty
string -- the sibling `elif user_portfolio.open_positions:` branch is
unreachable once the outer `if is_live and executor:` was taken, so NO
explicit "no open positions" statement ever reached the prompt. Into
that vacuum, the model reached for context_block's "Last discussed
asset: HYPE" (a real feature -- conversation_store.py's
build_context_prompt tracks the last symbol the user asked about) and
conflated "we talked about HYPE" with "you have an open HYPE trade."

The fix makes the ACTIVE POSITIONS section always explicit (never
silently blank) for both live and paper mode, and adds an explicit
GROUNDING instruction to the system prompt: never claim a position
exists unless it's listed, never state a live price you weren't given,
never treat "last discussed asset" as an open position.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.config import CONFIG
from bot.skills.telegram_handler import TelegramHandler as H


def _portfolio_state(open_positions=0, equity=100.0):
    return SimpleNamespace(
        open_positions=open_positions, equity_usd=equity,
        total_pnl=0.0, win_rate=0.0, total_trades=0,
    )


def _stub(*, is_live: bool, live_open_positions=None, paper_open_positions=None):
    user_portfolio = SimpleNamespace(
        snapshot=lambda: _portfolio_state(),
        open_positions=paper_open_positions or [],
        trade_history=[],
    )
    executor = SimpleNamespace(
        open_positions=live_open_positions or [],
        closed_positions=[],
    ) if is_live else None
    engine = SimpleNamespace(
        user_portfolios=SimpleNamespace(get=lambda uid: user_portfolio),
        live_executor=executor,
        risk=SimpleNamespace(circuit_breaker_active=False),
        get_effective_equity=lambda uid: 100.0,
        # Truthful-equity resolver used by _build_chat_system_prompt: returns
        # (equity, source). "live" when live, "paper" otherwise.
        resolve_display_equity_sync=lambda uid: (
            100.0, "live" if is_live else "paper"),
    )
    conversations = SimpleNamespace(build_context_prompt=lambda *a, **kw: "")
    ns = SimpleNamespace(
        engine=engine, conversations=conversations,
        _CHAT_SYSTEM_PROMPT=H._CHAT_SYSTEM_PROMPT,
        CHAT_TICKER_MAX_AGE_SEC=H.CHAT_TICKER_MAX_AGE_SEC,
        CHAT_TICKER_LEAD=H.CHAT_TICKER_LEAD,
        CHAT_TICKER_MAX=H.CHAT_TICKER_MAX,
    )
    # The prompt now injects a live-ticker block, so this stand-in `self` needs
    # it the same way it already needs `engine` and `conversations`. Bound to
    # the REAL method rather than stubbed to "": a stub would let the block
    # vanish here without any test noticing, and a vanishing block is precisely
    # the failure it was built to prevent — the model answering from the prices
    # in its weights. This engine has no ws_feed, so it produces the honest
    # "NONE AVAILABLE" text, which is the correct output for that state.
    ns._live_ticker_block = H._live_ticker_block.__get__(ns)
    return ns


class TestNoOpenPositionsIsExplicit:
    def test_live_mode_zero_positions_states_none_explicitly(self):
        stub = _stub(is_live=True, live_open_positions=[])
        with patch.object(type(CONFIG), "is_live", return_value=True):
            prompt = H._build_chat_system_prompt(stub, "u1")
        assert "ACTIVE POSITIONS" in prompt
        assert "none right now" in prompt
        assert "not an existing trade" in prompt or "no open position" in prompt.lower()

    def test_paper_mode_zero_positions_states_none_explicitly(self):
        # CONFIG.simulation_mode defaults to True (paper) -- no patch needed;
        # frozen dataclass fields can't be reassigned anyway.
        stub = _stub(is_live=False, paper_open_positions=[])
        with patch.object(type(CONFIG), "is_live", return_value=False):
            prompt = H._build_chat_system_prompt(stub, "u1")
        assert "ACTIVE POSITIONS" in prompt
        assert "none right now" in prompt

    def test_live_mode_with_a_real_position_still_lists_it(self):
        pos = SimpleNamespace(
            status="open", direction="SHORT", symbol="HYPE/USDT:USDT",
            entry_price=25.0, quantity=2.0, cost_usd=50.0, leverage=5,
            stop_loss=27.0, take_profit=20.0,
        )
        stub = _stub(is_live=True, live_open_positions=[pos])
        with patch.object(type(CONFIG), "is_live", return_value=True):
            prompt = H._build_chat_system_prompt(stub, "u1")
        assert "HYPE/USDT:USDT" in prompt
        assert "none right now" not in prompt


class TestGroundingInstructionsPresent:
    def test_system_prompt_forbids_inventing_positions(self):
        assert "never" in H._CHAT_SYSTEM_PROMPT.lower()
        assert "ACTIVE POSITIONS" in H._CHAT_SYSTEM_PROMPT

    def test_system_prompt_forbids_stating_a_price_it_was_not_given(self):
        """Pins the RULE, not the words it happened to be spelled with.

        WAS: `"real-time" in p or "live market-data" in p`. Both phrases came
        from one sentence — "You do NOT have a live market-data feed in this
        chat" — which was removed because it CONTRADICTED the LIVE MARKET
        block `_live_ticker_block()` appends to the same prompt. The model was
        being told, in one document, that it has no prices and that it has
        these prices.

        The rule the test is named for survived that rewrite intact, and is
        now stated more precisely than before: the distinction that matters is
        SOURCE, not availability. So the assertion moves to the two clauses
        that carry it — the prohibition, and what to do when the price is not
        in front of you — instead of a phrase from a sentence that no longer
        exists.
        """
        p = H._CHAT_SYSTEM_PROMPT.lower()
        assert "never state a price" in p, "the prohibition is gone"
        assert "you do not know that price" in p, \
            "nothing tells the model what to do when the price is not given"

    def test_system_prompt_warns_against_stale_conversation_history(self):
        assert "conversation" in H._CHAT_SYSTEM_PROMPT.lower()
        assert "positions close" in H._CHAT_SYSTEM_PROMPT.lower() or \
               "still open" in H._CHAT_SYSTEM_PROMPT.lower()
