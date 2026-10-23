"""Confidence band from a stated gap prior, widened when the book is thin.

The prior is not a fit to a dataset this process holds. It is a design
prior wide enough that a 30% handoff sits far outside it, and the replay
is scored against the published weekend-error benchmark separately. A
missing prior does not become a zero-width band.
"""
from __future__ import annotations

# Absolute reopen-gap fractions by (session, symbol class). Design prior.
GAP_PRIOR: dict[tuple[str, str], tuple[float, ...]] = {
    ("regular", "equity"): (0.0005, 0.001, 0.002, 0.003, 0.005, 0.008),
    ("pre", "equity"): (0.001, 0.003, 0.006, 0.01, 0.02, 0.04),
    ("post", "equity"): (0.001, 0.003, 0.006, 0.01, 0.02, 0.04),
    ("overnight", "equity"): (0.002, 0.005, 0.01, 0.02, 0.04, 0.08),
    ("closed", "equity"): (0.002, 0.004, 0.008, 0.015, 0.03, 0.06),
}


def sigma_of(session: str, symbol_class: str = "equity") -> float | None:
    """Standard deviation of the handoff-gap prior. ``None`` if there is no prior."""
    samples = GAP_PRIOR.get((session, symbol_class))
    if not samples:
        return None
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / len(samples)
    std = var ** 0.5
    if std <= 1e-12:
        return None
    return std


def quantile_of(session: str, q: float, symbol_class: str = "equity") -> float | None:
    """Empirical quantile of the absolute-gap prior. ``q`` is in (0, 1]."""
    samples = GAP_PRIOR.get((session, symbol_class))
    if not samples or q <= 0 or q > 1:
        return None
    ordered = sorted(abs(x) for x in samples)
    # Nearest-rank on a short prior. Index is in range by construction.
    idx = int(round(q * (len(ordered) - 1)))
    return ordered[idx]


def band_around(fv: float | None, session: str | None, n_eff_min: float | None,
                *, quantile: float, n_target: float) -> tuple[float | None, float | None]:
    """``(band_lo, band_hi)``. Both ``None`` when ``fv`` or the prior is missing.

    A low ``N_eff`` widens the band. An unreadable ``N_eff`` widens it by a
    fixed factor rather than leaving the prior width, which would read as
    "the book was fine".
    """
    if fv is None or session is None or fv <= 0:
        return (None, None)
    half_frac = quantile_of(session, quantile)
    if half_frac is None:
        return (None, None)
    if n_eff_min is None:
        widen = 2.0
    elif n_eff_min <= 0 or n_target <= 0:
        widen = 2.0
    else:
        widen = n_target / n_eff_min
        if widen < 1.0:
            widen = 1.0
    half = abs(fv) * half_frac * widen
    return (fv - half, fv + half)
