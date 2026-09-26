"""The web live gate's premise is an order on the user's OWN account, and it
now has a precondition that says whether one can exist.

`web_live_gate` opens live trading for a web-only account "on THEIR OWN
connected exchange keys; RUNECLAW custodies nothing", and decided that from
five inputs, none of which is whether the bot builds per-user executors at
all. With `PER_USER_LIVE_ENABLED` at its shipped default (off),
`engine._executor_for` answers the OPERATOR's executor for every caller, so
driven before this file existed -- operator switch on, bot live, the user opted
in, readable keys stored, an enforce-mode envelope bound -- the gate answered
"all preconditions met" and the order would have run on the operator's
account. Latent while `WEB_LIVE_TRADING_ENABLED` stays off, and armed by it.

`routes_to_own_account` is the sixth input, read by ONE function both sourcing
sites ask (the gateway's live decision and the operator's readiness card), so
the card an operator reads and the gate that decides a real order cannot
answer it two ways. The handler's check of what the resolver actually answers
is in `test_a_refused_web_confirm_is_not_a_confirmed_trade.py`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bot.web import web_live_gate as g

ALL = dict(feature_enabled=True, bot_is_live=True, user_opted_in=True,
           has_own_keys=True, envelope_enforcing=True, routes_to_own_account=True)


def test_every_other_input_satisfied_and_per_user_off_is_refused_by_name():
    d = g.evaluate(**{**ALL, "routes_to_own_account": False})
    assert d.allowed is False
    assert "PER_USER_LIVE_ENABLED" in d.reason
    assert "operator's account" in d.reason
    assert d.checklist["routes_to_own_account"] is False
    assert all(v for k, v in d.checklist.items() if k != "routes_to_own_account")


def test_all_six_allow():
    d = g.evaluate(**ALL)
    assert d.allowed is True
    assert set(d.checklist) == {"feature_enabled", "bot_is_live", "routes_to_own_account",
                                "user_opted_in", "has_own_keys", "envelope_enforcing"}


def test_it_is_asked_before_the_users_own_steps():
    """An operator-level condition the user cannot fix is named before the
    user is sent off to opt in, connect keys and bind an envelope."""
    d = g.evaluate(**{**ALL, "routes_to_own_account": False, "user_opted_in": False,
                      "has_own_keys": False, "envelope_enforcing": False})
    assert "PER_USER_LIVE_ENABLED" in d.reason


def test_the_input_is_required():
    kw = dict(ALL)
    kw.pop("routes_to_own_account")
    with pytest.raises(TypeError):
        g.evaluate(**kw)


# ── the one reading of the flag ───────────────────────────────────────────


@pytest.mark.parametrize("config, expected", [
    (SimpleNamespace(per_user_live_enabled=True), True),
    (SimpleNamespace(per_user_live_enabled=False), False),
    (SimpleNamespace(), False),                          # a stand-in without the field
    (SimpleNamespace(per_user_live_enabled="true"), False),
    (SimpleNamespace(per_user_live_enabled=1), False),
    (MagicMock(), False),                                # every attribute of a mock is truthy
    (None, False),
], ids=["true", "false", "absent", "string", "one", "mock", "none"])
def test_only_the_boolean_true_reads_as_on(config, expected):
    assert g.routes_to_own_account(config) is expected


def test_the_shipped_default_is_off():
    from bot.config import CONFIG
    assert CONFIG.per_user_live_enabled is False
    assert g.routes_to_own_account(CONFIG) is False


# ── both sourcing sites ask it ────────────────────────────────────────────


def _admin_readiness(monkeypatch, per_user):
    from bot.web import web_live_admin as adm
    monkeypatch.setenv("WEB_LIVE_TRADING_ENABLED", "1")
    monkeypatch.setattr(adm, "CONFIG", SimpleNamespace(
        is_live=lambda: True, per_user_live_enabled=per_user))
    monkeypatch.setattr(adm, "_has_own_keys", lambda tg: True)
    monkeypatch.setattr(adm, "_envelope_enforcing", lambda tg: True)
    users = SimpleNamespace(web_live_enabled=lambda tg: True)
    return adm.user_readiness(users, "web:5")


def test_the_operators_readiness_card_reads_it(monkeypatch):
    off = _admin_readiness(monkeypatch, False)
    assert off["allowed"] is False and "PER_USER_LIVE_ENABLED" in off["reason"]
    assert _admin_readiness(monkeypatch, True)["allowed"] is True


def test_the_card_labels_the_row():
    from bot.web import web_live_admin as adm
    card = adm.human_readable("web:5", {"allowed": False, "reason": "x",
                                        "checklist": {"routes_to_own_account": False}})
    assert "PER_USER_LIVE_ENABLED" in card


def _gateway_decision(monkeypatch, per_user):
    import bot.core.exchange_credentials as xc
    from bot.web import user_gateway as ug
    monkeypatch.setenv("WEB_LIVE_TRADING_ENABLED", "1")
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        is_live=lambda: True, per_user_live_enabled=per_user))
    monkeypatch.setattr(xc, "get_credential_store",
                        lambda: SimpleNamespace(credential_state=lambda tg: "readable"))
    monkeypatch.setattr(ug, "_web_envelope_enforcing", lambda app, tg: True)
    handler = SimpleNamespace(users=SimpleNamespace(web_live_enabled=lambda tg: True))
    return ug._web_live_decision({}, handler, "web:5")


def test_the_gateway_decision_reads_it(monkeypatch):
    assert _gateway_decision(monkeypatch, False).allowed is False
    assert _gateway_decision(monkeypatch, True).allowed is True


def test_both_sites_ask_the_one_reading(monkeypatch):
    """Planted: a reading that answers the opposite of the flag. A site with
    its own copy of the flag read agrees with every honest fixture and
    diverges here."""
    monkeypatch.setattr(g, "routes_to_own_account", lambda config: False)
    assert _gateway_decision(monkeypatch, True).allowed is False
    assert _admin_readiness(monkeypatch, True)["allowed"] is False
