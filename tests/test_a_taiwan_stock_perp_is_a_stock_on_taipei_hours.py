"""A Taiwan stock perp is a stock, and its session is the TWSE's.

2026-10-19: the new-listings card named TSMC, UMC, Delta, Nanya, Winbond,
Yageo and TWASX as Crypto, because a bare-ticker TradFi listing is Crypto
until a config entry names it. The owner asked for all of them. As stocks
they take the stock volume floor and the autonomous refusal; and the confirm
gate, which read New York's hours for every equity perp, reads Taipei's for
these (09:00-13:30 Asia/Taipei = 01:00-05:30 UTC, weekdays).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.config import CONFIG, STOCK_PERPETUALS, TAIWAN_STOCK_PERPETUALS
from bot.core.market_scanner import category_for_symbol
from bot.core.stock_trading import (
    autonomous_class_refusal,
    equity_session_for,
    get_market_session,
    stock_market,
)

TW = "TWTSM/USDT:USDT"
US = "TSLA/USDT:USDT"
MON = 19  # 2026-10-19 is a Monday


def _at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


class TestTheyAreStocks:
    @pytest.mark.parametrize("sym", TAIWAN_STOCK_PERPETUALS)
    def test_each_is_a_stock_but_not_in_the_curated_scan_lists(self, sym):
        assert category_for_symbol(sym) == "Stock"
        # Deep Scan's fixed universe is seeded from the curated lists; these
        # near-zero-volume names reach the scan through the catalog instead.
        assert sym not in STOCK_PERPETUALS

    def test_a_bare_ticker_not_named_is_still_crypto(self):
        assert category_for_symbol("TWFOO/USDT:USDT") == "Crypto"

    def test_the_autonomous_confirm_refuses_them_with_stock_trading_off(self):
        stocks = CONFIG.stocks
        was = stocks.enabled
        object.__setattr__(stocks, "enabled", False)
        try:
            assert autonomous_class_refusal(TW) is not None
            assert autonomous_class_refusal("BTC/USDT:USDT") is None
        finally:
            object.__setattr__(stocks, "enabled", was)


class TestTheirHoursAreTaipeis:
    def test_the_market_is_named(self):
        assert stock_market(TW) == "TW" and stock_market("TWTSM/USDT") == "TW"
        assert stock_market(US) == "US"

    def test_taipei_open_is_regular_and_new_york_is_closed(self):
        at = _at(MON, 2)  # 10:00 Taipei, 22:00 the night before in New York
        tw, us = equity_session_for(TW, at), equity_session_for(US, at)
        assert tw.market == "TW" and tw.session_name == "regular" and tw.size_multiplier == 1.0
        assert us.market == "US" and us.session_name != "regular"

    def test_new_york_open_is_taipei_closed(self):
        at = _at(MON, 15)  # 11:00 New York, 23:00 Taipei
        tw, us = equity_session_for(TW, at), equity_session_for(US, at)
        assert us.session_name == "regular" and us.size_multiplier == 1.0
        assert tw.session_name == "closed" and tw.size_multiplier < 1.0
        # Next Taipei open: Tuesday 01:00 UTC, 10 hours on.
        assert tw.hours_until_open == 10.0

    def test_the_close_is_at_05_30_utc(self):
        assert get_market_session(_at(MON, 5, 29), market="TW").session_name == "regular"
        assert get_market_session(_at(MON, 5, 30), market="TW").session_name == "closed"

    def test_friday_after_close_waits_for_monday_and_the_weekend_is_named(self):
        fri = get_market_session(_at(23, 6), market="TW")       # Friday 06:00 UTC
        assert fri.session_name == "closed" and fri.hours_until_open == 67.0
        sat = get_market_session(_at(24, 12), market="TW")      # Saturday
        assert sat.session_name == "weekend" and sat.hours_until_open == 37.0

    def test_the_us_session_is_unchanged_by_default(self):
        at = _at(MON, 15)
        assert get_market_session(at).market == "US"
        assert get_market_session(at) == get_market_session(at, market="US")
