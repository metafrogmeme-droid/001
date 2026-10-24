"""Design-choice parameters for the fair-value bridge.

Every number here is a parameter, not a measurement. The ones the mechanism
section names as design choices are the defaults. Keeper cadence and
staleness are the HIP-3 deployer-actions intervals, and both can be
changed: see ``oracle_cadence_s`` and ``oracle_stale_s``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FvbConfig:
    """One configuration. Copy it to change a parameter; nothing reads the
    environment except the feature flag."""

    # Session-handoff filter.
    gap_s: float = 300.0
    """A gap longer than this between external prints opens a handoff window."""

    confirm_prints: int = 3
    """How many external prints stay candidates (with ``confirm_s``, later of the two)."""

    confirm_s: float = 60.0
    """Seconds a print stays a candidate unless a confirmation rule fires."""

    accept_s: float = 300.0
    """After this, an external print is taken into the blend even if unconfirmed.

    ``confirmed`` stays false, so a consumer can halt instead of pricing.
    """

    delta: float = 0.01
    """A second print or a fill agrees when it is within this fraction."""

    v_min: float = 100.0
    """Shares that must trade near a candidate before volume confirms it.

    Venue-specific. One share does not clear it.
    """

    # Depth blend.
    n_target: float = 8.0
    q_ref: float = 10_000.0
    """Quote size walked for slippage. Internal. Never a public field."""

    persist_s: float = 30.0
    """A book mid enters the blend only after it has held this long."""

    regular_ext_share: float = 0.9
    """External share floor once a regular-session print is confirmed."""

    closed_book_cap: float = 0.6
    """Book share ceiling while the external session is closed."""

    cr1_haircut_from: float = 0.25
    band_quantile: float = 0.95

    # Keeper cadence and staleness. Both are parameters.
    #
    # Hyperliquid's HIP-3 deployer-actions note says deployers should call
    # about every 3 seconds, and that a stale mark falls back after 10
    # seconds. A companion note's 2.5s figure is not either of those, so
    # it is not a default here. Input ticks further down are a third thing.
    oracle_cadence_s: float = 3.0
    oracle_stale_s: float = 10.0

    # After a forced up-move, borrow power ramps from the last confirmed
    # price up to the forced fair value over this long. Liquidation is the
    # forced fair value for the whole ramp.
    borrow_power_ramp_s: float = 15.0 * 60.0

    # Open-interest scale. A ratio. ``oi_unconfirmed_cap`` is the ceiling
    # while a print is unconfirmed; ``oi_width_ref`` is the band half-width
    # (as a fraction of fair value) that halves the scale.
    oi_unconfirmed_cap: float = 0.5
    oi_width_ref: float = 0.04

    # Input ticks the mechanism section names as design choices, distinct
    # from the keeper cadence above.
    hyperevm_tick_s: float = 1.0
    solana_tick_s: float = 0.4

    signer_threshold: int = 3
    signer_count: int = 5
    scheme_epoch_s: float = 30.0 * 86400.0

    def __post_init__(self) -> None:
        if self.confirm_prints < 1:
            raise ValueError("confirm_prints")
        if self.regular_ext_share <= 0 or self.regular_ext_share >= 1:
            raise ValueError("regular_ext_share")
        if self.closed_book_cap <= 0 or self.closed_book_cap > 1:
            raise ValueError("closed_book_cap")
        if self.signer_threshold < 1 or self.signer_threshold > self.signer_count:
            raise ValueError("signer_threshold")
        if self.oracle_cadence_s <= 0:
            raise ValueError("oracle_cadence_s")
        if self.oracle_stale_s <= 0:
            raise ValueError("oracle_stale_s")
        if self.oracle_stale_s <= self.oracle_cadence_s:
            raise ValueError("oracle_stale_s")
        if self.borrow_power_ramp_s <= 0:
            raise ValueError("borrow_power_ramp_s")
        if self.oi_unconfirmed_cap <= 0 or self.oi_unconfirmed_cap > 1:
            raise ValueError("oi_unconfirmed_cap")
        if self.oi_width_ref <= 0:
            raise ValueError("oi_width_ref")
