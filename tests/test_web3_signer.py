"""WEB3-LIVE-EXEC slice 2 — the testnet live signer.

Locks the safety envelope of the first slice that ACTUALLY signs + broadcasts.
The feature + signing switches now DEFAULT ON so testnet signing works out of the
box — but the envelope is unchanged and airtight: admin-only, TESTNET-ONLY
(mainnet refused regardless of the mainnet-allow flag), envelope-enforced, inert
without the operator's key + eth-account, and — the hard money-path invariant —
the signing key is NEVER returned, logged, or surfaced in an error. The elliptic
signing itself is proven correct offline (the signature recovers to the signer's
own address) when the optional eth-account library is installed.
"""

import inspect

import pytest

from bot.web import web3_signer as signer
from bot.web import user_gateway

# canonical anvil/hardhat test account #0 — public, never holds real funds.
_TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_DEST = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

_ON = {
    "WEB3_LIVE_EXEC_ENABLED": "1",
    "WEB3_LIVE_EXEC_SIGN_ENABLED": "1",
    "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY,
}


def _ev(monkeypatch, *, lib=True, **kw):
    # Decouple the gate-logic tests from whether eth-account is installed.
    monkeypatch.setattr(signer, "signer_available", lambda: lib)
    kw.setdefault("is_admin", True)
    kw.setdefault("network", "sepolia")
    kw.setdefault("envelope_enforcing", True)
    kw.setdefault("env", _ON)
    return signer.evaluate_sign(**kw)


# ── the fail-closed signing gate ───────────────────────────────────────

def test_feature_default_on_but_hard_disable_wins(monkeypatch):
    # With nothing set, the feature is ON — testnet signing works out of the box
    # (key + testnet + envelope + lib all present here).
    on = _ev(monkeypatch, env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert on.allowed is True
    # …but an explicit WEB3_LIVE_EXEC_ENABLED=0 still hard-disables everything.
    off = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_ENABLED": "0",
                                "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert off.allowed is False and "not enabled" in off.reason.lower()


def test_signing_has_its_own_hard_disable(monkeypatch):
    # signing defaults ON, but its OWN switch can hard-disable it independently.
    off = _ev(monkeypatch, env={"WEB3_LIVE_EXEC_SIGN_ENABLED": "0",
                                "WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert off.allowed is False and "signing" in off.reason.lower()


def test_defaults_on_still_need_the_operator_key(monkeypatch):
    # Defaulting the switches ON does NOT bypass the operator's own setup: with no
    # signing key, signing is still refused — "on" is inert until the key is set.
    d = _ev(monkeypatch, env={})                      # everything default, no key
    assert d.allowed is False and "key" in d.reason.lower()


def test_mainnet_still_refused_by_default_with_everything_on(monkeypatch):
    # THE safety invariant of this change: even with the feature + signing ON by
    # default and a key present, a mainnet target is refused (testnet-only), with
    # no mainnet-allow flag anywhere in sight.
    d = _ev(monkeypatch, network="ethereum",
            env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
    assert d.allowed is False and "testnet" in d.reason.lower()


def test_new_testnets_pass_the_gate(monkeypatch):
    # the added testnets are usable end-to-end (gate allows; chain in testnet set).
    for net in ("polygon-amoy", "avalanche-fuji", "scroll-sepolia",
                "linea-sepolia", "blast-sepolia", "bsc-testnet"):
        d = _ev(monkeypatch, network=net, env={"WEB3_SIGNER_PRIVATE_KEY": _TEST_KEY})
        assert d.allowed is True, net
        assert d.network["testnet"] is True
        assert d.network["chain_id"] in signer._TESTNET_CHAIN_IDS


def test_admin_only(monkeypatch):
    d = _ev(monkeypatch, is_admin=False)
    assert d.allowed is False and "admin" in d.reason.lower()


def test_mainnet_is_refused_even_with_allow_flag(monkeypatch):
    env = dict(_ON, WEB3_LIVE_EXEC_ALLOW_MAINNET="1")   # mainnet allowed for previews…
    d = _ev(monkeypatch, network="ethereum", env=env)   # …but signing stays testnet-only
    assert d.allowed is False and "testnet" in d.reason.lower()


def test_unknown_network_denied(monkeypatch):
    assert _ev(monkeypatch, network="does-not-exist").allowed is False


def test_key_absent_denies(monkeypatch):
    env = {"WEB3_LIVE_EXEC_ENABLED": "1", "WEB3_LIVE_EXEC_SIGN_ENABLED": "1"}  # no key
    d = _ev(monkeypatch, env=env)
    assert d.allowed is False and "key" in d.reason.lower()


def test_library_absent_denies(monkeypatch):
    d = _ev(monkeypatch, lib=False)
    assert d.allowed is False and "library" in d.reason.lower()


def test_requires_enforcing_envelope(monkeypatch):
    d = _ev(monkeypatch, envelope_enforcing=False)
    assert d.allowed is False and "envelope" in d.reason.lower()


def test_all_preconditions_met_allows(monkeypatch):
    d = _ev(monkeypatch)
    assert d.allowed is True
    assert d.network and d.network["testnet"] is True
    assert all(d.checklist.values())


# ── the key never leaks ────────────────────────────────────────────────

def test_key_presence_is_boolean_never_the_value():
    assert signer.signer_key_present(_ON) is True
    assert signer.signer_key_present({}) is False


def test_no_public_helper_returns_the_key():
    # address derivation returns the ADDRESS, never the private key.
    addr = signer.signer_address(_ON)
    if addr is not None:                 # only when eth-account is installed
        assert addr.lower() == _TEST_ADDR.lower()
        assert _TEST_KEY not in addr


def test_signer_source_never_returns_resolved_key():
    # _resolve_key is internal; assert no public function passes it to a caller.
    for fn in (signer.signer_key_present, signer.signer_address, signer.evaluate_sign,
               signer.build_and_sign):
        src = inspect.getsource(fn)
        assert "return _resolve_key" not in src
        assert "return key" not in src


# ── real offline signing (skips when eth-account is not installed) ──────

def test_build_and_sign_produces_a_recoverable_testnet_signature():
    Account = pytest.importorskip("eth_account").Account
    out = signer.build_and_sign(network="sepolia", to=_DEST, value_wei=10**15,
                                nonce=0, gas=21000, env=_ON)
    assert out["ok"] is True
    assert out["from"].lower() == _TEST_ADDR.lower()
    assert out["chain_id"] == 11155111
    assert out["raw"].startswith("0x02")           # EIP-1559 typed tx
    # the signature must recover to the signer's OWN address — proof it is valid.
    recovered = Account.recover_transaction(out["raw"])
    assert recovered.lower() == _TEST_ADDR.lower()
    # the private key must appear nowhere in the returned payload.
    assert _TEST_KEY not in str(out)


def test_build_and_sign_refuses_mainnet():
    pytest.importorskip("eth_account")
    out = signer.build_and_sign(network="ethereum", to=_DEST, value_wei=1,
                                nonce=0, gas=21000, env=_ON)
    assert out["ok"] is False and "testnet" in out["error"].lower()


def test_build_and_sign_refuses_without_key():
    out = signer.build_and_sign(network="sepolia", to=_DEST, value_wei=1,
                                nonce=0, gas=21000, env={})
    assert out["ok"] is False


async def test_broadcast_refuses_non_testnet_chain():
    out = await signer.broadcast("0x02aa", "http://localhost:8545", 1)   # mainnet chain id
    assert out["ok"] is False and "testnet" in out["error"].lower()


async def test_broadcast_without_rpc_is_signed_only():
    out = await signer.broadcast("0x02aa", "", 11155111)                 # testnet, no rpc
    assert out["ok"] is False and "rpc" in out["error"].lower()


# ── gateway handler invariants ─────────────────────────────────────────

def test_sign_handler_gates_and_never_returns_the_key():
    src = inspect.getsource(user_gateway.handle_web3_sign)
    assert "evaluate_sign(" in src        # testnet-only signing gate
    assert "authorize(" in src            # envelope transfer gate
    assert "_is_admin_id" in src          # admin re-check
    assert "get_review_queue" in src      # recorded to the review queue
    # the handler delegates signing to web3_signer and never touches the key.
    assert "WEB3_SIGNER_PRIVATE_KEY" not in src
    assert "private_key" not in src


def test_sign_route_registered():
    src = inspect.getsource(user_gateway.build_gateway)
    assert 'add_post("/web3/sign", handle_web3_sign)' in src


def test_signer_key_is_vaulted():
    # the signing key is encrypted at rest (managed by the secrets vault).
    from bot.core import secrets_vault
    assert "WEB3_SIGNER_PRIVATE_KEY" in secrets_vault._DEFAULT_MANAGED


# ── signer status (key-free snapshot for the web UI) ───────────────────

def test_signer_status_is_key_free_and_testnet_scoped():
    st = signer.signer_status(_ON)
    # never the private key — only presence booleans + the public address.
    assert _TEST_KEY not in str(st)
    assert st["signer_key_present"] is True
    assert st["feature_enabled"] is True and st["signing_enabled"] is True
    assert st["testnet_only"] is True
    # every listed network is a testnet, with an rpc_configured flag.
    assert st["testnets"] and all(t["chain_id"] and "rpc_configured" in t
                                  for t in st["testnets"])
    labels = {t["network"] for t in st["testnets"]}
    assert "sepolia" in labels and "ethereum" not in labels   # no mainnet listed


def test_signer_status_reports_rpc_readiness():
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    st = signer.signer_status(env)
    by = {t["network"]: t for t in st["testnets"]}
    assert by["sepolia"]["rpc_configured"] is True
    assert by["base-sepolia"]["rpc_configured"] is False


# ── prepare (nonce/gas auto-fetch) — testnet-only, never signs ─────────

async def test_prepare_refuses_mainnet():
    out = await signer.prepare_tx(network="ethereum", address=_TEST_ADDR, env=_ON)
    assert out["ok"] is False and "testnet" in out["error"].lower()


async def test_prepare_without_rpc_is_refused():
    out = await signer.prepare_tx(network="sepolia", address=_TEST_ADDR, env=_ON)  # no RPC
    assert out["ok"] is False and "rpc" in out["error"].lower()


async def test_prepare_without_address_is_refused():
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    out = await signer.prepare_tx(network="sepolia", address="", env=env)
    assert out["ok"] is False and "address" in out["error"].lower()


async def test_prepare_composes_eip1559_fees(monkeypatch):
    # stub the RPC round-trip so we can prove nonce + fee composition without a net.
    async def fake_rpc(rpc_url, method, params):
        return {
            "eth_getTransactionCount": {"ok": True, "result": "0x5"},
            "eth_maxPriorityFeePerGas": {"ok": True, "result": hex(1_500_000_000)},
            "eth_getBlockByNumber": {"ok": True, "result": {"baseFeePerGas": hex(3_000_000_000)}},
        }[method]
    monkeypatch.setattr(signer, "_rpc_call", fake_rpc)
    env = dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia")
    out = await signer.prepare_tx(network="sepolia", address=_TEST_ADDR, env=env)
    assert out["ok"] is True
    assert out["nonce"] == 5
    assert out["base_fee_wei"] == 3_000_000_000
    assert out["max_priority_wei"] == 1_500_000_000
    # maxFee = 2×base + tip, and tip never exceeds maxFee.
    assert out["max_fee_wei"] == 2 * 3_000_000_000 + 1_500_000_000
    assert out["max_priority_wei"] <= out["max_fee_wei"]


# ── the prepare + status gateway handlers (admin + gate, never the key) ─

def test_prepare_and_status_handlers_are_admin_gated_and_keyless():
    for fn in (user_gateway.handle_web3_prepare, user_gateway.handle_web3_sign_status):
        src = inspect.getsource(fn)
        assert "_is_admin_id" in src            # admin re-check server-side
        assert "WEB3_SIGNER_PRIVATE_KEY" not in src and "private_key" not in src
    # prepare runs the same fail-closed signing gate before touching any RPC.
    assert "evaluate_sign(" in inspect.getsource(user_gateway.handle_web3_prepare)


def test_prepare_and_status_routes_registered():
    src = inspect.getsource(user_gateway.build_gateway)
    assert 'add_get("/web3/sign/status", handle_web3_sign_status)' in src
    assert 'add_post("/web3/sign/prepare", handle_web3_prepare)' in src


# ── block-explorer links (one click from a testnet sign to the on-chain record) ─

def test_explorer_tx_url_builds_per_network_links():
    from bot.web.web3_exec_gate import explorer_tx_url
    h = "0x" + "a" * 64
    assert explorer_tx_url("sepolia", h) == f"https://sepolia.etherscan.io/tx/{h}"
    assert explorer_tx_url("base-sepolia", h) == f"https://sepolia.basescan.org/tx/{h}"
    assert explorer_tx_url("optimism-sepolia", h).startswith("https://sepolia-optimism.etherscan.io/tx/")


def test_explorer_tx_url_refuses_unknown_or_malformed():
    from bot.web.web3_exec_gate import explorer_tx_url
    assert explorer_tx_url("does-not-exist", "0x" + "a" * 64) == ""  # unknown net
    assert explorer_tx_url("sepolia", "not-a-hash") == ""           # non-0x hash
    assert explorer_tx_url("sepolia", "") == ""                     # empty hash


def test_every_testnet_has_an_explorer():
    from bot.web import web3_exec_gate as gate
    for name, n in gate.NETWORKS.items():
        if n.get("testnet"):
            assert n.get("explorer", "").startswith("https://"), f"{name} missing explorer"


def test_sign_handler_returns_an_explorer_url():
    src = inspect.getsource(user_gateway.handle_web3_sign)
    assert "explorer_url" in src and "explorer_tx_url" in src
    # only when the broadcast actually succeeded — never a link to a tx that
    # never hit the chain.
    assert 'if bcast.get("ok") else ""' in src


# ── MegaETH network gate ────────────────────────────────────────────────
# Adding a chain to NETWORKS is not a data edit — it moves real money paths.
# MegaETH is the first chain in the table that does NOT cost 21,000 gas for a
# bare transfer: it meters compute AND storage against the same gasLimit field,
# so a transfer is 21,000 + 39,000 = 60,000 and the RPC rejects anything under.
# Every hardcoded 21,000 in the signer was therefore a silent under-gas, and the
# deploy-gas fallback of 1,500,000 covered ~150 bytes of contract.
#
# The chain ids below are a DELIBERATE independent literal, not derived from
# NETWORKS. _TESTNET_CHAIN_IDS is built by filtering the same `testnet` boolean
# it is meant to double-check, so it cannot detect a mistyped flag — only a
# constant written down separately can.

from bot.web import web3_exec_gate as gate                          # noqa: E402

_KNOWN_MAINNET_CHAIN_IDS = frozenset({1, 10, 137, 8453, 42161, 4326})


async def _stub_rpc_healthy(rpc_url, method, params):
    """A MegaETH-shaped endpoint: flat base fee, zero priority tip."""
    return {
        "eth_getTransactionCount": {"ok": True, "result": "0x7"},
        "eth_maxPriorityFeePerGas": {"ok": True, "result": "0x0"},   # real MegaETH value
        "eth_getBlockByNumber": {"ok": True, "result": {"baseFeePerGas": "0xf4240"}},
        "eth_gasPrice": {"ok": True, "result": "0xf4240"},
    }.get(method, {"ok": False, "error": "unexpected method"})


# ── mainnet containment ──

def test_no_mainnet_chain_id_is_in_the_testnet_broadcast_set():
    # Catches a mistyped `"testnet": True` on any mainnet row — the single most
    # likely mistake when editing this table, and one every existing test misses
    # because they all read the very field the typo corrupts.
    assert not (_KNOWN_MAINNET_CHAIN_IDS & signer._TESTNET_CHAIN_IDS)


async def test_megaeth_mainnet_is_refused_by_every_signer_entrypoint():
    # Catches the megaeth row becoming reachable by any path. It is in the table
    # ONLY so this denial is explicit rather than an unknown-network accident.
    d = signer.evaluate_sign(is_admin=True, network="megaeth", envelope_enforcing=True,
                             env=dict(_ON, WEB3_LIVE_EXEC_ALLOW_MAINNET="1"))
    assert d.allowed is False and "testnet" in d.reason.lower()

    b = signer.build_and_sign(network="megaeth", to=_DEST, value_wei=1, nonce=0,
                              gas=60000, env=_ON)
    assert b["ok"] is False and "testnet" in b["error"].lower()

    p = await signer.prepare_tx(network="megaeth", address=_TEST_ADDR, env=_ON)
    assert p["ok"] is False and "testnet" in p["error"].lower()

    c = await signer.broadcast("0x02aa", "http://rpc.example", 4326)
    assert c["ok"] is False and "testnet" in c["error"].lower()


def test_megaeth_chain_ids_are_pinned():
    # Catches a transposed digit — 6343 and 4326 share their digits.
    assert gate.NETWORKS["megaeth-testnet"]["chain_id"] == 6343
    assert gate.NETWORKS["megaeth"]["chain_id"] == 4326
    assert gate.NETWORKS["megaeth-testnet"]["testnet"] is True
    assert gate.NETWORKS["megaeth"]["testnet"] is False


# ── table self-consistency ──

def test_no_two_networks_share_a_chain_id():
    ids = [n["chain_id"] for n in gate.NETWORKS.values()]
    assert len(set(ids)) == len(ids), "duplicate chain_id — a row was copy-pasted"


def test_every_network_row_is_complete():
    # Catches the missing-`label` bug (signer_status reads .get("label") and the
    # UI renders it, so a null label is a blank, unselectable dropdown option)
    # and a missing min_tx_gas (which now denies rather than defaulting).
    for name, n in gate.NETWORKS.items():
        assert isinstance(n.get("label"), str) and n["label"], f"{name}: no label"
        assert isinstance(n.get("chain_id"), int), f"{name}: chain_id not an int"
        assert isinstance(n.get("testnet"), bool), f"{name}: testnet not a bool"
        exp = n.get("explorer", "")
        assert exp.startswith("https://"), f"{name}: explorer not https"
        assert not exp.endswith("/"), f"{name}: explorer has a trailing slash"
        assert isinstance(n.get("min_tx_gas"), int) and n["min_tx_gas"] > 0, \
            f"{name}: no positive min_tx_gas"


def test_ui_testnet_dropdown_matches_the_python_table():
    # app/public/js/dashboard.js keeps a SECOND copy of the testnet list for the
    # Contract Studio deploy dropdown. Copies drift: without this test, adding a
    # chain server-side leaves one the API accepts but the UI never offers.
    import pathlib
    import re
    js = pathlib.Path(__file__).resolve().parents[1] / "app/public/js/dashboard.js"
    src = js.read_text()
    block = src[src.index("const TESTNETS = ["):]
    block = block[:block.index("];")]
    in_ui = set(re.findall(r"\['([a-z0-9-]+)',", block))
    in_py = {k for k, v in gate.NETWORKS.items() if v.get("testnet")}
    assert in_ui == in_py, (
        f"dashboard.js TESTNETS drifted from NETWORKS: "
        f"missing in UI={sorted(in_py - in_ui)}, stale in UI={sorted(in_ui - in_py)}")


# ── the gas floor ──

async def test_prepare_returns_the_chains_floor_not_a_hardcoded_21000(monkeypatch):
    # Catches reintroduction of the literal. The whole point: two chains, two
    # different correct answers from the same code path.
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)
    mega = await signer.prepare_tx(
        network="megaeth-testnet", address=_TEST_ADDR,
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert mega["ok"] is True and mega["gas"] == 60000

    sep = await signer.prepare_tx(
        network="sepolia", address=_TEST_ADDR,
        env=dict(_ON, WEB3_RPC_SEPOLIA="https://rpc.example/sepolia"))
    assert sep["ok"] is True and sep["gas"] == 21000


def test_build_and_sign_refuses_gas_below_the_chain_floor():
    # Catches a caller passing Ethereum's 21,000 to MegaETH. Runs before the
    # library check, so it holds whether or not eth-account is installed.
    out = signer.build_and_sign(network="megaeth-testnet", to=_DEST, value_wei=1,
                                nonce=0, gas=21000, env=_ON)
    assert out["ok"] is False
    assert "minimum" in out["error"].lower() or "gas" in out["error"].lower()
    # …and the same call at the real floor clears the gas gate (it may still
    # fail later on the optional library — that is a different gate).
    ok = signer.build_and_sign(network="megaeth-testnet", to=_DEST, value_wei=1,
                               nonce=0, gas=60000, env=_ON)
    assert ok.get("error") != "gas is below the chain's intrinsic minimum"


def test_build_and_sign_requires_an_explicit_gas():
    # Catches re-adding the default. A gas that is right on 16 chains and wrong
    # on one is worse than no default at all.
    with pytest.raises(TypeError):
        signer.build_and_sign(network="sepolia", to=_DEST, value_wei=1, nonce=0,
                              env=_ON)


# ── the deploy fallback ──

async def test_estimate_deploy_gas_returns_none_when_unestimatable(monkeypatch):
    # Catches any resurrected constant, on all three failure paths.
    assert await signer.estimate_deploy_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR,
        bytecode="0x6080", env=_ON) is None                       # no RPC set

    async def fails(*a, **k):
        return {"ok": False, "error": "rpc call failed"}
    monkeypatch.setattr(signer, "_rpc_call", fails)
    env = dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega")
    assert await signer.estimate_deploy_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR,
        bytecode="0x6080", env=env) is None                       # rpc failed

    async def garbage(*a, **k):
        return {"ok": True, "result": "not-a-number"}
    monkeypatch.setattr(signer, "_rpc_call", garbage)
    assert await signer.estimate_deploy_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR,
        bytecode="0x6080", env=env) is None                       # unusable result


async def test_prepare_deploy_fails_closed_when_gas_cannot_be_estimated(monkeypatch):
    # Catches a silent guess reaching the signer. Without this, the operator
    # gets a deterministic contract address and an explorer link for code that
    # never lands.
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)

    async def no_estimate(**kwargs):
        return None
    monkeypatch.setattr(signer, "estimate_deploy_gas", no_estimate)
    out = await signer.prepare_deploy(
        network="megaeth-testnet", address=_TEST_ADDR, bytecode="0x6080",
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert out["ok"] is False and "estimate" in out["error"].lower()


def test_no_hardcoded_deploy_gas_constant_survives():
    # Matches an ASSIGNMENT, not a mention: the module comment names the deleted
    # constant on purpose, to explain why it must not come back. A test that
    # forbade the name outright would force the removal of its own rationale.
    import re
    for mod in (signer, user_gateway):
        assert not re.search(r"^_DEPLOY_GAS_FALLBACK\s*=", inspect.getsource(mod),
                             re.M), f"{mod.__name__}: fabricated constant reintroduced"
    # and nothing may read it back off the signer module either
    assert "_signer._DEPLOY_GAS_FALLBACK" not in inspect.getsource(user_gateway)


# ── the zero-fee fail-open ──

async def test_prepare_denies_when_the_base_fee_cannot_be_read(monkeypatch):
    # Catches the `max_fee = priority` self-referential rescue. Before the fix
    # this returned {"ok": True, "max_fee_wei": 0} — a signable transaction with
    # no fee. MegaETH is the first chain whose real tip is 0x0, which is what
    # made the dead branch reachable.
    async def no_fees(rpc_url, method, params):
        return {
            "eth_getTransactionCount": {"ok": True, "result": "0x7"},
            "eth_maxPriorityFeePerGas": {"ok": True, "result": "0x0"},
            "eth_getBlockByNumber": {"ok": True, "result": {}},      # no baseFeePerGas
            "eth_gasPrice": {"ok": False, "error": "rate limited"},
        }.get(method, {"ok": False, "error": "unexpected"})
    monkeypatch.setattr(signer, "_rpc_call", no_fees)
    out = await signer.prepare_tx(
        network="megaeth-testnet", address=_TEST_ADDR,
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert out["ok"] is False
    assert out.get("max_fee_wei") in (None, )


async def test_prepare_accepts_a_legitimate_zero_tip(monkeypatch):
    # Catches over-correcting the above by rejecting MegaETH's real 0x0 tip.
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)
    out = await signer.prepare_tx(
        network="megaeth-testnet", address=_TEST_ADDR,
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert out["ok"] is True
    assert out["max_fee_wei"] == 2_000_000          # 2 × 1_000_000 + 0
    assert out["max_priority_wei"] == 0


# ── explorer links ──

def test_explorer_urls_for_megaeth():
    from bot.web.web3_exec_gate import explorer_tx_url, explorer_address_url
    h = "0x" + "a" * 64
    assert explorer_tx_url("megaeth-testnet", h) == \
        f"https://testnet-mega.etherscan.io/tx/{h}"
    assert explorer_address_url("megaeth", "0x" + "b" * 40) == \
        "https://megaeth.blockscout.com/address/0x" + "b" * 40


def test_explorer_url_rejects_non_hex_and_wrong_length():
    # Catches the docstring/code gap: both claimed hex validation, both checked
    # only the 0x prefix and len >= 6, so a traversal-shaped value produced a
    # link a browser normalises to the explorer root.
    from bot.web.web3_exec_gate import explorer_tx_url, explorer_address_url
    assert explorer_tx_url("megaeth-testnet", "0x" + "Z" * 64) == ""   # not hex
    assert explorer_tx_url("sepolia", "0x../../foo") == ""             # traversal
    assert explorer_tx_url("sepolia", "0x" + "a" * 63) == ""           # short
    assert explorer_address_url("sepolia", "0x" + "a" * 64) == ""      # tx hash != address


# ── MUTATION tests: break the fix on purpose, prove a guard notices ──

def test_MUTATION_flipping_megaeth_to_testnet_is_caught(monkeypatch):
    """_TESTNET_CHAIN_IDS is derived from the same boolean it double-checks, so
    it structurally cannot detect this typo. Prove the independent literal can."""
    monkeypatch.setitem(gate.NETWORKS["megaeth"], "testnet", True)
    mutated = frozenset(n["chain_id"] for n in gate.NETWORKS.values() if n.get("testnet"))
    assert 4326 in mutated, "the mutation did not take hold"
    assert _KNOWN_MAINNET_CHAIN_IDS & mutated, "the guard did not fire"


async def test_MUTATION_removing_min_tx_gas_denies_instead_of_defaulting(monkeypatch):
    """Break the TABLE, not the code: a chain with no declared floor must deny,
    never silently inherit Ethereum's 21,000."""
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)
    monkeypatch.delitem(gate.NETWORKS["megaeth-testnet"], "min_tx_gas")
    out = await signer.prepare_tx(
        network="megaeth-testnet", address=_TEST_ADDR,
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert out["ok"] is False, "a missing floor produced a gas anyway"
    assert out.get("gas") != 21000
    signed = signer.build_and_sign(network="megaeth-testnet", to=_DEST, value_wei=1,
                                   nonce=0, gas=60000, env=_ON)
    assert signed["ok"] is False, "signing proceeded without a declared floor"


async def test_MUTATION_a_resurrected_deploy_constant_would_be_caught(monkeypatch):
    """Simulate a future contributor re-adding a 'conservative' fallback."""
    async def fails(*a, **k):
        return {"ok": False, "error": "rpc call failed"}
    monkeypatch.setattr(signer, "_rpc_call", fails)
    got = await signer.estimate_deploy_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR, bytecode="0x60806040",
        env=dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega"))
    assert got is None, f"estimation failure produced a fabricated gas limit: {got}"


# ── real gas estimation for the ACTUAL transaction ──────────────────────
# prepare_tx runs before a destination is entered, so the gas it returns is the
# chain's intrinsic FLOOR. That floor is exact for a bare transfer to an EOA and
# SHORT for a transfer to a CONTRACT, whose receive/fallback runs on arrival —
# on every chain in the table, not just the storage-metered ones. The two cases
# differ only in what code lives at the destination, which no table can know.
#
# So: measure when a destination is known, and label which of the two you got.

_CONTRACT_EST = 0xC350           # 50,000 — a contract that does work on receive


def _rpc_with_estimate(est_hex):
    async def _stub(rpc_url, method, params):
        return {
            "eth_getTransactionCount": {"ok": True, "result": "0x7"},
            "eth_maxPriorityFeePerGas": {"ok": True, "result": "0x0"},
            "eth_getBlockByNumber": {"ok": True, "result": {"baseFeePerGas": "0xf4240"}},
            "eth_gasPrice": {"ok": True, "result": "0xf4240"},
            "eth_estimateGas": {"ok": True, "result": est_hex},
        }.get(method, {"ok": False, "error": "unexpected method"})
    return _stub


_MEGA_ENV = dict(_ON, WEB3_RPC_MEGAETH_TESTNET="https://rpc.example/mega")


async def test_prepare_without_a_destination_returns_the_floor_and_says_so(monkeypatch):
    # The existing UI flow, unchanged. The number is the floor — and it is
    # LABELLED the floor, so nothing downstream can mistake it for a measurement.
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  env=_MEGA_ENV)
    assert out["ok"] is True
    assert out["gas"] == 60000
    assert out["gas_basis"] == "floor"


async def test_prepare_with_a_destination_measures_it(monkeypatch):
    # 50,000 estimate + 25% = 62,500, above MegaETH's 60,000 floor.
    monkeypatch.setattr(signer, "_rpc_call", _rpc_with_estimate(hex(_CONTRACT_EST)))
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  to=_DEST, value_wei=1, env=_MEGA_ENV)
    assert out["ok"] is True
    assert out["gas_basis"] == "estimated"
    assert out["gas"] == _CONTRACT_EST * 5 // 4


async def test_an_estimate_below_the_floor_never_goes_under_it(monkeypatch):
    # The floor is a protocol minimum the RPC enforces. An estimate under it is
    # not permission to go under it — Sepolia estimating 21,000 for a plain
    # transfer must not produce 21,000 * 0.8 on a chain whose floor is higher.
    monkeypatch.setattr(signer, "_rpc_call", _rpc_with_estimate(hex(21000)))
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  to=_DEST, value_wei=1, env=_MEGA_ENV)
    assert out["ok"] is True
    assert out["gas"] >= 60000, "went below the chain's intrinsic floor"


async def test_a_failed_estimate_falls_back_to_the_floor_and_relabels(monkeypatch):
    # Honest degradation: the gas is still usable, but it is no longer a
    # measurement and must not claim to be one.
    async def no_estimate(rpc_url, method, params):
        if method == "eth_estimateGas":
            return {"ok": False, "error": "rpc call failed"}
        return await _stub_rpc_healthy(rpc_url, method, params)
    monkeypatch.setattr(signer, "_rpc_call", no_estimate)
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  to=_DEST, value_wei=1, env=_MEGA_ENV)
    assert out["ok"] is True
    assert out["gas"] == 60000
    assert out["gas_basis"] == "floor", "an unavailable estimate claimed to be one"


async def test_estimate_tx_gas_returns_none_when_unavailable(monkeypatch):
    # No RPC configured.
    assert await signer.estimate_tx_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR, to=_DEST,
        value_wei=1, env=_ON) is None

    async def fails(*a, **k):
        return {"ok": False, "error": "rpc call failed"}
    monkeypatch.setattr(signer, "_rpc_call", fails)
    assert await signer.estimate_tx_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR, to=_DEST,
        value_wei=1, env=_MEGA_ENV) is None

    async def garbage(*a, **k):
        return {"ok": True, "result": "not-a-number"}
    monkeypatch.setattr(signer, "_rpc_call", garbage)
    assert await signer.estimate_tx_gas(
        network="megaeth-testnet", from_address=_TEST_ADDR, to=_DEST,
        value_wei=1, env=_MEGA_ENV) is None


async def test_estimate_omits_to_for_a_contract_creation(monkeypatch):
    # to=None must produce a CREATION-shaped call (no "to" key), not a call to
    # the zero address — those are different transactions with different gas.
    seen = {}

    async def capture(rpc_url, method, params):
        if method == "eth_estimateGas":
            seen.update(params[0])
            return {"ok": True, "result": hex(500000)}
        return {"ok": False, "error": "unexpected"}
    monkeypatch.setattr(signer, "_rpc_call", capture)
    got = await signer.estimate_tx_gas(network="megaeth-testnet",
                                       from_address=_TEST_ADDR, to=None,
                                       data="0x6080", env=_MEGA_ENV)
    assert got == 500000 * 5 // 4
    assert "to" not in seen, "a creation was estimated as a call to an address"
    assert seen.get("data") == "0x6080"


def test_the_sign_handler_refuses_gas_below_the_measured_estimate():
    # Source-level: the guard has to sit in handle_web3_sign, between the
    # envelope check and build_and_sign, because that is the first point in the
    # flow holding BOTH the destination and the value.
    src = inspect.getsource(user_gateway.handle_web3_sign)
    assert "estimate_tx_gas" in src, "the sign path does not measure the real tx"
    assert "gas_below_estimate" in src
    # Anchor on the CALL, not the bare name: the comment above the guard
    # mentions build_and_sign by name, and matching that made this assertion
    # compare a prose reference against a call site.
    assert src.index("estimate_tx_gas(") < src.index("_signer.build_and_sign("), \
        "the estimate must be taken BEFORE the transaction is signed"


# ── MUTATION: prove each new guard is load-bearing ──

async def test_MUTATION_dropping_the_floor_clamp_would_under_gas(monkeypatch):
    """If max(est, floor) became just est, a low estimate would sign a tx the
    RPC rejects outright. Prove the clamp is what prevents it."""
    monkeypatch.setattr(signer, "_rpc_call", _rpc_with_estimate(hex(1000)))
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  to=_DEST, value_wei=1, env=_MEGA_ENV)
    # 1000 * 1.25 = 1250, far under MegaETH's 60,000 floor.
    assert out["gas"] == 60000, (
        f"the floor clamp is gone — signed {out['gas']} against a 60,000 minimum")


async def test_MUTATION_a_floor_mislabelled_as_estimated_is_visible(monkeypatch):
    """gas_basis is the whole point: without it a floor and a measurement are
    the same integer and nothing downstream can tell them apart."""
    monkeypatch.setattr(signer, "_rpc_call", _stub_rpc_healthy)   # no eth_estimateGas
    out = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                  to=_DEST, value_wei=1, env=_MEGA_ENV)
    assert out["gas"] == 60000
    assert out["gas_basis"] == "floor"
    # …and the same 60,000 arrived at by measurement is labelled differently.
    monkeypatch.setattr(signer, "_rpc_call", _rpc_with_estimate(hex(48000)))
    out2 = await signer.prepare_tx(network="megaeth-testnet", address=_TEST_ADDR,
                                   to=_DEST, value_wei=1, env=_MEGA_ENV)
    assert out2["gas"] == 60000 and out2["gas_basis"] == "estimated"
    assert out["gas"] == out2["gas"] and out["gas_basis"] != out2["gas_basis"], \
        "identical numbers must still be distinguishable by basis"
