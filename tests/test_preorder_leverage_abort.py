"""A confirmed pre-order overshoot must not become a fill the guard flattens.

TWO LIVE CARDS, ONE HOUR APART, 2026-09-09:

    ⚠️ EXECUTION ABORTED — TRX/USDT
    The venue filled at 20x against a 5x target (sticky per-symbol setting),
    which is 4.0× the approved leverage. The position was CLOSED ...
    Entry: $0.3403 → Exit: $0.3403
    PnL: -$0.1008 (-0.06% margin / -0.00% notional, 20×) | Fees: $0.10

The price did not move. The whole loss was the fee. CLUSDT cost $0.81 the
same way.

WHAT ACTUALLY HAPPENED, and it is not the "unverifiable leverage" case the
fail-open default was written for:

    fetch_leverage      -> 20   (parses, so `_lev_verified = True`)
    20 != 5             -> retry set_leverage long + short
    re-verify           -> 20   (Bitget's sticky per-symbol leverage)
    LEVERAGE_FAIL_OPEN  -> "1" by default -> proceeding with warning
    order fills at 20x
    post-fill guard     -> 20/5 = 4.0 > 1.5 -> FLATTEN
    two fees

`_lev_verified` was set when the read-back merely PARSED, never when it
MATCHED — so a confirmed mismatch counted as verified, and the block whose
comment promises "only THAT still fails closed" was skipped entirely. The
engine read 20x, tried to fix it, confirmed it was STILL 20x, and placed the
order anyway.

The fix is not to close the fail-open default. That was an operator directive
(2026-07-21, "I can't open trades") about leverage the venue would not
CONFIRM — ETHFI returned a payload the parser could not read while
`set_leverage` had succeeded — and that branch must keep proceeding. An
unreadable value never reaches `preorder_leverage_verdict`.

What changed is narrower: a CONFIRMED reading at or beyond the same ratio the
post-fill guard flattens on aborts before capital moves. One threshold, both
gates, so they cannot disagree about what an overshoot is. Proceeding there
does not open a trade — it opens one that is closed seconds later, and
charges two fees for the privilege.
"""

import asyncio

import pytest

from bot.config import CONFIG
from bot.core.live_executor import (
    LiveExecutor,
    bitget_margin_mode,
    governing_fill_leverage,
    leverage_overshoot_verdict,
    leverage_set_params,
    preorder_leverage_verdict,
    uta_symbol_leverage,
)
from tests.leverage_drive import drive_ensure_leverage, lev

RATIO = 1.5


class TestTheLiveIncident:
    def test_trx_would_not_have_been_placed(self):
        v = preorder_leverage_verdict(5, 20, RATIO)
        assert v["decision"] == "abort"
        assert v["ratio"] == 4.0

    def test_the_reason_says_why_not_placing_is_the_point(self):
        why = preorder_leverage_verdict(5, 20, RATIO)["why"]
        assert "20x" in why and "5x" in why
        assert "overshoot guard would flatten" in why

    def test_clusdt_too(self):
        assert preorder_leverage_verdict(5, 21, RATIO)["decision"] == "abort"


class TestItAgreesWithThePostFillGuard:
    """One threshold, both gates. Disagreement is the whole defect."""

    @pytest.mark.parametrize("target,observed", [
        (5, 20), (5, 21), (10, 20), (3, 20), (5, 8), (2, 10), (5, 100),
        (5, 5), (5, 4), (10, 10), (20, 5), (5, 7), (10, 14), (4, 6),
    ])
    def test_abort_exactly_when_the_guard_would_close(self, target, observed):
        pre = preorder_leverage_verdict(target, observed, RATIO)
        post = leverage_overshoot_verdict(target, observed, RATIO)
        assert (pre["decision"] == "abort") == (post["decision"] == "close"), (
            f"{target}x target, venue {observed}x: pre-order says "
            f"{pre['decision']} and the post-fill guard says {post['decision']} "
            "— the engine would open what it is about to close")

    def test_the_ratios_match_where_both_compute_one(self):
        for target, observed in ((5, 20), (5, 4), (10, 25)):
            pre = preorder_leverage_verdict(target, observed, RATIO)
            post = leverage_overshoot_verdict(target, observed, RATIO)
            assert pre["ratio"] == post["ratio"]


class TestUnderLeverageIsNotAnAbort:
    """The old check was `!= target`, which blocks LESS risk than approved."""

    @pytest.mark.parametrize("observed", [1, 2, 3, 4, 5])
    def test_at_or_under_target_proceeds(self, observed):
        v = preorder_leverage_verdict(5, observed, RATIO)
        assert v["decision"] == "proceed"

    def test_the_old_equality_would_have_blocked_it(self):
        """Stated so the loosening is deliberate and not an accident."""
        assert 4 != 5
        assert preorder_leverage_verdict(5, 4, RATIO)["decision"] == "proceed"


class TestUnreadableIsTheOtherBranchsQuestion:
    """Fail-open governs UNCONFIRMED leverage. This function never sees it."""

    @pytest.mark.parametrize("observed", [None, "", "abc", float("nan"),
                                          float("inf"), object()])
    def test_an_unreadable_reading_proceeds(self, observed):
        v = preorder_leverage_verdict(5, observed, RATIO)
        assert v["decision"] == "proceed"
        assert v["ratio"] is None

    @pytest.mark.parametrize("target", [None, 0, -5, "x"])
    def test_an_unusable_target_proceeds(self, target):
        assert preorder_leverage_verdict(
            target, 20, RATIO)["decision"] == "proceed"

    def test_zero_observed_is_not_an_overshoot(self):
        v = preorder_leverage_verdict(5, 0, RATIO)
        assert v["decision"] == "proceed"

    def test_the_function_is_total(self):
        """It runs on the live order path; raising here kills an execution."""
        for t in (None, 0, -1, 5, "5", 5.0, float("inf"), float("nan")):
            for o in (None, 0, -1, 20, "20", 20.0, float("inf"), object()):
                out = preorder_leverage_verdict(t, o, RATIO)
                assert out["decision"] in ("abort", "proceed")


class TestTheThresholdIsRead:
    def test_a_looser_limit_lets_the_same_reading_through(self):
        assert preorder_leverage_verdict(5, 20, 1.5)["decision"] == "abort"
        assert preorder_leverage_verdict(5, 20, 5.0)["decision"] == "proceed"

    def test_the_boundary_is_not_an_abort(self):
        """`> max_ratio`, same comparison as the post-fill guard."""
        assert preorder_leverage_verdict(
            10, 15, 1.5)["decision"] == "proceed"     # exactly 1.5x
        assert preorder_leverage_verdict(
            10, 16, 1.5)["decision"] == "abort"


# ── the wiring, DRIVEN ────────────────────────────────────────────────────
#
# A source scan of these two sites was written first and thrown away. An hour
# earlier in this same session a line-based scan of a multi-line dispatch
# survived the mutation it existed to catch, with 33 tests green — so the
# claim "the order is not placed" is exercised, not grepped.

def _drive_ensure_leverage(readings, positions=None, target=5,
                           set_raises=False, fail_open=True, monkeypatch=None):
    """(aborted, error_text), over the ONE shared driver.

    The harness lives in `tests/leverage_drive.py` because
    `test_leverage_readback_governs_the_fill.py` drives the same method with
    more knobs (the observed margin mode, a per-side set that refuses, the
    direction), and two copies of a driver are two answers about what the code
    does the moment one is edited. This wrapper keeps this file's assertions
    reading as they did.
    """
    d = drive_ensure_leverage(
        readings, positions=positions, target=target, set_raises=set_raises,
        fail_open=fail_open, monkeypatch=monkeypatch)
    return d.aborted, d.why


def _lev(x):
    """A fetch_leverage payload the real read-back reads as `x`."""
    return lev(x)


#: The ETHFI shape: the call SUCCEEDS and the payload parses to None. An
#: unreadable read-back is not an exception, which is what the first draft of
#: these tests modelled — and raising RuntimeError from the stub tripped
#: `_ensure_leverage`'s own `except RuntimeError: raise`, so the test failed
#: for a reason that had nothing to do with the guard.
_UNREADABLE = {"info": {}}


class TestTheOrderPathRefusesAConfirmedOvershoot:
    def test_the_trx_sequence_aborts(self, monkeypatch):
        """20x read, re-set, 20x again — exactly the live card."""
        aborted, why = _drive_ensure_leverage(
            [_lev(20), _lev(20)], target=5, monkeypatch=monkeypatch)
        assert aborted, "the order would have been placed at 20x"
        assert "20" in why and "5" in why

    def test_it_aborts_even_though_fail_open_is_on(self, monkeypatch):
        """The default that let TRX through. A CONFIRMED overshoot ignores it."""
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _lev(20)], target=5, fail_open=True,
            monkeypatch=monkeypatch)
        assert aborted

    def test_a_venue_that_accepts_the_retry_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _lev(5)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "the retry fixed it; the order should go"

    def test_a_matching_first_read_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(5)], target=5, monkeypatch=monkeypatch)
        assert not aborted

    def test_under_leverage_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(3), _lev(3)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "less risk than approved is not a reason to abort"

    def test_a_small_overshoot_inside_tolerance_proceeds(self, monkeypatch):
        aborted, _ = _drive_ensure_leverage(
            [_lev(6), _lev(6)], target=5, monkeypatch=monkeypatch)
        assert not aborted, "1.2x is inside the 1.5x the guard allows"

    def test_an_unreadable_re_read_keeps_the_first_reading(self, monkeypatch):
        """Failing to confirm a fix is not confirming one."""
        aborted, _ = _drive_ensure_leverage(
            [_lev(20), _UNREADABLE], target=5, monkeypatch=monkeypatch)
        assert aborted, (
            "the first read confirmed 20x and the re-read said nothing — "
            "that is not evidence the retry worked")

    def test_the_position_read_also_aborts(self, monkeypatch):
        """The second confirmation source. A fix in one site is half a fix."""
        aborted, why = _drive_ensure_leverage(
            [_UNREADABLE],
            positions=[{"leverage": 20, "info": {"leverage": "20"}}],
            target=5, monkeypatch=monkeypatch)
        assert aborted
        assert "20" in why

    def test_the_ethfi_case_still_opens(self, monkeypatch):
        """2026-07-21: unparseable read-back, set_leverage SUCCEEDED.

        The regression the fail-open default was added for. An unreadable
        value never reaches the verdict, so this must still proceed.
        """
        aborted, _ = _drive_ensure_leverage(
            [_UNREADABLE], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch)
        assert not aborted, "the 2026-07-21 regression is back"


def _uta_settings(symbol: str, row_leverage: str, *,
                  account_leverage: str | None = None, **extra) -> dict:
    """A settings document. ``account_leverage`` is ``data.leverage``, the
    account ratio, which is not the fill. ``extra`` lands on ``data`` too."""
    data: dict = {
        "symbolConfigList": [{
            "category": "USDT-FUTURES",
            "symbol": symbol,
            "marginMode": "crossed",
            "leverage": row_leverage,
        }],
    }
    if account_leverage is not None:
        data["leverage"] = account_leverage
    data.update(extra)
    return {"code": "00000", "data": data}


_UTA_REFUSED = Exception('{"code":"40085","msg":"uta"}')


class TestUtaSymbolConfigIsThePreOrderRead:
    """A unified account's v2 leverage read does not exist (40085).

    The symbol row on ``GET /api/v3/account/settings`` does. JUPUSDT at the
    account default of 20x against a 5x target is the card: the fill is placed,
    the post-fill guard flattens it in the same minute, the price has moved
    +0.03% and the fee drag on a 20x margin prints about -0.99%. The price is
    not an input. The order is not placed.
    """

    def test_jup_at_20x_is_the_symbol_row_not_the_account_ratio(self):
        payload = _uta_settings(
            "JUPUSDT", "20",
            account_leverage="1000000",
            accountEquity="0.000000009166",
            usdtEquity="0.000000009166",
            coinConfigList=[{"coin": "USDT", "leverage": "6"}],
        )
        read = uta_symbol_leverage(payload, "JUP/USDT:USDT")
        assert read["value"] == 20
        assert read["governs"] is True
        assert read["mode"] == "crossed"
        assert preorder_leverage_verdict(5, read["value"], RATIO)["decision"] == "abort"

    def test_a_symbol_at_the_target_is_not_the_account_ratio(self):
        payload = _uta_settings(
            "JUPUSDT", "5", account_leverage="1000000",
            accountEquity="0.000000009166")
        # data.leverage is the account ratio. The symbol row is 5.
        read = uta_symbol_leverage(payload, "JUPUSDT")
        assert read["value"] == 5
        assert preorder_leverage_verdict(5, read["value"], RATIO)["decision"] == "proceed"

    def test_a_missing_symbol_is_unknown_not_the_dust_ratio(self):
        payload = _uta_settings(
            "BTCUSDT", "20",
            account_leverage="1000000",
            usdtEquity="0.000000009166")
        read = uta_symbol_leverage(payload, "JUP/USDT")
        assert read == {"value": None, "field": None, "governs": None, "mode": None}

    def test_a_blank_leverage_is_unknown_not_zero(self):
        read = uta_symbol_leverage(_uta_settings("JUPUSDT", ""), "JUPUSDT")
        assert read["value"] is None

    def test_the_order_is_not_placed_when_the_symbol_is_at_20x(self, monkeypatch):
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch,
            uta_settings=_uta_settings("TRXUSDT", "20"))
        assert d.uta_reads == 1
        assert d.aborted, "the fill would have been opened and flattened"
        assert "20" in d.why and "5" in d.why

    def test_a_symbol_at_the_target_is_placed(self, monkeypatch):
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch,
            uta_settings=_uta_settings("TRXUSDT", "5"))
        assert d.uta_reads == 1
        assert not d.aborted

    def test_inside_the_ratio_is_placed_and_a_price_move_is_not_an_input(self, monkeypatch):
        # 6/5 = 1.2, inside 1.5. The JUP card's +0.03% never reaches this gate.
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch,
            uta_settings=_uta_settings("TRXUSDT", "6"))
        assert not d.aborted
        assert leverage_overshoot_verdict(5, 6, RATIO)["decision"] == "keep"
        assert leverage_overshoot_verdict(5, 5, RATIO)["decision"] == "keep"
        assert leverage_overshoot_verdict(5, 20, RATIO)["decision"] == "close"

    def test_an_unreadable_symbol_row_does_not_become_an_overshoot(self, monkeypatch):
        payload = _uta_settings(
            "BTCUSDT", "20",
            account_leverage="1000000",
            usdtEquity="0.000000009166")
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch, uta_settings=payload)
        assert d.uta_reads == 1
        assert not d.aborted

    def test_a_classic_read_failure_does_not_consult_uta_settings(self, monkeypatch):
        """Fail-open for a read that did not answer. Not the UTA document."""
        d = drive_ensure_leverage(
            [Exception("fetch_leverage unavailable")], positions=[],
            target=5, fail_open=True, monkeypatch=monkeypatch,
            uta_settings=_uta_settings("TRXUSDT", "20"))
        assert d.uta_reads == 0
        assert not d.aborted

    def test_the_reader_asks_settings_and_a_failure_is_unknown(self, monkeypatch):
        calls = {}

        class _Client:
            @classmethod
            def for_account(cls, creds):
                calls["creds"] = creds
                return cls()

            def request(self, method, path, body_dict=None, timeout=10):
                calls["req"] = (method, path)
                return _uta_settings("JUPUSDT", "20")

        monkeypatch.setattr(
            "bot.core.bitget_v3_client.BitgetV3Client", _Client)
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._credentials = {"api_key": "k", "api_secret": "s", "passphrase": "p"}
        read = asyncio.run(ex._read_uta_symbol_leverage("JUP/USDT:USDT"))
        assert calls["req"] == ("GET", "/api/v3/account/settings")
        assert read["value"] == 20

        def _boom(self, method, path, body_dict=None, timeout=10):
            raise RuntimeError("api key sk-secret-should-not-surface")

        monkeypatch.setattr(_Client, "request", _boom)
        unread = asyncio.run(ex._read_uta_symbol_leverage("JUP/USDT:USDT"))
        assert unread["value"] is None
        assert "sk-secret" not in str(unread)

    def test_the_settings_read_is_asked_in_the_order_mode(self, monkeypatch):
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch,
            uta_settings=_uta_settings("TRXUSDT", "20"))
        assert d.uta_modes == [CONFIG.exchange.margin_mode]


def _both_modes(isolated: str, crossed: str) -> dict:
    """One symbol, both margin modes. The fill uses only one of them."""
    return {
        "code": "00000",
        "data": {
            "symbolConfigList": [
                {"category": "USDT-FUTURES", "symbol": "JUPUSDT",
                 "marginMode": "isolated", "leverage": isolated},
                {"category": "USDT-FUTURES", "symbol": "JUPUSDT",
                 "marginMode": "crossed", "leverage": crossed},
            ],
        },
    }


class TestTheOrderModeIsTheRow:
    """ccxt's UTA setLeverage omits marginMode, so Bitget writes cross.
    The entry sends the configured mode (isolated by default). A cross 5
    beside an isolated 20 is not a confirmation of the isolated fill."""

    def test_isolated_20_is_the_overshoot_when_the_order_is_isolated(self):
        read = uta_symbol_leverage(
            _both_modes("20", "5"), "JUP/USDT:USDT", "isolated")
        assert read["value"] == 20
        assert read["mode"] == "isolated"
        assert preorder_leverage_verdict(5, read["value"], RATIO)["decision"] == "abort"

    def test_crossed_5_does_not_answer_an_isolated_order(self):
        read = uta_symbol_leverage(
            _both_modes("20", "5"), "JUPUSDT", "cross")
        assert read["value"] == 5
        assert read["mode"] == "crossed"
        assert preorder_leverage_verdict(5, read["value"], RATIO)["decision"] == "proceed"

    def test_a_missing_mode_row_is_unknown_not_the_other_modes_20(self):
        payload = _uta_settings("JUPUSDT", "20")  # crossed only
        read = uta_symbol_leverage(payload, "JUP/USDT:USDT", "isolated")
        assert read["value"] is None
        assert governing_fill_leverage(20, read, uta=True) is None

    def test_with_no_mode_the_worst_row_still_answers(self):
        read = uta_symbol_leverage(_both_modes("5", "20"), "JUPUSDT")
        assert read["value"] == 20


class TestTheSetNamesTheOrderMode:
    def test_a_uta_isolated_write_names_the_side(self):
        params = leverage_set_params(
            {"productType": "USDT-FUTURES"}, uta=True,
            margin_mode="isolated", side="long")
        assert params["marginMode"] == "isolated"
        assert params["posSide"] == "long"
        assert "holdSide" not in params

    def test_a_uta_cross_write_is_per_symbol(self):
        params = leverage_set_params(
            {"productType": "USDT-FUTURES"}, uta=True,
            margin_mode="cross", side="short")
        assert params["marginMode"] == "crossed"
        assert "posSide" not in params
        assert "holdSide" not in params

    def test_a_classic_write_still_names_hold_side(self):
        params = leverage_set_params(
            {"productType": "USDT-FUTURES"}, uta=False,
            margin_mode="isolated", side="short")
        assert params["holdSide"] == "short"
        assert "posSide" not in params
        assert "marginMode" not in params

    def test_the_uta_client_is_asked_in_that_dialect(self, monkeypatch):
        d = drive_ensure_leverage(
            [_UTA_REFUSED], positions=[], target=5, fail_open=True,
            monkeypatch=monkeypatch, client_uta=True,
            uta_settings=_uta_settings("TRXUSDT", "5"))
        sides = [(p or {}).get("posSide") for _, _, p in d.set_calls]
        modes = {(p or {}).get("marginMode") for _, _, p in d.set_calls}
        assert modes == {bitget_margin_mode(CONFIG.exchange.margin_mode)}
        assert "holdSide" not in {
            key for _, _, p in d.set_calls for key in (p or {})}
        if bitget_margin_mode(CONFIG.exchange.margin_mode) == "isolated":
            assert sides == [None, "long", "short"]
