"""The debug capture writes bodies. It must never write the credential.

`ollama/ollama_auth_proxy.py` gained an opt-in dump of the request and
response bytes, because two rounds of guessing at why the bot saw "AI
temporarily unavailable" while ollama logged HTTP 200 could neither be
confirmed nor refuted from the outside.

It is the right tool and it sits in the wrong place to be careless: every
request through that proxy carries `Authorization: Bearer <RUNECLAW_PROXY
_TOKEN>`, and this repository has already had one incident from a proxy token
reaching a file. A capture feature that widened to headers would put the live
credential on disk in plain text, under a directory chosen for debugging and
therefore likely to be pasted into an issue.

Two properties, and they fail differently:

  - OFF BY DEFAULT is behavioural, so it is driven.
  - HEADERS ARE NEVER PASSED IN is a property of the CALL SITES, which no unit
    test can reach from inside the function — `_dump` cannot know what its
    caller declined to hand it. That one is a source scan, which is what
    CLAUDE.md reserves them for.
"""

from __future__ import annotations

import importlib.util
import re
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "ollama" / "ollama_auth_proxy.py"


def _load():
    spec = importlib.util.spec_from_file_location("ollama_auth_proxy", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code_only(path: Path) -> str:
    """Blank comments and strings, preserving offsets.

    A docstring here NAMES the Authorization header in order to explain why it
    is excluded — indistinguishable, to a grep, from code that dumps it. That
    exact false failure is recorded four times in CLAUDE.md.
    """
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                out.append(re.sub(r"\S", " ", tok.string))
            else:
                out.append(tok.string)
            out.append(" ")
    return "".join(out)


def test_the_DEFAULT_is_off(monkeypatch):
    """Assert the default itself, not a value handed in.

    The first version of this test set `DEBUG_DIR = ""` and then checked that
    nothing was written — which pins what `_dump` does with an empty string,
    and says nothing about what the module picks when the operator sets
    nothing. A mutation giving the env lookup a real fallback directory
    ("capture everything, always") passed all five tests. The module reads its
    env at import, so the honest check imports it with the variable unset.
    """
    monkeypatch.delenv("RUNECLAW_PROXY_DEBUG", raising=False)
    assert _load().DEBUG_DIR == ""


def test_an_empty_setting_writes_nothing(tmp_path, monkeypatch):
    """And the behaviour that follows from that default."""
    mod = _load()
    monkeypatch.setattr(mod, "DEBUG_DIR", "")
    # `_dump` touches no instance state, so it drives with a bare stand-in.
    mod.GateHandler._dump(object(), "request", b"secret prompt text")
    assert list(tmp_path.iterdir()) == []


def test_capture_writes_the_payload_when_switched_on(tmp_path, monkeypatch):
    """Guard the guard: a dump that never writes would pass the test above."""
    mod = _load()
    monkeypatch.setattr(mod, "DEBUG_DIR", str(tmp_path))
    mod.GateHandler._dump(object(), "request", b"hello")
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == b"hello"
    assert files[0].name.endswith("-request.txt")


def test_a_dump_failure_never_takes_the_proxy_down(tmp_path, monkeypatch):
    """A debugging aid must not be able to break the thing being debugged."""
    mod = _load()
    monkeypatch.setattr(mod, "DEBUG_DIR", "/proc/nonexistent/cannot-create")
    mod.GateHandler._dump(object(), "request", b"hello")  # must not raise


def test_no_call_site_hands_headers_or_the_token_to_the_dump():
    """The property `_dump` itself cannot check: what it is CALLED with."""
    code = _code_only(_SRC)
    calls = re.findall(r"_dump\s*\(([^)]*)\)", code)
    assert calls, "no _dump call sites found — this scan has drifted"
    for args in calls:
        low = args.lower()
        assert "header" not in low, f"_dump called with headers: {args.strip()}"
        assert "auth" not in low, f"_dump called with the credential: {args.strip()}"
        assert "token" not in low, f"_dump called with the token: {args.strip()}"


def test_the_body_is_only_read_after_the_credential_is_checked():
    """An unauthenticated caller's bytes must never reach the disk.

    Ordering inside one function, which is a shape a unit test cannot reach
    without a live socket: the deny must return before the read and the dump.
    """
    code = _code_only(_SRC)
    proxy = code[code.index("def _proxy"):]
    assert proxy.index("_deny") < proxy.index("_dump"), (
        "the request body is captured before the auth check")
