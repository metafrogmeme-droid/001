"""Incident pack. Facts and public labels, from a stand-in reader.

The five prototype checks are here, narrowed to this version: no report
templates, testnet and local only, and a failed read is not an empty pack.
Nothing in this file opens a socket.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.incident_pack import (
    CHAINS,
    FLAG,
    TOPIC,
    IncidentChainRefused,
    IncidentRequestRefused,
    IncidentSourceUnreadable,
    Source,
    command_text,
    flag_on,
    make_pack,
    merkle,
    render_notes,
    resolve_chain,
    run_body,
    verify,
)
from bot.skills.incident_pack_commands import IncidentPackCommands
from bot.web.mainnet_guard import mainnet_chain_ids
from bot.web.web3_exec_gate import NETWORKS

V = "0x" + "86" * 20
DR = "0x" + "a3" * 20
A1, A2 = "0x" + "32" * 20, "0x" + "53" * 20
TOK = "0x" + "fb" * 20
T0 = 1698763919
ROOT = Path(__file__).resolve().parents[1]
BANNED_TEMPLATES = (
    "10_police_nl_aangifte.md",
    "50_stablecoin_issuer_notice.md",
    "60_seal911_summary.md",
)
BANNED_WORDS = ("police", "ic3", "action fraud", "stablecoin issuer", "legal advice", "aangifte")
VERDICT = ("is a scam", "is safe", "malicious", "criminal")


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _z(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeSrc:
    """The prototype's reader. No transport."""

    c = {"bs": "fake", "rpc": "fake"}

    def block_at(self, ts, closest):
        return 100 if closest == "after" else 200

    def api(self, **p):
        action = p.get("action")
        if action == "txlist":
            return [{
                "hash": "0xt1", "timeStamp": str(T0), "blockNumber": "150",
                "from": V, "to": TOK, "value": "0", "isError": "0",
                "input": "0x095ea7b3" + _pad(DR)[2:] + "f" * 64,
            }]
        if action == "tokentx":
            return [
                {
                    "hash": "0xt2", "timeStamp": str(T0 + 60), "blockNumber": "151",
                    "from": V, "to": A1, "value": "800", "contractAddress": TOK,
                    "tokenSymbol": "TKN", "tokenDecimal": "0",
                },
                {
                    "hash": "0xt2", "timeStamp": str(T0 + 60), "blockNumber": "151",
                    "from": V, "to": A2, "value": "200", "contractAddress": TOK,
                    "tokenSymbol": "TKN", "tokenDecimal": "0",
                },
            ]
        if action == "getLogs" and p.get("topic0") == TOPIC["erc20_or_721_approval"]:
            return [{
                "address": TOK, "blockNumber": hex(150), "timeStamp": hex(T0),
                "transactionHash": "0xt1",
                "topics": [TOPIC["erc20_or_721_approval"], _pad(V), _pad(DR)],
                "data": "0x" + "f" * 64,
            }]
        return []

    def tx_v2(self, h):
        return {}

    def addr_v2(self, a):
        if a == DR:
            return {"metadata": {"tags": [{"name": "Fake Drainer Tag", "tagType": "name"}]}}
        return {}

    def get_code(self, a):
        return "0xef0100" + "89" * 20


def test_flag_is_off_unless_the_value_is_1_or_true(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert flag_on({}) is False
    assert flag_on({FLAG: ""}) is False
    assert flag_on({FLAG: "0"}) is False
    assert flag_on({FLAG: "false"}) is False
    assert flag_on({FLAG: "yes"}) is False
    assert flag_on({FLAG: "on"}) is False
    assert flag_on({FLAG: "1"}) is True
    assert flag_on({FLAG: "true"}) is True
    assert flag_on({FLAG: "TRUE"}) is True


def test_chains_are_local_or_the_testnet_table():
    assert set(CHAINS) == {"local", "sepolia", "base-sepolia", "arbitrum-sepolia"}
    banned = mainnet_chain_ids()
    for name, spec in CHAINS.items():
        assert spec["id"] not in banned
        if name == "local":
            assert spec["id"] == 31337
            assert "127.0.0.1" in spec["rpc"] and "127.0.0.1" in spec["bs"]
            continue
        assert NETWORKS[name]["testnet"] is True
        assert spec["id"] == NETWORKS[name]["chain_id"]
        assert spec["rpc"] == NETWORKS[name]["public_rpc"]
        assert "sepolia" in spec["bs"]
    for name in ("eth", "ethereum", "mainnet", "base", "arbitrum"):
        with pytest.raises(IncidentChainRefused):
            resolve_chain(name)


def test_pack_end_to_end(tmp_path):
    pack = make_pack(V, T0 - 3600, T0 + 3600, "local", str(tmp_path), src=FakeSrc())
    kinds = [e["kind"] for e in pack["events"]]
    assert kinds.count("token_out") == 2
    assert "erc20_or_721_approval" in kinds and "tx_sent" in kinds
    text = "\n".join(pack["findings"])
    assert "EIP-7702 delegation" in text
    assert "NOT sent by this address" in text
    assert "Fake Drainer Tag" in text
    approval = [e for e in pack["events"] if e["kind"] == "erc20_or_721_approval"][0]
    assert approval["unlimited"] and approval["spender"] == DR
    sent = [e for e in pack["events"] if e["kind"] == "tx_sent"][0]
    assert sent["decoded"]["fn"] == "approve" and "UNLIMITED_AMOUNT" in sent["signals"]
    assert verify(pack)
    for name in ("pack.json", "timeline.md", "70_self_checklist.md", "00_later.md"):
        assert (tmp_path / name).is_file()
    for name in BANNED_TEMPLATES:
        assert not (tmp_path / name).exists()
        assert not (tmp_path / "drafts" / name).exists()
    timeline = (tmp_path / "timeline.md").read_text(encoding="utf-8")
    assert "CET" in timeline and "UTC" in timeline and pack["seal"]["sha256"] in timeline
    checklist = (tmp_path / "70_self_checklist.md").read_text(encoding="utf-8")
    later = (tmp_path / "00_later.md").read_text(encoding="utf-8")
    assert "Revoke" in checklist and "Move any remaining" in checklist
    assert pack["seal"]["sha256"] in checklist
    assert "More templates will come later." in later
    assert "{{" not in checklist and "{{" not in later


def test_tamper_detected():
    pack = make_pack(V, T0 - 3600, T0 + 3600, "local", None, src=FakeSrc())
    changed = copy.deepcopy(pack)
    changed["events"][0]["amount_raw"] = "1"
    assert verify(pack) and not verify(changed)
    again = json.loads(json.dumps(pack))
    assert verify(again)


def test_notes_have_no_verdict_and_no_report(tmp_path):
    pack = make_pack(V, T0 - 3600, T0 + 3600, "local", str(tmp_path), src=FakeSrc())
    notes = render_notes(pack)
    assert set(notes) == {"00_later.md", "70_self_checklist.md"}
    timeline = (tmp_path / "timeline.md").read_text(encoding="utf-8")
    blob = "\n".join([
        timeline.lower(),
        json.dumps(pack["findings"]).lower(),
        json.dumps(pack["disclaimer"]).lower(),
        notes["70_self_checklist.md"].lower(),
        notes["00_later.md"].lower(),
    ])
    for word in VERDICT:
        assert word not in blob
    for word in BANNED_WORDS:
        assert word not in blob
    shipped = (ROOT / "bot" / "incident_pack.py").read_text(encoding="utf-8").lower()
    templates = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "bot" / "incident_pack_templates").glob("*.md")
    )
    templates = templates.lower()
    for word in BANNED_WORDS:
        assert word not in shipped and word not in templates


def test_no_network_send_capability():
    src = (ROOT / "bot" / "incident_pack.py").read_text(encoding="utf-8")
    lowered = src.lower()
    for bad in ("smtplib", "sendmail", 'requests.post("https://', "slack", "telegram"):
        assert bad not in lowered or bad == 'requests.post("https://'
    for bad in ("eth_sendrawtransaction", "eth_sendtransaction", "sign_transaction", "private_key"):
        assert bad not in lowered


def test_merkle_deterministic():
    assert merkle([b"a", b"b", b"c"]) == merkle([b"a", b"b", b"c"]) != merkle([b"a", b"c", b"b"])


def test_an_unreadable_source_is_not_an_empty_pack():
    class Boom:
        def block_at(self, ts, closest):
            raise IncidentSourceUnreadable()

    with pytest.raises(IncidentSourceUnreadable):
        make_pack(V, T0 - 3600, T0 + 3600, "local", None, src=Boom())
    body = run_body(
        {"address": V, "start": _z(T0 - 3600), "end": _z(T0 + 3600), "chain": "local"},
        src=Boom(),
    )
    assert body["ok"] is False and body["readable"] is False
    assert body["error"] == "IncidentSourceUnreadable"
    assert "events" not in body


def test_mainnet_and_a_secret_do_not_read(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("reader")

    monkeypatch.setattr("bot.incident_pack.Source", boom)
    refused = run_body({
        "address": V, "start": _z(T0 - 3600), "end": _z(T0 + 3600), "chain": "eth",
    })
    assert refused["refused"] == "chain"
    assert "events" not in refused
    pasted = " ".join(["alpha"] * 12)
    secret = run_body({
        "address": pasted, "start": _z(T0 - 60), "end": _z(T0 + 60), "chain": "local",
    })
    assert secret["refused"] == "secret"
    dumped = json.dumps(secret)
    assert V not in dumped and pasted not in dumped
    with pytest.raises(IncidentRequestRefused):
        make_pack(V, T0, T0 + WINDOW_TOO_LONG(), "local", None, src=FakeSrc())


def WINDOW_TOO_LONG() -> int:
    from bot.incident_pack import WINDOW_MAX
    return WINDOW_MAX + 1


def test_usage_does_not_construct_a_reader(monkeypatch):
    monkeypatch.setattr(Source, "__init__", lambda self, chain: (_ for _ in ()).throw(AssertionError(chain)))
    text = command_text(["0xabc"])
    assert text.startswith("Usage:")
    assert "127.0.0.1" not in text


def test_the_command_is_admin_only_and_previews_while_the_flag_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)

    class Host:
        def __init__(self, admin):
            self.admin = admin
            self.sent = []
            self._incident_src = FakeSrc()

        def _is_admin(self, update):
            return self.admin

        async def _send(self, update, text, reply_markup=None, edit=False):
            self.sent.append(text)

    class Ctx:
        def __init__(self, args):
            self.args = args

    stranger = Host(False)
    stranger._cmd_incidentpack = IncidentPackCommands._cmd_incidentpack.__get__(stranger, Host)
    asyncio.run(stranger._cmd_incidentpack(None, Ctx([V, _z(T0 - 3600), _z(T0 + 3600), "local"])))
    assert stranger.sent == ["Not found."]

    admin = Host(True)
    admin._cmd_incidentpack = IncidentPackCommands._cmd_incidentpack.__get__(admin, Host)
    asyncio.run(admin._cmd_incidentpack(None, Ctx([V, _z(T0 - 3600), _z(T0 + 3600), "local"])))
    reply = admin.sent[0]
    assert reply.startswith("Admin preview. Not public.")
    assert "Fake Drainer Tag" in reply and "NOT sent by this address" in reply
    assert "More templates will come later." in reply
    assert "Nothing was sent." in reply
    for word in VERDICT:
        assert word not in reply.lower()

    monkeypatch.setenv(FLAG, "1")
    admin.sent.clear()
    asyncio.run(admin._cmd_incidentpack(None, Ctx([V, _z(T0 - 3600), _z(T0 + 3600), "local"])))
    assert "Admin preview. Not public." not in admin.sent[0]
    assert "Nothing was sent." in admin.sent[0]


def test_the_command_is_recorded_as_admin_only():
    from bot.skills.command_catalog import all_entries
    from tests.command_gates import command_gates

    assert command_gates()["incidentpack"] == "is_admin"
    assert all_entries()["incidentpack"][1] == "admin"


def test_public_reader_does_not_follow_another_flag():
    from bot import admin_preview

    assert admin_preview.public_on("INCIDENT_PACK_ENABLED", env={}) is False
    assert admin_preview.public_on(
        "INCIDENT_PACK_ENABLED", env={"DUEL_CONFIDENCE_ENABLED": "on"},
    ) is False
    assert admin_preview.public_on("INCIDENT_PACK_ENABLED", env={FLAG: "1"}) is True
    assert admin_preview.flag_on_for("999001", "INCIDENT_PACK_ENABLED", env={}, store={}) is False
