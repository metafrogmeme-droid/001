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
    assert mod.allows_user(_Users("SoMeWaLLeT"), 1, "premium_scan") is True


def test_enabled_requires_mint(monkeypatch):
    # Flag on but no mint → still inert.
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true")
    assert mod.gate_enabled() is False
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is True


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
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    assert mod.gate_enabled() is True
    # No linked wallet → cannot prove stake → blocked.
    assert mod.allows_user(_Users(None), 1, "premium_scan") is False


def test_sufficient_stake_allows(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is True


def test_insufficient_stake_blocks(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    monkeypatch.setattr(mod, "balance_of", lambda w: 100.0)
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is False


def test_rpc_error_fails_open(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    # balance_of returns None on infra error → must fail OPEN (allow).
    monkeypatch.setattr(mod, "balance_of", lambda w: None)
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is True


def test_ungated_feature_allowed(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    assert mod.allows_user(_Users(None), 1, "not_a_gated_feature") is True


def test_balance_of_parses_rpc_result(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")

    def fake_rpc(method, params):
        assert method == "getTokenAccountsByOwner"
        return {"value": [
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 12.5}}}}}},
            {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 7.5}}}}}},
        ]}

    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    assert mod.balance_of("W") == 20.0


def test_balance_of_none_without_wallet_or_mint(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    assert mod.balance_of(None) is None
    mod2 = _reload_clean(monkeypatch)  # no mint
    assert mod2.balance_of("W") is None


def test_non_devnet_rpc_raises_misconfigured(monkeypatch):
    """A non-devnet host is a PERMANENT fault and must not reach the fail-open path."""
    import pytest
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT="Mint111",
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
            RCLAW_MINT="Mint111", RCLAW_RPC_URL=url,
        )
        with pytest.raises(mod.GateMisconfigured):
            mod._rpc("getVersion", [])


def test_misconfigured_gate_fails_closed(monkeypatch):
    """The whole point of F-03: misconfiguration must DENY, not allow everyone."""
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT="Mint111",
        RCLAW_RPC_URL="https://api.mainnet-beta.solana.com",
    )
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is False


# ── Wallet-ownership verification (F-01) ────────────────────────────────────

def test_unverified_wallet_denied(monkeypatch):
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    monkeypatch.setattr(mod, "balance_of", lambda w: 10_000_000.0)
    # Plenty of balance, but the address was never proven → no tier.
    assert mod.allows_user(_Users("W", verified=False), 1, "premium_scan") is False
    # Same wallet, proof recorded → allowed.
    assert mod.allows_user(_Users("W", verified=True), 1, "premium_scan") is True


def test_legacy_store_without_verification_fails_closed(monkeypatch):
    """A store predating verification must not grandfather unproven addresses."""
    mod = _reload_clean(monkeypatch, TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT="Mint111")
    monkeypatch.setattr(mod, "balance_of", lambda w: 10_000_000.0)
    assert mod.allows_user(_LegacyUsers("W"), 1, "premium_scan") is False


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
    mod = _reload_clean(monkeypatch, RCLAW_MINT="Mint111")
    assert mod.staking_program() == ""
    assert mod.staked_of("W") is None


def test_staked_of_parses_getprogramaccounts(monkeypatch):
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_DECIMALS="9")
    # 25,000 tokens @ 9 dp across two stake accounts (10k + 15k).
    def fake_rpc(method, params):
        assert method == "getProgramAccounts"
        assert params[1]["filters"][0]["memcmp"]["offset"] == mod.STAKE_OWNER_OFFSET
        return [
            {"account": {"data": [_stake_account_b64(10_000 * 10**9), "base64"]}},
            {"account": {"data": [_stake_account_b64(15_000 * 10**9), "base64"]}},
        ]
    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    assert mod.staked_of("W") == 25_000.0


def test_gate_prefers_staked_when_program_set(monkeypatch):
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT="Mint111",
        RCLAW_STAKING_PROGRAM="Stake111",
    )
    # Wallet holds plenty un-staked, but only a little is staked → blocked.
    monkeypatch.setattr(mod, "balance_of", lambda w: 1_000_000.0)
    monkeypatch.setattr(mod, "staked_of", lambda w: 100.0)
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is False
    # Enough staked → allowed.
    monkeypatch.setattr(mod, "staked_of", lambda w: 50_000.0)
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is True


def test_staked_rpc_error_fails_open(monkeypatch):
    mod = _reload_clean(
        monkeypatch,
        TOKEN_TIER_GATE_ENABLED="true",
        RCLAW_MINT="Mint111",
        RCLAW_STAKING_PROGRAM="Stake111",
    )
    monkeypatch.setattr(mod, "staked_of", lambda w: None)  # infra error
    assert mod.allows_user(_Users("W"), 1, "premium_scan") is True


# ── Regressions for the audit findings (RC-AUDIT) ───────────────────────────

def test_staked_of_filters_on_mint(monkeypatch):
    """A stake of some OTHER token must not count toward $RCLAW tier.

    The staking program previously bound no mint to a stake record; the gate now
    memcmp-filters on the mint at offset 40 whenever RCLAW_MINT is configured.
    """
    mod = _reload_clean(
        monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_MINT="Mint111", RCLAW_DECIMALS="9"
    )
    seen = {}

    def fake_rpc(method, params):
        seen['filters'] = params[1]["filters"]
        return []

    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    mod.staked_of("W")
    memcmps = [f for f in seen['filters'] if "memcmp" in f]
    offsets = [f["memcmp"]["offset"] for f in memcmps]
    assert mod.STAKE_OWNER_OFFSET in offsets, "must filter on owner"
    assert mod.STAKE_MINT_OFFSET in offsets, \
        "must ALSO filter on mint so foreign-token stake is excluded"
    mint_filter = [f for f in memcmps if f["memcmp"]["offset"] == mod.STAKE_MINT_OFFSET][0]
    assert mint_filter["memcmp"]["bytes"] == "Mint111"


def test_staked_of_constrains_account_size(monkeypatch):
    """A dataSize filter rejects arbitrarily-shaped accounts before parsing."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_MINT="Mint111")
    seen = {}
    monkeypatch.setattr(mod, "_rpc", lambda m, p: (seen.update(f=p[1]["filters"]) or []))
    mod.staked_of("W")
    sizes = [f["dataSize"] for f in seen["f"] if "dataSize" in f]
    assert sizes == [mod.STAKE_ACCOUNT_TOTAL_LEN]


def test_staked_of_reads_amount_at_documented_offset(monkeypatch):
    """Byte-layout lock, mirrored by a Rust test over the Borsh encoding."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_DECIMALS="9")
    monkeypatch.setattr(
        mod, "_rpc",
        lambda m, p: [{"account": {"data": [_stake_account_b64(7 * 10**9), "base64"]}}],
    )
    assert mod.staked_of("W") == 7.0
    # An account too short to hold the layout must be ignored, not misread.
    import base64 as _b64
    short = _b64.b64encode(bytes(57)).decode()
    monkeypatch.setattr(mod, "_rpc", lambda m, p: [{"account": {"data": [short, "base64"]}}])
    assert mod.staked_of("W") == 0.0


def test_expired_lock_does_not_count_toward_tier(monkeypatch):
    """A withdrawable position is rotatable, so it must not carry a tier."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_DECIMALS="9")
    import time as _t
    expired = _stake_account_b64(50_000 * 10**9, unlock_at=int(_t.time()) - 1)
    monkeypatch.setattr(mod, "_rpc", lambda m, p: [{"account": {"data": [expired, "base64"]}}])
    assert mod.staked_of("W") == 0.0
    live = _stake_account_b64(50_000 * 10**9, unlock_at=int(_t.time()) + 3600)
    monkeypatch.setattr(mod, "_rpc", lambda m, p: [{"account": {"data": [live, "base64"]}}])
    assert mod.staked_of("W") == 50_000.0


def test_unknown_account_version_is_skipped(monkeypatch):
    """An unrecognised layout must be refused, not misparsed."""
    mod = _reload_clean(monkeypatch, RCLAW_STAKING_PROGRAM="Stake111", RCLAW_DECIMALS="9")
    future = _stake_account_b64(99_000 * 10**9, version=99)
    monkeypatch.setattr(mod, "_rpc", lambda m, p: [{"account": {"data": [future, "base64"]}}])
    assert mod.staked_of("W") == 0.0
