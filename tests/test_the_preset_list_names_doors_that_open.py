"""The /run preset list names a door for each preset, and every door opens.

It printed each preset's alias as a slash command: "ALT Sweep  /altsweep".
`/dip`, `/momentum` and `/scalp` are registered; `/ethma`, `/volrotation`
and `/altsweep` are not, so the tap answered "unknown command" instead of
the preset. The list prints the door its own footer names, "run" and the
alias, which reaches the preset on every surface.
"""
from __future__ import annotations

import re

from bot.skills.skill_registry import RunStrategySkill
from tests.test_alerts_name_real_commands import registered_commands


def _listed():
    return RunStrategySkill._list()


def test_every_preset_with_an_alias_names_run_and_the_alias():
    text = _listed()
    for key in RunStrategySkill.PRESETS:
        aliases = [a for a, t in RunStrategySkill.ALIASES.items() if t == key]
        if aliases:
            assert f"<i>run {aliases[0]}</i>" in text, key


def test_every_named_door_resolves_to_its_preset():
    named = re.findall(r"<i>run ([^<]+)</i>", _listed())
    assert named, "the list names no door"
    for alias in named:
        assert RunStrategySkill._resolve(alias) == RunStrategySkill.ALIASES[alias]


def test_no_slash_command_is_printed_that_is_not_registered():
    known = registered_commands()
    printed = re.findall(r"(?<![\w<])/([a-z][a-z0-9_]*)", _listed())
    assert [c for c in printed if c not in known] == []


def test_each_rule_the_live_bot_does_not_run_names_a_scan_door_that_opens():
    """`/altsweep`, `/ethma` and `/volrotation` were printed with nothing
    behind them. They are commands now: each is the preset's scan door
    (`strategy_scan.scan_command`), and the list names it beside `run`. A
    preset the live bot runs has no scan door, and the list prints none."""
    from bot.core.strategy_scan import scan_command
    known = registered_commands()
    text = _listed()
    doors = {k: scan_command(k) for k in RunStrategySkill.PRESETS}
    assert {"altsweep", "ethma", "volrotation", "limitscan", "multitrend"} == {
        d for d in doors.values() if d}
    for key, door in doors.items():
        label = RunStrategySkill.PRESETS[key]["label"]
        line = next(ln for ln in text.splitlines() if f"<b>{label}</b>" in ln)
        if door:
            assert door in known and f"/{door} scans it now" in line, key
        else:
            assert "scans it now" not in line, key
    assert {"dip", "momentum", "scalp", "run"} <= known
