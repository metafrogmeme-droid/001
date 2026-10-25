"""Silent Fee Check. Mocked info responses only. No live network."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bot.silent_fee.address import parse_address
from bot.silent_fee.advisory import load_advisories, match_advisories
from bot.silent_fee.check import read_account
from bot.silent_fee.client import EXPLORER_URL, INFO_URL
from bot.silent_fee.flag import explorer_on, flag_on, mainnet_read_on
from bot.silent_fee.render import render_text
from bot.silent_fee.units import bp_of, headroom, per_10k_usdc, percent_of
from bot.skills.silent_fee_commands import SilentFeeCommands

BUILDER = "0xa6f967d47c6f85a5ba4fc43543e5e1c171cccf98"
USER = "0x" + "11" * 20
SUB = "0x" + "22" * 20
MASTER = "0x" + "33" * 20
OTHER = "0x" + "44" * 20
DIGEST = "0x" + "ab" * 32
NOW = 1_760_000_000_000
ON = {"SILENT_FEE_CHECK_ENABLED": "1"}
BOTH = {"SILENT_FEE_CHECK_ENABLED": "1", "SILENT_FEE_EXPLORER_ENABLED": "1"}
BANNED = ("victim", "scam", "professor", "compromised", "affected")


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class Script:
    """One scripted info host. A type not listed raises, so a surprise call fails."""

    def __init__(self, *, fills=None, role=None, subs=None, approved=None, caps=None,
                 referral=None, fees=None, tx=None, details=None, agents=None, boom=()):
        self.fills = [] if fills is None else fills
        self.role = {"role": "user"} if role is None else role
        self.subs = [] if subs is None else subs
        self.approved = [BUILDER] if approved is None else approved
        self.caps = {BUILDER: 100} if caps is None else caps
        self.referral = {
            "referredBy": {"referrer": OTHER, "code": "2OOLKIT"},
        } if referral is None else referral
        self.fees = {"userCrossRate": "0.00045", "activeReferralDiscount": "0.04"} if fees is None else fees
        self.tx = tx
        self.details = details
        self.agents = [] if agents is None else agents
        self.boom = set(boom)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, body):
        self.calls.append((url, dict(body)))
        kind = body.get("type")
        if kind in self.boom:
            raise TimeoutError("down")
        if kind == "userRole":
            return self.role
        if kind == "subAccounts":
            return self.subs
        if kind == "approvedBuilders":
            return list(self.approved)
        if kind == "maxBuilderFee":
            return self.caps.get(body.get("builder"))
        if kind == "referral":
            return self.referral
        if kind == "userFees":
            return self.fees
        if kind == "userFillsByTime":
            end = body.get("endTime")
            rows = [row for row in self.fills if row.get("time", 0) <= end]
            return rows[:2000]
        if kind == "extraAgents":
            return self.agents
        if kind == "txDetails":
            return self.tx
        if kind == "userDetails":
            return self.details
        raise AssertionError(kind)


def _fill(fee="1.50", tid=1, when=NOW - 86_400_000, digest=DIGEST):
    return {
        "tid": tid, "hash": digest, "time": when, "coin": "BTC",
        "px": "100", "sz": "1", "side": "B", "builderFee": fee,
    }


def _types(script: Script) -> list[str]:
    return [body["type"] for _url, body in script.calls]


def test_flags_accept_only_1_and_true():
    for name, fn in (
        ("SILENT_FEE_CHECK_ENABLED", flag_on),
        ("SILENT_FEE_EXPLORER_ENABLED", explorer_on),
        ("SFC_ALLOW_MAINNET_READ", mainnet_read_on),
    ):
        assert fn({}) is False
        assert fn({name: ""}) is False
        assert fn({name: "0"}) is False
        assert fn({name: "yes"}) is False
        assert fn({name: "on"}) is False
        assert fn({name: "1"}) is True
        assert fn({name: "true"}) is True
        assert fn({name: "TRUE"}) is True


def test_f_units_and_headroom():
    assert bp_of(1) == Decimal("0.1")
    assert percent_of(1) == Decimal("0.001")
    assert per_10k_usdc(1) == Decimal("0.1")
    assert bp_of(100) == Decimal("10")
    assert percent_of(100) == Decimal("0.1")
    assert per_10k_usdc(100) == Decimal("10")
    gap = headroom(100, 1)
    assert gap is not None
    assert gap["headroom_bp"] == "9.9"
    assert gap["headroom_per_10k_usdc"] == "9.9"
    assert gap["cap_bp"] == "10"
    assert gap["charged_bp"] == "0.1"
    assert headroom(None, 1) is None
    assert headroom(100, None) is None
    zero = headroom(0, 0)
    assert zero is not None and zero["headroom_bp"] == "0"
    assert zero["headroom_per_10k_usdc"] == "0"


def test_address_validation_refuses_before_any_call():
    def boom(url, body):
        raise AssertionError(url)

    key = "0x" + "ab" * 32
    seed = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    for raw, why in (("", "address"), ("0x1234", "address"), ("not-an-address", "address"),
                     (key, "secret"), (seed, "secret")):
        out = read_account(raw, transport=boom, env=ON)
        assert out["refused"] == why
        blob = json.dumps(out)
        assert key not in blob
        assert "alpha" not in blob
    window = read_account(USER, days=7, transport=boom, env=ON)
    assert window["refused"] == "window"
    assert parse_address(USER) == USER
    assert parse_address(USER.upper()) == USER
    assert parse_address("0x" + "zz" * 20) is None


def test_flag_off_does_not_call_out():
    def boom(url, body):
        raise AssertionError(url)

    out = read_account(USER, transport=boom, env={})
    assert out["available"] is False
    assert out["signs"] is False
    assert render_text(out) == "not available yet"


def test_documented_reads_run_without_the_explorer(monkeypatch):
    fills = [_fill(), _fill(fee="0.25", tid=2, digest="0x" + "cd" * 32)]
    script = Script(fills=fills, subs=[{"subAccountUser": SUB, "name": "desk"}])
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    assert out["network"] == "testnet"
    assert out["info_url"] == INFO_URL["testnet"]
    assert out["explorer"] is False
    kinds = set(_types(script))
    assert {"userRole", "subAccounts", "approvedBuilders", "maxBuilderFee",
            "referral", "userFees", "userFillsByTime"} <= kinds
    assert "txDetails" not in kinds
    assert "userDetails" not in kinds
    assert "extraAgents" not in kinds
    assert all(EXPLORER_URL not in url for url, _body in script.calls)
    account = out["accounts"][0]
    assert account["builder_fee_usdc"] == "1.75"
    assert account["builder_fee_fills"] == 2
    assert account["builder_fee_complete"] is True
    assert account["attribution"] == "off"
    assert account["unattributed_usdc"] == "1.75"
    assert account["builders_paid"] == []
    assert account["approved_builders"][0]["cap_bp"] == "10"
    assert account["approved_builders"][0]["cap_percent"] == "0.1"
    assert account["approved_builders"][0]["cap_per_10k_usdc"] == "10"
    assert out["headline"]["signal"] == "unread"
    assert out["sub_accounts"]["addresses"] == [SUB]
    text = render_text(out)
    assert "Headroom: not read" in text
    assert "Per-builder attribution is off" in text
    assert "MAL-2026-3679" in text
    assert "2026-05-12" in text
    assert "2OOLKIT" in text
    low = text.lower()
    for word in BANNED:
        assert word not in low
    assert "AI-written" not in text


def test_explorer_attributes_the_charged_rate_and_the_headroom():
    when = NOW - 86_400_000
    signed = NOW - 10 * 86_400_000
    script = Script(
        fills=[_fill(when=when), _fill(fee="0.50", tid=2, when=when)],
        tx={"tx": {"action": {"type": "order", "builder": {"b": BUILDER, "f": 1}}}},
        details={"txs": [{
            "time": signed,
            "action": {"type": "approveBuilderFee", "builder": BUILDER, "maxFeeRate": "0.1%"},
        }]},
        agents=[{"address": OTHER, "name": "desk"}],
    )
    out = read_account(USER, transport=script, env=BOTH, now_ms=NOW)
    assert out["explorer"] is True
    assert _types(script).count("txDetails") == 1
    account = out["accounts"][0]
    paid = account["builders_paid"][0]
    assert paid["builder"] == BUILDER
    assert paid["paid_usdc"] == "2"
    assert paid["n_fills"] == 2
    assert paid["charged_f"] == [1]
    assert paid["headroom_bp"] == "9.9"
    assert paid["headroom_per_10k_usdc"] == "9.9"
    assert paid["approved_cap_bp"] == "10"
    assert paid["charged_bp"] == "0.1"
    assert paid["first_seen"] == _date(when)
    assert paid["approval_signed_at"] == _date(signed)
    assert out["headline"]["headroom_bp"] == "9.9"
    assert out["headline"]["headroom_per_10k_usdc"] == "9.9"
    assert account["extra_agents"]["agents"][0]["name"] == "desk"
    text = render_text(out)
    assert "9.9 bp" in text
    assert "9.9 USDC per 10,000 USDC notional" in text
    assert f"First fee date {_date(when)}" in text
    assert "published 2026-05-12" in text
    low = text.lower()
    for word in BANNED:
        assert word not in low


def test_a_failed_explorer_read_stays_unattributed():
    script = Script(fills=[_fill()], boom=("txDetails", "userDetails", "extraAgents"))
    out = read_account(USER, transport=script, env=BOTH, now_ms=NOW)
    account = out["accounts"][0]
    assert account["builders_paid"] == []
    assert account["unattributed_usdc"] == "1.5"
    assert account["attribution"] == "partial"
    assert account["approved_builders"][0]["builder"] == BUILDER
    assert out["headline"]["signal"] == "unread"
    text = render_text(out)
    assert "Unattributed builder fees: 1.5 USDC" in text
    assert "not assigned to a builder" in text


def test_an_agent_address_is_read_on_its_master():
    script = Script(role={"role": "agent", "data": {"user": MASTER}})
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    users = {body["user"] for _url, body in script.calls if body["type"] == "approvedBuilders"}
    assert users == {MASTER}
    assert out["role"]["role"] == "agent"
    assert out["role"]["master"] == MASTER


def test_an_unreadable_builder_fee_is_not_a_zero():
    script = Script(fills=[_fill(fee="nope"), _fill(fee="1.00", tid=2, digest="0x" + "ef" * 32)])
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    account = out["accounts"][0]
    assert account["builder_fee_usdc"] == "1"
    assert account["builder_fee_unreadable_rows"] == 1
    assert account["builder_fee_complete"] is False
    assert "not the whole sum" in render_text(out)


def test_an_empty_fill_window_is_a_measured_zero():
    script = Script(fills=[])
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    account = out["accounts"][0]
    assert account["builder_fee_usdc"] == "0"
    assert account["builder_fee_fills"] == 0
    assert account["builder_fee_complete"] is True
    assert "0 USDC across 0 fills" in render_text(out)


def test_mainnet_info_is_a_separate_flag():
    script = Script(fills=[])
    out = read_account(
        USER, transport=script, env={**ON, "SFC_ALLOW_MAINNET_READ": "1"}, now_ms=NOW,
    )
    assert out["network"] == "mainnet"
    assert script.calls[0][0] == INFO_URL["mainnet"]


def test_advisory_matching_is_a_fact_with_a_date():
    table = load_advisories()
    assert table is not None
    assert table[0]["id"] == "MAL-2026-3679"
    assert table[0]["published"] == "2026-05-12"
    assert table[0]["builder"] == BUILDER
    hits = match_advisories(table, builder=BUILDER.upper(), code="2OOLKIT")
    assert hits is not None
    assert hits[0]["matched_on"] == ["builder", "referral_code"]
    assert match_advisories(table, builder=USER) == []
    assert match_advisories(None, builder=BUILDER) is None


def test_an_unreadable_advisory_table_is_not_no_match(monkeypatch):
    monkeypatch.setattr("bot.silent_fee.check.load_advisories", lambda: None)
    script = Script(fills=[])
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    assert out["advisory_table"]["read"] is False
    assert "could not be read" in render_text(out)
    assert "no match result" in render_text(out)


def test_paging_keeps_the_fill_past_the_first_page():
    fills = [_fill(fee="0.01", tid=i, when=NOW - i, digest="0x" + f"{i:064x}") for i in range(2001)]
    script = Script(fills=fills)
    out = read_account(USER, transport=script, env=ON, now_ms=NOW)
    pages = [body for _url, body in script.calls if body["type"] == "userFillsByTime"]
    assert len(pages) >= 2
    assert pages[1]["endTime"] < pages[0]["endTime"]
    assert out["accounts"][0]["builder_fee_usdc"] == "20.01"
    assert out["accounts"][0]["builder_fee_complete"] is True


def test_the_module_does_not_sign_or_read_a_key():
    root = Path(__file__).resolve().parents[1] / "bot" / "silent_fee"
    text = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    for banned in ("ETH_PRIVATE_KEY", "HYPERLIQUID_PRIVATE_KEY", "eth_account", "sign_transaction"):
        assert banned not in text


class _Host(SilentFeeCommands):
    def __init__(self, admin: bool):
        self.admin = admin
        self.sent: list[str] = []

    def _is_admin(self, update) -> bool:
        return self.admin

    async def _send(self, update, text, reply_markup=None, edit: bool = False) -> None:
        self.sent.append(text)


class _Msg:
    def __init__(self, text: str):
        self.text = text


class _Up:
    def __init__(self, text: str):
        self.effective_message = _Msg(text)
        self.message = self.effective_message


def test_the_command_is_admin_only_and_off_until_the_flag(monkeypatch):
    called = {"n": 0}

    def boom(url, body):
        called["n"] += 1
        raise AssertionError(url)

    monkeypatch.setattr("bot.silent_fee.check.urllib_transport", boom)
    stranger = _Host(False)
    asyncio.run(stranger._cmd_fees(_Up("/fees " + USER), None))
    assert stranger.sent == ["Not found."]
    assert called["n"] == 0

    monkeypatch.delenv("SILENT_FEE_CHECK_ENABLED", raising=False)
    admin = _Host(True)
    asyncio.run(admin._cmd_fees(_Up("/fees " + USER), None))
    assert admin.sent == ["not available yet"]
    assert called["n"] == 0

    monkeypatch.setenv("SILENT_FEE_CHECK_ENABLED", "1")
    script = Script(fills=[_fill()])
    monkeypatch.setattr("bot.silent_fee.check.urllib_transport", script)
    admin = _Host(True)
    asyncio.run(admin._cmd_fees(_Up("/fees " + USER + " 30"), None))
    assert script.calls
    assert "Builder" in admin.sent[0]
    assert "Nothing was signed" in admin.sent[0]

    admin = _Host(True)
    asyncio.run(admin._cmd_fees(_Up("/fees " + USER + " 7"), None))
    assert admin.sent == ["The window is 30 or 90 days."]
