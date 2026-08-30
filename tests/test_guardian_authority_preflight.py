"""Guardian least-privilege preflight + custody red-team.

Pins the honest reconciliation statuses (CONFIRMED / VIOLATION / UNVERIFIED) and
proves the custody red team denies every envelope-crossing attack while allowing
the in-bounds control action.
"""
from bot.guardian import authority as auth
from bot.guardian import authority_preflight as pf
from bot.guardian import authority_redteam as rt


def _env(**over):
    spec = {"allowed_venues": ["bitget"], "allowed_market_types": ["swap"],
            "max_notional_per_trade_usd": 1000}
    spec.update(over)
    return auth.compile_envelope(spec)


# ── preflight reconciliation ──────────────────────────────────────────

def test_read_confirmed_and_missing_read_is_violation():
    ok = pf.reconcile_posture(_env(), {"read": True})
    read = next(d for d in ok["dimensions"] if d["dimension"] == "read")
    assert read["status"] == pf.CONFIRMED

    bad = pf.reconcile_posture(_env(), {"read": False})
    assert bad["ok"] is False
    read2 = next(d for d in bad["dimensions"] if d["dimension"] == "read")
    assert read2["status"] == pf.VIOLATION


def test_withdraw_unknown_is_unverified_not_pass():
    # No withdraw evidence → UNVERIFIED (honest), and it does NOT fail the preflight.
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "unknown"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.UNVERIFIED
    assert rep["ok"] is True   # unverified is surfaced, not blocking
    assert "unverified" in rep["summary"].lower()


def test_over_privileged_key_is_violation():
    # Envelope forbids withdrawal, but the key CAN withdraw → over-privileged → VIOLATION.
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "on"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.VIOLATION
    assert rep["ok"] is False
    assert "OVER-PRIVILEGED" in wd["detail"]


def test_withdraw_off_matches_noncustodial_intent():
    rep = pf.reconcile_posture(_env(), {"read": True, "withdraw": "off"})
    wd = next(d for d in rep["dimensions"] if d["dimension"] == "withdraw")
    assert wd["status"] == pf.CONFIRMED


def test_environment_mismatch_is_violation():
    rep = pf.reconcile_posture(_env(), {
        "read": True, "environment": "live", "expected_environment": "demo"})
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.VIOLATION
    assert rep["ok"] is False


# ── custody red team ──────────────────────────────────────────────────

def test_authority_redteam_denies_every_attack():
    report = rt.run_authority_redteam()
    # every scenario must be handled correctly (attacks denied, control allowed)
    assert report["failed"] == 0, [s for s in report["scenarios"] if not s["passed"]]
    assert report["pass_rate"] == 100.0
    # the control action proves the gate is not just deny-everything
    control = next(s for s in report["scenarios"] if s["name"] == "control_in_bounds_trade")
    assert control["actual"] == "allow"
    # the injection attack is denied because compile clamped the forged cap
    inj = next(s for s in report["scenarios"] if s["name"] == "injection_raise_the_limit")
    assert inj["actual"] == "deny"
    # every non-control scenario is an attack expected to be denied
    attacks = [s for s in report["scenarios"] if s["category"] != "control"]
    assert all(s["expected"] == "deny" for s in attacks)
    assert all(s["actual"] == "deny" for s in attacks)


# ── key SCOPE: the dimension a non-custodial product actually rests on ──
#
# `withdraw` was the one dimension nothing could ever populate: probe_posture
# defaulted it to "unknown" and no caller supplied otherwise, so the crux of
# non-custody was permanently UNVERIFIED. These pin the parser that now answers
# it, and specifically pin the ASYMMETRY — "off" is a confident all-clear about
# somebody's money and must never be manufactured from a response we do not
# fully understand.

import asyncio

from bot.core import exchange_credentials as ec


def test_withdraw_authority_present_is_on():
    assert ec.bitget_withdraw_scope(["readonly", "spot_trade", "withdraw"]) == "on"
    # case and whitespace are the venue's business, not a reason to miss it
    assert ec.bitget_withdraw_scope([" WITHDRAW "]) == "on"


def test_fully_recognised_response_without_withdraw_is_off():
    assert ec.bitget_withdraw_scope(["readonly", "spot_trade", "contract_trade"]) == "off"


def test_an_unrecognised_authority_forces_unknown_never_off():
    """The safe-degradation rule, and the whole reason this parser exists.

    If Bitget renames a scope or adds one, every real response carries a token
    we do not know. Concluding "off" from that would print "non-custodial, as
    intended" over a permission set we could not read. It must degrade to
    "unknown" — which is exactly the information we had before.
    """
    assert ec.bitget_withdraw_scope(["readonly", "some_new_scope_2027"]) == "unknown"
    # ...but positive evidence of withdrawal survives an unknown token beside it
    assert ec.bitget_withdraw_scope(["withdraw", "some_new_scope_2027"]) == "on"


def test_absent_or_malformed_authorities_are_unknown_not_off():
    for bad in (None, {}, "withdraw", 42, []):
        assert ec.bitget_withdraw_scope(bad) == "unknown", bad


def test_ip_allowlist_distinguishes_not_restricted_from_not_readable():
    # the venue answered and the key is pinned
    assert ec.bitget_ip_allowlist({"ips": "1.2.3.4,5.6.7.8"}) == ["1.2.3.4", "5.6.7.8"]
    # the venue answered and the key is NOT pinned — a real, measured []
    assert ec.bitget_ip_allowlist({"ips": ""}) == []
    # nobody looked — None, which is not the same fact
    assert ec.bitget_ip_allowlist({}) is None
    assert ec.bitget_ip_allowlist({"ips": None}) is None
    assert ec.bitget_ip_allowlist(None) is None


# ── probe_posture must not manufacture an environment ──────────────────

def _observed(**kw):
    async def _fake_validate(venue, fields, sandbox=False):
        return kw["ok"], kw.get("detail", "")
    import bot.core.exchange_credentials as _ec
    real = _ec.validate_venue_credentials
    _ec.validate_venue_credentials = _fake_validate
    try:
        return asyncio.run(pf.probe_posture(
            "bitget", {}, sandbox=kw.get("sandbox", False),
            withdraw=kw.get("withdraw", "unknown"),
            expected_sandbox=kw.get("expected_sandbox")))
    finally:
        _ec.validate_venue_credentials = real


def test_failed_read_does_not_assert_an_environment():
    """The regression this whole change exists for.

    probe_posture used to set environment AND expected_environment from the same
    `sandbox` argument, before the probe ran. A key that could not authenticate
    at all still produced environment == expected_environment, which reconciles
    to CONFIRMED: "key is a live key, matching the bot". A measurement made by
    comparing a value against itself, on a failed probe.
    """
    obs = _observed(ok=False, expected_sandbox=False)
    assert "environment" not in obs, obs
    rep = pf.reconcile_posture(_env(), obs)
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.UNVERIFIED, env_dim


def test_successful_read_earns_the_environment_observation():
    obs = _observed(ok=True, sandbox=False, expected_sandbox=False)
    assert obs["environment"] == "live"
    rep = pf.reconcile_posture(_env(), obs)
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.CONFIRMED


def test_environment_violation_is_now_reachable_from_a_real_probe():
    """The pure branch was always tested; no PRODUCER could reach it."""
    obs = _observed(ok=True, sandbox=True, expected_sandbox=False)
    rep = pf.reconcile_posture(_env(), obs)
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.VIOLATION
    assert rep["ok"] is False


def test_without_an_expected_environment_nothing_is_claimed():
    obs = _observed(ok=True, sandbox=False, expected_sandbox=None)
    assert "expected_environment" not in obs
    rep = pf.reconcile_posture(_env(), obs)
    env_dim = next(d for d in rep["dimensions"] if d["dimension"] == "environment")
    assert env_dim["status"] == pf.INFO   # observed, but no constraint to check


# ── what the user is actually told at /connect ─────────────────────────

def test_withdraw_notice_is_three_valued_and_never_silent():
    on = pf.withdraw_notice("on")
    off = pf.withdraw_notice("off")
    unk = pf.withdraw_notice("unknown")
    assert on and off and unk
    assert len({on, off, unk}) == 3

    # an over-privileged key must be unmistakable and actionable
    assert "WITHDRAW" in on and "trade-only" in on

    # the unreadable case must not read as the safe case
    assert "not readable" in unk
    assert "cannot move funds out" not in unk
    assert "🟢" not in unk          # colour is a claim

    # absent scope is the unreadable case, never the safe one
    assert pf.withdraw_notice(None) == unk
    assert pf.withdraw_notice("") == unk
