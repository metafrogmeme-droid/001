"""A linked trader stakes THEIR OWN idle stables; the operator's keys stay the operator's.

`/stake`, `/stake fixed`, `/unstake` and the three confirm buttons behind
them each built their client with `BitgetV3Client.from_config()` — the
OPERATOR's keys — under an `_is_admin` gate. So the INCOME_MAP row said what
it said: "No user can move a cent. Every execution path is `_is_admin` and
runs against the OPERATOR's Bitget keys — a normal trader/paper/viewer gets a
rate and nothing to press." The per-user executors and the credential store
already existed and nothing on the Earn path asked them.

`bot.core.earn_account.resolve_earn_account` is the one ask: whose account
this caller's stake or redeem acts on, eight states, made once and asked
again at press time. The plan card names the account, the button carries
its owner tag, and the sealed record names it — so a plan built over one
book cannot execute against another, whoever taps.

THE FIXTURE IS ASYMMETRIC ON PURPOSE. The operator's free margin is planted
at $1,000 and the caller's at $250, the operator's client is built from one
key and the caller's from another, and every card assertion reads the
figure and the key: a card reading the wrong book is then a different
card. Plant the same numbers on both and a resolver that fell back to the
operator's keys is indistinguishable from one that did not — the lesson
`pro_scan`'s header taught one slice over.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.core.earn_account import (
    EARN_TAG_MISMATCH,
    EARN_VENUE,
    OPERATOR_TAG,
    STATES,
    USABLE,
    EarnAccount,
    earn_account_line,
    earn_refusal,
    resolve_earn_account,
)
from bot.core.yield_radar import ActionResult, YieldReport, YieldRow
from tests.source_scan import code_only
from tests.test_free_text_obeys_the_role_gate import OPERATOR, STRANGER, TRADER, _handler

ROOT = Path(__file__).resolve().parent.parent
CALLER_KEY, CALLER_SECRET = "caller-api-key", "caller-api-secret"
OP_CLIENT = SimpleNamespace(_api_key="operator-api-key", has_credentials=True)
FP = "BG-1a2b…f9"


# ── a planted credential store ───────────────────────────────────────────────

class _Store:
    def __init__(self, state="absent", venue="bitget", creds=None, fp=FP, raise_on=()):
        self.state, self.venue, self.creds, self.fp = state, venue, creds, fp
        self.raise_on = set(raise_on)
        self.asked: list[str] = []

    def _maybe_raise(self, what):
        self.asked.append(what)
        if what in self.raise_on:
            raise RuntimeError(f"planted {what} fault: host=vault.internal key=abc")

    def credential_state(self, uid):
        self._maybe_raise("state")
        return self.state

    def get_venue(self, uid):
        self._maybe_raise("venue")
        return self.venue

    def get(self, uid):
        self._maybe_raise("get")
        return self.creds

    def fingerprint(self, uid):
        self._maybe_raise("fingerprint")
        return self.fp


def _linked(**kw):
    creds = {"api_key": CALLER_KEY, "api_secret": CALLER_SECRET, "passphrase": "pp"}
    return _Store(state="readable", creds=creds, **kw)


def _resolve(store, *, uid=TRADER, is_admin=False, revoked=False, op=lambda: OP_CLIENT):
    calls = []

    def _op():
        calls.append(1)
        return op()
    acct = resolve_earn_account(uid, is_admin=is_admin, operator_client=_op,
                                store=store, revoked=revoked)
    return acct, calls


# ── the resolver ─────────────────────────────────────────────────────────────

class TestWhoseAccount:
    def test_a_linked_caller_gets_a_client_built_from_their_own_keys(self):
        acct, op_calls = _resolve(_linked())
        assert acct.state == "caller" and acct.usable
        assert acct.client._api_key == CALLER_KEY and acct.client._api_secret == CALLER_SECRET
        assert acct.owner == TRADER and acct.tag == TRADER
        assert acct.fingerprint == FP and acct.venue == EARN_VENUE
        assert op_calls == [], "the operator's client must not even be built for a linked caller"

    def test_a_linked_admin_stakes_their_own_account_too(self):
        # "Executor identity decides" — the rule /livebalance and _executor_for
        # follow. An admin who brought their own keys acts on their own book.
        acct, op_calls = _resolve(_linked(), uid=OPERATOR, is_admin=True)
        assert acct.state == "caller" and acct.client._api_key == CALLER_KEY
        assert op_calls == []

    def test_an_admin_with_no_linked_keys_gets_the_operators_account(self):
        acct, op_calls = _resolve(_Store("absent"), uid=OPERATOR, is_admin=True)
        assert acct.state == "operator" and acct.usable
        assert acct.client is OP_CLIENT and acct.tag == OPERATOR_TAG and acct.owner == "operator"
        assert op_calls == [1]

    def test_an_admin_with_neither_is_told_to_set_the_operator_keys(self):
        acct, _ = _resolve(_Store("absent"), uid=OPERATOR, is_admin=True, op=lambda: None)
        assert acct.state == "operator_absent" and not acct.usable
        assert "/setexchange" in earn_refusal(acct)

    def test_a_trader_with_no_linked_keys_is_told_to_connect_never_given_the_operators(self):
        acct, op_calls = _resolve(_Store("absent"))
        assert acct.state == "absent" and not acct.usable and acct.client is None
        assert op_calls == [], "THE DEFECT: the operator's keys reached for a caller with none"
        n = earn_refusal(acct)
        assert "/connect" in n and n.endswith("Nothing was moved.")
        assert "operator" not in n

    def test_unreadable_keys_are_not_absent(self):
        acct, op_calls = _resolve(_Store("unreadable"))
        assert acct.state == "unreadable" and op_calls == []
        n = earn_refusal(acct)
        assert "cannot read them" in n and "none is linked" not in n

    def test_unreadable_keys_do_not_become_the_operators_for_an_admin_either(self):
        acct, op_calls = _resolve(_Store("unreadable"), uid=OPERATOR, is_admin=True)
        assert acct.state == "unreadable" and op_calls == []

    def test_a_bybit_linked_caller_is_unsupported_not_operator_and_not_decrypted(self):
        store = _linked(venue="bybit")
        acct, op_calls = _resolve(store)
        assert acct.state == "unsupported" and acct.venue == "bybit" and op_calls == []
        assert "get" not in store.asked, "nothing to sign with, so nothing to decrypt"
        n = earn_refusal(acct)
        assert "bybit" in n and "Bitget-only" in n and n.endswith("Nothing was moved.")

    @pytest.mark.parametrize("what", ["state", "venue", "get", "fingerprint"])
    def test_a_store_that_raises_is_unavailable_never_absent(self, what):
        acct, op_calls = _resolve(_linked(raise_on=(what,)))
        assert acct.state == "unavailable" and acct.fault == "RuntimeError"
        assert op_calls == []
        n = earn_refusal(acct)
        assert "could not be asked" in n and "not a missing link" in n
        # The fault is the CLASS, never the message: a driver message can
        # carry a host or a key.
        assert "vault.internal" not in n and "vault.internal" not in acct.fault

    def test_a_half_record_cannot_sign_and_is_unreadable_not_the_operators(self):
        # `BitgetV3Client.for_account` answers a half-filled dict with the
        # OPERATOR's keys — the one fallback this module exists to close.
        store = _Store(state="readable", creds={"api_key": CALLER_KEY})
        acct, op_calls = _resolve(store, uid=OPERATOR, is_admin=True)
        assert acct.state == "unreadable" and acct.client is None and op_calls == []

    def test_a_revoke_outranks_a_readable_link(self):
        acct, op_calls = _resolve(_linked(), revoked=True)
        assert acct.state == "revoked" and not acct.usable and op_calls == []
        assert "revoked" in earn_refusal(acct)

    def test_a_word_the_store_should_not_say_is_unavailable(self):
        acct, _ = _resolve(_Store(state="connected"))
        assert acct.state == "unavailable" and acct.fault == "state='connected'"

    def test_an_empty_id_is_absent_without_asking_the_store(self):
        store = _Store("readable")
        acct, _ = _resolve(store, uid="")
        assert acct.state == "absent" and store.asked == []

    def test_the_source_never_reaches_the_operator_fallback(self):
        src = code_only((ROOT / "bot" / "core" / "earn_account.py").read_text(encoding="utf-8"))
        assert "for_account" not in src and "from_config" not in src


class TestTheWords:
    @pytest.mark.parametrize("state", [s for s in STATES if s not in USABLE])
    def test_every_refusal_ends_by_saying_nothing_moved(self, state):
        n = earn_refusal(EarnAccount(state, owner=TRADER, venue="bybit"))
        assert n.endswith("Nothing was moved."), (state, n)
        with pytest.raises(ValueError):
            earn_account_line(EarnAccount(state, owner=TRADER))

    @pytest.mark.parametrize("state", USABLE)
    def test_a_usable_account_has_a_name_and_no_refusal(self, state):
        acct = EarnAccount(state, client=OP_CLIENT, owner=TRADER if state == "caller" else "operator",
                           fingerprint=FP if state == "caller" else "", venue=EARN_VENUE)
        with pytest.raises(ValueError):
            earn_refusal(acct)
        line = earn_account_line(acct)
        assert ("your linked Bitget" in line) == (state == "caller")
        assert ("the operator's Bitget" in line) == (state == "operator")
        if state == "caller":
            assert FP in line

    def test_the_record_names_the_account_and_a_fingerprint_never_the_key(self):
        acct, _ = _resolve(_linked())
        rec = acct.record()
        assert rec == {"account": TRADER, "key": FP, "venue": EARN_VENUE}
        assert CALLER_KEY not in str(rec) and CALLER_SECRET not in str(rec)
        op, _ = _resolve(_Store("absent"), uid=OPERATOR, is_admin=True)
        assert op.record() == {"account": "operator", "key": "", "venue": EARN_VENUE}
        assert EarnAccount("absent", owner=TRADER).record()["account"] == TRADER
        assert EarnAccount("unavailable").record()["account"] == "none"

    def test_the_tag_mismatch_sentence_names_the_doors_and_claims_nothing_moved(self):
        assert "/stake" in EARN_TAG_MISMATCH and "/unstake" in EARN_TAG_MISMATCH
        assert EARN_TAG_MISMATCH.endswith("Nothing was moved.")


# ── the command, on a bare host — the plan is over the CALLER's book ─────────

def _yield_class():
    import bot.skills.telegram_handler as th
    return next(v for v in vars(th).values()
                if isinstance(v, type) and hasattr(v, "_cmd_stake") and hasattr(v, "_earn_account"))


class _Users:
    """The store the mixin asks: who is revoked, who holds `stake`."""

    def __init__(self, role="trader", revoked=False, denial=None, raise_revoked=False):
        self.role, self.revoked, self.denial, self.raise_revoked = role, revoked, denial, raise_revoked

    def live_trading_revoked(self, uid):
        if self.raise_revoked:
            raise RuntimeError("users.json unreadable")
        return self.revoked

    def permission_denial(self, uid, command):
        return self.denial

    def get(self, uid):
        return {"role": self.role}


def _host(monkeypatch, store, *, uid=TRADER, is_admin=False, users=None,
          caller_balance=None, caller_fetch_raises=False, fallback_to_operator=False,
          op_client=OP_CLIENT, live=True):
    """Bind the real command group onto a bare host with an ASYMMETRIC world:
    operator free margin $1,000 off the engine cache, the caller's $250 off
    their own executor, two different clients."""
    cls = _yield_class()
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", lambda: store)
    import bot.config as cfg
    monkeypatch.setattr(type(cfg.CONFIG), "is_live", lambda self: live)

    sent: list[tuple[str, object]] = []

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append((text, reply_markup))

    caller_exec = SimpleNamespace(fetch_balance=AsyncMock(
        side_effect=RuntimeError("venue down") if caller_fetch_raises else None,
        return_value=caller_balance if caller_balance is not None
        else {"free": 250.0, "used": 0.0, "total": 250.0, "holdings": []}))
    # The operator's executor ANSWERS a balance — the operator's — so a read
    # that reaches it by mistake produces a number the assertions can see,
    # rather than an AttributeError the code's own except turns into None.
    live_executor = SimpleNamespace(name="operator executor", fetch_balance=AsyncMock(
        return_value={"free": 1000.0, "used": 0.0, "total": 1000.0, "holdings": []}))
    engine = SimpleNamespace(
        live_balance_cached=lambda max_age_s=900.0: {"free": 1000.0},
        live_executor=live_executor,
        balance_view_calls=[],
    )

    def _bve(user_id):
        engine.balance_view_calls.append(user_id)
        return live_executor if fallback_to_operator else caller_exec
    engine.balance_view_executor = _bve

    guarded: list[str] = []

    async def _guard(update, command="", ctx=None):
        guarded.append(command)
        return True

    host = SimpleNamespace(
        engine=engine, users=users or _Users(), sent=sent, guarded=guarded,
        _send=_send, _guard=_guard, _is_admin=lambda update: is_admin,
        _get_tg_id=lambda update: uid, _lang=lambda update: "en",
        _yield_client=lambda: op_client, caller_exec=caller_exec,
    )
    for name in ("_engine_free_usdt", "_earn_account", "_free_usdt_for", "_earn_button_account",
                 "_cmd_stake", "_stake_fixed_plan", "_cmd_unstake"):
        setattr(host, name, getattr(cls, name).__get__(host))
    return host


def _plant_build(monkeypatch, *, fixed=False):
    """build_report that records WHICH client and WHICH free margin it was
    handed, and answers a row whose stakeable figure is that margin — so the
    card prints the number the plan was over."""
    seen: list[tuple[object, object]] = []

    def fake_build(client, futures_free_usdt=0.0, prices=None):
        seen.append((getattr(client, "_api_key", None), futures_free_usdt))
        rep = YieldReport()
        if futures_free_usdt is None:
            rep.incomplete = "Free futures margin could not be read, so it is not counted below."
            return rep
        rep.rows = [YieldRow(coin="USDT", idle_amount=futures_free_usdt, idle_usd=futures_free_usdt,
                             stakeable_usd=futures_free_usdt, apy_flexible=8.5,
                             est_year_usd=futures_free_usdt * 0.085, source="futures free",
                             product_id="7001",
                             fixed_terms=[{"days": 90, "apy": 9.9, "product_id": "fix90"}] if fixed else [])]
        return rep
    monkeypatch.setattr("bot.core.yield_radar.build_report", fake_build)
    return seen


def _update_obj(args=()):
    return SimpleNamespace(), SimpleNamespace(args=list(args))


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class TestThePlanCard:
    @pytest.mark.asyncio
    async def test_a_linked_traders_plan_is_over_their_own_book(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert host.guarded == ["stake"], "the role gate runs first"
        card, markup = host.sent[-1]
        assert "your linked Bitget" in card and FP in card
        assert "$250.00" in card and "1,000" not in card, card
        assert "the operator's" not in card
        assert seen == [(CALLER_KEY, 250.0)], "THE DEFECT: built over the operator's keys or margin"
        assert host.engine.balance_view_calls == [TRADER]
        assert "yld:s:USDT:" + TRADER in _buttons(markup)
        assert "yld:s:USDT:op" not in _buttons(markup)

    @pytest.mark.asyncio
    async def test_an_admin_without_keys_plans_over_the_operators_book_as_before(self, monkeypatch):
        host = _host(monkeypatch, _Store("absent"), uid=OPERATOR, is_admin=True)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        card, markup = host.sent[-1]
        assert "the operator's Bitget" in card and "$1,000.00" in card and "250" not in card
        assert seen == [("operator-api-key", 1000.0)]
        assert host.engine.balance_view_calls == [], "the operator's margin is the engine cache"
        assert "yld:s:USDT:op" in _buttons(markup)

    @pytest.mark.asyncio
    async def test_a_linked_admin_plans_over_their_own_book(self, monkeypatch):
        host = _host(monkeypatch, _linked(), uid=OPERATOR, is_admin=True)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [(CALLER_KEY, 250.0)]
        assert "your linked Bitget" in host.sent[-1][0]

    @pytest.mark.asyncio
    async def test_a_callers_unread_margin_is_none_never_the_operators_cache(self, monkeypatch):
        host = _host(monkeypatch, _linked(), caller_fetch_raises=True)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [(CALLER_KEY, None)], seen
        card = host.sent[-1][0]
        assert "could not be read" in card and "1,000" not in card

    @pytest.mark.asyncio
    async def test_a_venue_that_reports_no_free_field_is_none_not_zero(self, monkeypatch):
        host = _host(monkeypatch, _linked(), caller_balance={"total": 250.0, "used": 0.0})
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [(CALLER_KEY, None)]

    @pytest.mark.asyncio
    async def test_a_resolver_that_fell_back_to_the_operators_executor_answers_none(self, monkeypatch):
        # balance_view_executor falls back to the operator executor when a
        # record vanishes between resolve and read; that book's number is not
        # this caller's, and None is what "could not read" says.
        host = _host(monkeypatch, _linked(), fallback_to_operator=True)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [(CALLER_KEY, None)]

    @pytest.mark.asyncio
    async def test_a_paper_deployment_still_reads_the_callers_real_account(self, monkeypatch):
        # The bot's paper mode is a fact about the OPERATOR's book (0.0 free
        # margin: nothing live to read). A caller's linked account is real
        # whatever mode the bot runs in.
        host = _host(monkeypatch, _linked(), live=False)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [(CALLER_KEY, 250.0)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store,is_admin,expect", [
        (_Store("absent"), False, "/connect"),
        (_Store("unreadable"), False, "cannot read them"),
        (_linked(venue="bybit"), False, "Bitget-only"),
        (_Store("absent", raise_on=("state",)), False, "could not be asked"),
    ])
    async def test_a_refused_caller_has_nothing_read(self, monkeypatch, store, is_admin, expect):
        host = _host(monkeypatch, store, is_admin=is_admin)
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [] and host.engine.balance_view_calls == []
        text = host.sent[-1][0]
        assert expect in text and text.endswith("Nothing was moved.")
        assert host.sent[-1][1] is None, "a refusal carries no button"

    @pytest.mark.asyncio
    async def test_a_revoked_trader_is_refused_before_any_read(self, monkeypatch):
        host = _host(monkeypatch, _linked(), users=_Users(revoked=True))
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [] and "revoked" in host.sent[-1][0]

    @pytest.mark.asyncio
    async def test_a_store_that_cannot_say_who_is_revoked_is_unavailable(self, monkeypatch):
        host = _host(monkeypatch, _linked(), users=_Users(raise_revoked=True))
        seen = _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        assert seen == [] and "could not be asked" in host.sent[-1][0]

    @pytest.mark.asyncio
    async def test_the_fixed_plan_is_over_the_same_account_and_tags_its_buttons(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        seen = _plant_build(monkeypatch, fixed=True)
        await host._cmd_stake(*_update_obj(["fixed"]))
        card, markup = host.sent[-1]
        assert "your linked Bitget" in card and "$250.00" in card
        assert seen == [(CALLER_KEY, 250.0)]
        assert f"yldf:1:USDT:fix90:90:{TRADER}" in _buttons(markup)

    @pytest.mark.asyncio
    async def test_nothing_stakeable_still_names_the_account(self, monkeypatch):
        host = _host(monkeypatch, _linked(), caller_balance={"free": 1.0, "total": 1.0})
        _plant_build(monkeypatch)
        await host._cmd_stake(*_update_obj())
        card = host.sent[-1][0]
        assert "Nothing stakeable" in card and "your linked Bitget" in card


class TestTheHoldingsCard:
    @pytest.mark.asyncio
    async def test_unread_holdings_are_unknown_not_nothing_to_redeem(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        asked = []

        def _assets(client):
            asked.append(getattr(client, "_api_key", None))
            return None
        monkeypatch.setattr("bot.core.yield_radar.fetch_savings_assets", _assets)
        await host._cmd_unstake(*_update_obj())
        card = host.sent[-1][0]
        assert asked == [CALLER_KEY]
        assert "could not be read" in card and "unknown, not empty" in card
        assert "nothing to redeem" not in card, "THE DEFECT: a failed read shown as an empty book"
        assert "your linked Bitget" in card

    @pytest.mark.asyncio
    async def test_a_read_empty_book_is_nothing_to_redeem(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        monkeypatch.setattr("bot.core.yield_radar.fetch_savings_assets", lambda client: [])
        await host._cmd_unstake(*_update_obj())
        assert "nothing to redeem" in host.sent[-1][0]

    @pytest.mark.asyncio
    async def test_a_holding_gets_a_tagged_redeem_button(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        monkeypatch.setattr("bot.core.yield_radar.fetch_savings_assets",
                            lambda client: [{"product_id": "7001", "coin": "USDT", "amount": 98.0, "apy": 8.5}])
        await host._cmd_unstake(*_update_obj())
        card, markup = host.sent[-1]
        assert "your linked Bitget" in card
        assert f"yld:r:7001:{TRADER}" in _buttons(markup)

    @pytest.mark.asyncio
    async def test_a_refused_caller_has_no_holdings_read(self, monkeypatch):
        host = _host(monkeypatch, _Store("absent"))
        asked = []
        monkeypatch.setattr("bot.core.yield_radar.fetch_savings_assets",
                            lambda client: asked.append(1))
        await host._cmd_unstake(*_update_obj())
        assert asked == [] and "/connect" in host.sent[-1][0]


# ── the button: the presser's role, the presser's keys, the plan's tag ──────

class TestTheButtonAccount:
    @pytest.mark.asyncio
    async def test_the_plans_owner_may_press_it(self, monkeypatch):
        host = _host(monkeypatch, _linked())
        acct = await host._earn_button_account(SimpleNamespace(), TRADER)
        assert acct is not None and acct.state == "caller" and acct.client._api_key == CALLER_KEY
        assert host.sent == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tag", [OPERATOR_TAG, OPERATOR, "", "5353"])
    async def test_a_tag_that_is_not_the_pressers_account_is_refused(self, monkeypatch, tag):
        host = _host(monkeypatch, _linked())
        acct = await host._earn_button_account(SimpleNamespace(), tag)
        assert acct is None and host.sent[-1][0] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_the_operators_own_button_needs_the_operators_account(self, monkeypatch):
        host = _host(monkeypatch, _Store("absent"), uid=OPERATOR, is_admin=True)
        acct = await host._earn_button_account(SimpleNamespace(), OPERATOR_TAG)
        assert acct is not None and acct.state == "operator"
        # ...and once the admin links their own keys, the old operator-tagged
        # plan is not theirs any more.
        host2 = _host(monkeypatch, _linked(), uid=OPERATOR, is_admin=True)
        assert await host2._earn_button_account(SimpleNamespace(), OPERATOR_TAG) is None
        assert host2.sent[-1][0] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_a_presser_without_the_permission_is_refused_before_the_store_is_asked(self, monkeypatch):
        store = _linked()
        host = _host(monkeypatch, store, uid=STRANGER, users=_Users(role="paper", denial="role"))
        acct = await host._earn_button_account(SimpleNamespace(), STRANGER)
        assert acct is None
        assert "needs a higher role" in host.sent[-1][0]
        assert store.asked == [], "no account is resolved for a refused role"

    @pytest.mark.asyncio
    async def test_a_presser_whose_keys_stopped_decrypting_is_told_so(self, monkeypatch):
        host = _host(monkeypatch, _Store("unreadable"))
        acct = await host._earn_button_account(SimpleNamespace(), TRADER)
        assert acct is None and "cannot read them" in host.sent[-1][0]


# ── the real dispatcher: a tap executes with the presser's client, and the record says whose ──

@pytest.fixture
def bot(tmp_path):
    h = _handler(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
    h._yield_client = lambda: OP_CLIENT
    h._engine_free_usdt = lambda: 1000.0
    # The shared fixture's `_send` keeps the text and drops the keyboard; the
    # step-one drive reads the keyboard, so record both.
    h.markups = []

    async def _send(update, text, reply_markup=None, edit=False):
        h.sent.append(text)
        h.markups.append(reply_markup)
    h._send = _send
    h.engine.live_executor = SimpleNamespace(name="operator executor")
    caller_exec = SimpleNamespace(fetch_balance=AsyncMock(
        return_value={"free": 250.0, "used": 0.0, "total": 250.0, "holdings": []}))
    h.engine.balance_view_executor = lambda uid: caller_exec
    yield h
    patch.stopall()


def _tap(uid, data):
    q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        callback_query=q,
        effective_user=SimpleNamespace(id=int(uid), first_name="T"),
        effective_chat=SimpleNamespace(id=int(uid), type="private"),
        message=None,
    )


def _arm(monkeypatch, bot, store):
    """Plant the store, capture the sealed record and the money call."""
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", lambda: store)
    records, moved = [], []
    monkeypatch.setattr("bot.skills.callback_handler.audit",
                        lambda ch, msg, **kw: records.append((msg, kw)))

    def _stake(client, coin, futures_free_usdt=0.0):
        moved.append(("stake", getattr(client, "_api_key", None), coin, futures_free_usdt))
        return ActionResult(True, "subscribed")

    def _stake_fixed(client, coin, product_id, days, futures_free_usdt=0.0):
        moved.append(("fixed", getattr(client, "_api_key", None), coin, product_id, days, futures_free_usdt))
        return ActionResult(True, "locked")

    def _unstake(client, product_id):
        moved.append(("redeem", getattr(client, "_api_key", None), product_id))
        return ActionResult(True, "redeemed")
    monkeypatch.setattr("bot.core.yield_radar.execute_stake", _stake)
    monkeypatch.setattr("bot.core.yield_radar.execute_stake_fixed", _stake_fixed)
    monkeypatch.setattr("bot.core.yield_radar.execute_unstake", _unstake)
    return records, moved


class TestTheTap:
    @pytest.mark.asyncio
    async def test_a_traders_tap_executes_with_their_keys_and_the_record_names_them(self, monkeypatch, bot):
        records, moved = _arm(monkeypatch, bot, _linked())
        await bot._handle_callback(_tap(TRADER, f"yld:s:USDT:{TRADER}"), None)
        assert moved == [("stake", CALLER_KEY, "USDT", 250.0)], moved
        msg, kw = next(r for r in records if kw_is(r[1], "earn_action"))
        assert kw["result"] == "OK"
        assert kw["data"]["account"] == TRADER and kw["data"]["key"] == FP
        assert kw["data"]["venue"] == EARN_VENUE and kw["data"]["by"] == TRADER
        assert CALLER_KEY not in str(kw["data"]) and CALLER_SECRET not in str(kw["data"])
        assert "your linked Bitget" in bot.sent[-1] and "subscribed" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_the_operator_tapping_a_traders_button_moves_nothing(self, monkeypatch, bot):
        records, moved = _arm(monkeypatch, bot, _Store("absent"))
        await bot._handle_callback(_tap(OPERATOR, f"yld:s:USDT:{TRADER}"), None)
        assert moved == [] and bot.sent[-1] == EARN_TAG_MISMATCH
        assert not any(kw_is(kw, "earn_action") for _m, kw in records)

    @pytest.mark.asyncio
    async def test_a_trader_tapping_the_operators_button_moves_nothing(self, monkeypatch, bot):
        _records, moved = _arm(monkeypatch, bot, _linked())
        await bot._handle_callback(_tap(TRADER, "yld:s:USDT:op"), None)
        assert moved == [] and bot.sent[-1] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_a_button_from_before_tags_existed_moves_nothing(self, monkeypatch, bot):
        _records, moved = _arm(monkeypatch, bot, _linked())
        await bot._handle_callback(_tap(TRADER, "yld:s:USDT"), None)
        assert moved == [] and bot.sent[-1] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_a_self_admitted_users_tap_is_refused_by_role(self, monkeypatch, bot):
        store = _linked()
        _records, moved = _arm(monkeypatch, bot, store)
        await bot._handle_callback(_tap(STRANGER, f"yld:s:USDT:{STRANGER}"), None)
        assert moved == [] and "needs a higher role" in bot.sent[-1]
        assert store.asked == []

    @pytest.mark.asyncio
    async def test_the_operators_own_tap_still_runs_on_the_operators_keys(self, monkeypatch, bot):
        records, moved = _arm(monkeypatch, bot, _Store("absent"))
        await bot._handle_callback(_tap(OPERATOR, "yld:s:USDT:op"), None)
        assert moved == [("stake", "operator-api-key", "USDT", 1000.0)]
        _m, kw = next(r for r in records if kw_is(r[1], "earn_action"))
        assert kw["data"]["account"] == "operator" and kw["data"]["by"] == OPERATOR

    @pytest.mark.asyncio
    async def test_a_redeem_tap_is_the_same_gate(self, monkeypatch, bot):
        records, moved = _arm(monkeypatch, bot, _linked())
        await bot._handle_callback(_tap(TRADER, f"yld:r:7001:{TRADER}"), None)
        assert moved == [("redeem", CALLER_KEY, "7001")]
        await bot._handle_callback(_tap(TRADER, "yld:r:7001:op"), None)
        assert len(moved) == 1 and bot.sent[-1] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_the_fixed_lock_step_two_executes_with_the_pressers_keys(self, monkeypatch, bot):
        records, moved = _arm(monkeypatch, bot, _linked())
        await bot._handle_callback(_tap(TRADER, f"yldf:2:USDT:fix90:90:{TRADER}"), None)
        assert moved == [("fixed", CALLER_KEY, "USDT", "fix90", 90, 250.0)]
        _m, kw = next(r for r in records if kw_is(r[1], "earn_action_fixed"))
        assert kw["data"]["account"] == TRADER and kw["data"]["key"] == FP
        assert "your linked Bitget" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_the_fixed_lock_step_one_carries_the_tag_forward(self, monkeypatch, bot):
        _records, moved = _arm(monkeypatch, bot, _linked())
        _plant_build(monkeypatch, fixed=True)
        await bot._handle_callback(_tap(TRADER, f"yldf:1:USDT:fix90:90:{TRADER}"), None)
        assert moved == []
        assert "FINAL CONFIRM" in bot.sent[-1] and "your linked Bitget" in bot.sent[-1]
        assert f"yldf:2:USDT:fix90:90:{TRADER}" in "\n".join(_kb_data(bot))

    @pytest.mark.asyncio
    async def test_a_fixed_tap_by_the_wrong_account_moves_nothing(self, monkeypatch, bot):
        _records, moved = _arm(monkeypatch, bot, _Store("absent"))
        await bot._handle_callback(_tap(OPERATOR, f"yldf:2:USDT:fix90:90:{TRADER}"), None)
        assert moved == [] and bot.sent[-1] == EARN_TAG_MISMATCH

    @pytest.mark.asyncio
    async def test_cancel_moves_nothing_and_needs_no_account(self, monkeypatch, bot):
        store = _linked()
        _records, moved = _arm(monkeypatch, bot, store)
        await bot._handle_callback(_tap(TRADER, "yld:x"), None)
        assert moved == [] and "Cancelled" in bot.sent[-1] and store.asked == []


def kw_is(kw, action):
    return kw.get("action") == action


def _kb_data(bot):
    out = []
    for m in bot.markups:
        if m is not None:
            out.extend(b.callback_data for row in m.inline_keyboard for b in row)
    return out


# ── the reads behind the card are three-valued ───────────────────────────────

class _Client:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def request(self, method, path, body_dict=None, timeout=10):
        self.calls.append((method, path, body_dict))
        if self.exc:
            raise self.exc
        return self.resp


class TestTheReads:
    def test_holdings_that_could_not_be_read_are_none_and_an_empty_book_is_a_list(self):
        from bot.core.yield_radar import fetch_savings_assets
        assert fetch_savings_assets(_Client(exc=RuntimeError("down"))) is None
        assert fetch_savings_assets(_Client({"code": "40001", "msg": "denied"})) is None
        assert fetch_savings_assets(_Client({"code": "00000", "data": {"resultList": []}})) == []

    def test_a_redeem_over_unread_holdings_posts_nothing(self):
        from bot.core.yield_radar import execute_unstake
        c = _Client(exc=RuntimeError("down"))
        res = execute_unstake(c, "7001")
        assert not res.ok and "could not be read" in res.message
        assert not any(m == "POST" for m, _p, _b in c.calls)

    def test_a_catalog_that_could_not_be_read_is_not_an_empty_catalog(self):
        from bot.core.yield_radar import build_report, fetch_savings_catalog
        assert fetch_savings_catalog(_Client(exc=RuntimeError("down"))) is None
        assert fetch_savings_catalog(_Client({"code": "00000", "data": []})) == {}
        unread = build_report(_Client(exc=RuntimeError("down")), futures_free_usdt=100.0)
        empty = build_report(_Client({"code": "00000", "data": []}), futures_free_usdt=100.0)
        assert "could not be read" in unread.error and "operator" not in unread.error
        assert "lists no savings products" in empty.error
        assert unread.error != empty.error


# ── the tables, the words, the shape ─────────────────────────────────────────

class TestTheTables:
    def test_stake_is_a_trader_permission_and_the_commands_carry_it(self):
        from bot.utils.user_store import ROLE_PERMISSIONS, VOUCHED_ONLY_PERMISSIONS
        assert "stake" in ROLE_PERMISSIONS["trader"] and "stake" in VOUCHED_ONLY_PERMISSIONS
        for role in ("paper", "viewer", "pending"):
            assert "stake" not in ROLE_PERMISSIONS[role]
        src = (ROOT / "bot" / "skills" / "yield_commands.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        guards = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "guard":
                        guards[node.name] = dec.args[0].value
        assert guards.get("_cmd_stake") == "stake" and guards.get("_cmd_unstake") == "stake"

    def test_the_money_doors_reach_the_operators_keys_only_through_the_resolver(self):
        """A scan for the SHAPE the resolver exists to end: no money door and
        no button branch builds a client from config or gates on `_is_admin`
        any more. The behaviour is driven above; this pins that a later edit
        cannot quietly put the old line back beside the new one."""
        src = (ROOT / "bot" / "skills" / "yield_commands.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in (
                    "_cmd_stake", "_stake_fixed_plan", "_cmd_unstake", "_free_usdt_for",
                    "_earn_button_account"):
                body = ast.unparse(node)
                assert "_yield_client(" not in body and "from_config(" not in body, node.name
                assert "_is_admin(" not in body, node.name
        # The two button branches, found by their own condition rather than by
        # a slice of the file: `if data.startswith("yldf:")` and `("yld:")`.
        cb_tree = ast.parse((ROOT / "bot" / "skills" / "callback_handler.py").read_text(encoding="utf-8"))
        branches = {}
        for node in ast.walk(cb_tree):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                    and getattr(node.test.func, "attr", "") == "startswith"
                    and node.test.args and isinstance(node.test.args[0], ast.Constant)
                    and node.test.args[0].value in ("yldf:", "yld:")):
                branches[node.test.args[0].value] = ast.unparse(node)
        assert set(branches) == {"yldf:", "yld:"}, sorted(branches)
        for prefix, body in branches.items():
            assert "_yield_client(" not in body and "_is_admin(" not in body, prefix
            assert "from_config(" not in body, prefix
            assert body.count("_earn_button_account(") == 1, prefix
            assert "earn.record()" in body, prefix

    def test_the_catalogue_and_the_prompt_stopped_saying_admin_only(self):
        from bot.skills.chat_runtime import _CHAT_CANNOT_ACT_RULE, act_intent_notice
        from bot.skills.command_catalog import all_entries
        entries = all_entries()
        for name in ("stake", "unstake"):
            title, audience, desc = entries[name]
            assert audience == "user", (name, audience)
            assert "operator" not in desc.lower()
        assert "admin-only" not in _CHAT_CANNOT_ACT_RULE
        assert "/connect" not in _CHAT_CANNOT_ACT_RULE, (
            "the rule is one string every surface reads — the linking door is "
            "the notice's, per surface, never the rule's")
        for surface in ("telegram", "web"):
            n = act_intent_notice("stake", None, surface, verb="stake")
            assert "trader role" in n and "admin-only" not in n
        # The linking door is the SURFACE's own: /connect on Telegram; on the
        # web the dashboard's step, because the web chat cannot run /connect
        # and a slash command named to it is a door painted on a wall
        # (tests/test_chat_prompt_describes_only_the_callers_book.py pins the
        # same rule on the prompt's no-account block).
        assert "/connect" in act_intent_notice("stake", None, "telegram", verb="stake")
        web = act_intent_notice("stake", None, "web", verb="stake")
        assert "/connect" not in web and "Connect an exchange step" in web

    def test_the_guarded_commands_baseline_carries_both(self):
        names = set((ROOT / "tests" / "guarded_commands_baseline.txt").read_text().split())
        assert {"_cmd_stake", "_cmd_unstake"} <= names
