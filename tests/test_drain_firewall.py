"""Claw Firewall scan and the testnet deploy script.

The flag is off unless it is 1 or true. The library still reads when the
flag is off: the route is the door. Fuji does not record a 7702 delegation.
A secret is not echoed. An unread chain is not a clean list.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from bot.approval_hygiene import CHAINS, ChainRead, OpenApproval
from bot.drain_firewall import (
    COULD_READ,
    PARTIAL_LINE,
    SECRET_REPLY,
    SUPPORTS_7702,
    TESTNETS,
    UNREAD_LINE,
    flag_on,
    main,
    scan_account,
)

ROOT = Path(__file__).resolve().parents[1]
WALLET = "0x" + "11" * 20
TOKEN = "0x" + "aa" * 20
SPENDER = "0x" + "bb" * 20
DELEGATE = "0x" + "d0" * 20
NOW = 1_762_000_000
SEED = " ".join(["alpha"] * 12)


class _List:
    def get(self):
        return set()


class _Reader:
    def __init__(self, mainnet, testnets, delegations):
        self.mainnet = mainnet
        self.testnets = testnets
        self.delegations = delegations
        self.asked = []

    def read(self, address):
        return self.mainnet

    def delegation(self, chain_id, owner):
        self.asked.append(chain_id)
        return self.delegations.get(chain_id, ("none", None))

    def _chain(self, chain_id, name, owner):
        return self.testnets.get(chain_id, ChainRead(chain_id, name, "unreadable"))


def _mainnet_ok():
    return (
        ChainRead(1, "Ethereum", "ok", ()),
        ChainRead(8453, "Base", "ok", ()),
        ChainRead(43114, "Avalanche C-Chain", "ok", ()),
        ChainRead(42161, "Arbitrum", "ok", ()),
    )


def _mainnet_unread():
    return tuple(ChainRead(chain_id, name, "unreadable") for chain_id, name in CHAINS)


def test_flag_words_match_the_panic_button():
    assert flag_on({}) is False
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "0"}) is False
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "false"}) is False
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "yes"}) is False
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "on"}) is False
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "1"}) is True
    assert flag_on({"DRAIN_FIREWALL_ENABLED": "TRUE"}) is True
    assert flag_on({"DRAIN_FIREWALL_ENABLED": " 1 "}) is True
    assert 43113 not in SUPPORTS_7702
    assert 84532 in SUPPORTS_7702
    fuji = [row for row in TESTNETS if row[0] == 43113]
    assert fuji == [(43113, "Avalanche Fuji", False)]


def test_a_secret_is_not_echoed_and_the_flag_off_library_still_reads(capsys):
    code = main(env={"DRAIN_FIREWALL_FROM_ROUTE": "1"}, stdin=json.dumps({"address": SEED}))
    out = capsys.readouterr().out
    assert code == 0
    assert SEED not in out
    assert SECRET_REPLY in out
    closed = main(env={}, stdin=json.dumps({"address": WALLET}))
    closed_out = capsys.readouterr().out
    assert closed == 0
    assert json.loads(closed_out)["available"] is False
    reader = _Reader(
        _mainnet_ok(),
        {
            84532: ChainRead(84532, "Base Sepolia", "ok", ()),
            421614: ChainRead(421614, "Arbitrum Sepolia", "unreadable"),
            43113: ChainRead(43113, "Avalanche Fuji", "ok", (
                OpenApproval(43113, "Avalanche Fuji", TOKEN, SPENDER, "erc20", 5, 10, True, NOW),
            )),
        },
        {84532: ("delegated", DELEGATE), 43113: ("delegated", DELEGATE)},
    )
    result = scan_account(WALLET, reader=reader, exploits=_List(), now=NOW)
    assert WALLET not in json.dumps(result)
    assert result["read_failed"] is False
    assert result["message"] == COULD_READ
    assert result["partial_line"] == PARTIAL_LINE
    assert "Arbitrum Sepolia" in result["unread_chains"]
    kinds = {(item["chain_id"], item["kind"]) for item in result["items"]}
    assert (84532, "delegation_7702") in kinds
    assert (43113, "erc20") in kinds
    assert (43113, "delegation_7702") not in kinds
    assert 43113 not in reader.asked
    assert 84532 in reader.asked


def test_every_unread_chain_is_not_a_clean_list():
    reader = _Reader(_mainnet_unread(), {}, {})
    result = scan_account(WALLET, reader=reader, exploits=_List(), now=NOW)
    assert result["read_failed"] is True
    assert result["message"] == UNREAD_LINE
    assert result["partial_line"] is None
    assert result["items"] == []
    names = [name for _, name in CHAINS] + [name for _, name, _ in TESTNETS]
    for name in names:
        assert name in result["unread_chains"]


def test_deploy_script_refuses_the_server_fuji_delegation_and_mainnet():
    script = ROOT / "contracts" / "drainfw" / "script" / "deploy.sh"
    text = script.read_text(encoding="utf-8")
    assert not any(
        line.lstrip().startswith("source ") or line.lstrip().startswith(". ")
        for line in text.splitlines()
    )
    assert ".env" in text  # the comment that says the script never sources it
    assert "never sources" in text
    env = os.environ.copy()
    for key in (
        "RUNECLAW_SERVER",
        "WEB3_SIGNER_PRIVATE_KEY",
        "HYPERLIQUID_PRIVATE_KEY",
        "SAR_ORACLE_SIGNING_KEY",
        "HRC_FEED_SIGNER_KEY",
        "HASHKEY_SEAL_SIGNER_KEY",
        "ETH_PRIVATE_KEY",
    ):
        env.pop(key, None)

    def run(extra, args):
        got = subprocess.run(
            ["bash", str(script), *args],
            env={**env, **extra},
            capture_output=True,
            text=True,
            check=False,
        )
        return got

    server = run({"RUNECLAW_SERVER": "1"}, ["--network", "fuji"])
    assert server.returncode == 1
    assert "RUNECLAW_SERVER" in server.stderr
    assert "broadcast" not in server.stderr.lower() or "does not" in server.stderr

    leaked = run({"WEB3_SIGNER_PRIVATE_KEY": "present"}, ["--network", "base-sepolia"])
    assert leaked.returncode == 1
    assert "WEB3_SIGNER_PRIVATE_KEY" in leaked.stderr
    assert "present" not in leaked.stderr

    fuji = run({}, ["--network", "fuji", "--delegate"])
    assert fuji.returncode == 1
    assert "Base Sepolia" in fuji.stderr

    mainnet = run({}, ["--network", "mainnet"])
    assert mainnet.returncode == 1
    assert "Mainnet" in mainnet.stderr

    arb = run({}, ["--network", "arbitrum-sepolia", "--delegate"])
    assert arb.returncode == 1
    assert "Base Sepolia" in arb.stderr
