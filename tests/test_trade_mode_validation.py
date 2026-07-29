"""An unimplemented TRADE_MODE must fail at boot, not at a live order.

    is_futures = CONFIG.exchange.trade_mode == "futures"

That was the only test of the value, anywhere. TRADE_MODE was read raw and
never validated, so ANY other string — a typo, a capitalisation, an
aspirational "spot" — flipped is_futures to False and thereby:

  * skipped the futures-market existence check
  * skipped _ensure_leverage
  * skipped the spot->swap symbol conversion

while the venue client stayed hardwired to defaultType "swap". Orders would
have reached a live account with leverage never set.

The config comment made it worse by DOCUMENTING the broken value:

    # Trading mode: "spot" for no leverage, "futures" for USDT-M perpetual

`LivePosition.is_spot` has been marked "DEPRECATED: always False (futures-only
mode)" the whole time. The setting was advertised, unimplemented, and
safety-reducing.

A value that quietly degrades safety is worse than one that refuses to boot.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from bot.config import CONFIG
from tests.source_scan import code_only

CONFIG_SRC = Path("bot/config.py").read_text(encoding="utf-8")
EXECUTOR = code_only(Path("bot/core/live_executor.py").read_text(encoding="utf-8"))


def _boot(env_value: str | None):
    """Import bot.config in a fresh interpreter with TRADE_MODE set."""
    import os
    env = dict(os.environ)
    env.pop("TRADE_MODE", None)
    if env_value is not None:
        env["TRADE_MODE"] = env_value
    return subprocess.run(
        [sys.executable, "-c",
         "import bot.config as c; print(c.CONFIG.exchange.trade_mode)"],
        capture_output=True, text=True, env=env, cwd=Path.cwd())


# ── the implemented value still works ─────────────────────────────────────

def test_the_default_is_futures_and_boots():
    assert CONFIG.exchange.trade_mode == "futures"
    r = _boot(None)
    assert r.returncode == 0, r.stderr
    assert "futures" in r.stdout


def test_an_explicit_futures_boots():
    r = _boot("futures")
    assert r.returncode == 0, r.stderr


def test_case_and_whitespace_are_normalised():
    """`TRADE_MODE=Futures` is the typo that would have silently disabled the
    leverage call on a live account."""
    for raw in ("Futures", " FUTURES ", "FuTuReS"):
        r = _boot(raw)
        assert r.returncode == 0, f"{raw!r} should normalise: {r.stderr}"
        assert "futures" in r.stdout


# ── everything else refuses ───────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["spot", "margin", "futres", "swap", "1"])
def test_an_unsupported_mode_refuses_to_start(bad):
    r = _boot(bad)
    assert r.returncode != 0, f"{bad!r} booted — it must not"
    assert "FATAL" in r.stderr and bad in r.stderr


def test_the_error_lists_what_is_valid():
    """An error that says only "invalid" makes the operator go read source."""
    r = _boot("spot")
    assert "Valid values: futures" in r.stderr


def test_the_error_says_why_it_refuses_rather_than_defaulting():
    """Falling back to futures would be worse: the operator asked for
    something else and would never learn they did not get it."""
    r = _boot("spot")
    assert "skip the leverage" in r.stderr


# ── the documentation no longer advertises what does not exist ────────────

def test_spot_is_not_offered_as_a_valid_value():
    assert '_env_choice("TRADE_MODE", "futures", ("futures",))' in CONFIG_SRC
    block = CONFIG_SRC[CONFIG_SRC.index("    # Trading mode."):
                       CONFIG_SRC.index("trade_mode: str =")]
    assert '"spot" for no leverage' not in block, (
        "the comment promised a mode that turns safety checks off")
    assert "ONLY \"futures\" is implemented" in block


def test_the_reason_spot_does_not_work_is_written_down():
    block = CONFIG_SRC[CONFIG_SRC.index("    # Trading mode."):
                       CONFIG_SRC.index("trade_mode: str =")]
    for fact in ("defaultType", "leverage", "is_spot"):
        assert fact in block, f"the note should say why spot is absent: {fact}"


# ── the premise, checked rather than assumed ──────────────────────────────

def test_is_futures_really_is_a_bare_equality_on_this_value():
    assert 'is_futures = CONFIG.exchange.trade_mode == "futures"' in EXECUTOR


def test_the_skipped_guards_really_are_behind_it():
    """If these ever stop being gated on is_futures, this whole hazard
    changes shape and the note above goes stale."""
    i = EXECUTOR.index('is_futures = CONFIG.exchange.trade_mode == "futures"')
    # Wide enough to reach _ensure_leverage, which sits ~3.6k chars after
    # the flag. A window that stops short would pass this test by
    # missing the guard rather than by finding it absent.
    block = EXECUTOR[i:i + 4500]
    assert "if is_futures:" in block
    assert "_ensure_leverage" in block
    assert "has_futures" in block


def test_is_spot_is_still_the_vestige_the_note_describes():
    assert "is_spot: bool = False" in EXECUTOR
