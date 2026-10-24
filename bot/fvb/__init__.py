"""Fair-value bridge. Testnet only. Off unless ``FVB_ENABLED`` is set.

The public label is ``Public signals, not advice.`` Prices are feed
units. Importing this package does not sign and does not read a key.
"""
from __future__ import annotations

from bot.fvb.adapters import (
    aggregator_round,
    clamp_to_external,
    euler_price,
    morpho_price,
    oracle_proposal,
)
from bot.fvb.band import band_around, quantile_of, sigma_of
from bot.fvb.blend import fair_value, full_external_coefficient, session_floor, shares
from bot.fvb.board import empty_board, load_board, save_board
from bot.fvb.book import book_weight, depth_of, n_eff_and_cr1, slippage_cost
from bot.fvb.chains import assert_testnet_chain, assert_testnet_cluster, public_chains
from bot.fvb.collateral import (
    borrowed_asset_marks,
    collateral_marks,
    invariant_holds,
    oi_scale_ratio,
    perp_mark,
)
from bot.fvb.config import FvbConfig
from bot.fvb.euler import liquidate_enabled
from bot.fvb.flag import flag_on, gate
from bot.fvb.keccak import keccak256
from bot.fvb.packet import public_signal, scale_price
from bot.fvb.pricer import Pricer
from bot.fvb.render import card_text
from bot.fvb.replay import deep_book, run_sk_hynix, run_weekends, sk_prior, weekend_gaps
from bot.fvb.sign import (
    commit_pubkey,
    ed25519_message,
    eip712_digest,
    packet_words,
    require_scheme,
    session_code,
    signature_ok,
    symbol_word,
)

__all__ = [
    "FvbConfig",
    "Pricer",
    "aggregator_round",
    "assert_testnet_chain",
    "assert_testnet_cluster",
    "band_around",
    "book_weight",
    "borrowed_asset_marks",
    "card_text",
    "clamp_to_external",
    "collateral_marks",
    "commit_pubkey",
    "deep_book",
    "depth_of",
    "ed25519_message",
    "eip712_digest",
    "empty_board",
    "euler_price",
    "fair_value",
    "flag_on",
    "full_external_coefficient",
    "gate",
    "invariant_holds",
    "keccak256",
    "liquidate_enabled",
    "load_board",
    "morpho_price",
    "n_eff_and_cr1",
    "oi_scale_ratio",
    "oracle_proposal",
    "packet_words",
    "perp_mark",
    "public_chains",
    "public_signal",
    "quantile_of",
    "require_scheme",
    "run_sk_hynix",
    "run_weekends",
    "save_board",
    "scale_price",
    "session_code",
    "session_floor",
    "shares",
    "sigma_of",
    "signature_ok",
    "sk_prior",
    "slippage_cost",
    "symbol_word",
    "weekend_gaps",
]
