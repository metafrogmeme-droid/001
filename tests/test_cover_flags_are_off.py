"""Cover flags stay off, and the Python and JS readings agree.

Not offered to customers. Licence review pending. Do not enable in production.

The JS reading in contracts/cover/flags.cjs is the one the website uses. This
module is the Python reading. An empty mapping is a clean slate in both: it
is not the process environment.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "contracts" / "cover" / "cover_flags.py"
JS = ROOT / "contracts" / "cover" / "flags.cjs"
HOLD = "Not offered to customers. Licence review pending. Do not enable in production."

_spec = spec_from_file_location("cover_flags", PY)
assert _spec is not None and _spec.loader is not None
cover_flags = module_from_spec(_spec)
_spec.loader.exec_module(cover_flags)

_NODE = r"""
const f = require(process.argv[1]);
const mode = process.argv[2];
const env = JSON.parse(process.argv[3]);
const src = mode === 'process' ? undefined : env;
process.stdout.write(JSON.stringify({
  adl: f.adlOn(src),
  stop: f.stopGapOn(src),
}));
"""


def _js(mode: str, env: dict[str, str], process_env: dict[str, str] | None = None) -> dict[str, bool]:
    child_env = dict(process_env or {})
    child_env["PATH"] = os.environ.get("PATH", "")
    out = subprocess.run(
        ["node", "-e", _NODE, str(JS), mode, json.dumps(env)],
        check=True,
        capture_output=True,
        text=True,
        env=child_env,
    )
    return json.loads(out.stdout)


def _py(mode: str, env: dict[str, str], monkeypatch) -> dict[str, bool]:
    monkeypatch.delenv("COVER_ADL_ENABLED", raising=False)
    monkeypatch.delenv("COVER_STOPGAP_ENABLED", raising=False)
    if mode == "process":
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        src = None
    else:
        src = env
    return {"adl": cover_flags.adl_on(src), "stop": cover_flags.stop_gap_on(src)}


def test_the_module_does_not_define_enabled():
    tree = ast.parse(PY.read_text(encoding="utf-8"))
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert "flag_on" in names
    assert "enabled" not in names
    js = JS.read_text(encoding="utf-8")
    assert "function flagOn(" in js
    assert "function enabled(" not in js


def _expect(env: dict[str, str]) -> dict[str, bool]:
    def on(key: str) -> bool:
        raw = env.get(key)
        if raw is None:
            return False
        return str(raw).strip().lower() in ("1", "true")

    return {"adl": on("COVER_ADL_ENABLED"), "stop": on("COVER_STOPGAP_ENABLED")}


def test_both_readings_agree_and_default_off(monkeypatch):
    rows = [
        ("explicit", {}),
        ("explicit", {"COVER_ADL_ENABLED": "1"}),
        ("explicit", {"COVER_ADL_ENABLED": "true"}),
        ("explicit", {"COVER_ADL_ENABLED": " TRUE "}),
        ("explicit", {"COVER_STOPGAP_ENABLED": "1"}),
        ("explicit", {"COVER_ADL_ENABLED": "false"}),
        ("explicit", {"COVER_ADL_ENABLED": "0"}),
        ("explicit", {"COVER_ADL_ENABLED": "yes"}),
        ("explicit", {"COVER_ADL_ENABLED": "on"}),
        ("explicit", {"COVER_ADL_ENABLED": ""}),
        ("explicit", {"COVER_STOPGAP_ENABLED": "true", "COVER_ADL_ENABLED": "0"}),
        ("process", {}),
        ("process", {"COVER_ADL_ENABLED": "1"}),
        ("process", {"COVER_STOPGAP_ENABLED": "true"}),
    ]
    # An empty mapping must not see a flag that is set on the process.
    monkeypatch.setenv("COVER_ADL_ENABLED", "1")
    monkeypatch.setenv("COVER_STOPGAP_ENABLED", "1")
    assert cover_flags.adl_on({}) is False
    assert cover_flags.stop_gap_on({}) is False
    assert _js("explicit", {}) == {"adl": False, "stop": False}
    monkeypatch.delenv("COVER_ADL_ENABLED")
    monkeypatch.delenv("COVER_STOPGAP_ENABLED")

    for mode, env in rows:
        py = _py(mode, env, monkeypatch)
        js = _js(mode, env, env if mode == "process" else {})
        assert py == js == _expect(env), (mode, env, py, js)


def test_the_hold_is_the_same_sentence_everywhere():
    files = [
        ROOT / "contracts" / "cover" / "CoverEscrow.sol",
        ROOT / "contracts" / "cover" / "README.md",
        ROOT / "contracts" / "cover" / "flags.cjs",
        ROOT / "contracts" / "cover" / "cover_flags.py",
        ROOT / "app" / "public" / "cover-adl.html",
        ROOT / "app" / "public" / "cover-stopgap.html",
        ROOT / "app" / "lib" / "cover_page.js",
        ROOT / "app" / "server.js",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert HOLD in text, path
    sol = (ROOT / "contracts" / "cover" / "CoverEscrow.sol").read_text(encoding="utf-8")
    assert "0xb88339CB7199b77E23DB6E890353E22632Ba630f" not in sol
    assert "0x2B3370eE501B4a559b57D449569354196457D8Ab" not in sol
    assert "guaranteed" not in sol.lower()
    assert "risk-free" not in sol.lower()
