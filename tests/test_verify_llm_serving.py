"""The serving checker must never render an unreadable server as a healthy one.

`scripts/verify_llm_serving.py` exists because two numbers that decide whether
the bot's LLM path works live on different machines — the server's granted slot
count and the client's `SCAN_ANALYSIS_CONCURRENCY` — and nothing held both. The
danger in writing such a tool is that it becomes another surface that reports
"fine" when it could not look, which is the exact shape the outage wore: an
idle `/api/ps` answering `{"models":[]}` read as healthy, an empty `bot.log`
read as no errors.

So the load-bearing test here is not that a mismatch is caught. It is that
EVERY unreadable input degrades the exit code.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import scripts.verify_llm_serving as v
from tests.test_preflight_matches_ci import code_only

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The property the whole script exists to have.
# --------------------------------------------------------------------------

def test_unreadable_endpoint_is_not_a_pass():
    """A server that did not answer must never score 0."""
    checks = [
        v.check_endpoint(None, "http://localhost:11434/v1"),
        v.check_model_available("runeclaw-v6", None),
        v.check_concurrency(12, None),
        v.check_context(None, None),
    ]
    assert all(c.status == v.UNKNOWN for c in checks)
    assert v.rollup(checks) == 3


@pytest.mark.parametrize("checks", [
    [v.check_endpoint(None, "u")],
    [v.check_model_available("m", None)],
    [v.check_concurrency(12, None)],
    [v.check_concurrency(None, 4)],
    [v.check_context(None, None)],
])
def test_every_unknown_degrades_the_rollup(checks):
    """No single UNKNOWN may be absorbed into a green result."""
    assert v.rollup(checks) == 3


def test_a_failed_read_is_not_an_empty_model_list():
    """`fetch_served_models` must answer None, not [], when it cannot read.

    An empty list is a MEASURED fact — the server answered and serves nothing.
    Collapsing the two would make an unreachable server indistinguishable from
    an empty one, and only one of those is an outage.
    """
    v_get = v._get_json
    try:
        v._get_json = lambda *a, **k: None
        assert v.fetch_served_models("http://x/v1", None) is None
    finally:
        v._get_json = v_get


def test_a_payload_without_the_expected_field_is_also_unreadable(monkeypatch):
    """A 200 carrying the wrong shape is not a measurement either.

    Found by mutation: every other test patched `fetch_served_models` and
    `fetch_loaded_context` wholesale, so the fetchers' own branches were
    covered by nothing. Returning `[]` or `0` from the malformed-payload path
    survived the entire suite — and `0` is the worse of the two, because
    `check_context` reads it as the measured fact "no model is loaded".
    """
    monkeypatch.setattr(v, "_get_json", lambda *a, **k: {"unexpected": 1})
    assert v.fetch_served_models("http://x/v1", None) is None
    assert v.fetch_loaded_context("http://x/v1", None) is None


def test_an_unreachable_ps_is_not_zero_loaded_models(monkeypatch):
    """None from the transport must stay None all the way to the check.

    `0` is a real answer — the server replied and nothing is resident — and it
    resolves to NOT_APPLICABLE, which does not degrade the exit code. So an
    unreadable read leaking in as `0` would turn a dead endpoint green.
    """
    monkeypatch.setattr(v, "_get_json", lambda *a, **k: None)
    got = v.fetch_loaded_context("http://x/v1", None)
    assert got is None
    assert got != 0
    assert v.check_context(None, got).status == v.UNKNOWN


def test_a_loaded_model_without_a_context_field_is_unreadable(monkeypatch):
    """Present model, absent field: still not a number we may print."""
    monkeypatch.setattr(v, "_get_json", lambda *a, **k: {"models": [{"name": "m"}]})
    assert v.fetch_loaded_context("http://x/v1", None) is None


def test_ps_is_asked_at_the_root_not_under_v1(monkeypatch):
    """/api/ps lives beside /v1, not inside it — a 404 here reads as unknown."""
    seen = []
    monkeypatch.setattr(v, "_get_json",
                        lambda url, *a, **k: seen.append(url) or {"models": []})
    v.fetch_loaded_context("http://localhost:11434/v1", None)
    assert seen == ["http://localhost:11434/api/ps"]


def test_measured_empty_is_reported_as_measured():
    """A server that answers with zero models is OK at the endpoint check."""
    assert v.check_endpoint([], "u").status == v.OK
    # ...and the model it cannot supply is a MISMATCH, not an UNKNOWN.
    assert v.check_model_available("runeclaw-v6", []).status == v.MISMATCH


def test_no_model_loaded_is_not_an_unreadable_context():
    """Zero loaded models is a measured precondition, not a failed read."""
    assert v.check_context(None, 0).status == v.NOT_APPLICABLE
    assert v.rollup([v.check_context(None, 0)]) == 0
    # But being unable to ask at all still degrades.
    assert v.check_context(None, None).status == v.UNKNOWN


# --------------------------------------------------------------------------
# The incident itself, reconstructed.
# --------------------------------------------------------------------------

def test_the_2026_09_02_outage_is_caught():
    """One serving slot, twelve concurrent analyses — the actual fault."""
    c = v.check_concurrency(12, 1)
    assert c.status == v.MISMATCH
    # The remedy must name the number to set, not merely report a conflict.
    assert "SCAN_ANALYSIS_CONCURRENCY=1" in c.detail


def test_the_missing_model_tag_is_caught():
    """`runeclaw-v6` against a store that has other tags is a name fault."""
    served = ["pbdes2022/humanoid-traders:v12-14b", "runeclaw-sc:latest",
              "llama3.2:latest"]
    c = v.check_model_available("runeclaw-v6", served)
    assert c.status == v.MISMATCH
    # It must show what IS there, or the operator cannot act on it.
    assert "humanoid-traders" in c.detail


def test_concurrency_within_slots_passes():
    assert v.check_concurrency(4, 4).status == v.OK
    assert v.check_concurrency(2, 4).status == v.OK


# --------------------------------------------------------------------------
# The per-slot context arithmetic — the "16K looks like 64K" trap.
# --------------------------------------------------------------------------

def test_ctx_size_is_divided_by_parallel():
    """llama.cpp's --ctx-size is the TOTAL across slots, not per request."""
    log = ('time=... msg="starting llama server" cmd="... --parallel 4 '
           '--ctx-size 65536 --flash-attn on"\n')
    slots, per_slot = v.read_granted_slots(log)
    assert slots == 4
    assert per_slot == 16384


def test_the_last_load_wins():
    """A restarted server appends; only the newest line describes reality."""
    log = ('msg="starting llama server" --parallel 1 --ctx-size 262144\n'
           'msg="starting llama server" --parallel 4 --ctx-size 65536\n')
    assert v.read_granted_slots(log) == (4, 16384)


def test_a_log_with_no_load_line_reads_as_unknown():
    """Present-but-silent is not a measurement."""
    assert v.read_granted_slots("some other line\n") == (None, None)
    assert v.read_granted_slots("") == (None, None)
    assert v.read_granted_slots(None) == (None, None)


def test_granted_not_requested():
    """A ceiling in the environment is not a slot the scheduler handed out.

    The config map prints OLLAMA_NUM_PARALLEL:4 whether or not four slots fit;
    only the load line reports what was granted, so that is what is parsed.
    """
    log = 'msg="starting llama server" --parallel 1 --ctx-size 16384\n'
    slots, _ = v.read_granted_slots(log)
    assert slots == 1


# --------------------------------------------------------------------------
# Drift pins: the copied defaults must equal their originals.
# --------------------------------------------------------------------------

def test_default_concurrency_matches_config():
    src = code_only((REPO / "bot" / "config.py").read_text(encoding="utf-8"))
    m = re.search(
        r"scan_analysis_concurrency\s*:\s*int\s*=\s*int\s*\(\s*_env_float\s*\(\s*"
        r"[\"']SCAN_ANALYSIS_CONCURRENCY[\"']\s*,\s*(\d+)", src)
    assert m, "could not locate scan_analysis_concurrency in bot/config.py"
    assert int(m.group(1)) == v.DEFAULT_SCAN_CONCURRENCY


def test_default_runeclaw_model_matches_provider():
    src = code_only((REPO / "bot" / "llm" / "provider.py").read_text(encoding="utf-8"))
    # NB: `code_only` strips any string token at statement start as a
    # docstring, which inside a dict literal eats the KEYS — so `"default_model"`
    # is not in the scanned text and cannot be the anchor. The env var name is,
    # and it is unique in the file.
    m = re.search(
        r"os\s*\.\s*getenv\s*\(\s*[\"']RUNECLAW_LLM_MODEL[\"']\s*,"
        r"\s*[\"']([^\"']+)[\"']", src)
    assert m, "could not locate the RUNECLAW default_model in provider.py"
    assert m.group(1) == v.DEFAULT_RUNECLAW_MODEL


# --------------------------------------------------------------------------
# End to end, through the seam.
# --------------------------------------------------------------------------

def test_run_reports_three_not_two(monkeypatch):
    """A wholly unreachable server exits 3 — never 0, never 1."""
    monkeypatch.setattr(v, "fetch_served_models", lambda *a, **k: None)
    monkeypatch.setattr(v, "fetch_loaded_context", lambda *a, **k: None)
    monkeypatch.setattr(v, "_read_log", lambda: None)
    _checks, code = v.run("http://localhost:11434/v1", "runeclaw-v6", None, 12)
    assert code == 3


def test_run_is_green_only_when_everything_was_read(monkeypatch):
    monkeypatch.setattr(v, "fetch_served_models", lambda *a, **k: ["runeclaw-v6"])
    monkeypatch.setattr(v, "fetch_loaded_context", lambda *a, **k: 16384)
    monkeypatch.setattr(
        v, "_read_log",
        lambda: 'msg="starting llama server" --parallel 4 --ctx-size 65536\n')
    checks, code = v.run("http://x/v1", "runeclaw-v6", None, 4)
    assert code == 0, [(c.name, c.status, c.detail) for c in checks]


def test_run_flags_the_mismatch_over_the_unknown(monkeypatch):
    """A real mismatch outranks an unreadable check — 1 beats 3."""
    monkeypatch.setattr(v, "fetch_served_models", lambda *a, **k: ["other:latest"])
    monkeypatch.setattr(v, "fetch_loaded_context", lambda *a, **k: None)
    monkeypatch.setattr(v, "_read_log", lambda: None)
    _checks, code = v.run("http://x/v1", "runeclaw-v6", None, 12)
    assert code == 1


# --------------------------------------------------------------------------
# The runbook describes this script. Prose is what rots first.
# --------------------------------------------------------------------------

RUNBOOK = REPO / "docs" / "LIVE_HARDENING_RUNBOOK.md"


def test_runbook_points_at_a_script_that_exists():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/verify_llm_serving.py" in text
    assert (REPO / "scripts" / "verify_llm_serving.py").is_file()


def test_runbook_exit_table_matches_the_rollup():
    """Every code the table documents must be one `rollup` can produce.

    `2` is the argparse usage exit and never comes from `rollup`, so it is
    listed separately here rather than asserted against it.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    for code in ("`0`", "`1`", "`2`", "`3`"):
        assert code in text, f"runbook no longer documents exit {code}"
    produced = {
        v.rollup([Check for Check in []]),                    # nothing: 0
        v.rollup([v.check_concurrency(12, 1)]),               # mismatch: 1
        v.rollup([v.check_concurrency(12, None)]),            # unknown: 3
    }
    assert produced == {0, 1, 3}


def test_runbook_names_the_valve_the_code_actually_reads():
    """`LLM_BACKGROUND_SCANS` must still be an on/off `_env_switch` flag.

    The runbook tells an operator to set it to `off` while a fine-tune has the
    GPU. If it were ever re-read with `_env_bool` — whose false-vocabulary is
    ("", "false", "0", "no") and omits "off" — that instruction would silently
    turn the valve ON, which is the opposite of the advice.
    """
    src = code_only((REPO / "bot" / "config.py").read_text(encoding="utf-8"))
    assert re.search(
        r"llm_background_scans\s*:\s*bool\s*=\s*_env_switch\s*\(\s*"
        r"[\"']LLM_BACKGROUND_SCANS[\"']", src), \
        "LLM_BACKGROUND_SCANS is no longer an _env_switch flag"
    assert "LLM_BACKGROUND_SCANS=off" in RUNBOOK.read_text(encoding="utf-8")
