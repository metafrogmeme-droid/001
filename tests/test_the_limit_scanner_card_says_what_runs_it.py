"""The Limit Entry Scanner's card, its confirm gate and its Lab run.

A card that names a rule claims something runs it. The scanner's card says
the frozen backtest runs it and the live bot does not yet, names what the
frozen bars cannot carry (book, funding, taker flow), and is measured on its
own frozen universe: the house window's BTC, ETH and SOL would be three of
its 27 markets and BTC is its gate, which is not the book. "Reproduce in
Lab" sends the card's own numbers through the same reader the runner uses,
over every symbol the card was measured on.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import HTTPException

from bot.api.lab import LabRunRequest, _preset_gate_args
from bot.core import limit_scanner as ls
from bot.core import strategy_catalog as sc
from bot.skills.skill_registry import RunStrategySkill
from scripts.gen_agent_scorecards import _gate_args, card_window, scorecard_gates

KEY = "limit scanner"
CFG = RunStrategySkill.PRESETS[KEY]


def test_the_preset_is_the_scanner_and_its_numbers_read():
    p = ls.params_from_preset(CFG)
    assert p is not None and p.min_score == 70
    assert [(c.name, c.leader) for c in p.classes] == [("crypto", "BTC"), ("metals", "XAU")]
    # Every listed market is a class's leader or one of its members.
    listed = {ls._base(s) for s in CFG["symbols"]}
    assert listed == {c.leader for c in p.classes} | set().union(*(c.members for c in p.classes))
    assert RunStrategySkill._resolve("limitscan") == KEY
    assert set(ls.scanner_config(CFG)) <= set(ls.SCANNER_KEYS)


def test_the_card_says_who_runs_it_and_what_the_bars_cannot_carry():
    card = sc.get_agent("limit-scanner")
    how = card["how"]
    assert how.startswith(sc.NOT_RUN_LIVE)
    assert "The frozen backtest runs it." in how
    assert ls.how_line(CFG) in how and ls.BACKTEST_READS in how
    assert "$" not in json.dumps(card)
    # The leader is the gate and is never traded; its own floor is not named.
    assert "27 crypto markets behind BTC" in how and "5 metals markets behind XAU" in how
    assert "1.5% for ETH" in how and "for BTC" not in how
    assert "thin markets" in how, "the metals floor says what it admits"
    assert "each class's best market gets one limit a pass" in how


def test_the_card_note_names_the_exits_that_ran():
    from scripts.gen_agent_scorecards import scorecard_note
    note = scorecard_note(CFG)
    assert "runs the scanner's own exits, never the house's" in note
    assert "are the house exits" not in note
    assert "each leader's side" in note and "BTC's side" not in note
    # The published card carries the note its generator writes now.
    path = os.path.join(os.path.dirname(__file__), "..", "benchmark", "scorecards",
                        "limit-scanner.json")
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["note"] == note


def test_the_card_is_measured_on_its_own_universe():
    dataset, symbols, bars = card_window(CFG)
    assert os.path.basename(dataset) == "limit_scanner_1h" and bars == 6000
    assert len(symbols) == 34 and symbols[0] == "BTC/USDT:USDT"
    # The house window is unchanged for every other preset.
    for key, cfg in RunStrategySkill.PRESETS.items():
        if key != KEY:
            assert card_window(cfg)[1] == ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


def test_the_runner_is_given_the_presets_own_numbers():
    args = _gate_args(CFG)
    assert args[:2] == ["--scanner", "limit_entry"]
    assert json.loads(args[3]) == ls.scanner_config(CFG)
    gates = scorecard_gates(CFG)
    assert gates["scanner"] == "limit_entry"
    assert gates["scanner_config"] == ls.scanner_config(CFG)


# ── the Lab ─────────────────────────────────────────────────────────────────

def test_the_lab_runs_the_cards_numbers_through_the_runners_reader():
    req = LabRunRequest(dataset="limit_scanner_1h", scanner="limit_entry",
                        scanner_config=ls.scanner_config(CFG),
                        symbols=card_window(CFG)[1])
    args, params = _preset_gate_args(req)
    assert args == _gate_args(CFG)
    assert params["scanner_config"] == ls.scanner_config(CFG)
    assert len(req.symbols) == 34, "the card's whole universe is one request"


@pytest.mark.parametrize("body", [
    {"scanner": "other"},
    {"scanner": "limit_entry", "scanner_config": {"min_score": "70"}},
    {"scanner": "limit_entry", "scanner_config": {"min_score": 101}},
    {"scanner": "limit_entry", "scanner_config": {"invented": 1}},
    {"scanner": "", "scanner_config": {"min_score": 70}},
])
def test_the_lab_refuses_numbers_the_runner_would_not_read(body):
    with pytest.raises(HTTPException) as e:
        _preset_gate_args(LabRunRequest(dataset="x", **body))
    assert e.value.status_code == 400


def test_a_house_lab_run_is_still_four_symbols():
    import asyncio
    from unittest.mock import patch

    from bot.api import lab
    cats = {"majors_1h": {"symbols": [f"S{i}" for i in range(10)]}}
    started = []

    async def fake_job(job_id, cmd, out_file):
        started.append(cmd)

    with patch.object(lab, "_datasets", return_value=cats), \
            patch.object(lab, "_run_job", fake_job), \
            patch.object(lab, "_last_submit", 0.0), patch.object(lab, "_running_id", None):
        async def go(req):
            out = await lab.lab_run(req)
            await asyncio.sleep(0)
            return out
        house = asyncio.run(go(LabRunRequest(dataset="majors_1h",
                                             symbols=[f"S{i}" for i in range(10)])))
        assert house["params"]["symbols"] == ["S0", "S1", "S2", "S3"]
        lab._last_submit = 0.0
        lab._running_id = None
        scan = asyncio.run(go(LabRunRequest(dataset="majors_1h", scanner="limit_entry",
                                            scanner_config={},
                                            symbols=[f"S{i}" for i in range(10)])))
        assert len(scan["params"]["symbols"]) == 10
