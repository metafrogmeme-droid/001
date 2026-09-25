"""An explicit ``{}`` means "nothing is set", and the prose names the real default.

`(env or os.environ)` treats an empty dict as ABSENT, so a caller that asked
about a clean slate was answered with whatever the process exported. Driven,
`signer_key_present({})` answered True on a box whose environment held a
signing key — and two guards in `test_web3_signer.py` pass `{}` to mean "no
key", so they were measuring the box they ran on. Six readers carried the
shape; each reads `env if env is not None else os.environ` now, the spelling
`meme_executor.feature_enabled` already used.

The second half is prose that named the wrong default. The signer's refusal
text called WEB3_LIVE_EXEC_SIGN_ENABLED "its own default-OFF switch", and the
sign and deploy handlers' docstrings said "default-OFF", while both switches
ship ON (`.env.example` says so correctly). `tests/default_comments.py`'s rule
reads `bot/config.py` declarations and cannot see these, so they are pinned
by NAME against the reader itself, called with ``env={}``.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from bot.web import user_gateway as ug
from bot.web import web3_exec_gate as gate
from bot.web import web3_signer as sg
from bot.web import web_live_gate as wlg

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch):
    # The vault mirrors managed env vars only when bot.config is imported,
    # which has already happened; this makes the intent explicit anyway.
    monkeypatch.setenv("SECRETS_VAULT_ENABLED", "0")


# ── an empty dict is a clean slate ───────────────────────────────────────

@pytest.mark.parametrize("var,value,read,clean,process", [
    ("WEB3_LIVE_EXEC_SIGN_ENABLED", "0", sg.signing_enabled, True, False),
    ("WEB3_LIVE_EXEC_ENABLED", "0", gate.feature_enabled, True, False),
    ("WEB3_LIVE_EXEC_ALLOW_MAINNET", "1", gate.mainnet_allowed, False, True),
    ("WEB_LIVE_TRADING_ENABLED", "1", wlg.feature_enabled, False, True),
    ("WEB3_SIGNER_PRIVATE_KEY", "0x" + "11" * 32, sg.signer_key_present, False, True),
])
def test_an_empty_dict_is_not_the_process_environment(monkeypatch, var, value, read,
                                                      clean, process):
    monkeypatch.setenv(var, value)
    assert read({}) is clean, f"{read.__name__}({{}}) read the process environment"
    assert read(None) is process, f"{read.__name__}(None) must still read the process"


def test_the_rpc_reader_too(monkeypatch):
    monkeypatch.setenv("WEB3_RPC_SEPOLIA", "http://rpc.from.the.process")
    assert sg.rpc_url_for("sepolia", {}) == ""
    assert sg.rpc_url_for("sepolia") == "http://rpc.from.the.process"


def _or_environ(tree: ast.AST) -> list[int]:
    """Lines where `x or os.environ` appears — the shape that reads `{}` as absent."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for v in node.values[1:]:
                if (isinstance(v, ast.Attribute) and v.attr == "environ"
                        and isinstance(v.value, ast.Name) and v.value.id == "os"):
                    hits.append(node.lineno)
    return hits


def test_no_reader_in_bot_spells_or_os_environ():
    offenders = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        for line in _or_environ(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == [], ("`env or os.environ` reads an explicit {} as the process "
                             "environment — spell it `env if env is not None else "
                             "os.environ`:\n  " + "\n  ".join(offenders))


def test_the_rule_sees_the_shape_it_forbids():
    """A rule the real tree cannot reach is measured on planted source."""
    planted = ast.parse("import os\ndef f(env=None):\n    return (env or os.environ).get('X')\n")
    assert _or_environ(planted) == [3]
    fixed = ast.parse("import os\ndef f(env=None):\n"
                      "    e = env if env is not None else os.environ\n    return e.get('X')\n")
    assert _or_environ(fixed) == []


# ── the prose names the default the reader has ──────────────────────────

_WORD = re.compile(r"\bdefaults?[- ](on|off)\b", re.I)


def _words(text: str) -> set[str]:
    return {w.upper() for w in _WORD.findall(" ".join(str(text).split()))}


def _default(reader) -> str:
    return "ON" if reader({}) else "OFF"


@pytest.mark.parametrize("name,text,readers", [
    ("the signer's refusal for its own switch",
     dict(sg._SIGN_CHECKS)["signing_enabled"], [sg.signing_enabled]),
    ("the preview gate's refusal for the feature switch",
     dict(gate._CHECKS)["feature_enabled"], [gate.feature_enabled]),
    ("signing_enabled's docstring", sg.signing_enabled.__doc__, [sg.signing_enabled]),
    ("mainnet_allowed's docstring", gate.mainnet_allowed.__doc__, [gate.mainnet_allowed]),
    ("the web live gate's docstring", wlg.feature_enabled.__doc__, [wlg.feature_enabled]),
    ("handle_web3_sign's docstring", ug.handle_web3_sign.__doc__,
     [gate.feature_enabled, sg.signing_enabled]),
    ("handle_contract_deploy's docstring", ug.handle_contract_deploy.__doc__,
     [gate.feature_enabled, sg.signing_enabled]),
])
def test_a_sentence_names_the_default_its_switch_has(name, text, readers):
    said = _words(text)
    real = {_default(r) for r in readers}
    assert said, f"{name} names no default — the pin has lost its sentence"
    assert said == real, f"{name} says default {sorted(said)}; the reader answers {sorted(real)}"


def test_the_web_relay_names_the_defaults_the_bot_has():
    js = (ROOT / "app/routes/web3_execute.js").read_text(encoding="utf-8")
    said = _words(js)
    assert said == {_default(gate.feature_enabled), _default(sg.signing_enabled)} == {"ON"}
    assert "default-OFF" not in js


def test_the_gate_does_not_claim_there_is_no_signer():
    """It opened "RUNECLAW has NO on-chain execution infrastructure today (no
    signer…)" beside a module that signs and broadcasts."""
    doc = " ".join((gate.__doc__ or "").split())
    assert callable(getattr(sg, "build_and_sign", None))
    assert "NO on-chain execution infrastructure" not in doc
    assert "no signer" not in doc.lower()
