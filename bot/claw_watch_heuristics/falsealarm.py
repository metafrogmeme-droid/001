"""False-alarm check against Hyperliquid testnet activity and our own bot.

The live read is public and read-only, and only the testnet info host.
It does not run while ``CLAW_WATCH_HEURISTICS_ENABLED`` is off. The
traces in ``own_bot_traces`` need no network: they are the patterns
this repo already configures (a partial take-profit ladder, five stops,
a tagged flatten, an untagged flatten, an API-wallet rotation).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Optional

from bot.claw_watch_heuristics.flag import flag_on
from bot.claw_watch_heuristics.hl_events import (
    baseline_from_events,
    fetch,
    info,
    to_events,
)
from bot.claw_watch_heuristics.session import analyze

DAY = 86400


def own_bot_traces(t0: int) -> dict[str, list[dict]]:
    """Patterns from this repo, as session events.

    ``MAX_OPEN_POSITIONS`` is 5. A kill-switch flatten is
    ``flatten_all_positions(reason="admin_closeall")``. Tagged rows set
    ``initiator`` to ``self``. The untagged copy is the same flatten
    when that mark is not wired through.
    """
    traces: dict[str, list[dict]] = {}
    traces["tp_ladder_partial_fills"] = [
        {
            "type": "order", "ts": t0 + i * 20 + k, "reduce_only": True,
            "oid": f"tp{i}", "notional": 10,
        }
        for i in range(3) for k in range(4)
    ]
    traces["five_stops_on_a_wick"] = [
        {"type": "order", "ts": t0 + i * 45, "reduce_only": True, "oid": f"sl{i}", "notional": 10}
        for i in range(5)
    ]
    tagged = [
        {
            "type": "order", "ts": t0 + i, "reduce_only": True, "oid": f"ks{i}",
            "notional": 10, "initiator": "self",
        }
        for i in range(5)
    ]
    traces["kill_switch_flatten_tagged"] = tagged
    traces["kill_switch_flatten_untagged"] = [{**event, "initiator": None} for event in tagged]
    traces["api_wallet_rotation"] = [
        {"type": "api_key_created", "ts": t0, "key_id": "0xnewagent", "scopes": ["trade"]},
    ] + [
        {"type": "order", "ts": t0 + 300 + i * 60, "reduce_only": True, "oid": f"r{i}", "notional": 10}
        for i in range(2)
    ]
    return traces


def _hot(events: list[dict], baseline: Optional[dict] = None) -> list[str]:
    return [
        alert["code"] for alert in analyze(events, baseline or {})
        if alert["level"] in ("attention", "urgent")
    ]


def _off() -> dict:
    return {
        "enabled": False,
        "sampled": None,
        "accounts": None,
        "codes": None,
        "flagged_accounts": None,
        "own_bot": None,
        "unread_coins": None,
    }


def run(coins: list[str], days: int, max_users: int, out_path: Optional[str] = None, *,
        info_fn=None, env: Optional[dict] = None, now_ms: Optional[int] = None,
        sleep_s: float = 0.3) -> dict:
    """Score the last day of each sampled testnet account against the days before it.

    ``info_fn`` replaces the POST. The default posts only to the testnet
    info host. While the flag is off the function returns without calling
    it, and the counts are None.
    """
    if not flag_on(env):
        return _off()
    call = info_fn or info
    users: list[str] = []
    unread: list[dict] = []
    for coin in coins:
        try:
            trades = call({"type": "recentTrades", "coin": coin})
            if not isinstance(trades, list):
                unread.append({"coin": coin, "error": "unreadable"})
            else:
                for trade in trades:
                    if not isinstance(trade, dict):
                        continue
                    named = trade.get("users")
                    if not isinstance(named, list):
                        continue
                    for account in named:
                        if isinstance(account, str) and account.lower() not in users:
                            users.append(account.lower())
        except Exception as exc:
            unread.append({"coin": coin, "error": type(exc).__name__})
        if sleep_s:
            time.sleep(sleep_s)
    users = users[:max_users]
    clock = now_ms if now_ms is not None else int(time.time() * 1000)
    start = clock - days * DAY * 1000
    split = clock - DAY * 1000
    result: dict = {
        "enabled": True,
        "accounts": [],
        "codes": Counter(),
        "flagged_accounts": 0,
        "unread_coins": unread,
    }
    for account in users:
        try:
            raw = fetch(account, start, clock, fetch_fn=call, env=env)
            if sleep_s:
                time.sleep(sleep_s)
            if raw.get("status") != "read":
                result["accounts"].append({"user": account, "error": raw.get("status") or "unreadable"})
                continue
            hist = {
                **raw,
                "fills": [row for row in raw["fills"] if row["time"] < split],
                "ledger": [row for row in raw["ledger"] if row["time"] < split],
            }
            last = {
                **raw,
                "fills": [row for row in raw["fills"] if row["time"] >= split],
                "ledger": [row for row in raw["ledger"] if row["time"] >= split],
            }
            known = {
                agent["address"] for agent in raw["agents"]
                if isinstance(agent, dict) and isinstance(agent.get("address"), str)
            }
            base = baseline_from_events(to_events(hist, known_agents=known))
            events = to_events(last, known_agents=known)
            alerts = analyze(events, base)
            hot = [alert["code"] for alert in alerts if alert["level"] in ("attention", "urgent")]
            result["codes"].update(hot)
            if hot:
                result["flagged_accounts"] += 1
            outflows = [event for event in events if event["type"] != "order"]
            result["accounts"].append({
                "user": account,
                "fills_last_day": len(last["fills"]),
                "outflows_last_day": len(outflows),
                "alerts": hot,
            })
        except Exception as exc:
            result["accounts"].append({"user": account, "error": type(exc).__name__})
    own: dict[str, list[str]] = {}
    for name, events in own_bot_traces(clock // 1000).items():
        own[name] = _hot(events)
    result["own_bot"] = own
    result["sampled"] = len(users)
    result["codes"] = dict(result["codes"])
    if isinstance(out_path, str) and out_path:
        folder = os.path.dirname(out_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return result


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Testnet false-alarm read. The flag must be on.")
    parser.add_argument("--coins", nargs="+", default=["BTC", "ETH", "SOL", "HYPE"])
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--max-users", type=int, default=25)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if not flag_on():
        json.dump(_off(), sys.stdout)
        sys.stdout.write("\n")
        return
    result = run(args.coins, args.days, args.max_users, args.out or None)
    shown = {
        key: result[key]
        for key in ("sampled", "flagged_accounts", "codes", "own_bot", "enabled")
    }
    json.dump(shown, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
