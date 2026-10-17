"""`/paper` reads the caller's admission before it reads the flag.

A self-admitted account's confirms open on its own practice book whatever
`PAPER_SIM_OPT_IN_ENABLED` or its opt-in says (`engine.confirm_is_practice`,
the one reading; `tests/test_a_paper_users_first_confirm_is_practice.py`).
The command used to answer such an account from the flag and the opt-in
alone — "Paper practice mode is not enabled on this bot", or "OFF — trades
live" — a surface saying LIVE over a confirm that lands on the practice book.
Both arms: a self-admitted account hears that it is practice already, and a
vouched account hears exactly what it heard before.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from bot.config import CONFIG
from bot.skills import trading_commands as tc
from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

UID = "515151"


@pytest.fixture
def store(tmp_path):
    st = UserStore(str(tmp_path / "users.json"))
    st.register(int(UID), "newcomer")
    return st


@pytest.fixture
def flag():
    """Set `PAPER_SIM_OPT_IN_ENABLED` on the frozen config, restored after."""
    old = CONFIG.paper_sim_opt_in_enabled

    def _set(value: bool):
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", value)

    try:
        yield _set
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", old)


def _self_admitted(store):
    store._users[UID]["role"] = SELF_ADMISSION_ROLE
    store._users[UID]["admitted_by"] = SELF_ADMISSION_BY


def _vouched(store):
    store._users[UID]["role"] = "trader"
    store._users[UID]["admitted_by"] = "admin:1"
    store._users[UID]["authorized"] = True


class _Host:
    """A stand-in host for the mixin: the store, and the sends recorded."""

    def __init__(self, store):
        self.users = store
        self.sent: list[str] = []

    async def _guard(self, update, command, ctx=None):
        return True  # the gate is not this suite's subject

    def _get_tg_id(self, update):
        return UID

    async def _send(self, update, text, **kw):
        self.sent.append(text)


def _paper(host, *args):
    ctx = types.SimpleNamespace(args=list(args))
    asyncio.new_event_loop().run_until_complete(tc.TradingCommands._cmd_paper(host, object(), ctx))
    assert len(host.sent) == 1, host.sent
    return host.sent[0]


class TestASelfAdmittedAccount:
    @pytest.mark.parametrize("flag_on", [False, True])
    @pytest.mark.parametrize("args", [(), ("on",), ("off",), ("status",)])
    def test_hears_that_it_is_practice_already_whatever_the_flag_says(self, store, flag, flag_on, args):
        flag(flag_on)
        _self_admitted(store)
        out = _paper(_Host(store), *args)
        assert "already practice" in out and "practice book" in out
        assert "not enabled" not in out and "trades live" not in out and "LIVE</b>" not in out
        assert "vouch" in out, "it names the thing that changes the answer"

    def test_and_its_opt_in_is_left_alone(self, store, flag):
        flag(True)
        _self_admitted(store)
        _paper(_Host(store), "on")
        assert store.sim_opt_in(UID) is False, "/paper changes nothing for an account that is practice by admission"


class TestAVouchedAccount:
    def test_with_the_flag_off_hears_the_refusal(self, store, flag):
        flag(False)
        _vouched(store)
        out = _paper(_Host(store))
        assert "not enabled" in out and "PAPER_SIM_OPT_IN_ENABLED" in out
        assert "already practice" not in out

    def test_with_the_flag_on_and_no_opt_in_is_told_its_trades_are_live(self, store, flag):
        flag(True)
        _vouched(store)
        out = _paper(_Host(store))
        assert "OFF" in out and "trades live" in out
        assert "already practice" not in out

    def test_with_the_flag_on_can_still_switch(self, store, flag):
        flag(True)
        _vouched(store)
        assert "SIMULATED" in _paper(_Host(store), "on")
        assert store.sim_opt_in(UID) is True
        assert "LIVE" in _paper(_Host(store), "off")
        assert store.sim_opt_in(UID) is False
