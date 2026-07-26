"""Tests for the $RCLAW token-tier gate (bot/token/tier_gate.py).

Pins the invariants that make the gate safe to ship default-OFF:
  - disabled by default → never blocks (behavior byte-identical to today);
  - a confirmed insufficient balance blocks a premium feature when enabled;
  - an RPC/infra error fails OPEN (never locks a user out);
  - the balance→tier thresholds are honored and env-overridable.
The Solana RPC is monkeypatched — no network, no real chain.
"""

import importlib

import bot.token.tier_gate as tg

# Real 32-byte base58 pubkeys. The gate decodes stored wallets and denies
# anything that is not a valid pubkey, so placeholders like "W" no longer work.
WALLET = "So11111111111111111111111111111111111111112"
MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _rpc_stub(program_accounts=None, decimals=9, capture=None):
    """A method-aware _rpc fake.

    `_decimals()` now reads the mint via getAccountInfo, so a stub that only
    models getProgramAccounts breaks the moment a test touches staked_of.
    """
    def fake(method, params):
        if method == "getAccountInfo":
            return {"value": {"data": {"parsed": {"info": {"decimals": decimals}}}}}
        if capture is not None:
            capture["filters"] = params[1].get("filters")
        return program_accounts if program_accounts is not None else []
    return fake


def _reload_clean(monkeypatch, **env):
    """Reload the module with a controlled env so module-level reads are fresh."""
    for k in [
        "TOKEN_TIER_GATE_ENABLED", "RCLAW_MINT", "RCLAW_RPC_URL",
        "RCLAW_TIER_PRO_MIN", "RCLAW_TIER_ELITE_MIN",
        "RCLAW_STAKING_PROGRAM", "RCLAW_DECIMALS",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(tg)


class _Users:
    """Minimal UserStore stand-in exposing get_sol_wallet.

    ``verified`` defaults True so the pre-existing tier tests keep asserting the
    behaviour they were written for; the verification gate has its own tests
    below.
    """
    def __init__(self, wallet=None, verified=True):
        self._w = wallet
        self._v = verified

    def get_sol_wallet(self, uid):
        return self._w

    def is_sol_wallet_verified(self, uid):
        return bool(self._w) and self._v


class _LegacyUsers:
    """A store predating wallet verification — no is_sol_wallet_verified."""
    def __init__(self, wallet=None):
        self._w = wallet

    def get_sol_wallet(self, uid):
        return self._w


def test_disabled_by_default_never_blocks(monkeypatch):
    mod = _reload_clean(monkeypatch)
    assert mod.gate_enabled() is False
    # Even with a wealthy or absent user, disabled gate always allows.
    assert mod.allows_user(_Users(None), 1, "premium_scan") is True
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_enabled_requires_mint(monkeypatch):
    # Flag on but no mint → still inert.
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true")
    assert mod.gate_enabled() is False
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_tier_thresholds_and_overrides(monkeypatch):
    mod = _reload_clean(monkeypatch)
    assert mod.tier_for_balance(0) == "basic"
    assert mod.tier_for_balance(9_999) == "basic"
    assert mod.tier_for_balance(10_000) == "pro"
    assert mod.tier_for_balance(100_000) == "elite"
    # Env overrides.
    mod = _reload_clean(monkeypatch, RCLAW_TIER_PRO_MIN="500", RCLAW_TIER_ELITE_MIN="5000")
    assert mod.tier_for_balance(500) == "pro"
    assert mod.tier_for_balance(5_000) == "elite"


def test_no_wallet_blocks_when_enabled(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    assert mod.gate_enabled() is True
    # No linked wallet → cannot prove stake → blocked.
    assert mod.allows_user(_Users(None), 1, "premium_scan") is False


def test_sufficient_stake_allows(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_insufficient_stake_blocks(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 100.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_rpc_error_denies_without_a_recent_reading(monkeypatch):
    """Renamed from test_rpc_error_fails_open, and the assertion is inverted.

    It used to allow on ANY RPC error, which promoted users who had never held a
    token and did so for as long as the outage lasted. Grace is now bounded and
    conditional on a recent successful read — see the outage tests at the bottom.
    """
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_ungated_feature_allowed(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    assert mod.allows_user(_Users(None), 1, "not_a_gated_feature") is True


def test_balance_of_parses_rpc_result(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)

    def fake_rpc(method, params):
        assert method == "getTokenAccountsByOwner"
        return {"value": [
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 12.5}}}}}},
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 7.5}}}}}},
        ]}

    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    assert mod.balance_of(WALLET) == 20.0


def test_balance_of_none_without_wallet_or_mint(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    assert mod.balance_of(None) is None
    mod2 = _reload_clean(monkeypatch)  # no mint
    assert mod2.balance_of("W") is None


def test_non_devnet_rpc_raises_misconfigured(monkeypatch):
    """A non-devnet host is a PERMANENT fault and must not reach the fail-open path."""
    import pytest
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT=MINT,
        RCLAW_RPC_URL="https://api.mainnet-beta.solana.com",
    )
    with pytest.raises(mod.GateMisconfigured):
        mod._rpc("getVersion", [])


def test_mainnet_guard_is_not_a_substring_match(monkeypatch):
    """The old guard was `"mainnet" in url`. These all bypassed it."""
    import pytest
    for url in (
        "https://API.MAINNET-BETA.SOLANA.COM",      # uppercase
        "https://rpc.helius.xyz/?api-key=x",         # rebranded mainnet
        "https://alpha.quiknode.pro/abc/",           # rebranded mainnet
        "http://10.0.0.5:8899",                      # private validator
    ):
        mod = _reload_clean(
            monkeypatch, TOKEN_TIER_GATE_ENABLED="true",
            RCLAW_MINT=MINT, RCLAW_RPC_URL=url,
        )
        with pytest.raises(mod.GateMisconfigured):
            mod._rpc("getVersion", [])


def test_misconfigured_gate_fails_closed(monkeypatch):
    """The whole point of F-03: misconfiguration must DENY, not allow everyone."""
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT=MINT,
        RCLAW_RPC_URL="https://api.mainnet-beta.solana.com",
    )
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


# ── Wallet-ownership verification (F-01) ────────────────────────────────────

def test_unverified_wallet_denied(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 10_000_000.0)
    # Plenty of balance, but the address was never proven → no tier.
    assert mod.allows_user(_Users(WALLET, verified=False), 1, "premium_scan") is False
    # Same wallet, proof recorded → allowed.
    assert mod.allows_user(_Users(WALLET, verified=True), 1, "premium_scan") is True


def test_legacy_store_without_verification_fails_closed(monkeypatch):
    """A store predating verification must not grandfather unproven addresses."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 10_000_000.0)
    assert mod.allows_user(_LegacyUsers(WALLET), 1, "premium_scan") is False


# ── Staking gate (programs/rclaw_staking) ───────────────────────────────────

def _stake_account_b64(amount_base, *, unlock_at=None, version=1, size=None):
    """Encode StakeAccount data — layout MUST match programs/rclaw_staking.

    8 disc | version @8 | owner @9 (32) | mint @41 (32) | amount @73 (u64 LE)
          | staked_at @81 | unlock_at @89 | bump @97, then RESERVED zeros.
    The Rust side asserts these same offsets against the Borsh encoding in
    ``layout_tests::borsh_offsets_match_the_python_gate``.
    """
    import base64 as _b64
    import time as _time
    total = size if size is not None else 8 + 90 + 64
    data = bytearray(total)
    data[8] = version
    data[73:81] = int(amount_base).to_bytes(8, "little")
    unlock = int(_time.time()) + 86_400 if unlock_at is None else int(unlock_at)
    data[89:97] = int(unlock).to_bytes(8, "little", signed=True)
    return _b64.b64encode(bytes(data)).decode()


def test_staked_of_none_without_program(monkeypatch):
    mod = _reload_clean(monkeypatch, RCLAW_MINT=MINT)
    assert mod.staking_program() == ""
    assert mod.staked_of(WALLET) is None


def test_staked_of_parses_getprogramaccounts(monkeypatch):
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_DECIMALS="9")
    # 25,000 tokens @ 9 dp across two stake accounts (10k + 15k).
    def fake_rpc(method, params):
        if method == "getAccountInfo":
            return {"value": {"data": {"parsed": {"info": {"decimals": 9}}}}}
        assert method == "getProgramAccounts"
        assert params[1]["filters"][0]["memcmp"]["offset"] == mod.STAKE_OWNER_OFFSET
        return [
            {"account": {"data": [_stake_account_b64(10_000 * 10**9), "base64"]}},
            {"account": {"data": [_stake_account_b64(15_000 * 10**9), "base64"]}},
        ]
    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    assert mod.staked_of(WALLET) == 25_000.0


def test_gate_prefers_staked_when_program_set(monkeypatch):
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT=MINT,
        RCLAW_STAKING_PROGRAM=WALLET,
    )
    # Wallet holds plenty un-staked, but only a little is staked → blocked.
    monkeypatch.setattr(mod, "balance_of", lambda w: 1_000_000.0)
    monkeypatch.setattr(mod, "staked_of", lambda w: 100.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False
    # Enough staked → allowed.
    monkeypatch.setattr(mod, "staked_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_staked_rpc_error_denies_without_a_recent_reading(monkeypatch):
    """Same inversion as above, on the staking-program path."""
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT=MINT,
        RCLAW_STAKING_PROGRAM=WALLET,
    )
    monkeypatch.setattr(mod, "staked_of", lambda w: None)  # infra error
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False
    # And the grace path works here too, not just on the balance path.
    monkeypatch.setattr(mod, "staked_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True
    monkeypatch.setattr(mod, "staked_of", lambda w: None)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


# ── Regressions for the audit findings (RC-AUDIT) ───────────────────────────

def test_staked_of_filters_on_mint(monkeypatch):
    """A stake of some OTHER token must not count toward $RCLAW tier.

    The staking program previously bound no mint to a stake record; the gate now
    memcmp-filters on the mint at offset 40 whenever RCLAW_MINT is configured.
    """
    mod = _reload_clean(
        monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_MINT=MINT, RCLAW_DECIMALS="9"
    )
    seen = {}
    monkeypatch.setattr(mod, "_rpc", _rpc_stub(capture=seen))
    mod.staked_of(WALLET)
    memcmps = [f for f in seen["filters"] if "memcmp" in f]
    offsets = [f["memcmp"]["offset"] for f in memcmps]
    assert mod.STAKE_OWNER_OFFSET in offsets, "must filter on owner"
    assert mod.STAKE_MINT_OFFSET in offsets, \
        "must ALSO filter on mint so foreign-token stake is excluded"
    mint_filter = [f for f in memcmps if f["memcmp"]["offset"] == mod.STAKE_MINT_OFFSET][0]
    assert mint_filter["memcmp"]["bytes"] == MINT


def test_staked_of_constrains_account_size(monkeypatch):
    """A dataSize filter rejects arbitrarily-shaped accounts before parsing."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_MINT=MINT)
    seen = {}
    monkeypatch.setattr(mod, "_rpc", _rpc_stub(capture=seen))
    mod.staked_of(WALLET)
    sizes = [f["dataSize"] for f in seen["filters"] if "dataSize" in f]
    assert sizes == [mod.STAKE_ACCOUNT_TOTAL_LEN]


def test_staked_of_reads_amount_at_documented_offset(monkeypatch):
    """Byte-layout lock, mirrored by a Rust test over the Borsh encoding."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_DECIMALS="9")
    monkeypatch.setattr(
        mod, "_rpc",
        _rpc_stub([{"account": {"data": [_stake_account_b64(7 * 10**9), "base64"]}}]),
    )
    assert mod.staked_of(WALLET) == 7.0
    # An account too short to hold the layout must be ignored, not misread.
    import base64 as _b64
    short = _b64.b64encode(bytes(57)).decode()
    monkeypatch.setattr(mod, "_rpc", _rpc_stub([{"account": {"data": [short, "base64"]}}]))
    assert mod.staked_of(WALLET) == 0.0


def test_expired_lock_does_not_count_toward_tier(monkeypatch):
    """A withdrawable position is rotatable, so it must not carry a tier."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_DECIMALS="9")
    import time as _t
    expired = _stake_account_b64(50_000 * 10**9, unlock_at=int(_t.time()) - 1)
    monkeypatch.setattr(mod, "_rpc", _rpc_stub([{"account": {"data": [expired, "base64"]}}]))
    assert mod.staked_of(WALLET) == 0.0
    live = _stake_account_b64(50_000 * 10**9, unlock_at=int(_t.time()) + 3600)
    monkeypatch.setattr(mod, "_rpc", _rpc_stub([{"account": {"data": [live, "base64"]}}]))
    assert mod.staked_of(WALLET) == 50_000.0


def test_unknown_account_version_is_skipped(monkeypatch):
    """An unrecognised layout must be refused, not misparsed."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_DECIMALS="9")
    future = _stake_account_b64(99_000 * 10**9, version=99)
    monkeypatch.setattr(mod, "_rpc", _rpc_stub([{"account": {"data": [future, "base64"]}}]))
    assert mod.staked_of(WALLET) == 0.0


# ── Decimals sourced from the mint (F-29) ───────────────────────────────────

def test_decimals_come_from_the_mint_not_the_env(monkeypatch):
    """A wrong RCLAW_DECIMALS mis-scales every threshold; the mint is authority."""
    mod = _reload_clean(
        monkeypatch, RCLAW_STAKING_PROGRAM=WALLET, RCLAW_MINT=MINT, RCLAW_DECIMALS="6"
    )
    # Mint says 9 dp; env says 6. The mint must win.
    monkeypatch.setattr(
        mod, "_rpc",
        _rpc_stub([{"account": {"data": [_stake_account_b64(5 * 10**9), "base64"]}}], decimals=9),
    )
    assert mod.staked_of(WALLET) == 5.0, "env override must not mis-scale by 1000x"


def test_decimals_fall_back_to_env_then_default(monkeypatch):
    mod = _reload_clean(monkeypatch, RCLAW_MINT=MINT, RCLAW_DECIMALS="6")
    monkeypatch.setattr(mod, "_rpc", lambda m, p: None)  # mint unreadable
    assert mod._decimals() == 6
    mod2 = _reload_clean(monkeypatch, RCLAW_MINT=MINT)
    monkeypatch.setattr(mod2, "_rpc", lambda m, p: None)
    assert mod2._decimals() == 9


def test_decimals_are_cached_per_mint(monkeypatch):
    """Immutable per mint, and this sits on every gate check."""
    mod = _reload_clean(monkeypatch, RCLAW_MINT=MINT)
    calls = []

    def counting(method, params):
        calls.append(method)
        return {"value": {"data": {"parsed": {"info": {"decimals": 9}}}}}

    monkeypatch.setattr(mod, "_rpc", counting)
    assert mod._decimals() == 9
    assert mod._decimals() == 9
    assert calls.count("getAccountInfo") == 1


# ── Invalid stored input must not fail open (F-43) ──────────────────────────

def test_invalid_stored_wallet_denies_rather_than_failing_open(monkeypatch):
    """A malformed address makes the RPC error, which reads as None == fail-open.

    That sentinel is right for an outage and wrong for attacker-supplied input.
    """
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    # balance_of would return None (RPC rejects the address) -> old code allowed.
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    for bad in ("W", "not-base58-0OIl", "ARJrg3Ti675tnwWkiW84C1f2omYtz7Dx7", ""):
        assert mod.allows_user(_Users(bad), 1, "premium_scan") is False, bad
    # A valid address with a genuine infra error is DENIED unless we have read
    # its stake recently — see the bounded-grace tests below.
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_malformed_threshold_warns_and_uses_default(monkeypatch):
    mod = _reload_clean(monkeypatch, RCLAW_TIER_PRO_MIN="not-a-number")
    assert mod.tier_for_balance(10_000) == "pro"


# ── RPC outages: bounded last-known-good, not an open gate ─────────────────
#
# `allows_user` used to `return True` on any RPC error. The intent — don't lock
# a paying user out over a blip — was right; the implementation granted far more:
# it PROMOTED users who had never held a token, it was UNBOUNDED in time, and it
# was cheap to INDUCE, because public Solana RPCs rate-limit aggressively. The
# reward for making the bot's reads fail was free premium access.

def test_rpc_outage_denies_a_user_we_have_never_read(monkeypatch):
    """Never entitled means never entitled. An outage is not a promotion."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_rpc_outage_preserves_a_recently_confirmed_tier(monkeypatch):
    """The case the fail-open existed for, and the only one it should cover."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    # One good read establishes the tier...
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True
    # ...and it survives the RPC going away.
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_the_grace_window_actually_expires(monkeypatch):
    """Bounded. An entitlement nobody can confirm does not last forever."""
    mod = _reload_clean(
        monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT,
        RCLAW_TIER_GRACE_SECONDS="60",
    )
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True

    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    real_time = mod.time.time
    monkeypatch.setattr(mod.time, "time", lambda: real_time() + 61)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_a_cached_reading_does_not_confer_a_tier_it_never_had(monkeypatch):
    """The cache preserves the tier that was read — it does not upgrade it."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 1.0)  # below every threshold
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is False


def test_the_cache_is_per_wallet_not_global(monkeypatch):
    """One user's confirmed stake must not entitle a different wallet."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    other = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users(other), 2, "premium_scan") is False


def test_check_user_distinguishes_cannot_check_from_did_not_stake(monkeypatch):
    """The reason drives which message the user sees, so it has to be right."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)

    # Too small a stake -> "insufficient": tell them to stake more.
    monkeypatch.setattr(mod, "balance_of", lambda w: 1.0)
    assert mod.check_user(_Users(WALLET), 1, "premium_scan") == (False, "insufficient")

    # RPC down with nothing cached -> "unavailable": our fault, not theirs.
    other = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.check_user(_Users(other), 2, "premium_scan") == (False, "unavailable")

    # No wallet, and an unverified one, stay distinct from both.
    assert mod.check_user(_Users(None), 3, "premium_scan") == (False, "no_wallet")


def test_allows_user_still_works_as_a_boolean(monkeypatch):
    """The old entry point is kept so nothing that imports it silently changes."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users(WALLET), 1, "premium_scan") is True


def test_the_two_messages_do_not_say_the_same_thing(monkeypatch):
    mod = _reload_clean(monkeypatch)
    upgrade, unavailable = mod.upgrade_message(), mod.unavailable_message()
    assert "stake" in upgrade.lower()
    assert "stake at least" not in unavailable.lower(), "outage message must not demand more stake"
    assert "try again" in unavailable.lower()
