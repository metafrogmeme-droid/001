"""The flag A/B harness — the operator doctrine made runnable.

"If we add new, do a direct A/B; if it improves, default on." Voters had an
ablation harness; behavior FLAGS had nothing, so four shipped features sat
dark and unmeasured. These tests pin the two things that decide whether the
harness can be trusted: the verdict rule (conservative — a flag must earn its
default, and only walk-forward may say FLIP ON) and arm isolation (both arms
start from the same known flag state, so a stray env var cannot fake a win).
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ab_flag", Path(__file__).resolve().parents[1] / "scripts" / "ab_flag.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def _wf(mean_oos, folds=4, total=6):
    return {"ok": True, "mean_oos_pct": mean_oos, "profitable_folds": folds,
            "total_folds": total}


def test_only_walk_forward_can_flip_a_default():
    base = _wf(1.00)
    # A clear OOS improvement with folds intact is the ONE case that flips.
    assert ab.verdict(base, _wf(1.40, folds=5), wf=True).startswith("FLIP ON")
    # Same improvement, but fewer profitable folds → not good enough.
    assert ab.verdict(base, _wf(1.40, folds=3), wf=True).startswith("STAY DARK")
    # Inside noise, and outright harm, both stay dark.
    assert ab.verdict(base, _wf(1.05), wf=True).startswith("STAY DARK")
    assert ab.verdict(base, _wf(0.50), wf=True).startswith("STAY DARK")
    # A single window may never flip anything — the strongest word is PROMISING.
    single = {"ok": True, "return_pct": 5.0}
    assert ab.verdict({"ok": True, "return_pct": 1.0}, single, wf=False).startswith("PROMISING")
    assert "FLIP ON" not in ab.verdict({"ok": True, "return_pct": 1.0}, single, wf=False)


def test_a_failed_arm_never_produces_a_verdict():
    base = _wf(1.0)
    assert ab.verdict(base, {"ok": False}, wf=True) == "run failed — no verdict"
    assert ab.verdict({"ok": False}, _wf(9.0), wf=True) == "run failed — no verdict"


def test_both_arms_start_from_the_same_known_flag_state(monkeypatch, tmp_path):
    """Arm isolation: the baseline must force every known flag OFF, so an
    operator shell that happens to export one cannot contaminate the run."""
    class _P:
        returncode = 0

    # A contaminated environment: a flag already exported ON.
    monkeypatch.setenv("PATTERN_TARGET_LEVELS_ENABLED", "1")
    envs = []

    def fake_run2(cmd, stdout=None, stderr=None, env=None, cwd=None):
        envs.append(dict(env))
        stdout.write("Total Return: 1.00%\nTotal Trades: 10\n"
                     "Profit Factor: 1.5\nSharpe Ratio: 0.5\nWinners: 5 (50%)\n")
        return _P()

    monkeypatch.setattr(ab.subprocess, "run", fake_run2)
    ab._run("baseline", None, str(tmp_path), False, 100)
    ab._run("vp", "VP_TWOPASS_DIRECTION_ENABLED", str(tmp_path), False, 100)

    baseline_env, arm_env = envs[0], envs[1]
    # Baseline: every known flag explicitly off, contamination included.
    for f in ab.KNOWN_FLAGS:
        assert baseline_env[f] == "0", f"{f} leaked into the baseline arm"
    # Treatment arm: exactly one flag on, everything else still off.
    on = [f for f in ab.KNOWN_FLAGS if arm_env[f] == "1"]
    assert on == ["VP_TWOPASS_DIRECTION_ENABLED"], f"arm turned on {on}"


def test_every_known_flag_is_actually_read_by_the_engine():
    """A typo'd flag name would measure nothing and quietly report "no change".

    Flags live in two places: most in bot/config.py, but some are read at
    module import time (chart_patterns reads ELLIOTT_CURRENT_WAVE_ENABLED that
    way). Module-level reads still work here because each arm is a fresh
    subprocess — what matters is only that SOMETHING reads the name.
    """
    root = Path(__file__).resolve().parents[1] / "bot"
    sources = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in root.rglob("*.py"))
    for flag in ab.KNOWN_FLAGS:
        assert f'"{flag}"' in sources, f"{flag} is not read anywhere in bot/"
        assert ab.KNOWN_FLAGS[flag], f"{flag} needs a note explaining what it does"
