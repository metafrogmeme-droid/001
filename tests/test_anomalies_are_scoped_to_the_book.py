"""An anomaly on an asset you do not hold is market news, not an alert.

THE REPORT, 2026-09-13, from the live channel: four messages in fifteen
minutes — a "+1 more severe anomaly" at 09:50, an ANOMALY DIGEST naming
eleven symbols at 10:02, a severe BNB/USDT spread card at 10:04 and another
"+1 more" at 10:05 — across WLFI, LAB, PENDLE, PUMP, RAVE, UNI, ATOM, BCH,
BNB and XPL. The operator held none of them.

EVERY CONTROL THIS FILE'S SUBJECT ALREADY HAD BOUNDS VOLUME AND NONE BOUNDS
RELEVANCE. `_SEVERE_CARDS_PER_TICK` caps how wide one burst is,
`_SEVERE_CARDS_PER_HOUR` how many bursts an hour holds,
`BLACK_SWAN_SEVERE_REPEAT` how often one condition repeats, and the digest
batches the mild ones. All four are about HOW MANY, so turning any of them
down only trades a real warning for a quieter flood of irrelevant ones.
`active_alerts` is the whole scanned universe and nothing between it and the
operator had ever asked whether the symbol was one they had money in.

Two dials, both the operator's: `scope` (held, default) and `interval`
(one hour, default). Driven here against the reported burst.

UNREADABLE IS NOT EMPTY, and that rule decides the design rather than
decorating it. A book that cannot be read must not become "you hold nothing"
and silence the channel whose job is to interrupt you — it keeps every alert
and says why. A book that IS empty is a real reading and does suppress.
"""
from __future__ import annotations

import types

from bot.core.anomaly_scope import (
    DEFAULT_BUDGET_PER_HOUR,
    DEFAULT_INTERVAL_SEC,
    MAX_INTERVAL_SEC,
    MIN_INTERVAL_SEC,
    SCOPE_ALL,
    SCOPE_HELD,
    held_symbols,
    is_due,
    normalise_interval,
    normalise_scope,
    parse_setting,
    scoped,
    settings_card,
)
from bot.core.proactive_monitor import ProactiveMonitor


def _an(symbol, kind, severity, *, action="MONITOR"):
    return types.SimpleNamespace(
        symbol=symbol, anomaly_type=kind, severity=severity,
        description=f"{symbol} {kind.lower()} fired",
        recommended_action=action)


def _engine(rows, positions=()):
    return types.SimpleNamespace(
        black_swan=types.SimpleNamespace(active_alerts=list(rows)),
        live_executor=types.SimpleNamespace(
            open_positions=[types.SimpleNamespace(symbol=s) for s in positions]))


#: The burst as reported, trimmed to its shape: severe singles plus a wide
#: mild spread, none of it on a symbol the operator held.
REPORTED = [
    _an("BNB/USDT", "SPREAD_WIDENING", 0.81, action="HALT_NEW_TRADES"),
    _an("WLFI/USDT", "CORRELATION_BREAKDOWN", 0.79),
    _an("WLFI/USDT", "PRICE_ACCELERATION", 0.66),
    _an("LAB/USDT", "SPREAD_WIDENING", 0.62),
    _an("UNI/USDT", "VOLUME_COLLAPSE", 0.60),
    _an("PENDLE/USDT", "PRICE_ACCELERATION", 0.44),
    _an("PUMP/USDT", "PRICE_ACCELERATION", 0.41),
    _an("RAVE/USDT", "CORRELATION_BREAKDOWN", 0.38),
    _an("ATOM/USDT", "SPREAD_WIDENING", 0.33),
    _an("BCH/USDT", "SPREAD_WIDENING", 0.31),
    _an("XPL/USDT", "VOLUME_COLLAPSE", 0.30),
]


def _monitor(rows, positions=(), *, scope=SCOPE_HELD, interval=DEFAULT_INTERVAL_SEC):
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.engine = _engine(rows, positions)
    m.set_anomaly_prefs_fn(lambda: {"scope": scope, "interval": interval})
    return m


# ── 1. the reported burst, end to end ───────────────────────────────────────

def test_the_reported_burst_sends_nothing_when_you_hold_none_of_it():
    """The whole complaint, in one assertion."""
    m = _monitor(REPORTED, positions=["ETH/USDT"])
    assert m._check_black_swan() == []


def test_the_same_burst_still_pages_for_a_symbol_you_DO_hold():
    """RED HERRING for the fix: quiet must not be the only outcome. The
    severe BNB card is exactly what an operator holding BNB needs."""
    m = _monitor(REPORTED, positions=["BNB/USDT"])
    out = m._check_black_swan()
    assert out, "a severe anomaly on a held symbol was suppressed"
    joined = "\n".join(a.body for a in out)
    assert "BNB/USDT" in joined
    for never in ("WLFI", "PENDLE", "RAVE", "XPL"):
        assert never not in joined, never


def test_scope_all_restores_the_old_breadth():
    m = _monitor(REPORTED, positions=["ETH/USDT"], scope=SCOPE_ALL)
    assert m._check_black_swan(), "scope=all must still see the market"


def test_a_second_pass_inside_the_interval_says_nothing():
    m = _monitor(REPORTED, positions=["BNB/USDT"])
    assert m._check_black_swan(), "the first pass should speak"
    assert m._check_black_swan() == [], "the second inside the hour should not"


def test_the_interval_delays_and_does_not_drop():
    """`active_alerts` is a live set, so a condition still standing at the
    next due window is still reported. The dial bounds the channel; it does
    not lose a warning.

    BOTH CLOCKS ARE REWOUND, and the first draft rewound only one. The
    interval is not the sole suppression on this path: `_bs_is_news` holds an
    unchanged severe condition for `BLACK_SWAN_SEVERE_REPEAT` against its own
    `_bs_last` stamp, so moving `_bs_last_message_at` alone is not "an hour
    passed" — it is "an hour passed for one filter and no time at all for the
    other", which is not a state the world can be in. The test said the
    condition had been dropped when what had actually happened is that the
    simulation was incoherent.
    """
    m = _monitor(REPORTED, positions=["BNB/USDT"])
    assert m._check_black_swan()
    elapsed = DEFAULT_INTERVAL_SEC + 1
    m._bs_last_message_at -= elapsed
    m._bs_last = {k: (t - elapsed, n) for k, (t, n) in m._bs_last.items()}
    assert m._check_black_swan(), "the condition was dropped rather than delayed"


def test_the_interval_blocks_a_message_the_repeat_filter_would_ALLOW():
    """Isolates the interval from the filter standing in front of it.

    `test_a_second_pass_inside_the_interval_says_nothing` passes with the
    interval gate DELETED, because `_bs_is_news` already suppresses an
    unchanged condition — so it proves the channel is quiet and not which
    control made it quiet. A NEW condition on the second pass is news by that
    filter's own rule, so only the interval can stop it.
    """
    m = _monitor(REPORTED, positions=["BNB/USDT", "ETH/USDT"])
    assert m._check_black_swan(), "the first pass should speak"
    m.engine = _engine(
        REPORTED + [_an("ETH/USDT", "VOLUME_COLLAPSE", 0.95,
                        action="HALT_NEW_TRADES")],
        ["BNB/USDT", "ETH/USDT"])
    assert m._check_black_swan() == [], (
        "a brand-new severe condition inside the hour was sent anyway")


def test_a_pass_that_SENDS_nothing_does_not_start_the_hour():
    """The interval is charged against what is sent, not what is built — the
    same correction the card budget records having needed. A pass whose every
    alert the repeat filter suppressed has not spoken to the operator, and
    starting their hour of quiet from it would be the cure doing the
    disease's job.

    Driven by moving ONE clock: the interval has expired, the repeat filter
    has not. The pass builds and sends nothing, so the next pass — once the
    filter also expires — must still be allowed to speak.
    """
    m = _monitor(REPORTED, positions=["BNB/USDT"])
    assert m._check_black_swan()
    first_stamp = m._bs_last_message_at
    m._bs_last_message_at -= (DEFAULT_INTERVAL_SEC + 1)
    assert m._check_black_swan() == [], "the repeat filter should hold it"
    assert m._bs_last_message_at < first_stamp, (
        "a pass that sent nothing re-armed the hour of quiet")


def test_the_message_says_what_the_scope_removed():
    """A channel filtered to your own book must say so, or a quiet hour is
    indistinguishable from a broken detector."""
    m = _monitor(REPORTED, positions=["BNB/USDT"])
    joined = "\n".join(a.body for a in m._check_black_swan())
    assert "you do not hold" in joined


# ── 2. unreadable is not empty ──────────────────────────────────────────────

def test_an_unreadable_book_shows_everything_rather_than_nothing():
    """The direction that matters. Silence manufactured from a failed read,
    on the surface whose job is to interrupt you, is the most expensive
    version of this mistake."""
    m = _monitor(REPORTED)
    m.engine.live_executor = None
    out = m._check_black_swan()
    assert out, "an unreadable book silenced the channel"
    assert "unreadable" in "\n".join(a.body for a in out)


def test_held_symbols_answers_None_not_an_empty_set():
    assert held_symbols(types.SimpleNamespace()) is None
    assert held_symbols(types.SimpleNamespace(live_executor=None)) is None
    assert held_symbols(_engine([], [])) == set()
    assert held_symbols(_engine([], ["BTC/USDT"])) == {"BTC/USDT"}


def test_a_RAISING_position_read_is_unreadable_not_empty():
    """The absent-executor case and the RAISING one are different doors to
    the same wrong answer, and only the first was driven: the mutation that
    made the `except` return an empty set survived a green suite. An exchange
    that times out mid-read is the ordinary way this happens.
    """
    class _Angry:
        @property
        def open_positions(self):
            raise RuntimeError("venue timeout")

    assert held_symbols(types.SimpleNamespace(live_executor=_Angry())) is None
    # And `open_positions = None` — a book that exists and holds no list.
    assert held_symbols(types.SimpleNamespace(
        live_executor=types.SimpleNamespace(open_positions=None))) is None


def test_scoped_keeps_everything_when_the_book_is_None():
    kept, dropped, note = scoped([_an("X/USDT", "K", 0.5)], None)
    assert len(kept) == 1 and dropped == [] and note


def test_a_genuinely_flat_book_suppresses_and_says_why():
    kept, dropped, note = scoped([_an("X/USDT", "K", 0.5)], set())
    assert kept == [] and len(dropped) == 1
    assert "no open positions" in note


# ── 3. the dials ────────────────────────────────────────────────────────────

def test_the_defaults_are_the_quiet_ones():
    assert DEFAULT_INTERVAL_SEC == 3600
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    # Three dials now: the channel budget rides beside scope and interval,
    # and its default is the quiet one too.
    assert m._anomaly_dials() == {"scope": SCOPE_HELD,
                                  "interval": DEFAULT_INTERVAL_SEC,
                                  "budget": DEFAULT_BUDGET_PER_HOUR}


def test_a_failing_prefs_read_falls_back_to_the_NARROW_default():
    """A fault must not be able to widen the scope — falling back to the
    default can only ever send less."""
    def _boom():
        raise RuntimeError("store down")

    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m.set_anomaly_prefs_fn(_boom)
    assert m._anomaly_dials()["scope"] == SCOPE_HELD


def test_junk_values_are_refused_rather_than_defaulted():
    assert normalise_interval("abc") is None
    assert normalise_interval(MIN_INTERVAL_SEC - 1) is None
    assert normalise_interval(MAX_INTERVAL_SEC + 1) is None
    assert normalise_interval("3600") == 3600
    assert normalise_scope("HELD") == SCOPE_HELD
    assert normalise_scope("everything") is None


def test_a_never_sent_channel_is_due_immediately():
    """The first alert after a restart is the one most worth having."""
    assert is_due(None, 1_000_000.0, 3600) is True
    assert is_due(1_000_000.0, 1_000_100.0, 3600) is False
    assert is_due(1_000_000.0, 1_004_000.0, 3600) is True
    assert is_due("junk", 1_000_000.0, 3600) is True


# ── 4. the setting surface ──────────────────────────────────────────────────

def test_the_parser_reads_what_an_operator_types():
    # Four slots now: the channel budget rides beside scope and interval.
    assert parse_setting([]) == (None, None, None, "")
    assert parse_setting(["all"])[0] == SCOPE_ALL
    assert parse_setting(["held"])[0] == SCOPE_HELD
    assert parse_setting(["every", "2h"])[1] == 7200
    assert parse_setting(["every", "30m"])[1] == 1800
    assert parse_setting(["budget", "20"])[2] == 20


def test_an_unreadable_setting_is_a_refusal_not_a_default():
    """The whole slice is about an operator getting something they did not
    ask for; quietly substituting an hour here would be that again."""
    for args in (["banana"], ["every", "3s"], ["every", "nope"], ["every"],
                 ["budget"], ["budget", "0"], ["budget", "lots"],
                 ["budget", "9999"]):
        scope, interval, budget, err = parse_setting(args)
        assert scope is None and interval is None and budget is None and err, args


def test_the_card_says_what_quiet_would_mean():
    held = settings_card({"scope": SCOPE_HELD, "interval": 3600},
                         held={"BNB/USDT"})
    assert "BNB/USDT" in held and "1 hour" in held
    flat = settings_card({"scope": SCOPE_HELD, "interval": 3600}, held=set())
    assert "none" in flat and "flat" in flat
    blind = settings_card({"scope": SCOPE_HELD, "interval": 3600}, held=None)
    assert "could not be read" in blind
    every = settings_card({"scope": SCOPE_ALL, "interval": 7200}, held=None)
    assert "every scanned symbol" in every and "2 hours" in every
    # The card names the commands that change it, and they must be real.
    assert "/alerts all" in held and "/alerts held" in held


# ── 5. the store ────────────────────────────────────────────────────────────

def test_the_store_defaults_for_a_user_it_has_never_seen(tmp_path):
    """The alert path runs on a timer; an unknown row must not fail it open
    into the flood or closed into silence."""
    from bot.utils.user_store import UserStore

    st = UserStore(path=tmp_path / "u.json")
    assert st.anomaly_prefs("nobody") == {"scope": SCOPE_HELD,
                                          "interval": DEFAULT_INTERVAL_SEC,
                                          "budget": DEFAULT_BUDGET_PER_HOUR}


def test_a_corrupt_stored_row_cannot_widen_the_scope(tmp_path):
    from bot.utils.user_store import UserStore

    st = UserStore(path=tmp_path / "u.json")
    st.register("7", "x")
    st.authorize("7", "trader")
    st._users["7"]["anomaly_prefs"] = {"scope": "EVERYTHING", "interval": "soon",
                                       "budget": "lots"}
    got = st.anomaly_prefs("7")
    assert got == {"scope": SCOPE_HELD, "interval": DEFAULT_INTERVAL_SEC,
                   "budget": DEFAULT_BUDGET_PER_HOUR}


def test_each_dial_is_set_without_clearing_the_other(tmp_path):
    from bot.utils.user_store import UserStore

    st = UserStore(path=tmp_path / "u.json")
    st.register("7", "x")
    st.authorize("7", "trader")
    assert st.set_anomaly_prefs("7", scope=SCOPE_ALL)
    assert st.set_anomaly_prefs("7", interval=7200)
    assert st.set_anomaly_prefs("7", budget=30)
    assert st.anomaly_prefs("7") == {"scope": SCOPE_ALL, "interval": 7200,
                                     "budget": 30}
    assert st.set_anomaly_prefs("nobody", scope=SCOPE_ALL) is False


def test_the_setting_survives_a_restart(tmp_path):
    from bot.utils.user_store import UserStore

    path = tmp_path / "u.json"
    st = UserStore(path=path)
    st.register("7", "x")
    st.authorize("7", "trader")
    st.set_anomaly_prefs("7", scope=SCOPE_ALL, interval=7200, budget=30)
    assert UserStore(path=path).anomaly_prefs("7") == {"scope": SCOPE_ALL,
                                                       "interval": 7200,
                                                       "budget": 30}


# ── 6. what the SCOPE does NOT change: who receives the message ────────────

def test_the_scope_filter_does_not_narrow_the_audience():
    """The third thing asked for was "then direct message", and this slice
    deliberately does NOT deliver it. Writing down why, because the obvious
    change is one field and it is wrong.

    `audience="admin"` on these three constructors was built, driven, and
    reverted. Two things decided it.

    The premise was FALSE. The argument for narrowing was that `_dispatch`
    publishes a non-admin alert's TITLE to the public mind-stream, so eleven
    unheld symbols were reaching a public feed. They were not: the titles are
    `f"Anomaly: {kind}"`, a fixed "more severe conditions this pass", and
    `f"Anomaly digest: {len(all_syms)} symbols"` — a type, a phrase, and a
    COUNT. No symbol has ever reached that feed. A fix argued from a leak
    that does not exist is a fix with no reason.

    And there is a WRITTEN decision the other way.
    `test_alert_audience.py::test_the_alerts_a_trader_acts_on_still_reach_them`
    lists BLACK_SWAN among the alerts that must reach every watching chat,
    because each "names the reader's own risk" and "an audience gate that
    swallows them would be a worse bug than the leak it replaced". The scope
    filter does weaken that premise — these rows are now narrowed by ONE
    operator's book — but weakening a premise is not the same as being
    entitled to overturn the decision, and the honest version needs
    per-recipient scope, which does not exist yet.

    A conditional audience would also have defeated the guard rather than
    satisfied it: `_alert_audiences()` reads the constructor by AST and
    treats any non-Constant `audience` as "all", so an expression there would
    record "all" while the runtime sent "admin" — a false acquittal inside
    the one test that owns this decision.
    """
    import ast
    import inspect

    from bot.core import proactive_monitor as pm

    found = []
    for node in ast.walk(ast.parse(inspect.getsource(pm))):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Alert"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        t = kw.get("alert_type")
        if isinstance(t, ast.Constant) and t.value == "BLACK_SWAN":
            aud = kw.get("audience")
            found.append(aud.value if isinstance(aud, ast.Constant) else "all")
    assert found, "no BLACK_SWAN alert found — the drive is not reaching it"
    assert set(found) == {"all"}, (
        "BLACK_SWAN was narrowed; test_alert_audience.py owns that decision")
