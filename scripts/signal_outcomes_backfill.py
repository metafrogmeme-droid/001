#!/usr/bin/env python3
"""Close the website's stuck calls: sealed NEW/OPEN rows nothing will re-send.

The bot's outcome walk used to give each call one look before any bar after it
had closed, and its ledger cap then evicted the call still pending. Its website
row was never sent a second word and stays NEW (or OPEN) for good, so the Claw
Record counts it as a live call forever. The walk is fixed for new calls; this
answers the old ones.

DRY RUN BY DEFAULT. It reads the website's sealed pending calls
(``GET /api/bot/sync/signals/pending``), reads PUBLIC hourly candles from the
venue the bot scans (Bitget; spot, or swap for a ``:`` market) from an hour
before each call forward, and asks the bot's own ``signal_outcomes.resolve``
what each call did, over the same entry window the bot's walk uses. It prints
COUNTS ONLY: no key, symbol, price or level leaves this process in its output.
Nothing is written unless ``--apply`` is given.

``--apply`` posts the FINAL answers (TARGET, STOP, AMBIGUOUS, EXPIRED,
NO_EXIT, UNSCORED) through ``POST /api/bot/sync/signals``, the door the bot
uses, in batches of at most 500. Each carries its own ``resolved_at``: the
close of the bar that answered it (the end of the entry window for EXPIRED,
the end of the week for NO_EXIT), so a call sealed when it was published has
``sealed_at <= resolved_at`` and is counted. A call still genuinely pending, or
one whose candles did not read this run, is not sent. A market the venue does
not list, or candles that do not reach back to the call, are UNSCORED (the
latter is ``resolve``'s own answer). The website's terminal guard keeps a
final word against a later pending one, and lets the bot's later final word
correct this one.

OPERATOR-ONLY. ``--apply`` is refused when ``RUNECLAW_SERVER=1`` (the entry
points set it), like ``scripts/anchor_roots.sh --broadcast``: the server never
writes the public record on its own initiative. The bot secret is read from
the process environment (``BOT_SYNC_SECRET``) when a request is made and is
never printed; the website is ``--base-url`` or the bot's ``site_url()``.

    python3 scripts/signal_outcomes_backfill.py                 # dry run
    python3 scripts/signal_outcomes_backfill.py --apply         # write
"""
from __future__ import annotations

import argparse
import bisect
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: The most rows one POST carries: the website's ingest takes 500 a request.
MAX_BATCH = 500
#: Candles one venue request asks for, and the most requests per market.
PAGE_BARS = 200
MAX_PAGES = 100

#: Exit codes. 3 is "could not check": the pending calls could not be read.
EXIT_OK, EXIT_NOT_ALL_LANDED, EXIT_REFUSED, EXIT_UNREAD = 0, 1, 2, 3


class MarketUnlisted(Exception):
    """The venue does not list this market: no candle will ever answer it."""


@dataclass
class Summary:
    read: int = 0
    markets: int = 0
    markets_read: int = 0
    markets_unlisted: int = 0
    markets_unread: int = 0
    answered: Counter = field(default_factory=Counter)
    still_pending: int = 0
    retry: int = 0
    unread_calls: int = 0
    sealed_after_outcome: int = 0
    to_post: int = 0
    posted: int = 0
    landed: int = 0


def _ms(value: object) -> Optional[int]:
    from bot.core.signal_outcomes import parse_time_ms
    return parse_time_ms(value)


def _window(bars: list, keys: list, created_ms: int, span_ms: int) -> list:
    """The bars from the one open when the call was made through ``span_ms``
    after it: every bar ``resolve`` can read a verdict from, and no more.
    ``resolve`` reads only bars that opened at or after the call, and the end
    of its entry window, its week and the tail grace bounds every verdict, so
    the slice answers exactly what the whole series would. ``keys`` are the
    bars' open times, built once per market."""
    from bot.core.signal_outcomes import BAR_MS
    lo = bisect.bisect_left(keys, created_ms - BAR_MS)
    hi = bisect.bisect_right(keys, created_ms + span_ms + 2 * BAR_MS)
    return bars[lo:hi]


def outcome_payload(row: dict, res) -> dict:
    """The row the website receives: the call's own fields (ignored by the
    upsert for a row it holds) with its outcome set. R only for the two words
    that carry one; ``resolved_at`` is the answering bar's close."""
    from bot.core.signal_outcomes import SCORED
    ms = res.resolved_ms
    return {
        "signal_key": row["signal_key"], "symbol": row["symbol"],
        "direction": row["direction"], "entry_price": row.get("entry_price"),
        "stop_loss": row.get("stop_loss"), "take_profit": row.get("take_profit"),
        "created_at": row.get("created_at"), "status": res.status,
        "pnl": res.r if res.status in SCORED else None,
        "resolved_at": (datetime.fromtimestamp(ms / 1000, UTC).isoformat()
                        if isinstance(ms, (int, float)) else ""),
    }


def run(fetch_page: Callable[[int], Optional[dict]],
        fetch_bars: Callable[[str, int], list],
        post_batch: Callable[[list], bool], *,
        now_ms: int, window_s: object, apply: bool) -> Optional[Summary]:
    """Read every pending call, answer what the candles can, and (with
    ``apply``) post the final answers. None when the pending calls could not
    be read: nothing is known, and nothing was sent."""
    from bot.core import signal_outcomes as so

    rows: list[dict] = []
    after = 0
    while True:
        page = fetch_page(after)
        if page is None:
            return None
        rows.extend(r for r in page["signals"] if isinstance(r, dict) and r.get("signal_key")
                    and r.get("symbol") and r.get("direction"))
        nxt = page.get("next_after_id")
        if nxt is None or not page["signals"] or int(nxt) <= after:
            break
        after = int(nxt)
    out = Summary(read=len(rows))
    by_market: dict = {}
    for r in rows:
        by_market.setdefault(so.market_for(r.get("symbol")), []).append(r)
    out.markets = len(by_market)
    span_ms = (so.entry_window_s(window_s) + so.HORIZON_S + so.TAIL_GRACE_S) * 1000
    final: list[dict] = []
    for market, group in by_market.items():
        answers = []
        if market is None:
            answers = [(r, so.Resolution(so.UNSCORED, "the symbol names no market")) for r in group]
        else:
            starts = [ms for ms in (_ms(r.get("created_at")) for r in group) if ms is not None]
            try:
                bars = fetch_bars(market, min(starts) - so.BAR_MS) if starts else []
            except MarketUnlisted:
                out.markets_unlisted += 1
                answers = [(r, so.Resolution(so.UNSCORED, "the venue does not list this market"))
                           for r in group]
            except Exception:  # noqa: BLE001 -- counted; never scored
                out.markets_unread += 1
                out.unread_calls += len(group)
                continue
            else:
                out.markets_read += 1
                keys = [int(b[0]) for b in bars]
                for r in group:
                    created = _ms(r.get("created_at"))
                    walk = _window(bars, keys, created, span_ms) if created is not None else bars
                    answers.append((r, so.resolve(r, walk, now_ms, window_s)))
        for r, res in answers:
            if res.retry:
                out.retry += 1
            elif res.status not in so.TERMINAL:
                out.still_pending += 1
            else:
                out.answered[res.status] += 1
                sealed = _ms(r.get("sealed_at"))
                if (res.resolved_ms is not None and sealed is not None
                        and sealed > res.resolved_ms):
                    out.sealed_after_outcome += 1
                final.append(outcome_payload(r, res))
    out.to_post = len(final)
    if apply:
        for i in range(0, len(final), MAX_BATCH):
            batch = final[i:i + MAX_BATCH]
            out.posted += len(batch)
            if post_batch(batch):
                out.landed += len(batch)
    return out


def render(s: Optional[Summary], *, apply: bool) -> str:
    """The summary, counts only."""
    if s is None:
        return ("signal outcomes backfill: the website's pending calls could not be read; "
                "nothing was walked and nothing was sent (could not check)")
    words = ", ".join(f"{w} {n}" for w, n in sorted(s.answered.items())) or "none"
    lines = [
        "signal outcomes backfill (" + ("APPLIED" if apply else "dry run: nothing was sent") + ")",
        f"  sealed pending calls read: {s.read}",
        f"  markets: {s.markets} (candles read {s.markets_read}, not listed "
        f"{s.markets_unlisted}, not read {s.markets_unread})",
        f"  final answers: {words}",
        f"  still pending: {s.still_pending}; candle unreadable, asked again later: "
        f"{s.retry}; calls on markets whose candles did not read: {s.unread_calls}",
        f"  answers whose outcome precedes the seal (counted apart by the record): "
        f"{s.sealed_after_outcome}",
    ]
    if apply:
        lines.append(f"  posted {s.posted} of {s.to_post}; landed {s.landed}")
    else:
        lines.append(f"  would post: {s.to_post} (run again with --apply to write)")
    return "\n".join(lines)


def _venue_bars(now_ms: int) -> Callable[[str, int], list]:
    """Public hourly candles from the venue the bot scans, paged forward from
    ``since``, closed bars only. Spot for ``BASE/QUOTE``, swap for a ``:``
    market, as `market_scanner` builds its clients. No key is used."""
    import ccxt

    from bot.core.signal_outcomes import BAR_MS, TIMEFRAME
    from bot.utils.candles import drop_forming_candle

    clients: dict = {}

    def client(swap: bool):
        if swap not in clients:
            opts = {"enableRateLimit": True}
            if swap:
                opts["options"] = {"defaultType": "swap"}
            clients[swap] = ccxt.bitget(opts)
        return clients[swap]

    def fetch(market: str, since_ms: int) -> list:
        ex = client(":" in market)
        bars: list = []
        cursor = since_ms
        for _ in range(MAX_PAGES):
            try:
                page = ex.fetch_ohlcv(market, TIMEFRAME, since=cursor, limit=PAGE_BARS)
            except ccxt.BadSymbol as exc:
                raise MarketUnlisted(market) from exc
            page = [b for b in page or [] if b and int(b[0]) >= cursor]
            if not page:
                break
            bars.extend(page)
            cursor = int(page[-1][0]) + BAR_MS
            if cursor + BAR_MS > now_ms:
                break
        return drop_forming_candle(bars, TIMEFRAME)
    return fetch


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="post the final answers (default: dry run, read-only)")
    ap.add_argument("--base-url", default=None, help="the website (default: site_url())")
    ap.add_argument("--entry-window-s", default=os.getenv("LIMIT_ORDER_EXPIRE_SEC"),
                    help="the entry window, as the bot's walk uses it "
                         "(default: LIMIT_ORDER_EXPIRE_SEC, else four hours)")
    ap.add_argument("--min-age-h", type=float, default=1.0,
                    help="leave out calls younger than this (default 1)")
    args = ap.parse_args(argv)
    if args.apply and os.environ.get("RUNECLAW_SERVER") == "1":
        print("signal outcomes backfill: --apply is operator-only and is refused when "
              "RUNECLAW_SERVER=1; the server does not write the public record on its own.")
        return EXIT_REFUSED

    from bot.core.signal_outcomes import entry_window_s
    from bot.utils import website_sync as ws

    if args.base_url:
        ws.WEBSITE_URL = args.base_url.rstrip("/")
    older_than_s = max(0, int(args.min_age_h * 3600))
    now_ms = int(time.time() * 1000)
    summary = run(
        lambda after: ws.fetch_pending_signals(after, MAX_BATCH, older_than_s),
        _venue_bars(now_ms),
        ws.sync_signals,
        now_ms=now_ms, window_s=entry_window_s(args.entry_window_s), apply=args.apply)
    print(render(summary, apply=args.apply))
    if summary is None:
        return EXIT_UNREAD
    if args.apply and summary.landed < summary.to_post:
        return EXIT_NOT_ALL_LANDED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
