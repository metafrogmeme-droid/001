"""Signature decoder. Public signals, not a verdict.

The 25 fixtures are the prototype's regression set. A pass means the
decoder still emits the codes those fixtures name. It is not a claim
about field accuracy. The lists are the bundled copies. Nothing here
opens a socket.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.sigdecoder import (
    DISCLAIMER,
    MAX_INPUT_CHARS,
    decode,
    decode_calldata,
    decode_text,
    flag_on,
    render_text,
)
from bot.sigdecoder.__main__ import main
from bot.sigdecoder.labels import counterparty_signals
from bot.sigdecoder.util import typed_data_digest
from tests.fixtures.sig_decoder_vectors import VECTORS
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "bot" / "sigdecoder"
JS_FLAG = ROOT / "app" / "lib" / "sig_decoder_flag.js"
VERDICT = ("is safe", "is a scam", "scammer", "safe to sign")


def _codes(result: dict) -> set[str]:
    return {row["code"] for row in result["signals"]}


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vector):
    result = decode(vector["input"], vector.get("kind", "auto"), vector["now"])
    codes = _codes(result)
    missing = set(vector["expect"]["must_include"]) - codes
    extra = set(vector["expect"]["must_exclude"]) & codes
    assert not missing and not extra, f"missing={missing} unexpected={extra} got={sorted(codes)}"
    assert result["summary"]
    assert result["disclaimer"] == DISCLAIMER
    assert result["assisted"] == "ai"
    assert result["reads_network"] is False


def test_no_verdict_words():
    for vector in VECTORS:
        result = decode(vector["input"], vector.get("kind", "auto"), vector["now"])
        blob = json.dumps(result).lower()
        for word in VERDICT:
            assert word not in blob, (vector["name"], word)
        assert "legal advice" not in blob


def test_calldata_approve_unlimited():
    data = "0x095ea7b3" + "00" * 12 + "99" * 20 + "ff" * 32
    result = decode_calldata("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", data)
    assert result["fn"] == "approve"
    assert result["amount"] == "UNLIMITED"
    assert any(row["code"] == "COUNTERPARTY_NOT_ON_ALLOWLIST" for row in result["signals"])


def test_calldata_setapprovalforall():
    data = "0xa22cb465" + "00" * 12 + "99" * 20 + "00" * 31 + "01"
    result = decode_calldata("0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D", data)
    assert result["fn"] == "setApprovalForAll"
    assert result["approved"] is True


def test_eip712_mail_digest():
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Person": [
                {"name": "name", "type": "string"},
                {"name": "wallet", "type": "address"},
            ],
            "Mail": [
                {"name": "from", "type": "Person"},
                {"name": "to", "type": "Person"},
                {"name": "contents", "type": "string"},
            ],
        },
        "primaryType": "Mail",
        "domain": {
            "name": "Ether Mail",
            "version": "1",
            "chainId": 1,
            "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
        },
        "message": {
            "from": {"name": "Cow", "wallet": "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"},
            "to": {"name": "Bob", "wallet": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"},
            "contents": "Hello, Bob!",
        },
    }
    assert typed_data_digest(typed) == "0xbe609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"


def test_disclaimer_matches_the_website_reader():
    assert DISCLAIMER == "AI-assisted. Public signals, not a verdict."
    js = JS_FLAG.read_text(encoding="utf-8")
    assert f"const DISCLAIMER = '{DISCLAIMER}';" in js
    rendered = render_text(decode("hello", "message", 1))
    assert rendered.endswith(DISCLAIMER + "\nAI-assisted.")


def test_flag_is_off_unless_the_value_is_1_or_true():
    assert flag_on({}) is False
    assert flag_on({"SIG_DECODER_ENABLED": ""}) is False
    assert flag_on({"SIG_DECODER_ENABLED": "yes"}) is False
    assert flag_on({"SIG_DECODER_ENABLED": "on"}) is False
    assert flag_on({"SIG_DECODER_ENABLED": " 1 "}) is True
    assert flag_on({"SIG_DECODER_ENABLED": "TRUE"}) is True


def test_absent_amount_is_not_read_as_zero():
    result = decode({
        "types": {"EIP712Domain": [{"name": "name", "type": "string"}]},
        "primaryType": "Permit",
        "domain": {"name": "Token", "chainId": 1, "verifyingContract": "0x" + "11" * 20},
        "message": {"owner": "0x" + "22" * 20, "spender": "0x" + "33" * 20, "deadline": 1},
    }, "typed", 1)
    codes = _codes(result)
    assert "AMOUNT_NOT_IN_PAYLOAD" in codes
    assert "UNLIMITED_AMOUNT" not in codes
    assert result["fields"]["amount"] == "absent"


def test_unreadable_lists_are_not_a_miss():
    import bot.sigdecoder.labels as labels

    saved = labels.LISTS_READ
    labels.LISTS_READ = False
    try:
        label, rows = counterparty_signals("0x" + "11" * 20, "spender")
    finally:
        labels.LISTS_READ = saved
    assert label is None
    codes = {row["code"] for row in rows}
    assert codes == {"LISTS_UNREADABLE"}
    assert labels.LISTS_READ is True


def test_the_package_does_not_fetch_or_name_a_key():
    blob = "\n".join(code_only(path.read_text(encoding="utf-8")) for path in PKG.rglob("*.py"))
    for word in (
        "SlowMist", "Grok", "legal advice", "requests", "httpx", "urllib",
        "ETH_PRIVATE_KEY", "HYPERLIQUID_PRIVATE_KEY", "https://", "socket",
    ):
        assert word not in blob, word
    assert (PKG / "data" / "lists.json").is_file()
    assert (PKG / "data" / "scamsniffer_address.json").is_file()


def test_the_command_is_recorded_as_admin_only():
    rows = (ROOT / "tests" / "command_gate_baseline.txt").read_text(encoding="utf-8").splitlines()
    assert "sigdecode is_admin" in rows


class _Host:
    def __init__(self, admin: bool) -> None:
        self.admin = admin
        self.sent: list[str] = []

    def _is_admin(self, update) -> bool:
        return self.admin

    def _lang(self, update) -> str:
        return "en"

    async def _send(self, update, text, reply_markup=None, edit=False) -> None:
        self.sent.append(text)


def _host(admin: bool):
    from bot.skills.sigdecoder_commands import SigDecoderCommands

    class Host(_Host, SigDecoderCommands):
        pass

    return Host(admin)


def _update(text: str | None, reply: str | None = None):
    replied = None if reply is None else SimpleNamespace(text=reply, caption=None)
    return SimpleNamespace(message=SimpleNamespace(text=text, reply_to_message=replied))


def test_sigdecode_refuses_a_non_admin_before_decoding(monkeypatch):
    monkeypatch.delenv("SIG_DECODER_ENABLED", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("decoded")

    monkeypatch.setattr("bot.skills.sigdecoder_commands.decode_text", boom)
    host = _host(False)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode hello"), None))
    assert len(host.sent) == 1
    assert "operator command" in host.sent[0]
    assert "Signals" not in host.sent[0]
    assert DISCLAIMER not in host.sent[0]


def test_sigdecode_admin_preview_while_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("SIG_DECODER_ENABLED", raising=False)
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode hello"), None))
    text = host.sent[0]
    assert text.startswith("Admin preview. Not public.")
    assert "hello" in text
    assert DISCLAIMER in text
    assert "AI-assisted." in text
    for word in VERDICT:
        assert word not in text.lower()


def test_sigdecode_stays_admin_and_drops_the_preview_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("SIG_DECODER_ENABLED", "1")
    refused = _host(False)
    import asyncio
    asyncio.run(refused._cmd_sigdecode(_update("/sigdecode hello"), None))
    assert "operator command" in refused.sent[0]
    assert "hello" not in refused.sent[0].split("operator command", 1)[-1]
    host = _host(True)
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode@RuneClawBot hello"), None))
    assert "Admin preview. Not public." not in host.sent[0]
    assert "hello" in host.sent[0]
    assert DISCLAIMER in host.sent[0]


def test_yes_and_on_stay_a_preview(monkeypatch):
    monkeypatch.setenv("SIG_DECODER_ENABLED", "yes")
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode hello"), None))
    assert host.sent[0].startswith("Admin preview. Not public.")


def test_sigdecode_escapes_html_and_reads_a_reply(monkeypatch):
    monkeypatch.setenv("SIG_DECODER_ENABLED", "true")
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode", reply="<b>hi</b>"), None))
    assert "<b>" not in host.sent[0]
    assert "&lt;b&gt;hi&lt;/b&gt;" in host.sent[0]


def test_sigdecode_names_the_exception_class_only(monkeypatch):
    monkeypatch.setenv("SIG_DECODER_ENABLED", "1")

    def boom(*_a, **_k):
        raise RuntimeError("secret key material")

    monkeypatch.setattr("bot.skills.sigdecoder_commands.decode_text", boom)
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode hello"), None))
    assert host.sent == ["RuntimeError\n" + DISCLAIMER]
    assert "secret" not in host.sent[0]


def test_sigdecode_refuses_an_oversized_request(monkeypatch):
    monkeypatch.setenv("SIG_DECODER_ENABLED", "1")
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode " + ("a" * (MAX_INPUT_CHARS + 1))), None))
    assert "too large" in host.sent[0]
    assert DISCLAIMER in host.sent[0]


def test_sigdecode_asks_for_a_payload_when_the_message_is_empty(monkeypatch):
    monkeypatch.delenv("SIG_DECODER_ENABLED", raising=False)
    host = _host(True)
    import asyncio
    asyncio.run(host._cmd_sigdecode(_update("/sigdecode"), None))
    assert "Paste a signature request" in host.sent[0]
    assert DISCLAIMER in host.sent[0]
    assert "Admin preview" not in host.sent[0]


def test_cli_attention_exits_2_and_a_failure_names_the_class(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(VECTORS[0])))
    assert main(["-"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["disclaimer"] == DISCLAIMER
    assert body["reads_network"] is False
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    assert main(["-", "--kind", "typed"]) == 1
    err = json.loads(capsys.readouterr().out)
    assert err["signals"] is None
    assert err["error"] == "AttributeError"
    assert "not-json" not in json.dumps(err)
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * (MAX_INPUT_CHARS + 1)))
    assert main(["-"]) == 0
    too = json.loads(capsys.readouterr().out)
    assert too["error"] == "too_large"
    assert too["signals"] is None


def test_decode_text_unwraps_a_fixture_and_leaves_typed_data_alone():
    vector = VECTORS[0]
    unwrapped = decode_text(json.dumps(vector))
    direct = decode(vector["input"], vector.get("kind", "auto"), vector["now"])
    assert _codes(unwrapped) == _codes(direct)
    typed = decode_text(json.dumps(vector["input"]), "auto", vector["now"])
    assert _codes(typed) == _codes(direct)
