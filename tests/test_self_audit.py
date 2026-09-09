"""
Nightly LLM self-audit — proposal gate, scheduling, measurement, report,
and wiring pins.

The audit is ADVISORY ONLY: a fixed flag allowlist with bounds, every
proposal measured on a frozen benchmark before the operator sees it, and
nothing ever applied automatically. These tests pin that posture.
"""

import asyncio
import inspect
import json
import time
from types import SimpleNamespace

from bot.core.self_audit import (
    ALLOWED_FLAGS,
    SelfAudit,
    _parse_metrics,
    parse_llm_json,
    validate_proposals,
)


# ── proposal validation (the safety gate) ─────────────────────────────

class TestValidateProposals:
    def test_unknown_flag_dropped(self):
        assert validate_proposals(
            [{"flag": "MAX_LEVERAGE", "value": 50, "rationale": "moon"}]) == []

    def test_out_of_bounds_dropped(self):
        assert validate_proposals(
            [{"flag": "TREND_UP_SIZE_MULT", "value": 5.0, "rationale": "x"}]) == []

    def test_wrong_type_dropped(self):
        # bool flag with a number, numeric flag with a bool
        assert validate_proposals(
            [{"flag": "EQUITY_THROTTLE_ENABLED", "value": 1, "rationale": "x"},
             {"flag": "TREND_UP_SIZE_MULT", "value": True, "rationale": "x"}]) == []

    def test_valid_proposals_normalized(self):
        out = validate_proposals(
            [{"flag": "equity_throttle_enabled", "value": True, "rationale": "r"},
             {"flag": "TREND_UP_SIZE_MULT", "value": 0.5, "rationale": "r"}],
            max_proposals=5)
        assert [p["flag"] for p in out] == ["EQUITY_THROTTLE_ENABLED",
                                            "TREND_UP_SIZE_MULT"]
        assert out[0]["value"] == "1" and out[1]["value"] == "0.5"

    def test_noop_vs_current_env_dropped(self):
        out = validate_proposals(
            [{"flag": "EQUITY_THROTTLE_ENABLED", "value": True, "rationale": "r"}],
            current_env={"EQUITY_THROTTLE_ENABLED": "1"})
        assert out == []

    def test_duplicates_and_cap(self):
        raw = [{"flag": "TREND_UP_SIZE_MULT", "value": 0.5, "rationale": "a"},
               {"flag": "TREND_UP_SIZE_MULT", "value": 0.6, "rationale": "b"},
               {"flag": "EQUITY_THROTTLE_ENABLED", "value": True, "rationale": "c"},
               {"flag": "ENTRY_TIMING_ENABLED", "value": True, "rationale": "d"}]
        out = validate_proposals(raw, max_proposals=2)
        assert len(out) == 2
        assert out[0]["value"] == "0.5"  # first wins, dup dropped

    def test_garbage_never_raises(self):
        assert validate_proposals([None, 42, {"flag": None}, {}]) == []


# ── LLM response parsing ──────────────────────────────────────────────

class TestParseLlmJson:
    def test_plain_array(self):
        assert parse_llm_json('[{"flag": "X", "value": 1}]') == [
            {"flag": "X", "value": 1}]

    def test_fenced_and_prosed(self):
        text = ('Here is my analysis:\n```json\n'
                '[{"flag": "A", "value": true, "rationale": "r"}]\n```\nDone.')
        assert parse_llm_json(text)[0]["flag"] == "A"

    def test_unparseable_is_none_not_an_empty_proposal_list(self):
        """`[]` means the model proposed nothing. These are not that.

        This assertion used to read `== []`, and every layer downstream treats
        an empty proposal list as "the model reviewed the evidence and endorsed
        the configuration" — so a truncated reply, a refusal, a rate-limit stub
        and two paragraphs of prose all rendered as an endorsement of a live
        trading config. `None` is the third state; `no_change_verdict` reports
        it as a FAILED audit.
        """
        assert parse_llm_json("I would not change anything.") is None
        assert parse_llm_json("") is None
        assert parse_llm_json(None) is None
        # A truncated array is the one that actually happens: the reply ran out
        # of tokens mid-object. It must not read as "nothing to change".
        assert parse_llm_json('[{"flag": "ENTRY_TIMING_ENABL') is None
        # A JSON object where an array was asked for is also not an answer.
        assert parse_llm_json('{"flag": "X", "value": 1}') is None
        # BRACKETED BUT NOT JSON — the case that reaches the `except` rather
        # than the no-match return. Every input above bails at the regex, so
        # the mutation turning that handler back into `return []` survived the
        # first version of this test: three spellings of the same branch and
        # none of the other one.
        assert parse_llm_json("[not json at all]") is None
        assert parse_llm_json("[{'flag': 'X'}]") is None      # single quotes
        assert parse_llm_json("[1, 2,]") is None              # trailing comma

    def test_a_real_empty_array_stays_empty(self):
        """The other side of it: an empty list is a measurement and survives."""
        assert parse_llm_json("[]") == []
        assert parse_llm_json("No changes needed.\n```json\n[]\n```") == []


# ── benchmark output parsing ──────────────────────────────────────────

def test_parse_metrics():
    out = _parse_metrics(
        "  Total Return:           +6.30%\n  Total Trades:     47\n"
        "  Max Drawdown:     0.91%  ($97.38)\n  Profit Factor:    3.81\n")
    assert out == {"return_pct": 6.30, "trades": 47.0,
                   "max_dd_pct": 0.91, "pf": 3.81}


def test_parse_metrics_empty_on_garbage():
    assert _parse_metrics("Traceback (most recent call last): ...") == {}


# ── scheduling ────────────────────────────────────────────────────────

class _cfg:
    """Temporarily override frozen CONFIG fields (project pattern)."""

    def __init__(self, **kw):
        self.kw = kw
        self.old: dict = {}

    def __enter__(self):
        from bot.config import CONFIG
        for k, v in self.kw.items():
            self.old[k] = getattr(CONFIG, k)
            object.__setattr__(CONFIG, k, v)
        return self

    def __exit__(self, *exc):
        from bot.config import CONFIG
        for k, v in self.old.items():
            object.__setattr__(CONFIG, k, v)


class TestScheduling:
    def _audit(self, tmp_path):
        return SelfAudit(state_file=str(tmp_path / "sa.json"))

    def test_due_only_in_configured_hour(self, tmp_path):
        sa = self._audit(tmp_path)
        at_4utc = 1_750_000_000 - (1_750_000_000 % 86400) + 4 * 3600 + 60
        at_9utc = at_4utc + 5 * 3600
        with _cfg(self_audit_enabled=True, self_audit_hour_utc=4):
            assert sa.due(now_ts=at_4utc) is True
            assert sa.due(now_ts=at_9utc) is False

    def test_not_due_twice_in_a_day(self, tmp_path):
        sa = self._audit(tmp_path)
        at_4utc = 1_750_000_000 - (1_750_000_000 % 86400) + 4 * 3600 + 60
        sa._save_state({"last_run_ts": at_4utc - 3600})  # ran 1h ago
        with _cfg(self_audit_enabled=True, self_audit_hour_utc=4):
            assert sa.due(now_ts=at_4utc) is False
            # ...but IS due the following night (persisted stamp respected)
            assert sa.due(now_ts=at_4utc + 86400) is True

    def test_disabled_never_due(self, tmp_path):
        sa = self._audit(tmp_path)
        with _cfg(self_audit_enabled=False):
            assert sa.due(now_ts=time.time()) is False


# ── the run (LLM + benchmarks mocked) ─────────────────────────────────

class _FakeAnalyzer:
    def __init__(self, response):
        self._response = response

    def _resolve_llm_config(self):
        return SimpleNamespace(is_configured=lambda: True)

    def _build_client_for_config(self, cfg):
        return object()  # non-None = "configured"


def _fake_engine(response="[]"):
    closed = [SimpleNamespace(symbol="BTC/USDT", direction="LONG",
                              strategy_type="swing", net_pnl=5.0,
                              close_reason="TP HIT"),
              SimpleNamespace(symbol="ETH/USDT", direction="SHORT",
                              strategy_type="scalp", net_pnl=-3.0,
                              close_reason="SL HIT")]
    return SimpleNamespace(
        analyzer=_FakeAnalyzer(response),
        live_executor=SimpleNamespace(_closed_trades=closed),
        risk=None)


class TestRun:
    def test_full_cycle_with_proposal(self, tmp_path, monkeypatch):
        # LLM proposes one allowlisted change; benchmark runner is injected.
        calls = []

        def fake_bt(dataset, env=None):
            calls.append((dataset, env))
            return ({"return_pct": 2.0, "pf": 1.5, "trades": 30}
                    if env else
                    {"return_pct": 1.0, "pf": 1.2, "trades": 30})

        async def fake_complete(client, cfg, sys_p, user_p):
            return json.dumps([{"flag": "EQUITY_THROTTLE_ENABLED",
                                "value": True, "rationale": "PF sagging"}])

        import bot.llm.provider as prov
        monkeypatch.setattr(prov, "llm_complete", fake_complete)
        monkeypatch.delenv("EQUITY_THROTTLE_ENABLED", raising=False)

        sa = SelfAudit(state_file=str(tmp_path / "sa.json"),
                       run_backtest=fake_bt)
        report = asyncio.run(sa.run(_fake_engine()))
        assert report is not None
        assert "EQUITY_THROTTLE_ENABLED=1" in report
        assert "+1.00pp vs baseline" in report
        assert "nothing auto-applied" in report
        # baseline ran once, proposal once
        assert len(calls) == 2 and calls[0][1] is None
        # queued for the proactive monitor + persisted for /audit
        assert sa.drain_pending()[0]["report"] == report
        assert SelfAudit(state_file=str(tmp_path / "sa.json")).last_report() == report

    def test_empty_proposals_skip_benchmarks(self, tmp_path, monkeypatch):
        calls = []

        def fake_bt(dataset, env=None):
            calls.append(dataset)
            return {}

        async def fake_complete(client, cfg, sys_p, user_p):
            return "[]"

        import bot.llm.provider as prov
        monkeypatch.setattr(prov, "llm_complete", fake_complete)
        sa = SelfAudit(state_file=str(tmp_path / "sa.json"),
                       run_backtest=fake_bt)
        report = asyncio.run(sa.run(_fake_engine()))
        assert "No changes proposed" in report
        assert calls == []  # zero benchmark runs when nothing to measure

    def test_no_llm_configured_skips_silently(self, tmp_path):
        sa = SelfAudit(state_file=str(tmp_path / "sa.json"))
        eng = SimpleNamespace(analyzer=None, live_executor=None, risk=None)
        assert asyncio.run(sa.run(eng)) is None
        assert sa.drain_pending() == []

    def test_run_never_raises(self, tmp_path, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("provider down")

        import bot.llm.provider as prov
        monkeypatch.setattr(prov, "llm_complete", boom)
        sa = SelfAudit(state_file=str(tmp_path / "sa.json"))
        assert asyncio.run(sa.run(_fake_engine())) is None


# ── report rendering ──────────────────────────────────────────────────

def test_unmeasured_proposal_marked_not_verified():
    report = SelfAudit.render_report(
        {"summary": {"n": 10, "win_rate": 0.4, "net_pnl": -2.0, "pf": 0.8}},
        [{"flag": "X", "value": "1", "rationale": "r", "measured": {}}],
        {"return_pct": 1.0, "pf": 1.2}, "alts_1h")
    assert "NOT VERIFIED" in report


def test_costly_shadow_gate_surfaces_in_report():
    """An ESTABLISHED gate is still named as the costliest one.

    `verdict`/`lower_r` come off `gate_report()` in production; the fixture
    carries them because this test is about the renderer. The n=8 row it used
    to carry now falls under `MIN_GATE_TRADES` — see the test below, which is
    the half that changed.
    """
    report = SelfAudit.render_report(
        {"summary": {"n": 5, "win_rate": 0.6, "net_pnl": 1.0},
         "shadow_gates": {"CORRELATION": {
             "n": 24, "net_r": 9.6, "wins": 15, "losses": 9, "avg_r": 0.4,
             "sum_r2": 12.0, "lower_r": 0.18, "upper_r": 0.62,
             "verdict": "eating_edge"}}},
        [], {}, "alts_1h")
    assert "CORRELATION" in report and "+9.6R" in report
    assert "costliest gate" in report
    assert "+0.18R/trade" in report, "the bound the verdict turns on is missing"


def test_a_gate_that_only_leads_the_sort_is_not_called_the_costliest():
    """The live card's own defect: +4.1R over 97 trades is +0.042R each.

    `net_r > 0.5` was the whole bar, so a total accumulated over a large
    sample read exactly like a real per-trade effect — on the scoreboard whose
    next step is loosening a risk gate on a live account.
    """
    report = SelfAudit.render_report(
        {"summary": {"n": 40, "scored": 40, "win_rate": 0.28, "pf": 0.4,
                     "net_pnl": -26.86},
         "shadow_gates": {"MTF_ALIGNMENT": {
             "n": 97, "net_r": 4.1, "wins": 30, "losses": 60, "avg_r": 0.042,
             "sum_r2": 210.0, "lower_r": -0.2519, "upper_r": 0.3365,
             "verdict": "undistinguished"}}},
        [], {}, "alts_1h")
    assert "costliest gate" not in report
    assert "not distinguishable from noise" in report
    # The per-trade figure has to be ON the card: without it no reader can
    # recover the 0.042 the whole verdict turns on.
    assert "+0.042R/trade" in report
    assert "MTF_ALIGNMENT" in report and "97 blocked trades" in report


def test_an_unreadable_shadow_book_is_not_a_clean_one():
    """`None` and `{}` both used to render as silence."""
    unreadable = SelfAudit.render_report(
        {"summary": {"n": 5}, "shadow_gates": None}, [], {}, "alts_1h")
    assert "could not be read" in unreadable
    empty = SelfAudit.render_report(
        {"summary": {"n": 5}, "shadow_gates": {}}, [], {}, "alts_1h")
    assert "Shadow book" not in empty, (
        "an empty ledger has nothing to report; saying so would be noise")


# ── wiring pins ───────────────────────────────────────────────────────

class TestWiring:
    def test_config_flags_exist(self):
        from bot.config import CONFIG
        assert isinstance(CONFIG.self_audit_enabled, bool)
        assert 0 <= CONFIG.self_audit_hour_utc <= 23
        assert 1 <= CONFIG.self_audit_max_proposals <= 5

    def test_engine_tick_spawns(self):
        from bot.core import engine as eng_mod
        src = inspect.getsource(eng_mod)
        assert "SELF_AUDIT.maybe_spawn(self)" in src

    def test_monitor_delivers(self):
        from bot.core.proactive_monitor import ProactiveMonitor
        src = inspect.getsource(ProactiveMonitor)
        assert "_check_self_audit" in src
        assert "SELF_AUDIT.drain_pending()" in src

    def test_telegram_registers_audit(self):
        from bot.skills import telegram_handler as th
        src = inspect.getsource(th)
        assert '("audit", self._cmd_audit)' in src

    def test_allowlist_is_bounded_and_typed(self):
        for flag, spec in ALLOWED_FLAGS.items():
            assert spec["type"] in ("bool", "float")
            if spec["type"] == "float":
                assert spec["min"] < spec["max"]
