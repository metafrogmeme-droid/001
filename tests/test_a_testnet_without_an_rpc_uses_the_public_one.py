"""A testnet with no WEB3_RPC_<NETWORK> uses its chain's public endpoint.

The admin signer's network picker read "— no RPC" on every chain of a deploy
that set none of the eleven WEB3_RPC_* keys (2026-10-18): the signer was armed
and could reach nothing. Each testnet row now carries the chain's free public
endpoint (checked against its chain id when it was written), used when the
operator has not set one, and the status says which it is so the picker can
name a shared, rate-limited endpoint as such.

Three readings, both arms: configured wins over public; public when unset;
none when the row has no endpoint (Scroll Sepolia) or the operator turned the
defaults off. A mainnet row never has one. Nothing here reaches a chain: the
one RPC call is recorded, not sent.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from bot.web import web3_exec_gate as gate
from bot.web import web3_signer as sg

OFF = {"WEB3_RPC_PUBLIC_DEFAULTS": "0"}


def _status(env):
    return {t["network"]: t for t in sg.signer_status(env)["testnets"]}


class TestTheTable:
    def test_every_testnet_but_scroll_has_a_public_https_endpoint(self):
        have = {n for n, r in gate.NETWORKS.items() if r.get("testnet") and r.get("public_rpc")}
        testnets = {n for n, r in gate.NETWORKS.items() if r.get("testnet")}
        assert testnets - have == {"scroll-sepolia"}, "a testnet lost its endpoint, or Scroll gained one unchecked"
        for n in have:
            u = urlparse(gate.NETWORKS[n]["public_rpc"])
            assert u.scheme == "https" and u.hostname and not u.username, n

    def test_no_mainnet_row_has_one_and_none_is_used_for_mainnet(self):
        assert not [n for n, r in gate.NETWORKS.items() if not r.get("testnet") and "public_rpc" in r]
        for n, r in gate.NETWORKS.items():
            if not r.get("testnet"):
                assert sg.rpc_url_for(n, {}) == "" and sg.rpc_source(n, {}) is None, n


class TestTheThreeReadings:
    def test_unset_reads_the_public_endpoint_and_says_so(self):
        by = _status({})
        assert by["sepolia"]["rpc_configured"] is True and by["sepolia"]["rpc_source"] == "public"
        assert sg.rpc_url_for("base-sepolia", {}) == gate.NETWORKS["base-sepolia"]["public_rpc"]

    def test_the_operator_s_endpoint_wins_and_is_named_configured(self):
        env = {"WEB3_RPC_BASE_SEPOLIA": "https://rpc.operator.invalid/base"}
        by = _status(env)
        assert by["base-sepolia"]["rpc_source"] == "configured"
        assert sg.rpc_url_for("base-sepolia", env) == "https://rpc.operator.invalid/base"
        assert by["sepolia"]["rpc_source"] == "public", "one chain's setting is not another's"

    def test_a_chain_with_no_endpoint_still_says_none(self):
        by = _status({})
        assert by["scroll-sepolia"]["rpc_configured"] is False and by["scroll-sepolia"]["rpc_source"] is None

    def test_the_defaults_turn_off(self):
        by = _status(dict(OFF, WEB3_RPC_SEPOLIA="https://rpc.operator.invalid/sep"))
        assert by["sepolia"]["rpc_source"] == "configured"
        assert by["base-sepolia"]["rpc_configured"] is False and by["base-sepolia"]["rpc_source"] is None


def test_prepare_on_an_unset_chain_calls_the_public_endpoint(monkeypatch):
    seen = []

    async def record(url, method, params):
        seen.append(url)
        return {"ok": False, "error": "rpc call failed"}

    monkeypatch.setattr(sg, "_rpc_call", record)
    out = asyncio.run(sg.prepare_tx(network="linea-sepolia",
                                    address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", env={}))
    assert out["ok"] is False
    assert seen and set(seen) == {gate.NETWORKS["linea-sepolia"]["public_rpc"]}

    seen.clear()
    out = asyncio.run(sg.prepare_tx(network="linea-sepolia",
                                    address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", env=OFF))
    assert out["ok"] is False and "rpc" in out["error"].lower() and seen == []
