"""Shadow-mode sandwich restitution.

Recorded windows live in tests/fixtures/srb_cases.json. The run records what
would be paid and pays nothing. Public output is a signal: no named wallet,
no amount, no token. Amounts stay on the admin view.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from bot.srb.flag import flag_on
from bot.srb.shadow import ClusterRefused, build_report, demo_report, issue_claim_token

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "fixtures" / "srb_cases.json").read_text(encoding="utf-8"))
BOND_UNITS = 4242424242
WALLET_LABELS = ("wallet-a", "wallet-a2", "wallet-v", "wallet-b", "funder")
PUBLIC_FORBIDDEN = {
    "bond_base_units", "loss_base_units", "would_record_base_units",
    "signer", "wallet", "attacker", "victim", "reason", "token",
    "claim_token", "subrogation", "stake", "rclaw", "lamports", "sol",
}


def _run(name: str, **kwargs):
    return build_report(CASES[name], **kwargs)


def _public_blob(report: dict) -> str:
    return json.dumps(report["public"])


def _assert_public_signal(report: dict) -> None:
    blob = _public_blob(report)
    for label in WALLET_LABELS:
        assert label not in blob
    assert "SOL" not in blob
    assert "$" not in blob
    assert "✓" not in blob
    assert str(BOND_UNITS) not in blob
    assert report["public"]["pays"] is False
    assert report["public"]["mode"] == "shadow"
    for key in PUBLIC_FORBIDDEN:
        assert key not in blob
    for signal in report["public"]["signals"]:
        assert signal["kind"] == "public_signal"
        assert "named wallet" not in signal["sentence"]
        assert "Nothing is paid." in signal["sentence"]


def test_flag_on_is_off_unless_set(monkeypatch):
    assert flag_on({}) is False
    assert flag_on({"SRB_ENABLED": ""}) is False
    assert flag_on({"SRB_ENABLED": "0"}) is False
    assert flag_on({"SRB_ENABLED": "false"}) is False
    assert flag_on({"SRB_ENABLED": "true"}) is True
    assert flag_on({"SRB_ENABLED": "1"}) is True
    monkeypatch.delenv("SRB_ENABLED", raising=False)
    off = _run("classic")
    assert off["admin"]["flag_on"] is False
    assert "flag_on" not in _public_blob(off)
    monkeypatch.setenv("SRB_ENABLED", "1")
    on = _run("classic")
    assert on["admin"]["flag_on"] is True
    assert "flag_on" not in _public_blob(on)
    assert on["public"]["providers"][0]["status"] == "Bonded"


def test_classic_records_a_public_signal_and_pays_nothing():
    report = _run("classic")
    _assert_public_signal(report)
    assert report["public"]["providers"][0]["status"] == "Bonded"
    signal = report["public"]["signals"][0]
    assert signal["restitution"] == "recorded"
    assert signal["impact_bps"] == 2000
    assert signal["restitution_bps"] == 2000
    assert signal["wide"] is False
    admin = report["admin"]["amounts"]
    assert admin["bond_base_units"] == BOND_UNITS
    assert admin["signals"][0]["would_record_base_units"] == 2
    assert admin["signals"][0]["loss_base_units"] == 2
    assert report["admin"]["ledger"][0]["paid"] is False
    assert report["admin"]["ledger"][0]["mode"] == "shadow"


def test_amounts_stay_off_the_public_view():
    report = _run("classic")
    assert "amounts" not in report["public"]
    assert report["public"]["providers"][0]["status"] == "Bonded"
    assert "Bonded ✓" not in _public_blob(report)
    assert report["admin"]["amounts"]["bond_base_units"] == BOND_UNITS


def test_wide_window_is_a_signal():
    report = _run("wide")
    signal = report["public"]["signals"][0]
    assert signal["wide"] is True
    assert signal["slot_lo"] == 10
    assert signal["slot_hi"] == 13
    assert signal["restitution"] == "recorded"


@pytest.mark.parametrize("name", [
    "false_unrelated",
    "false_no_harm",
    "false_other_pool",
    "false_outside_window",
    "false_no_quote",
])
def test_false_positives_are_not_signals(name):
    report = _run(name)
    assert report["public"]["signals"] == []
    assert report["admin"]["ledger"] == []
    assert report["public"]["providers"][0]["status"] == "Bonded"


@pytest.mark.parametrize("name,reason", [
    ("self_signer", "self_signer"),
    ("self_funding", "self_funding"),
    ("self_cex", "self_cex"),
    ("profit_to_self", "profit_to_self"),
    ("profit_not_shown", "profit_not_shown"),
    ("net_loss", "net_loss"),
    ("net_unreadable", "net_unreadable"),
])
def test_self_dealing_is_withheld_and_not_named_in_public(name, reason):
    report = _run(name)
    _assert_public_signal(report)
    assert report["public"]["signals"][0]["restitution"] == "withheld"
    assert "restitution_bps" not in report["public"]["signals"][0]
    assert report["admin"]["amounts"]["signals"][0]["reason"] == reason
    assert report["admin"]["amounts"]["signals"][0]["would_record_base_units"] is None
    assert report["admin"]["ledger"][0]["paid"] is False


def test_a_dispute_inside_the_window_drops_the_record():
    report = _run("dispute")
    assert report["public"]["signals"][0]["restitution"] == "withheld"
    assert report["admin"]["amounts"]["signals"][0]["reason"] == "disputed"


def test_a_late_dispute_does_not_erase_a_closed_window():
    report = _run("dispute_late")
    assert report["public"]["signals"][0]["restitution"] == "recorded"
    assert report["admin"]["amounts"]["signals"][0]["reason"] == "would_record"


def test_provider_cannot_leave_before_the_delay():
    report = _run("exit_early")
    assert report["admin"]["release"] == "held"
    assert report["public"]["providers"][0]["status"] == "Unbonding"
    assert report["admin"]["label"] == "Unbonding"
    assert str(BOND_UNITS) not in _public_blob(report)
    assert report["admin"]["amounts"]["bond_base_units"] == BOND_UNITS
    assert report["admin"]["ledger"][0]["paid"] is False


def test_provider_can_release_after_the_delay_without_a_payment():
    report = _run("exit_after_delay")
    assert report["admin"]["release"] == "released"
    assert report["public"]["providers"][0]["status"] == "Unbonded"
    assert report["admin"]["label"] == "Unbonded"
    assert report["admin"]["ledger"][0]["paid"] is False
    assert "transfer" not in _public_blob(report)


def test_a_receipt_outside_the_batch_root_is_withheld():
    held = _run("uncommitted")
    early = _run("batch_open")
    for report in (held, early):
        assert report["public"]["signals"][0]["restitution"] == "withheld"
        assert report["admin"]["amounts"]["signals"][0]["reason"] == "uncommitted"
        assert report["admin"]["ledger"][0]["paid"] is False


def test_a_methodology_hash_waits_out_its_timelock():
    pending = _run("methodology_pending")
    assert pending["admin"]["amounts"]["signals"][0]["reason"] == "methodology"
    active = _run("methodology_active")
    assert active["public"]["signals"][0]["restitution"] == "recorded"


def test_an_open_claim_window_does_not_record_a_payment():
    report = _run("open_window")
    assert report["public"]["signals"][0]["restitution"] == "pending"
    assert "restitution_bps" not in report["public"]["signals"][0]
    assert report["admin"]["amounts"]["signals"][0]["would_record_base_units"] is None
    assert report["admin"]["ledger"][0]["paid"] is False


def test_the_per_swap_cap_limits_the_recorded_figure():
    report = _run("cap")
    assert report["admin"]["amounts"]["signals"][0]["loss_base_units"] == 2
    assert report["admin"]["amounts"]["signals"][0]["would_record_base_units"] == 1
    assert report["public"]["signals"][0]["restitution_bps"] == 1000


def test_devnet_recorded_window_runs_and_mainnet_observe_does_not_fetch(tmp_path):
    report = _run("devnet_recorded")
    assert report["public"]["cluster"] == "devnet"
    assert report["public"]["signals"][0]["restitution"] == "recorded"
    assert "claim_token" not in _public_blob(report)
    token = report["admin"]["amounts"]["signals"][0]["claim_token"]
    assert token["kind"] == "subrogation_claim"
    assert token["cluster"] == "devnet"
    ledger = tmp_path / "shadow.jsonl"
    calls = []

    def transport(url):
        calls.append(url)
        raise AssertionError("transport was called")

    observed = build_report(
        CASES["mainnet_label"], transport=transport, ledger_path=str(ledger),
    )
    assert calls == []
    assert observed["public"]["cluster"] == "mainnet"
    assert observed["public"]["pays"] is False
    line = json.loads(ledger.read_text(encoding="utf-8"))
    assert line["paid"] is False
    assert "claim_token" not in json.dumps(observed)


def test_a_live_devnet_read_signs_and_a_live_mainnet_read_does_not(monkeypatch):
    seen = []

    def transport(url):
        seen.append(url)
        body = dict(CASES["classic"])
        body["cluster"] = "devnet"
        return body

    report = build_report(
        {"cluster": "https://api.devnet.solana.com", "fetch": True},
        transport=transport,
    )
    assert seen == ["https://api.devnet.solana.com"]
    assert report["public"]["cluster"] == "devnet"
    assert report["public"]["signals"][0]["restitution"] == "recorded"
    assert report["admin"]["amounts"]["signals"][0]["claim_token"]["cluster"] == "devnet"

    def explode(*_args, **_kwargs):
        raise AssertionError("signer")

    monkeypatch.setattr("bot.srb.shadow._sign", explode)
    monkeypatch.setattr("bot.srb.shadow._signer_seed", explode)
    monkeypatch.setattr(
        "bot.srb.shadow.Ed25519PrivateKey.from_private_bytes", explode,
    )
    main_seen = []

    def main_transport(url):
        main_seen.append(url)
        body = dict(CASES["mainnet_label"])
        body["cluster"] = "mainnet-beta"
        return body

    observed = build_report(
        {"cluster": "https://api.mainnet-beta.solana.com", "fetch": True},
        transport=main_transport,
    )
    assert main_seen == ["https://api.mainnet-beta.solana.com"]
    assert observed["public"]["cluster"] == "mainnet"
    assert observed["public"]["pays"] is False
    assert observed["admin"]["ledger"][0]["paid"] is False
    blob = json.dumps(observed)
    assert "signature" not in blob
    assert "claim_token" not in blob
    assert "keypair" not in blob.lower()


def test_the_ledger_file_records_the_shadow_row_and_refuses_a_payment(tmp_path):
    path = tmp_path / "shadow.jsonl"
    report = build_report(CASES["classic"], ledger_path=str(path))
    line = json.loads(path.read_text(encoding="utf-8"))
    assert line["paid"] is False
    assert line["decision"] == "would_record"
    assert line == report["admin"]["ledger"][0]


def test_the_demo_window_matches_the_classic_record():
    demo = demo_report()
    classic = _run("classic")
    assert demo["public"]["providers"][0]["status"] == "Bonded"
    assert demo["public"]["signals"][0]["signal_id"] == classic["public"]["signals"][0]["signal_id"]
    assert str(BOND_UNITS) not in json.dumps(demo["public"])
    assert demo["admin"]["amounts"]["bond_base_units"] == BOND_UNITS


def test_claim_token_runs_on_devnet_and_refuses_mainnet():
    issued = issue_claim_token("devnet", "sig-v")
    assert issued["kind"] == "subrogation_claim"
    assert issued["cluster"] == "devnet"
    assert issued["token"]
    assert issue_claim_token("testnet", "sig-v")["cluster"] == "testnet"
    for source in ("mainnet", "mainnet-beta", "https://api.mainnet-beta.solana.com"):
        with pytest.raises(ClusterRefused):
            issue_claim_token(source, "sig-v")
    fixture = _run("classic")
    assert "claim_token" not in json.dumps(fixture)


def test_the_mainnet_path_has_no_signer_or_keypair():
    import ast
    import inspect

    from bot.srb import shadow

    src = inspect.getsource(shadow._observe_mainnet)
    tree = ast.parse(src)
    names = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)]
    attrs = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
    banned = {"_sign", "_signer_seed", "Keypair", "Ed25519PrivateKey", "from_private_bytes"}
    assert banned.isdisjoint(set(names) | set(attrs))
    tree_src = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "bot" / "srb").rglob("*.py")
    )
    assert "Keypair" not in tree_src
    assert "id.json" not in tree_src


def test_admin_label_names_the_bond_in_sol_and_the_public_status_does_not():
    report = _run("classic")
    assert report["admin"]["label"] == "Bonded ✓ 4.242424242 SOL"
    assert report["public"]["providers"][0]["status"] == "Bonded"
    assert "✓" not in _public_blob(report)
    assert "SOL" not in _public_blob(report)
    assert str(BOND_UNITS) not in _public_blob(report)


def test_the_static_page_does_not_bake_in_the_bond():
    html = (ROOT / "app" / "public" / "srb.html").read_text(encoding="utf-8")
    assert "Bonded ✓" not in html
    assert str(BOND_UNITS) not in html
    assert re.search(r"\bSOL\b", html) is None
    assert "$" not in html
    page = (ROOT / "app" / "public" / "js" / "srb.js").read_text(encoding="utf-8")
    assert str(BOND_UNITS) not in page
    assert "body.admin" in page
    assert "admin.label" in page


class _SrbHost:
    def __init__(self, admin: bool) -> None:
        self.admin = admin
        self.sent: list[str] = []

    def _is_admin(self, update) -> bool:
        return self.admin

    def _lang(self, update) -> str:
        return "en"

    async def _send(self, update, text, reply_markup=None, edit=False) -> None:
        self.sent.append(text)


def _srb_host(admin: bool):
    from bot.skills.srb_commands import SrbCommands

    class Host(_SrbHost, SrbCommands):
        pass

    return Host(admin)


def test_srb_command_refuses_a_non_admin_before_any_amount(monkeypatch):
    monkeypatch.delenv("SRB_ENABLED", raising=False)

    def boom():
        raise RuntimeError("secret key material")

    monkeypatch.setattr("bot.skills.srb_commands.demo_report", boom)
    host = _srb_host(False)
    asyncio.run(host._cmd_srb(None, None))
    assert len(host.sent) == 1
    assert "operator command" in host.sent[0]
    assert "Bonded" not in host.sent[0]
    assert "✓" not in host.sent[0]
    assert re.search(r"\bSOL\b", host.sent[0]) is None
    assert str(BOND_UNITS) not in host.sent[0]
    assert "RuntimeError" not in host.sent[0]
    assert "secret" not in host.sent[0]


def test_srb_command_admin_preview_is_bonded_and_the_units_stay_here(monkeypatch):
    monkeypatch.delenv("SRB_ENABLED", raising=False)
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    text = host.sent[0]
    assert text.startswith("Admin preview. Not public.")
    assert "SRB_ENABLED is off." in text
    assert "provider-a: Bonded ✓ 4.242424242 SOL" in text
    assert "$" not in text
    assert f"Bond base units: {BOND_UNITS}" in text
    assert "Recorded base units: 2" in text
    assert "Nothing is paid." in text


def test_srb_command_stays_admin_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("SRB_ENABLED", "1")
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    text = host.sent[0]
    assert text.startswith("Admin preview. Not public.")
    assert "SRB_ENABLED is on." in text
    assert "SRB_ENABLED is off." not in text
    assert "provider-a: Bonded ✓ 4.242424242 SOL" in text


def test_srb_command_names_the_exception_class_only(monkeypatch):
    monkeypatch.setenv("SRB_ENABLED", "1")

    def boom():
        raise RuntimeError("secret key material")

    monkeypatch.setattr("bot.skills.srb_commands.demo_report", boom)
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    assert host.sent == ["RuntimeError"]
