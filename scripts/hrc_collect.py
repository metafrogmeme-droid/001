#!/usr/bin/env python3
"""Read one Hyperliquid borrow-lend hour and, on testnet only, sign it.

Mainnet is a read. The info API is a POST. The precompile, when
HRC_RPC_URL is set, is eth_call. Neither path sends a transaction, and
the signing key is not read unless the confirmed chain id is 998.
Unset HRC_CHAIN_ID is not testnet.

    python3 scripts/hrc_collect.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.caps.strictest import apply_pending, load_book, save_book  # noqa: E402
from bot.hrc.collector import (  # noqa: E402
    collect_hour,
    combine_readings,
    eth_call_precompile,
    history_path,
    http_transport,
    store_history,
)
from bot.hrc.feed import (  # noqa: E402
    CHAIN_TESTNET,
    SigningRefused,
    confirmed_chain_id,
    sign_feed,
    verify_feed,
)
from bot.hrc.index import calibrate  # noqa: E402
from bot.hrc.model import floor_rate_bps  # noqa: E402
from bot.hrc.store import columns, ensure, postgres_ddl, record_quote, record_sample  # noqa: E402


def _cap_updates(env):
    raw = env.get("HRC_CAP_UPDATES")
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _coerce(updates: list) -> list:
    out = []
    for item in updates:
        if not isinstance(item, dict):
            raise SigningRefused()
        proposed = item.get("proposed")
        if isinstance(proposed, str):
            proposed = Decimal(proposed)
        out.append({"source": item.get("source"), "vault": item.get("vault"), "proposed": proposed})
    return out


def _sign_sample(reading: dict) -> dict:
    rate = reading.get("formula_rate")
    bps = floor_rate_bps(rate if isinstance(rate, Decimal) else None)
    return {
        "hour": reading.get("hour"),
        "supplied": reading.get("raw_supplied"),
        "borrowed": reading.get("raw_borrowed"),
        "rate_bps": bps,
        "funding_apr": reading.get("funding_apr") if isinstance(reading.get("funding_apr"), Decimal) else None,
    }


def _dec(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def persist_sample(env, reading: dict) -> bool:
    """Write the hour when HRC_SAMPLE_DB is set. Unset leaves the tape alone.

    The sqlite columns have to match the Postgres DDL. A mismatch writes
    nothing. A quote is stored only when the caller passed a series, a
    side, and a notional. An unreadable premium is NULL, not zero.
    """
    db = env.get("HRC_SAMPLE_DB")
    if not isinstance(db, str) or not db.strip():
        return False
    if not isinstance(reading, dict) or reading.get("readable") is not True:
        return False
    supplied = _dec(reading.get("supplied"))
    borrowed = _dec(reading.get("borrowed"))
    if supplied is None or borrowed is None:
        return False
    expected = columns(postgres_ddl(), "reserve_samples")
    conn = sqlite3.connect(db)
    try:
        ensure(conn)
        have = [row[1] for row in conn.execute("PRAGMA table_info(reserve_samples)").fetchall()]
        if have != expected:
            return False
        ts_utc = reading.get("ts_utc")
        src = reading.get("source")
        if not isinstance(ts_utc, str) or not isinstance(src, str):
            return False
        wrote = record_sample(
            conn,
            ts_utc=ts_utc,
            src=src,
            token=0,
            total_supplied=supplied,
            total_borrowed=borrowed,
            util=_dec(reading.get("utilization")),
            borrow_rate=_dec(reading.get("formula_rate")),
            supply_rate=None,
            l1_block=reading.get("l1_block") if type(reading.get("l1_block")) is int else None,
        )
        series = env.get("HRC_QUOTE_SERIES")
        side = env.get("HRC_QUOTE_SIDE")
        notional = _dec(env.get("HRC_QUOTE_NOTIONAL"))
        premium = _dec(reading.get("premium"))
        if isinstance(series, str) and series and side in ("buy", "sell") and notional is not None:
            record_quote(
                conn, series_id=series, side=side, notional=notional, premium=premium,
            )
        conn.commit()
        return wrote
    finally:
        conn.close()


def run(env, transport, hour: int, *, precompile_transport=None) -> dict:
    """Collect one hour. Sign only when the confirmed chain is 998."""
    chain = confirmed_chain_id(env)
    network = "testnet" if chain == CHAIN_TESTNET else "mainnet"
    info = collect_hour(transport, network=network, hour=hour)
    pre = None
    if network == "mainnet":
        rpc = env.get("HRC_RPC_URL")
        if isinstance(rpc, str) and rpc.strip():
            pre = eth_call_precompile(rpc, 0, precompile_transport or transport)
            if isinstance(pre, dict) and pre.get("readable") is True:
                pre["network"] = "mainnet"
                pre["synthetic"] = False
                calibrate(pre)
    reading = combine_readings(info, pre) if pre is not None else info
    signed = None
    verified = False
    if chain == CHAIN_TESTNET and isinstance(reading, dict) and reading.get("readable") is True:
        try:
            signed = sign_feed(_sign_sample(reading), env, chain)
        except SigningRefused:
            signed = None
        if isinstance(signed, dict):
            digest = bytes.fromhex(str(signed["digest"])[2:])
            verified = verify_feed(
                digest,
                [(signed["r"], signed["s"], signed["v"])],
                [signed["address"]],
            )
    path = history_path(env)
    if path and isinstance(reading, dict) and reading.get("readable") is True:
        store_history(path, reading)
    cap_path = env.get("HRC_CAP_LOG")
    if isinstance(cap_path, str) and cap_path.strip():
        updates = _cap_updates(env)
        if isinstance(updates, list) and updates:
            book = load_book(cap_path)
            apply_pending(book, _coerce(updates))
            save_book(cap_path, book)
    persist_sample(env, reading if isinstance(reading, dict) else {})
    return {"reading": reading, "signed": signed, "verified": verified}


def main() -> None:
    hour_raw = os.environ.get("HRC_HOUR")
    if hour_raw is None or str(hour_raw).strip() == "":
        raise SystemExit(2)
    try:
        hour = int(str(hour_raw).strip(), 10)
    except ValueError:
        raise SystemExit(2)
    run(os.environ, http_transport, hour)


if __name__ == "__main__":
    main()
