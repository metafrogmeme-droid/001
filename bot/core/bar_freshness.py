"""How many periods old the newest closed bar may be before a series is stale.

One reading, asked by the analyzer (which refuses a stale series before it
analyses it) and by the risk gate (which re-reads the age the analyzer carried
on the idea). Two copies of the threshold would be two answers.

THE FLOOR IS 1.75, NOT 1, AND THE ARITHMETIC IS THE ENGINE'S OWN CACHE.
`_cached_ohlcv` drops the forming bar before it stores and serves the stored
series for `_mtf_ttl(timeframe)` = max(180 s, period / 4). So on a perfectly
healthy feed the newest CLOSED bar ended up to one period before the fetch,
plus the TTL before the read: 1.25 periods on 1h, 1.6 on 5m (the 180 s floor).
A threshold under that refuses the bot's own cache hits.
`tests/test_a_frozen_feed_is_not_fresh.py` holds the default above that sum
for every supported timeframe.

Read from the environment here, not as a `bot/config.py` field, so adding it
moves none of the line citations `.env.example`'s generated block carries.
"""

from __future__ import annotations

from bot.config import _env_float_bounded

#: Default: the newest closed bar may be at most two periods old.
DEFAULT_MAX_PERIODS = 2.0
_MIN_PERIODS = 1.75
_MAX_PERIODS = 48.0


def bar_freshness_max_periods() -> float:
    """``BAR_FRESHNESS_MAX_PERIODS`` (default 2.0), clamped to [1.75, 48]."""
    return _env_float_bounded("BAR_FRESHNESS_MAX_PERIODS", DEFAULT_MAX_PERIODS,
                              _MIN_PERIODS, _MAX_PERIODS)
