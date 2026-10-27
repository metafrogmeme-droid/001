"""A router that asks every venue an account trades on, at once, and says
which ones answered.

WHY THIS EXISTS. ``engine.flatten_all_positions`` closes what each account's
executor has on RECORD, one account after another, and reports what the
executor said. It never asks the venue. A flatten that an executor reports as
closed over a book the venue still holds, or over a venue that never answered,
reads the same as a flat account on the emergency card. This module is the
second reading: the venue's own position list, read for every account's venue
concurrently, with a per-venue timeout, and a verdict that keeps three
different facts apart:

  ``ok``       the venue answered and the answer was parsed. ``flat`` then says
               whether it holds a position (True / False), or None when a row
               came back that could not be read.
  ``unread``   the venue did not answer in time, refused the read, or answered
               with something that is not a position list. NOT FLAT. Nothing
               here may print this as an empty book or a zero.
  ``faulted``  the adapter itself failed, or refused an order it will not send
               (an order path nobody has driven, a signed order on mainnet).

UNREAD IS NEVER FLAT, and absent is never a measurement. ``balance`` is None
when the venue stated none, never ``0.0``; ``latency_ms`` is None when the
ping did not complete.

WHAT IT DOES NOT DO. It does not replace the executors. They own the book, the
stops, the fills and the closed-trade record; a close that does not go through
``LiveExecutor`` leaves the record open over a flat venue. The router is
additive: the engine runs the executors' flatten first and asks the router what
the venues hold afterwards (``VenueRouter.account_overview``), and only where
the operator opted in (``VENUE_ROUTER_CLOSE_RESIDUAL``) sends a reduce-only
close for what the venue still holds (``VenueRouter.close_all``).

IDENTITY. An adapter is built for ONE executor: its account label, its venue
and the credentials that executor holds (``adapters_for_executors``). There is
no lookup by venue name alone, and nothing falls back to the operator's
executor or the operator's keys for an account whose own cannot be used. Two
adapters for one (account, venue) are refused at construction: one account,
one venue, one book.

THE ORDER PATH. A close is a reduce-only market order in the venue's OWN
position unit (the size the venue reported for the row), never a coin
quantity converted by this module. OKX counts perp orders in contracts where
this bot sends coins (``venues._KeySecretPerpVenue``), and
``PER_USER_EXECUTION_VENUES`` is the allow-list of venues whose order shapes
were driven. OKX is not on it, so ``OkxAdapter`` reads and refuses to close.
Hyperliquid orders are SIGNED; the server never signs on mainnet
(``bot/web/mainnet_guard.py``), so ``HyperliquidAdapter`` closes only on the
testnet client and refuses otherwise. Reads sign nothing and stay open on both.

CCXT. The adapters wrap a SYNCHRONOUS ccxt client through
``asyncio.to_thread``, so a slow venue holds a worker thread and never the
event loop, and a per-venue ``asyncio.wait_for`` bounds how long the router
waits. A thread cannot be cancelled: a timed-out call may still land, which is
why a timeout is ``unread`` and never a verdict. Calls on one client are
serialised by a lock taken in the worker thread, because ccxt's rate limiter
is not thread-safe. Tests hand in a fake client; nothing here reaches a real
venue unless the factory it is built with does.

Error text names the exception class only (CLAUDE.md): a ccxt message can carry
a request body.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

from ccxt.base.errors import BaseError as CcxtBaseError

from bot.core.venues import Venue, per_user_execution_refusal

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_UNREAD = "unread"
STATUS_FAULTED = "faulted"

#: The words ``VenueOutcome.verdict`` speaks in, for the card that renders them.
VERDICT_FLAT = "flat"
VERDICT_OPEN = "open"
VERDICT_UNREAD = "unread"
VERDICT_FAULTED = "faulted"
#: Not an outcome of the router: the account's venue has no adapter. The engine
#: reports it so the account is not silently absent from the check.
VERDICT_NOT_ROUTED = "not_routed"

#: How long the router waits on one venue before calling it unread.
DEFAULT_TIMEOUT_S = 8.0


class VenueReadError(Exception):
    """A venue answered with something that is not the reading asked for."""

    pass


class VenueOrderRefused(Exception):
    """The adapter will not send this order. ``reason`` is this module's own
    sentence (never a venue's text), so it may reach a person."""

    def __init__(self, reason: str):
        super().__init__(type(self).__name__)
        self.reason = reason


@dataclass(frozen=True)
class PositionRow:
    """One open position as the VENUE states it, in the venue's own unit."""

    symbol: str
    side: Optional[str]
    size: float
    mark_price: Optional[float]


@dataclass(frozen=True)
class BalanceReading:
    """The margin coin's balance as stated. ``None`` is "the venue stated
    none", which is not ``0.0``: a fully deployed account states free 0.0."""

    coin: str
    free: Optional[float]
    total: Optional[float]


@dataclass(frozen=True)
class PositionRead:
    """What a position read returned: the rows that parsed, and a count of the
    rows that did not. A row that cannot be read is counted, never dropped."""

    rows: tuple[PositionRow, ...]
    unreadable_rows: int


@dataclass(frozen=True)
class VenueOutcome:
    """One venue's answer for one account."""

    account: str
    venue: str
    status: str
    positions: Optional[tuple[PositionRow, ...]] = None
    unreadable_rows: int = 0
    balance: Optional[BalanceReading] = None
    latency_ms: Optional[float] = None
    messages: tuple[str, ...] = ()
    error: Optional[str] = None
    note: Optional[str] = None

    @property
    def flat(self) -> Optional[bool]:
        """True only when the venue answered, listed no position and returned
        no row that could not be read. None when that cannot be said."""
        if self.status != STATUS_OK or self.positions is None:
            return None
        if self.positions:
            return False
        return True if self.unreadable_rows == 0 else None

    @property
    def open_count(self) -> Optional[int]:
        """Positions the venue listed, None when the list was not read. With
        ``unreadable_rows`` above zero this is a floor, not the count."""
        return None if self.positions is None else len(self.positions)

    @property
    def verdict(self) -> str:
        if self.status == STATUS_FAULTED:
            return VERDICT_FAULTED
        flat = self.flat
        if flat is True:
            return VERDICT_FLAT
        if flat is False:
            return VERDICT_OPEN
        return VERDICT_UNREAD

    def describe(self) -> str:
        """One sentence for a card. It says "flat" only for a venue that was
        read and held nothing."""
        label = self.venue
        v = self.verdict
        if v == VERDICT_FLAT:
            return f"{label}: flat at the venue (read back)"
        if v == VERDICT_OPEN:
            n = self.open_count or 0
            more = " or more" if self.unreadable_rows else ""
            return f"{label}: {n}{more} position(s) still open at the venue"
        if v == VERDICT_FAULTED:
            why = self.note or self.error or "adapter fault"
            return f"{label}: adapter fault ({why}); flat NOT verified"
        if self.error:
            return f"{label}: not read ({self.error}); flat NOT verified"
        if self.unreadable_rows:
            return (f"{label}: {self.unreadable_rows} position row(s) could "
                    f"not be read; flat NOT verified")
        return f"{label}: not read; flat NOT verified"

    def venue_row(self) -> dict:
        """The shape the engine puts on a flatten or account row. No dollar
        figure rides here; the balance stays on ``balance`` for a caller that
        is a private admin view."""
        return {
            "venue": self.venue,
            "venue_state": self.verdict,
            "venue_status": self.status,
            "venue_open": self.open_count,
            "venue_unreadable_rows": self.unreadable_rows,
            "venue_latency_ms": self.latency_ms,
            "venue_error": self.error,
            "venue_closed": list(self.messages),
            "venue_note": self.describe(),
        }


def _finite(value: Any) -> Optional[float]:
    """``float(value)`` when it is a finite number, else None. A bool is not
    a number here (``True`` would read as a size of 1)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_positions(rows: Any) -> PositionRead:
    """ccxt's unified position list as rows, with the unreadable counted.

    A row with ``contracts`` of 0 is not a position. A row whose size is
    ABSENT or not a finite number is not a zero: it is counted in
    ``unreadable_rows`` so a book is never called flat over a row nobody could
    read. The list itself must be a list; anything else is not a reading.
    """
    if not isinstance(rows, (list, tuple)):
        raise VenueReadError("position list is not a list")
    parsed: list[PositionRow] = []
    unreadable = 0
    for row in rows:
        if not isinstance(row, dict):
            unreadable += 1
            continue
        size = _finite(row.get("contracts"))
        if size is None:
            unreadable += 1
            continue
        if size == 0.0:
            continue
        side = str(row.get("side") or "").strip().lower()
        parsed.append(PositionRow(
            symbol=str(row.get("symbol") or ""),
            side=side if side in ("long", "short") else None,
            size=abs(size),
            mark_price=_finite(row.get("markPrice")),
        ))
    return PositionRead(tuple(parsed), unreadable)


def parse_balance(raw: Any, coin: str) -> BalanceReading:
    """The margin coin's free and total from a ccxt balance, None where the
    venue stated nothing. A balance with no entry for the coin is not 0.0."""
    if not isinstance(raw, dict):
        raise VenueReadError("balance is not a mapping")
    entry = raw.get(coin)
    if not isinstance(entry, dict):
        return BalanceReading(coin, None, None)
    return BalanceReading(coin, _finite(entry.get("free")), _finite(entry.get("total")))


class ProductionVenueAdapter(ABC):
    """One account's view of one venue.

    ``account`` is the label the engine uses for the account (the user id, or
    ``operator``); ``venue_id`` is the venue's id. Both are fixed at
    construction, and they are the router's key.
    """

    venue_id: str = ""

    def __init__(self, account: str):
        self.account = str(account)

    @abstractmethod
    async def fetch_positions(self) -> PositionRead:
        """The venue's open positions, or raise: unreadable is not empty."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_balance(self) -> BalanceReading:
        """The margin coin's balance as the venue states it."""
        raise NotImplementedError

    @abstractmethod
    async def close_all(self, reason: str = "venue_router") -> tuple[str, ...]:
        """Send a reduce-only close for every position the venue holds.

        Returns one message per position. Raises ``VenueOrderRefused`` when the
        adapter will not send an order at all, and a read error when the book
        could not be listed (nothing is sent on a guess).
        """
        raise NotImplementedError

    @abstractmethod
    async def ping(self) -> float:
        """Round-trip milliseconds to the venue's clock endpoint, or raise."""
        raise NotImplementedError


class CcxtVenueAdapter(ProductionVenueAdapter):
    """A venue reached through a synchronous ccxt client, off the event loop.

    ``client`` is a ready client; ``client_factory`` builds one on first use,
    in a worker thread. Exactly one is given. ``venue`` supplies the symbol and
    param dialect (``Venue.order_symbol`` / ``close_params``), so no second
    copy of either lives here.
    """

    def __init__(self, account: str, venue: Venue, *, client: Any = None,
                 client_factory: Optional[Callable[[], Any]] = None):
        super().__init__(account)
        if (client is None) == (client_factory is None):
            raise ValueError("give exactly one of client and client_factory")
        self.venue = venue
        self.venue_id = venue.id
        self._client = client
        self._factory = client_factory
        self._call_lock = threading.Lock()
        self._build_lock = threading.Lock()

    # ── the thread boundary ───────────────────────────────────────
    def _client_now(self) -> Any:
        """The client, built once. Runs in a worker thread."""
        with self._build_lock:
            if self._client is None:
                assert self._factory is not None
                self._client = self._factory()
            return self._client

    async def _call_timed(self, method: str, *args: Any, **kwargs: Any) -> tuple[Any, float]:
        """``client.<method>(*args)`` in a worker thread, one call at a time
        per client, with the milliseconds the CALL took. The lock is taken IN
        the thread, so a call that timed out upstream and is still running
        holds it and the next call waits; the clock starts after the lock, so
        a queued call is not charged for the wait."""

        def run() -> tuple[Any, float]:
            client = self._client_now()
            with self._call_lock:
                started = time.perf_counter()
                out = getattr(client, method)(*args, **kwargs)
                return out, (time.perf_counter() - started) * 1000.0

        return await asyncio.to_thread(run)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        out, _ms = await self._call_timed(method, *args, **kwargs)
        return out

    # ── the four operations ───────────────────────────────────────
    async def fetch_positions(self) -> PositionRead:
        raw = await self._call("fetch_positions", None, self.venue.futures_params())
        return parse_positions(raw)

    async def fetch_balance(self) -> BalanceReading:
        raw = await self._call("fetch_balance", self.venue.balance_fetch_params())
        return parse_balance(raw, self.venue.balance_coin)

    async def ping(self) -> float:
        _out, ms = await self._call_timed("fetch_time")
        return round(ms, 1)

    async def _order_refusal(self) -> Optional[str]:
        """Why this adapter will not send an order, or None when it may."""
        return per_user_execution_refusal(self.venue_id)

    async def close_all(self, reason: str = "venue_router") -> tuple[str, ...]:
        refusal = await self._order_refusal()
        if refusal:
            raise VenueOrderRefused(refusal)
        read = await self.fetch_positions()
        if read.unreadable_rows:
            # Closing what parsed and leaving a row nobody could read would
            # report a partial flatten; the caller is told the book was not
            # fully read, in the messages below.
            logger.warning("venue router: %s/%s has %d unreadable position "
                           "row(s); closing the readable ones only",
                           self.account, self.venue_id, read.unreadable_rows)
        messages: list[str] = []
        for pos in read.rows:
            messages.append(await self._close_one(pos, reason))
        if read.unreadable_rows:
            messages.append(
                f"{self.venue_id}: {read.unreadable_rows} position row(s) could "
                f"not be read and were not closed")
        if not read.rows and not read.unreadable_rows:
            messages.append(f"{self.venue_id}: nothing open at the venue")
        return tuple(messages)

    async def _close_one(self, pos: PositionRow, reason: str) -> str:
        label = f"{pos.side or '?'} {pos.symbol}"
        if pos.side is None:
            return f"{label}: side not stated, not closed"
        if self.venue.market_order_needs_price and pos.mark_price is None:
            return f"{label}: no mark price for a slippage-bounded close, not closed"
        side = "sell" if pos.side == "long" else "buy"
        price = pos.mark_price if self.venue.market_order_needs_price else None
        params = self.venue.close_params(None)
        try:
            order = await self._call(
                "create_order", self.venue.order_symbol(pos.symbol), "market",
                side, pos.size, price, params)
        except CcxtBaseError as exc:
            # A refusal placed nothing; a network error says nothing about
            # whether the venue took the order. Neither is reported as closed;
            # the read-back after the sweep decides what is left.
            kind = type(exc).__name__
            return f"{label}: close not confirmed ({kind})"
        oid = str((order or {}).get("id") or "") if isinstance(order, dict) else ""
        logger.info("venue router close sent: %s/%s %s reason=%s",
                    self.account, self.venue_id, label, reason)
        return f"{label}: close order sent" + (f" ({oid})" if oid else "")


class HyperliquidAdapter(CcxtVenueAdapter):
    """Hyperliquid. Orders are signed with a wallet key, so a close is sent
    only on the testnet client; on mainnet the server does not sign
    (``bot/web/mainnet_guard.py``) and the adapter reads only."""

    async def _order_refusal(self) -> Optional[str]:
        base = await super()._order_refusal()
        if base:
            return base
        client = await asyncio.to_thread(self._client_now)
        if getattr(client, "isSandboxModeEnabled", False) is not True:
            return ("Hyperliquid orders are signed and this server does not "
                    "sign on mainnet, so the router sends no close there. Read "
                    "only.")
        return None


class BybitAdapter(CcxtVenueAdapter):
    """Bybit USDT linear perpetuals (one-way mode, as ``BybitVenue`` assumes)."""

    pass


class OkxAdapter(CcxtVenueAdapter):
    """OKX. Read only: OKX counts perp orders in contracts where this bot sends
    coins, and the venue is not in ``PER_USER_EXECUTION_VENUES``. The refusal
    is ``per_user_execution_refusal``'s sentence, so it lifts when that
    allow-list does and not before a test has driven the order shape."""

    pass


#: venue id -> adapter class. A venue absent here is reported ``not_routed``
#: by the engine; it is never given another venue's adapter.
ADAPTER_CLASSES: dict[str, type[CcxtVenueAdapter]] = {
    "hyperliquid": HyperliquidAdapter,
    "okx": OkxAdapter,
    "bybit": BybitAdapter,
}


def sync_client_for(venue: Venue, cfg: Any, credentials: Optional[dict]) -> Any:
    """A synchronous ccxt client for ``venue`` and these credentials.

    ONE construction rule: ``Venue.create_exchange`` builds the async client
    (credential checks, the Hyperliquid master-key refusal, timeouts, options),
    and this reads its settings onto the sync class of the same exchange. The
    async client is never opened, so it holds no session to close. Called in a
    worker thread by the adapter, never on the event loop.
    """
    import ccxt  # the synchronous client

    built = venue.create_exchange(cfg, credentials)
    factory = getattr(ccxt, str(built.id), None)
    if factory is None:
        raise RuntimeError(f"ccxt has no synchronous client for {built.id!r}")
    settings: dict[str, Any] = {
        "timeout": built.timeout,
        "enableRateLimit": True,
        "options": dict(built.options),
    }
    for attr in ("apiKey", "secret", "password", "walletAddress", "privateKey"):
        value = getattr(built, attr, None)
        if value:
            settings[attr] = value
    client = factory(settings)
    if getattr(built, "isSandboxModeEnabled", False) is True:
        client.set_sandbox_mode(True)
    return client


def executor_key(ex: Any) -> tuple[str, str]:
    """``(account, venue)`` for an executor: the router's key for it. The
    account is the executor's own user id, or ``operator`` for the shared
    executor; the venue is the id of the venue THAT executor trades."""
    uid = getattr(ex, "user_id", None)
    venue = getattr(ex, "_venue", None)
    return str(uid or "operator"), str(getattr(venue, "id", "") or "?")


def _factory_for(venue: Venue, cfg: Any, credentials: Optional[dict]) -> Callable[[], Any]:
    """The client factory for ONE executor's venue and credentials. Looked up
    at call time, so the construction rule stays ``sync_client_for``."""

    def build() -> Any:
        return sync_client_for(venue, cfg, credentials)

    return build


def adapters_for_executors(
    executors: Iterable[Any], cfg: Any,
) -> tuple[list[ProductionVenueAdapter], list[tuple[str, str]]]:
    """One adapter per executor whose venue has one, built from THAT executor's
    own account label, venue and credentials.

    Returns ``(adapters, unrouted)``; ``unrouted`` lists ``(account, venue)``
    for an executor whose venue has no adapter (Bitget, BingX), so the caller
    can say the account was not checked instead of leaving it out. An executor
    with no venue is unrouted under venue ``?``.
    """
    adapters: list[ProductionVenueAdapter] = []
    unrouted: list[tuple[str, str]] = []
    for ex in executors:
        account, vid = executor_key(ex)
        venue = getattr(ex, "_venue", None)
        cls = ADAPTER_CLASSES.get(vid)
        if venue is None or cls is None:
            unrouted.append((account, vid))
            continue
        credentials = getattr(ex, "_credentials", None) or None
        adapters.append(cls(account, venue, client_factory=_factory_for(
            venue, cfg, credentials)))
    return adapters, unrouted


class VenueRouter:
    """Runs every adapter at once and reports each venue's outcome."""

    def __init__(self, adapters: Sequence[ProductionVenueAdapter], *,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 close_timeout_s: Optional[float] = None,
                 unrouted: Sequence[tuple[str, str]] = ()):
        if not (isinstance(timeout_s, (int, float)) and not isinstance(timeout_s, bool)
                and math.isfinite(timeout_s) and timeout_s > 0):
            raise ValueError("timeout_s must be a positive number")
        seen: set[tuple[str, str]] = set()
        for a in adapters:
            key = (a.account, a.venue_id)
            if key in seen:
                raise ValueError(f"two adapters for one account and venue: {key}")
            seen.add(key)
        self.adapters = tuple(adapters)
        self.timeout_s = float(timeout_s)
        # A close is a read, an order per position and a read: it is given
        # three reads' worth of time unless the caller says otherwise.
        self.close_timeout_s = (float(close_timeout_s) if close_timeout_s is not None
                                else self.timeout_s * 3.0)
        self.unrouted = tuple(unrouted)

    # ── one guarded call ──────────────────────────────────────────
    async def _guarded(self, coro: Any, timeout: Optional[float] = None,
                       ) -> tuple[Any, Optional[BaseException]]:
        """``(value, None)`` or ``(None, exception)``. Cancellation of the
        router's own task is not swallowed."""
        try:
            return await asyncio.wait_for(
                coro, timeout=self.timeout_s if timeout is None else timeout), None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return None, exc

    @staticmethod
    def _is_read_failure(exc: BaseException) -> bool:
        return isinstance(exc, (asyncio.TimeoutError, TimeoutError,
                                VenueReadError, CcxtBaseError))

    async def _overview_one(self, adapter: ProductionVenueAdapter,
                            extras: bool = True) -> VenueOutcome:
        async def nothing() -> None:
            return None

        (pos, pos_exc), (bal, _bal_exc), (lat, _lat_exc) = await asyncio.gather(
            self._guarded(adapter.fetch_positions()),
            self._guarded(adapter.fetch_balance() if extras else nothing()),
            self._guarded(adapter.ping() if extras else nothing()),
        )
        balance = bal if isinstance(bal, BalanceReading) else None
        latency = float(lat) if isinstance(lat, (int, float)) and not isinstance(lat, bool) else None
        if pos_exc is not None:
            return VenueOutcome(
                adapter.account, adapter.venue_id,
                STATUS_UNREAD if self._is_read_failure(pos_exc) else STATUS_FAULTED,
                balance=balance, latency_ms=latency, error=type(pos_exc).__name__)
        if not isinstance(pos, PositionRead):
            return VenueOutcome(adapter.account, adapter.venue_id, STATUS_FAULTED,
                                balance=balance, latency_ms=latency,
                                error="BadAdapterReply")
        return VenueOutcome(
            adapter.account, adapter.venue_id, STATUS_OK, positions=pos.rows,
            unreadable_rows=pos.unreadable_rows, balance=balance, latency_ms=latency)

    async def _close_one(self, adapter: ProductionVenueAdapter, reason: str) -> VenueOutcome:
        sent, exc = await self._guarded(adapter.close_all(reason), self.close_timeout_s)
        if isinstance(exc, VenueOrderRefused):
            return VenueOutcome(adapter.account, adapter.venue_id, STATUS_FAULTED,
                                error=type(exc).__name__, note=exc.reason)
        if exc is not None:
            return VenueOutcome(
                adapter.account, adapter.venue_id,
                STATUS_UNREAD if self._is_read_failure(exc) else STATUS_FAULTED,
                error=type(exc).__name__)
        messages = tuple(str(m) for m in (sent or ()))
        # The close was sent; what the venue holds NOW is a separate reading.
        # An order the venue accepted is not a flat book.
        after, read_exc = await self._guarded(adapter.fetch_positions())
        if read_exc is not None or not isinstance(after, PositionRead):
            err = type(read_exc).__name__ if read_exc is not None else "BadAdapterReply"
            return VenueOutcome(adapter.account, adapter.venue_id, STATUS_UNREAD,
                                messages=messages, error=err)
        return VenueOutcome(adapter.account, adapter.venue_id, STATUS_OK,
                            positions=after.rows, unreadable_rows=after.unreadable_rows,
                            messages=messages)

    # ── public ────────────────────────────────────────────────────
    async def account_overview(self) -> list[VenueOutcome]:
        """Positions, balance and latency for every adapter, concurrently.

        Never raises for a venue: each adapter yields an outcome, in adapter
        order. A venue past its timeout is ``unread``.
        """
        return await self._run(self._overview_one)

    async def verify_flat(self) -> list[VenueOutcome]:
        """The positions read alone, for a flatten that must not wait on a
        balance or a ping. Same outcomes, ``balance`` and ``latency_ms`` None."""

        async def one(adapter: ProductionVenueAdapter) -> VenueOutcome:
            return await self._overview_one(adapter, extras=False)

        return await self._run(one)

    async def close_all(self, reason: str = "venue_router", *,
                        only: Optional[Iterable[tuple[str, str]]] = None,
                        ) -> list[VenueOutcome]:
        """Reduce-only close of what each venue holds, then a read-back.

        ``only`` limits it to those ``(account, venue)`` keys (the engine
        passes the ones a read found still open); an adapter outside it is
        not touched and has no outcome. Concurrent across venues. A venue that refuses (OKX, Hyperliquid on
        mainnet) is ``faulted`` with the reason; one that cannot be read is
        ``unread``. The outcome's ``positions`` is what the venue holds AFTER.
        """

        async def one(adapter: ProductionVenueAdapter) -> VenueOutcome:
            return await self._close_one(adapter, reason)

        return await self._run(one, only=None if only is None else set(only))

    async def _run(self, per_adapter: Callable[[ProductionVenueAdapter], Any], *,
                   only: Optional[set[tuple[str, str]]] = None) -> list[VenueOutcome]:
        chosen = [a for a in self.adapters
                  if only is None or (a.account, a.venue_id) in only]
        if not chosen:
            return []
        results = await asyncio.gather(
            *(per_adapter(a) for a in chosen), return_exceptions=True)
        out: list[VenueOutcome] = []
        for adapter, res in zip(chosen, results):
            if isinstance(res, VenueOutcome):
                out.append(res)
                continue
            # A coroutine that escaped its own guard (a cancelled child, an
            # error in this module). Reported, with the class name only.
            kind = type(res).__name__
            logger.error("venue router: %s/%s escaped its guard: %s",
                         adapter.account, adapter.venue_id, kind)
            out.append(VenueOutcome(adapter.account, adapter.venue_id,
                                    STATUS_FAULTED, error=kind))
        return out


def build_router(executors: Iterable[Any], cfg: Any, *,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> VenueRouter:
    """The router for these executors (see ``adapters_for_executors``)."""
    adapters, unrouted = adapters_for_executors(executors, cfg)
    return VenueRouter(adapters, timeout_s=timeout_s, unrouted=unrouted)


async def router_reads(executors: Sequence[Any], config: Any, *,
                       flatten_reason: Optional[str] = None,
                       full: bool = False) -> dict:
    """What each executor's VENUE says it holds, keyed ``(account, venue)``.

    The second reading beside the executors' own books, shaped for the rows the
    engine already builds (``VenueOutcome.venue_row``). ``config`` is the
    ``AppConfig``: ``venue_router_enabled`` off returns ``{}`` so every caller
    is byte-identical until an operator turns the router on, and the router is
    not built. Never raises: a router that cannot run leaves every account
    ``unread``, with the exception class and nothing else.

    THE KEYS ARE THE EXECUTORS' OWN. Each adapter is built from one executor's
    account label, venue and credentials (``adapters_for_executors``), so an
    account whose own executor does not exist is absent, and nothing here
    substitutes the operator's book or the operator's keys. A venue with no
    adapter is reported ``not_routed``, never given another venue's reading.

    ``full`` also reads each venue's balance and latency (``/accounts``); a
    flatten reads positions alone. ``flatten_reason`` set, with
    ``VENUE_ROUTER_CLOSE_RESIDUAL`` on, sends a reduce-only close for a venue
    that still holds a position and reads it back. A close that is refused,
    faulted or unread leaves the venue's earlier ``open`` reading standing,
    with the reason beside it: the last thing anyone read is not replaced by a
    reading nobody made.
    """
    if not getattr(config, "venue_router_enabled", False):
        return {}
    close_notes: dict[tuple[str, str], str] = {}
    try:
        router = build_router(
            executors, config.exchange,
            timeout_s=float(getattr(config, "venue_router_timeout_s", DEFAULT_TIMEOUT_S)))
        outcomes = await (router.account_overview() if full else router.verify_flat())
        if flatten_reason is not None and getattr(config, "venue_router_close_residual", False):
            still = {(o.account, o.venue) for o in outcomes if o.verdict == VERDICT_OPEN}
            if still:
                closed = {(o.account, o.venue): o for o in
                          await router.close_all(flatten_reason, only=still)}
                merged: list[VenueOutcome] = []
                for o in outcomes:
                    c = closed.get((o.account, o.venue))
                    if c is None:
                        merged.append(o)
                    elif c.verdict in (VERDICT_FLAT, VERDICT_OPEN):
                        merged.append(c)              # sent, and read back
                    else:
                        merged.append(o)
                        close_notes[(o.account, o.venue)] = c.note or c.error or "no reply"
                outcomes = merged
    except Exception as exc:
        kind = type(exc).__name__
        logger.warning("Venue router could not run: %s", kind)
        failed: dict = {}
        for ex in executors:
            acct, vid = executor_key(ex)
            failed[(acct, vid)] = {
                "venue": vid, "venue_state": VERDICT_UNREAD, "venue_status": STATUS_UNREAD,
                "venue_open": None, "venue_unreadable_rows": 0,
                "venue_latency_ms": None, "venue_error": kind, "venue_closed": [],
                "venue_note": f"{vid}: venue router failed ({kind}); flat NOT verified",
            }
        return failed
    rows: dict = {(o.account, o.venue): o.venue_row() for o in outcomes}
    for key, why in close_notes.items():
        rows[key]["venue_note"] += f" Close not settled: {why}"
    for acct, vid in router.unrouted:
        rows[(acct, vid)] = {
            "venue": vid, "venue_state": VERDICT_NOT_ROUTED, "venue_status": None,
            "venue_open": None, "venue_unreadable_rows": 0, "venue_latency_ms": None,
            "venue_error": None, "venue_closed": [],
            "venue_note": f"{vid}: no venue adapter; only the executor's own record was checked",
        }
    return rows
