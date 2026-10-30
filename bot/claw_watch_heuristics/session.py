"""Session and API-key anomaly alerts. No network.

A list of events becomes alerts. The sentences name what was read.
They do not name a verdict. ``consider_session`` does not score while
``CLAW_WATCH_HEURISTICS_ENABLED`` is off: ``alerts`` is None, not an
empty list. Off is not the same as no signals.

Nothing here signs, sends, or blocks. The route is the door that asks
the flag; this module scores a payload the caller already has.
"""

from __future__ import annotations

import json
import statistics
import sys
from typing import Optional

from bot.approval_hygiene import looks_like_secret
from bot.claw_watch_heuristics.flag import flag_on

FAST_OUTFLOW_S = 15 * 60
BURST_WINDOW_S = 5 * 60
BURST_CLOSES = 5
BURST_COOLDOWN_S = 30 * 60
SPIKE_MULT = 5.0
DORMANT_S = 90 * 86400

DISCLAIMER = "Public account signal only. This is not a verdict."
EVENTS_SENTENCE = "The events could not be read."
BASELINE_SENTENCE = "The baseline could not be read."

TEXT = {
    "NEW_API_KEY": "A new API key was created on this account.",
    "API_KEY_WITHDRAW_SCOPE": "A new API key can withdraw or transfer funds.",
    "NEW_DEVICE_SESSION": "A session started from a device or network not seen before.",
    "SCRIPT_ORIGIN_CHANGE": (
        "This session loaded a third-party script origin not seen in earlier sessions."
    ),
    "FAST_OUTFLOW_AFTER_NEW_ACCESS": "Funds left within 15 minutes of a new key or new device.",
    "POSITION_CLOSE_BURST": "Many positions were closed in a few minutes.",
    "OUTFLOW_NEW_RECIPIENT": "Funds were sent to an address this account has never paid before.",
    "OUTFLOW_SPIKE": "This outflow is far larger than this account usually moves.",
    "DORMANT_WALLET_ACTIVE": "A wallet with no outflows for 90+ days just moved funds.",
    "COMPOUND_PATTERN": "Several signals together match a session-takeover drain pattern.",
}

LEVEL = {
    "NEW_API_KEY": "info",
    "NEW_DEVICE_SESSION": "info",
    "SCRIPT_ORIGIN_CHANGE": "attention",
    "API_KEY_WITHDRAW_SCOPE": "attention",
    "FAST_OUTFLOW_AFTER_NEW_ACCESS": "attention",
    "POSITION_CLOSE_BURST": "attention",
    "OUTFLOW_NEW_RECIPIENT": "info",
    "OUTFLOW_SPIKE": "attention",
    "DORMANT_WALLET_ACTIVE": "attention",
    "COMPOUND_PATTERN": "urgent",
}

_WITHDRAW_SCOPES = frozenset({"withdraw", "transfer"})
_OUTFLOW = frozenset({"withdrawal", "transfer"})
_ACCESS = frozenset({"NEW_API_KEY", "NEW_DEVICE_SESSION", "SCRIPT_ORIGIN_CHANGE"})
_ACTION = frozenset({"POSITION_CLOSE_BURST", "FAST_OUTFLOW_AFTER_NEW_ACCESS"})


def _alert(code: str, ts: int, **detail: object) -> dict:
    return {"code": code, "level": LEVEL[code], "ts": ts, "detail": detail, "text": TEXT[code]}


def _amount(raw: object) -> Optional[float]:
    """A readable number, or None. ``0`` is a reading. A bool is not."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _ts(raw: object) -> Optional[int]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw)


def _strings(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _devices(raw: object) -> set[tuple]:
    if not isinstance(raw, list):
        return set()
    found = set()
    for row in raw:
        if isinstance(row, (list, tuple)):
            found.add(tuple(row))
    return found


def _walk(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def contains_secret(payload: object) -> bool:
    """True when any string in the payload is a key or a word-list seed."""
    for text in _walk(payload):
        if looks_like_secret(text):
            return True
    return False


def _burst_threshold(baseline: dict) -> Optional[int]:
    """Closes in five minutes that count as a burst for this account.

    An absent record uses the floor. A present value that is not an integer
    is unreadable: the caller does not invent ``0`` and then alert on it.
    """
    if "max_closes_5m" not in baseline:
        recorded: Optional[int] = 0
    else:
        raw = baseline.get("max_closes_5m")
        if isinstance(raw, int) and not isinstance(raw, bool):
            recorded = raw
        else:
            recorded = None
    if recorded is None:
        return None
    return max(BURST_CLOSES, 2 * recorded + 1)


def analyze(events: list[dict], baseline: Optional[dict] = None) -> list[dict]:
    """Alerts for one account. ``events`` is sorted by ``ts`` here.

    A close the caller marks ``initiator`` ``self`` is left out. One
    reduce-only order that fills in pieces counts once, by ``oid``.
    """
    book = baseline if isinstance(baseline, dict) else {}
    devices = _devices(book.get("devices", []))
    origins = set(_strings(book.get("script_origins", [])))
    recipients = {item.lower() for item in _strings(book.get("recipients", []))}
    past_raw = book.get("outflows", [])
    past_out = []
    if isinstance(past_raw, list):
        for item in past_raw:
            amount = _amount(item)
            if amount is not None:
                past_out.append(amount)
    if "last_outflow_ts" not in book or book.get("last_outflow_ts") is None:
        last_out_ts: Optional[int] = None
        last_known = True
    else:
        last_out_ts = _ts(book.get("last_outflow_ts"))
        last_known = last_out_ts is not None
    access_ts: dict[object, int] = {}
    closes: list[tuple[int, object]] = []
    burst_n = _burst_threshold(book)
    last_burst: Optional[int] = None
    alerts: list[dict] = []
    ordered = sorted(events, key=lambda row: row["ts"])
    for event in ordered:
        kind = event["type"]
        ts = event["ts"]
        if not isinstance(ts, int) or isinstance(ts, bool):
            raise TypeError("ts")
        if kind == "session_start":
            dev = (event.get("device"), event.get("ip_asn"))
            if devices and dev not in devices:
                alerts.append(_alert(
                    "NEW_DEVICE_SESSION", ts, session=event.get("session"), device=event.get("device"),
                ))
                session = event.get("session")
                if session is not None:
                    access_ts[session] = ts
            devices.add(dev)
            seen = set(_strings(event.get("script_origins", [])))
            fresh = sorted(seen - origins) if origins else []
            if fresh:
                alerts.append(_alert(
                    "SCRIPT_ORIGIN_CHANGE", ts, session=event.get("session"), origins=fresh,
                ))
                session = event.get("session")
                if session is not None:
                    access_ts[session] = ts
            origins |= seen
        elif kind == "api_key_created":
            key_id = event.get("key_id")
            alerts.append(_alert("NEW_API_KEY", ts, key_id=key_id))
            scopes = event.get("scopes", [])
            if isinstance(scopes, list) and (_WITHDRAW_SCOPES & set(scopes)):
                alerts.append(_alert("API_KEY_WITHDRAW_SCOPE", ts, key_id=key_id, scopes=scopes))
            if key_id is not None:
                access_ts[key_id] = ts
        elif kind == "order":
            closing = event.get("reduce_only") or event.get("side") == "sell_position"
            if closing and event.get("initiator") != "self":
                oid = event.get("oid")
                if oid is None:
                    oid = f"_{ts}_{len(closes)}"
                closes = [row for row in closes if ts - row[0] < BURST_WINDOW_S]
                if oid not in {row[1] for row in closes}:
                    closes.append((ts, oid))
                    cooled = last_burst is None or ts - last_burst >= BURST_COOLDOWN_S
                    if burst_n is not None and len(closes) >= burst_n and cooled:
                        last_burst = ts
                        alerts.append(_alert(
                            "POSITION_CLOSE_BURST", ts,
                            count=len(closes), window_s=BURST_WINDOW_S, threshold=burst_n,
                        ))
        elif kind in _OUTFLOW:
            actor = event.get("session") or event.get("key_id")
            amount = _amount(event.get("amount"))
            dest = event.get("to")
            if amount is None or not isinstance(dest, str) or dest == "":
                continue
            dest_l = dest.lower()
            if actor in access_ts and ts - access_ts[actor] <= FAST_OUTFLOW_S:
                alerts.append(_alert(
                    "FAST_OUTFLOW_AFTER_NEW_ACCESS", ts, actor=actor, seconds=ts - access_ts[actor],
                ))
            if recipients and dest_l not in recipients:
                alerts.append(_alert("OUTFLOW_NEW_RECIPIENT", ts, to=dest_l))
            if len(past_out) >= 3:
                median = statistics.median(past_out)
                if amount > SPIKE_MULT * median:
                    alerts.append(_alert("OUTFLOW_SPIKE", ts, amount=amount, median=median))
            if last_known and last_out_ts is not None and ts - last_out_ts >= DORMANT_S:
                idle = round((ts - last_out_ts) / 86400)
                alerts.append(_alert("DORMANT_WALLET_ACTIVE", ts, idle_days=idle))
            recipients.add(dest_l)
            past_out.append(amount)
            last_out_ts = ts
            last_known = True
    by_hour: dict[int, set[str]] = {}
    for alert in alerts:
        by_hour.setdefault(alert["ts"] // 3600, set()).add(alert["code"])
    for hour, codes in sorted(by_hour.items()):
        access = codes & _ACCESS
        action = codes & _ACTION
        if access and action:
            alerts.append(_alert("COMPOUND_PATTERN", hour * 3600, signals=sorted(access | action)))
    return alerts


def _unread(error_class: str, sentence: Optional[str] = None) -> dict:
    body = {
        "enabled": True,
        "scored": False,
        "alerts": None,
        "disclaimer": DISCLAIMER,
        "error_class": error_class,
    }
    if sentence is not None:
        body["sentence"] = sentence
    return body


def score_payload(payload: object) -> dict:
    """Score one posted body. The route already gated the flag.

    A secret-shaped string is refused. The refusal does not echo it.
    ``alerts`` is None when the body could not be scored. That is not a clear.
    """
    if not isinstance(payload, dict):
        return _unread("Payload", EVENTS_SENTENCE)
    if contains_secret(payload):
        return _unread("Refused")
    events = payload.get("events")
    baseline = payload.get("baseline")
    if not isinstance(events, list):
        return _unread("Payload", EVENTS_SENTENCE)
    if baseline is not None and not isinstance(baseline, dict):
        return _unread("Payload", BASELINE_SENTENCE)
    try:
        alerts = analyze(events, baseline)
    except Exception as exc:
        return _unread(type(exc).__name__)
    return {
        "enabled": True,
        "scored": True,
        "alerts": alerts,
        "disclaimer": DISCLAIMER,
    }


def consider_session(events: object, baseline: object = None, env: Optional[dict] = None) -> dict:
    """The in-process door. While the flag is off this does not score."""
    if not flag_on(env):
        return {
            "enabled": False,
            "scored": False,
            "alerts": None,
            "disclaimer": DISCLAIMER,
        }
    body: dict = {"events": events}
    if baseline is not None:
        body["baseline"] = baseline
    return score_payload(body)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = None
    json.dump(score_payload(payload), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
