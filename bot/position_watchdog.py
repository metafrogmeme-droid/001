"""A read-only watchdog over the operator's open Bitget positions.

One pass per run, started by a systemd timer
(``scripts/systemd/runeclaw-position-watchdog.timer``, every five minutes
unless the timer says otherwise). A pass:

1. reads the open positions, the resting plan and TP/SL orders and the account
   equity, through the client the bot's own venue adapter builds
   (``bot/core/venues.py`` ``BitgetVenue.create_exchange``) and the same
   queries the executor lists them with (``plan_order_queries``);
2. says of every open position whether a stop-loss and a take-profit are
   resting: present, absent, or unread;
3. adds the open losses the venue reports to today's realized result on the
   bot's own record, and holds the sum to the configured daily stop;
4. sends one Telegram message for what it found, and repeats a finding at
   most once per re-alert window while it lasts.

IT NEVER PLACES, AMENDS OR CANCELS AN ORDER. Two things hold that, and a test
holds each (``tests/test_the_position_watchdog_only_reads.py``). The client is
wrapped so that a request which is not a GET is refused before it is sent
(`read_only`): the client's order methods exist, as they do on any ccxt
client, and none of them can reach the venue through it. And no module this
one imports, at any depth, contains a call that places, amends or cancels an
order: the executor, the engine and the signed REST client are not in its
import closure.

It builds no engine and writes none of the bot's files. Its one file is its
own record of what it last alerted (``data/position_watchdog_state.json``).
No new credential: the venue keys are the operator's, read from the running
configuration as the bot reads them, and the alert goes to the chat the other
out-of-process alerters use (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_ALERT_CHAT_ID``).

OFF BY DEFAULT. With ``POSITION_WATCHDOG_ENABLED`` unset a pass reads nothing
and exits 0.

Unreadable is never zero. A read that fails is reported as unread, in its own
words, and is never counted as "no stop" or as "no loss".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from bot.config import CONFIG, _env_bool, _env_float_bounded
from bot.core.bitget_account_mode import note_classic_refusal
from bot.core.equity_basis import MARKED_EQUITY_FIELDS
from bot.core.order_state import read_amount, stop_attached
from bot.core.plan_cleanup import plan_row_protects
from bot.core.venues import get_venue
from bot.utils.json_store import READ, UNREADABLE, StoreUnreadable, read_json_store, update_json_store
from bot.utils.outbound import reply_safe
from bot.utils.paths import env_state_path, state_path

logger = logging.getLogger("position_watchdog")

ENABLED_ENV = "POSITION_WATCHDOG_ENABLED"
#: Minutes between passes. The timer file is where it is set; this is the
#: figure that file ships with, and a test holds the two together.
DEFAULT_INTERVAL_MIN = 5

EXIT_OK = 0
EXIT_FAILED = 1
#: Switched on without the keys or the alert chat it needs (EX_CONFIG, as the
#: duel bots use it).
EXIT_NOT_CONFIGURED = 78

PRESENT = "present"
ABSENT = "absent"
UNREAD = "unread"

#: The most rows one plan-order query returns. A listing this long may be cut,
#: and a cut listing is not the listing: the legs are then unread, not absent.
LISTING_PAGE = 100

Publisher = Callable[[str], Any]


def switched_on() -> bool:
    return _env_bool(ENABLED_ENV, False)


def realert_seconds() -> float:
    """How long a finding that has not cleared stays quiet after an alert."""
    return 60.0 * _env_float_bounded("POSITION_WATCHDOG_REALERT_MIN", 60.0, 5.0, 1440.0)


def grace_seconds() -> float:
    """How young a position may be before its missing legs are reported: the
    bot places an entry and its stops in separate calls, and a pass that lands
    between them would report a stop that is seconds away."""
    return _env_float_bounded("POSITION_WATCHDOG_GRACE_S", 120.0, 0.0, 3600.0)


# --- the client can only read ------------------------------------------------

class WriteRefused(RuntimeError):
    # The watchdog's client was asked to send something other than a GET.
    # A comment and not a docstring: a class whose whole body is a docstring
    # does not parse once `tests/source_scan.py` `code_only` has blanked it.
    pass


def read_only(exchange: Any) -> Any:
    """Wrap ``exchange`` so that only a GET leaves it.

    Every REST call a ccxt client makes goes through its ``fetch``. The
    refusal names the method and the path and nothing else: no query string,
    no header, no body.
    """
    send = exchange.fetch

    async def fetch(url: Any, method: Any = "GET", headers: Any = None, body: Any = None) -> Any:
        verb = str(method).upper()
        if verb != "GET":
            raise WriteRefused(
                f"the position watchdog only reads: refused {verb} "
                f"{urllib.parse.urlparse(str(url)).path}")
        return await send(url, method, headers, body)

    exchange.fetch = fetch
    return exchange


async def _ask(exchange: Any, call: Callable[[], Awaitable[Any]]) -> Any:
    """``call()``, asked once more on the classic API when the venue answers
    that the account is Classic (40084). The same rule as
    `bitget_account_mode.balance_in_account_family`, for any read."""
    try:
        return await call()
    except Exception as exc:
        if not note_classic_refusal(exchange, exc):
            raise
        return await call()


# --- what the venue holds ----------------------------------------------------

@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: Optional[str]            # "long" | "short" | None when the venue stated neither
    contracts: Optional[float]
    mark: Optional[float]
    entry: Optional[float]
    unrealized: Optional[float]
    opened_ms: Optional[float]
    info: dict

    @property
    def name(self) -> str:
        return f"{self.symbol} {self.side or 'side unstated'}"


@dataclass(frozen=True)
class Account:
    """One pass's reading. A field is None when that read failed; the class
    of the failure is in ``unread``."""
    positions: Optional[list[OpenPosition]]
    plans: Optional[list[dict]]
    equity: Optional[float]
    unread: dict[str, str]


def _number(value: Any) -> Optional[float]:
    """A finite number, or None. A bool is not a number."""
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _open_position(row: Any) -> Optional[OpenPosition]:
    """A unified position row as an `OpenPosition`; None for a row that states
    a size of zero. A row whose size cannot be read is kept: it may be held."""
    if not isinstance(row, dict):
        return None
    contracts = _number(row.get("contracts"))
    if contracts is not None and contracts == 0:
        return None
    side = row.get("side")
    info = row.get("info")
    return OpenPosition(
        symbol=str(row.get("symbol") or "symbol unstated"),
        side=side if side in ("long", "short") else None,
        contracts=contracts,
        mark=_number(row.get("markPrice")),
        entry=_number(row.get("entryPrice")),
        unrealized=_number(row.get("unrealizedPnl")),
        opened_ms=_number(row.get("timestamp")),
        info=info if isinstance(info, dict) else {},
    )


def equity_from_balance(balance: Any, coin: str) -> Optional[float]:
    """The account's equity from a ``fetch_balance`` answer, or None.

    The fields the executor reads, in its order (`MARKED_EQUITY_FIELDS`): the
    venue's own marked-to-market figure, from the raw rows. Then the coin's
    wallet total. A figure that is not positive is not an equity to divide by.
    """
    if not isinstance(balance, dict):
        return None
    raw = balance.get("info")
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows if isinstance(rows, list) else []:
        for key in MARKED_EQUITY_FIELDS:
            value = read_amount(row, key) if isinstance(row, dict) else None
            if value is not None and value > 0:
                return value
    wallet = balance.get(coin)
    total = read_amount(wallet, "total") if isinstance(wallet, dict) else None
    return total if total is not None and total > 0 else None


async def read_account(exchange: Any, venue: Any) -> Account:
    """Positions, resting plan orders and equity. Three reads, each on its own:
    one that fails leaves its field None and the others as they were read."""
    unread: dict[str, str] = {}

    positions: Optional[list[OpenPosition]] = None
    try:
        rows = await _ask(exchange, lambda: exchange.fetch_positions(
            params=venue.futures_params()))
        positions = [p for p in (_open_position(r) for r in rows or []) if p is not None]
    except Exception as exc:
        unread["positions"] = type(exc).__name__

    plans: Optional[list[dict]] = None
    try:
        listed: list[dict] = []
        for query in venue.plan_order_queries():

            async def one_listing(params: dict = dict(query)) -> Any:
                return await exchange.fetch_open_orders(None, params=dict(params))

            rows = await _ask(exchange, one_listing) or []
            if len(rows) >= LISTING_PAGE:
                raise OverflowError(f"a listing returned {len(rows)} rows and may be cut")
            listed.extend(order for order in rows if isinstance(order, dict))
        plans = listed
    except OverflowError:
        unread["plan orders"] = f"a listing of {LISTING_PAGE} rows may be cut"
    except Exception as exc:
        unread["plan orders"] = type(exc).__name__

    equity: Optional[float] = None
    try:
        balance = await _ask(exchange, lambda: exchange.fetch_balance(
            venue.balance_fetch_params()))
        equity = equity_from_balance(balance, venue.balance_coin)
        if equity is None:
            unread["equity"] = "no equity figure in the venue's answer"
    except Exception as exc:
        unread["equity"] = type(exc).__name__

    return Account(positions=positions, plans=plans, equity=equity, unread=unread)


# --- does a position have its stop and its target ---------------------------

#: Bitget's plan types for a stop on a position, and for a target on one.
SL_PLAN_TYPES = frozenset({"pos_loss", "loss_plan", "moving_plan"})
TP_PLAN_TYPES = frozenset({"pos_profit", "profit_plan"})


def take_profit_attached(info: Any) -> Optional[bool]:
    """`order_state.stop_attached`, read for the target: a positive
    ``takeProfit`` or a non-empty ``takeProfitId`` is a target; the fields
    present and empty is the venue saying there is none; absent is unread."""
    if not isinstance(info, dict):
        return None
    if "takeProfit" not in info and "takeProfitId" not in info:
        return None
    price = _number(info.get("takeProfit"))
    if (price is not None and price > 0) or str(info.get("takeProfitId") or "").strip():
        return True
    return False


def _closes(order: dict, info: dict) -> Optional[bool]:
    """Whether a trigger order closes a position: True, False (it opens one),
    or None when the row says neither."""
    trade_side = str(info.get("tradeSide") or "").lower()
    if trade_side == "close":
        return True
    if trade_side == "open":
        return False
    reduce_only = order.get("reduceOnly")
    if isinstance(reduce_only, bool):
        return reduce_only
    return None


def leg_kinds(order: dict, position: OpenPosition) -> Optional[frozenset[str]]:
    """The legs a resting order gives ``position``: a subset of ``{"sl",
    "tp"}``. Empty for an order that protects nothing (an entry trigger).
    None when the row does not say enough to tell.

    Three readings, in order. A position TP/SL names its kind (``planType``).
    A unified strategy order states the levels it holds (``stopLoss``,
    ``takeProfit``). A closing trigger, which is how the bot places its
    classic stops and targets, is a stop when it rests on the losing side of
    the mark and a target on the winning side: the mark and not the entry,
    because a stop trailed past the entry is still a stop.
    """
    raw = order.get("info")
    info: dict = raw if isinstance(raw, dict) else {}
    plan = str(info.get("planType") or "").lower()
    if plan in SL_PLAN_TYPES:
        return frozenset({"sl"})
    if plan in TP_PLAN_TYPES:
        return frozenset({"tp"})

    stated = set()
    stop = _number(info.get("stopLoss"))
    target = _number(info.get("takeProfit"))
    if stop is not None and stop > 0:
        stated.add("sl")
    if target is not None and target > 0:
        stated.add("tp")
    if stated:
        return frozenset(stated)

    closes = _closes(order, info)
    if closes is False:
        return frozenset()
    if closes is None:
        return None
    trigger = _number(order.get("triggerPrice"))
    reference = position.mark if position.mark is not None else position.entry
    if trigger is None or reference is None or position.side is None or trigger == reference:
        return None
    on_losing_side = (trigger < reference) == (position.side == "long")
    return frozenset({"sl"} if on_losing_side else {"tp"})


def _could_protect(order: dict, position: OpenPosition) -> bool:
    """False only when the order is definitely another position's: a different
    market, or a side the venue stated and it is the other one."""
    if order.get("symbol") != position.symbol:
        return False
    side = plan_row_protects(order)
    return side is None or position.side is None or side == position.side


def protection(position: OpenPosition, plans: Optional[list[dict]]) -> dict[str, str]:
    """``{"sl": ..., "tp": ...}``, each `PRESENT`, `ABSENT` or `UNREAD`.

    A leg is present when the position row carries it or a resting order
    gives it. It is absent only when the listing was read whole and every
    order that could be this position's was told apart. Otherwise it is
    unread: a failed listing, or an order nobody can classify, may be the leg.
    """
    found = {"sl": stop_attached(position.info) is True,
             "tp": take_profit_attached(position.info) is True}
    unclear = plans is None
    for order in plans or []:
        if not _could_protect(order, position):
            continue
        kinds = leg_kinds(order, position)
        if kinds is None:
            unclear = True
            continue
        for kind in kinds:
            found[kind] = True
    return {kind: PRESENT if found[kind] else (UNREAD if unclear else ABSENT)
            for kind in ("sl", "tp")}


# --- the daily stop ----------------------------------------------------------

def _bot_state_dir() -> Path:
    """Where the bot keeps its state: ``RUNECLAW_STATE_DIR`` as the bot reads
    it, else ``data``."""
    named = os.environ.get("RUNECLAW_STATE_DIR", "").strip()
    return state_path(named) if named else state_path("data")


def realized_today(today: str, state_dir: Optional[Path] = None) -> tuple[Optional[float], str]:
    """``(realized, basis)``: today's realized result on the bot's own record.

    The figure is the one the bot's daily-loss breaker accumulates
    (``live_daily_pnl``), read from the file the bot saves it to, with the
    bot's own day rule (`RiskEngine._restore_live_daily`): a record stamped
    another day is no loss recorded today. A file that is missing, will not
    read, or holds no number is None, never zero.
    """
    folder = state_dir if state_dir is not None else _bot_state_dir()
    risk: Any = None
    detail = "the bot has saved no risk state"
    for name, key in (("combined_state.json", "risk"), ("risk_state.json", None)):
        reading = read_json_store(folder / name)
        if reading.state == UNREADABLE:
            return None, f"{name} could not be read ({reading.detail})"
        if reading.state != READ:
            continue
        risk = reading.data.get(key) if key else reading.data
        if isinstance(risk, dict):
            break
        detail = f"{name} holds no risk state"
    if not isinstance(risk, dict):
        return None, detail
    day = risk.get("live_daily_day")
    if not isinstance(day, str) or not day:
        return None, "the bot's record names no day"
    if day != today:
        return 0.0, f"the bot's last record is from {day}; nothing recorded today"
    pnl = _number(risk.get("live_daily_pnl"))
    if pnl is None:
        return None, "the bot's record of today holds no number"
    return pnl, "the bot's record of today"


@dataclass(frozen=True)
class DailyStop:
    state: str                     # "within" | "breached" | "unread"
    detail: str
    realized: Optional[float] = None
    open_losses: Optional[float] = None
    equity: Optional[float] = None
    loss_pct: Optional[float] = None


def open_losses(positions: Optional[list[OpenPosition]]) -> Optional[float]:
    """The sum of the losing positions' unrealized results, zero or below.
    None when the positions were not read or one of them states no figure: a
    sum over rows that include an unread one is not the sum."""
    if positions is None:
        return None
    total = 0.0
    for position in positions:
        if position.unrealized is None:
            return None
        total += min(position.unrealized, 0.0)
    return total


def daily_stop(account: Account, realized: Optional[float], basis: str, *,
               stop_pct: float, floor_usd: float) -> DailyStop:
    """Realized today plus open losses, against the configured daily stop.

    The breaker's own rule (`RiskEngine`, live): a loss, at or past
    ``stop_pct`` of equity, and at or past the dollar floor. The breaker counts
    realized results only; this adds what the open positions are losing now.
    """
    losing = open_losses(account.positions)
    if realized is not None and losing is not None and realized + losing >= 0:
        # No loss today: there is nothing to hold to the stop, whatever the
        # equity is, so an unread equity is not a finding here.
        return DailyStop("within", basis, realized, losing, account.equity, 0.0)
    missing = [name for name, value in (("today's realized result", realized),
                                        ("open losses", losing),
                                        ("equity", account.equity)) if value is None]
    if realized is None or losing is None or account.equity is None:
        why = "; ".join(missing)
        if realized is None:
            why += f" ({basis})"
        return DailyStop(UNREAD, f"not read: {why}", realized, losing, account.equity)
    loss = -(realized + losing)
    loss_pct = loss / account.equity * 100.0
    breached = loss_pct >= stop_pct and loss >= floor_usd
    return DailyStop("breached" if breached else "within", basis,
                     realized, losing, account.equity, loss_pct)


# --- findings, and which of them are due ------------------------------------

@dataclass(frozen=True)
class Finding:
    key: str
    line: str


def findings(account: Account, stop: DailyStop, *, now: float, grace_s: float,
             stop_pct: float) -> list[Finding]:
    out: list[Finding] = []
    if account.positions is None:
        out.append(Finding("venue:positions", "Open positions could not be read "
                           f"({account.unread.get('positions', 'no reason given')}). "
                           "Nothing below was checked."))
    for position in account.positions or []:
        if position.opened_ms is not None and now - position.opened_ms / 1000.0 < grace_s:
            continue
        held = protection(position, account.plans)
        for kind, label in (("sl", "stop-loss"), ("tp", "take-profit")):
            if held[kind] == ABSENT:
                out.append(Finding(f"{kind}:absent:{position.symbol}:{position.side}",
                                   f"{position.name}: no {label} is resting."))
            elif held[kind] == UNREAD:
                why = account.unread.get("plan orders")
                reason = (f"the plan orders could not be read ({why})" if why
                          else "an order on it could not be told apart")
                out.append(Finding(f"{kind}:unread:{position.symbol}:{position.side}",
                                   f"{position.name}: {label} not confirmed, {reason}."))
    if stop.state == "breached":
        out.append(Finding(
            "daily_stop:breached",
            f"Daily stop: realized {stop.realized:+.2f} plus open losses "
            f"{stop.open_losses:+.2f} is {stop.loss_pct:.2f}% of equity "
            f"{stop.equity:.2f}, at or past the {stop_pct:g}% stop ({stop.detail})."))
    elif stop.state == UNREAD:
        out.append(Finding("daily_stop:unread", f"Daily stop could not be checked: {stop.detail}."))
    return out


def _state_file() -> Path:
    return env_state_path("POSITION_WATCHDOG_STATE_PATH", "data/position_watchdog_state.json")


def _sent(data: Any) -> dict:
    sent = data.get("sent") if isinstance(data, dict) else None
    return sent if isinstance(sent, dict) else {}


def due_keys(path: Path, keys: list[str], *, now: float, window_s: float) -> tuple[list[str], str]:
    """``(due, note)``: the findings to alert now. A finding alerted inside the
    window is quiet. When the watchdog's own record will not read, every
    finding is due and the note says why: a naked position is not kept quiet
    by a broken file."""
    reading = read_json_store(path)
    if reading.state == UNREADABLE:
        return list(keys), (f"The watchdog's own record could not be read ({reading.detail}), "
                            "so this alert repeats every pass until it can.")
    sent = _sent(reading.data)
    due = []
    for key in keys:
        last = _number(sent.get(key))
        if last is None or now - last >= window_s:
            due.append(key)
    return due, ""


def record(path: Path, *, active: list[str], alerted: list[str], now: float) -> str:
    """Write what was alerted, after the send. A finding that has cleared is
    forgotten, so it alerts at once if it comes back. Returns the class of a
    failure to save, or ''."""

    def change(data: dict) -> Any:
        before = _sent(data)
        after = {key: before[key] for key in active if key in before}
        after.update({key: now for key in alerted})
        if after == before and "sent" in data:
            return False
        data["sent"] = after
        return None

    try:
        update_json_store(path, change)
    except StoreUnreadable as exc:
        return exc.detail
    except OSError as exc:
        return type(exc).__name__
    return ""


def message(found: list[Finding], due: list[str], note: str) -> str:
    lines = ["RUNECLAW position watchdog"]
    lines += [f.line for f in found if f.key in due]
    quiet = len(found) - len(due)
    if quiet:
        lines.append(f"{quiet} earlier finding(s) still stand and were not repeated.")
    if note:
        lines.append(note)
    lines.append("The watchdog only reads. It changed nothing.")
    return "\n".join(lines)


# --- the alert chat ----------------------------------------------------------

def telegram_publisher(env: Mapping[str, str]) -> Optional[Publisher]:
    """Posts to the operator's alert chat, with the two variables and the call
    the other out-of-process alerters use
    (``scripts/daily_trend_paper_book.py``, ``scripts/health_check.sh``). None
    when either variable is unset. The token is only ever sent to
    api.telegram.org and is never logged."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if not token or not chat:
        return None

    def post(text: str) -> str:
        body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(request, timeout=20) as reply:
            answer = json.loads(reply.read())
        if not answer.get("ok"):
            raise RuntimeError("telegram refused the message")
        return str((answer.get("result") or {}).get("message_id"))

    return post


# --- one pass ----------------------------------------------------------------

async def run_once(*, exchange_factory: Optional[Callable[[], Any]] = None,
                   publisher: Optional[Publisher] = None,
                   dry_run: bool = False,
                   now: Optional[float] = None,
                   state_file: Optional[Path] = None,
                   bot_state_dir: Optional[Path] = None,
                   out: Callable[[str], Any] = print) -> int:
    """One pass. Returns the process's exit status."""
    if not switched_on():
        logger.info("%s is off: nothing was read", ENABLED_ENV)
        return EXIT_OK
    venue = get_venue("bitget")
    if exchange_factory is None and not venue.has_operator_credentials(CONFIG.exchange):
        logger.error("switched on without the operator's Bitget keys "
                     "(BITGET_API_KEY, BITGET_API_SECRET, BITGET_PASSPHRASE)")
        return EXIT_NOT_CONFIGURED
    if publisher is None and not dry_run:
        publisher = telegram_publisher(os.environ)
        if publisher is None:
            logger.error("switched on without an alert chat "
                         "(TELEGRAM_BOT_TOKEN, TELEGRAM_ALERT_CHAT_ID)")
            return EXIT_NOT_CONFIGURED

    moment = time.time() if now is None else now
    build = exchange_factory or (lambda: venue.create_exchange(CONFIG.exchange))
    exchange = read_only(build())
    try:
        account = await read_account(exchange, venue)
    finally:
        try:
            await exchange.close()
        except Exception as exc:
            logger.debug("closing the client raised %s", type(exc).__name__)

    stop_pct = float(CONFIG.risk.max_daily_loss_pct)
    realized, basis = realized_today(time.strftime("%Y-%m-%d", time.gmtime(moment)), bot_state_dir)
    stop = daily_stop(account, realized, basis, stop_pct=stop_pct,
                      floor_usd=float(CONFIG.risk.daily_loss_breaker_min_usd))
    found = findings(account, stop, now=moment, grace_s=grace_seconds(), stop_pct=stop_pct)

    path = state_file if state_file is not None else _state_file()
    keys = [f.key for f in found]
    due, note = due_keys(path, keys, now=moment, window_s=realert_seconds())
    held = "unread" if account.positions is None else str(len(account.positions))
    logger.info("positions=%s findings=%d due=%d daily_stop=%s",
                held, len(found), len(due), stop.state)
    if dry_run:
        out(message(found, due, note) if found else "RUNECLAW position watchdog: nothing to report.")
        return EXIT_OK
    alerted: list[str] = []
    if due and publisher is not None:
        try:
            publisher(reply_safe(message(found, due, note)))
            alerted = due
        except Exception as exc:
            logger.error("the alert was not sent (%s); it stays due", type(exc).__name__)
    unsaved = record(path, active=keys, alerted=alerted, now=moment)
    if unsaved:
        logger.error("the watchdog's record was not saved (%s)", unsaved)
    return EXIT_OK if alerted == due else EXIT_FAILED


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m bot.position_watchdog",
        description="One read-only pass over the operator's open Bitget positions.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent; send nothing and record nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    try:
        return asyncio.run(run_once(dry_run=args.dry_run))
    except Exception as exc:
        logger.error("the pass did not finish (%s)", type(exc).__name__)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
