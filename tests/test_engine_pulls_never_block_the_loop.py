"""A slow website froze SL/TP monitoring for as long as it took to answer.

`bot/utils/credential_pull._request` is a synchronous
``urllib.request.urlopen(req, timeout=15)``. Three of the engine's `async def`
pumps called it — through `pull_and_apply`, `pull_and_apply_controls`,
`pull_and_apply_stance`, `fetch_flatten_pending`, `ack_flatten`,
`fetch_leaderboard_optins` — **directly**, on the same loop that runs stop-loss
and take-profit monitoring, the websocket feed and every Telegram handler
(`bot/main.py`).

Driven, not argued. Against a server that answers in 2 seconds, a heartbeat
coroutine wanting to tick every 10ms:

    the fetch took            2.01s
    heartbeats during it      0     (expected ~200)
    longest heartbeat gap     2.01s

Nothing else on the loop ran at all. At the real 15s timeout that is a 15s
freeze, and `_maybe_pull_web_credentials` chained three pulls of two requests
each — six of them.

TWO THINGS MAKE IT WORSE THAN A SLOW POLL.

Sync code never yields, so the tick's own `asyncio.wait_for` cap cannot
interrupt it. The timeout that looks like the backstop is not one, which is
why "there's a timeout on the tick" was not the reassurance it appeared to be.

And the pump that fetches emergency-stop requests is among them: the loop was
blocked *asking whether to flatten*, which is the one place a stall costs the
most.

The fix is `asyncio.to_thread`, which `bot/skills/telegram_handler.py` already
uses at every one of its own pull sites. The engine's pumps were the outlier,
so this is the house pattern reaching the last place that lacked it.

The first version of the probe below reported "not blocked". It stopped the
heartbeat immediately after the fetch, so no tick ever landed *after* the
stall and the gap metric had nothing to measure against — a metric that reads
a total freeze as no gap at all. It is kept honest here by asserting on the
tick COUNT during the call as well as the gap, which disagree only when the
measurement is broken.
"""
from __future__ import annotations

import ast
import asyncio
import http.server
import re
import socketserver
import threading
import time
import tokenize
import io
import pathlib

import pytest

import bot.utils.control_pull as control_pull
import bot.utils.credential_pull as credential_pull
import bot.utils.leaderboard_pull as leaderboard_pull

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "bot" / "core" / "engine.py"

SERVER_DELAY = 1.0          # keeps the suite quick; the real timeout is 15s
TICK = 0.01


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    def _answer(self):
        time.sleep(SERVER_DELAY)
        body = b'{"pending": [], "acks": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):          # keep pytest output clean
        pass


@pytest.fixture
def engine_stub():
    """Duck-typed `self` for the pumps under test.

    The real engine takes an exchange connection, a risk engine and a
    portfolio to construct; none of that is what these methods are being
    asked about. The coroutines are called unbound against a stub carrying
    exactly the attributes they touch, so the test exercises the real method
    bodies and nothing else.
    """
    class _Store:
        def set_live_trading(self, *a, **k): pass
        def set_sim_opt_in(self, *a, **k): pass
        def set_max_margin(self, *a, **k): pass
        def max_margin(self, *a, **k): return None
        def sim_opt_in(self, *a, **k): return False
        def can_trade_live(self, *a, **k): return False
        # Required by test_live_open_and_auto_accept's guard, which caught this
        # stub: a double that answers `can_trade_live` and not
        # `live_trading_revoked` answers half of what the live gate asks, and
        # a half-answering double lets a test agree with a gate that would
        # actually behave differently. Not incidental scaffolding — the guard
        # exists because `can_trade_live is False` was once read as "revoked"
        # when it is also the paper-only default every registration writes.
        def live_trading_revoked(self, *a, **k): return False
        def get_tier(self, *a, **k): return "trader"

    class _Stub:
        _user_store = _Store()

        def invalidate_user_executor(self, uid): pass

        def _is_operator_user(self, tg): return False

        def _executor_for(self, tg):
            raise AssertionError("the stub server returns no pending rows")

    return _Stub()


@pytest.fixture
def slow_site(monkeypatch):
    """Point the pull channel at a server that takes SERVER_DELAY to answer."""
    srv = socketserver.TCPServer(("127.0.0.1", 0), _SlowHandler)
    srv.allow_reuse_address = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    secret = "s" * 48
    for mod in (credential_pull, control_pull, leaderboard_pull):
        if hasattr(mod, "WEBSITE_URL"):
            monkeypatch.setattr(mod, "WEBSITE_URL", url, raising=False)
        if hasattr(mod, "SYNC_SECRET"):
            monkeypatch.setattr(mod, "SYNC_SECRET", secret, raising=False)
    yield url
    srv.shutdown()
    srv.server_close()


async def _loop_liveness(coro):
    """Run `coro` while a heartbeat ticks. Return (ticks_during, longest_gap).

    The two must agree: a real stall shows as ~0 ticks AND a gap of about the
    stall's length. If they disagree the measurement is wrong, not the code.
    """
    ticks: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(TICK)

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.1)                     # let it establish a rhythm
    before = len(ticks)
    await coro
    await asyncio.sleep(0.2)                     # let it RESUME before stopping
    stop.set()
    await hb
    gaps = [b - a for a, b in zip(ticks, ticks[1:])] or [0.0]
    return len(ticks) - before, max(gaps)


def _assert_loop_kept_running(during, gap, what):
    expected = SERVER_DELAY / TICK
    assert during > expected * 0.25, (
        f"{what}: only {during} heartbeats ran during a {SERVER_DELAY}s call "
        f"(expected ~{int(expected)}) — the event loop was blocked, and that "
        f"loop is also running SL/TP monitoring")
    assert gap < SERVER_DELAY * 0.5, (
        f"{what}: the loop stalled for {gap:.2f}s")


# ── the three pumps, driven ──────────────────────────────────────────

async def test_the_flatten_pump_does_not_stall_the_loop(slow_site, engine_stub):
    """The emergency-stop pump. Blocking here means the loop is frozen while
    asking whether to close positions."""
    from bot.core.engine import RuneClawEngine
    during, gap = await _loop_liveness(
        RuneClawEngine._maybe_flatten_web_requests(engine_stub))
    _assert_loop_kept_running(during, gap, "_maybe_flatten_web_requests")


async def test_the_credential_pump_does_not_stall_the_loop(slow_site, engine_stub,
                                                           monkeypatch):
    """Three chained pulls, two requests each. This one had the most to lose."""
    from bot.core.engine import RuneClawEngine
    monkeypatch.setattr(credential_pull, "is_configured", lambda: True)
    during, gap = await _loop_liveness(
        RuneClawEngine._maybe_pull_web_credentials(engine_stub))
    _assert_loop_kept_running(during, gap, "_maybe_pull_web_credentials")


# `_maybe_publish_user_leaderboards` is the third pump and is NOT driven here:
# it sits behind three env gates and a five-minute throttle, and standing all
# that up would be more scaffolding than assertion. It is covered by the
# reachability guard at the bottom of this file — which is the case CLAUDE.md
# says source scanning is legitimately for, precisely because two sibling
# pumps ARE exercised behaviourally right above it.


# ── the measurement itself ───────────────────────────────────────────

async def test_the_probe_can_actually_detect_a_stall(slow_site):
    """Without this the three tests above pass on a harness that measures
    nothing — which is the failure mode of the probe that started as
    'not blocked' against code that was totally blocked. Block the loop on
    purpose and require BOTH signals to fire."""
    async def blocking():
        time.sleep(SERVER_DELAY)                 # deliberately sync

    during, gap = await _loop_liveness(blocking())
    assert during < SERVER_DELAY / TICK * 0.25, "the tick count sees nothing"
    assert gap > SERVER_DELAY * 0.5, "the gap metric sees nothing"


# ── the guard, at every call site ────────────────────────────────────

def _code_only(path: pathlib.Path) -> str:
    """Source with comments and docstrings stripped.

    A comment naming the call it forbids is indistinguishable from the call —
    and the fix for this defect put `pull_and_apply` in a comment two lines
    above the fixed line, which would have failed the scan below on the very
    change that cured it.
    """
    out, prev = [], tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, tokenize.DEDENT):
            out.append("''")                     # a docstring
        else:
            out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
    return " ".join(out)


def _blocking_pull_names() -> set[str]:
    """Every public function in bot/utils/*_pull.py that reaches `_request`.

    DERIVED, not listed. A hardcoded list is a second copy of the truth that
    stops tracking the first — the same reason preflight.py parses ci.yml
    instead of restating it. A new pull helper is covered the day it is
    written, without anyone remembering to add it here.
    """
    names: set[str] = set()
    for path in sorted((ROOT / "bot" / "utils").glob("*_pull.py")):
        src = _code_only(path)
        # Two passes: direct `_request(` users, then anything calling those.
        bodies = dict(re.findall(r"\bdef\s+(\w+)\s*\([^)]*\)[^:]*:(.*?)(?=\bdef\s|\Z)",
                                 src, re.S))
        direct = {n for n, b in bodies.items() if "_request (" in b or "_request(" in b}
        grew = True
        while grew:
            grew = False
            for n, b in bodies.items():
                if n in direct:
                    continue
                if any(re.search(rf"\b{re.escape(d)}\s*\(", b) for d in direct):
                    direct.add(n)
                    grew = True
        names |= {n for n in direct if not n.startswith("_")}
    return names


def test_the_derivation_finds_the_functions_this_is_about():
    """If the derivation silently found nothing, the guard below is vacuous
    and would pass on the original defect."""
    found = _blocking_pull_names()
    for expected in ("fetch_flatten_pending", "ack_flatten",
                     "pull_and_apply", "pull_and_apply_controls",
                     "pull_and_apply_stance", "fetch_leaderboard_optins"):
        assert expected in found, (
            f"the scan no longer sees {expected} as a blocking pull — the "
            f"guard below has stopped protecting it. Found: {sorted(found)}")


def test_no_engine_code_calls_a_blocking_pull_directly():
    """The behavioural tests above cover two pumps. This covers the next one
    somebody writes — a source scan for a shape a unit test cannot reach: a
    guard being REACHED at every call site.

    SCOPE IS THE WHOLE FILE, not just `async def` bodies, and that is the
    lesson from writing it. The first version scanned only coroutines — and
    `_maybe_pull_web_credentials` was a plain `def` called directly from the
    tick coroutine, so it blocked the loop exactly as hard while sitting in a
    place the scan could not see. There is no loop-free context in this
    module: everything here runs under the engine's tick, so a synchronous
    `urlopen(timeout=15)` anywhere in it is on the loop.
    """
    blocking = _blocking_pull_names()
    offenders = []
    for i, raw in enumerate(ENGINE.read_text().splitlines(), 1):
        code = raw.split("#", 1)[0]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for name in blocking:
            # `await asyncio.to_thread(name, ...)` passes the function by
            # REFERENCE — no call parens — so requiring `name(` matches only
            # the direct invocations and lets the offloaded ones through.
            if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", code):
                offenders.append(f"engine.py:{i}: {stripped[:88]}")

    assert not offenders, (
        "these blocking website pulls are invoked directly in the engine. "
        "Each is a synchronous urlopen(timeout=15) on the loop that also runs "
        "SL/TP monitoring, the WS feed and every Telegram handler — wrap with "
        "`await asyncio.to_thread(...)`:\n  " + "\n  ".join(offenders))


#: Synchronous HTTP. Anything here reached from a coroutine blocks the loop.
_BLOCKING = {"urlopen", "urlretrieve"}
_BLOCKING_ATTR = {("requests", m) for m in
                  ("get", "post", "put", "delete", "patch", "head", "request")}
_BLOCKING_ATTR |= {("httpx", m) for m in
                   ("get", "post", "put", "delete", "patch", "head")}


def _is_blocking_call(node) -> bool:
    f = getattr(node, "func", None)
    if isinstance(f, ast.Attribute):
        if f.attr in _BLOCKING:
            return True
        base = f.value
        if isinstance(base, ast.Name) and (base.id, f.attr) in _BLOCKING_ATTR:
            return True
    return isinstance(f, ast.Name) and f.id in _BLOCKING


def _offloaded_names(node) -> set:
    """Names passed BY REFERENCE to to_thread / run_in_executor here."""
    out = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name not in ("to_thread", "run_in_executor"):
            continue
        for arg in n.args:
            if isinstance(arg, ast.Name):
                out.add(arg.id)
    return out


def blocking_http_in_coroutines(path: pathlib.Path) -> list:
    """Blocking HTTP a coroutine in `path` would execute on the loop.

    TWO forms, and the second is the one a line-based scan misses. A mutation
    pass proved it: replacing `await asyncio.to_thread(_validate_token)` with
    `_validate_token()` left the urlopen inside a nested plain `def`, so every
    line matching "urlopen inside an async def" still passed while the loop was
    blocked again. Undoing the fix has to fail, not just deleting it.
    """
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:                                    # pragma: no cover
        return []
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        nested = {n.name: n for n in ast.walk(fn)
                  if isinstance(n, ast.FunctionDef) and n is not fn}
        # A nested `def` is a to_thread TARGET — it does not run on the loop.
        blocking_nested = {name for name, n in nested.items()
                           if any(_is_blocking_call(c) for c in ast.walk(n)
                                  if isinstance(c, ast.Call))}
        offloaded = _offloaded_names(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if any(node is c for n in nested.values()
                   for c in ast.walk(n) if isinstance(c, ast.Call)):
                continue                                   # inside a nested def
            if _is_blocking_call(node):
                out.append((node.lineno, "synchronous HTTP on the loop"))
            fname = getattr(node.func, "id", None)
            if fname in blocking_nested and fname not in offloaded:
                out.append((node.lineno,
                            f"{fname}() blocks and is CALLED, not offloaded"))
    return sorted(set(out))


def test_no_coroutine_anywhere_in_the_bot_makes_a_blocking_http_call():
    """The whole package, not just the engine.

    The engine-scoped guard above was written first and would never have found
    what re-running the search did: `bot/skills/user_middleware.py` validated
    the `/link` token with a 10-second synchronous `urlopen` inside a Telegram
    handler — same loop, same freeze, one directory away. Any user typing
    `/link` against a slow website froze position protection for ten seconds.

    "The grep tells you where you looked; the test tells you where you didn't."
    This is that grep, promoted, so the next one is found on the day it lands.
    """
    offenders = [f"{p.relative_to(ROOT)}:{line}: {why}"
                 for p in sorted((ROOT / "bot").rglob("*.py"))
                 for line, why in blocking_http_in_coroutines(p)]
    assert not offenders, (
        "synchronous HTTP reachable from a coroutine — this blocks the loop "
        "that runs SL/TP monitoring, the WS feed and every Telegram handler. "
        "Put the call in a nested `def` and `await asyncio.to_thread(...)` "
        "it:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("src,should_fire,label", [
    ("async def h():\n"
     "    with urllib.request.urlopen(req, timeout=10) as r:\n"
     "        return r.read()\n",
     True, "inline urlopen in a coroutine"),
    ("async def h():\n"
     "    def _go():\n"
     "        return urllib.request.urlopen(req).read()\n"
     "    return _go()\n",
     True, "blocking helper CALLED rather than offloaded"),
    ("async def h():\n"
     "    def _go():\n"
     "        return urllib.request.urlopen(req).read()\n"
     "    return await asyncio.to_thread(_go)\n",
     False, "the fixed form"),
    ("async def h():\n"
     "    return requests.get(url).json()\n",
     True, "requests inside a coroutine"),
    ("def h():\n"
     "    return urllib.request.urlopen(req).read()\n",
     False, "a plain sync function is not on the loop"),
    ("async def h():\n"
     "    async with session.get(url) as r:\n"
     "        return await r.json()\n",
     False, "aiohttp, which is the right way"),
])
def test_the_scan_fires_on_the_defect_and_not_on_the_fix(tmp_path, src,
                                                         should_fire, label):
    """A scan that matches nothing passes forever, and one that matches the
    fix makes the fix impossible. Both directions, driven."""
    p = tmp_path / "sample.py"
    p.write_text(src)
    hits = blocking_http_in_coroutines(p)
    assert bool(hits) is should_fire, f"{label}: got {hits}"


def test_the_credential_pump_is_awaited_rather_than_called():
    """It was a sync `def` invoked bare from the tick. Making it `async` is
    only half the fix — a coroutine created and never awaited would silently
    do nothing at all, which is a quieter failure than the stall it replaces.
    """
    lines = ENGINE.read_text().splitlines()
    assert any(re.search(r"async\s+def\s+_maybe_pull_web_credentials", ln)
               for ln in lines), "the credential pump must be a coroutine"

    calls = [(i, ln.split("#", 1)[0].strip()) for i, ln in enumerate(lines, 1)
             if "_maybe_pull_web_credentials(" in ln.split("#", 1)[0]
             and "def " not in ln]
    assert calls, "the tick no longer calls the credential pump at all"
    for i, stripped in calls:
        # A coroutine created and never awaited does NOTHING — a quieter
        # failure than the stall it replaces, and one that only shows up as
        # "credentials stopped importing" days later.
        window = " ".join(lines[max(0, i - 3):i])
        assert "await" in window, (
            f"engine.py:{i}: the credential pump coroutine is created without "
            f"being awaited — {stripped}")
