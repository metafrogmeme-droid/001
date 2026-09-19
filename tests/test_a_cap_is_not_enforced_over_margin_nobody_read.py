"""A hard cap may not be enforced over a figure nobody measured.

`cost_usd` is the position's MARGIN, and `position_size_basis` already
documents what 0.0 there means: "the venue never told us", the ORPHAN case.
`adopt_exchange_positions` writes exactly that — `margin = _margin if
_margin is not None else 0.0` — and records "margin" in `adoption_unread`;
and adoption skips a position it already tracks (`if (sym, side) in tracked:
continue`), so the figure never arrives on its own.

Five places summed that field RAW: the exposure CAP in `_preflight_check`,
the `total_exposure_usd` property, and the /portfolio, risk and
live-portfolio cards. A sixth — `engine.account_risk_overview` — had been
given the honest reading BY HAND, which is what a second copy looks like
from outside: it agrees with every fixture and diverges on the first edit to
either.

Two of the five also disagreed about WHICH BOOK. The cap summed
`status == "open"`; every card summed `open_positions`, which is open AND
`pending_fill`. So a resting limit order held cap room on every surface an
operator reads and none in the limit that enforces it — one noun, two
answers, and the looser one was the gate.
"""

import ast
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.core.live_executor import (
    MICRO_MAX_TOTAL_EXPOSURE,
    CommittedMargin,
    LiveExecutor,
    committed_margin,
    committed_margin_note,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pos(symbol="ADA/USDT:USDT", cost=25.0, status="open"):
    """A position row shaped like the ones the CARDS below render.

    The reading needs only `symbol` and `cost_usd`; the rest is what
    `/portfolio`, `/livebalance` and the playbook read off a row, and a
    fixture that cannot reach the line under test measures nothing.
    """
    return SimpleNamespace(
        symbol=symbol, cost_usd=cost, status=status, direction="LONG",
        entry_price=1.0, quantity=10.0, leverage=5, stop_loss=0.9,
        take_profit=1.2, pnl_usd=None, trade_id=f"T-{symbol}",
        opened_at=datetime.now(timezone.utc), sl_order_id="sl-1",
        tp_order_id="tp-1", close_reason="", commission=0.0,
        order_type="market", origin="bot")


def _ex(tmp_path, positions=None, user_id=None):
    ex = LiveExecutor(user_id=user_id, state_dir=str(tmp_path))
    ex._hedge_mode = False
    ex._positions = {f"t{i}": p for i, p in enumerate(positions or [])}
    return ex


class TestTheReading:
    """`committed_margin` answers three facts, and two of them are not zero."""

    def test_a_flat_book_is_a_measurement(self):
        r = committed_margin([])
        assert r.total == 0.0 and r.complete and r.counted == 0, (
            "Nothing is committed because nothing is open — a reading, not an "
            "absence.")

    def test_a_read_book_sums_what_the_venue_stated(self):
        r = committed_margin([_pos(cost=25.0), _pos(cost=17.5)])
        assert r.total == 42.5 and r.scored == 2 and r.counted == 2
        assert r.complete and r.unread == ()

    def test_an_unread_margin_is_named_and_not_summed_as_zero(self):
        r = committed_margin([_pos("ADA/USDT:USDT", 25.0),
                              _pos("PENDLE/USDT:USDT", 0.0)])
        assert r.total == 25.0, "the FLOOR — only what was stated"
        assert r.scored == 1 and r.counted == 2 and not r.complete
        assert r.unread == ("PENDLEUSDT",), (
            "The refusal has to name the position, so the reading carries the "
            "symbol rather than a count alone.")

    def test_nothing_read_is_none_and_never_zero(self):
        r = committed_margin([_pos("ADA/USDT:USDT", 0.0)])
        assert r.total is None, (
            "$0.00 here is a measured flat book. A book of one position whose "
            "margin nobody read is not that.")
        assert not r.complete and r.unread == ("ADAUSDT",)

    def test_a_flat_book_and_an_unreadable_one_are_different(self):
        assert committed_margin([]).total == 0.0
        assert committed_margin([_pos(cost=0.0)]).total is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0, None,
                                     "25.0abc"])
    def test_an_absence_wearing_digits_is_an_absence(self, bad):
        # Not re-decided here: `position_size_basis` owns what one position's
        # margin is, and `_to_float` owns which floats are readings.
        r = committed_margin([_pos(cost=bad)])
        assert r.total is None and not r.complete

    def test_a_numeric_string_the_venue_sent_is_still_a_reading(self):
        assert committed_margin([_pos(cost="25.0")]).total == 25.0


class TestTheCapRefusesWhatItCannotMeasure:
    """The one reader where an unread margin is a HARD CAP, not a card."""

    def test_the_old_sum_allowed_the_order_and_the_reading_refuses(self, tmp_path):
        # THE MEASUREMENT. Ten adopted positions the venue stated no margin
        # for sum to $0.00 raw, so the cap had nothing to enforce.
        book = [_pos(f"A{i}/USDT:USDT", 0.0) for i in range(10)]
        assert sum(p.cost_usd for p in book) == 0.0, (
            "the shape being removed: a partial total read as free capital")
        err = _ex(tmp_path, book)._preflight_check(50.0)
        assert err and "cannot be measured" in err

    def test_the_refusal_names_the_position(self, tmp_path):
        ex = _ex(tmp_path, [_pos("ADA/USDT:USDT", 25.0),
                            _pos("PENDLE/USDT:USDT", 0.0)])
        err = ex._preflight_check(10.0)
        assert err and "PENDLEUSDT" in err, (
            '"exposure cannot be measured" does not say what to go and '
            "change — `_leverage_field_phrase`'s rule, one control over.")
        assert "/liveclose" in err, "and the remedy is a door that exists"

    def test_the_partial_refusal_says_the_figure_is_a_floor(self, tmp_path):
        ex = _ex(tmp_path, [_pos("ADA/USDT:USDT", 25.0),
                            _pos("PENDLE/USDT:USDT", 0.0)])
        err = ex._preflight_check(10.0)
        assert "$25.00" in err and "floor" in err and "1 of 2" in err

    def test_nothing_read_quotes_no_floor(self, tmp_path):
        # A different fact from the partial case, so a different sentence:
        # there is no floor to quote, and quoting one would be a figure
        # nobody measured printed as the account's committed capital.
        ex = _ex(tmp_path, [_pos("ADA/USDT:USDT", 0.0)])
        err = ex._preflight_check(10.0)
        assert err and "floor" not in err and "ADAUSDT" in err
        assert "$0.00" not in err

    def test_a_fully_read_book_under_the_cap_still_passes(self, tmp_path):
        ex = _ex(tmp_path, [_pos(cost=25.0)])
        assert ex._preflight_check(10.0) is None, (
            "Fail-closed is not fail-always: a book that could be read is "
            "enforced exactly as before.")

    def test_a_fully_read_book_over_the_cap_gets_the_ordinary_refusal(self, tmp_path):
        ex = _ex(tmp_path, [_pos(cost=MICRO_MAX_TOTAL_EXPOSURE)])
        err = ex._preflight_check(10.0)
        assert err and "would exceed" in err and "cannot be measured" not in err

    def test_an_empty_book_passes(self, tmp_path):
        assert _ex(tmp_path, [])._preflight_check(10.0) is None


class TestTheCapAndTheCardReadOneBook:
    """A resting limit order is in both books or in neither."""

    def test_a_pending_limit_order_counts_toward_the_cap(self, tmp_path):
        # It carries the SIZED margin (`cost_usd=cost` at placement), so
        # counting it is a reading rather than an estimate — and the gate was
        # the one surface that did not.
        book = [_pos(cost=MICRO_MAX_TOTAL_EXPOSURE - 5.0, status="pending_fill")]
        ex = _ex(tmp_path, book)
        assert sum(p.cost_usd for p in book if p.status == "open") == 0.0, (
            "what the cap used to see")
        err = ex._preflight_check(10.0)
        assert err and "would exceed" in err

    def test_the_cap_and_the_card_answer_the_same_figure(self, tmp_path):
        # The request has to clear the PER-POSITION cap or a different gate
        # answers first — the first draft asked for $500 and read the
        # per-trade refusal as this one.
        ex = _ex(tmp_path, [_pos(cost=300.0),
                            _pos(cost=120.0, status="pending_fill")])
        assert committed_margin(ex.open_positions).total == 420.0
        # Driven rather than asserted about the source: the cap's refusal
        # quotes its own total, and it is the card's.
        err = ex._preflight_check(90.0)
        assert err and f"${420.0 + 90.0:,.2f}" in err


class TestOneReading:
    """Five raw sums, and a sixth copy of the judgement written by hand."""

    # No allow-list, deliberately: `committed_margin` itself sums the floats
    # `position_size_basis` handed back, not the field, so the rule has no
    # exception to carry — and an allow-list entry nothing needs is a claim
    # that there is one.

    @staticmethod
    def _raw_sums():
        hits = []
        for path in sorted(ROOT.glob("bot/**/*.py")):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "sum"):
                    continue
                if "cost_usd" not in ast.dump(node):
                    continue
                hits.append(f"{rel}:{node.lineno}")
        return hits

    def test_no_surface_sums_the_margin_by_hand(self):
        hits = self._raw_sums()
        assert not hits, (
            "A second sum of this field is a second answer about how much of "
            "the book it covers. Ask `committed_margin`.\n" + "\n".join(hits))

    def test_the_rule_can_see_one(self, tmp_path):
        # A rule no input can reach is a claim that there is a check.
        planted = tmp_path / "bot" / "planted"
        planted.mkdir(parents=True)
        (planted / "card.py").write_text(
            "def f(rows):\n    return sum(p.cost_usd for p in rows)\n")
        found = []
        tree = ast.parse((planted / "card.py").read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "sum" and "cost_usd" in ast.dump(node)):
                found.append(node.lineno)
        assert found == [2]

    def test_the_lossy_property_is_gone(self):
        assert not hasattr(LiveExecutor, "total_exposure_usd"), (
            "A float cannot say that two of its three rows were read. "
            "Deleted rather than wrapped — a lossy accessor beside the "
            "honest one is the second answer this reading removes.")

    def test_the_cross_account_row_asks_the_reading(self, monkeypatch):
        """PATCHED and READ, because a copy agrees with every fixture.

        The first draft of this planted the marker and then asserted about
        the SOURCE — a fixture that cannot fail — and the mutation round said
        so: restoring the hand-written copy left every assertion green.
        """
        import asyncio

        import bot.core.engine as eng
        from tests.test_account_risk_overview import _engine, _FakeExec, _pos

        # `scored` and `total` are both unreachable from the fixture's own
        # book (two readable positions summing to $150), so a row that
        # re-derives either answers a different number.
        marker = CommittedMargin(total=1234.0, scored=7, counted=9,
                                 unread=("X",))
        monkeypatch.setattr(eng, "committed_margin", lambda _p: marker)
        e = _engine(_FakeExec(positions=[_pos(50.0), _pos(100.0)]),
                    op_balance={"total": 10_000.0})
        row = asyncio.run(e.account_risk_overview())[0]
        assert row["exposure_usd"] == 1234.0, row
        assert row["exposure_scored"] == 7, row


class TestTheCardsSayWhatTheyCouldNotRead:
    def test_an_unreadable_exposure_is_not_a_dollar_figure(self):
        from bot.formatters.rich_cards import render_live_portfolio_summary
        out = "\n".join(render_live_portfolio_summary(
            equity=100.0, open_count=1, exposure=None, realized_pnl=None,
            total_closed=0, win_rate=None))
        assert "Exposure: <code>unavailable</code>" in out
        assert "$0.00" not in out

    def test_a_partial_exposure_says_how_much_of_the_book_it_covers(self):
        from bot.formatters.rich_cards import render_live_portfolio_summary
        note = committed_margin_note(
            committed_margin([_pos(cost=25.0), _pos(cost=0.0)]))
        out = "\n".join(render_live_portfolio_summary(
            equity=100.0, open_count=2, exposure=25.0, realized_pnl=None,
            total_closed=0, win_rate=None, exposure_note=note))
        assert "$25.00" in out and "1 of 2" in out

    def test_the_note_is_silent_when_it_does_not_bite(self):
        # A permanent "3 of 3" on every healthy card is the row that trains a
        # reader to stop reading the line.
        assert committed_margin_note(committed_margin([_pos(cost=25.0)])) == ""
        assert committed_margin_note(committed_margin([])) == ""

    def test_the_note_is_silent_when_nothing_was_read(self):
        # The figure beside it is already the word for that; a caveat about a
        # figure that is not there is a hedge about nothing.
        assert committed_margin_note(committed_margin([_pos(cost=0.0)])) == ""

    def test_the_balance_block_carries_the_note(self):
        from bot.formatters.live_balance import (
            BalanceReading,
            render_balance_block,
        )
        r = BalanceReading(venue_answered=True, free=10.0, used=5.0,
                           total=15.0, holdings=[], reason="")
        out = "\n".join(render_balance_block(
            r, exposure=25.0, equity=15.0, sep="-",
            exposure_note=" NOTE"))
        assert "Exposure: <code>$25.00</code> NOTE" in out


class TestEveryCardCarriesTheShortfall:
    """The note reaching a CARD is a different claim from the note being
    right, and the mutation round is what said so: four cards survived every
    assertion about `committed_margin_note` itself. Each is DRIVEN — a scan
    of the call site cannot see whether the string is reached, which is the
    one thing being asked.
    """

    @staticmethod
    def _partial():
        return [_pos("ADA/USDT:USDT", 25.0), _pos("PENDLE/USDT:USDT", 0.0)]

    @staticmethod
    def _note(book):
        return committed_margin_note(committed_margin(book))

    def test_the_risk_card_says_how_much_of_the_book_it_read(self):
        from bot.skills.skill_registry import CheckRiskSkill

        # `_risk` reads neither `self` nor `state`, so it is called unbound:
        # the assertion is about the CARD rather than about a harness.
        out = CheckRiskSkill._risk(None, None, False, 0, 25.0, 25.0, {},
                                   100.0, 2, 0.0,
                                   exp_note=self._note(self._partial()))
        line = next(ln for ln in out.splitlines()
                    if ln.lstrip().startswith("- Exposure:"))
        assert "$25.00" in line and "1 of 2" in line, line

    def test_the_playbook_does_not_divide_by_an_unread_exposure(self, monkeypatch):
        import asyncio

        from tests.test_chat_prompt_describes_only_the_callers_book import (
            TestTheTools,
        )

        h = TestTheTools()
        reg = h._cfg(monkeypatch)
        book = SimpleNamespace(open_positions=[_pos("ADA/USDT:USDT", 0.0)],
                               closed_positions=[], closed_trades_read_failed=False)
        engine = h._tool_engine(viewer=lambda uid: book, equity=100.0,
                                balance={"total": 100.0, "free": 40.0})
        out = asyncio.run(reg.build_default_registry().get("playbook")
                          .execute(engine, user_id="555"))
        # `(total_exposure or 0) / equity` prints 0.0% — the reassuring end of
        # the range — for a book whose margin nobody read.
        assert "Utilization: <code>0.0%" not in out, out
        assert "Total Exposure: <code>$0.00" not in out, out

    def test_the_portfolio_card_says_how_much_of_the_book_it_read(self, monkeypatch):
        import asyncio

        import bot.skills.portfolio_commands as pc
        from tests.test_the_record_cards_read_the_callers_book import Stand

        class _Live:
            """CONFIG is a FROZEN dataclass; the module attribute is the seam."""

            def is_live(self):
                return True

            def __getattr__(self, name):
                return getattr(pc.CONFIG, name)

        monkeypatch.setattr(pc, "CONFIG", _Live())
        book = SimpleNamespace(open_positions=self._partial(), closed_positions=[],
                               closed_trades_read_failed=False)
        me = Stand({"scope": "own", "executor": book, "balance": None,
                    "total": None, "age_s": None})
        asyncio.run(pc.PortfolioCommands._cmd_portfolio(me, object(), object()))
        said = "\n".join(me.sent)
        line = next(ln for ln in said.splitlines() if "Exposure" in ln)
        assert "$25.00" in line and "1 of 2" in line, line

    def test_the_portfolio_card_does_not_print_an_unread_exposure_as_zero(
            self, monkeypatch):
        # A PARTIAL book cannot tell `live_exposure or 0` from the guard —
        # both render $25.00. Only a book where NOTHING was read separates
        # them, and the mutation round is what said so.
        import asyncio

        import bot.skills.portfolio_commands as pc
        from tests.test_the_record_cards_read_the_callers_book import Stand

        class _Live:
            def is_live(self):
                return True

            def __getattr__(self, name):
                return getattr(pc.CONFIG, name)

        monkeypatch.setattr(pc, "CONFIG", _Live())
        book = SimpleNamespace(open_positions=[_pos("ADA/USDT:USDT", 0.0)],
                               closed_positions=[], closed_trades_read_failed=False)
        me = Stand({"scope": "own", "executor": book, "balance": None,
                    "total": None, "age_s": None})
        asyncio.run(pc.PortfolioCommands._cmd_portfolio(me, object(), object()))
        said = "\n".join(me.sent)
        line = next(ln for ln in said.splitlines() if "Exposure" in ln)
        assert "$0.00" not in line, line

    def test_the_livebalance_card_says_how_much_of_the_book_it_read(self, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from bot.core.engine import RuneClawEngine
        from bot.skills.telegram_handler import TelegramHandler

        admin = "6307156912"
        h = TelegramHandler(RuneClawEngine())
        h.users.seed_admin(admin)
        sent: list[str] = []

        ex = MagicMock()
        ex.fetch_balance = AsyncMock(return_value={
            "free": 10.0, "used": 5.0, "total": 15.0, "holdings": []})
        ex._get_exchange = AsyncMock(return_value=MagicMock())
        ex.open_positions = self._partial()
        ex.closed_positions = []
        monkeypatch.setattr(h.engine, "balance_view_executor",
                            lambda *_a, **_k: ex)
        monkeypatch.setattr(h, "_get_tg_id", lambda *_a, **_k: admin)
        monkeypatch.setattr(h, "_reply", AsyncMock(
            side_effect=lambda *a, **k: sent.append(
                next((x for x in a if isinstance(x, str)), ""))), raising=False)

        update, ctx = MagicMock(), MagicMock()
        update.effective_user = MagicMock(id=int(admin))
        update.effective_user.first_name = "Op"
        update.message = MagicMock(reply_text=AsyncMock(
            side_effect=lambda *a, **k: sent.append(a[0] if a else "")))
        update.callback_query = None
        ctx.args = []
        asyncio.run(h._cmd_livebalance(update, ctx))
        said = "\n".join(sent)
        assert "Balance" in said, f"not the card: {said[:200]}"
        # EVERY line, not the first: this card prints the figure twice (the
        # Balance block and the PnL waterfall), and taking the first one read
        # the note off the sibling — the mutation that dropped it from the
        # waterfall survived a green suite.
        lines = [ln for ln in said.splitlines() if "Exposure" in ln]
        assert len(lines) == 2, f"expected two Exposure lines: {lines}"
        for line in lines:
            assert "1 of 2" in line, line
