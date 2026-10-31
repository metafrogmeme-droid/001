"""Claw Guard per-order checks. Testnet, flag off, admin while it is off.

N1–N40 are the review's cases. Wallet actions that the order path cannot
see return no verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from bot.claw_guard.evaluate import OUT_OF_SCOPE, evaluate
from bot.claw_guard.flag import flag_on
from bot.claw_guard.keys import HL_KEY_ENV
from bot.claw_guard.network import (
    HL_TESTNET_CHAIN_ID,
    HL_TESTNET_EXCHANGE,
    PM_PAPER,
    PM_TEST_CHAIN_ID,
    endpoint_refusal,
)
from bot.claw_guard.service import check_body
from bot.claw_guard.signer import approve, bot_submit, new_session, reject_resubmit, submit_intent
from bot.claw_guard.verdicts import ALLOW_TEXT, BLOCK_PREFIX, CONFIRM_PREFIX, blocked, needs_ok
from tests.source_scan import code_only

NOW = 1_700_000_000.0
ROOT = Path(__file__).resolve().parents[1]
KEY = "k" * 48
ENV = {"CLAW_GUARD_ENABLED": "1", HL_KEY_ENV: KEY}


def _rules(**overrides):
    base = {
        "allowlist": ["BTC", "ETH"],
        "leverage_cap": 5,
        "size_confirm_frac": 0.25,
        "size_block_frac": 1.0,
        "builder_max_f": 0,
        "builder_allowlist": [],
        "pm_builder_allowlist": [],
        "own_30d_5min_max": 3,
        "price_confirm_frac": 0.02,
        "price_block_frac": 0.04,
    }
    base.update(overrides)
    return base


def _acct(**overrides):
    base = {
        "equity_usd": 10000.0,
        "positions": {
            "BTC": {
                "market": "BTC", "underlying": "BTC", "size": 0.05,
                "side": "long", "has_stop": False,
            },
        },
        "markets": {
            "BTC": {
                "mark": 100000.0, "mark_age_s": 1.0,
                "best_bid": 99950.0, "best_ask": 100050.0,
            },
            "ETH": {"mark": 3000.0, "mark_age_s": 1.0},
            "SOL-PERP": {"mark": 150.0, "mark_age_s": 1.0},
            "xyz:BTC": {"mark": 100000.0, "mark_age_s": 1.0},
        },
        "meta": {
            0: {"name": "BTC", "kind": "perp", "underlying": "BTC"},
            1: {"name": "ETH", "kind": "perp", "underlying": "ETH"},
            2: {"name": "SOL-PERP", "kind": "perp", "underlying": "SOL"},
            110000: {"name": "xyz:BTC", "kind": "hip3", "underlying": "BTC"},
        },
        "signers": {
            "bot-1": {
                "first_seen_ts": NOW - 30 * 86400, "deregistered": False, "orders": 100,
            },
        },
        "recent_orders": [],
        "recent_closes": [],
        "recent_exits": [],
        "rules": _rules(),
        "subaccounts": {},
        "holdings": {},
        "deregistered_signers": [],
    }
    base.update(overrides)
    return base


def _hl(**overrides):
    base = {
        "venue": "hyperliquid",
        "type": "order",
        "market": "BTC",
        "side": "buy",
        "size": 0.01,
        "price": 100100.0,
        "reduce_only": False,
        "signer": "bot-1",
    }
    base.update(overrides)
    return base


def _pm_account(n=4, **overrides):
    holdings = {}
    markets = {}
    for i in range(n):
        name = f"M{i + 1}"
        holdings[name] = {"shares": 1000.0}
        markets[name] = {
            "mid": 0.41, "mid_age_s": 1.0, "best_bid": 0.40, "best_ask": 0.42,
            "depth": 5000.0, "tick_size": 0.01,
        }
    base = _acct(holdings=holdings, markets=markets, positions={})
    base["signers"] = {
        "pm-old": {
            "first_seen_ts": NOW - 30 * 86400, "orders": 10, "sells": 5, "scope": "CLOB",
        },
    }
    base.update(overrides)
    return base


def _pm(**overrides):
    base = {
        "venue": "polymarket",
        "type": "order",
        "market": "M1",
        "side": "sell",
        "size": 1000.0,
        "price": 0.40,
        "signer": "pm-old",
    }
    base.update(overrides)
    return base


def _texts(result):
    return [row["text"] for row in result["rules"]]


def _has(result, sentence):
    return sentence == result["text"] or sentence in _texts(result)


def test_the_flag_is_off_by_default_and_admin_only_while_off():
    assert flag_on({}) is False
    assert flag_on({"CLAW_GUARD_ENABLED": ""}) is False
    assert flag_on({"CLAW_GUARD_ENABLED": "yes"}) is False
    assert flag_on({"CLAW_GUARD_ENABLED": "on"}) is False
    assert flag_on({"CLAW_GUARD_ENABLED": "1"}) is True
    assert flag_on({"CLAW_GUARD_ENABLED": " TRUE "}) is True
    from bot.admin_preview import public_on
    assert public_on("CLAW_GUARD_ENABLED", env={}) is False
    assert public_on("CLAW_GUARD_ENABLED", env={"CLAW_GUARD_ENABLED": "1"}) is True
    closed = submit_intent(_hl(), _acct(), now=NOW, caller_is_admin=False, env={})
    assert closed["refused"] is True
    assert closed["signed"] is False
    preview = submit_intent(_hl(), _acct(), now=NOW, caller_is_admin=True, env={})
    assert preview["refused"] is False
    assert preview["decision"] == "ALLOW"
    assert preview["text"] == ALLOW_TEXT
    assert preview["signed"] is False


def test_mainnet_endpoints_and_chain_ids_are_refused():
    assert endpoint_refusal(HL_TESTNET_EXCHANGE, HL_TESTNET_CHAIN_ID) is None
    assert endpoint_refusal(PM_PAPER, PM_TEST_CHAIN_ID) is None
    for url in (
        "https://api.hyperliquid.xyz/exchange",
        "https://api.hyperliquid.xyz/info",
        "https://clob.polymarket.com",
        "https://clob.polymarket.com/order",
    ):
        assert endpoint_refusal(url, None) is not None
        result = evaluate(_hl(endpoint=url), _acct(), now=NOW)
        assert result["decision"] == "BLOCK"
        assert _has(result, blocked("mainnet is refused."))
        signed = submit_intent(
            _hl(endpoint=url), _acct(), now=NOW, caller_is_admin=True, env=ENV,
        )
        assert signed["signed"] is False
    for cid in (1, 137, 42161, 8453):
        assert endpoint_refusal(HL_TESTNET_EXCHANGE, cid) is not None
        result = evaluate(_hl(chain_id=cid), _acct(), now=NOW)
        assert result["decision"] == "BLOCK"
    assert evaluate(_hl(chain="Mainnet"), _acct(), now=NOW)["decision"] == "BLOCK"
    assert evaluate(_hl(chain_id=HL_TESTNET_CHAIN_ID), _acct(), now=NOW)["decision"] == "ALLOW"


def test_verdict_strings_exist_only_as_the_three_sentences():
    banned = ("sa" + "fe", "sc" + "am")
    package = ROOT / "bot" / "claw_guard"
    for path in package.rglob("*.py"):
        code = code_only(path.read_text(encoding="utf-8"))
        for phrase in (ALLOW_TEXT, BLOCK_PREFIX, CONFIRM_PREFIX, "tap to allow once"):
            if phrase in code:
                assert path.name == "verdicts.py", path
        lowered = code.lower()
        for word in banned:
            assert word not in lowered, path
    for path in (ROOT / "app" / "routes" / "claw_guard_orders.js", ROOT / "app" / "lib" / "claw_guard_flag.js"):
        code = code_only(path.read_text(encoding="utf-8")).lower()
        for word in banned:
            assert word not in code
        assert ALLOW_TEXT not in code
        assert "tap to allow once" not in code
    samples = [
        evaluate(_hl(), _acct(), now=NOW),
        evaluate(_hl(size=0.03, price=100000.0), _acct(), now=NOW),
        evaluate(_hl(size=0.25, price=100000.0), _acct(), now=NOW),
        evaluate({"venue": "polymarket", "type": "convert"}, _pm_account(), now=NOW),
    ]
    for result in samples:
        if result["decision"] is None:
            assert result["text"] == ""
            continue
        if result["decision"] == "ALLOW":
            assert result["text"] == ALLOW_TEXT
        elif result["decision"] == "BLOCK":
            assert result["text"].startswith(BLOCK_PREFIX)
        else:
            assert result["text"].startswith(CONFIRM_PREFIX)
        for row in result["rules"]:
            if row["level"] == "block":
                assert row["text"].startswith(BLOCK_PREFIX)
            else:
                assert row["text"].startswith(CONFIRM_PREFIX)
            lowered = row["text"].lower()
            for word in banned:
                assert word not in lowered


def test_the_trade_key_is_read_only_by_the_guard_and_never_returned():
    for path in (ROOT / "bot").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if HL_KEY_ENV in text or "CLAW_GUARD_PM_SESSION_KEY" in text:
            assert path.as_posix().endswith("bot/claw_guard/keys.py")
    sent = []
    result = submit_intent(
        _hl(), _acct(), now=NOW, caller_is_admin=True, env=ENV,
        poster=lambda payload, signature: sent.append((payload, signature)),
    )
    assert result["signed"] is True
    assert result["sent"] is True
    blob = json.dumps(result)
    assert KEY not in blob
    assert KEY not in result["signature"]
    blocked_order = submit_intent(
        _hl(size=0.25, price=100000.0), _acct(), now=NOW,
        caller_is_admin=True, env=ENV,
        poster=lambda payload, signature: sent.append("blocked"),
    )
    assert blocked_order["decision"] == "BLOCK"
    assert blocked_order["signed"] is False
    assert "blocked" not in sent


def test_n1_five_buys_inside_ten_minutes():
    account = _acct()
    recent = []
    for i in range(4):
        result = evaluate(
            _hl(size=0.025, price=100000.0),
            {**account, "recent_orders": list(recent)},
            now=NOW + i,
        )
        assert result["decision"] == "ALLOW", result
        assert result["text"] == ALLOW_TEXT
        recent.append({
            "ts": NOW + i, "notional_usd": 2500.0, "risk_increasing": True, "oid": str(i),
        })
    fifth = evaluate(
        _hl(size=0.025, price=100000.0),
        {**account, "recent_orders": recent},
        now=NOW + 4,
    )
    assert fifth["decision"] == "BLOCK"
    assert _has(fifth, blocked(
        "orders in the last 10 min would use 125% of account value (max 100%).",
    ))


def test_n2_batch_is_one_exposure():
    orders = [
        {"market": "BTC", "side": "buy", "size": 0.03, "price": 100000.0, "reduce_only": False}
        for _ in range(4)
    ]
    result = evaluate(_hl(orders=orders, size=0.03, price=100000.0), _acct(), now=NOW)
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("this batch would use 120% of account value (max 100%)."))


def test_n3_close_then_reopen_is_a_reversal():
    first = evaluate(
        _hl(side="sell", size=0.05, price=100000.0, reduce_only=True), _acct(), now=NOW,
    )
    assert first["decision"] == "ALLOW"
    assert first["text"] == ALLOW_TEXT
    account = _acct(positions={}, recent_closes=[{
        "market": "BTC", "ts": NOW, "closed_side": "long",
    }])
    second = evaluate(
        _hl(side="sell", size=0.10, price=100000.0, reduce_only=False), account, now=NOW + 20,
    )
    assert second["decision"] == "CONFIRM"
    assert _has(second, needs_ok(
        "this reverses your BTC position from long to short within 10 minutes.",
    ))


def test_n4_modify_that_flips():
    confirm = evaluate(
        _hl(type="modify", side="sell", size=0.15, price=100000.0, reduce_only=False),
        _acct(), now=NOW,
    )
    assert confirm["decision"] == "CONFIRM"
    assert _has(confirm, needs_ok(
        "this order closes your long and opens a short of 0.10 BTC.",
    ))
    block = evaluate(
        _hl(type="modify", side="sell", size=0.20, price=100000.0, reduce_only=False),
        _acct(), now=NOW,
    )
    assert block["decision"] == "BLOCK"
    assert _has(block, blocked(
        "one order may use at most 100% of account value (this one: 150%).",
    ))


def test_n5_batch_modify_size():
    result = evaluate(
        _hl(type="batchModify", side="buy", size=0.30, price=100000.0, reduce_only=False),
        _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked(
        "one order may use at most 100% of account value (this one: 350%).",
    ))


def test_n6_modify_price():
    result = evaluate(
        _hl(type="modify", side="sell", size=0.05, price=94000.0, reduce_only=True),
        _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("sell price 6.0% below mark (max 4%)."))


def test_n7_trigger_that_would_flip():
    result = evaluate(
        _hl(
            type="trigger", side="sell", size=0.15, price=99000.0,
            trigger_px=99000.0, reduce_only=False,
        ),
        _acct(), now=NOW,
    )
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok(
        "if triggered, this order closes your long and opens a short of 0.10 BTC.",
    ))


def test_n8_stop_and_ladder_are_each_within_the_position():
    account = _acct()
    for size in (0.05, 0.025, 0.015, 0.01):
        result = evaluate(
            _hl(side="sell", size=size, price=100000.0, reduce_only=True), account, now=NOW,
        )
        assert result["decision"] == "ALLOW", (size, result)
        assert result["text"] == ALLOW_TEXT


def test_n9_twap_size():
    result = evaluate(
        _hl(type="twapOrder", side="buy", size=0.30, price=100000.0), _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked(
        "one order may use at most 100% of account value (this one: 300%).",
    ))


def test_n10_trailing_stop_that_would_flip():
    result = evaluate(
        _hl(type="trailingStop", side="sell", size=0.15, price=100000.0, reduce_only=False),
        _acct(), now=NOW,
    )
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok(
        "if triggered, this order closes your long and opens a short of 0.10 BTC.",
    ))


def test_n11_update_leverage():
    allowed = evaluate(_hl(type="updateLeverage", leverage=5), _acct(), now=NOW)
    assert allowed["decision"] == "ALLOW"
    assert allowed["text"] == ALLOW_TEXT
    blocked_row = evaluate(_hl(type="updateLeverage", leverage=6), _acct(), now=NOW)
    assert blocked_row["decision"] == "BLOCK"
    assert _has(blocked_row, blocked("max leverage 5x (requested 6x)."))


def test_n12_removing_isolated_margin():
    result = evaluate(
        _hl(type="updateIsolatedMargin", resulting_leverage=8), _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("max leverage 5x (this change: 8x)."))


def test_n13_cross_to_isolated_at_twenty():
    result = evaluate(
        _hl(type="updateLeverage", leverage=20, isCross=False), _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("max leverage 5x (requested 20x)."))


def test_n14_vault_address_uses_the_sub_account():
    account = _acct(subaccounts={"0xsub": {"equity_usd": 1000.0, "positions": {}}})
    result = evaluate(
        _hl(size=0.005, price=100000.0, vaultAddress="0xsub"), account, now=NOW,
    )
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok("one order would use 50% of account value (above 25%)."))


def test_n15_unknown_actions_are_refused():
    for kind in ("usdSend", "withdraw3", "sendAsset", "spotSend", "notARealAction"):
        result = evaluate(_hl(type=kind), _acct(), now=NOW)
        assert result["decision"] == "BLOCK"
        assert _has(result, blocked("this key may only place and cancel orders."))


def test_n16_a_replaced_key_cannot_be_used():
    account = _acct(deregistered_signers=["0xabc"])
    result = evaluate(_hl(signer="0xAbC"), account, now=NOW)
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("this API key was replaced and can't be used again."))


def test_n17_a_rotated_address_is_new():
    account = _acct()
    account["signers"] = {
        "0xnew": {"first_seen_ts": NOW - 600, "orders": 0, "deregistered": False},
    }
    result = evaluate(_hl(signer="0xnew", size=0.01, price=100000.0), account, now=NOW)
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok("first orders from an API key added 10 min ago."))


def test_n18_a_kill_switch_tag_does_not_skip_the_close_burst():
    def run(prior, origin="user"):
        recent = [
            {"ts": NOW, "closing": True, "oid": str(i), "origin": "user"}
            for i in range(prior)
        ]
        account = _acct(recent_orders=recent)
        action = _hl(
            side="sell", size=0.05, price=100000.0, reduce_only=True,
            client_tag="admin_closeall", cloid="admin_closeall",
        )
        return evaluate(action, account, now=NOW + 30, origin=origin)

    assert run(6)["decision"] == "ALLOW"
    assert run(7)["decision"] == "CONFIRM"
    assert _has(run(7), needs_ok("8 closes in 5 minutes, more than your limit of 7."))
    assert run(8)["decision"] == "CONFIRM"
    assert run(8, origin="kill_switch")["decision"] == "ALLOW"


def test_n19_and_n20_builder_fee_units():
    rules = _rules(builder_allowlist=["0xbuilder"], builder_max_f=10)
    account = _acct(rules=rules)
    sub = evaluate(_hl(
        builder={"b": "0xbuilder", "f": 5, "s": [{"b": "0xunknown", "s": 50000}]},
    ), account, now=NOW)
    assert sub["decision"] == "BLOCK"
    assert _has(sub, blocked("builder fee goes to an address not on your list."))
    allowed = evaluate(_hl(builder={"b": "0xbuilder", "f": 10}), account, now=NOW)
    assert allowed["decision"] == "ALLOW"
    over = evaluate(_hl(builder={"b": "0xbuilder", "f": 11}), account, now=NOW)
    assert over["decision"] == "BLOCK"
    assert _has(over, blocked("builder fee 1.1 bps, your max is 1 bps."))


def test_n21_ioc_far_from_mark():
    result = evaluate(
        _hl(side="sell", size=0.05, price=50000.0, tif="Ioc", reduce_only=False),
        _acct(), now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("sell price 50.0% below mark (max 4%)."))


def test_n22_price_and_thin_book():
    account = _acct()
    account["markets"] = dict(account["markets"])
    account["markets"]["BTC"] = {
        "mark": 100000.0, "mark_age_s": 1.0, "best_bid": 95000.0, "best_ask": 100050.0,
    }
    far = evaluate(
        _hl(side="sell", size=0.05, price=94500.0, reduce_only=True), account, now=NOW,
    )
    assert far["decision"] == "BLOCK"
    assert _has(far, blocked("sell price 5.5% below mark (max 4%)."))
    thin = evaluate(
        _hl(side="sell", size=0.05, price=99000.0, reduce_only=True), account, now=NOW,
    )
    assert thin["decision"] == "CONFIRM"
    assert _has(thin, needs_ok("this sell would fill up to 5% below mark (thin book)."))


def test_n23_expiry_is_ten_seconds_and_a_later_resubmit_is_rejected():
    result = submit_intent(_hl(), _acct(), now=NOW, caller_is_admin=True, env=ENV)
    assert result["signed"] is True
    assert result["payload"]["expiresAfter"] <= NOW + 10
    assert reject_resubmit(result["payload"], NOW + 60) is True
    assert reject_resubmit(result["payload"], NOW) is False


def test_n24_and_n25_asset_ids():
    hip3 = evaluate(_hl(asset_id=110000, market="BTC"), _acct(), now=NOW)
    assert hip3["decision"] == "BLOCK"
    assert _has(hip3, blocked("xyz:BTC is not on your list."))
    other = evaluate(_hl(asset_id=2, market="BTC"), _acct(), now=NOW)
    assert other["decision"] == "BLOCK"
    assert _has(other, blocked("SOL-PERP is not on your list."))


def test_n26_gross_underlying():
    account = _acct(rules=_rules(allowlist=["BTC", "ETH", "xyz:BTC"]))
    account["positions"] = {
        "BTC": {"market": "BTC", "underlying": "BTC", "size": 0.06, "side": "long"},
    }
    result = evaluate(
        _hl(market="xyz:BTC", side="buy", size=0.05, price=100000.0), account, now=NOW,
    )
    assert result["decision"] == "BLOCK"
    assert _has(result, blocked("total BTC exposure 110% of account value (max 100%)."))


def test_n27_removing_the_last_stop():
    account = _acct()
    account["positions"]["BTC"]["has_stop"] = True
    account["signers"] = {
        "bot-1": {"first_seen_ts": NOW - 3600, "orders": 1, "deregistered": False},
    }
    cancel = evaluate(
        _hl(type="cancel", market="BTC", stop=True, signer="bot-1"), account, now=NOW,
    )
    assert cancel["decision"] == "CONFIRM"
    assert _has(cancel, needs_ok("this removes the last stop on your BTC position."))
    scheduled = evaluate(
        _hl(type="scheduleCancel", market="BTC", signer="bot-1"), account, now=NOW,
    )
    assert scheduled["decision"] == "CONFIRM"
    assert _has(scheduled, needs_ok("this removes the last stop on your BTC position."))


def test_n28_a_down_guard_sends_nothing_and_the_kill_switch_still_flattens():
    down = bot_submit(
        _hl(size=0.03, price=100000.0), _acct(), now=NOW,
        caller_is_admin=True, guard_up=False, env=ENV,
    )
    assert down["not_sent"] is True
    assert down["signed"] is False
    assert down["sent"] is False
    flatten = submit_intent(
        _hl(side="sell", size=0.05, price=100000.0, reduce_only=True),
        _acct(), now=NOW, caller_is_admin=True, env=ENV, origin="kill_switch",
    )
    assert flatten["decision"] == "ALLOW"
    assert flatten["signed"] is True
    assert flatten["waited"] is False


def test_n29_stale_prices_block_a_buy_and_not_a_reduce_only_close():
    account = _acct()
    account["markets"] = dict(account["markets"])
    account["markets"]["BTC"] = {
        "mark": 100000.0, "mark_age_s": 25.0, "best_bid": 99950.0, "best_ask": 100050.0,
    }
    close = evaluate(
        _hl(side="sell", size=0.05, price=100000.0, reduce_only=True), account, now=NOW,
    )
    assert close["decision"] == "ALLOW"
    assert close["text"] == ALLOW_TEXT
    buy = evaluate(_hl(size=0.01, price=100000.0), account, now=NOW)
    assert buy["decision"] == "BLOCK"
    assert _has(buy, blocked("price data is older than 10 s."))


def test_n30_merges_count_as_exits():
    account = _pm_account(4)
    account["signers"]["pm-new"] = {
        "first_seen_ts": NOW - 300, "orders": 0, "sells": 0, "scope": "CLOB",
    }
    exits = []
    decisions = []
    for name in ("M1", "M2", "M3"):
        account["recent_exits"] = list(exits)
        result = evaluate(
            {
                "venue": "polymarket", "type": "merge", "market": name,
                "size": 1000.0, "signer": "pm-new",
            },
            account, now=NOW,
        )
        decisions.append(result["decision"])
        exits.append({"market": name, "ts": NOW, "value": 400.0})
    assert decisions == ["ALLOW", "ALLOW", "BLOCK"]
    assert _has(result, blocked(
        "closing most positions in 3 markets in 10 min from a session started 5 min ago.",
    ))


def test_n31_complement_buys_count_as_exits():
    account = _pm_account(12)
    account["signers"]["pm-new"] = {
        "first_seen_ts": NOW - 300, "orders": 0, "sells": 0, "scope": "CLOB",
    }
    exits = []
    last = None
    for name in ("M1", "M2", "M3"):
        account["recent_exits"] = list(exits)
        last = evaluate(
            _pm(market=name, side="buy", size=1000.0, price=0.40, signer="pm-new", complement=True),
            account, now=NOW,
        )
        exits.append({"market": name, "ts": NOW, "value": 400.0})
    assert last["decision"] == "BLOCK"
    assert _has(last, blocked(
        "closing most positions in 3 markets in 10 min from a session started 5 min ago.",
    ))


def test_n32_and_n36_wallet_actions_have_no_verdict():
    for kind in ("convert", "negRiskConvert", "setApprovalForAll", "transfer"):
        result = evaluate(
            {"venue": "polymarket", "type": kind, "signer": "pm-old"}, _pm_account(), now=NOW,
        )
        assert result["decision"] is None
        assert result["text"] == ""
        assert result["out_of_scope"] == OUT_OF_SCOPE
        assert result["rules"] == []


def test_n33_an_unanswered_confirm_blocks_after_sixty_seconds():
    session = new_session()
    action = _hl(size=0.03, price=100000.0)
    account = _acct()
    first = submit_intent(
        action, account, now=NOW, caller_is_admin=True, env=ENV, session=session,
    )
    assert first["decision"] == "CONFIRM"
    assert first["waited"] is True
    assert first["signed"] is False
    late = submit_intent(
        action, account, now=NOW + 60, caller_is_admin=True, env=ENV, session=session,
    )
    assert late["decision"] == "BLOCK"
    assert _has(late, blocked("no OK received within 60 s."))
    assert late["signed"] is False


def test_approval_is_single_use_admin_only_and_rechecked():
    session = new_session()
    action = _hl(size=0.03, price=100000.0)
    account = _acct()
    first = submit_intent(
        action, account, now=NOW, caller_is_admin=True, env=ENV, session=session,
    )
    assert approve(session, first["order_hash"], NOW + 1, admin=False) is None
    token = approve(session, first["order_hash"], NOW + 1, admin=True)
    assert token is not None
    stranger = dict(token)
    stranger["order_hash"] = "0" * 64
    missed = submit_intent(
        action, account, now=NOW + 2, caller_is_admin=True, env=ENV,
        session=session, approval=stranger,
    )
    assert missed["signed"] is False
    smaller = _acct(equity_usd=2000.0)
    changed = submit_intent(
        action, smaller, now=NOW + 3, caller_is_admin=True, env=ENV,
        session=session, approval=token,
    )
    assert changed["decision"] == "BLOCK"
    assert changed["signed"] is False
    session2 = new_session()
    submit_intent(action, account, now=NOW, caller_is_admin=True, env=ENV, session=session2)
    token2 = approve(session2, first["order_hash"], NOW + 1, admin=True)
    ok = submit_intent(
        action, account, now=NOW + 2, caller_is_admin=True, env=ENV,
        session=session2, approval=token2,
    )
    assert ok["signed"] is True
    again = submit_intent(
        action, account, now=NOW + 3, caller_is_admin=True, env=ENV,
        session=session2, approval=token2,
    )
    assert again["signed"] is False


def test_a_reduce_only_close_and_a_cancel_do_not_wait():
    recent = [
        {"ts": NOW, "closing": True, "oid": str(i), "origin": "user"} for i in range(7)
    ]
    burst = submit_intent(
        _hl(side="sell", size=0.05, price=100000.0, reduce_only=True),
        _acct(recent_orders=recent), now=NOW + 10, caller_is_admin=True, env=ENV,
    )
    assert burst["decision"] == "CONFIRM"
    assert burst["waited"] is False
    assert burst["signed"] is True
    account = _acct()
    account["positions"]["BTC"]["has_stop"] = True
    account["signers"] = {
        "bot-1": {"first_seen_ts": NOW - 3600, "orders": 1, "deregistered": False},
    }
    cancel = submit_intent(
        _hl(type="cancel", market="BTC", stop=True),
        account, now=NOW, caller_is_admin=True, env=ENV,
    )
    assert cancel["decision"] == "CONFIRM"
    assert cancel["waited"] is False
    assert cancel["signed"] is True
    scheduled = submit_intent(
        _hl(type="scheduleCancel", market="BTC"),
        account, now=NOW, caller_is_admin=True, env=ENV,
    )
    assert scheduled["decision"] == "CONFIRM"
    assert scheduled["waited"] is True
    assert scheduled["signed"] is False


def test_n34_wide_spread_uses_the_bid():
    account = _pm_account()
    account["markets"]["M1"] = {
        "mid": 0.40, "mid_age_s": 1.0, "best_bid": 0.10, "best_ask": 0.70,
        "depth": 5000.0, "tick_size": 0.01,
    }
    result = evaluate(_pm(price=0.12, size=100.0), account, now=NOW)
    assert result["decision"] == "ALLOW"
    assert result["text"] == ALLOW_TEXT


def test_n35_current_tick_is_used():
    account = _pm_account()
    account["markets"]["M1"] = {
        "mid": 0.41, "mid_age_s": 1.0, "best_bid": 0.40, "best_ask": 0.42,
        "depth": 5000.0, "tick_size": 0.001,
    }
    result = evaluate(_pm(side="buy", price=0.405, size=10.0), account, now=NOW)
    assert result["decision"] == "ALLOW"
    assert result["text"] == ALLOW_TEXT


def test_n37_a_resting_order_is_not_rechecked():
    result = check_body({"op": "resting", "now": NOW})
    assert result["decision"] is None
    assert result["text"] == ""
    assert result["out_of_scope"] == OUT_OF_SCOPE


def test_n38_first_large_sell_from_a_new_key():
    account = _pm_account(4)
    account["signers"]["pm-new"] = {
        "first_seen_ts": NOW - 600, "orders": 0, "sells": 0, "scope": "CLOB",
    }
    result = evaluate(_pm(signer="pm-new", size=1000.0, price=0.40), account, now=NOW)
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok("first large sell from a key added today."))


def test_n39_combo_scope_is_refused():
    for scope in ("COMBOSRFQ", "All"):
        result = evaluate(_pm(scope=scope), _pm_account(), now=NOW)
        assert result["decision"] == "BLOCK"
        assert _has(result, blocked("this key may only trade on the order book."))


def test_n40_the_third_market_completes_the_exit():
    account = _pm_account(12)
    account["signers"]["pm-new"] = {
        "first_seen_ts": NOW - 300, "orders": 0, "sells": 0, "scope": "CLOB",
    }
    exits = []
    first = evaluate(_pm(market="M1", signer="pm-new"), {**account, "recent_exits": []}, now=NOW)
    assert first["decision"] == "ALLOW"
    assert first["text"] == ALLOW_TEXT
    exits.append({"market": "M1", "ts": NOW, "value": 400.0})
    second = evaluate(
        _pm(market="M2", signer="pm-new"), {**account, "recent_exits": list(exits)}, now=NOW + 10,
    )
    assert second["decision"] == "ALLOW"
    exits.append({"market": "M2", "ts": NOW + 10, "value": 400.0})
    third = evaluate(
        _pm(market="M3", signer="pm-new"), {**account, "recent_exits": list(exits)}, now=NOW + 20,
    )
    assert third["decision"] == "BLOCK"
    assert _has(third, blocked(
        "closing most positions in 3 markets in 10 min from a session started 5 min ago.",
    ))


def test_pm_price_bounds_depth_and_builder_code():
    account = _pm_account()
    near = evaluate(_pm(price=0.40), account, now=NOW)
    assert near["decision"] == "ALLOW"
    confirm = evaluate(_pm(price=0.33), account, now=NOW)
    assert confirm["decision"] == "CONFIRM"
    assert _has(confirm, needs_ok("sell at 0.33 when mid is 0.41."))
    far = evaluate(_pm(price=0.12), account, now=NOW)
    assert far["decision"] == "BLOCK"
    assert _has(far, blocked("sell at 0.12 when mid is 0.41."))
    bounds = evaluate(_pm(side="buy", price=1.02, size=10.0), account, now=NOW)
    assert bounds["decision"] == "BLOCK"
    assert _has(bounds, blocked("price must be between 0 and 1."))
    thin_account = _pm_account()
    thin_account["markets"]["M1"] = dict(thin_account["markets"]["M1"])
    thin_account["markets"]["M1"]["depth"] = 1000.0
    thin = evaluate(_pm(size=800.0, price=0.40), thin_account, now=NOW)
    assert thin["decision"] == "CONFIRM"
    assert _has(thin, needs_ok("this order is bigger than half the book near mid."))
    code = "0x" + "ab" * 32
    refused = evaluate(_pm(builder=code, size=10.0, price=0.40), account, now=NOW)
    assert refused["decision"] == "BLOCK"
    assert _has(refused, blocked("builder code is not on your list."))
    zero = "0x" + "00" * 32
    assert evaluate(_pm(builder=zero, size=10.0, price=0.40), account, now=NOW)["decision"] == "ALLOW"


def test_t2_and_a_price_inside_the_confirm_band():
    confirm = evaluate(_hl(size=0.03, price=100000.0), _acct(), now=NOW)
    assert confirm["decision"] == "CONFIRM"
    assert _has(confirm, needs_ok("one order would use 30% of account value (above 25%)."))
    band = evaluate(
        _hl(side="sell", size=0.05, price=97500.0, reduce_only=True), _acct(), now=NOW,
    )
    assert band["decision"] == "CONFIRM"
    assert _has(band, needs_ok("sell price 2.5% below mark (max 4%)."))


def test_a_learned_confirm_line_only_tightens():
    loose = _acct(rules=_rules(history_days=10, learned_confirm_frac=0.50))
    assert evaluate(_hl(size=0.02, price=100000.0), loose, now=NOW)["decision"] == "ALLOW"
    tight = _acct(rules=_rules(history_days=14, learned_confirm_frac=0.10))
    result = evaluate(_hl(size=0.02, price=100000.0), tight, now=NOW)
    assert result["decision"] == "CONFIRM"


def test_an_oversized_reduce_only_on_the_right_side_is_allowed():
    result = evaluate(
        _hl(side="sell", size=0.10, price=100000.0, reduce_only=True), _acct(), now=NOW,
    )
    assert result["decision"] == "ALLOW"
    wrong = evaluate(
        _hl(side="buy", size=0.01, price=100000.0, reduce_only=True), _acct(), now=NOW,
    )
    assert wrong["decision"] == "BLOCK"
    assert _has(wrong, blocked("reduce-only orders may not exceed your position."))


def test_the_confirm_path_hands_the_intent_to_the_guard():
    source = (ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
    assert "from bot.claw_guard.door import accept_intent" in source
    assert "guard_intent" in source


def test_old_key_exit_on_the_third_market_confirms():
    account = _pm_account(12)
    exits = [
        {"market": "M1", "ts": NOW, "value": 400.0},
        {"market": "M2", "ts": NOW, "value": 400.0},
    ]
    result = evaluate(
        _pm(market="M3", signer="pm-old"),
        {**account, "recent_exits": exits},
        now=NOW + 20,
    )
    assert result["decision"] == "CONFIRM"
    assert _has(result, needs_ok("closing most positions in 3 markets in 10 min."))
