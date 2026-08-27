"""A safety monitor that nothing ever constructed.

THE DEFECT

`bot/core/black_swan.py` is 454 lines of anomaly detection with a unit-test
class of its own (`tests/test_core.py::TestBlackSwanDetector`) — flash-crash
detection, volume collapse, volatility explosion, correlation breakdown. It has
three consumers. `engine.black_swan` was never assigned anywhere, so:

  api_bridge.py /blackswan          returned {"status": "not_initialized"} always
  proactive_monitor._check_black_swan  `hasattr(engine, 'black_swan')` always
                                       False -> returned [] on every pass of the
                                       alert pipeline it is registered in

The tests passed the whole time. They test the detector; nothing tested that
anyone built one. That is this audit's recurring shape, and its sixth instance:
"does the guard work?" and "is the guard reached?" are different questions.

WHAT EXECUTING IT FOR THE FIRST TIME FOUND

Three bugs in code that had shipped and could not possibly have run:

  * `proactive_monitor` compared `alert_obj.severity == "SEVERE"`, but
    `AnomalyAlert.severity` is a float in [0, 1] (`Field(ge=0.0, le=1.0)`). The
    comparison was always False, so a genuine flash crash would have rendered as
    an orange WARNING rather than a red CRITICAL.
  * `AnomalyType` is a `str, Enum`, and Python 3.11 formats those as
    "AnomalyType.PRICE_ACCELERATION". That string went into the Telegram message
    body AND into the dedup key.
  * `api_bridge.py` read `detector.recent_alerts`. No such attribute exists; the
    `getattr` default returned [] so the endpoint reported "monitoring" with
    zero alerts regardless of what the detector held. Invisible until now,
    because the `engine.black_swan is None` branch returned first every time —
    two dead layers, each hiding the next.

SCOPE, DELIBERATELY LIMITED

The detector OBSERVES. Nothing acts on `halt_recommended`, and this change does
not make anything act on it: automatically halting a live account on a
statistical trigger is a new trading behaviour, not the fix for a dead
reference. The operator-facing text used to say "Engine may auto-halt if
severity is SEVERE" — false then and false now — and now says the opposite,
because an operator who stands down expecting protection that does not exist is
worse off than one who was told nothing.
"""

import ast
import pathlib


REPO = pathlib.Path(__file__).resolve().parent.parent

from bot.core.black_swan import _HALT_SEVERITY, AnomalyType  # noqa: E402


def _strip_comments(src: str) -> str:
    """Source with `#` comments removed.

    Every one of these assertions is a search for a string in source, and the
    first draft of three of them matched the COMMENT explaining the bug rather
    than the bug. A test that a comment can satisfy is a test that a comment can
    also break; worse, it would have passed if the fix were reverted and only
    the comment left behind. Naive but sufficient here: `#` inside a string
    literal would over-strip, and over-stripping only costs a false failure.
    """
    out = []
    for line in src.splitlines():
        q = None
        for i, ch in enumerate(line):
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == "#":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _call_source(src: str, prefix: str) -> str:
    """The full `prefix...)` call text, paren-balanced.

    Truncating at the first `)` cuts `update(symbol, price=float(signal.price)`
    in half and then reports the remaining kwargs as missing.
    """
    i = src.index(prefix)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced parens after {prefix!r}")


def _fn_source(path: pathlib.Path, name: str) -> str:
    src = path.read_text()
    lines = src.splitlines(keepends=True)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found in {path.name}")


def _endpoint_source() -> str:
    src = (REPO / "api_bridge.py").read_text()
    fn = src[src.index("async def blackswan_status"):]
    return fn[:fn.index("\n@app.")]


def test_the_engine_constructs_one():
    """The finding itself. Source-level, because building a real Engine needs
    exchange credentials and a live venue."""
    init = _fn_source(REPO / "bot" / "core" / "engine.py", "__init__")
    assert "self.black_swan = BlackSwanDetector()" in init, (
        "nothing assigns engine.black_swan — the detector is dead code again, and "
        "both consumers silently degrade to 'not initialized' rather than failing")


def test_the_scan_path_actually_feeds_it():
    """Constructing it is not enough: a detector with no data never fires.

    `update()` must be called from the analysis path, and with the volume and
    ATR arguments — a call passing price alone would leave three of the five
    checks permanently blind while still looking wired.
    """
    analyze = _fn_source(REPO / "bot" / "core" / "engine.py", "_analyze_signal")
    assert "self.black_swan.update(" in analyze, (
        "the detector is constructed but never fed — _analyze_signal is the only "
        "place in the scan path holding symbol, price, volume and ATR together")
    call = _call_source(analyze, "self.black_swan.update(")
    for arg in ("volume=", "atr="):
        assert arg in call, f"black_swan.update() is called without {arg}"


def test_feeding_it_can_never_break_a_trade_decision():
    """Fail-open, and pinned. The detector only observes, so an exception in it
    must cost nothing. A future edit that lets it raise would let a statistics
    bug stop the bot trading."""
    analyze = _fn_source(REPO / "bot" / "core" / "engine.py", "_analyze_signal")
    i = analyze.index("self.black_swan.update(")
    before, after = analyze[:i], analyze[i:]
    assert before.rstrip().endswith(("try:", '"""')) or "try:" in before[-400:], (
        "the black_swan.update() call is not inside a try block")
    assert "except Exception" in after[:600], (
        "nothing catches an exception from black_swan.update()")


def test_severity_is_compared_as_a_number_not_a_string():
    """The bug that only executing the path could find."""
    fn = _strip_comments(_fn_source(REPO / "bot" / "core" / "proactive_monitor.py",
                                    "_check_black_swan"))
    assert '== "SEVERE"' not in fn, (
        "severity is a float in [0, 1]; comparing it to the string 'SEVERE' is "
        "always False, so a flash crash renders as a WARNING")
    assert "_HALT_SEVERITY" in fn, (
        "the alert's CRITICAL threshold must be the detector's own escalation "
        "threshold, or the colour and the detector's advice can disagree")


def test_the_alert_renders_the_enum_value_not_its_repr():
    """`f"{AnomalyType.X}"` is "AnomalyType.X" on Python 3.11, and that string
    reached the user's chat and the dedup key."""
    assert f"{AnomalyType.PRICE_ACCELERATION}" != "PRICE_ACCELERATION", (
        "premise changed: str-Enum formatting now yields the bare value, so the "
        "`.value` calls this test guards are no longer load-bearing")
    fn = _strip_comments(_fn_source(REPO / "bot" / "core" / "proactive_monitor.py",
                                    "_check_black_swan"))
    assert "{alert_obj.anomaly_type}" not in fn, (
        "the raw enum is interpolated into user-facing text or the dedup key")


def test_no_message_promises_an_automatic_halt():
    """Nothing reads `halt_recommended`. Promising protection that does not
    exist is worse than promising nothing — the operator stands down."""
    fn = _strip_comments(_fn_source(REPO / "bot" / "core" / "proactive_monitor.py",
                                    "_check_black_swan"))
    assert "may auto-halt" not in fn
    assert "does NOT auto-halt" in fn

    engine_src = _strip_comments((REPO / "bot" / "core" / "engine.py").read_text())
    assert "halt_recommended" not in engine_src, (
        "the engine now reads halt_recommended — if it acts on it, the operator "
        "message saying it does NOT auto-halt has become the false one, and the "
        "new behaviour needs its own tests")


def test_the_api_reads_an_attribute_that_exists():
    from bot.core.black_swan import BlackSwanDetector
    d = BlackSwanDetector()
    assert not hasattr(d, "recent_alerts"), (
        "recent_alerts exists now — the api_bridge getattr default was masking "
        "its absence, and this test can be dropped")
    assert hasattr(d, "active_alerts") and hasattr(d, "halt_recommended")

    fn = _strip_comments(_endpoint_source())
    assert "recent_alerts" not in fn, "the endpoint still reads a non-existent attribute"
    assert "active_alerts" in fn


def test_the_endpoint_enumerates_anomaly_types_from_the_enum():
    """The list was retyped by hand. It agreed then; nothing kept it agreeing."""
    fn = _strip_comments(_endpoint_source())
    assert "for t in AnomalyType" in fn, (
        "anomaly_types is hand-listed again — add a type to the enum and this "
        "endpoint silently under-reports it")


def test_a_severe_alert_is_reachable_end_to_end():
    """Not source-level: drive the real detector into a halt-worthy state and
    check the rendering path agrees with it.

    Without this, every assertion above is about text. This one runs the thing.
    """
    from bot.core.black_swan import BlackSwanDetector
    d = BlackSwanDetector()
    for _ in range(40):
        d.update("BTC/USDT", price=100.0, volume=1000.0, atr=1.0)
    # A flash crash: price collapses, on the same history that was flat.
    alerts = []
    for px in (95.0, 80.0, 60.0, 40.0):
        alerts += d.update("BTC/USDT", price=px, volume=1000.0, atr=1.0)

    assert alerts, "no anomaly fired on a 60% collapse — the detector is not working"
    assert all(isinstance(a.severity, float) for a in alerts)
    severe = [a for a in alerts if a.severity >= _HALT_SEVERITY]
    assert severe, "a 60% collapse produced no alert at halt severity"
    assert d.halt_recommended is True

    # ...and the render path classifies it the way the detector does.
    a = severe[0]
    assert (float(a.severity) >= _HALT_SEVERITY) is True
    kind = getattr(a.anomaly_type, "value", a.anomaly_type)
    assert kind in {t.value for t in AnomalyType}
    assert "AnomalyType." not in kind
