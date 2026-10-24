"""The duration price, the flag, and the July verdict."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from bot.skills.t2c_commands import T2CCommands
from bot.t2c.backtest import july_window, lead_verdict
from bot.t2c.borrow import borrow_rate
from bot.t2c.churn import cap_queue_scenario, churn_eth_per_epoch, withdrawability_days
from bot.t2c.config import haircut_config, t_p95_days
from bot.t2c.coordinator import propose_cap_down, strictest_cap
from bot.t2c.duration import duration_percentiles, smooth_samples
from bot.t2c.etherfi import EETH, ETH, STETH, interpret_limit, recorded_etherfi
from bot.t2c.flag import t2c_flag_on
from bot.t2c.price import duration_price, slash_haircut, stake_yield
from bot.t2c.reading import assemble_reading
from bot.t2c.rxb import attempt_sign, holds_keys

ROOT = Path(__file__).resolve().parents[1]


def test_flag_accepts_only_one_and_true():
    assert t2c_flag_on({}) is False
    assert t2c_flag_on({"T2C_ENABLED": "yes"}) is False
    assert t2c_flag_on({"T2C_ENABLED": " YES "}) is False
    assert t2c_flag_on({"T2C_ENABLED": ""}) is False
    assert t2c_flag_on({"T2C_ENABLED": "1"}) is True
    assert t2c_flag_on({"T2C_ENABLED": " True "}) is True


def test_t_p95_unset_falls_back_to_ten_days():
    from bot.t2c.config import observed_duration, weeth_restaked
    days, source = t_p95_days({})
    assert days == Decimal(10)
    assert source == "fallback"
    days, source = t_p95_days({"T2C_T_P95_DAYS": "nope"})
    assert days is None
    assert source == "unreadable"
    days, source = t_p95_days({"T2C_T_P95_DAYS": "12"})
    assert days == Decimal(12)
    assert source == "config"
    observed = observed_duration()
    assert observed["p50_days"] == "5.98"
    assert observed["p95_days"] == "9.17"
    assert observed["fallback_days"] == "10"
    assert weeth_restaked({}) is False
    assert weeth_restaked({"T2C_WEETH_RESTAKED": "1"}) is False


def test_haircut_defaults_are_labelled_unverified():
    hair = haircut_config({})
    assert hair["h_base"] == Decimal("0.001")
    assert hair["h_base_source"] == "unverified_config"
    assert hair["h_restake"] == Decimal("0.01")
    assert hair["h_restake_source"] == "unverified_config"


def test_lido_yield_is_a_reading_and_absence_is_not_zero():
    from bot.t2c.price import carry_term
    unread = stake_yield("wstETH", None)
    assert unread["y_stake"] is None
    assert unread["source"] == "absent"
    read = stake_yield("wstETH", Decimal("0.03"))
    assert read["y_stake"] == Decimal("0.03")
    assert read["source"] == "reading"
    below = carry_term(Decimal("0.019"), Decimal("0.022"))
    assert below["carry"] == 0
    assert below["source"] == "borrow_at_or_below_stake"
    assert carry_term(Decimal("0.05"), None)["carry"] is None
    assert duration_price(Decimal(1), Decimal("0.019"), Decimal("0.022"), Decimal(10), Decimal(0)) == Decimal(1)


def test_weeth_is_priced_on_the_steth_route_then_the_lido_queue():
    from bot.t2c.etherfi import cheapest_weeth_exit
    from bot.t2c.price import apply_exit_fee
    route = cheapest_weeth_exit(Decimal(0), Decimal(5000), Decimal(10))
    assert route["route"] == "steth_then_lido_queue"
    assert route["restaked"] is False
    base = duration_price(Decimal(1), Decimal("0.05"), Decimal(0), Decimal(365), Decimal(0))
    priced = apply_exit_fee(base, route["fee_bps"])
    assert priced == base * Decimal("0.999")
    assert cheapest_weeth_exit(None, Decimal(5000), Decimal(10))["route"] is None
    assert cheapest_weeth_exit(Decimal(1), Decimal(5000), Decimal(10))["route"] == "instant_eth"
    reading = assemble_reading({}, {
        "t_p95_days": Decimal(365),
        "aave_weth": Decimal("0.05"),
        "morpho_weth": Decimal("0.04"),
        "er_wsteth": Decimal(1),
        "er_weeth": Decimal(1),
        "y_wsteth": Decimal(0),
        "y_weeth": Decimal(0),
    })
    weeth = next(asset for asset in reading["public"]["price"]["assets"] if asset["asset"] == "weETH")
    assert weeth["restaked"] is False
    assert Decimal(weeth["p"]) == (-Decimal("0.05")).exp() * Decimal("0.999") * Decimal("0.999")


def test_borrow_rate_needs_both_venues():
    assert borrow_rate(Decimal("0.02"), None) is None
    assert borrow_rate(None, Decimal("0.02")) is None
    assert borrow_rate(Decimal(0), Decimal(0)) == 0
    assert borrow_rate(Decimal("0.01"), Decimal("0.04")) == Decimal("0.04")


def test_slash_haircut_and_price_follow_the_formula():
    s = slash_haircut(Decimal(365), False, None, Decimal("0.001"), Decimal("0.01"))
    assert s == Decimal("0.001")
    assert slash_haircut(Decimal(365), True, None, Decimal("0.001"), Decimal("0.01")) is None
    capped = slash_haircut(Decimal(365), False, None, Decimal("0.2"), Decimal("0.01"))
    assert capped == Decimal("0.05")
    price = duration_price(Decimal(1), Decimal("0.05"), Decimal(0), Decimal(365), Decimal(0))
    assert price == (-Decimal("0.05")).exp()
    # Carry below zero does not increase the price above ER·(1−s).
    flat = duration_price(Decimal(2), Decimal("0.01"), Decimal("0.04"), Decimal(10), Decimal(0))
    assert flat == Decimal(2)
    assert duration_price(Decimal(1), None, Decimal(0), Decimal(10), Decimal(0)) is None


def test_churn_is_unread_until_the_balance_is_read():
    assert churn_eth_per_epoch(None) is None
    assert churn_eth_per_epoch(0) == Decimal(128)
    huge = Decimal(10) ** 18
    assert churn_eth_per_epoch(huge) == Decimal(256)
    # 200 ETH of churn before the cap: total = 200 ETH * 65536, in gwei.
    total = Decimal(200) * Decimal(10) ** 9 * Decimal(65536)
    assert churn_eth_per_epoch(total) == Decimal(200)


def test_queue_scenario_is_not_forced_to_equal_the_quote():
    scenario = cap_queue_scenario(Decimal(12950))
    assert scenario["quoted_wait"] == "1 day 7 hours"
    assert scenario["matches_quoted_wait"] is False
    assert scenario["sum_hours"] == "32.7025"
    assert scenario["queue_hours"] == "5.39583333"
    assert scenario["withdrawability_hours"] == "27.30666667"
    assert withdrawability_days() > 0


def test_unread_sweep_is_not_a_zero_duration():
    missing = duration_percentiles(Decimal(1), Decimal(1), None, Decimal(0))
    assert missing["p50_days"] is None
    assert missing["p95_days"] is None
    zero_sweep = duration_percentiles(Decimal(1), Decimal(1), Decimal(0), Decimal(0))
    assert zero_sweep["p50_days"] == Decimal(2)
    assert zero_sweep["p95_days"] == Decimal(2)
    spread = duration_percentiles(Decimal(1), Decimal(0), Decimal(10), Decimal(0))
    assert spread["p50_days"] == Decimal(6)
    assert spread["p95_days"] == Decimal("10.5")
    restaked = duration_percentiles(Decimal(1), Decimal(0), Decimal(0), Decimal(0), True, None)
    assert restaked["p95_days"] is None


def test_an_empty_smooth_window_is_not_zero():
    assert smooth_samples([])["source"] == "not_smoothed"
    assert smooth_samples([])["value"] is None
    assert smooth_samples([Decimal(1), None])["source"] == "unreadable"
    assert smooth_samples([Decimal(1), Decimal(3)])["value"] == Decimal(2)


def test_etherfi_wrong_key_is_not_a_zero_limit_and_the_snapshot_is_dated():
    wrong = interpret_limit(EETH, Decimal(0))
    assert wrong["capacity_eth"] is None
    assert wrong["reason"] == "wrong_key"
    assert wrong["measured_zero"] is False
    below = interpret_limit(ETH, pool_eth=Decimal(2456), floor_eth=Decimal(22647))
    assert below["capacity_eth"] == 0
    assert below["measured_zero"] is True
    above = interpret_limit(ETH, pool_eth=Decimal(30000), floor_eth=Decimal(22647))
    assert above["capacity_eth"] == Decimal(7353)
    assert above["measured_zero"] is False
    unread = interpret_limit(ETH, pool_eth=None, floor_eth=Decimal(1))
    assert unread["capacity_eth"] is None
    recorded = recorded_etherfi()
    assert recorded["as_of"] == "2026-10-23"
    assert recorded["source"] == "recorded_snapshot"
    assert recorded["eth"]["instant_eth"] == "0"
    assert recorded["steth"]["limit_eth"] == "5000"
    assert recorded["steth"]["issuer_fee_note"] == "not the Exit Swap fee"
    assert interpret_limit(STETH, None)["capacity_eth"] is None


def test_a_missing_price_does_not_propose_zero():
    missing = propose_cap_down(None, Decimal(1))
    assert missing["cap"] is None
    assert missing["applied"] is False
    assert missing["reason"] == "unreadable"
    same = propose_cap_down(Decimal(1), Decimal(1))
    assert same["reason"] == "not_a_cap_down"
    down = propose_cap_down(Decimal("0.8"), Decimal(1))
    assert down["cap"] == Decimal("0.8")
    assert down["applied"] is False
    strict = strictest_cap([
        {"cap": None, "source": "a"},
        {"cap": Decimal("0.9"), "source": "b"},
        {"cap": Decimal("0.7"), "source": "c"},
    ])
    assert strict["cap"] == Decimal("0.7")
    assert strict["source"] == "c"
    assert strictest_cap([{"cap": None, "source": "a"}])["cap"] is None


def test_signing_is_refused_and_no_key_is_read(monkeypatch):
    monkeypatch.setenv("T2C_SIGNING_KEY", "not-a-key")
    monkeypatch.setenv("PRIVATE_KEY", "not-a-key")
    assert holds_keys() is False
    signed = attempt_sign({"kind": "t2c"})
    assert signed["signed"] is False
    assert signed["error"] == "SigningRefused"
    assert signed["holds_keys"] is False
    source = (ROOT / "bot" / "t2c" / "rxb.py").read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "PRIVATE_KEY" not in source


def test_default_reading_does_not_invent_a_price_or_a_lead():
    reading = assemble_reading({})
    public = reading["public"]
    assert public["liquidation_price"] is False
    assert public["price"]["p"] is None
    assert public["price"]["r_borrow"] is None
    assert public["price"]["y_stake"] is None
    assert public["price"]["y_stake_source"] == "absent"
    assert public["carry_recorded"]["carry"] == "0"
    assert public["carry_recorded"]["carry_source"] == "borrow_at_or_below_stake"
    daily = public["backtest"]["daily"]
    assert daily["sanity"]["sale_status"] == "matches_stated"
    assert daily["sanity"]["queue_cost_status"] == "not_read"
    assert daily["days"] == 480
    assert daily["curve_10k_days"] == 477
    assert daily["borrow_days"] == 477
    assert daily["error_days"] == 0
    assert daily["not_mined"] == 3
    assert daily["result_bps"] is None
    assert daily["lead"]["verdict"] == "not_established"
    assert daily["widest_10k_day"] == "2026-04-19"
    july = json.loads((ROOT / "benchmark" / "t2c" / "daily_chain.json").read_text(encoding="utf-8"))
    anchor = next(day for day in july["days"] if day["day"] == "2025-07-21")
    assert anchor["curve"]["10000"] == "9017058912517896534594"
    assert anchor["borrow_ray"] == "55901435106137103049916020"
    assert public["price"]["t_p95_days"] == "10"
    assert public["price"]["t_p95_source"] == "fallback"
    assert public["duration_observed"]["p50_days"] == "5.98"
    assert public["duration_observed"]["p95_days"] == "9.17"
    assert public["consumers"]["price_feed"][0] == "Morpho"
    assert public["consumers"]["unwind"]["venue"] == "Aave v3 Ethereum"
    assert public["consumers"]["unwind"]["signal_only"] is True
    assert public["consumers"]["unwind"]["liquidation_price"] is False
    weeth = next(asset for asset in public["price"]["assets"] if asset["asset"] == "weETH")
    assert weeth["restaked"] is False
    assert weeth["exit_route"] == "steth_then_lido_queue"
    assert weeth["exit_fee_bps"] == "10"
    assert weeth["exit_source"] == "recorded_snapshot"
    assert public["duration"]["p95_days"] is None
    assert public["backtest"]["verdict"] == "not_established"
    assert public["backtest"]["july_31"] == "absent"
    assert "supplied_usd" not in public
    assert "research" not in public
    dumped = str(public)
    assert "$" not in dumped
    assert "3640000000" not in dumped
    assert "6858000000" not in dumped
    admin = reading["admin"]
    assert admin["research"]["supplied_usd"][0]["usd"] == "3640000000"
    assert admin["research"]["target_collateral_usd"] == "6858000000"
    assert admin["research"]["target_collateral_billions"] == "6.86"
    assert admin["research"]["as_of"] == "2026-10-23"


def test_an_estimated_duration_overrides_config_and_prices_wsteth():
    reading = assemble_reading({}, {
        "t_p95_days": Decimal(365),
        "aave_weth": Decimal("0.05"),
        "morpho_weth": Decimal("0.04"),
        "er_wsteth": Decimal(1),
        "y_wsteth": Decimal(0),
    })
    price = reading["public"]["price"]
    assert price["t_p95_source"] == "estimated"
    assert price["r_borrow"] == "0.05"
    assert Decimal(price["p"]) == (-Decimal("0.05")).exp() * Decimal("0.999")


def test_july_window_keeps_both_disagreeing_prints_and_names_the_widest():
    window = july_window()
    assert window["verdict"] == "not_established"
    assert window["widest_day"] == "2025-07-21"
    assert window["widest_steth_discount_bp"] == "39.12"
    assert "2025-07-31" in window["missing_days"]
    assert "2025-07-18" in window["missing_days"]
    july_26 = [row for row in window["rows"] if row["day"] == "2025-07-26"]
    assert len(july_26) == 2
    signs = [Decimal(row["steth_discount_bp"]) > 0 for row in july_26]
    assert signs == [True, False] or signs == [False, True]
    assert "exchange rate" in window["weeth_note"]
    for row in window["rows"]:
        assert "price" not in row


def test_lead_detector_both_arms_and_the_missing_arm():
    model = [Decimal(0)] * 4 + [Decimal(1)] * 6
    market = [Decimal(0)] * 7 + [Decimal(1)] * 3
    assert lead_verdict(model, market)["verdict"] == "led"
    assert lead_verdict(market, model)["verdict"] == "did_not_lead"
    assert lead_verdict([None] * 10, model)["verdict"] == "not_established"
    assert lead_verdict([Decimal(1)] * 10, [Decimal(1)] * 10)["verdict"] == "not_established"


@pytest.mark.asyncio
async def test_the_chat_command_is_admin_only_and_names_an_unread_price():
    sent: list[str] = []

    class Host(T2CCommands):
        def __init__(self, admin: bool) -> None:
            self.admin = admin

        def _is_admin(self, update) -> bool:
            return self.admin

        def _lang(self, update) -> str:
            return "en"

        async def _send(self, update, text, reply_markup=None, edit: bool = False) -> None:
            sent.append(text)

    await Host(False)._cmd_t2c(None, None)
    assert sent and "T2C cap signal" not in sent[0]
    sent.clear()
    await Host(True)._cmd_t2c(None, None)
    assert "T2C cap signal" in sent[0]
    assert "not read" in sent[0]
    assert "3640000000" not in sent[0]


def test_the_contracts_are_not_a_liquidation_oracle_and_match_the_intake_cap():
    from bot.t2c.exit_model import INTAKE_DENOMINATOR, INTAKE_NUMERATOR
    src_dir = ROOT / "contracts" / "t2c"
    blob = "\n".join(path.read_text(encoding="utf-8") for path in src_dir.rglob("*.sol"))
    assert "function price(" not in blob
    assert "liquidationCall" not in blob
    model = (src_dir / "src" / "ExitSwapModel.sol").read_text(encoding="utf-8")
    deposit_files = sorted(
        path.name
        for path in src_dir.rglob("*.sol")
        if "function deposit(" in path.read_text(encoding="utf-8")
    )
    assert deposit_files == ["T2CPool.sol"]
    assert "function deposit(" not in model
    assert "function supply(" not in blob
    assert "function borrow(" not in blob
    assert "MainnetForbidden" in model
    assert "31337" in model
    assert "ENTER_EDGE_BPS = 50" in model
    assert "EXIT_EDGE_BPS = 20" in model
    assert "function quote(" in blob
    assert "function sell(" in blob
    assert "function claim(" in blob
    assert "event Bought" in blob
    assert "function latest(" in blob
    assert "event Posted" in blob
    assert f"INTAKE_NUMERATOR = {INTAKE_NUMERATOR}" in model
    assert f"INTAKE_DENOMINATOR = {INTAKE_DENOMINATOR}" in model
    tree = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "bot" / "t2c").glob("*.py"))
    assert "liquidationCall" not in tree
