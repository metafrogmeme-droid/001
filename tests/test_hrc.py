"""Kink Cap: the curve, the truncated precompile word, the feed, the cap rule.

Testnet bytes decode and do not calibrate. A missing read is not a zero.
The signing key is not read unless the confirmed chain is HyperEVM testnet.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from bot.caps.strictest import (
    BATCH_3,
    CapRefused,
    apply_cap_down,
    effective_cap,
    empty_book,
    load_book,
)
from bot.hrc.backtest import build_report, replay
from bot.hrc.collector import (
    collect_hour,
    combine_readings,
    eth_call_precompile,
    http_transport,
    load_history,
)
from bot.hrc.corewriter_probe import encode_action, load_record
from bot.hrc.feed import (
    SigningRefused,
    address_of,
    feed_digest,
    recover_address,
    sign_digest,
    sign_feed,
    verify_feed,
)
from bot.hrc.flag import flag_on
from bot.hrc.index import (
    CalibrationRefused,
    accept_sample,
    calibrate,
    decode_precompile,
)
from bot.hrc.keccak import keccak256
from bot.hrc.model import (
    DEFAULT_SPREAD_CEIL,
    FUNDING_BASELINE,
    KIND_CAP,
    KIND_DIGITAL,
    KIND_SPREAD,
    PER_USER_BORROW_CAP,
    baseline_crossover,
    borrow_apy,
    borrow_apy_matching_funding,
    can_unfreeze,
    cap_hit_pays,
    carry_strike,
    choose_settlement,
    collateral_for,
    collateral_for_series,
    crossover_utilization,
    default_carry_strike,
    freeze_reason,
    hour_payoff,
    points_below,
    quote_matches,
    quoted_rate_matches,
    rate_shock_loss_24h,
    replay_withdrawal,
    supply_to_hold,
    trailing_mean,
    truncation_tolerance,
    utilization_for_borrow_apy,
    utilization_of,
)
from bot.hrc.present import admin_packet, card_text, dashboard
from bot.hrc.rfq import premium_fraction
from bot.hrc.store import columns, ensure, postgres_ddl, record_quote, record_sample
from bot.skills.hrc_commands import HrcCommands

ROOT = Path(__file__).resolve().parent.parent
MAINNET = json.loads((ROOT / "tests/fixtures/hrc_mainnet_precompile.json").read_text())
SYNTHETIC = json.loads((ROOT / "tests/fixtures/hrc_testnet_synthetic.json").read_text())

_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


class Spy(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads: list[tuple[str, str]] = []

    def __getitem__(self, key):
        self.reads.append(("item", key))
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.reads.append(("get", key))
        return super().get(key, default)


def _saw(env: Spy, key: str) -> bool:
    return any(name == key for _, name in env.reads)


def _collect_mod():
    path = ROOT / "scripts" / "hrc_collect.py"
    spec = importlib.util.spec_from_file_location("hrc_collect_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _info(url_must):
    def transport(url, body):
        assert body == {"type": "allBorrowLendReserveStates"}
        assert url_must in url
        assert body.get("method", "")[:8] != "eth_send"
        row = dict(MAINNET["info_api_different_read"])
        row["token"] = 0
        return [row]
    return transport


# ── flag ─────────────────────────────────────────────────────────────────────

def test_only_1_and_true_turn_the_flag_on():
    assert flag_on({"HRC_ENABLED": "1"}) is True
    assert flag_on({"HRC_ENABLED": "true"}) is True
    assert flag_on({"HRC_ENABLED": "TRUE"}) is True
    assert flag_on({"HRC_ENABLED": "yes"}) is False
    assert flag_on({"HRC_ENABLED": "on"}) is False
    assert flag_on({"HRC_ENABLED": "0"}) is False
    assert flag_on({}) is False


# ── curve ────────────────────────────────────────────────────────────────────

def test_checkpoints_and_a_missing_utilization_is_not_the_base_rate():
    assert borrow_apy(Decimal("0.85")) == Decimal("0.2875")
    assert borrow_apy(Decimal("0.90")) == Decimal("0.525")
    assert borrow_apy(Decimal(1)) == Decimal(1)
    assert borrow_apy(Decimal("1.2")) == Decimal(1)
    assert borrow_apy(Decimal("0.5")) == Decimal("0.05")
    assert borrow_apy(Decimal(0)) == Decimal("0.05")
    assert borrow_apy(None) is None
    assert utilization_of(0, 1) is None
    assert utilization_of(True, 1) is None  # type: ignore[arg-type]


def test_the_funding_baseline_is_11_6_percent_and_the_crossover_is_derived():
    src = (ROOT / "bot/hrc/model.py").read_text(encoding="utf-8")
    assert "0.1095" not in src
    assert "0.8139" not in src
    assert "81.39" not in src
    got = baseline_crossover()
    assert got == utilization_for_borrow_apy(FUNDING_BASELINE)
    assert default_carry_strike() == FUNDING_BASELINE == Decimal("0.116")
    pct = (got * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert pct == Decimal("81.39")
    # A continuous print still goes through exp. The baseline does not.
    printed = crossover_utilization(Decimal("0.1095"))
    assert printed == utilization_for_borrow_apy(borrow_apy_matching_funding(Decimal("0.1095")))
    assert printed != got


def test_truncation_tolerance_accepts_the_real_word_and_rejects_two_bp():
    assert truncation_tolerance() == Decimal("0.0002")
    words = MAINNET["precompile"]["words"]
    supplied, borrowed, rate_bps, util_word = words[6], words[7], words[0], words[3]
    assert (borrowed * 10000) % supplied != 0
    assert (borrowed * 10000) // supplied == util_word == 7773
    util = utilization_of(supplied, borrowed)
    assert quote_matches(util, rate_bps) is True
    assert borrow_apy(util) == Decimal("0.05")
    raw = b"".join(int(w).to_bytes(32, "big") for w in words)
    accepted = accept_sample(decode_precompile(raw))
    assert accepted["formula_rate"] == Decimal("0.05")
    assert accepted["utilization_bps"] == 7773

    above_s, above_b = 1_000_000, 820_099
    above = utilization_of(above_s, above_b)
    assert quote_matches(above, 1453) is True
    assert quote_matches(above, 1452) is False
    assert quote_matches(above, 1450) is False
    exact = borrow_apy(above)
    assert quoted_rate_matches(above, exact - Decimal("0.000199")) is True
    assert quoted_rate_matches(above, exact - Decimal("0.0002")) is False


def test_a_short_or_oversized_word_is_not_a_sample_of_zeros():
    assert decode_precompile(b"\x00" * 32) is None
    assert decode_precompile(b"\x00" * 256) is not None
    wide = bytearray(b"\x00" * 256)
    wide[31] = 0
    wide[0] = 1  # top of the first word, so the word is >= 2^64
    assert decode_precompile(bytes(wide)) is None
    decoded = decode_precompile(b"".join(int(w).to_bytes(32, "big") for w in SYNTHETIC["words"]))
    assert decoded is not None
    assert SYNTHETIC["synthetic"] is True
    with pytest.raises(CalibrationRefused):
        calibrate({"synthetic": True, "network": "testnet", "formula_rate": Decimal("0.05")})
    with pytest.raises(CalibrationRefused):
        calibrate({"synthetic": False, "network": "testnet", "formula_rate": Decimal("0.05")})


def test_settlement_takes_the_lower_rate_and_pays_zero_when_both_are_missing():
    low, high = Decimal("0.05"), Decimal("0.40")
    assert choose_settlement(None, None) == {"rate": None, "source": "none", "payout_zero": True}
    assert choose_settlement(low, None)["source"] == "precompile"
    assert choose_settlement(None, high)["source"] == "signed_feed"
    assert choose_settlement(low, low)["source"] == "precompile"
    gap = choose_settlement(high, low)
    assert gap["source"] == "lower_of"
    assert gap["rate"] == low
    assert gap["payout_zero"] is False
    assert choose_settlement("nope", None)["source"] == "none"  # type: ignore[arg-type]
    edge = choose_settlement(Decimal("0.05"), Decimal("0.0502"))
    assert edge["source"] == "lower_of"
    assert choose_settlement(Decimal("0.05"), Decimal("0.0501"))["source"] == "precompile"


def test_a_short_window_or_a_missing_hour_is_not_a_mean_of_zero():
    assert trailing_mean([Decimal("0.2")] * 5) is None
    assert trailing_mean([Decimal("0.2")] * 5 + [None]) is None
    assert trailing_mean([Decimal("0.2")] * 6) == Decimal("0.2")
    assert hour_payoff(8760, None, Decimal("0.1")) is None
    assert hour_payoff(8760, Decimal("0.1"), Decimal("0.2")) == 0
    assert hour_payoff(8760, Decimal(1), Decimal(0)) == 1
    assert points_below(None, Decimal(1)) is None
    assert points_below(Decimal("0.8"), Decimal("0.8")) == Decimal(0)


def test_payoff_stays_inside_the_deposit_and_a_zero_margin_still_covers():
    assert carry_strike(None, Decimal(0)) is None
    assert carry_strike(Decimal(0), Decimal(0)) == Decimal(0)
    assert collateral_for(8760, Decimal(0), 10) == 10
    import random
    rng = random.Random(812)
    for _ in range(80):
        notional = rng.randint(1, 10**9)
        hours = rng.randint(0, 400)
        strike = Decimal(rng.randint(0, 10000)) / Decimal(10000)
        deposit = collateral_for(notional, strike, hours)
        pay = hour_payoff(notional, Decimal(1), strike)
        assert deposit is not None and pay is not None
        assert pay * hours <= deposit


def test_cap_hit_and_shock_keep_unread_apart_from_zero():
    assert cap_hit_pays(None, 100) is None
    assert cap_hit_pays(0, 100) is False
    assert cap_hit_pays(97, 100) is False
    assert cap_hit_pays(98, 100) is True
    assert rate_shock_loss_24h(None) is None
    assert rate_shock_loss_24h(Decimal(0)) == Decimal(0)
    assert rate_shock_loss_24h(Decimal(1000000)) == Decimal(12600000) / Decimal(8760)


def test_replay_uses_one_unit_and_does_not_return_the_withdrawal():
    supplied, borrowed, withdraw = 551760966, 430819345, 21_500_000
    row = replay_withdrawal(supplied, borrowed, withdraw)
    assert set(row) == {"readable", "utilization", "borrow_apy"}
    util = Decimal(borrowed) / Decimal(supplied - withdraw)
    assert row["utilization"] == util
    assert row["borrow_apy"] == borrow_apy(util)
    larger = replay_withdrawal(supplied, borrowed, 50_000_000)
    assert larger["utilization"] > row["utilization"]
    assert larger["borrow_apy"] == borrow_apy(larger["utilization"])
    assert replay_withdrawal(supplied, borrowed, supplied)["reason"] == "withdrawal_exceeds_supply"
    assert replay_withdrawal(None, borrowed, 1)["readable"] is False  # type: ignore[arg-type]


# ── feed ─────────────────────────────────────────────────────────────────────

def test_keccak_matches_the_empty_and_abc_digests():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


def test_a_signature_recovers_and_two_signers_are_required():
    assert address_of(1) == "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
    digest = feed_digest(998, 4, 1_000_000, 100_000, 500, 0)
    assert digest != feed_digest(999, 4, 1_000_000, 100_000, 500, 0)
    assert digest != feed_digest(998, 5, 1_000_000, 100_000, 500, 0)
    keys = (2, 3, 4)
    sigs = [sign_digest(k, digest) for k in keys]
    for r, s, v in sigs:
        assert s <= _N // 2
        assert recover_address(digest, r, _N - s, v) is None
    signers = [address_of(k) for k in keys]
    assert verify_feed(digest, sigs[:2], signers) is True
    assert verify_feed(digest, [sigs[0], sigs[0]], signers) is False
    assert verify_feed(digest, [sigs[0]], signers) is False
    stranger = sign_digest(9, digest)
    assert verify_feed(digest, [sigs[0], stranger], signers) is False
    fixture = json.loads((ROOT / "contracts/kinkcap/test/sig.json").read_text(encoding="utf-8"))
    pinned = feed_digest(
        fixture["chain_id"], fixture["hour"], fixture["supplied"],
        fixture["borrowed"], fixture["rate_bps"], fixture["funding_wad"],
    )
    assert "0x" + pinned.hex() == fixture["digest"]
    got = recover_address(pinned, int(fixture["r"], 16), int(fixture["s"], 16), fixture["v"])
    assert got == fixture["address"]


def test_the_key_is_read_only_after_the_chain_is_testnet():
    sample = {
        "hour": 4,
        "supplied": 1_000_000,
        "borrowed": 100_000,
        "rate_bps": 500,
        "funding_apr": Decimal(0),
    }
    mainnet = Spy({"HRC_FEED_SIGNER_KEY": "02"})
    with pytest.raises(SigningRefused):
        sign_feed(sample, mainnet, 999)
    assert not _saw(mainnet, "HRC_FEED_SIGNER_KEY")
    unset = Spy({"HRC_FEED_SIGNER_KEY": "02"})
    with pytest.raises(SigningRefused):
        sign_feed(sample, unset, None)
    assert not _saw(unset, "HRC_FEED_SIGNER_KEY")
    missing_funding = Spy({"HRC_FEED_SIGNER_KEY": "02"})
    with pytest.raises(SigningRefused):
        sign_feed({**sample, "funding_apr": None}, missing_funding, 998)
    assert not _saw(missing_funding, "HRC_FEED_SIGNER_KEY")
    env = Spy({"HRC_FEED_SIGNER_KEY": "02"})
    signed = sign_feed(sample, env, 998)
    assert _saw(env, "HRC_FEED_SIGNER_KEY")
    assert signed["address"] == address_of(2)
    assert signed["chain_id"] == 998
    assert signed["s"] <= _N // 2


# ── collector ────────────────────────────────────────────────────────────────

def test_testnet_is_synthetic_and_is_not_a_calibration():
    row = collect_hour(_info("hyperliquid-testnet"), network="testnet", hour=2)
    assert row["readable"] is True
    assert row["synthetic"] is True
    assert row["source"] == "info_api"
    with pytest.raises(CalibrationRefused):
        calibrate(row)


def test_mainnet_info_is_not_labelled_synthetic_and_a_failure_names_the_class():
    row = collect_hour(_info("api.hyperliquid.xyz"), network="mainnet", hour=2)
    assert row["synthetic"] is False
    assert row["source"] == "info_api"
    assert row["formula_rate"] == Decimal("0.05")

    def boom(url, body):
        raise TimeoutError("secret-token-should-not-surface")

    failed = collect_hour(boom, network="mainnet", hour=2)
    assert failed["readable"] is False
    assert failed["reason"] == "TimeoutError"
    assert "secret-token" not in json.dumps(failed)
    absent = collect_hour(lambda url, body: [{"token": 7}], network="mainnet", hour=2)
    assert absent["reason"] == "absent"
    assert "supplied" not in absent


def test_eth_call_is_a_call_and_a_send_is_refused():
    words = MAINNET["precompile"]["words"]
    raw = "0x" + b"".join(int(w).to_bytes(32, "big") for w in words).hex()
    seen = {}

    def transport(url, body):
        seen["body"] = body
        return {"result": raw}

    got = eth_call_precompile("http://example.invalid", 0, transport)
    assert seen["body"]["method"] == "eth_call"
    assert seen["body"]["params"][0]["to"].endswith("0812")
    assert got["readable"] is True
    assert got["formula_rate"] == Decimal("0.05")
    with pytest.raises(RuntimeError):
        http_transport("http://example.invalid", {"method": "eth_sendRawTransaction", "params": []})


def test_disagreeing_reads_settle_at_the_lower_rate():
    info = {
        "readable": True,
        "formula_rate": Decimal("0.05"),
        "supplied": "100",
        "borrowed": "50",
        "unit": "human",
        "network": "mainnet",
        "synthetic": False,
        "hour": 1,
        "source": "info_api",
    }
    pre = {
        "readable": True,
        "formula_rate": Decimal("0.40"),
        "supplied": 1000,
        "borrowed": 870,
        "oracle_px": 1,
    }
    lower = combine_readings(info, pre)
    assert lower["source"] == "lower_of"
    assert lower["formula_rate"] == Decimal("0.05")
    close = combine_readings(
        {**info, "formula_rate": Decimal("0.05001")},
        {**pre, "formula_rate": Decimal("0.05")},
    )
    assert close["source"] == "precompile"
    assert close["unit"] == "raw"
    neither = combine_readings({"readable": False, "reason": "absent"}, {"readable": False})
    assert neither["readable"] is False
    assert neither["source"] == "none"
    assert "formula_rate" not in neither


def test_a_missing_history_file_is_not_an_empty_book(tmp_path):
    missing = load_history(str(tmp_path / "absent.json"))
    assert missing["readable"] is False
    assert missing["reason"] == "no_history"
    assert "supplied" not in missing
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    bad = load_history(str(tmp_path / "bad.json"))
    assert bad["readable"] is False
    assert bad["reason"] == "unreadable"


def test_run_on_mainnet_does_not_read_the_signing_key():
    mod = _collect_mod()
    env = Spy({
        "HRC_CHAIN_ID": "999",
        "HRC_FEED_SIGNER_KEY": "02",
        "HRC_HOUR": "3",
    })
    out = mod.run(env, _info("api.hyperliquid.xyz"), 3)
    assert out["signed"] is None
    assert out["reading"]["synthetic"] is False
    assert not _saw(env, "HRC_FEED_SIGNER_KEY")
    unset = Spy({"HRC_FEED_SIGNER_KEY": "02"})
    out = mod.run(unset, _info("api.hyperliquid.xyz"), 3)
    assert out["signed"] is None
    assert out["reading"]["network"] == "mainnet"
    assert not _saw(unset, "HRC_FEED_SIGNER_KEY")
    testnet = Spy({"HRC_CHAIN_ID": "998", "HRC_FEED_SIGNER_KEY": "02"})
    out = mod.run(testnet, _info("hyperliquid-testnet"), 3)
    assert out["reading"]["synthetic"] is True
    assert out["signed"] is None
    assert not _saw(testnet, "HRC_FEED_SIGNER_KEY")


# ── caps ─────────────────────────────────────────────────────────────────────

def test_the_strictest_proposal_wins_and_absent_is_not_zero(tmp_path):
    assert BATCH_3 == ("RXB", "HRC", "T2C")
    book = empty_book()
    assert effective_cap(book, "usdc") is None
    apply_cap_down(book, "HRC", "usdc", Decimal(10))
    assert book["log"][-1]["previous"] is None
    assert book["log"][-1]["effective_after"] == Decimal(10)
    apply_cap_down(book, "CGC", "usdc", Decimal(4))
    assert effective_cap(book, "usdc") == Decimal(4)
    assert book["proposals"]["usdc"]["HRC"] == Decimal(10)
    before = len(book["log"])
    for bad in (("NOPE", Decimal(1)), ("CGC", Decimal(4)), ("CGC", Decimal("-1")), ("RXB", None)):
        with pytest.raises(CapRefused):
            apply_cap_down(book, bad[0], "usdc", bad[1])
    assert len(book["log"]) == before
    apply_cap_down(book, "RXB", "usdc", 0)
    assert effective_cap(book, "usdc") == Decimal(0)
    fresh = empty_book()
    apply_cap_down(fresh, "T2C", "usdc", 0)
    assert fresh["log"][-1]["previous"] is None
    assert effective_cap(fresh, "usdc") == Decimal(0)
    missing = load_book(str(tmp_path / "no-book.json"))
    assert missing["readable"] is True
    assert effective_cap(missing, "usdc") is None
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    corrupt = load_book(str(tmp_path / "bad.json"))
    assert corrupt["readable"] is False
    with pytest.raises(CapRefused):
        apply_cap_down(corrupt, "HRC", "usdc", Decimal(1))


# ── present and the command ──────────────────────────────────────────────────

def test_the_dashboard_shows_rates_and_the_card_drops_the_shock():
    reading = {
        "readable": True,
        "network": "mainnet",
        "synthetic": False,
        "source": "info_api",
        "unit": "human",
        "supplied": "551760966",
        "borrowed": "430819345",
        "oracle": "1",
    }
    view = dashboard(reading)
    assert "rate_shock_loss_24h" not in view
    blob = json.dumps(view)
    assert "$" not in blob
    assert "21500000" not in blob
    assert "50000000" not in blob
    assert view["borrow_apy"] == "0.05"
    assert "funding_crossover" not in view
    printed = dict(reading)
    printed["funding_apr"] = "0.1095"
    shown = dashboard(printed)
    assert shown["funding_crossover"] == format(crossover_utilization(Decimal("0.1095")), "f")
    assert shown["points_to_crossover"] != shown["funding_crossover"]
    scenario = view["replays"]["scenario_a"]
    util = Decimal(430819345) / Decimal(551760966 - 21_500_000)
    assert Decimal(scenario["utilization"]) == util
    assert Decimal(scenario["borrow_apy"]) == borrow_apy(util)
    packet = admin_packet(reading)
    shock = packet["rate_shock_loss_24h"]
    assert shock is not None
    text = card_text(packet)
    assert shock not in text
    assert "$" not in text
    assert "rate_shock" not in text
    assert "Mainnet read." in text
    unread = card_text(admin_packet({"readable": False, "reason": "no_history"}))
    assert "Unreadable (no_history)." in unread
    assert "0%" not in unread
    zero = dashboard({
        "readable": True, "network": "mainnet", "synthetic": False, "source": "precompile",
        "unit": "human", "supplied": "100", "borrowed": "0", "oracle": "1",
    })
    assert zero["utilization"] == "0"
    assert zero["borrow_apy"] == "0.05"


def test_series_terms_appear_only_when_the_reading_carries_them():
    bare = dashboard({
        "readable": True, "network": "mainnet", "synthetic": False, "source": "precompile",
        "unit": "human", "supplied": "100", "borrowed": "50", "oracle": "1",
    })
    for key in ("hour_payoff_tokens", "collateral_tokens", "freeze", "can_unfreeze"):
        assert key not in bare
    struck = dashboard({
        "readable": True, "network": "mainnet", "synthetic": False, "source": "precompile",
        "unit": "human", "supplied": "100", "borrowed": "50", "oracle": "1",
        "strike": "0.04", "rates": ["0.05", "0.05", "0.05", "0.05", "0.05", "0.05"],
        "notional": 10_000_000, "hours": 24,
        "previous_hour": 10, "hour": 13, "formula_ok": True, "model_ok": True,
        "frozen_at": 10, "now_hour": 12, "good_hours": [11, 12], "guardian": True,
    })
    assert struck["freeze"] == "gap"
    assert struck["can_unfreeze"] is True
    assert int(struck["hour_payoff_tokens"]) > 0
    assert int(struck["collateral_tokens"]) > 0
    held = dashboard({
        "readable": True, "network": "mainnet", "synthetic": False, "source": "precompile",
        "unit": "human", "supplied": "100", "borrowed": "50", "oracle": "1",
        "strike": "0.20", "rates": ["0.05", "0.05", "0.05", "0.05", "0.05", "0.05"],
        "notional": 10_000_000, "hours": 24, "hour": 11, "formula_ok": True, "model_ok": True,
    })
    assert held["hour_payoff_tokens"] == "0"
    assert "freeze" not in held


def test_a_sample_db_stores_the_hour_and_leaves_a_missing_util_null(tmp_path):
    mod = _collect_mod()
    db = tmp_path / "samples.sqlite"
    reading = {
        "readable": True,
        "source": "info_api",
        "supplied": "100",
        "borrowed": "50",
        "formula_rate": Decimal("0.05"),
        "ts_utc": "2026-10-23T20:00:00Z",
    }
    assert mod.persist_sample({"HRC_SAMPLE_DB": str(db)}, reading) is True
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT util, borrow_rate, total_supplied FROM reserve_samples"
    ).fetchone()
    assert row[0] is None
    assert row[1] == "0.05"
    assert row[2] == "100"
    quoted = dict(reading)
    quoted["premium"] = None
    assert mod.persist_sample({
        "HRC_SAMPLE_DB": str(db),
        "HRC_QUOTE_SERIES": "1",
        "HRC_QUOTE_SIDE": "sell",
        "HRC_QUOTE_NOTIONAL": "10",
    }, quoted) is True
    premium = conn.execute("SELECT premium FROM quotes").fetchone()
    assert premium == (None,)
    assert mod.persist_sample({}, reading) is False


def test_an_unreadable_quote_is_not_a_zero_premium():
    assert premium_fraction(None, Decimal("0.1"), 24) is None
    assert premium_fraction(Decimal("0.1"), Decimal("0.2"), 24) == Decimal(0)


def test_the_command_is_not_found_for_anyone_else_and_previews_for_an_admin(tmp_path, monkeypatch):
    history = tmp_path / "history.json"
    history.write_text(json.dumps({
        "readable": True,
        "network": "mainnet",
        "synthetic": False,
        "source": "precompile",
        "unit": "human",
        "supplied": "10000000",
        "borrowed": "1000000",
        "oracle": "1",
    }), encoding="utf-8")
    monkeypatch.setenv("HRC_HISTORY_PATH", str(history))
    monkeypatch.setenv("HRC_ENABLED", "yes")

    class Host:
        def __init__(self, admin):
            self.admin = admin
            self.sent = []

        def _is_admin(self, update):
            return self.admin

        async def _send(self, update, text, reply_markup=None, edit=False):
            self.sent.append(text)

    stranger = Host(False)
    stranger._cmd_hrc = HrcCommands._cmd_hrc.__get__(stranger, Host)
    asyncio.run(stranger._cmd_hrc(None, None))
    assert stranger.sent == ["Not found."]
    assert "$" not in stranger.sent[0]

    called = {}
    real = rate_shock_loss_24h

    def wrapped(borrows):
        called["borrows"] = borrows
        return real(borrows)

    monkeypatch.setattr("bot.hrc.present.rate_shock_loss_24h", wrapped)
    admin = Host(True)
    admin._cmd_hrc = HrcCommands._cmd_hrc.__get__(admin, Host)
    asyncio.run(admin._cmd_hrc(None, None))
    assert called["borrows"] == Decimal(1000000)
    assert "Admin preview. Not public." in admin.sent[0]
    assert "$" not in admin.sent[0]
    shock = format(real(Decimal(1000000)), "f")
    assert shock not in admin.sent[0]

    monkeypatch.setenv("HRC_ENABLED", "1")
    admin.sent.clear()
    asyncio.run(admin._cmd_hrc(None, None))
    assert "Admin preview. Not public." not in admin.sent[0]
    assert "cap spread" in admin.sent[0]
    assert "0.40" in admin.sent[0]


def test_spread_collateral_uses_the_ceiling_and_a_digital_does_not_divide_by_the_year():
    assert collateral_for_series(KIND_CAP, 8760, Decimal(0), 10) == collateral_for(8760, Decimal(0), 10)
    assert collateral_for_series(KIND_SPREAD, 8760, Decimal("0.05"), 10, None) == 4
    assert collateral_for_series(KIND_SPREAD, 8760, Decimal("0.05"), 10, DEFAULT_SPREAD_CEIL) == 4
    assert collateral_for_series(KIND_DIGITAL, 10000, Decimal(0), 2, digital=Decimal("0.01")) == 200
    assert collateral_for_series(KIND_DIGITAL, 10000, Decimal(0), 2, digital=None) is None
    high = [(hour, Decimal("0.50")) for hour in range(8)]
    spread = replay(high, kind=KIND_SPREAD, strike=Decimal("0.10"), ceil=DEFAULT_SPREAD_CEIL)
    cap = replay(high, kind=KIND_CAP, strike=Decimal("0.10"))
    digital = replay(high, kind=KIND_DIGITAL, strike=Decimal("0.10"), digital=Decimal("0.01"))
    assert spread["pays"][5]["pay"] == Decimal("0.30") / Decimal(8760)
    assert cap["pays"][5]["pay"] == Decimal("0.40") / Decimal(8760)
    assert digital["pays"][5]["pay"] == Decimal("0.01")
    assert spread["pays"][4]["reason"] == "mean_unreadable"
    assert spread["pays"][4]["pay"] == Decimal(0)
    gapped = replay(
        [(0, Decimal("0.2")), (1, Decimal("0.2")), (4, Decimal("0.2"))],
        kind=KIND_CAP, strike=Decimal("0.05"),
    )
    assert gapped["frozen_at"] == 4
    assert [item["hour"] for item in gapped["pays"]] == [0, 1]


def test_a_freeze_needs_a_real_gap_and_unfreeze_needs_the_guardian():
    assert freeze_reason(None, 10, True, True) is None
    assert freeze_reason(10, 12, True, True) is None
    assert freeze_reason(10, 13, True, True) == "gap"
    assert freeze_reason(10, 11, False, True) == "formula"
    assert freeze_reason(10, 11, True, False) == "rate_model"
    assert freeze_reason(None, None, False, False) is None
    assert can_unfreeze(10, 12, [11, 12], True) is True
    assert can_unfreeze(10, 12, [11], True) is False
    assert can_unfreeze(10, 12, [11, 12], False) is False
    assert can_unfreeze(10, 35, [11, 12], True) is False
    held = supply_to_hold(Decimal(100), Decimal(0), Decimal("0.10"))
    assert held["reachable"] is False
    assert held["withdrawal"] is None
    already = supply_to_hold(Decimal(100), Decimal(50), Decimal("0.05"))
    assert already["withdrawal"] == Decimal(0)


def test_reserve_rows_keep_a_missing_rate_null_and_match_the_postgres_columns():
    pg = columns(postgres_ddl(), "reserve_samples")
    quotes = columns(postgres_ddl(), "quotes")
    assert pg == [
        "ts_utc", "src", "token", "total_supplied", "total_borrowed",
        "util", "borrow_rate", "supply_rate", "l1_block",
    ]
    assert quotes == ["id", "series_id", "side", "notional", "premium", "model_ver", "created_at"]
    conn = sqlite3.connect(":memory:")
    ensure(conn)
    sql_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(reserve_samples)").fetchall()
    ]
    assert sql_cols == pg
    assert record_sample(
        conn, ts_utc="2026-10-23T20:00:00Z", src="precompile", token=0,
        total_supplied=Decimal("100"), total_borrowed=Decimal("50"),
        util=None, borrow_rate=Decimal("0.05"), supply_rate=None, l1_block=None,
    ) is True
    assert record_sample(
        conn, ts_utc="2026-10-23T20:00:00Z", src="precompile", token=0,
        total_supplied=Decimal(0), total_borrowed=Decimal(0),
        util=Decimal(0), borrow_rate=Decimal(0), supply_rate=Decimal(0), l1_block=0,
    ) is False
    row = conn.execute(
        "SELECT util, supply_rate, l1_block FROM reserve_samples"
    ).fetchone()
    assert row == (None, None, None)
    assert record_quote(
        conn, series_id="1", side="sell", notional=Decimal(10), premium=None,
    ) is True
    stored = conn.execute("SELECT premium, model_ver FROM quotes").fetchone()
    assert stored[0] is None
    assert stored[1] == "HRCModel.v1"


def test_the_hourly_replay_and_the_corewriter_record_stay_off_the_settlement_path():
    report = build_report(
        ROOT / "tests/fixtures/hypebasis_usdc_hourly.csv",
        MAINNET,
    )
    saved = json.loads((ROOT / "benchmark/hrc/backtest_2026-10-23.json").read_text())
    assert saved == report
    assert report["readable_hours"] == 168
    assert report["collector"]["formula_rate"] == "0.05"
    assert report["collector"]["synthetic"] is False
    spread = report["payouts"]["spread_ceiling_0.40"]
    # Five captures clear 10%. One trailing mean does. The 20% strike never does.
    assert spread["0.10"]["paying_hours"] == 1
    assert spread["0.20"]["paying_hours"] == 0
    assert spread["0.04"]["accrued_hours"] == 142
    assert spread["0.04"]["mean_unreadable_hours"] == 21
    assert Decimal(spread["0.04"]["total_per_unit"]) > 0
    assert spread["0.04"]["total_per_unit"] == report["payouts"]["cap_ceiling_1"]["0.04"]["total_per_unit"]
    curve = {row["strike"]: row for row in report["manipulation"]["strikes"]}
    assert curve["0.05"]["withdrawal_tokens"] == "0"
    assert Decimal(curve["0.10"]["withdrawal_tokens"]) > 0
    assert curve["1.01"]["reachable"] is False
    assert curve["1.01"]["withdrawal_tokens"] is None
    assert report["manipulation"]["per_user_borrow_cap_tokens"] == "50000000"
    assert PER_USER_BORROW_CAP == Decimal(50_000_000)
    assert Decimal(curve["0.10"]["withdrawal_over_per_user_cap"]) > 0
    blob = json.dumps(report)
    assert "$" not in blob
    assert "total_supplied_usd" not in blob

    encoded = encode_action(0, 0, 1)
    assert encoded is not None and encoded[:4] == bytes([1, 0, 0, 15])
    assert encode_action(0, 0, 0) is None
    record = load_record()
    spec = importlib.util.spec_from_file_location(
        "hrc_replay_under_test", ROOT / "scripts" / "hrc_replay.py")
    replay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay_mod)
    assert replay_mod.record_encodes(record) is True
    assert replay_mod.record_encodes({"depended_on": True}) is False
    assert record["depended_on"] is False
    assert record["chain_id"] == 998
    assert record["supply"]["eth_call"] == "success"
    assert record["withdraw"]["eth_call"] == "success"
    market = (ROOT / "contracts/kinkcap/KinkCapMarket.sol").read_text(encoding="utf-8")
    assert "3333333333333333333333333333333333333333" not in market
    assert "PRECOMPILE_GAS = 20720" in market
    assert "staticcall{gas: PRECOMPILE_GAS}" in market
    assert "RAW_PER_TOKEN = 1e8" in market
    assert "FUNDING_BASELINE_BPS = 1160" in market
    assert "575e12" not in market
    assert "corewriter_action15" not in (ROOT / "bot/hrc/model.py").read_text(encoding="utf-8")
