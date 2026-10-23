"""Session-aware fair value.

A handoff opens when the session changes, or when the external print
arrives after a gap longer than ``gap_s``. Until the print is confirmed,
its weight is the session coefficient times a decay in the size of the
gap. Confirmation (volume, a second venue, or the book moving halfway)
fires on the tick the evidence arrives. After ``accept_s`` the print is
taken into the blend at full session weight and ``confirmed`` stays
false.

Book mids enter only after they have held for ``persist_s``. A flash
that reverts before then leaves the previous mid in place.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from bot.fvb.adapters import clamp_to_external
from bot.fvb.band import band_around, sigma_of
from bot.fvb.blend import fair_value, full_external_coefficient, session_floor, shares
from bot.fvb.book import Book, book_weight, depth_of
from bot.fvb.collateral import (
    BorrowedMarks,
    CollateralMarks,
    borrowed_asset_marks,
    collateral_marks,
    invariant_holds,
    oi_scale_ratio,
    perp_mark,
)
from bot.fvb.config import FvbConfig


def _pos(price: float | None) -> bool:
    return price is not None and isinstance(price, (int, float)) and math.isfinite(price) and price > 0


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


@dataclass
class _Print:
    ts: float
    price: float
    volume: float | None
    venue: str


@dataclass
class _State:
    last_session: str | None = None
    last_ext_ts: float | None = None
    last_confirmed: float | None = None
    candidate: bool = False
    started: float | None = None
    candidate_price: float | None = None
    reference: float | None = None
    prints: list[_Print] = field(default_factory=list)
    pending: dict[str, tuple[float, float]] = field(default_factory=dict)
    snapshot: dict[str, Book] = field(default_factory=dict)
    forced_at: float | None = None
    confirm_delay_s: float | None = None
    reading: "Reading | None" = None


@dataclass(frozen=True)
class Reading:
    symbol: str
    session: str
    confirmed: bool
    forced: bool
    fv: float | None
    band_lo: float | None
    band_hi: float | None
    external: float | None
    last_trade: float | None
    w_ext: float | None
    n_eff_min: float | None
    borrow_power: float | None
    liquidation: float | None
    debt_check: float | None
    debt_liquidation: float | None
    perp_mark: float | None
    oi_scale: float | None
    ts: float
    confirm_delay_s: float | None
    unit: str
    basis: str


class Pricer:
    """One pricer, many symbols. Time is the caller's ``ts``."""

    def __init__(self, config: FvbConfig | None = None) -> None:
        self.config = config if config is not None else FvbConfig()
        self._states: dict[str, _State] = {}

    def current_reading(self, symbol: str) -> Reading | None:
        state = self._states.get(symbol)
        if state is None:
            return None
        return state.reading

    def ingest_print(self, symbol: str, *, ts: float, session: str,
                     external: float | None, books: list[Book],
                     volume: float | None = None, venue: str = "external",
                     corroborations: list[tuple[str, float]] | None = None,
                     unit: str = "index", basis: str = "") -> Reading:
        state = self._states.setdefault(symbol, _State())
        self._observe(state, books, ts)
        price = external if _pos(external) else None
        vol = volume if volume is not None and volume >= 0 else None
        confirmed, forced = self._handoff(state, ts, session, price, vol, venue, corroborations)
        fv, ext_share, n_eff = self._blend(state, session, price, confirmed, forced)
        band_lo, band_hi = band_around(
            fv, session, n_eff, quantile=self.config.band_quantile, n_target=self.config.n_target,
        )
        marks, debt = self._marks(state, price, band_lo, band_hi, confirmed, forced, fv, ts)
        if not invariant_holds(marks, debt):
            marks = CollateralMarks(None, None)
            debt = BorrowedMarks(None, None)
        oi = oi_scale_ratio(
            confirmed=confirmed, band_lo=band_lo, band_hi=band_hi, fv=fv,
            unconfirmed_cap=self.config.oi_unconfirmed_cap, width_ref=self.config.oi_width_ref,
        )
        marked = self._perp(state.last_confirmed, confirmed, fv, price, band_lo, band_hi)
        if confirmed and _pos(fv):
            state.last_confirmed = fv
        reading = Reading(
            symbol=symbol, session=session, confirmed=confirmed, forced=forced,
            fv=fv, band_lo=band_lo, band_hi=band_hi, external=price, last_trade=price,
            w_ext=ext_share, n_eff_min=n_eff,
            borrow_power=marks.borrow_power, liquidation=marks.liquidation,
            debt_check=debt.borrow_check, debt_liquidation=debt.liquidation,
            perp_mark=marked, oi_scale=oi, ts=ts,
            confirm_delay_s=state.confirm_delay_s if confirmed else None,
            unit=unit, basis=basis,
        )
        state.last_session = session
        if price is not None:
            state.last_ext_ts = ts
        state.reading = reading
        return reading

    def _observe(self, state: _State, books: list[Book], ts: float) -> None:
        live: set[str] = set()
        for book in books:
            live.add(book.venue)
            mid = book.mid
            if not _pos(mid):
                state.snapshot.pop(book.venue, None)
                state.pending.pop(book.venue, None)
                continue
            assert mid is not None
            pending = state.pending.get(book.venue)
            if pending is None or not _close(pending[0], mid):
                state.pending[book.venue] = (mid, ts)
            elif ts - pending[1] >= self.config.persist_s:
                state.snapshot[book.venue] = book
        for venue in list(state.snapshot):
            if venue not in live:
                state.snapshot.pop(venue, None)
                state.pending.pop(venue, None)

    def _handoff(self, state: _State, ts: float, session: str, price: float | None,
                 volume: float | None, venue: str,
                 corroborations: list[tuple[str, float]] | None) -> tuple[bool, bool]:
        if price is None:
            if state.candidate:
                return False, self._forced(state, ts)
            return True, False
        session_changed = state.last_session is not None and session != state.last_session
        time_gap = state.last_ext_ts is not None and (ts - state.last_ext_ts) > self.config.gap_s
        if session_changed or time_gap:
            state.candidate = True
            state.started = ts
            state.candidate_price = price
            state.reference = self._reference(state)
            state.prints = []
            state.forced_at = None
            state.confirm_delay_s = None
        if state.candidate:
            state.prints.append(_Print(ts, price, volume, venue))
            for other_venue, other_price in corroborations or []:
                if _pos(other_price):
                    state.prints.append(_Print(ts, other_price, None, other_venue))
            if self._evidence(state, price, venue, ts):
                state.confirm_delay_s = 0.0 if state.started is None else ts - state.started
                state.candidate = False
                state.forced_at = None
                return True, False
            return False, self._forced(state, ts)
        return True, False

    def _reference(self, state: _State) -> float | None:
        if _pos(state.last_confirmed):
            return state.last_confirmed
        mids = [book.mid for book in state.snapshot.values() if _pos(book.mid)]
        if not mids:
            return None
        ordered = sorted(mid for mid in mids if mid is not None)
        return ordered[(len(ordered) - 1) // 2]

    def _forced(self, state: _State, ts: float) -> bool:
        if state.started is None:
            return False
        if ts - state.started < self.config.accept_s:
            return False
        if state.forced_at is None:
            state.forced_at = state.started + self.config.accept_s
        return True

    def _evidence(self, state: _State, price: float, venue: str, ts: float) -> bool:
        if self._volume_supports(state, price):
            return True
        if self._second_venue(state, price, venue):
            return True
        return self._book_follows(state, ts)

    def _volume_supports(self, state: _State, price: float) -> bool:
        if price <= 0:
            return False
        total = 0.0
        saw = False
        for printed in state.prints:
            if printed.volume is None:
                continue
            if abs(printed.price - price) / price <= self.config.delta:
                total += printed.volume
                saw = True
        if not saw:
            return False
        return total >= self.config.v_min

    def _second_venue(self, state: _State, price: float, venue: str) -> bool:
        if price <= 0:
            return False
        for printed in state.prints:
            if printed.venue == venue:
                continue
            if abs(printed.price - price) / price <= self.config.delta:
                return True
        return False

    def _book_follows(self, state: _State, ts: float) -> bool:
        if state.started is None or not _pos(state.reference) or not _pos(state.candidate_price):
            return False
        if ts - state.started > self.config.confirm_s:
            return False
        ref = state.reference
        cand = state.candidate_price
        assert ref is not None and cand is not None
        halfway = ref + 0.5 * (cand - ref)
        for book in state.snapshot.values():
            mid = book.mid
            if not _pos(mid):
                continue
            assert mid is not None
            if cand >= ref and mid >= halfway:
                return True
            if cand < ref and mid <= halfway:
                return True
        return False

    def _blend(self, state: _State, session: str, price: float | None,
               confirmed: bool, forced: bool) -> tuple[float | None, float | None, float | None]:
        weights: list[float] = []
        mids: list[float | None] = []
        neffs: list[float] = []
        for book in state.snapshot.values():
            depth = depth_of(book, self.config.q_ref)
            weight = book_weight(
                depth, self.config.q_ref,
                n_target=self.config.n_target, cr1_from=self.config.cr1_haircut_from,
            )
            if weight is None:
                continue
            weights.append(weight)
            mids.append(book.mid)
            if depth.n_eff is not None:
                neffs.append(depth.n_eff)
        book_sum = sum(weights)
        decayed = state.candidate and not forced and not confirmed
        decay = self._decay(state, session) if decayed else 1.0
        floor = session_floor(session, self.config.regular_ext_share)
        coeff = full_external_coefficient(book_sum, floor) * decay
        if price is None:
            coeff = 0.0
        cap = self.config.closed_book_cap if session == "closed" and not decayed else None
        split = shares(external_coeff=coeff, book_weights=weights, cap=cap)
        n_eff = min(neffs) if neffs else None
        if split is None:
            return None, None, n_eff
        ext_share, book_shares = split
        fv = fair_value(price, ext_share, mids, book_shares)
        return fv, ext_share, n_eff

    def _decay(self, state: _State, session: str) -> float:
        ref = state.reference
        price = state.candidate_price
        if not _pos(ref) or not _pos(price):
            return 0.0
        assert ref is not None and price is not None
        sigma = sigma_of(session)
        if sigma is None:
            return 0.0
        return math.exp(-abs((price - ref) / ref) / sigma)

    def _marks(self, state: _State, price: float | None, band_lo: float | None,
               band_hi: float | None, confirmed: bool, forced: bool,
               fv: float | None, ts: float) -> tuple[CollateralMarks, BorrowedMarks]:
        elapsed = None
        if forced and state.forced_at is not None:
            elapsed = ts - state.forced_at
        collateral = collateral_marks(
            last_confirmed=state.last_confirmed, new_print=price, band_lo=band_lo,
            confirmed=confirmed, fv=fv, forced=forced, ramp_elapsed_s=elapsed,
            ramp_s=self.config.borrow_power_ramp_s,
        )
        borrowed = borrowed_asset_marks(
            last_confirmed=state.last_confirmed, new_print=price, band_hi=band_hi,
            confirmed=confirmed, fv=fv,
        )
        return collateral, borrowed

    def _perp(self, last: float | None, confirmed: bool, fv: float | None,
              external: float | None, band_lo: float | None, band_hi: float | None) -> float | None:
        if confirmed:
            return clamp_to_external(fv, external, band_lo, band_hi)
        return perp_mark(last_confirmed=last, confirmed=False, fv=fv)
