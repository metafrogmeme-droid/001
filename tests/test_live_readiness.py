"""The preconditions for a real order, and the card that reports them.

`/golive CONFIRM` used to arm live trading and verify nothing. It set
``RUNTIME.live_mode``, granted ``Permission.LIVE_TRADE``, and replied:

    🟢 LIVE TRADING ENABLED
    Real orders will execute on Bitget (USDT-M futures).

On a default install that sentence is false three times over, and each cause is
independently sufficient:

  * ``SIMULATION_MODE`` defaults true, and the engine's veto is
    ``bool(CONFIG.simulation_mode)`` — documented as holding "regardless of any
    runtime flag (e.g. RUNTIME.live_mode)". Every order comes back "Trade
    REJECTED: SIMULATION_MODE=true".
  * ``CONFIG.is_live()`` returns False on an empty ``TELEGRAM_CHAT_ID`` and
    reports it to a log file, under a banner the operator just read as green.
  * No credential is checked. The boot preflight returns early in simulation
    mode, so on a sim-booted bot no key has ever been sent to the venue.

The existing test `test_golive_confirm_enables_live_mode` passed against
exactly that config, which is what pinning the absence of a check looks like.

`assess()` is pure and takes every input explicitly, so the decision table can
be driven through every combination without a CONFIG (which is frozen) or an
engine (which opens exchange connections).
"""
from __future__ import annotations

from bot.core.live_readiness import assess, render

READY = dict(
    simulation_mode=False,
    chat_id="6307156912",
    api_key="k",
    api_secret="s",
    passphrase="p",
    auth_probed=True,
    auth_healthy=True,
)


def _codes(report, key="blockers"):
    return {i["code"] for i in report[key]}


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------

def test_a_fully_configured_bot_can_execute():
    r = assess(**READY)
    assert r["can_execute"] is True
    assert r["blockers"] == []


def test_the_default_install_is_blocked_three_ways():
    """Every default is a blocker, and each is named separately.

    Naming only the first would send an operator to fix one thing and hit the
    next — three round trips to learn what one card can say at once.
    """
    r = assess(simulation_mode=True, chat_id="", api_key="", api_secret="",
               passphrase="", auth_probed=False, auth_healthy=True)
    assert r["can_execute"] is False
    assert _codes(r) == {"simulation_mode", "no_chat_allowlist", "no_credentials"}


def test_simulation_mode_alone_blocks():
    """The hard veto is independent of every other flag, so it blocks alone."""
    r = assess(**{**READY, "simulation_mode": True})
    assert r["can_execute"] is False
    assert _codes(r) == {"simulation_mode"}


def test_an_empty_chat_allowlist_alone_blocks():
    """CONFIG.is_live() refuses without it and says so only in the log."""
    r = assess(**{**READY, "chat_id": "   "})
    assert r["can_execute"] is False
    assert _codes(r) == {"no_chat_allowlist"}


def test_a_missing_passphrase_alone_blocks():
    """Bitget needs all three. The startup check reads only key and secret, so
    a passphrase lost to a wiped .env — or to a vault that could not decrypt it
    — reaches the venue as an auth failure rather than as a missing setting."""
    r = assess(**{**READY, "passphrase": ""})
    assert r["can_execute"] is False
    assert _codes(r) == {"no_credentials"}
    assert "BITGET_PASSPHRASE" in r["blockers"][0]["detail"]


def test_each_missing_credential_is_named():
    r = assess(**{**READY, "api_key": "", "api_secret": ""})
    detail = r["blockers"][0]["detail"]
    assert "BITGET_API_KEY" in detail and "BITGET_API_SECRET" in detail
    assert "BITGET_PASSPHRASE" not in detail   # that one IS set


def test_every_blocker_carries_a_fix():
    """A blocker an operator cannot act on is a complaint, not a diagnosis."""
    r = assess(simulation_mode=True, chat_id="", api_key="", api_secret="",
               passphrase="", auth_probed=False, auth_healthy=False)
    for b in r["blockers"]:
        assert b["fix"].strip(), f"{b['code']} has no fix"


# ---------------------------------------------------------------------------
# The third outcome
# ---------------------------------------------------------------------------

def test_never_probed_is_unverified_not_healthy_and_not_a_blocker():
    """`live_auth_healthy()` answers True for an account nobody has probed.

    Right for a detector — "fail-open on detection, fail-closed only on a
    confirmed failure" — and wrong as a readiness answer. Untested credentials
    must not read as working credentials, and must not read as broken ones
    either.
    """
    r = assess(**{**READY, "auth_probed": False, "auth_healthy": True})
    assert r["can_execute"] is True             # not a blocker
    assert _codes(r, "unverified") == {"auth_never_probed"}   # and not silence


def test_a_confirmed_auth_failure_is_a_blocker_not_an_unknown():
    r = assess(**{**READY, "auth_probed": True, "auth_healthy": False})
    assert r["can_execute"] is False
    assert _codes(r) == {"auth_failing"}
    assert r["unverified"] == []


def test_a_probed_healthy_account_is_neither():
    r = assess(**READY)
    assert r["unverified"] == []
    assert r["blockers"] == []


def test_unverified_never_clears_a_blocker():
    """The two lists are independent; an unknown must not offset a refusal."""
    r = assess(**{**READY, "simulation_mode": True, "auth_probed": False})
    assert r["can_execute"] is False
    assert "simulation_mode" in _codes(r)
    assert "auth_never_probed" in _codes(r, "unverified")


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def test_the_card_never_promises_execution_while_blocked():
    r = assess(simulation_mode=True, chat_id="", api_key="", api_secret="",
               passphrase="", auth_probed=False, auth_healthy=True)
    out = render(r)
    assert "NOT ARMED" in out
    assert "Real orders will execute" not in out
    assert "🟢" not in out
    # and it says what to do about each one
    assert "SIMULATION_MODE=false" in out
    assert "TELEGRAM_CHAT_ID" in out


def test_an_armed_card_does_not_overclaim():
    """"No gate refuses" is not "your trade will fill", and the card says so."""
    out = render(assess(**READY))
    assert "ARMED" in out
    assert "not a promise" in out.lower()


def test_the_card_shows_unverified_separately_from_armed():
    out = render(assess(**{**READY, "auth_probed": False}))
    assert "ARMED" in out
    assert "Unverified" in out
    assert "UNTESTED" in out


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

def test_an_unreadable_config_resolves_to_the_blocking_side():
    """A readiness check is consulted precisely when something is wrong.

    `simulation_mode` unreadable must mean "assume the veto is on": the unsafe
    answer here is the permissive one, so an unknown resolves to blocking.
    """
    from bot.core.live_readiness import from_engine

    class Opaque:
        def __getattr__(self, name):
            raise RuntimeError("config unreadable")

    r = from_engine(engine=None, config=Opaque())
    assert r["can_execute"] is False
    assert "simulation_mode" in _codes(r)


def test_the_adapter_survives_an_engine_that_raises():
    """A readiness card that raises tells the operator nothing at all."""
    from bot.core.live_readiness import from_engine

    class Hostile:
        def live_auth_probed(self):
            raise RuntimeError("boom")

        def live_auth_healthy(self):
            raise RuntimeError("boom")

    r = from_engine(engine=Hostile(), config=type("C", (), {})())
    assert isinstance(r["can_execute"], bool)
    # an engine that cannot answer has not probed
    assert "auth_never_probed" in _codes(r, "unverified")


def test_live_auth_probed_distinguishes_never_seen_from_healthy():
    """The engine accessor the whole fix rests on."""
    from bot.core.engine import RuneClawEngine

    eng = RuneClawEngine()
    assert eng.live_auth_probed() is False        # nothing has asked the venue
    assert eng.live_auth_healthy() is True        # yet this still answers True
    eng.set_live_auth_status(True)
    assert eng.live_auth_probed() is True


# ---------------------------------------------------------------------------
# The mode label — the same claim, on three more surfaces
# ---------------------------------------------------------------------------

class _Cfg:
    def __init__(self, live, sim):
        self._live, self.simulation_mode = live, sim

    def is_live(self):
        return self._live


def test_sim_off_but_not_armed_is_idle_not_live():
    """The defect on three status cards, one of which feeds the LLM.

    They read `"LIVE" if not CONFIG.simulation_mode else "PAPER"`. But
    `is_live()` also needs `live_trading_enabled` (or a runtime arm) AND a
    non-empty chat allow-list — so SIMULATION_MODE=false with live not yet
    armed placed no orders at all while all three announced LIVE.
    """
    from bot.core.live_readiness import mode_label

    assert mode_label(_Cfg(live=False, sim=False)) == "IDLE"


def test_the_two_settled_states_still_read_normally():
    from bot.core.live_readiness import mode_label

    assert mode_label(_Cfg(live=True, sim=False)) == "LIVE"
    assert mode_label(_Cfg(live=False, sim=True)) == "PAPER"


def test_an_unreadable_config_never_reads_as_live():
    """Claiming LIVE off a failed read is the expensive direction."""
    from bot.core.live_readiness import mode_label

    class Broken:
        def is_live(self):
            raise RuntimeError("nope")

    assert mode_label(Broken()) == "UNKNOWN"

    class HalfBroken:
        def is_live(self):
            return False

        @property
        def simulation_mode(self):
            raise RuntimeError("nope")

    assert mode_label(HalfBroken()) == "UNKNOWN"


#: Two-valued mode derivations that are NOT a status claim, each with why.
#: Two-way, like `known_failures.txt`: an entry that stops matching any
#: expression must be deleted in the commit that made it stale.
MODE_SHAPE_ALLOWED = {
    "bot/core/live_readiness.py": "mode_label's own module — the reading, and "
                                  "the fallback inside it IS three-valued",
}


def _two_valued_mode_sites(root):
    r"""Every `<str> if <...>simulation_mode else <str>` in the tree.

    AST, not three exact literals. The previous guard forbade
    `'PAPER' if CONFIG . simulation_mode else 'LIVE'` and two siblings, and
    scanned ONE file — `bot/skills/telegram_handler.py`, which the sites left
    during the handler split. Wrong file AND wrong literal: the live spelling
    carries a warning emoji (`"⚠️ LIVE"`), so even in the right file
    none of the three would have matched. A checker with a blind spot
    manufactures exactly the acquittal it exists to prevent.

    The shape is the thing: a conditional on the simulation flag ALONE whose
    two branches are both strings. `is_live()` needs the arm flag and the chat
    allow-list too, so that expression cannot answer IDLE or UNKNOWN.
    """
    import ast

    hits = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "site/", "app/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # not ours to judge
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.IfExp):
                continue
            test = node.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                test = test.operand
            if not (isinstance(test, ast.Attribute)
                    and test.attr == "simulation_mode"):
                continue
            if not (isinstance(node.body, ast.Constant)
                    and isinstance(node.orelse, ast.Constant)
                    and isinstance(node.body.value, str)
                    and isinstance(node.orelse.value, str)):
                continue
            hits.append((rel, node.lineno, ast.unparse(node)))
    return hits


def test_no_status_surface_still_derives_the_mode_from_simulation_mode_alone():
    """Write the assertion, then re-run the search — over the whole tree.

    Three sites had this shape when the rule was written. By the time anyone
    looked again there were five, in three files the guard did not read, and
    the guard was green throughout: `CheckRiskSkill._status` (a card reachable
    from web chat and fed to the LLM as engine state), `ProScanSkill`,
    `PlaybookSkill`, `/version`, and the BOOT BANNER — which printed
    `Mode: LIVE` directly above its own `Live Trading: DISABLED`.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    bad = [(f, ln, src) for f, ln, src in _two_valued_mode_sites(root)
           if f not in MODE_SHAPE_ALLOWED]
    assert bad == [], (
        "these derive the trading mode from simulation_mode alone:\n  "
        + "\n  ".join(f"{f}:{ln}  {src}" for f, ln, src in bad)
        + "\n\nis_live() needs the arm flag and the chat allow-list too, so this "
          "expression cannot answer IDLE (sim off, live never armed) or UNKNOWN "
          "(the config could not be read). Use live_readiness.mode_label(), and "
          "mode_badge() if you want the badge.")


def test_the_mode_shape_allow_list_has_no_stale_entry():
    """A stale entry is a rule that quietly stopped applying."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    seen = {f for f, _ln, _src in _two_valued_mode_sites(root)}
    stale = [f for f in MODE_SHAPE_ALLOWED if f not in seen]
    assert stale == [], stale


def test_the_shape_scan_would_catch_the_spelling_the_old_guard_missed():
    """Guards the guard, on a planted tree where the rule is the only thing in
    play — including the emoji spelling three live sites used and the three
    forbidden literals did not cover."""
    import tempfile
    from pathlib import Path

    planted = (
        'CONFIG = None\n'
        'def a():\n'
        '    return "PAPER" if CONFIG.simulation_mode else "⚠️ LIVE"\n'
        'def b():\n'
        '    return "LIVE" if not CONFIG.simulation_mode else "PAPER"\n'
        'def ok():\n'
        '    return "LIVE" if can_live and not CONFIG.simulation_mode else "PAPER"\n'
        'def also_ok():\n'
        '    return mode_label()\n'
    )
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "bot").mkdir()
        (root / "bot" / "card.py").write_text(planted, encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "t.py").write_text(
            'x = "PAPER" if CONFIG.simulation_mode else "LIVE"\n', encoding="utf-8")
        hits = _two_valued_mode_sites(root)
    assert [(f, ln) for f, ln, _s in hits] == [("bot/card.py", 3),
                                               ("bot/card.py", 5)], hits


def test_one_badge_map_serves_every_card():
    """`mode_badge` is the rendering of `mode_label`, and it has to have all
    four values: a two-valued badge is how an IDLE real account printed LIVE."""
    from bot.formatters.rich_cards import mode_badge

    seen = {m: mode_badge(m) for m in ("LIVE", "PAPER", "IDLE", "UNKNOWN")}
    assert len(set(seen.values())) == 4, seen
    assert "LIVE" in seen["LIVE"] and "PAPER" in seen["PAPER"]
    assert "not armed" in seen["IDLE"]
    # An unknown word is not quietly rendered as one of the other three.
    assert mode_badge("something-new") == seen["UNKNOWN"]


def test_the_status_badge_does_not_paint_an_idle_real_account_as_paper():
    """Colour is a claim, and 🟡 PAPER over a real-money account is the
    reassuring direction of a wrong one.

    `render_status_card` read `"LIVE" if mode == "LIVE" else PAPER`, so every
    value that was not exactly "LIVE" — including IDLE and UNKNOWN — printed
    the yellow paper badge.
    """
    from bot.formatters.rich_cards import render_status_card

    common = dict(active=True, equity=1000.0, open_positions=0,
                  daily_pnl=0.0, drawdown=0.0, max_drawdown=10.0,
                  market_bias="NEUTRAL")
    live = render_status_card(mode="LIVE", **common)
    paper = render_status_card(mode="PAPER", **common)
    idle = render_status_card(mode="IDLE", **common)
    unknown = render_status_card(mode="UNKNOWN", **common)

    assert "IDLE" in idle and "🟡" not in idle
    assert "UNKNOWN" in unknown and "🟡" not in unknown
    # the two settled states are untouched
    assert "🔴" in live
    assert "🟡" in paper


def test_the_status_badge_renderer_is_not_two_valued_again():
    """The fifth site. A grep for three variable spellings found four; this
    one lived in the formatter, one layer below all of them."""
    import io
    import tokenize
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "bot" / "formatters" / "rich_cards.py"
    out, prev_type = [], tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src.read_text()).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, tokenize.DEDENT):
            continue
        out.append(tok.string)
        prev_type = tok.type if tok.type != tokenize.NL else prev_type
    code = " ".join(out)

    assert 'if mode == "LIVE" else' not in code, (
        "the status badge is two-valued again — IDLE and UNKNOWN would both "
        "print the PAPER badge over a real-money account"
    )
