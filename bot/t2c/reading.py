"""One reading of the cap signal. Prices are strings. Unread stays null."""
from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal

from bot.t2c.backtest import july_window
from bot.t2c.borrow import borrow_rate
from bot.t2c.chain_series import daily_report
from bot.t2c.churn import cap_queue_scenario, churn_eth_per_epoch, confirmed_churn
from bot.t2c.config import haircut_config, t_eigen_days, t_p95_days, weeth_restaked
from bot.t2c.consumers import admin_consumer_context, public_consumers
from bot.t2c.coordinator import cap_json, propose_cap_down
from bot.t2c.duration import duration_percentiles, smooth_samples
from bot.t2c.etherfi import WEETH, interpret_limit, recorded_etherfi
from bot.t2c.flag import t2c_flag_on
from bot.t2c.numbers import as_decimal, json_num
from bot.t2c.packet import build_packet, haircut_for
from bot.t2c.price import carry_term, duration_price, slash_haircut, stake_yield
from bot.t2c.rxb import attempt_sign, sentinel_submit
from bot.t2c.snapshots import recorded_carry, recorded_request_times


def _choose(estimated: object, configured: Decimal | None, source: str) -> tuple[Decimal | None, str]:
    reading = as_decimal(estimated)
    if reading is not None:
        return reading, "estimated"
    if configured is not None and source == "config":
        return configured, "config"
    if source == "unreadable":
        return None, "unreadable"
    return None, "absent"


def _asset_price(
    asset: str,
    restaked: bool,
    er: object,
    r_borrow: Decimal | None,
    y_reading: object,
    t_p95: Decimal | None,
    eigen: Decimal | None,
    hair: dict,
) -> dict:
    y = stake_yield(asset, y_reading)
    term = carry_term(r_borrow, y["y_stake"])
    s = slash_haircut(t_p95, restaked, eigen, hair["h_base"], hair["h_restake"])
    price = duration_price(er, r_borrow, y["y_stake"], t_p95, s)
    return {
        "asset": asset,
        "restaked": restaked,
        "er": json_num(as_decimal(er)),
        "y_stake": json_num(y["y_stake"]),
        "y_stake_source": y["source"],
        "carry": json_num(term["carry"]),
        "carry_source": term["source"],
        "s": json_num(s),
        "p": json_num(price),
    }


def assemble_reading(
    env: Mapping[str, str] | None = None,
    inputs: Mapping[str, object] | None = None,
) -> dict:
    """The signal. Live inputs are whatever the caller measured; nothing here fetches.

    An omitted input is unread. It is not filled from the July window or from
    the dated ether.fi snapshot.
    """
    src = os.environ if env is None else env
    measured = inputs if inputs is not None else {}
    hair = haircut_config(src)
    configured_p95, p95_source = t_p95_days(src)
    configured_eigen, eigen_source = t_eigen_days(src)
    samples = measured.get("smooth_samples")
    if not isinstance(samples, list):
        samples = None
    smoothed = smooth_samples(samples if isinstance(samples, list) else None)
    duration = duration_percentiles(
        measured.get("t_queue_days"),
        measured.get("t_withdrawability_days"),
        measured.get("t_sweep_days"),
        measured.get("t_protocol_days"),
        restaked=False,
    )
    if duration["p95_days"] is not None:
        t_p95, t_source = duration["p95_days"], "estimated"
    else:
        t_p95, t_source = _choose(measured.get("t_p95_days"), configured_p95, p95_source)
    eigen, eigen_chosen = _choose(measured.get("t_eigen_days"), configured_eigen, eigen_source)
    r_borrow = borrow_rate(measured.get("aave_weth"), measured.get("morpho_weth"))
    headline_er = measured.get("er_wsteth")
    assets = [
        _asset_price("wstETH", False, headline_er, r_borrow,
                     measured.get("y_wsteth"), t_p95, eigen, hair),
        _asset_price("weETH", weeth_restaked(src), measured.get("er_weeth"), r_borrow,
                     measured.get("y_weeth"), t_p95, eigen, hair),
        _asset_price("rsETH", True, measured.get("er_rseth"), r_borrow,
                     measured.get("y_rseth"), t_p95, eigen, hair),
    ]
    headline = assets[0]
    request_time = recorded_request_times()
    packets = []
    for bucket in request_time["buckets"]:
        packets.append(build_packet(
            "wstETH",
            bucket.get("size_eth"),
            bucket.get("t_secs"),
            None,
            None,
            None,
            None,
            None,
            haircut_for(
                bucket.get("t_secs"), False, eigen,
                hair["h_base"], hair["h_restake"],
            ),
            request_time.get("fetched_at"),
        ))
    wrong_key = interpret_limit(WEETH, Decimal(0))
    cap = cap_json(propose_cap_down(as_decimal(headline["p"]), headline_er))
    signed = attempt_sign({"kind": "t2c_cap_signal"})
    sentinel = sentinel_submit({"kind": "t2c_cap_signal"})
    live_churn = churn_eth_per_epoch(measured.get("total_active_gwei"))
    built = {
        "flag_on": t2c_flag_on(src),
        "liquidation_price": False,
        "t_p95_days": json_num(t_p95),
        "t_p95_source": t_source,
        "t_eigen_days": json_num(eigen),
        "t_eigen_source": eigen_chosen,
        "r_borrow": json_num(r_borrow),
        "h_base": json_num(hair["h_base"]),
        "h_base_source": hair["h_base_source"],
        "h_restake": json_num(hair["h_restake"]),
        "h_restake_source": hair["h_restake_source"],
        "s_cap": json_num(hair["s_cap"]),
        "assets": assets,
        "duration": {
            "p50_days": json_num(duration["p50_days"]),
            "p95_days": json_num(duration["p95_days"]),
            "source": duration["source"],
            "smoothed": json_num(smoothed["value"]),
            "smooth_source": smoothed["source"],
        },
        "churn": confirmed_churn(),
        "churn_eth_per_epoch": json_num(live_churn),
        "observation": {
            "as_of": "2026-10-23",
            "queue_eth": "12950",
            "quoted_wait": "1 day 7 hours",
            "sweep_days": "7.1",
            "sweep_source": "observation",
            "scenario": cap_queue_scenario(),
        },
        "etherfi": recorded_etherfi(),
        "etherfi_wrong_key": {
            "output": "weeth",
            "capacity_eth": json_num(wrong_key["capacity_eth"]),
            "reason": wrong_key["reason"],
            "measured_zero": wrong_key["measured_zero"],
        },
        "consumers": public_consumers(),
        "cap": cap,
        "rxb": {
            "signed": signed["signed"],
            "error": signed["error"],
            "holds_keys": signed["holds_keys"],
            "sentinel": sentinel["sink"],
            "applied": sentinel["applied"],
        },
        "packets": packets,
        "request_time": request_time,
        "carry_recorded": recorded_carry(),
        "backtest": july_window(),
        "daily": daily_report(),
        "fee": {
            "applies_to": "exit_swap_only",
            "formula_bps": "clamp(10 + 40*U^2, 5, 100)",
            "intake": "2/100 of pool ETH per block",
        },
    }
    return {"public": public_payload(built), "admin": admin_payload(built)}


def public_payload(built: dict) -> dict:
    """The feed. No research dollar sizes and no fork-ledger example."""
    backtest = built["backtest"]
    return {
        "kind": "t2c_cap_signal",
        "liquidation_price": False,
        "flag_on": built["flag_on"],
        "price": {
            "p": built["assets"][0]["p"],
            "er": built["assets"][0]["er"],
            "r_borrow": built["r_borrow"],
            "y_stake": built["assets"][0]["y_stake"],
            "y_stake_source": built["assets"][0]["y_stake_source"],
            "carry": built["assets"][0]["carry"],
            "carry_source": built["assets"][0]["carry_source"],
            "t_p95_days": built["t_p95_days"],
            "t_p95_source": built["t_p95_source"],
            "s": built["assets"][0]["s"],
            "s_cap": built["s_cap"],
            "h_base": built["h_base"],
            "h_base_source": built["h_base_source"],
            "h_restake": built["h_restake"],
            "h_restake_source": built["h_restake_source"],
            "assets": built["assets"],
        },
        "duration": built["duration"],
        "churn": {
            "status": built["churn"]["status"],
            "confirmed_as_of": built["churn"]["confirmed_as_of"],
            "confirmed_from": built["churn"]["confirmed_from"],
            "min_eth_per_epoch": built["churn"]["min_eth_per_epoch"],
            "max_eth_per_epoch": built["churn"]["max_eth_per_epoch"],
            "quotient": built["churn"]["churn_limit_quotient"],
            "live_eth_per_epoch": built["churn_eth_per_epoch"],
        },
        "observation": built["observation"],
        "etherfi": built["etherfi"],
        "etherfi_wrong_key": built["etherfi_wrong_key"],
        "consumers": built["consumers"],
        "packets": built["packets"],
        "request_time": {
            "source": built["request_time"]["source"],
            "fetched_at": built["request_time"].get("fetched_at"),
            "buckets": built["request_time"].get("buckets"),
        },
        "carry_recorded": built["carry_recorded"],
        "cap": built["cap"],
        "rxb": built["rxb"],
        "backtest": {
            "verdict": backtest["verdict"],
            "reason": backtest["reason"],
            "window": backtest["window"],
            "july_31": backtest["july_31"],
            "weeth_note": backtest["weeth_note"],
            "widest_steth_discount_bp": backtest["widest_steth_discount_bp"],
            "widest_day": backtest["widest_day"],
            "missing_days": backtest["missing_days"],
            "rows": backtest["rows"],
            "lead": backtest["lead"],
            "daily": built["daily"],
        },
        "fee": built["fee"],
    }


def admin_payload(built: dict) -> dict:
    payload = public_payload(built)
    payload["research"] = admin_consumer_context()
    return payload
