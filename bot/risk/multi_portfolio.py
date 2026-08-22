"""
Multi-user portfolio manager for RUNECLAW.

Wraps PortfolioTracker to provide per-user isolated paper wallets.
Each user gets their own balance, positions, trade history, and PNL.
The engine's shared portfolio remains as a fallback for system-level
operations (risk checks, stop monitoring).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import threading
from typing import Optional, Callable

from bot.risk.portfolio import PortfolioTracker, TrailingStopConfig
from bot.utils.models import PortfolioState, TradeExecution

log = logging.getLogger("runeclaw.multi_portfolio")

DEFAULT_PAPER_BALANCE = 10_000.0
DATA_DIR = "data"


class MultiUserPortfolio:
    """Manages per-user PortfolioTracker instances.

    Usage:
        multi = MultiUserPortfolio(default_balance=10_000.0)
        portfolio = multi.get("user_123")  # creates on first access
        portfolio.open_position(idea, size_usd)
    """

    def __init__(
        self,
        default_balance: float = DEFAULT_PAPER_BALANCE,
        on_trade_close: Optional[Callable[[float], None]] = None,
        on_trade_close_user: Optional[Callable[[str, float], None]] = None,
        trailing_config: Optional[TrailingStopConfig] = None,
    ) -> None:
        self._default_balance = default_balance
        self._on_trade_close = on_trade_close
        # User-aware close callback (user_id, pnl) -> None.  When set, each
        # user's trade close is routed WITH its user_id so per-user risk state
        # (loss streak / circuit breaker) can be isolated.  When None, behaviour
        # falls back to the plain (pnl)->None callback above.  Both attributes
        # are resolved LIVE at close time (see _make_close_cb), so the engine can
        # wire them after construction and restored portfolios route correctly.
        self._on_trade_close_user = on_trade_close_user
        self._trailing_config = trailing_config
        self._portfolios: dict[str, PortfolioTracker] = {}
        # Split-venue books, keyed by (user_id, venue). A SECOND map rather
        # than a composite key in the first one — see get(). Declared before
        # _load_existing() because that populates it.
        self._venue_portfolios: dict[tuple, PortfolioTracker] = {}
        self._lock = threading.Lock()
        # Auto-load existing portfolios from disk
        self._load_existing()
        self._load_existing_venues()

    def _make_close_cb(self, user_id: str) -> Callable[[float], None]:
        """Build a stable per-tracker close callback for ``user_id``.

        The returned closure resolves ``_on_trade_close_user`` /
        ``_on_trade_close`` LIVE on every close, so callbacks wired onto this
        manager AFTER a tracker is created (including portfolios restored from
        disk during __init__) still route correctly.  Prefers the user-aware
        callback when present; otherwise preserves the historical (pnl)->None
        behaviour exactly."""
        def _cb(pnl: float) -> None:
            user_cb = self._on_trade_close_user
            if user_cb is not None:
                user_cb(user_id, pnl)
                return
            plain_cb = self._on_trade_close
            if plain_cb is not None:
                plain_cb(pnl)
        return _cb

    def _load_existing(self) -> None:
        """Scan data/ for existing portfolio_*.json files and load them."""
        pattern = os.path.join(DATA_DIR, "portfolio_*.json")
        for path in glob.glob(pattern):
            filename = os.path.basename(path)
            # Extract user_id from "portfolio_{user_id}.json"
            if not filename.startswith("portfolio_") or not filename.endswith(".json"):
                continue
            raw_user_id = filename[len("portfolio_"):-len(".json")]
            if not raw_user_id:
                continue
            # Register under the same canonical key get() will look up by.
            try:
                user_id = self._sanitize(raw_user_id)
            except ValueError:
                continue
            try:
                portfolio = PortfolioTracker(
                    initial_balance=None,  # None triggers _load_state_on_init()
                    on_trade_close=self._make_close_cb(user_id),
                    state_file=path,
                    trailing_config=self._trailing_config,
                )
                self._portfolios[user_id] = portfolio
                snap = portfolio.snapshot()
                log.info(
                    "Restored portfolio for user %s: balance=$%.2f, "
                    "%d open positions, %d trades",
                    user_id, snap.balance_usd,
                    snap.open_positions, snap.total_trades,
                )
            except Exception as e:
                log.error("Failed to load portfolio for user %s from %s: %s",
                          user_id, path, e)

    def _load_existing_venues(self) -> None:
        """Restore split-venue books from ``data/venue/{venue}/portfolio_*.json``.

        Without this, a restart silently resets every split venue to the
        default paper balance with no open positions — which reads on every
        surface as "nothing is happening on Bybit" while a real position sits
        there unmonitored. The single-venue path has had this since it was
        written (``_load_existing``); the split path needs it for the same
        reason.

        The venue comes from the DIRECTORY, and is accepted only if the
        credential store recognises it. A stray directory is skipped rather
        than turned into a venue nobody can trade.
        """
        try:
            from bot.core.venue_key import normalize_venue, venue_root
        except Exception as e:                      # pragma: no cover - import guard
            log.debug("venue portfolio restore skipped: %s", e)
            return
        for path in glob.glob(os.path.join(venue_root(), "*", "portfolio_*.json")):
            venue = normalize_venue(os.path.basename(os.path.dirname(path)))
            if not venue:
                log.warning("Skipping portfolio under an unrecognised venue "
                            "directory: %s", path)
                continue
            filename = os.path.basename(path)
            raw = filename[len("portfolio_"):-len(".json")]
            try:
                user_id = self._sanitize(raw)
            except ValueError:
                continue
            try:
                portfolio = PortfolioTracker(
                    initial_balance=None,  # None triggers _load_state_on_init()
                    on_trade_close=self._make_close_cb(user_id),
                    state_file=path,
                    trailing_config=self._trailing_config,
                )
                self._venue_portfolios[(user_id, venue)] = portfolio
                snap = portfolio.snapshot()
                log.info("Restored %s portfolio for user %s: balance=$%.2f, "
                         "%d open positions, %d trades", venue, user_id,
                         snap.balance_usd, snap.open_positions, snap.total_trades)
            except Exception as e:
                log.error("Failed to load %s portfolio for user %s from %s: %s",
                          venue, user_id, path, e)

    @staticmethod
    def _sanitize(user_id: str) -> str:
        """Canonical key for a user_id.  Must be applied consistently across
        get()/has_user()/_load_existing() so the in-memory dict key, the on-disk
        filename, and every lookup agree.  Previously get() sanitized only on the
        slow path while has_user()/the fast path used the raw id, so any id that
        changed under sanitization could never be found and would be recreated
        (wiping balance/positions) on every access."""
        cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', str(user_id))
        if not cleaned:
            raise ValueError("Invalid user_id: empty after sanitization")
        return cleaned

    def get(self, user_id: str, venue: str = "") -> PortfolioTracker:
        """Get or create a portfolio for the given user, optionally per-venue.

        ``venue=""`` (and the default venue, and any venue the credential store
        does not recognise) resolves to the ORIGINAL single-venue tracker —
        same key, same ``data/portfolio_{user}.json``, same object. That is not
        a convenience: Phase 2 of multi-venue must be byte-identical to Phase 1
        on the default path, and the cheapest way to guarantee that is for the
        default path to be the same code it always was.

        A SPLIT venue gets its own tracker under ``data/venue/{venue}/``. The
        venue is a directory rather than part of the filename because
        ``_load_existing`` reconstructs the user id from the filename and
        ``_sanitize`` would either strip the separator or make user and venue
        ambiguous — see ``bot/core/venue_key`` for the measurement.
        """
        user_id = self._sanitize(user_id)
        try:
            from bot.core.venue_key import is_split, venue_state_path
            split = is_split(venue)
        except Exception:
            # Fail to the single-venue path. An import or lookup fault must
            # never invent a second book; it must fall back to the one that
            # already holds the money.
            split = False

        if not split:
            if user_id in self._portfolios:
                return self._portfolios[user_id]
            with self._lock:
                if user_id not in self._portfolios:
                    self._portfolios[user_id] = PortfolioTracker(
                        initial_balance=self._default_balance,
                        on_trade_close=self._make_close_cb(user_id),
                        state_file=f"data/portfolio_{user_id}.json",
                        trailing_config=self._trailing_config,
                    )
                    log.info("Created portfolio for user %s (balance=$%.2f)",
                             user_id, self._default_balance)
                return self._portfolios[user_id]

        # Split venues live in their OWN map, deliberately. `_portfolios` is
        # read as a user_id → tracker mapping by six modules, and two of them
        # feed its keys straight back into get() — `proactive_monitor` does
        # `for uid in all_portfolios(): get(uid)`. A composite key there would
        # be re-sanitized ('bybit/alice' → 'bybitalice', the slash stripped)
        # and CREATE A PHANTOM ACCOUNT on the way past: the same silent
        # corruption the on-disk layout avoids, arriving through the in-memory
        # key instead of the filename.
        v = str(venue).strip().lower()
        key = (user_id, v)
        if key in self._venue_portfolios:
            return self._venue_portfolios[key]
        with self._lock:
            if key not in self._venue_portfolios:
                self._venue_portfolios[key] = PortfolioTracker(
                    initial_balance=self._default_balance,
                    on_trade_close=self._make_close_cb(user_id),
                    state_file=venue_state_path("portfolio", user_id, v),
                    trailing_config=self._trailing_config,
                )
                log.info("Created portfolio for user %s on %s (balance=$%.2f)",
                         user_id, v, self._default_balance)
            return self._venue_portfolios[key]

    def venue_readings(self, user_id: str) -> list:
        """One ``VenueReading`` per book this user holds, default venue first.

        Phase 3 feeds this to ``venue_aggregate.aggregate`` to count the
        person-level caps. Each book is read INDIVIDUALLY inside its own
        try/except, so one unreadable venue costs that venue's numbers and not
        the whole reading — the "omit, do not blank the rest" half of the
        repo's guard-or-omit rule.

        A book that raises yields a reading whose fields are ``None``. That is
        the entire point: ``None`` propagates into the total as INCOMPLETE, and
        an incomplete total can refuse but never allow. Returning zeroes here
        would silently loosen every cap the moment a snapshot failed.
        """
        from bot.core.venue_key import DEFAULT_VENUE
        from bot.risk.venue_aggregate import VenueReading

        try:
            uid = self._sanitize(user_id)
        except ValueError:
            return []

        out: list = []
        books = []
        if uid in self._portfolios:
            books.append((DEFAULT_VENUE, self._portfolios[uid]))
        for (u, v), book in sorted(self._venue_portfolios.items()):
            if u == uid:
                books.append((v, book))

        for venue, book in books:
            try:
                snap = book.snapshot()
                out.append(VenueReading(
                    venue=venue,
                    open_positions=int(snap.open_positions),
                    equity_usd=float(snap.equity_usd),
                    daily_pnl_usd=float(snap.daily_pnl),
                ))
            except Exception as exc:
                log.warning("Could not read %s book for user %s: %s",
                            venue, uid, exc)
                # Fields stay None. Never 0.0 — see the docstring.
                out.append(VenueReading(venue=venue,
                                        unreadable_reason="snapshot_failed"))
        return out

    def venue_portfolios(self) -> dict:
        """``{(user_id, venue): tracker}`` for split venues only.

        Separate from ``all_portfolios()`` because that one answers "which
        USERS have a book", and every caller of it treats the key as a user id.
        """
        return dict(self._venue_portfolios)

    def _every_tracker(self):
        """Every tracker this manager owns, default-venue and split alike.

        The distinction between the two maps is about KEYS. It is not about
        money: a total that skips the split venues under-reports real exposure,
        and an under-reported exposure is a cap that does not bind. Every
        aggregate below goes through here for that reason.
        """
        return list(self._portfolios.values()) + list(self._venue_portfolios.values())

    def has_user(self, user_id: str) -> bool:
        """Check if a user has an active portfolio."""
        try:
            return self._sanitize(user_id) in self._portfolios
        except ValueError:
            return False

    def all_user_ids(self) -> list[str]:
        """Return all user IDs with active portfolios."""
        return list(self._portfolios.keys())

    def all_portfolios(self) -> dict[str, PortfolioTracker]:
        """Return all user portfolios."""
        return dict(self._portfolios)

    def mark_to_market_all(self, prices: dict[str, float]) -> None:
        """Update mark-to-market prices across ALL portfolios, every venue.

        A position whose venue tracker never gets marked keeps its ENTRY price
        as its mark for ever — so its unrealised PnL reads 0.00 and its stop is
        measured against a price from the moment it opened. Not a reporting
        gap; a stop that does not fire."""
        for portfolio in self._every_tracker():
            portfolio.mark_to_market(prices)

    def check_stops_all(self, prices: dict[str, float]) -> dict[str, list[TradeExecution]]:
        """Check stop-losses/take-profits for ALL users. Returns {user_id: [closed_trades]}."""
        results: dict[str, list[TradeExecution]] = {}
        for user_id, portfolio in self._portfolios.items():
            closed = portfolio.check_stops(prices)
            if closed:
                results[user_id] = closed
        # Split venues too — a stop-loss that only runs on the default venue is
        # the "half-done version worse than not doing it" the scope opens with.
        # Keyed by USER (never a composite), and EXTENDED rather than assigned,
        # so a user closing on two venues in one tick keeps both.
        for (user_id, _venue), portfolio in self._venue_portfolios.items():
            closed = portfolio.check_stops(prices)
            if closed:
                results.setdefault(user_id, []).extend(closed)
        return results

    def snapshot_all(self) -> dict[str, PortfolioState]:
        """Get portfolio snapshots for all users."""
        return {uid: p.snapshot() for uid, p in self._portfolios.items()}

    def total_open_positions(self) -> int:
        """Total open positions across all users, on every venue.

        This feeds exposure checks. An under-count here is a cap that does not
        bind — §2 of the scope: the split must never loosen a gate."""
        return sum(len(p.open_positions) for p in self._every_tracker())

    def combined_snapshot(self) -> PortfolioState:
        """Combined portfolio state for system-level risk checks."""
        total_balance = 0.0
        total_equity = 0.0
        total_trades = 0
        total_pnl = 0.0
        total_open = 0
        total_daily_pnl = 0.0
        max_dd = 0.0
        total_initial_balance = 0.0
        total_gross_pnl = 0.0
        total_commission = 0.0

        for p in self._every_tracker():
            snap = p.snapshot()
            total_balance += snap.balance_usd
            total_equity += snap.equity_usd
            total_trades += snap.total_trades
            total_pnl += snap.total_pnl
            total_open += snap.open_positions
            total_daily_pnl += snap.daily_pnl
            total_gross_pnl += snap.total_gross_pnl
            total_commission += snap.total_commission
            # NOTE: max_dd here is the worst individual drawdown, not a true
            # combined drawdown.  We also compute an approximate combined
            # drawdown below from combined equity vs sum of initial balances.
            max_dd = max(max_dd, snap.max_drawdown_pct)
            total_initial_balance += getattr(p, '_initial_balance', snap.balance_usd)

        # Approximate combined drawdown from combined equity vs sum of initial balances.
        # This is more meaningful than the per-user max when portfolios diverge.
        if total_initial_balance > 0:
            combined_peak = max(total_initial_balance, total_equity)
            combined_dd = ((combined_peak - total_equity) / combined_peak * 100) if combined_peak > 0 else 0.0
            max_dd = max(max_dd, combined_dd)

        wins = 0
        total_count = 0
        for p in self._every_tracker():
            for t in p.trade_history:
                total_count += 1
                if t.pnl > 0:
                    wins += 1

        return PortfolioState(
            balance_usd=round(total_balance, 2),
            equity_usd=round(total_equity, 2),
            open_positions=total_open,
            total_trades=total_count,
            win_rate=round(wins / total_count, 2) if total_count > 0 else 0.0,
            total_pnl=round(total_pnl, 2),
            total_gross_pnl=round(total_gross_pnl, 2),
            total_commission=round(total_commission, 2),
            daily_pnl=round(total_daily_pnl, 2),
            max_drawdown_pct=round(max_dd, 2),
        )
