"""Recursion Breaker: the price, the look-through, the frozen pull, the caps.

Unreadable stays null. A thin market is left out of the min. The October
API windows are not an all-clear and not a crossing.
"""

from __future__ import annotations

import json
from decimal import ROUND_FLOOR, Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from bot.rxb.backtest import run_backtest
from bot.rxb.chains import MAINNET_CHAIN_IDS, signing_allowed
from bot.rxb.flag import flag_on
from bot.rxb.indexer import (
    euler_vaults_url,
    fetch_all,
    fetch_euler_ethereum,
    fetch_markets,
    parse_history_point,
)
from bot.rxb.machine import fresh_state, step
from bot.rxb.metrics import GEdge, GToken, cross_venue_dms, solve_graph
from bot.rxb.oracle_math import (
    _BITS,
    DEPTH_MIN_USD,
    HALF_LIFE_S,
    WAD,
    morpho_price,
    quote_price,
    weight_wad,
)
from bot.rxb.reading import node_view
from bot.rxb.report import admin_text
from bot.rxb.solver import Claim, Issuer, solve_issuers
from bot.utils.outbound import reply_safe
from bot.vaults.cap_coordinator import coordinate_caps
from bot.vaults.cap_id import MIN_INCREASE_DELAY_S, cap_id_hex, collateral_id_data, onboard_vault
from bot.vaults.sentinel import plan_cap_down

ROOT = Path(__file__).resolve().parent.parent
WAD_Q = 10**18


def _node(**kwargs) -> Issuer:
    base = dict(
        token="A", nav=100, external=100, onchain_face=100, debt=0,
        attested=0, attestation_age_s=None, claims=(), borrower_count=None,
    )
    base.update(kwargs)
    return Issuer(**base)


def _frac(text: str | None) -> Fraction | None:
    if text is None:
        return None
    num, den = text.split("/")
    return Fraction(int(num), int(den))


def test_weight_is_exact_at_half_lives_and_rejects_a_negative_age():
    assert weight_wad(0) == WAD
    assert weight_wad(HALF_LIFE_S) == WAD // 2
    assert weight_wad(2 * HALF_LIFE_S) == WAD // 4
    assert weight_wad(64 * HALF_LIFE_S) == 0
    with pytest.raises(ValueError):
        weight_wad(-1)
    with pytest.raises(TypeError):
        weight_wad(True)  # type: ignore[arg-type]


def test_the_bit_constants_are_the_half_life_series():
    for k, got in enumerate(_BITS):
        expect = (Decimal(WAD) * Decimal(2) ** (Decimal(-1) / Decimal(2) ** (k + 1)))
        assert got == int(expect.to_integral_value(rounding=ROUND_FLOOR))


def test_a_thin_or_unread_market_is_dropped_not_zeroed():
    full = dict(nav=WAD_Q, v_chain=WAD_Q, v_att=0, supply=WAD_Q, delta_s=0)
    thin = quote_price(**full, p_mkt=10**16, include_mkt=True, depth_usd=DEPTH_MIN_USD - 1)
    unread = quote_price(**full, p_mkt=0, include_mkt=True, depth_usd=None)
    assert thin == WAD_Q
    assert unread == WAD_Q
    assert thin != 0 and unread != 0


def test_a_liquid_twap_is_only_an_upper_bound():
    full = dict(nav=2 * WAD_Q, v_chain=2 * WAD_Q, v_att=0, supply=WAD_Q, delta_s=0,
                include_mkt=True, depth_usd=DEPTH_MIN_USD)
    assert quote_price(**full, p_mkt=WAD_Q) == WAD_Q
    assert quote_price(**full, p_mkt=3 * WAD_Q) == 2 * WAD_Q
    # A measured zero on a deep book is a reading. An unread book is not.
    assert quote_price(**full, p_mkt=0) == 0
    assert morpho_price(WAD_Q) == WAD_Q * 10**18
    assert morpho_price(None) is None


def test_unreadable_inputs_are_not_a_zero_price():
    assert quote_price(
        nav=None, v_chain=WAD_Q, v_att=0, supply=WAD_Q, p_mkt=None,
        include_mkt=False, depth_usd=None, delta_s=0,
    ) is None
    assert quote_price(
        nav=WAD_Q, v_chain=WAD_Q, v_att=0, supply=0, p_mkt=None,
        include_mkt=False, depth_usd=None, delta_s=0,
    ) is None


def test_intra_scc_claims_are_zero_and_the_two_ratios_differ():
    """The published shape: one cycle, look-through haircut, on-chain face kept."""
    solved = solve_issuers([
        _node(token="xUSD", nav=530_000_000, external=170_000_000, onchain_face=170_000_000,
              claims=(Claim("deUSD", 68_000_000),)),
        _node(token="deUSD", nav=104_615_385, external=36_615_385, onchain_face=104_615_385,
              claims=(Claim("xUSD", 68_000_000),)),
    ])
    assert _frac(solved["xUSD"]["cbf"]) == Fraction(360_000_000, 530_000_000)
    assert _frac(solved["xUSD"]["uf"]) == Fraction(360_000_000, 530_000_000)
    assert _frac(solved["deUSD"]["cbf"]) == Fraction(68_000_000, 104_615_385)
    assert _frac(solved["deUSD"]["uf"]) == 0
    assert solved["xUSD"]["tripped"] is True
    assert "uf" not in solved["deUSD"]["reasons"]
    assert solved["deUSD"]["edges"][0]["inside_scc"] is True


def test_an_outside_claim_is_marked_and_cbf_is_not_clamped():
    solved = solve_issuers([
        _node(token="A", nav=100, external=40, onchain_face=40,
              claims=(Claim("B", 50),)),
        _node(token="B", nav=100, external=80, onchain_face=80),
    ])
    assert solved["A"]["edges"][0]["inside_scc"] is False
    assert _frac(solved["A"]["v"]) == 80
    underwater = solve_issuers([
        _node(token="A", nav=100, external=0, onchain_face=0, debt=50),
    ])
    assert _frac(underwater["A"]["cbf"]) == Fraction(3, 2)


def test_missing_nav_is_not_a_zero_cbf_and_a_missing_age_is_not_stale():
    missing = solve_issuers([_node(nav=None)])
    assert missing["A"]["cbf"] is None
    assert missing["A"]["tripped"] is None
    unread_age = solve_issuers([_node(attested=5, attestation_age_s=None)])
    assert unread_age["A"]["stale"] is None
    assert unread_age["A"]["tripped"] is None
    fresh_zero = solve_issuers([_node(attested=0, attestation_age_s=None)])
    assert fresh_zero["A"]["stale"] is False
    stale = solve_issuers([_node(attested=5, attestation_age_s=HALF_LIFE_S + 1)])
    assert stale["A"]["stale"] is True
    assert "stale" in stale["A"]["reasons"]
    boundary = solve_issuers([_node(attested=5, attestation_age_s=HALF_LIFE_S, onchain_face=100)])
    assert boundary["A"]["stale"] is False


def test_uf_does_not_go_negative_and_a_missing_borrower_count_is_not_many():
    excess = solve_issuers([_node(nav=100, onchain_face=150)])
    assert excess["A"]["uf"] == "0/1"
    assert _frac(excess["A"]["excess"]) == 50
    assert solve_issuers([_node(borrower_count=None)])["A"]["concentration"] == "unreadable"
    assert solve_issuers([_node(borrower_count=1)])["A"]["concentration"] == "single_borrower"
    assert solve_issuers([_node(borrower_count=4)])["A"]["concentration"] == "not_single"


def test_the_backtest_does_not_signal_before_28_oct_and_is_not_an_all_clear():
    result = run_backtest()
    assert result["shows_crossing_before_2025_10_28"] is False
    assert result["earlier_windows_readable"] is False
    assert result["all_clear"] is False
    assert result["earliest_sourced_crossing"] == "2025-10-28"
    assert result["earliest_basis"] == "published_comparison"
    assert result["endpoint"] == "https://api.morpho.org/graphql"
    assert result["pulled_at"] == "2026-10-23"
    published = {row["token"]: row for row in result["published"]}
    assert published["xUSD"]["tripped"] is True
    assert published["deUSD"]["tripped"] is True
    assert _frac(published["deUSD"]["uf"]) == 0
    for window in result["windows"]:
        assert window["readable"] is False
        assert window["crossed"] is None
    text = json.dumps(result)
    assert "$" not in text
    # Token symbols (xUSD, deUSD) are names. A dollar amount would be a
    # "$" or a JSON key whose name is the unit.
    assert '"usd"' not in text.lower()


def test_the_frozen_pull_keeps_the_october_points_and_no_professor_path():
    fixture = json.loads(
        (ROOT / "bot/rxb/fixtures/stream_elixir_2025.json").read_text(encoding="utf-8"))
    sde = fixture["market_facts"][0]
    oct28 = [p for p in sde["points"] if p["day"] == "2025-10-28" and p["field"] == "collateral_usd"]
    assert oct28[0]["y"] == "11336510.60"
    assert oct28[0]["x"] == 1761609600
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "bot/rxb").rglob("*")
        if p.is_file() and p.suffix in {".py", ".json", ".md"}
    )
    assert "professor/data" not in blob
    assert parse_history_point({"x": 1, "y": None}) == {"t": 1, "y": None, "readable": False}
    assert parse_history_point({"x": 1, "y": 0})["readable"] is True
    assert parse_history_point({"x": 1, "y": 0})["y"] == "0"


def test_a_failed_fetch_is_not_an_empty_book_and_a_full_page_is_incomplete():
    def dead(_query, _variables):
        return None

    failed = fetch_markets(1, dead, page_size=2)
    assert failed["readable"] is False
    assert failed["items"] is None

    def full(_query, variables):
        n = variables["first"]
        return {"data": {"markets": {"items": [{"marketId": str(i)} for i in range(n)]}}}

    partial = fetch_markets(1, full, page_size=2)
    assert partial["complete"] is False
    book = fetch_all(dead, page_size=2)
    assert book["readable"] is False
    assert book["order"] == ["ethereum", "robinhood", "base"]
    assert all(chain["items"] is None for chain in book["chains"])


def test_the_strictest_cap_wins_and_an_unreadable_one_blocks(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = coordinate_caps([
        {"source": "rxb", "vault": "v", "id_data": "id", "absolute": 100, "relative": 10**18, "readable": True},
        {"source": "cgc", "vault": "v", "id_data": "id", "absolute": 40, "relative": None, "readable": True},
        {"source": "t2c", "vault": "v", "id_data": "id", "absolute": 70, "relative": 5 * 10**17, "readable": True},
    ], str(path))
    assert first["decisions"][0]["absolute"] == 40
    assert first["decisions"][0]["relative"] == 5 * 10**17
    blocked = coordinate_caps([
        {"source": "rxb", "vault": "v", "id_data": "id", "absolute": 10, "relative": 1, "readable": False},
        {"source": "cgc", "vault": "v", "id_data": "id", "absolute": 40, "relative": 1, "readable": True},
    ], str(path))
    assert blocked["decisions"][0]["readable"] is False
    assert blocked["decisions"][0]["absolute"] is None
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["absolute"] == 40


def test_an_absent_opinion_does_not_loosen_and_mainnet_never_sends(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    decided = coordinate_caps([
        {"source": "cgc", "vault": "v", "id_data": "id", "absolute": 40, "relative": None, "readable": True},
        {"source": "t2c", "vault": "v", "id_data": "id", "absolute": None, "relative": None, "readable": True},
    ], path)
    assert decided["decisions"][0]["absolute"] == 40
    calls: list = []

    def broadcaster(plan):
        calls.append(plan)

    base = dict(vault="v", id_data="id", broadcaster=broadcaster, audit_path=path,
                proposals=[{"source": "rxb", "vault": "v", "id_data": "id",
                            "absolute": 0, "relative": None, "readable": True}])
    refused = plan_cap_down(chain_id=1, current_absolute=100, current_relative=None, env={"RXB_ENABLED": "1"}, **base)
    assert refused["reason"] == "mainnet_refusal"
    assert calls == []
    assert MAINNET_CHAIN_IDS == {1, 8453, 4663}
    for chain in (1, 8453, 4663, 42161, 10, 137, 56, 43114):
        assert signing_allowed(chain) is False
    for chain in (31337, 1337, 11155111, 84532, 46630):
        assert signing_allowed(chain) is True
    off = plan_cap_down(chain_id=31337, current_absolute=100, current_relative=None, env={}, **base)
    assert off["reason"] == "flag_off"
    assert calls == []
    unknown = plan_cap_down(chain_id=31337, current_absolute=None, current_relative=None,
                            env={"RXB_ENABLED": "true"}, increase_delay=MIN_INCREASE_DELAY_S, **base)
    assert unknown["reason"] == "current_unreadable"
    assert calls == []
    flat = plan_cap_down(chain_id=31337, current_absolute=0, current_relative=None,
                         env={"RXB_ENABLED": "1"}, increase_delay=MIN_INCREASE_DELAY_S, **base)
    assert flat["reason"] == "no_decrease"
    assert calls == []
    sent = plan_cap_down(chain_id=31337, current_absolute=100, current_relative=None,
                         env={"RXB_ENABLED": "1"}, increase_delay=MIN_INCREASE_DELAY_S, **base)
    assert sent["sent"] is True
    assert calls == [[{"kind": "absolute", "cap": 0, "id_data": "id", "cap_id": None}]]


def test_the_flag_accepts_only_one_and_true():
    assert flag_on({}) is False
    assert flag_on({"RXB_ENABLED": "yes"}) is False
    assert flag_on({"RXB_ENABLED": "on"}) is False
    assert flag_on({"RXB_ENABLED": " TRUE "}) is True
    assert flag_on({"RXB_ENABLED": "1"}) is True


def test_node_view_and_the_admin_text_keep_ratios_and_the_refusal():
    xusd = node_view("xUSD")
    assert xusd["cbf"] == "34/265"
    assert xusd["lts"] == "36/53"
    assert xusd["state"] == "Fired"
    assert xusd["readable"] is True
    assert xusd["price"]["price_wad"] is None
    assert "v" not in xusd and "excess" not in xusd
    dumped = json.dumps(xusd)
    assert "$" not in dumped and '"usd"' not in dumped.lower()
    sde = node_view("sdeUSD")
    assert sde["readable"] is True
    assert sde["cbf"] == "13600000/20923077"
    assert sde["lts"] == "13600000/20923077"
    assert sde["state"] == "Fired"
    assert sde["basis"] == "published_comparison"
    usd3 = node_view("USD3")
    assert usd3["readable"] is False
    assert usd3["cbf"] is None
    assert usd3["reason"] == "no_lookthrough_inputs"
    text = admin_text("")
    assert "does not show a threshold crossing before 28 Oct 2025" in text
    assert "not an all-clear" in text
    assert "setIsSentinel" in text
    assert "$" not in text
    assert len(text) < 4000
    safe = reply_safe(text)
    assert "28 Oct 2025" in safe
    doc = (ROOT / "docs/rxb/SENTINEL.md").read_text(encoding="utf-8")
    for sentence in (
        "Only the vault owner can call setIsSentinel",
        "The curator cannot",
        "decreaseAbsoluteCap(bytes idData, uint256 newAbsoluteCap)",
        "delay can be configured down to zero",
        "never takes a sentinel role on mainnet",
        "2-of-3",
        "72 hour",
        "phase 2",
        "1e36",
    ):
        assert sentence in doc


def test_the_adapter_scale_is_one_e36_times_the_decimal_gap():
    assert morpho_price(WAD) == WAD * 10**18
    assert morpho_price(WAD, 6, 18) == 10**24
    assert morpho_price(1, 0, 36) is None
    assert morpho_price(WAD, 37, 18) is None


def test_inside_weights_are_cbf_and_a_missing_age_is_not_a_haircut():
    published = solve_graph(
        [GToken("xUSD", 530_000_000), GToken("deUSD", 104_615_385)],
        [
            GEdge("xUSD", None, 170_000_000, "holding", "onchain", offchain_id="external_face"),
            GEdge("xUSD", "deUSD", 68_000_000, "holding", "onchain"),
            GEdge("deUSD", None, 36_615_385, "holding", "onchain", offchain_id="external_face"),
            GEdge("deUSD", "xUSD", 68_000_000, "holding", "onchain"),
        ],
    )
    assert _frac(published["xUSD"]["cbf"]) == Fraction(34, 265)
    assert _frac(published["xUSD"]["lts"]) == Fraction(36, 53)
    assert published["xUSD"]["uf"] == "0/1"
    unsigned = solve_graph(
        [GToken("A", 100)],
        [GEdge("A", None, 40, "holding", "none", offchain_id="note")],
    )["A"]
    assert unsigned["uf"] == "2/5"
    assert unsigned["cbf"] == "0/1"
    assert unsigned["lts"] == "1/1"
    signed = solve_graph(
        [GToken("A", 100)],
        [GEdge("A", None, 100, "holding", "signed", offchain_id="note")],
    )["A"]
    assert signed["cbf"] == "0/1"
    assert signed["lts"] is None
    assert signed["v"] is None


def test_one_venue_is_not_a_migration_and_a_missing_borrow_is_not_zero():
    one = cross_venue_dms([{"borrow_now": 180, "borrow_prev": 100}])
    assert one["readable"] is True
    assert one["dms"] == "0/1"
    missing = cross_venue_dms([
        {"borrow_now": 10, "borrow_prev": None},
        {"borrow_now": 12, "borrow_prev": 10},
    ])
    assert missing["readable"] is False
    assert missing["dms"] is None
    moved = cross_venue_dms([
        {"borrow_now": 160, "borrow_prev": 100},
        {"borrow_now": 160, "borrow_prev": 100},
    ])
    assert _frac(moved["dms"]) == Fraction(3, 5)


def test_fire_waits_an_hour_and_recovery_waits_for_the_timelock():
    hot = {"cbf": "34/265", "uf": "0/1", "lts": "36/53", "dms": "0/1"}
    calm = {"cbf": "1/100", "uf": "1/100", "lts": "1/100", "dms": "1/100"}
    alerted = step(fresh_state(), {**hot, "ts": 1_000})
    assert alerted["state"] == "Alert"
    assert step(alerted, {**hot, "ts": 1_000 + 1_800})["state"] == "Alert"
    fired = step(alerted, {**hot, "ts": 1_000 + 3_600})
    assert fired["state"] == "Fired"
    unread = step(alerted, {"ts": 2_000, "cbf": None, "uf": None, "lts": None, "dms": None})
    assert unread["state"] == "Alert"
    assert unread["below_since"] is None
    lowered = step(alerted, {**calm, "ts": 5_000})
    assert lowered["state"] == "Alert"
    watching = step(lowered, {**calm, "ts": 5_000 + 86_400})
    assert watching["state"] == "Watching"
    cleared = step(fired, {**calm, "ts": 10_000})
    assert cleared["state"] == "Fired"
    still = step(cleared, {**calm, "ts": 10_000 + 7 * 86_400})
    assert still["state"] == "Fired"
    recovered = step(cleared, {**calm, "ts": 10_000 + 7 * 86_400}, caps_reraised=True)
    assert recovered["state"] == "Recovered"


def test_the_schema_names_the_four_tables_and_the_check_vocabulary():
    sql = (ROOT / "bot/rxb/schema.sql").read_text(encoding="utf-8")
    for table in ("tokens", "edges", "markets", "scc_runs"):
        assert f"CREATE TABLE {table}" in sql
    for kind in ("stable", "yield", "lst", "wrapper", "credit"):
        assert f"'{kind}'" in sql
    for kind in ("holding", "loan", "collateral", "mint_backing"):
        assert f"'{kind}'" in sql
    for verif in ("onchain", "por", "zk", "signed", "none"):
        assert f"'{verif}'" in sql
    assert "dst_token IS NOT NULL AND offchain_id IS NULL" in sql


def test_coverage_fires_six_days_before_the_disclosure_and_does_not_score_a_listing():
    result = run_backtest()
    v2 = result["v2"]
    assert v2["readable"] is True
    assert v2["lead_days"] == 6
    assert v2["fired_on"] == "2025-10-29"
    assert v2["disclosure"] == "2025-11-04"
    assert v2["no_lookahead"] is True
    fired = {(row["chain"], row["pair"], row["fired_on"], row["lead_days"]) for row in v2["fired"]}
    assert ("arbitrum", "USDC/xUSD", "2025-10-29", 6) in fired
    assert ("ethereum", "USDC/sdeUSD", "2025-10-29", 6) in fired
    assert v2["usd3"]["covered"] is True
    assert v2["usd3"]["history_starts"] == "2025-11-20"
    assert v2["usd3"]["fired_on"] is None
    assert v2["usd3"]["state"] == "Watching"
    assert v2["euler"]["readable"] is False
    assert v2["false_fires_per_vault_year"] is None
    supply = v2["new_supply_over_fire_day"]
    assert supply["arbitrum:xUSD"] == "0.000093"
    assert supply["ethereum:sdeUSD"] == "0.325786"
    assert supply["ethereum:deUSD"] == "0.000263"
    text = json.dumps(result)
    assert "$" not in text
    assert '"usd"' not in text.lower()


def test_onboarding_uses_the_owner_and_refuses_a_short_timelock(tmp_path):
    token = "0x" + "e5" * 20
    data = collateral_id_data(token)
    assert data is not None
    assert cap_id_hex(data) == "0xd9f9e6671459365711313800dce57d4eebe1922bbb096ae6de8605c5f17c0338"
    curator = onboard_vault(caller_role="curator", increase_delay=MIN_INCREASE_DELAY_S, token=token)
    assert curator["accepted"] is False
    assert curator["reason"] == "not_owner"
    assert curator["caller"] == "owner"
    assert curator["grant"] == "setIsSentinel"
    missing = onboard_vault(caller_role="owner", increase_delay=None, token=token)
    assert missing["reason"] == "timelock_unreadable"
    default = onboard_vault(caller_role="owner", increase_delay=0, token=token)
    assert default["accepted"] is False
    assert default["reason"] == "timelock_below_72h"
    almost = onboard_vault(caller_role="owner", increase_delay=MIN_INCREASE_DELAY_S - 1, token=token)
    assert almost["reason"] == "timelock_below_72h"
    accepted = onboard_vault(caller_role="owner", increase_delay=MIN_INCREASE_DELAY_S, token=token)
    assert accepted["accepted"] is True
    assert accepted["caller"] == "owner"
    assert accepted["id_data"] == "0x" + data.hex()
    assert accepted["cap_id"] == cap_id_hex(data)
    calls: list = []
    path = str(tmp_path / "audit.jsonl")
    base = dict(
        vault="v", id_data=accepted["id_data"], broadcaster=calls.append, audit_path=path,
        proposals=[{"source": "rxb", "vault": "v", "id_data": accepted["id_data"],
                    "absolute": 0, "relative": None, "readable": True}],
    )
    refused = plan_cap_down(
        chain_id=31337, current_absolute=100, current_relative=None,
        env={"RXB_ENABLED": "1"}, increase_delay=0, **base,
    )
    assert refused["sent"] is False
    assert refused["reason"] == "timelock_below_72h"
    assert calls == []
    sent = plan_cap_down(
        chain_id=31337, current_absolute=100, current_relative=None,
        env={"RXB_ENABLED": "1"}, increase_delay=MIN_INCREASE_DELAY_S, **base,
    )
    assert sent["sent"] is True
    assert calls == [[{
        "kind": "absolute", "cap": 0, "id_data": accepted["id_data"], "cap_id": accepted["cap_id"],
    }]]


def test_euler_ethereum_keeps_a_failed_oracle_out_of_the_sum():
    fixture = json.loads(
        (ROOT / "bot/rxb/fixtures/euler_ethereum_xusd_2026-10-23.json").read_text(encoding="utf-8")
    )
    asset = fixture["asset"]
    seen: list[str] = []

    def transport(url: str) -> dict | None:
        seen.append(url)
        if url == euler_vaults_url(asset):
            return fixture["list"]
        address = url.rsplit("/", 1)[-1]
        return fixture["details"].get(address)

    book = fetch_euler_ethereum(transport, asset=asset)
    assert book["readable"] is True
    assert book["chain"] == "ethereum"
    assert book["oracle_complete"] is False
    assert book["oracle_omitted"] == ["exUSD-1"]
    by_symbol = {row["symbol"]: row for row in book["vaults"]}
    assert by_symbol["exUSD-1"]["oracle_value"] is None
    assert by_symbol["exUSD-1"]["assets"] == "4154656"
    assert by_symbol["exUSD-2"]["oracle_value"].startswith("16100173.")
    assert book["oracle_value"] == by_symbol["exUSD-2"]["oracle_value"]
    assert book["quoted_supply_value"] != book["oracle_value"]
    assert book["quoted_supply_complete"] is True
    dead = fetch_euler_ethereum(lambda _url: None, asset=asset)
    assert dead["readable"] is False
    assert dead["vaults"] is None
    assert dead["oracle_value"] is None
    empty = fetch_euler_ethereum(
        lambda _url: {"data": [], "meta": {"total": 0, "timestamp": "2026-10-23T00:00:00Z"}},
        asset=asset,
    )
    assert empty["readable"] is True
    assert empty["vaults"] == []
    assert empty["oracle_value"] == "0"
    missed: list[str] = []

    def record(url: str) -> dict | None:
        missed.append(url)
        return None

    unread = fetch_euler_ethereum(record)
    assert unread["readable"] is False
    assert unread["vaults"] is None
    assert missed and asset.lower() in missed[0].lower()
    assert "hidden" in missed[0] and "pending_review" in missed[0]
    assert "chainId=1" in missed[0]
