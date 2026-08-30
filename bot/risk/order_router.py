"""Pre-trade slippage estimate from order-book depth, and the order type it implies.

NOT THE SAME THING AS THE WIRED SLIPPAGE GUARD. `live_executor`'s guard is
POST-FILL: it compares what actually filled against the approved entry and
complains afterwards. This walks the book BEFORE the order exists and asks
whether a market order would fill anywhere near the top — the only pre-trade
slippage estimate in this codebase, and the half a post-fill check cannot do.

THE DEFECT IT SAT ON WHILE NOBODY COULD RUN IT
----------------------------------------------
Three exits returned a NUMBER for a book they could not read:

    no order_book_depth      -> 0.02%, "MARKET", warning=None
    insufficient depth       -> 0.0%,  "MARKET", warning=<set>
    zero fill quantity       -> 0.0%,  "MARKET", warning=<set>

0.0% is the best slippage there is. An unreadable book therefore produced the
most reassuring possible reading, and `optimal_order_type()` consumes exactly
that number — so "we could not see the book" and "the book is deep and calm"
recommended the same MARKET order. The first of the three did not even set a
warning. This is CLAUDE.md's first table row on a risk input.

`slippage_pct` is `None` in all three cases now, `readable` says which happened,
and `optimal_order_type(None)` refuses rather than guessing. The paper-mode
default did not disappear — it moved behind an explicit `assume_pct` argument,
so a caller that WANTS a stand-in has to say so and the module never invents
one on its own.

Stdlib-only, no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SlippageEstimate:
    """Result of a slippage estimation.

    `slippage_pct is None` means NOT MEASURED. It is never 0.0 for an
    unreadable book — 0.0 is the best reading available and would be the most
    dangerous thing to invent here.
    """
    slippage_pct: Optional[float]
    estimated_fill: Optional[float]
    order_type: Optional[str]  # "LIMIT" | "MARKET" | None when unmeasured
    warning: Optional[str]
    readable: bool = True


class SmartOrderRouter:
    """Estimates slippage from order book depth and recommends order types."""

    # Thresholds
    LIMIT_THRESHOLD_PCT = 0.1   # slippage > 0.1% -> recommend LIMIT
    REJECT_THRESHOLD_PCT = 0.5  # slippage > 0.5% -> recommend rejection
    DEFAULT_PAPER_SLIPPAGE_PCT = 0.02  # default for paper mode (no book)

    def estimate_slippage(
        self,
        symbol: str,
        size_usd: float,
        order_book_depth: Optional[list[list[float]]] = None,
        assume_pct: Optional[float] = None,
    ) -> dict:
        """Estimate fill price and slippage from order book depth.

        Args:
            symbol: Trading pair e.g. "BTCUSDT".
            size_usd: Notional order size in USD.
            order_book_depth: List of [price, quantity_usd] levels on the
                relevant side (asks for buys, bids for sells).
                Each level: [price, size_in_base_units].
            assume_pct: opt-in stand-in for when there is no book at all
                (paper mode). Omit it and an unreadable book reports None
                rather than a number nobody measured.

        Returns:
            Dict with slippage_pct, estimated_fill, order_type, warning and
            `readable`. `slippage_pct is None` means NOT MEASURED — never 0.0.
        """
        if size_usd <= 0:
            return self._unreadable("Invalid order size")

        if not order_book_depth or len(order_book_depth) == 0:
            # NO BOOK. Previously this returned DEFAULT_PAPER_SLIPPAGE_PCT with
            # warning=None — a made-up number, presented as measured, with
            # nothing marking it. `assume_pct` is how a caller opts into a
            # stand-in now, and it is labelled as one.
            if assume_pct is not None:
                return {
                    "slippage_pct": float(assume_pct),
                    "estimated_fill": None,
                    "order_type": "MARKET",
                    "warning": f"assumed {assume_pct}% — no order book was read",
                    "readable": False,
                }
            return self._unreadable("No order book available")

        # Walk the book to compute volume-weighted average fill price
        best_price = order_book_depth[0][0]
        if best_price <= 0:
            return self._unreadable("Invalid book data")

        # ONE pass. There were two: the first accumulated a `cost_weighted_sum`
        # nothing ever read, under a comment that talked itself out of the
        # formula mid-loop ("Actually: VWAP = ... Let's recalculate properly")
        # and then did it again below. Its only surviving effect was a
        # `filled_usd <= 0` guard the second loop's `total_qty <= 0` already
        # covers exactly — qty is fill_usd / price and price is > 0 at every
        # level counted, so the two conditions cannot disagree.
        filled_usd_2 = 0.0
        total_qty = 0.0
        for level in order_book_depth:
            if len(level) < 2:
                continue
            price, qty = level[0], level[1]
            if price <= 0 or qty <= 0:
                continue
            level_usd = price * qty
            remaining = size_usd - filled_usd_2
            if remaining <= 0:
                break
            fill_usd = min(level_usd, remaining)
            total_qty += fill_usd / price
            filled_usd_2 += fill_usd

        if total_qty <= 0:
            return self._unreadable("Insufficient book depth", best_price)

        vwap = filled_usd_2 / total_qty
        slippage_pct = abs(vwap - best_price) / best_price * 100

        # Determine order type and warnings
        warning = None
        if slippage_pct > self.REJECT_THRESHOLD_PCT:
            order_type = "LIMIT"
            warning = (
                f"High slippage ({slippage_pct:.3f}%) on {symbol} for "
                f"${size_usd:.0f} — consider reducing size or rejecting"
            )
        elif slippage_pct > self.LIMIT_THRESHOLD_PCT:
            order_type = "LIMIT"
            warning = (
                f"Moderate slippage ({slippage_pct:.3f}%) on {symbol} — "
                f"LIMIT order recommended"
            )
        else:
            order_type = "MARKET"

        # If book couldn't fill the full order, warn
        if filled_usd_2 < size_usd * 0.99:
            insufficiency = (1 - filled_usd_2 / size_usd) * 100
            partial_msg = (
                f"Order book can only fill {filled_usd_2:.0f}/{size_usd:.0f} USD "
                f"({insufficiency:.1f}% unfilled)"
            )
            warning = f"{warning}; {partial_msg}" if warning else partial_msg

        return {
            "slippage_pct": round(slippage_pct, 4),
            "estimated_fill": round(vwap, 8),
            "order_type": order_type,
            "warning": warning,
            "readable": True,
        }

    @staticmethod
    def _unreadable(reason: str, best_price: Optional[float] = None) -> dict:
        """One shape for every "we could not measure it" exit.

        `order_type` is None, not "MARKET". The old exits all recommended a
        market order off a slippage they had not measured, which is the
        recommendation with the most to lose when the book is actually thin —
        exactly the case that makes a book unreadable in the first place.
        """
        return {
            "slippage_pct": None,
            "estimated_fill": best_price,
            "order_type": None,
            "warning": reason,
            "readable": False,
        }

    def optimal_order_type(
        self,
        slippage_pct: Optional[float],
        urgency: str = "normal",
    ) -> str:
        """Recommend order type based on slippage and urgency.

        Args:
            slippage_pct: Estimated slippage percentage, or None when the book
                could not be read.
            urgency: "low", "normal", or "high".

        Returns:
            "MARKET", "LIMIT", "REJECT", or "UNKNOWN".

        UNKNOWN, not MARKET, on a None input. Every branch below is a
        comparison against a number; feeding it a fabricated 0.0 walks straight
        past all of them to `return "MARKET"` — the least cautious answer,
        produced by the least information. A caller that wants to trade anyway
        can decide that itself, with the fact in front of it.
        """
        if slippage_pct is None:
            return "UNKNOWN"
        if slippage_pct > self.REJECT_THRESHOLD_PCT and urgency != "high":
            return "REJECT"
        if slippage_pct > self.REJECT_THRESHOLD_PCT and urgency == "high":
            return "MARKET"  # force fill despite slippage
        if slippage_pct > self.LIMIT_THRESHOLD_PCT:
            return "LIMIT"
        if urgency == "high":
            return "MARKET"
        if urgency == "low":
            return "LIMIT"
        return "MARKET"
