"""Pro and Elite time paid in Telegram Stars: the grant, the checks, the doors.

Driven through the real `UserStore` and the real handler methods; Telegram
itself is a recorder of what it was handed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.token import stars
from bot.utils import user_store as us
from bot.utils.user_store import UserStore

DAY = 86_400


@pytest.fixture
def store(tmp_path, monkeypatch):
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(us, "_now_ts", lambda: clock["now"])
    st = UserStore(str(tmp_path / "users.json"))
    st.register("42", name="Buyer")
    st.clock = clock
    return st


@pytest.fixture
def selling(monkeypatch):
    monkeypatch.setenv("STARS_PAYMENTS_ENABLED", "true")
    monkeypatch.setenv("STARS_PRO_PRICE", "250")
    monkeypatch.setenv("STARS_ELITE_PRICE", "900")
    monkeypatch.delenv("STARS_PERIOD_DAYS", raising=False)


def _ladder(enforced=False, basic_cap=5, premium=False):
    return {"enforced": enforced, "tiers": [
        {"tier": "basic", "chat_per_day": basic_cap, "premium_analyze": False, "unlocks": []},
        {"tier": "pro", "chat_per_day": None, "premium_analyze": premium,
         "unlocks": [{"feature": "deepscan", "commands": ["deepscan"]}]},
        {"tier": "elite", "chat_per_day": None, "premium_analyze": premium,
         "unlocks": [{"feature": "run_backtest", "commands": ["backtest"]}]},
    ]}


# ── the paid grant ─────────────────────────────────────────────────────────

def test_a_paid_period_starts_now_and_lapses_to_basic(store):
    g = store.grant_paid_tier("42", "pro", 30, "c1")
    assert g["tier"] == "pro" and g["until"] == store.clock["now"] + 30 * DAY
    assert store.get_tier("42") == "pro"
    store.clock["now"] += 30 * DAY
    assert store.get_tier("42") == "basic", "an ended period is not a grant"


def test_a_second_period_of_the_same_plan_extends_the_first(store):
    first = store.grant_paid_tier("42", "pro", 30, "c1")["until"]
    store.clock["now"] += 10 * DAY
    assert store.grant_paid_tier("42", "pro", 30, "c2")["until"] == first + 30 * DAY


def test_one_charge_buys_one_period_however_often_it_is_delivered(store):
    until = store.grant_paid_tier("42", "pro", 30, "c1")["until"]
    again = store.grant_paid_tier("42", "pro", 30, "c1")
    assert again["duplicate"] is True and again["until"] == until


@pytest.mark.parametrize("held, until_days, buying", [
    ("pro", None, "pro"),       # an operator grant with no end
    ("elite", None, "pro"),
    ("admin", None, "elite"),
    ("elite", 5, "pro"),        # a higher paid tier still running
])
def test_a_period_that_buys_nothing_is_refused(store, held, until_days, buying):
    store.set_tier("42", held)
    if until_days is not None:
        store._users["42"]["tier_until"] = store.clock["now"] + until_days * DAY
    assert store.grant_paid_tier("42", buying, 30, "c1") is None
    assert "c1" not in store.get("42").get("paid_charges", [])


def test_an_upgrade_from_a_running_pro_to_elite_is_sold(store):
    store.grant_paid_tier("42", "pro", 30, "c1")
    g = store.grant_paid_tier("42", "elite", 30, "c2")
    assert g["tier"] == "elite" and g["until"] == store.clock["now"] + 30 * DAY


def test_an_operator_grant_clears_the_paid_end_date(store):
    store.grant_paid_tier("42", "pro", 30, "c1")
    store.set_tier("42", "elite")
    assert store.tier_until("42") is None
    store.clock["now"] += 365 * DAY
    assert store.get_tier("42") == "elite"


def test_only_pro_and_elite_are_sold(store):
    assert store.grant_paid_tier("42", "admin", 30, "c1") is None
    assert store.grant_paid_tier("42", "basic", 30, "c1") is None
    assert store.grant_paid_tier("nobody", "pro", 30, "c1") is None


# ── what is on sale ────────────────────────────────────────────────────────

def test_payments_are_off_until_the_switch_and_a_price_are_both_set(monkeypatch):
    monkeypatch.delenv("STARS_PAYMENTS_ENABLED", raising=False)
    monkeypatch.setenv("STARS_PRO_PRICE", "250")
    assert stars.payments_on() is False
    monkeypatch.setenv("STARS_PAYMENTS_ENABLED", "true")
    assert stars.payments_on() is True
    monkeypatch.delenv("STARS_PRO_PRICE")
    monkeypatch.setenv("STARS_ELITE_PRICE", "zero")
    assert stars.payments_on() is False, "an unparseable price sells nothing"


def test_commands_count_as_a_benefit_only_while_tiers_are_enforced():
    assert stars.benefits(_ladder(enforced=False), "pro") == [
        "no daily cap on AI questions (Basic: 5 a day)"]
    assert stars.benefits(_ladder(enforced=True), "elite")[-1] == "/backtest /deepscan"


def test_a_tier_that_adds_nothing_is_not_offered(selling):
    flat = _ladder(enforced=False, basic_cap=None)
    assert stars.benefits(flat, "pro") == []
    assert stars.offers(flat) == []
    assert [o["tier"] for o in stars.offers(_ladder())] == ["pro", "elite"]


def test_the_premium_model_is_named_only_where_the_ladder_routes_it():
    assert "the operator-funded model on /analyze" in stars.benefits(_ladder(premium=True), "pro")
    assert "the operator-funded model on /analyze" not in stars.benefits(_ladder(), "pro")


# ── the checkout question ──────────────────────────────────────────────────

def _check(raw=None, uid="42", currency="XTR", total=250, held="basic", until=None):
    raw = raw if raw is not None else stars.payload("42", "pro", 30, 250)
    return stars.check_checkout(raw, uid, currency, total, _ladder(), held, until, 1e9)


def test_the_invoice_this_bot_issued_is_accepted(selling):
    assert _check() is None


@pytest.mark.parametrize("kwargs, needle", [
    ({"raw": "rc1:pro:30:250"}, "not one this bot issued"),
    ({"uid": "7"}, "someone else"),
    ({"currency": "USD"}, "Stars"),
    ({"total": 1}, "price changed"),
    ({"raw": stars.payload("42", "pro", 30, 100), "total": 100}, "price changed"),
    ({"held": "pro"}, "buy nothing"),
])
def test_each_mismatch_alone_refuses_the_checkout(selling, kwargs, needle):
    assert needle in _check(**kwargs)


def test_a_checkout_after_sales_stop_is_refused(selling, monkeypatch):
    monkeypatch.setenv("STARS_PAYMENTS_ENABLED", "false")
    assert "not on sale" in _check()


# ── the doors ──────────────────────────────────────────────────────────────

class _Bot:
    def __init__(self, refund_ok=True):
        self.sent, self.invoices, self.refunds = [], [], []
        self.refund_ok = refund_ok

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def send_invoice(self, **kw):
        self.invoices.append(kw)

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        self.refunds.append((user_id, telegram_payment_charge_id))
        return self.refund_ok


class _Handler:
    """The real mixin methods, bound to a recorder host."""
    from bot.skills.upgrade_commands import UpgradeCommands as _U
    _cmd_upgrade = _U._cmd_upgrade
    _on_pre_checkout = _U._on_pre_checkout
    _on_successful_payment = _U._on_successful_payment
    _notify_operator_upgrade = _U._notify_operator_upgrade

    def __init__(self, users):
        self.users, self.replies = users, []

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.replies.append(text)

    def _get_tg_id(self, update):
        return str(update.effective_user.id)

    async def _guard(self, update, permission, ctx=None):
        self.guarded = permission
        return True

    @staticmethod
    def _operator_chat_ids():
        return ["1"]


def _run(coro):
    return asyncio.run(coro)


def _payment_update(raw, charge="ch-1"):
    pay = SimpleNamespace(invoice_payload=raw, telegram_payment_charge_id=charge)
    return SimpleNamespace(effective_user=SimpleNamespace(id=42, first_name="Buyer"),
                           message=SimpleNamespace(successful_payment=pay))


def test_a_landed_payment_grants_the_period(store, selling):
    h, bot = _Handler(store), _Bot()
    _run(h._on_successful_payment(_payment_update(stars.payload("42", "pro", 30, 250)),
                                  SimpleNamespace(bot=bot)))
    assert store.get_tier("42") == "pro" and not bot.refunds
    assert "Pro</b> is active until" in h.replies[-1]


def test_a_payment_that_buys_nothing_is_refunded_and_the_operator_told(store, selling):
    store.set_tier("42", "elite")
    h, bot = _Handler(store), _Bot()
    _run(h._on_successful_payment(_payment_update(stars.payload("42", "pro", 30, 250)),
                                  SimpleNamespace(bot=bot)))
    assert bot.refunds == [(42, "ch-1")]
    assert store.get_tier("42") == "elite" and store.tier_until("42") is None
    assert "refunded" in h.replies[-1] and "refunded" in bot.sent[-1][1]


def test_a_refund_that_fails_says_so(store, selling):
    store.set_tier("42", "elite")
    h, bot = _Handler(store), _Bot(refund_ok=False)
    _run(h._on_successful_payment(_payment_update(stars.payload("42", "pro", 30, 250)),
                                  SimpleNamespace(bot=bot)))
    assert "asked the operator to refund" in h.replies[-1]
    assert "could NOT be refunded" in bot.sent[-1][1]


def test_pre_checkout_answers_from_the_checks(store, selling, monkeypatch):
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "plan_ladder", lambda users, uid: _ladder())
    answers = []

    async def answer(ok, error_message=None):
        answers.append((ok, error_message))

    def q(raw):
        return SimpleNamespace(pre_checkout_query=SimpleNamespace(
            from_user=SimpleNamespace(id=42), invoice_payload=raw, currency="XTR",
            total_amount=250, answer=answer))

    h = _Handler(store)
    _run(h._on_pre_checkout(q(stars.payload("42", "pro", 30, 250)), None))
    _run(h._on_pre_checkout(q(stars.payload("7", "pro", 30, 250)), None))
    assert answers[0] == (True, None)
    assert answers[1][0] is False and "someone else" in answers[1][1]


def test_upgrade_without_payments_sends_a_request_and_no_invoice(store, monkeypatch):
    monkeypatch.delenv("STARS_PAYMENTS_ENABLED", raising=False)
    h, bot = _Handler(store), _Bot()
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=42, first_name="Buyer"),
                          effective_chat=SimpleNamespace(id=42))
    _run(h._cmd_upgrade(upd, SimpleNamespace(bot=bot, args=[])))
    assert h.guarded == "tier"
    assert not bot.invoices
    assert "Plan request" in bot.sent[-1][1] and "passed your request on" in h.replies[-1]


def test_upgrade_sells_nothing_where_no_tier_adds_anything(store, selling, monkeypatch):
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "plan_ladder",
                        lambda users, uid: _ladder(enforced=False, basic_cap=None))
    h, bot = _Handler(store), _Bot()
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=42, first_name="Buyer"),
                          effective_chat=SimpleNamespace(id=42))
    _run(h._cmd_upgrade(upd, SimpleNamespace(bot=bot, args=[])))
    assert bot.invoices == []
    assert "Nothing is on sale" in h.replies[-1]


def test_upgrade_with_payments_issues_one_invoice_per_tier_on_sale(store, selling, monkeypatch):
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "plan_ladder", lambda users, uid: _ladder())
    h, bot = _Handler(store), _Bot()
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=42, first_name="Buyer"),
                          effective_chat=SimpleNamespace(id=42))
    _run(h._cmd_upgrade(upd, SimpleNamespace(bot=bot, args=[])))
    tiers = [stars.parse_payload(i["payload"])["tier"] for i in bot.invoices]
    assert tiers == ["pro", "elite"]
    assert all(i["currency"] == "XTR" and i["provider_token"] == "" for i in bot.invoices)


# ── the prompts ────────────────────────────────────────────────────────────

def test_the_web_quota_wall_sells_only_what_the_cap_decides():
    from bot.web import chat_quota
    web = chat_quota.exhausted_notice({"limit": 5, "reset_in_seconds": 3600}, "en", surface="web")
    assert "Pro / Elite: no daily cap" in web and "/upgrade" in web
    assert "#account/aplan" in web
    for promise in ("live signals", "deeper research", "priority models"):
        assert promise not in web


def test_the_offer_line_is_empty_when_the_cap_exempts_no_sold_tier(monkeypatch):
    from bot.web import chat_quota
    monkeypatch.setattr(chat_quota, "is_quota_exempt", lambda tier: False)
    assert chat_quota.uncapped_offer("en") == ""


@pytest.mark.parametrize("on, words", [(True, "bought with Telegram Stars"),
                                       (False, "granted by the operator")])
def test_the_tier_card_names_the_way_to_get_a_tier(on, words):
    from bot.formatters.tier_card import render_tier_card
    ladder = dict(_ladder(), stars=on)
    card = render_tier_card({"tier": "basic", "granted": "basic", "granted_known": True}, ladder)
    assert words in card and "/upgrade" in card
