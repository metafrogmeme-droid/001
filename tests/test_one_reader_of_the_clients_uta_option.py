"""One reader of the ccxt client's ``options["uta"]``.

`close_lookup.client_is_uta` and `live_executor.client_marks_uta` were two
identical readers of the same option, one for the close lookup and one for
the leverage set and the post-fill guard: two answers to keep in step. The
executor now asks the one.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

from bot.core import close_lookup, live_executor

ROOT = Path(__file__).resolve().parent.parent


def _reads_uta_option(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "uta"):
            yield node.lineno


def test_exactly_one_function_reads_the_option():
    sites = []
    for path in (ROOT / "bot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sites += [(str(path.relative_to(ROOT)), ln) for ln in _reads_uta_option(tree)]
    assert [s[0] for s in sites] == ["bot/core/close_lookup.py"], sites
    assert not hasattr(live_executor, "client_marks_uta")
    assert live_executor.client_is_uta is close_lookup.client_is_uta


def test_the_reader_answers_both_arms():
    assert close_lookup.client_is_uta(SimpleNamespace(options={"uta": True})) is True
    assert close_lookup.client_is_uta(SimpleNamespace(options={"uta": "yes"})) is False
    assert close_lookup.client_is_uta(SimpleNamespace(options={})) is False
    assert close_lookup.client_is_uta(SimpleNamespace(options=None)) is False


def test_the_rule_finds_a_planted_second_reader():
    src = 'def client_marks_uta(x):\n    return x.options.get("uta") is True\n'
    assert list(_reads_uta_option(ast.parse(src))) == [2]
