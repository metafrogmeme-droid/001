"""Dated readings. They are not substituted for a missing live input."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from bot.t2c.numbers import as_decimal, json_num
from bot.t2c.price import carry_term

ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "t2c"
SIZE_BUCKETS = (Decimal(1000), Decimal(10000), Decimal(100000))
_MATCH_SECONDS = Decimal(5)


def _parse_time(value: object) -> int | None:
    if not isinstance(value, str) or value.strip() == "":
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


def request_time_seconds(payload: object, fetched_at: object) -> Decimal | None:
    """Seconds until finalization.

    ``finalizationIn`` is matched to ``finalizationAt - fetched_at``. The
    scale that agrees within five seconds is the one used. A scale that
    does not agree is not guessed.
    """
    if not isinstance(payload, dict) or payload.get("status") != "calculated":
        return None
    info = payload.get("requestInfo")
    if not isinstance(info, dict):
        return None
    raw = info.get("finalizationIn")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    stamp = _parse_time(info.get("finalizationAt"))
    fetched = _parse_time(fetched_at)
    if stamp is None or fetched is None:
        return None
    delta = Decimal(stamp - fetched)
    as_milliseconds = Decimal(raw) / Decimal(1000)
    as_seconds = Decimal(raw)
    if abs(as_milliseconds - delta) <= _MATCH_SECONDS and abs(as_milliseconds - delta) <= abs(as_seconds - delta):
        return as_milliseconds
    if abs(as_seconds - delta) <= _MATCH_SECONDS:
        return as_seconds
    return None


def recorded_request_times() -> dict:
    path = ROOT / "wq_api_2026-10-23.json"
    empty: dict[str, object] = {"source": "absent", "fetched_at": None, "buckets": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(document, dict):
        return empty
    fetched = document.get("fetched_at")
    payloads = document.get("payloads")
    if not isinstance(payloads, dict):
        return empty
    buckets = []
    for size in SIZE_BUCKETS:
        payload = payloads.get(str(int(size)))
        seconds = request_time_seconds(payload, fetched)
        buckets.append({
            "size_eth": json_num(size),
            "t_secs": json_num(seconds),
            "source": "recorded_snapshot" if seconds is not None else "unreadable",
        })
    return {
        "source": "recorded_snapshot",
        "fetched_at": fetched if isinstance(fetched, str) else None,
        "queried_at": "2026-10-23",
        "endpoint": "wq-api.lido.fi/v2/request-time/calculate",
        "buckets": buckets,
    }


def recorded_carry() -> dict:
    """Today's borrow against the stETH APR. A snapshot, not a live feed."""
    path = ROOT / "carry_2026-10-23.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": "absent", "carry": None}
    if not isinstance(document, dict):
        return {"source": "absent", "carry": None}
    stake_raw = document.get("stake")
    borrow_raw = document.get("borrow")
    stake = stake_raw if isinstance(stake_raw, dict) else {}
    borrow = borrow_raw if isinstance(borrow_raw, dict) else {}
    percent = as_decimal(stake.get("sma_apr_percent"))
    ray = as_decimal(borrow.get("ray"))
    apr = None if percent is None else percent / Decimal(100)
    rate = None if ray is None else ray / (Decimal(10) ** 27)
    term = carry_term(rate, apr)
    return {
        "source": "recorded_snapshot",
        "queried_at": "2026-10-23",
        "r_borrow": json_num(rate),
        "y_stake": json_num(apr),
        "y_unit": "smaApr percent / 100",
        "carry": json_num(term["carry"]),
        "carry_source": term["source"],
        "stake_time_unix": stake.get("time_unix"),
    }
