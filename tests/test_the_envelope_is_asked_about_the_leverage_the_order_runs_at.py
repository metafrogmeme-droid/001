"""The web-live envelope is asked about the notional at the leverage that FILLS.

`_authorize_web_live_trade` reconstructs the order's notional as margin ×
leverage and asks the user's enforce-mode Authority Envelope about it: the
per-trade cap, the 24h cap, and the 24h ledger it then records into. The
leverage it multiplied by was `CONFIG.exchange.default_leverage` -- a config
field -- while the executor places every order at the operator standard (the
`/leverage` override, up to the MAX_LEVERAGE ceiling), lowered by the user's
own preference and the idea's margin-risk cap. Driven against the unfixed
tree under `/leverage set 10` (default 5, ceiling 10):

    margin $50, per-trade cap $300  ->  authorized (250 <= 300), ledger +250
    the executor places the order at 10x: notional $500

Half the order's notional was checked, and half was recorded against the day.
The reading is the user's own executor's, and a leverage that cannot be read
denies by name and records nothing.

SINCE 11 OCTOBER THE READING IS THE SIZED LEVERAGE (`_compute_sized_leverage`).
The venue is set to 10x while every order is sized at 5x, and the executor
places the sized notional: the margin it locks is notional / 10. So the
notional an order carries is its margin x the leverage it is SIZED at, and
the venue's leverage would have doubled it. An override or a preference BELOW
the sizing leverage still lowers the sized one, which is where the original
defect is driven now: under `/leverage set 2` a $50 ticket is $100 of
notional, and a reading of the configured default would have said $250 or
$500.
"""
from __future__ import annotations

import time
import types

import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import live_executor as le
from bot.core.leverage import order_standards
from bot.core.live_executor import LiveExecutor
from bot.guardian import user_authority_store as uas
from bot.guardian.authority import compile_envelope
from bot.guardian.authority_ledger import AuthoritySpendLedger
from bot.web import user_gateway as ug

UID = "web:5"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(uas, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: types.SimpleNamespace(get_venue=lambda uid: "bitget"))
    return store, ledger


def _bind(store, per_trade=300, daily=2000):
    store.bind(UID, compile_envelope({
        "mode": "enforce", "label": "t", "allowed_venues": ["bitget"],
        "symbol_allowlist": ["SOL"], "max_notional_per_trade_usd": per_trade,
        "max_notional_daily_usd": daily}))


@pytest.fixture
def override():
    """The operator's `/leverage set 2`, under the 10x venue standard and the
    5x sizing standard: both the venue's and the sized leverage drop to 2."""
    old = RUNTIME.leverage_override
    RUNTIME.leverage_override = 2
    try:
        assert order_standards(CONFIG.exchange, RUNTIME.leverage_override) == (2, 2)
        yield 2
    finally:
        RUNTIME.leverage_override = old


@pytest.fixture
def no_override():
    old = RUNTIME.leverage_override
    RUNTIME.leverage_override = None
    try:
        assert order_standards(CONFIG.exchange, None) == (5, 10)
        yield
    finally:
        RUNTIME.leverage_override = old


def _engine(executor, margin=50, tid="T1"):
    return types.SimpleNamespace(
        _pending_ideas={tid: types.SimpleNamespace(asset="SOL/USDT")},
        _manual_margin_override={tid: margin},
        live_executor=object(),
        _executor_for=lambda tg_id, venue=None: executor)


def _real_executor(tmp_path, pref=None):
    """A real LiveExecutor as the user's own, its preference planted."""
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._user_leverage_reading = lambda: pref
    return ex


class TestTheLeverageIsTheExecutors:

    def test_the_override_reaches_the_check_and_the_ledger(self, wired, override, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=90)
        ex = _real_executor(tmp_path)
        assert ex._compute_sized_leverage("SOL/USDT", None) == 2, "the premise: the order is sized at 2x"
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(ex), UID, "T1")
        assert ok is False, "a $100 order is over a $90 per-trade cap"
        assert any("per-trade" in r or "notional" in r for r in reasons), reasons
        assert ledger.spent(UID, time.time()) == 0.0, "a denial records nothing"

    def test_an_allowed_order_records_the_notional_that_fills(self, wired, override, tmp_path):
        store, ledger = wired
        _bind(store, per_trade=600)
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(_real_executor(tmp_path)), UID, "T1")
        assert ok is True, reasons
        assert ledger.spent(UID, time.time()) == 100.0

    def test_a_venue_above_the_sizing_leverage_does_not_raise_the_notional(
            self, wired, no_override, tmp_path):
        """The shipped standards: the venue at 10x, the order sized at 5x. A
        $50 ticket is $250 of notional, which the venue holds on $25 of
        margin; priced at the venue's leverage it would read $500 and be
        refused under a $300 cap it fits."""
        store, ledger = wired
        _bind(store, per_trade=300)
        ex = _real_executor(tmp_path)
        assert ex._compute_target_leverage("SOL/USDT", None) == 10
        assert ex._compute_sized_leverage("SOL/USDT", None) == 5
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(ex), UID, "T1")
        assert ok is True, reasons
        assert ledger.spent(UID, time.time()) == 250.0

    def test_the_users_own_lower_preference_lowers_the_notional(self, wired, override, tmp_path):
        """A person pinned to 2x is sized and placed at 2x under the 10x venue
        standard, so their $50 is a $100 order against their own caps."""
        store, ledger = wired
        _bind(store, per_trade=150)
        ex = _real_executor(tmp_path, pref=2)
        assert ex._compute_sized_leverage("SOL/USDT", None) == 2
        assert ex._compute_target_leverage("SOL/USDT", None) == 2
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(ex), UID, "T1")
        assert ok is True, reasons
        assert ledger.spent(UID, time.time()) == 100.0

    def test_the_configured_default_is_never_the_multiplier(self, wired, override, tmp_path):
        """A stub executor answering 3x is what the envelope hears, whatever
        the config's standards (10x venue, 5x sizing) or the override say."""
        store, ledger = wired
        _bind(store, per_trade=600)
        stub = types.SimpleNamespace(_compute_sized_leverage=lambda symbol, idea=None: 3)
        ok, _, _ = ug._authorize_web_live_trade({}, _engine(stub), UID, "T1")
        assert ok is True
        assert ledger.spent(UID, time.time()) == 150.0

    def test_the_executor_handed_in_is_the_one_read(self, wired, tmp_path):
        """The handler resolves the user's executor once and hands it over;
        the engine's resolver is not asked again."""
        store, ledger = wired
        _bind(store, per_trade=600)
        handed = types.SimpleNamespace(_compute_sized_leverage=lambda symbol, idea=None: 4)
        calls = []

        def _resolve(tg_id, venue=None):
            calls.append(tg_id)
            return types.SimpleNamespace(_compute_sized_leverage=lambda s, i=None: 9)

        eng = _engine(None)
        eng._executor_for = _resolve
        ok, _, _ = ug._authorize_web_live_trade({}, eng, UID, "T1", executor=handed)
        assert ok is True and calls == []
        assert ledger.spent(UID, time.time()) == 200.0


class TestALeverageNobodyReadDenies:

    def test_a_resolver_that_raises_denies_by_name_and_records_nothing(self, wired):
        store, ledger = wired
        _bind(store, per_trade=10_000)

        def _boom(tg_id, venue=None):
            raise RuntimeError("apiKey=SECRETVALUE")

        eng = _engine(None)
        eng._executor_for = _boom
        ok, reasons, _ = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert ok is False
        assert reasons == ["the account this order would run on could not be resolved (RuntimeError)"]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_a_resolver_that_answers_the_operators_account_denies(self, wired, tmp_path):
        """With no executor handed, the resolution is `_own_account_executor`'s,
        so the operator's executor -- what `_executor_for` answers for a user
        with no usable keys -- is refused by identity, never read for its
        leverage and never recorded against."""
        store, ledger = wired
        _bind(store, per_trade=10_000)
        eng = _engine(None)
        eng.live_executor = _real_executor(tmp_path)
        eng._executor_for = lambda tg_id, venue=None: eng.live_executor
        ok, reasons, recorded = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert ok is False and recorded is False
        assert reasons == ["this order would run on the operator's account, not yours"]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_no_executor_denies(self, wired):
        store, ledger = wired
        _bind(store, per_trade=10_000)
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(None), UID, "T1")
        assert ok is False
        assert reasons == ["no account of your own could be resolved for this order"]
        assert ledger.spent(UID, time.time()) == 0.0

    @pytest.mark.parametrize("answer", [0, "junk"], ids=["zero", "junk"])
    def test_a_leverage_that_is_not_one_denies(self, wired, answer):
        store, ledger = wired
        _bind(store, per_trade=10_000)
        stub = types.SimpleNamespace(_compute_sized_leverage=lambda symbol, idea=None: answer)
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(stub), UID, "T1")
        assert ok is False
        assert reasons and reasons[0].startswith("the leverage this order would run at could not be read")
        assert "SECRETVALUE" not in " ".join(reasons)
        assert ledger.spent(UID, time.time()) == 0.0

    def test_a_reading_that_raises_names_the_class_only(self, wired):
        store, ledger = wired
        _bind(store, per_trade=10_000)

        def _raise(symbol, idea=None):
            raise ValueError("apiKey=SECRETVALUE")

        stub = types.SimpleNamespace(_compute_sized_leverage=_raise)
        ok, reasons, _ = ug._authorize_web_live_trade({}, _engine(stub), UID, "T1")
        assert ok is False
        assert reasons == ["the leverage this order would run at could not be read (ValueError)"]
        assert ledger.spent(UID, time.time()) == 0.0

    def test_an_auto_sized_order_asks_no_leverage(self, wired):
        """No margin means no notional whatever the leverage; the envelope's
        own 'notional is unknown' answer stands, and nothing reads a leverage
        for it."""
        store, ledger = wired
        _bind(store, per_trade=300)
        asked = []
        stub = types.SimpleNamespace(
            _compute_sized_leverage=lambda symbol, idea=None: asked.append(1) or 5)
        eng = _engine(stub, margin=None)
        eng._manual_margin_override = {}
        ok, reasons, _ = ug._authorize_web_live_trade({}, eng, UID, "T1")
        assert ok is False and any("notional is unknown" in r for r in reasons)
        assert asked == []


# ── the refusal and the reading share one resolution ─────────────────────

def test_the_own_account_refusal_is_the_resolvers_sentence():
    op = object()
    own = object()
    eng = types.SimpleNamespace(live_executor=op, _executor_for=lambda t, venue=None: own)
    assert ug._own_account_executor(eng, UID) == (own, None)
    assert ug._operator_account_refusal(eng, UID) is None
    eng._executor_for = lambda t, venue=None: op
    ex, why = ug._own_account_executor(eng, UID)
    assert ex is None and why == "this order would run on the operator's account, not yours"
    assert ug._operator_account_refusal(eng, UID) == why


def test_the_handler_hands_the_executor_it_resolved_to_the_authorization():
    """A scan, stated as one: the handler is a 400-line aiohttp coroutine and
    the claim is a CALL SHAPE -- the executor the own-account check resolved
    is the `executor=` the authorization is handed, so the leverage is read
    off the account the order runs on, resolved once."""
    import ast
    import inspect

    src = inspect.getsource(ug.handle_trade_confirm)
    tree = ast.parse(inspect.cleandoc(src) if not src.startswith("async") else src)
    resolved = None
    handed = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if getattr(fn, "id", None) == "_own_account_executor":
                resolved = node.targets[0].elts[0].id
            if getattr(fn, "id", None) == "_authorize_web_live_trade":
                for kw in node.value.keywords:
                    if kw.arg == "executor":
                        handed = kw.value.id
    assert resolved is not None and handed == resolved, (resolved, handed)
    assert "_operator_account_refusal(" not in src, "one resolution, not two"


def test_the_executors_reading_is_the_one_every_order_is_placed_at():
    """`_compute_sized_leverage` is the leverage every order's notional is
    sized at (`_order_leverages`, the one reading `execute` takes); the
    envelope asks that name and no other -- not the venue's leverage, which
    would double a 5x order's notional at a 10x venue."""
    import inspect

    from bot.guardian import order_authority
    from tests.source_scan import code_only
    # The reading lives in the leaf both doors ask; the web door's wrapper
    # resolves the account and reads nothing of its own.
    src = code_only(inspect.getsource(order_authority.placement_leverage))
    assert "_compute_sized_leverage(" in src and "_compute_target_leverage" not in src
    door = code_only(inspect.getsource(ug._placement_leverage))
    assert "placement_leverage(ex, idea)" in door and "_compute_sized_leverage" not in door
    assert "default_leverage" not in code_only(inspect.getsource(ug._authorize_web_live_trade))
    assert le.LiveExecutor._compute_sized_leverage is LiveExecutor._compute_sized_leverage
