"""
RUNECLAW -- Website sync module.
Pushes portfolio state and trade events to the website API
so the dashboard shows real, live data.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _t
import uuid
import urllib.request
import urllib.error
from typing import Optional
from bot.utils.site_url import site_url

log = logging.getLogger(__name__)

WEBSITE_URL = site_url()
SYNC_SECRET = os.getenv("BOT_SYNC_SECRET", "")

if not SYNC_SECRET:
    log.warning("BOT_SYNC_SECRET not set — website sync will be rejected by the server.")


def _attr(obj, key, default=None):
    """Safely get attribute from Pydantic model or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    val = getattr(obj, key, default)
    return val if val is not None else default


#: Status codes worth trying again. A 5xx and a dropped connection mean "this
#: server could not answer right now"; a 4xx means "this request will never be
#: accepted", and retrying it is noise that hides the real fault. The live 503
#: on 2026-07-31 was BOT_SYNC_SECRET being unset — a 401-shaped problem
#: wearing a 5xx code — so the log line below has to name the code either way.
_RETRY_STATUS = frozenset({500, 502, 503, 504, 408, 429})

#: Backoff between attempts, seconds. Short: this runs on the trading path and
#: a sync is never worth delaying an order.
_RETRY_BACKOFF = (0.5, 2.0, 5.0)

#: Backoff for the pushes that run on their OWN THREAD, where the reason for
#: the short profile above does not apply and its consequence is severe.
#:
#: The website is an ephemeral instance that is torn down after a short idle
#: and COLD-STARTS on the next request — `/api/version` reported `uptime_s` in
#: the low hundreds on a site nobody had restarted. A cold start answers the
#: request that triggered it with a 503, quickly, and serves normally about
#: half a minute later.
#:
#: Against that, `_RETRY_BACKOFF` with `retries=2` spends its whole budget in
#: 2.5 SECONDS — three fast 503s inside the first three seconds of a thirty
#: second warm-up — and gives up. The next scan cycle finds the instance cold
#: again, because the only traffic that would have kept it warm is the traffic
#: that just gave up. `Synced` sat at 0 for a day and a half with a correct
#: secret, a correct URL and a healthy site, and the retry did not read as the
#: cause because there WAS a retry: it was simply an order of magnitude
#: shorter than the failure it was retrying through.
#:
#: A retry budget has to be scaled to the outage it exists to survive, not to
#: the machine it runs on.
_COLD_START_BACKOFF = (5.0, 15.0, 30.0)

#: Per-attempt socket timeout for those same pushes. A cold start that HANGS
#: rather than 503-ing is the other half of the same failure, and 15s cuts off
#: a request that would have been answered at 25.
_COLD_START_TIMEOUT = 30


def _post(path: str, data: dict, *, retries: int = 0,
          backoff: tuple = _RETRY_BACKOFF,
          timeout: int = 15) -> Optional[dict]:
    """POST JSON to the website API. Returns response dict or None on error.

    ``retries`` DEFAULTS TO ZERO, and that default is a safety property rather
    than a conservative guess. Retrying a POST is only sound when the endpoint
    can recognise the second delivery of the same event. /api/bot/sync/trade-event
    appends with a bare INSERT: a retry of an `open` whose response was merely
    lost inserts a phantom second trade, and a retry of a `close` deletes
    another OPEN row and fabricates a closed trade with it. Losing an event is
    bad; inventing one is worse, and it is the failure this repo's §4 exists to
    prevent.

    So callers opt in, and only after making their payload replayable — either
    because the endpoint replaces state wholesale (portfolio, scan, signals,
    tiers: the next push heals the gap anyway, and a retry just heals it
    sooner) or because it carries an event_id the server dedupes on.
    """
    url = f"{WEBSITE_URL}{path}"
    payload = json.dumps(data, default=str).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RUNECLAW-Bot/1.0",
            # Read the env per request (not only import time) so a vault
            # restore or admin repair takes effect without a restart.
            "X-Bot-Secret": os.getenv("BOT_SYNC_SECRET", "") or SYNC_SECRET,
        },
        method="POST",
    )
    attempts = max(0, int(retries)) + 1
    # Elapsed time is logged on give-up. Without it "gave up after 3 attempts"
    # is the same sentence whether the budget was 2.5 seconds or 90, and that
    # difference was the whole bug the cold-start profile exists to fix.
    _began = _t.monotonic()
    for attempt in range(attempts):
        _last = attempt == attempts - 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = json.loads(resp.read().decode())
                # isinstance narrows the json.loads Any for mypy (this module
                # is now transitively type-checked via live_executor ->
                # agent_feed) and guards against a non-object JSON body from a
                # proxy/CDN.
                return parsed if isinstance(parsed, dict) else None
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
            except Exception:
                pass
            retryable = e.code in _RETRY_STATUS
            if retryable and not _last:
                log.warning("Sync HTTP error %s (attempt %d/%d, retrying): %s",
                            e.code, attempt + 1, attempts, body)
            else:
                # Say WHY this is the end of the road: a 4xx that was never
                # going to succeed reads very differently from a 5xx that ran
                # out of attempts, and the old single line said neither.
                log.error("Sync HTTP error %s (%s): %s", e.code,
                          "gave up after %d attempts over %.1fs"
                          % (attempts, _t.monotonic() - _began) if retryable
                          else "not retryable", body)
                return None
        except Exception as exc:
            if _last:
                log.error("Sync error (%s): %s",
                          "gave up after %d attempts over %.1fs"
                          % (attempts, _t.monotonic() - _began) if retries
                          else "no retry configured", exc)
                return None
            log.warning("Sync error (attempt %d/%d, retrying): %s",
                        attempt + 1, attempts, exc)
        _t.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None


def unlink_telegram_on_website(user_id: int, chat_id: str) -> Optional[bool]:
    """Clear the website's telegram_linked flag. True / False / None.

    TRI-STATE ON PURPOSE, and the reason is the whole point of this function:

      True   the website confirmed it cleared the link
      False  the website answered, and refused (no such user, chat mismatch)
      None   the website could not be reached, so NOTHING IS KNOWN

    /unlink used to delete the bot's local row and report "Unlinked from
    {email}. Your data is preserved." — while the website still had
    telegram_linked = TRUE and kept routing exchange-credential submissions and
    live-trading controls to that chat. Collapsing an unreachable website to
    False (or to True) reinstates that: an unreadable result is not a result,
    and the person deserves to be told which half actually happened.
    """
    result = _post("/api/bot/sync/telegram-unlink",
                   {"user_id": int(user_id), "chat_id": str(chat_id)},
                   retries=2)   # idempotent: clearing a flag replays safely
    if result is None:
        return None
    return bool(result.get("ok") and result.get("unlinked"))


def sync_portfolio(user_id: int, equity: float,
                   positions: list, closed_trades: list) -> bool:
    """Full sync: replace all website data for a user with current bot state."""
    open_list = []
    for p in positions:
        open_list.append({
            "symbol": _attr(p, "asset", ""),
            "direction": str(_attr(p, "direction", "")).split(".")[-1],
            "entry_price": float(_attr(p, "entry_price", 0)),
            "size_usd": float(_attr(p, "quantity", 0)) * float(_attr(p, "entry_price", 0)),
            "fees": float(_attr(p, "commission", 0)),
            "pattern": _attr(p, "pattern"),
            "stop_loss": float(_attr(p, "stop_loss", 0)),
            "take_profit": float(_attr(p, "take_profit", 0)),
            "opened_at": str(_attr(p, "opened_at", "")),
            "venue": str(_attr(p, "venue", "bitget") or "bitget"),
        })

    # Send only what counts as a trade. The website's schema stores neither
    # close_reason nor trade_id, so it CANNOT apply this rule itself — which
    # is exactly why it must be applied here. Filtering at the source makes
    # the website's plain SUM agree with Telegram by construction, instead of
    # the two reporting different numbers under the same label.
    from bot.utils.trade_filter import countable as _countable
    closed_list = []
    for t in _countable(closed_trades):
        closed_list.append({
            "symbol": _attr(t, "asset", ""),
            "direction": str(_attr(t, "direction", "")).split(".")[-1],
            "entry_price": float(_attr(t, "entry_price", 0)),
            "exit_price": float(_attr(t, "exit_price", 0)),
            "size_usd": float(_attr(t, "quantity", 0)) * float(_attr(t, "entry_price", 0)),
            "pnl": float(_attr(t, "pnl", 0)),
            "fees": float(_attr(t, "commission", 0)),
            "pattern": _attr(t, "pattern"),
            "opened_at": str(_attr(t, "opened_at", "")),
            "closed_at": str(_attr(t, "closed_at", "")),
            # Phase 0 taught the bot's records where a trade happened; without
            # this line that attribution died at the wire and the dashboard —
            # the surface anyone actually looks at — could never show it.
            # `_attr` defaults to the venue every existing trade IS.
            "venue": str(_attr(t, "venue", "bitget") or "bitget"),
        })

    # Replace-all: this endpoint overwrites the user's website state with the
    # snapshot in this payload, so a second delivery is a no-op rather than a
    # duplicate. Retrying only closes the gap sooner than the next scheduled
    # push would.
    result = _post("/api/bot/sync", {
        "user_id": user_id,
        "equity": equity,
        "positions": open_list,
        "closed_trades": closed_list,
    }, retries=2)

    if result and result.get("ok"):
        log.info(f"Synced to website: user={user_id} equity={equity} "
                 f"open={len(open_list)} closed={len(closed_list)}")
        return True
    return False


def sync_trade_event(user_id: int, event: str, trade, equity: float) -> bool:
    """Push a single trade event (open/close) to the website."""
    trade_data = {
        "symbol": _attr(trade, "asset", ""),
        "direction": str(_attr(trade, "direction", "")).split(".")[-1],
        "entry_price": float(_attr(trade, "entry_price", 0)),
        "size_usd": float(_attr(trade, "quantity", 0)) * float(_attr(trade, "entry_price", 0)),
        "fees": float(_attr(trade, "commission", 0)),
        "pattern": _attr(trade, "pattern"),
        "stop_loss": float(_attr(trade, "stop_loss", 0)),
        "take_profit": float(_attr(trade, "take_profit", 0)),
    }

    if event == "close":
        trade_data["exit_price"] = float(_attr(trade, "exit_price", 0))
        trade_data["pnl"] = float(_attr(trade, "pnl", 0))
        trade_data["opened_at"] = str(_attr(trade, "opened_at", ""))
        trade_data["closed_at"] = str(_attr(trade, "closed_at", ""))

    # One id per LOGICAL event, minted here rather than derived from the
    # trade's fields. A hash of the fields would be stable across retries but
    # would also collide with a genuinely separate, identical event — two opens
    # of the same symbol at the same price would look like one. A uuid minted
    # once and reused by every retry of THIS call is stable where it must be
    # and unique where it must be. Without it a retry of a lost `open` inserts
    # a phantom trade, which is why this sync had no retry at all until now.
    event_id = uuid.uuid4().hex
    result = _post("/api/bot/sync/trade-event", {
        "user_id": user_id,
        "event": event,
        "event_id": event_id,
        "trade": trade_data,
        "equity": equity,
    }, retries=2)

    if result and result.get("ok"):
        log.info(f"Trade event synced: user={user_id} event={event} "
                 f"symbol={trade_data['symbol']}")
        return True
    return False


def sync_in_background(user_id: int, equity: float,
                       positions: list, closed_trades: list) -> None:
    """Non-blocking sync: runs in a background thread."""
    t = threading.Thread(
        target=sync_portfolio,
        args=(user_id, equity, positions, closed_trades),
        daemon=True,
    )
    t.start()


def sync_event_in_background(user_id: int, event: str, trade, equity: float) -> None:
    """Non-blocking trade event sync."""
    t = threading.Thread(
        target=sync_trade_event,
        args=(user_id, event, trade, equity),
        daemon=True,
    )
    t.start()


def sync_scan_data(scan_payload: dict) -> bool:
    """Push scan results to the website dashboard.

    scan_payload should match the dashboard's expected schema:
    {
        regime: { label, score, gate, long_short, funding },
        circuit_breaker: { rules: [{ label, active }] },
        symbols: { 'ADAUSDT': { book_ratio, book_side, status, status_label } },
        entry_cards: [{ symbol, direction, score, entry, stop_loss, tp1, tp2,
                        margin, rr, book_ratio, trigger, thesis }],
        key_call: "HTML narrative string",
        timestamp: "2026-06-18 11:28 CST"
    }
    """
    result = _post("/api/bot/sync/scan", scan_payload, retries=3,
                   backoff=_COLD_START_BACKOFF, timeout=_COLD_START_TIMEOUT)
    if result and result.get("ok"):
        log.info("Scan data synced to website dashboard")
        return True
    log.warning("Scan data sync failed")
    return False


def sync_scan_in_background(scan_payload: dict) -> None:
    """Non-blocking scan data sync."""
    t = threading.Thread(
        target=sync_scan_data,
        args=(scan_payload,),
        daemon=True,
    )
    t.start()


def build_signal_payload(signal_key: str, idea, *, score: float = 0.0,
                         regime: str = "", status: str = "NEW",
                         pnl: Optional[float] = None,
                         created_at: str = "", resolved_at: str = "") -> dict:
    """Shape one signal-stream row from a TradeIdea-like object (dict or model).

    ``signal_key`` is a STABLE per-signal id so re-syncing the same signal updates
    its outcome (status/pnl) instead of duplicating. Every generated signal —
    taken or not — belongs in the stream; the dashboard joins a user's own trades
    to it. Pure shaping (no I/O); returns a JSON-ready dict.
    """
    direction = str(_attr(idea, "direction", "")).split(".")[-1]
    entry = float(_attr(idea, "entry_price", 0) or 0)
    sl = float(_attr(idea, "stop_loss", 0) or 0)
    tp = float(_attr(idea, "take_profit", 0) or 0)
    rr = _attr(idea, "risk_reward_ratio", None)
    if rr is None:
        risk = abs(entry - sl)
        rr = (abs(tp - entry) / risk) if risk > 0 else 0.0
    return {
        "signal_key": str(signal_key),
        "symbol": _attr(idea, "asset", "") or _attr(idea, "symbol", ""),
        "direction": direction,
        "confidence": float(_attr(idea, "confidence", 0) or 0),
        "score": float(score or 0),
        "pattern": _attr(idea, "pattern"),
        "regime": regime or "",
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "rr": float(rr or 0),
        "thesis": _attr(idea, "reasoning", "") or _attr(idea, "thesis", ""),
        "status": status,
        "pnl": pnl,
        "created_at": created_at or "",
        "resolved_at": resolved_at or "",
    }


def sync_signals(signals: list[dict]) -> bool:
    """Push a batch of signal-stream rows to the website (UPSERT by signal_key)."""
    if not signals:
        return True
    result = _post("/api/bot/sync/signals", {"signals": signals}, retries=3,
                   backoff=_COLD_START_BACKOFF, timeout=_COLD_START_TIMEOUT)
    if result and result.get("ok"):
        log.info(f"Synced {result.get('upserted', 0)} signal(s) to website")
        return True
    log.warning("Signal stream sync failed")
    return False


def sync_signals_in_background(signals: list[dict]) -> None:
    """Non-blocking signal-stream sync."""
    if not signals:
        return
    t = threading.Thread(target=sync_signals, args=(list(signals),), daemon=True)
    t.start()


def sync_flight_records(records: list[dict], chain: Optional[dict] = None,
                        policy: Optional[dict] = None,
                        guardian_status: Optional[dict] = None,
                        incidents: Optional[list[dict]] = None) -> bool:
    """Push Guardian Flight Recorder records + engine-verified chain status +
    the active Intent-policy summary + the whole-layer Guardian posture.

    Telemetry only (the decision ledger and the enforceable policy both live
    bot-side): a failed POST just returns False. ``chain`` carries the
    authoritative ``verify()`` result — {ok, length, tip_hash, problems} — so
    the website can show an engine-verified integrity badge without re-hashing
    the whole file. ``policy`` is a read-only summary of the compiled intent
    policy (id, mode, rules, hash) for display. ``guardian_status`` is the
    read-only Guardian console snapshot (chain health, per-module risk + armed
    flags, overall posture) so the web can mirror the Telegram /guardian view.
    """
    if not records and not chain and not policy and not guardian_status and not incidents:
        return True
    result = _post("/api/bot/sync/flight", {
        "records": list(records or []),
        "chain": chain or {},
        "policy": policy or None,
        "guardian_status": guardian_status or None,
        "incidents": list(incidents or []),
    })
    if result and result.get("ok"):
        log.info(f"Synced {result.get('stored', len(records or []))} flight record(s)")
        return True
    log.debug("Flight-record sync failed")
    return False


def sync_flight_records_in_background(records: list[dict], chain: Optional[dict] = None,
                                     policy: Optional[dict] = None,
                                     guardian_status: Optional[dict] = None,
                                     incidents: Optional[list[dict]] = None) -> None:
    """Non-blocking flight-record sync (fire-and-forget)."""
    t = threading.Thread(
        target=sync_flight_records,
        args=(list(records or []), chain, policy, guardian_status, list(incidents or [])),
        daemon=True)
    t.start()


def sync_reports(payload: dict) -> bool:
    """Push the web-reports payload (funding/arb/parity/yield sections built
    by bot.core.web_reports) to the website's reports cache."""
    if not payload:
        return True
    result = _post("/api/bot/sync/reports", payload)
    if result and result.get("ok"):
        log.info("Web reports synced")
        return True
    log.debug("Web reports sync failed")
    return False


def sync_agent_events(events: list[dict]) -> bool:
    """Push a batch of public agent-feed (mind-stream) events to the website.

    Events are shaped by bot.core.agent_feed (type-whitelisted, truncated).
    Best-effort telemetry: a failed POST just returns False — the caller
    (AgentFeed.flush_once) drops the batch rather than retrying.
    """
    if not events:
        return True
    result = _post("/api/bot/sync/events", {"events": list(events)})
    if result and result.get("ok"):
        return True
    log.debug("Agent feed sync failed")
    return False


# ── Membership tier sync (bot is the tier authority) ─────────────────

_last_tiers_sent: str = ""   # hash of the last successfully-pushed map


def sync_tiers(tier_map: dict) -> bool:
    """Mirror {telegram_id: tier} to the website so users.plan matches the
    bot's tier authority. Skips the POST when nothing changed since the
    last successful push (tiers change rarely; no need to spam)."""
    global _last_tiers_sent
    if not tier_map:
        return True
    import hashlib
    import json as _json
    digest = hashlib.sha256(
        _json.dumps(tier_map, sort_keys=True).encode()).hexdigest()
    if digest == _last_tiers_sent:
        return True
    # UPDATE-by-key, so replaying it lands the same rows on the same users.
    result = _post("/api/bot/sync/tiers", {
        "tiers": [{"telegram_id": k, "tier": v}
                  for k, v in tier_map.items()],
    }, retries=3, backoff=_COLD_START_BACKOFF, timeout=_COLD_START_TIMEOUT)
    if result and result.get("ok"):
        _last_tiers_sent = digest
        log.info(f"Synced {result.get('updated', 0)} user tier(s) to website")
        return True
    log.warning("Tier sync failed")
    return False


def sync_tiers_in_background(tier_map: dict) -> None:
    """Non-blocking tier sync."""
    if not tier_map:
        return
    t = threading.Thread(target=sync_tiers, args=(dict(tier_map),), daemon=True)
    t.start()
