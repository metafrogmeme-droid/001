"""Hourly read of Hyperliquid's borrow-lend reserves.

The transport is injected. Tests never open a socket. The info API is a
POST of ``{"type": "allBorrowLendReserveStates"}``. A precompile read, when
the operator points one at mainnet, is ``eth_call`` and nothing else.

Testnet readings are marked synthetic. They decode. They do not calibrate.
A failed read is unreadable, not a book of zeros. A missing history file is
the same: there is no empty book to display.
"""

from __future__ import annotations

import json
import os
import urllib.request
from decimal import ROUND_DOWN, Decimal
from typing import Callable, Optional

from bot.hrc.index import PRECOMPILE, accept_sample, decode_precompile
from bot.hrc.model import (
    RAW_PER_TOKEN,
    borrow_apy,
    choose_settlement,
    quoted_rate_matches,
    utilization_of,
)
from bot.utils.atomic_write import atomic_write_json

INFO_URL = {
    "mainnet": "https://api.hyperliquid.xyz/info",
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
}
_INFO_BODY = {"type": "allBorrowLendReserveStates"}


def _dec(value) -> Optional[Decimal]:
    """A decimal string or an int. A float is unreadable, not a guess."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def _as_raw(amount: Decimal, unit: str) -> Optional[int]:
    """Integer raw token units. Human amounts are floored at 1e8.

    A unit we do not know is None. Mixing the two scales in one ratio is
    how a withdrawal replay lies, so the caller passes one unit.
    """
    if unit == "raw":
        if amount != amount.to_integral_value():
            return None
        return int(amount)
    if unit == "human":
        scaled = (amount * Decimal(RAW_PER_TOKEN)).to_integral_value(rounding=ROUND_DOWN)
        return int(scaled)
    return None


def _find_usdc(payload) -> Optional[dict]:
    """The token-0 object, or None when the payload has no such object.

    None means absent. A payload that is not a list or a dict is unreadable
    and the caller says so; this function only searches.
    """
    found: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if "totalSupplied" in node and "totalBorrowed" in node:
                token = node.get("token", node.get("index"))
                if token == 0 or token == "0":
                    found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)
    if not found:
        return None
    return found[0]


def _info_side(row: dict) -> dict:
    supplied = _dec(row.get("totalSupplied"))
    borrowed = _dec(row.get("totalBorrowed"))
    quoted = _dec(row.get("borrowYearlyRate"))
    oracle = _dec(row.get("oraclePx"))
    if supplied is None or borrowed is None or quoted is None:
        return {"readable": False, "reason": "unreadable"}
    if supplied <= 0 or borrowed < 0:
        return {"readable": False, "reason": "unreadable"}
    raw_s = _as_raw(supplied, "human")
    raw_b = _as_raw(borrowed, "human")
    if raw_s is None or raw_b is None or raw_s <= 0:
        return {"readable": False, "reason": "unreadable"}
    util = utilization_of(raw_s, raw_b)
    if not quoted_rate_matches(util, quoted):
        return {"readable": False, "reason": "quote_rejected"}
    rate = borrow_apy(util)
    if rate is None:
        return {"readable": False, "reason": "unreadable"}
    return {
        "readable": True,
        "supplied": str(supplied),
        "borrowed": str(borrowed),
        "raw_supplied": raw_s,
        "raw_borrowed": raw_b,
        "quoted": str(quoted),
        "formula_rate": rate,
        "oracle": None if oracle is None else str(oracle),
        "unit": "human",
    }


def collect_hour(transport: Callable, *, network: str, hour: int) -> dict:
    """One info-API reading. No signer, no key, no transaction.

    ``network`` is ``mainnet`` or ``testnet``. Testnet is synthetic even
    when the payload looks ordinary. A transport that raises is unreadable,
    and the reason is the exception class, never its message.
    """
    if network not in INFO_URL:
        return {"readable": False, "reason": "unreadable", "network": None, "synthetic": None}
    if type(hour) is not int or hour < 0:
        return {"readable": False, "reason": "unreadable", "network": network,
                "synthetic": network == "testnet"}
    synthetic = network == "testnet"
    try:
        payload = transport(INFO_URL[network], dict(_INFO_BODY))
    except Exception as exc:
        return {
            "readable": False,
            "reason": type(exc).__name__,
            "network": network,
            "synthetic": synthetic,
            "hour": hour,
        }
    if not isinstance(payload, (dict, list)):
        return {"readable": False, "reason": "unreadable", "network": network,
                "synthetic": synthetic, "hour": hour}
    row = _find_usdc(payload)
    if row is None:
        return {"readable": False, "reason": "absent", "network": network,
                "synthetic": synthetic, "hour": hour}
    side = _info_side(row)
    side["network"] = network
    side["synthetic"] = synthetic
    side["hour"] = hour
    # An info-API read is not a 2-of-3 signature. It stays info_api until
    # two registered signers have produced the digest.
    side["source"] = "info_api" if side.get("readable") else "none"
    return side


def combine_readings(info: dict, precompile: Optional[dict]) -> dict:
    """The rate the hour would settle at, from the two reads.

    The precompile rate and the info-API rate are the curve on each read's
    own totals. When both exist and they differ by more than the truncation
    tolerance, the lower rate is the one kept, and the totals shown are the
    totals that produced it. When neither exists the result is unreadable,
    not a 0% rate.
    """
    if not isinstance(info, dict):
        info = {"readable": False}
    info_rate = None
    if info.get("readable") is True and isinstance(info.get("formula_rate"), Decimal):
        info_rate = info["formula_rate"]
    pre_rate = None
    pre_view = precompile if isinstance(precompile, dict) else None
    pre_ok = (pre_view is not None and pre_view.get("readable") is True
              and isinstance(pre_view.get("formula_rate"), Decimal))
    if pre_ok and pre_view is not None:
        pre_rate = pre_view["formula_rate"]
    choice = choose_settlement(pre_rate, info_rate)
    network = info.get("network") if isinstance(info, dict) else None
    synthetic = info.get("synthetic") if isinstance(info, dict) else None
    hour = info.get("hour") if isinstance(info, dict) else None
    if choice["source"] == "none" or choice["rate"] is None:
        return {
            "readable": False,
            "reason": "unreadable",
            "source": "none",
            "network": network,
            "synthetic": synthetic,
            "hour": hour,
        }
    use_pre = choice["source"] == "precompile" or (
        choice["source"] == "lower_of" and pre_rate is not None and choice["rate"] == pre_rate
    )
    if use_pre and pre_view is not None:
        base = {
            "readable": True,
            "supplied": str(pre_view["supplied"]),
            "borrowed": str(pre_view["borrowed"]),
            "unit": "raw",
            "formula_rate": choice["rate"],
            "oracle": str(pre_view["oracle_px"]) if type(pre_view.get("oracle_px")) is int else None,
            "network": "mainnet",
            "synthetic": False,
            "hour": hour,
            "source": choice["source"],
        }
        return base
    base = dict(info)
    base["formula_rate"] = choice["rate"]
    # choose_settlement calls the info side "signed_feed". This process has
    # not collected two signatures, so the label stays the read it actually is
    # unless the two reads disagreed and the info rate was the lower one.
    if choice["source"] == "lower_of":
        base["source"] = "lower_of"
    else:
        base["source"] = "info_api"
    return base


def eth_call_precompile(url: str, token: int, transport: Callable) -> dict:
    """``eth_call`` of precompile 0x812. Never a send.

    The result is an accepted sample or an unreadable reading. The reason
    on failure is an exception class or ``unreadable``, not a payload.
    """
    if type(token) is not int or token < 0 or token >= 2**64:
        return {"readable": False, "reason": "unreadable"}
    data = "0x" + token.to_bytes(32, "big").hex()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": PRECOMPILE, "data": data}, "latest"],
    }
    try:
        payload = transport(url, body)
    except Exception as exc:
        return {"readable": False, "reason": type(exc).__name__}
    if not isinstance(payload, dict):
        return {"readable": False, "reason": "unreadable"}
    result = payload.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        return {"readable": False, "reason": "unreadable"}
    try:
        raw = bytes.fromhex(result[2:])
    except ValueError:
        return {"readable": False, "reason": "unreadable"}
    decoded = decode_precompile(raw)
    accepted = accept_sample(decoded) if decoded is not None else None
    if accepted is None:
        return {"readable": False, "reason": "rejected"}
    accepted["readable"] = True
    accepted["formula_rate"] = accepted["formula_rate"]
    return accepted


def http_transport(url: str, body: dict):
    """POST JSON. A method that would send a transaction is refused."""
    method = body.get("method") if isinstance(body, dict) else None
    if isinstance(method, str) and method.startswith("eth_send"):
        raise RuntimeError("refused")
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310  (operator URL, read-only)
        return json.loads(resp.read().decode())


def _encode(value):
    if isinstance(value, Decimal):
        return {"__decimal__": format(value, "f")}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return value


def _decode(value):
    if isinstance(value, dict) and set(value) == {"__decimal__"} and isinstance(value["__decimal__"], str):
        try:
            return Decimal(value["__decimal__"])
        except Exception:
            return None
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def history_path(env) -> Optional[str]:
    """Where the hour is stored. Unset is None, not a file of zeros."""
    direct = env.get("HRC_HISTORY_PATH")
    if isinstance(direct, str) and direct.strip():
        return direct
    folder = env.get("HRC_STATE_DIR")
    if isinstance(folder, str) and folder.strip():
        return os.path.join(folder, "history.json")
    return None


def store_history(path: str, reading: dict) -> None:
    """Replace the history file. The previous bytes are not the display."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(path, _encode(reading), separators=(", ", ": "))


def load_history(path: Optional[str]) -> dict:
    """The stored hour, or an unreadable reading when the file is not there.

    A missing file is not an empty book. A file that does not parse is not
    an empty book either. Both come back ``readable: False``.
    """
    if not path or not os.path.exists(path):
        return {"readable": False, "reason": "no_history"}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"readable": False, "reason": "unreadable"}
    if not isinstance(payload, dict):
        return {"readable": False, "reason": "unreadable"}
    decoded = _decode(payload)
    if not isinstance(decoded, dict):
        return {"readable": False, "reason": "unreadable"}
    return decoded
