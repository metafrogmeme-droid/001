"""The self-audit's allow-list range for VOLATILITY_GUARD_ATR_PCT was a
FRACTION (0.03..0.15) on a flag that config declares and the risk engine
reads as a PERCENT (7.0, bounded 0.1..100; check #16 compares
``atr / entry * 100`` against it).

Driven on the card the operator pasted on 2026-09-27:

    VOLATILITY_GUARD_ATR_PCT=0.03
      tighten the volatility guard
      🟥 measured +0.00% (-0.97pp vs baseline) · PF 0.0 · 0tr

Three things were wrong on that one row, and each is a test here.

1. The range ADMITTED 0.03 and would have DROPPED 5 -- the only values it
   accepted were ones that close the gate, and the only ones it refused were
   the ones in the unit the flag reads.  The rule is derived now: every float
   row's range contains the shipped default (a range that excludes the
   default is in the wrong unit, or always proposes a change) and sits
   inside the config's own bounds where it declares any.
2. A run that TOOK NO TRADES was rendered as a measured return, in red.  The
   runner prints +0.00% and PF 0.00 for it -- the same figures a run of pure
   losses prints -- and the card read the zero-trade run as a strategy that
   lost 0.97 points.  A zero-trade run is its own verdict.
3. The card printed the proposed value and never the value IN FORCE, so an
   `OF_MAX_SPREAD_BPS=100` under "tighten the spread guard" could not be
   seen to LOOSEN a guard sitting at 50.  The value in force travels with
   the proposal now.
"""
from __future__ import annotations

import pathlib
import re

from bot.core import self_audit as sa
from bot.core.self_audit import ALLOWED_FLAGS, validate_proposals

ROOT = pathlib.Path(__file__).resolve().parent.parent
_DECL_FILES = ("bot/config.py", "bot/core/order_flow.py")


def declared(flag: str, sources: dict[str, str] | None = None):
    """The flag's own declaration: ``(default, lo, hi)`` for an
    ``_env_float_bounded``, ``(default, None, None)`` for a bare
    ``_env_float``/``_env_int``, ``None`` when no file declares it.  Read off
    the SOURCE, the way the venue-cap suite reads ``MAX_LEVERAGE``'s: the
    value in force on this box is the box's, and a test of the allow-list
    must not fail because an operator's `.env` set the knob low."""
    sources = sources if sources is not None else {
        f: (ROOT / f).read_text() for f in _DECL_FILES}
    esc = re.escape(flag)
    for src in sources.values():
        m = re.search(r'_env_float_bounded\("%s",\s*([\d.]+),\s*([\d.]+),\s*([\d._]+)\)'
                      % esc, src)
        if m:
            d, lo, hi = (float(x.replace("_", "")) for x in m.groups())
            return d, lo, hi
        m = re.search(r'_env_(?:float|int)\("%s",\s*([\d._]+)' % esc, src)
        if m:
            return float(m.group(1).replace("_", "")), None, None
    return None


def range_faults(allowed: dict, sources: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Every float row whose range does not contain the flag's declared
    default, or reaches outside the config's own bounds, or whose flag no
    file declares.  A bool row is outside the rule.  Loud on every branch:
    an undeclared flag is a fault, never an acquittal."""
    faults: list[tuple[str, str]] = []
    for flag, spec in allowed.items():
        if spec.get("type") != "float":
            continue
        lo, hi = float(spec["min"]), float(spec["max"])
        decl = declared(flag, sources)
        if decl is None:
            faults.append((flag, "no declaration found"))
            continue
        default, clo, chi = decl
        if not (lo <= default <= hi):
            faults.append((flag, f"default {default:g} outside audit range [{lo:g}, {hi:g}]"))
        if clo is not None and (lo < clo or hi > chi):
            faults.append((flag, f"audit range [{lo:g}, {hi:g}] outside config bounds [{clo:g}, {chi:g}]"))
    return faults


# ── 1. the range is in the unit the flag reads ─────────────────────────────

def test_every_float_row_contains_its_default_and_sits_inside_the_config_bounds():
    assert range_faults(ALLOWED_FLAGS) == []


def test_the_volatility_row_is_a_percent_now():
    spec = ALLOWED_FLAGS["VOLATILITY_GUARD_ATR_PCT"]
    default, lo, hi = declared("VOLATILITY_GUARD_ATR_PCT")
    assert default == 7.0 and (lo, hi) == (0.1, 100.0)
    assert spec["min"] <= 7.0 <= spec["max"]
    assert spec["min"] >= 1, "a floor under 1% is the fraction unit creeping back"


def test_the_rule_sees_the_fraction_row_it_was_written_for():
    planted = {"VOLATILITY_GUARD_ATR_PCT": {"type": "float", "min": 0.03, "max": 0.15}}
    reasons = [r for _, r in range_faults(planted)]
    assert len(reasons) == 2, reasons
    assert "default 7 outside audit range [0.03, 0.15]" in reasons
    assert "audit range [0.03, 0.15] outside config bounds [0.1, 100]" in reasons


def test_the_rule_is_loud_about_a_flag_nothing_declares():
    assert range_faults({"NO_SUCH_KNOB": {"type": "float", "min": 1, "max": 2}}) == [
        ("NO_SUCH_KNOB", "no declaration found")]


def test_a_range_wider_than_the_config_bounds_is_a_fault_on_its_own():
    src = {"x.py": 'a = _env_float_bounded("KNOB", 5.0, 1.0, 10.0)\n'}
    assert range_faults({"KNOB": {"type": "float", "min": 0.5, "max": 8}}, src) == [
        ("KNOB", "audit range [0.5, 8] outside config bounds [1, 10]")]
    assert range_faults({"KNOB": {"type": "float", "min": 2, "max": 8}}, src) == []


def test_a_bare_declaration_is_checked_on_its_default_alone():
    src = {"x.py": 'a = _env_float("KNOB", 50.0)\n'}
    assert range_faults({"KNOB": {"type": "float", "min": 10, "max": 200}}, src) == []
    assert range_faults({"KNOB": {"type": "float", "min": 60, "max": 200}}, src) == [
        ("KNOB", "default 50 outside audit range [60, 200]")]


def test_a_bool_row_is_outside_the_rule():
    assert range_faults({"SOMETHING_ENABLED": {"type": "bool"}}, {"x.py": ""}) == []


# ── the validator, driven on the unit ──────────────────────────────────────

def _validate(value, env=None):
    env = env if env is not None else {"VOLATILITY_GUARD_ATR_PCT": "7"}
    return validate_proposals(
        [{"flag": "VOLATILITY_GUARD_ATR_PCT", "value": value, "rationale": "r"}],
        current_env=env)


def test_the_fraction_the_model_proposed_is_dropped_and_the_percent_is_kept():
    assert _validate(0.03) == []
    kept = _validate(5)
    assert [p["value"] for p in kept] == ["5"]


def test_the_edges_of_the_percent_range():
    assert _validate(2) == []
    assert _validate(20) == []
    assert [p["value"] for p in _validate(3)] == ["3"]
    assert [p["value"] for p in _validate(15)] == ["15"]


# ── 3. the value in force travels with the proposal ────────────────────────

def test_the_validator_records_the_value_in_force():
    kept = validate_proposals(
        [{"flag": "OF_MAX_SPREAD_BPS", "value": 100, "rationale": "tighten"}],
        current_env={"OF_MAX_SPREAD_BPS": "50"})
    assert kept and kept[0]["current"] == "50"


def test_the_recorded_value_in_force_is_spelled_for_the_card_not_raw(monkeypatch):
    # The env fixture above hands the validator the STRING "50", which a raw
    # record and a spelled one agree on; a CONFIG read hands it a float and a
    # bool, and only those separate the two. The mutation that records `cur`
    # raw survived a round on the string fixture alone.
    monkeypatch.setattr(sa, "effective_value", lambda flag, env=None: 50.0)
    kept = validate_proposals(
        [{"flag": "OF_MAX_SPREAD_BPS", "value": 100, "rationale": "tighten"}],
        current_env={})
    assert kept and kept[0]["current"] == "50"
    monkeypatch.setattr(sa, "effective_value", lambda flag, env=None: True)
    kept = validate_proposals(
        [{"flag": "EQUITY_THROTTLE_ENABLED", "value": False, "rationale": "off"}],
        current_env={})
    assert kept and kept[0]["current"] == "on"


def test_a_value_in_force_nobody_could_read_is_recorded_as_unread(monkeypatch):
    monkeypatch.setattr(sa, "effective_value", lambda flag, env=None: None)
    kept = validate_proposals(
        [{"flag": "OF_MAX_SPREAD_BPS", "value": 100, "rationale": "tighten"}],
        current_env={})
    assert kept and "current" in kept[0] and kept[0]["current"] is None


def test_the_current_text_spells_a_figure_short_and_a_bool_as_a_word():
    assert sa._current_text(None) is None
    assert sa._current_text("50.0") == "50"
    assert sa._current_text(7.0) == "7"
    assert sa._current_text(True) == "on" and sa._current_text(False) == "off"
    assert sa._current_text("junk") == "junk"


def _render(results, baseline=None):
    baseline = baseline or {"return_pct": 0.97, "pf": 1.24, "trades": 31}
    ev = {"summary": {"n": 40, "scored": 40, "win_rate": 0.20, "pf": 0.35,
                      "net_pnl": -48.81}}
    return sa.SelfAudit.render_report(ev, results, baseline=baseline,
                                      dataset="alts_1h")


def _proposal(flag="TREND_UP_SIZE_MULT", value=0.3, **extra):
    # A MEASURABLE flag by default: OF_MAX_SPREAD_BPS is benchmark-blind, and
    # the harness verdict is read before a run's figures are (its own test
    # below), so a verdict test on it would measure the blind branch.
    row = {"flag": flag, "value": value, "rationale": "shrink uptrend adds",
           "measured": {"return_pct": 1.50, "pf": 1.40, "trades": 29}}
    row.update(extra)
    return row


def _header(out: str, flag: str) -> str:
    return next(ln for ln in out.splitlines() if ln.startswith(f"<b>{flag}="))


def test_the_card_prints_the_value_in_force_beside_the_proposal():
    out = _render([_proposal("OF_MAX_SPREAD_BPS", 100, current="50")])
    assert _header(out, "OF_MAX_SPREAD_BPS") == "<b>OF_MAX_SPREAD_BPS=100</b> (in force: 50)"


def test_an_unread_value_in_force_says_so_and_an_older_result_says_nothing():
    unread = _render([_proposal("OF_MAX_SPREAD_BPS", 100, current=None)])
    assert _header(unread, "OF_MAX_SPREAD_BPS").endswith("(in force: unread)")
    older = _render([_proposal("OF_MAX_SPREAD_BPS", 100)])
    assert "in force" not in older


# ── 2. a zero-trade run is not a measured return ───────────────────────────

ZERO = {"return_pct": 0.0, "pf": 0.0, "trades": 0, "max_dd_pct": 0.0}


def _verdict(out: str, flag: str) -> str:
    lines = out.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(f"<b>{flag}="))
    return lines[i + 2].strip()


def test_the_pasted_card_no_longer_paints_a_zero_trade_run_red():
    out = _render([{"flag": "TREND_UP_SIZE_MULT", "value": 0.3,
                    "rationale": "shrink uptrend adds", "measured": ZERO}])
    v = _verdict(out, "TREND_UP_SIZE_MULT")
    assert v.startswith("⬜ REFUSED EVERY ENTRY on this dataset")
    assert "0 trades against the baseline's 31" in v
    assert "no return or PF to compare" in v
    assert "measured" not in v and "pp" not in v and "PF 0" not in v
    assert "\U0001f7e5" not in v and "\U0001f7e9" not in v


def test_a_zero_trade_run_beside_a_baseline_with_no_count_still_refuses():
    out = _render([_proposal(measured=ZERO)], baseline={"return_pct": 0.97, "pf": 1.24})
    v = _verdict(out, "TREND_UP_SIZE_MULT")
    assert v.startswith("⬜ REFUSED EVERY ENTRY") and "where the baseline took some" in v


def test_a_run_with_one_trade_is_still_a_measurement():
    out = _render([_proposal(measured={"return_pct": -0.40, "pf": 0.0, "trades": 1})])
    v = _verdict(out, "TREND_UP_SIZE_MULT")
    assert v.startswith("\U0001f7e5 measured -0.40% (-1.37pp vs baseline)")
    assert v.endswith("1tr")


def test_an_absent_trade_count_is_unread_never_zero():
    out = _render([_proposal(measured={"return_pct": 4.00, "pf": 2.10})])
    v = _verdict(out, "TREND_UP_SIZE_MULT")
    assert v.endswith("trades unread"), v
    assert "0tr" not in out


def test_a_blind_flag_is_not_measurable_before_its_zero_trade_run_is_read():
    # The harness verdict comes first: a knob the benchmark cannot exercise
    # that also happened to take no trades is "not measurable", not "refused".
    out = _render([_proposal("OF_MAX_SPREAD_BPS", 100, measured=ZERO)])
    v = _verdict(out, "OF_MAX_SPREAD_BPS")
    assert v.startswith("⬜ NOT MEASURABLE HERE")


def test_zero_trades_is_a_reported_zero():
    assert sa._zero_trades({"trades": 0})
    assert sa._zero_trades({"trades": "0"})
    assert not sa._zero_trades({})
    assert not sa._zero_trades({"trades": None})
    assert not sa._zero_trades({"trades": 3})
    assert not sa._zero_trades({"trades": "junk"})
