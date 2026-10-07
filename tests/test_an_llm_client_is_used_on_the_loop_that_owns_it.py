"""An operator LLM client is used only on the event loop that owns it.

The analyzer builds its operator clients once and the SDKs pool connections
on the loop that first used them. The scan lane's batch and the engine loop's
user analyses shared those clients, so a pooled connection reached the other
loop ("bound to a different event loop"), and after the SDK's retries the
thesis fell back to the rule engine. Each loop now gets its own twin, built
once from the same config.
"""
import ast
import asyncio
from pathlib import Path

from bot.core.analyzer import Analyzer


class _Client:
    pass


def _analyzer():
    a = Analyzer.__new__(Analyzer)
    built = []

    def _build(cfg):
        c = _Client()
        built.append((cfg, c))
        return c

    a._build_client_for_config = _build
    return a, built


def _on(loop, a, client, cfg):
    async def go():
        return a._on_this_loop(client, cfg)
    return loop.run_until_complete(go())


def test_the_first_loop_keeps_the_client_and_another_loop_gets_one_twin():
    a, built = _analyzer()
    original, cfg = _Client(), object()
    one, two = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        assert _on(one, a, original, cfg) is original
        assert _on(one, a, original, cfg) is original
        twin = _on(two, a, original, cfg)
        assert twin is not original
        assert built == [(cfg, twin)]
        assert _on(two, a, original, cfg) is twin, "built once per loop"
        assert _on(one, a, original, cfg) is original
    finally:
        one.close()
        two.close()


def test_no_client_no_config_or_no_loop_is_passed_through():
    a, built = _analyzer()
    c = _Client()
    assert a._on_this_loop(c, object()) is c          # no running loop
    loop = asyncio.new_event_loop()
    try:
        assert _on(loop, a, None, object()) is None
        assert _on(loop, a, c, None) is c
    finally:
        loop.close()
    assert built == []


def test_a_refresh_forgets_the_twins_of_the_old_clients():
    a, built = _analyzer()
    a._client_home, a._loop_twins = {1: (1, object())}, {(1, 1): object()}
    a._resolve_llm_config = lambda: None
    a._build_llm_client = lambda: None
    a.refresh_llm_client()
    assert a._client_home == {} and a._loop_twins == {}


def test_the_thesis_path_asks_for_this_loops_client():
    # The routing is inside a 300-line method with no seam; the call that
    # matters is held to its place: after the operator routing, before the
    # per-user and per-tier clients (built per call) can replace it.
    src = Path("bot/core/analyzer.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_llm_thesis")
    body = ast.get_source_segment(src, fn)
    at = body.index("active_client = self._on_this_loop(active_client, active_cfg)")
    assert body.count("self._on_this_loop(") == 1
    assert body.index("active_client = self._llm") < at < body.index("self._maybe_user_client(user_id)")
