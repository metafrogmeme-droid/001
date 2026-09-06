"""The first slice out of the 15,000-line handler, and the rule for the rest.

`bot/skills/chat_runtime.py` holds the chat's leaf pieces — the rate limiter,
the chain timing constants, the thinking phrases, the two tool rules, the
Telegram edit-stream, the event fan-out and the `_chat_ret` funnel. Their
behaviour is covered where it always was (`test_chat_streaming`,
`test_rr_honesty`, `test_fabricated_tool_results`, `test_chat_chrome_i18n`,
`test_chat_chain_deadline`); this file pins the SPLIT:

  1. the definitions live in the new module, and the handler only re-exports
     them — the same objects, under the original names, so the ninety test
     files that import from the handler did not have to move;
  2. the new module is a leaf: it imports nothing from the handler and
     nothing from the engine, so it can never become a second brain;
  3. `_llm_chat` and `_chat_tools_for` stay in the handler, on purpose —
     eighteen suites monkeypatch `CONFIG`, `llm_complete`,
     `create_llm_client`, `resolve_tier_config` and `resolve_profile_note`
     on the handler module, and the brain reads exactly those globals. A
     slice that moved it would pass every one of those tests against code
     the patches no longer reach.
"""
from __future__ import annotations

import inspect
import io
import re
import tokenize

import bot.skills.chat_runtime as rt
import bot.skills.telegram_handler as th

MOVED = (
    "RateLimiter", "TelegramStream", "CHAT_MIN_ATTEMPT_SEC", "CHAT_TOOL_ATTEMPT_SEC",
    "THINKING_PHRASE_KEYS", "thinking_phrase", "_say", "_emit_event",
    "_CHAT_NO_TOOLS_RULE", "_CHAT_TOOLS_RULE", "_chat_ret",
)
STAYS = ("_llm_chat", "_chat_tools_for")


def code_only(src: str) -> str:
    """Source with comments and docstrings blanked, so prose that names a
    definition cannot be mistaken for the definition."""
    out = []
    prev_type = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (tokenize.INDENT, tokenize.NEWLINE,
                                                          tokenize.NL, None):
            prev_type = tok.type
            continue
        out.append(tok.string)
        prev_type = tok.type
    return " ".join(out)


HANDLER_SRC = code_only(inspect.getsource(th))
RUNTIME_SRC = code_only(inspect.getsource(rt))


def _assigns(src: str, name: str) -> bool:
    """A statement-level `NAME = ...` or `NAME: T = ...` — a definition. A
    use such as `if left < NAME:` also puts a colon after the name, which
    is what the first draft of this matched."""
    return re.search(rf"(?:^|\n)\s*{re.escape(name)}\s*[=:]", src) is not None


def test_the_handler_re_exports_the_very_same_objects():
    for name in MOVED:
        assert getattr(th, name) is getattr(rt, name), name


def test_the_definitions_left_the_handler():
    for name in ("RateLimiter", "TelegramStream"):
        assert f"class {name}" in RUNTIME_SRC and f"class {name}" not in HANDLER_SRC, name
    for name in ("thinking_phrase", "_say", "_emit_event", "_chat_ret"):
        assert f"def {name} (" in RUNTIME_SRC and f"def {name} (" not in HANDLER_SRC, name
    for name in ("CHAT_MIN_ATTEMPT_SEC", "CHAT_TOOL_ATTEMPT_SEC", "THINKING_PHRASE_KEYS",
                 "_CHAT_NO_TOOLS_RULE", "_CHAT_TOOLS_RULE"):
        assert _assigns(RUNTIME_SRC, name), name
        assert not _assigns(HANDLER_SRC, name), name


def test_the_brain_stays_where_its_seams_are():
    for name in STAYS:
        assert f"def {name} (" in HANDLER_SRC, name
        assert f"def {name} (" not in RUNTIME_SRC, name
    # The globals the suites patch are the handler's, and the brain reads them.
    for name in ("CONFIG", "llm_complete", "create_llm_client", "resolve_tier_config",
                 "resolve_profile_note"):
        assert hasattr(th, name), name


def test_the_runtime_is_a_leaf():
    src = inspect.getsource(rt)
    assert "telegram_handler" not in src.split('"""', 2)[2], "no import back into the handler"
    assert "bot.core.engine" not in src
    assert "from bot.config" not in src, "no CONFIG here — the brain's patches would miss it"
    for line in src.splitlines():
        if line.startswith(("from ", "import ")):
            assert "telegram" not in line, line


def test_the_stream_and_the_funnel_still_work_through_the_handler_name():
    """A smoke test on the re-exported names, so an import that resolved to a
    stale copy would show here rather than in production."""
    limiter = th.RateLimiter(2)
    assert limiter.allow(1) and limiter.allow(1) and not limiter.allow(1)
    assert th._chat_ret("plain", None, False) == "plain"
    assert th._say("zh", "chat_unavailable", "en text") != "en text"
    assert th._say("en", "chat_unavailable", "en text") == "en text"
    assert th.thinking_phrase("en").startswith("<i>") or "⚔️" in th.thinking_phrase("en")
