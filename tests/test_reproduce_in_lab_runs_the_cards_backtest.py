""""Reproduce in Lab" runs the backtest the card was measured with.

The generator measures each published preset with `_gate_args(cfg)`. The Lab
re-runs a card from the request the website builds out of the card's gate
block (`AgentScorecard.labBody`) and turns into runner flags
(`bot/api/lab.py::_preset_gate_args`). Two presets did not survive the trip:

- Safe Scalper was measured with a 1.5-ATR stop and a 2.0-ATR target. The
  card did not record them and the Lab had no field for them, so the Lab ran
  the analyzer's own levels under the card's name.
- ETH MA trend's card names its sizing `target_weight`, `max_gross_leverage`
  and `utilization`; the website read `ma_*` names no card carries, so the
  Lab ran a book that cannot size a fill.

Driven across the runtimes: the real website builder in a node process, the
real Lab flag builder, against the real generator flags, for every committed
card. Skips where node is unavailable (GitHub's ubuntu images ship it).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.api.lab import LabRunRequest, _preset_gate_args
from bot.skills.skill_registry import RunStrategySkill
from scripts.gen_agent_scorecards import _gate_args, _slug, breaker_args, scorecard_gates

ROOT = Path(__file__).resolve().parent.parent
CARDS = sorted((ROOT / "benchmark" / "scorecards").glob("*.json"))
_PRESETS = {_slug(k): cfg for k, cfg in RunStrategySkill.PRESETS.items()}


def _lab_bodies(cards: list[dict]) -> list[dict]:
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = (
        "const S = require(process.argv[1]);"
        "let d = ''; process.stdin.on('data', c => d += c);"
        "process.stdin.on('end', () => {"
        "  const out = JSON.parse(d).map(c => { const r = S.labBody(c.name || c.id, c); return r && r.body; });"
        "  process.stdout.write(JSON.stringify(out)); });"
    )
    res = subprocess.run(
        ["node", "-e", js, str(ROOT / "app" / "public" / "js" / "agent-scorecard.js")],
        input=json.dumps(cards), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:400]
    return json.loads(res.stdout)


def _flags(args: list[str]) -> dict:
    """``--flag value`` pairs, values compared as numbers where they are."""
    out = {}
    for flag, value in zip(args[::2], args[1::2]):
        assert flag.startswith("--"), args
        try:
            out[flag] = float(value)
        except ValueError:
            out[flag] = value
    return out


def _lab_flags(body: dict) -> dict:
    req = LabRunRequest(**{k: v for k, v in body.items() if v is not None})
    args, _params = _preset_gate_args(req)
    flags = _flags(args)
    flags["--confidence-threshold"] = max(0.0, min(1.0, float(req.confidence_threshold)))
    return flags


def _generator_flags(cfg: dict, card: dict) -> dict:
    """The flags the generator ran this card with: the preset's gates and the
    breaker reset the card records (`breaker.reset_bars`)."""
    flags = _flags(_gate_args(cfg) + breaker_args(card["breaker"]["reset_bars"]))
    flags.setdefault("--confidence-threshold", 0.0)   # the runner's default
    return flags


def test_every_card_is_a_preset_and_carries_the_gates_it_was_measured_with():
    assert CARDS, "no committed scorecards"
    for path in CARDS:
        card = json.loads(path.read_text())
        assert path.stem in _PRESETS, path.stem
        assert card["gates"] == json.loads(json.dumps(scorecard_gates(_PRESETS[path.stem]))), path.stem


def test_the_lab_request_runs_the_generators_flags_for_every_card():
    cards = [json.loads(p.read_text()) for p in CARDS]
    bodies = _lab_bodies(cards)
    for path, card, body in zip(CARDS, cards, bodies):
        assert body is not None, f"{path.stem}: the card builds no Lab request"
        assert _lab_flags(body) == _generator_flags(_PRESETS[path.stem], card), path.stem


def test_the_two_presets_that_dropped_out_now_carry_their_runs():
    cards = {p.stem: json.loads(p.read_text()) for p in CARDS}
    bodies = dict(zip(cards, _lab_bodies(list(cards.values()))))
    scalper = _lab_flags(bodies["safe-scalper"])
    assert scalper["--sl-atr-mult"] == 1.5 and scalper["--tp-atr-mult"] == 2.0
    ma = _lab_flags(bodies["eth-ma-trend"])
    for flag in ("--ma-target-weight", "--ma-max-gross-leverage", "--ma-utilization"):
        assert flag in ma, flag


def test_the_lab_refuses_an_exit_multiple_out_of_range():
    from fastapi import HTTPException
    for bad in (0, -1.5, 25, 20.0001):
        with pytest.raises(HTTPException):
            _preset_gate_args(LabRunRequest(dataset="x", sl_atr_mult=bad))
    args, params = _preset_gate_args(LabRunRequest(dataset="x", tp_atr_mult=2))
    assert args == ["--tp-atr-mult", "2.0"] and params == {"tp_atr_mult": 2.0}


def test_an_ma_run_without_its_sizing_is_refused_not_reported_as_zero_trades():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _preset_gate_args(LabRunRequest(dataset="x", ma_fast=50, ma_slow=200))
    assert e.value.status_code == 400
    for name in ("ma_target_weight", "ma_max_gross_leverage", "ma_utilization", "signal_confidence"):
        assert name in e.value.detail
    args, _ = _preset_gate_args(LabRunRequest(
        dataset="x", ma_fast=50, ma_slow=200, ma_target_weight=1.0,
        ma_max_gross_leverage=1.0, ma_utilization=1.0, signal_confidence=0.7))
    assert "--ma-target-weight" in args and "--ma-signal-confidence" in args


def test_the_lab_runs_the_breaker_reset_the_card_was_measured_with():
    # A card measured with a 24-bar reset and re-run at 0 halts at the first
    # trip: Full Scan's 40 trades would reproduce as 9 under the card's name.
    cards = [json.loads(p.read_text()) for p in CARDS]
    for card, body in zip(cards, _lab_bodies(cards)):
        reset = card["breaker"]["reset_bars"]
        assert body["breaker_reset_bars"] == reset, card["agent_id"]
        flags = _lab_flags(body)
        assert flags.get("--breaker-reset-bars") == (float(reset) if reset else None)
    # A card from before the block sends nothing, so the Lab runs 0, as it did.
    old = dict(cards[0])
    old.pop("breaker")
    (body,) = _lab_bodies([old])
    assert "breaker_reset_bars" not in body
    assert "--breaker-reset-bars" not in _lab_flags(body)


def test_the_lab_refuses_a_breaker_reset_that_is_not_a_count():
    from fastapi import HTTPException
    from pydantic import ValidationError
    for bad in (-1, 169):
        with pytest.raises(HTTPException):
            _preset_gate_args(LabRunRequest(dataset="x", breaker_reset_bars=bad))
    for not_a_count in (True, 1.5, "24"):
        with pytest.raises(ValidationError):
            LabRunRequest(dataset="x", breaker_reset_bars=not_a_count)
    assert _preset_gate_args(LabRunRequest(dataset="x", breaker_reset_bars=24)) == (
        ["--breaker-reset-bars", "24"], {"breaker_reset_bars": 24})
    assert _preset_gate_args(LabRunRequest(dataset="x", breaker_reset_bars=0)) == (
        [], {"breaker_reset_bars": 0})
