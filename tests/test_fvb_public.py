"""Public payload, flag, and chain list."""
from __future__ import annotations

import json

from bot.fvb.board import load_board, save_board
from bot.fvb.chains import (
    MAINNET_CHAIN_IDS,
    TEST_NETWORKS,
    MainnetRefused,
    assert_testnet_chain,
    public_chains,
)
from bot.fvb.euler import OP_FLASHLOAN, OP_LIQUIDATE, liquidate_enabled
from bot.fvb.flag import flag_on, gate
from bot.fvb.packet import LABEL, PUBLIC_KEYS, PayloadRefused, public_signal
from bot.fvb.render import BANNER, card_text
from bot.fvb.replay import run_sk_hynix


def test_flag_is_off_unless_explicitly_on():
    assert flag_on({}) is False
    assert flag_on({"FVB_ENABLED": "1"}) is True
    assert flag_on({"FVB_ENABLED": " true "}) is True
    assert flag_on({"FVB_ENABLED": "yes"}) is False
    assert flag_on({"FVB_ENABLED": "0"}) is False
    assert gate(False, False) == "deny"
    assert gate(False, True) == "preview"
    assert gate(True, False) == "open"


def test_flag_off_stays_closed_for_the_public_and_opens_for_an_admin():
    """The door is flag_on_for. Off is 404 for everyone who is not an admin."""
    from bot.admin_preview import flag_on_for, is_preview

    assert flag_on_for("999", "FVB_ENABLED", env={}, store={}) is False
    assert is_preview("999", "FVB_ENABLED", env={}, store={}) is False
    assert flag_on_for(
        "999", "FVB_ENABLED", env={"FVB_ENABLED": "1"}, store={}) is True
    assert is_preview(
        "999", "FVB_ENABLED", env={"FVB_ENABLED": "1"}, store={}) is False
    admin = {"ADMIN_TELEGRAM_IDS": "1"}
    assert flag_on_for("1", "FVB_ENABLED", env=admin, store={}) is True
    assert is_preview("1", "FVB_ENABLED", env=admin, store={}) is True
    assert flag_on_for("2", "FVB_ENABLED", env=admin, store={}) is False
    assert flag_on_for(
        "7", "FVB_ENABLED", env={}, store={"7": {"role": "admin"}}) is True
    assert flag_on_for(
        "7", "FVB_ENABLED", env={}, store={"7": {"role": "trader"}}) is False


def test_public_chains_are_the_testnets_that_actually_list_the_product():
    chains = public_chains()
    names = [row["chain"] for row in chains]
    assert names == ["hyperliquid", "solana", "ethereum", "robinhood"]
    blob = json.dumps(chains).lower()
    assert "base" not in blob
    assert "arbitrum" not in blob
    assert "$" not in blob
    ids = {net.chain: net.chain_id for net in TEST_NETWORKS}
    assert ids["ethereum"] == 11155111
    assert ids["robinhood"] == 46630
    assert ids["hyperliquid"] == 998
    for refused in (1, 8453, 42161, 999, 4663):
        assert refused in MAINNET_CHAIN_IDS
        try:
            assert_testnet_chain(refused)
        except MainnetRefused:
            continue
        raise AssertionError(refused)
    assert_testnet_chain(11155111)
    assert_testnet_chain(46630)


def test_euler_liquidate_is_bit_11_not_bit_12():
    assert OP_LIQUIDATE == 1 << 11
    assert OP_LIQUIDATE != 1 << 12
    assert OP_LIQUIDATE.bit_length() - 1 == 11
    assert OP_FLASHLOAN == 1 << 12
    assert liquidate_enabled(OP_LIQUIDATE) is True
    assert liquidate_enabled(OP_FLASHLOAN) is False
    assert liquidate_enabled(0) is False


def test_public_row_is_allowlisted_and_has_no_dollar_shape():
    reading = run_sk_hynix()[-1]
    row = public_signal({
        "symbol": reading.symbol,
        "session": reading.session,
        "confirmed": reading.confirmed,
        "forced": reading.forced,
        "fv": reading.fv,
        "band_lo": reading.band_lo,
        "band_hi": reading.band_hi,
        "external": reading.external,
        "last_trade": reading.last_trade,
        "w_ext": reading.w_ext,
        "n_eff_min": reading.n_eff_min,
        "borrow_power": reading.borrow_power,
        "liquidation": reading.liquidation,
        "oi_scale": reading.oi_scale,
        "ts": reading.ts,
        "unit": reading.unit,
        "basis": reading.basis,
        "pnl": 1,
        "notional_usd": 5,
    })
    assert set(row) == set(PUBLIC_KEYS)
    assert "pnl" not in row
    assert "notional_usd" not in row
    assert row["label"] == LABEL
    assert row["v"] == 1
    encoded = json.dumps(row)
    assert "$" not in encoded
    assert "rclaw" not in encoded.lower()
    assert row["ts"] != 0


def test_a_banned_string_is_refused():
    try:
        public_signal({"basis": "price is $5"})
    except PayloadRefused:
        return
    raise AssertionError("dollar sign was accepted")


def test_missing_board_is_not_a_zero_price(tmp_path):
    missing = load_board(tmp_path / "absent.json")
    assert missing["available"] is False
    assert missing["reason"] == "not_published"
    assert missing["rows"] == []
    text = card_text(missing, preview=True)
    assert BANNER in text
    assert LABEL in text
    assert "not published" in text.lower()
    assert "$" not in text
    save_board(tmp_path / "board.json", [{
        "symbol": "EQ",
        "session": "regular",
        "confirmed": True,
        "forced": False,
        "fv": 100.0,
        "band_lo": 99.0,
        "band_hi": 101.0,
        "external": 100.0,
        "last_trade": 100.0,
        "w_ext": 0.9,
        "n_eff_min": 8.0,
        "borrow_power": 100.0,
        "liquidation": 100.0,
        "oi_scale": 0.8,
        "ts": 80.0,
        "unit": "index",
        "basis": "weekend replay",
    }])
    loaded = load_board(tmp_path / "board.json")
    assert loaded["available"] is True
    assert loaded["label"] == LABEL
    chains = loaded["chains"]
    assert isinstance(chains, list)
    assert all(row["chain"] != "base" for row in chains)
    open_card = card_text(loaded, preview=False)
    assert BANNER not in open_card
    assert LABEL in open_card
