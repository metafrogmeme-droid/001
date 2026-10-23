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
from bot.srb.shadow import ClusterRefused, build_report, demo_report

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


def test_flag_on_is_off_unless_set():
    assert flag_on({}) is False
    assert flag_on({"SRB_ENABLED": ""}) is False
    assert flag_on({"SRB_ENABLED": "0"}) is False
    assert flag_on({"SRB_ENABLED": "false"}) is False
    assert flag_on({"SRB_ENABLED": "true"}) is True
    assert flag_on({"SRB_ENABLED": "1"}) is True


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
    assert str(BOND_UNITS) not in _public_blob(report)
    assert report["admin"]["amounts"]["bond_base_units"] == BOND_UNITS
    assert report["admin"]["ledger"][0]["paid"] is False


def test_provider_can_release_after_the_delay_without_a_payment():
    report = _run("exit_after_delay")
    assert report["admin"]["release"] == "released"
    assert report["public"]["providers"][0]["status"] == "Unbonded"
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


def test_devnet_recorded_window_runs_and_mainnet_does_not(tmp_path):
    report = _run("devnet_recorded")
    assert report["public"]["cluster"] == "devnet"
    assert report["public"]["signals"][0]["restitution"] == "recorded"
    ledger = tmp_path / "shadow.jsonl"
    calls = []

    def transport(url):
        calls.append(url)
        raise AssertionError("transport was called")

    with pytest.raises(ClusterRefused):
        build_report(CASES["mainnet_label"], transport=transport, ledger_path=str(ledger))
    assert calls == []
    assert not ledger.exists()


def test_a_live_devnet_read_uses_the_transport_and_mainnet_never_does():
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

    def boom(url):
        raise AssertionError(url)

    with pytest.raises(ClusterRefused):
        build_report(
            {"cluster": "https://api.mainnet-beta.solana.com", "fetch": True},
            transport=boom,
        )


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


def test_v0_has_no_claim_token_and_no_mainnet_signing_path():
    src = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "bot" / "srb").rglob("*.py")
    )
    lowered = src.lower()
    assert "subrogat" not in lowered
    assert "claim_token" not in lowered
    assert "bonded ✓" not in lowered
    assert "mainnet-beta" not in src
    assert "id.json" not in src
    assert "Keypair" not in src


def test_product_copy_says_bonded_and_keeps_the_amount_off_the_page():
    """The status is the word Bonded. The bond figure is not product copy."""
    pages = [
        ROOT / "app" / "public" / "srb.html",
        ROOT / "app" / "public" / "js" / "srb.js",
        ROOT / "bot" / "skills" / "srb_commands.py",
        ROOT / "bot" / "srb" / "README.md",
    ]
    blob = "\n".join(path.read_text(encoding="utf-8") for path in pages)
    assert "Bonded ✓" not in blob
    assert "✓" not in blob
    assert str(BOND_UNITS) not in blob
    assert re.search(r"\bSOL\b", blob) is None
    assert "$" not in blob
    page = (ROOT / "app" / "public" / "js" / "srb.js").read_text(encoding="utf-8")
    assert "Recorded base units:" in page
    assert "Bond base units:" in page
    assert "body.admin" in page


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
    assert str(BOND_UNITS) not in host.sent[0]
    assert "RuntimeError" not in host.sent[0]
    assert "secret" not in host.sent[0]


def test_srb_command_admin_preview_is_bonded_and_the_units_stay_here(monkeypatch):
    monkeypatch.delenv("SRB_ENABLED", raising=False)
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    text = host.sent[0]
    assert text.startswith("Admin preview. Not public.")
    assert "provider-a: Bonded" in text
    assert "Bonded ✓" not in text
    assert "✓" not in text
    assert re.search(r"\bSOL\b", text) is None
    assert "$" not in text
    assert f"Bond base units: {BOND_UNITS}" in text
    assert "Recorded base units: 2" in text
    assert "Nothing is paid." in text


def test_srb_command_with_the_flag_on_drops_the_preview_banner(monkeypatch):
    monkeypatch.setenv("SRB_ENABLED", "1")
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    text = host.sent[0]
    assert "Admin preview. Not public." not in text
    assert "provider-a: Bonded" in text
    assert f"Bond base units: {BOND_UNITS}" in text
    assert "Bonded ✓" not in text


def test_srb_command_names_the_exception_class_only(monkeypatch):
    monkeypatch.setenv("SRB_ENABLED", "1")

    def boom():
        raise RuntimeError("secret key material")

    monkeypatch.setattr("bot.skills.srb_commands.demo_report", boom)
    host = _srb_host(True)
    asyncio.run(host._cmd_srb(None, None))
    assert host.sent == ["RuntimeError"]
