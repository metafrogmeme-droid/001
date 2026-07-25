"""ExchangeFlowProvider seeding — the macro LIQUIDATION_RISK path is alive.

The audit found a dead path: MacroEventProvider's funding check reads the
ExchangeFlowProvider CACHE via the sync accessor, but nothing in the engine
loop ever called the async fetchers that fill it — so the cache stayed empty
forever, funding_rate_provider returned 0.0 for every symbol, and the
synthetic LIQUIDATION_RISK event could never fire. The fix: the engine seeds
the cache with the funding rate its order-flow pass already fetched (no new
API calls). These tests pin the seed → cache → macro-event chain.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from bot.core.exchange_flow import ExchangeFlowProvider
from bot.core.macro_events import MacroEventProvider


def test_seed_populates_cache_across_symbol_formats():
    p = ExchangeFlowProvider(exchange_factory=None)
    # Empty cache → honest 0.0 (no invented rates).
    assert p.funding_rate_provider("BTC/USDT") == 0.0
    p.seed_funding_rate("BTC/USDT", 0.0012)
    # Spot and swap notations resolve to the same cache entry.
    assert p.funding_rate_provider("BTC/USDT") == 0.0012
    assert p.funding_rate_provider("BTC/USDT:USDT") == 0.0012
    # Junk never poisons the cache.
    p.seed_funding_rate("BTC/USDT", "not-a-number")
    assert p.funding_rate_provider("BTC/USDT") == 0.0012


def test_seeded_rate_reaches_the_macro_liquidation_check():
    p = ExchangeFlowProvider(exchange_factory=None)
    macro = MacroEventProvider(funding_provider=p.funding_rate_provider)
    now = datetime.now(timezone.utc)
    # No seed → no synthetic event (0.0 is below any critical threshold).
    assert macro._check_funding("BTC/USDT", now) is None
    # Seed an extreme rate — the exact path the engine now feeds.
    p.seed_funding_rate("BTC/USDT", macro.FUNDING_CRITICAL_THRESHOLD * 1.5)
    ev = macro._check_funding("BTC/USDT", now)
    assert ev is not None and ev["event_type"] == "LIQUIDATION_RISK"
    assert ev["impact"] == "CRITICAL" and ev["synthetic"] is True
    # A different (unseeded) symbol still reads honestly empty.
    assert macro._check_funding("ETH/USDT", now) is None


def test_engine_seeds_after_the_order_flow_pass():
    src = Path("bot/core/engine.py").read_text(encoding="utf-8")
    m = re.search(
        r"of_signal = results\[1\][^\n]*\n(.*?)seed_funding_rate\(signal\.symbol, of_signal\.funding_rate\)",
        src, re.S)
    assert m, "engine must seed the exchange-flow cache right after order-flow analyze"
    assert 'getattr(of_signal, "funding_rate", None) is not None' in src
