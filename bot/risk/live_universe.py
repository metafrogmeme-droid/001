"""A live order stays in the markets a card measured.

Live's parity card of 17 Oct 2026: 226 of 249 strategy exits were on markets
no frozen benchmark holds, and the TradFi legs among them ran at a profit
factor of 0.03 to 0.22 over 77 exits. The scan, the analysis and the paper
book still reach every market; a LIVE order does not, unless the market is in
the measured set. That set is READ from the benchmark on record
(`benchmark/majors_1h/manifest.json`, the snapshot `/parity` compares live
against), never typed here, so the gate and the card cannot name two
universes. `LIVE_UNIVERSE` (a comma list of markets) replaces it when the
operator widens or narrows it on purpose; `LIVE_UNIVERSE_GATE_ENABLED` (on)
is the switch.

Fail-closed: a manifest that cannot be read is a set nobody read, and a live
order is refused by name rather than passed as "in".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from bot.config import CONFIG
from bot.utils.market_symbols import base_ticker as _base
from bot.utils.paths import benchmark_root

GATE = "LIVE_UNIVERSE"
#: The snapshot whose universe is the measured set: the benchmark on record.
MEASURED_SNAPSHOT = "majors_1h"


@dataclass(frozen=True)
class Universe:
    """The measured set, or why it could not be read."""

    markets: Optional[frozenset[str]]
    source: str
    error: Optional[str] = None

    @property
    def readable(self) -> bool:
        return self.markets is not None


def _from_env(raw: str) -> Optional[Universe]:
    names = {_base(s) for s in str(raw or "").split(",") if _base(s)}
    if not names:
        return None
    return Universe(frozenset(names), "LIVE_UNIVERSE")


def measured_universe() -> Universe:
    """The markets a live order may be placed on.

    `LIVE_UNIVERSE` when the operator set one; else the benchmark manifest's
    symbols, as base tickers. A manifest that is missing, unreadable, or
    holds no symbols is `Universe(None, ..., error)`.
    """
    env = _from_env(getattr(CONFIG.risk, "live_universe", ""))
    if env is not None:
        return env
    path = benchmark_root() / MEASURED_SNAPSHOT / "manifest.json"
    source = f"benchmark/{MEASURED_SNAPSHOT}"
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        symbols = manifest.get("symbols") if isinstance(manifest, dict) else None
        if isinstance(symbols, dict):
            symbols = list(symbols)
        names = {_base(s) for s in (symbols or []) if _base(s)}
    except (OSError, ValueError, TypeError) as exc:
        return Universe(None, source, type(exc).__name__)
    if not names:
        return Universe(None, source, "no symbols in the manifest")
    return Universe(frozenset(names), source)


def live_universe_line(asset: str) -> tuple[bool, str]:
    """``(allowed, line)`` for one live order on ``asset``: the check's line
    for the risk card. Nothing here reads the mode; the caller decides it is
    a live evaluation."""
    name = _base(asset)
    uni = measured_universe()
    if not uni.readable:
        return False, (f"{GATE}: the measured set could not be read ({uni.error}); "
                       f"a live order is refused until it can.")
    n = len(uni.markets or ())
    if name and name in (uni.markets or ()):
        return True, f"{GATE}: {name} is in the measured set ({n} markets, {uni.source})"
    return False, (f"{GATE}: {name or asset} is not in the measured set ({n} markets, "
                   f"{uni.source}); a live order is limited to the markets a card "
                   f"measured. LIVE_UNIVERSE=BTC,ETH,... widens it on purpose.")


__all__ = ["GATE", "MEASURED_SNAPSHOT", "Universe", "measured_universe", "live_universe_line"]
